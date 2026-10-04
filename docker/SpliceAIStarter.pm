# GUIDE-IEI, MIT license (see repository LICENSE).
# Exact SNV + stable Ensembl gene lookup for MANE 1.5, compact or sharded.
# Legacy single-file tables continue to use Ensembl's SpliceAI plugin.
package SpliceAIStarter;
use strict;
use warnings;
use JSON::PP qw(decode_json);
use File::Basename qw(dirname);
use Bio::EnsEMBL::Variation::Utils::BaseVepTabixPlugin;
use base qw(Bio::EnsEMBL::Variation::Utils::BaseVepTabixPlugin);

my @FIELDS = qw(SYMBOL DS_AG DS_AL DS_DG DS_DL DP_AG DP_AL DP_DG DP_DL);

sub _json {
    my ($path) = @_;
    open my $fh, '<', $path or die "Cannot read SpliceAI starter provenance: $path: $!\n";
    local $/;
    return decode_json(<$fh>);
}

sub new {
    my $class = shift;
    my $self = $class->SUPER::new(@_);
    my $params = $self->params_to_hash();
    die "SpliceAIStarter requires shards= or snv= with manifest=\n"
        unless $params->{shards} || ($params->{snv} && $params->{manifest});
    my $map = _json(dirname(__FILE__) . '/SpliceAI-MANE1.5-gene-map.json');
    my $source = _json($params->{shards} || $params->{manifest});
    if ($params->{shards}) {
        my $settings = $source->{scientific_configuration} || {};
        die "Unsupported full SpliceAI MANE provenance\n" unless
            ($source->{genome_assembly} || '') eq 'GRCh38/hg38' &&
            ($settings->{annotation_source_sha256} || '') eq $map->{source_sha256}{gtf} &&
            ($settings->{distance} || 0) == 500 && ($settings->{mask} || 0) == 1;
        my %files;
        for my $row (@{$source->{files} || []}) {
            my $chr = $row->{contig} || '';
            my $name = "data/spliceai-mane-v1.5-d500-m1.snv.chr$chr.vcf.gz";
            die "Invalid SpliceAI chromosome manifest\n" unless
                $chr =~ /^(?:[1-9]|1[0-9]|2[0-2]|X|Y)$/ && !$files{$chr} &&
                ($row->{vcf} || '') eq $name && ($row->{index} || '') eq "$name.tbi";
            my $file = dirname($params->{shards}) . '/' . $name;
            $self->check_file($file);
            $files{$chr} = $file;
        }
        die "Full SpliceAI requires all 24 chromosome files\n" unless keys(%files) == 24;
        $self->{shard_files} = \%files;
    } else {
    die "Unsupported SpliceAI starter provenance\n" unless
        ($map->{schema} || '') eq 'guide-iei.spliceai-gene-map/v1' &&
        ($source->{resource} || '') eq 'starter_spliceai' &&
        ($source->{assembly} || '') eq 'GRCh38';
    my %hashes = map { ($_->{role} || '') => ($_->{sha256} || '') } @{$source->{sources} || []};
    for my $role (qw(mane_summary gtf)) {
        die "SpliceAI starter $role does not match the bundled gene identities\n"
            unless ($hashes{$role} || '') eq $map->{source_sha256}{$role};
    }
        $self->add_file($params->{snv});
    }
    $self->{starter_genes} = $map->{genes};
    $self->expand_left(0); $self->expand_right(0);
    # The base class caches per file. Bound aggregate entries to about 5,000
    # per worker instead of multiplying the single-file cache by 24.
    $self->cache_size($self->{shard_files} ? 200 : 5000);
    return $self;
}

sub feature_types { return ['Transcript']; }
sub get_header_info {
    my $scope = $_[0]{shard_files} ? 'full MANE Select SNV' : 'essential-site starter';
    return {map { ('SpliceAI_pred_' . $_) => "SpliceAI MANE 1.5 D500 M1 $scope $_; exact SNV and stable Ensembl gene identity" } @FIELDS};
}

sub parse_data {
    my ($self, $line) = @_;
    my ($chr, $pos, undef, $ref, $alt, undef, undef, $info) = split /\t/, $line;
    my ($values) = $info =~ /(?:^|;)SpliceAI=([^;]+)/;
    die "Malformed SpliceAI starter record\n" unless defined $values;
    my @entries;
    for my $text (split /,/, $values) {
        my @v = split /\|/, $text, -1;
        die "Malformed SpliceAI starter entry\n" unless @v == 10 && $v[0] eq $alt;
        shift @v;
        push @entries, \@v;
    }
    $chr =~ s/^chr//;
    return {chr => $chr, start => $pos, ref => $ref, alt => $alt, entries => \@entries};
}

sub run {
    my ($self, $tva) = @_;
    my $vf = $tva->variation_feature;
    return {} unless defined $vf->{start} && defined $vf->{end} && $vf->{start} == $vf->{end};
    my $ref = $vf->ref_allele_string;
    my $alt = $tva->variation_feature_seq;
    return {} unless defined $ref && defined $alt && $ref =~ /^[ACGT]$/ && $alt =~ /^[ACGT]$/;
    # VEP's reference/variation_feature_seq are genomic alleles, independent
    # of transcript strand. Never reverse-complement for a minus-strand gene.
    return {} unless $vf->strand == 1;
    my $tx = $tva->transcript;
    my $gene = eval { $tx->get_Gene->stable_id } || $tx->{_gene_stable_id} || $tx->{gene_stable_id};
    return {} unless $gene;
    $gene =~ s/\.\d+$//;
    my $context = $self->{starter_genes}{$gene} or return {};
    my $chr = $vf->{chr} // ''; $chr =~ s/^chr//;
    return {} unless $context->{chrom} eq $chr;
    my %names = map { $_ => 1 } @{$context->{symbols}};
    my %candidates;
    # Supplying the fourth argument bypasses the base class's all-file loop.
    # Tabix handles open lazily in each VEP worker and are reused by that worker.
    my $file = $self->{shard_files} ? $self->{shard_files}{$chr} : undef;
    return {} if $self->{shard_files} && !$file;
    for my $row (@{$self->get_data($chr, $vf->{start}, $vf->{end}, $file)}) {
        next unless $row->{chr} eq $chr && $row->{start} == $vf->{start}
            && $row->{ref} eq $ref && $row->{alt} eq $alt;
        for my $entry (@{$row->{entries}}) {
            next unless $names{$entry->[0]};
            # Multiple aliases for the same gene are fine only if all eight
            # scores/positions agree. Never select by order or maximum score.
            my $key = join('|', @{$entry}[1..8]);
            $candidates{$key}{$entry->[0]} = $entry;
        }
    }
    return {} unless keys(%candidates) == 1;
    my ($matches) = values %candidates;
    my ($symbol) = sort keys %$matches;
    my $entry = $matches->{$symbol};
    return {map { ('SpliceAI_pred_' . $FIELDS[$_]) => $entry->[$_] } 0..$#FIELDS};
}
1;
