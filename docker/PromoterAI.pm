=head1 NAME

PromoterAI - transcript/TSS-aware lookup of locally prepared Illumina scores

=head1 SYNOPSIS

  --plugin PromoterAI,file=/data/promoterai_scores.tsv.gz,
                    transcript_map=/data/promoterai_transcripts.tsv

=head1 DESCRIPTION

The plugin contains no PromoterAI model or licensed score data. It reads the
compact local files produced by scripts/prepare_promoterai.sh and reports a
score only when the genomic allele and the VEP transcript's TSS agree.

=cut

package PromoterAI;

use strict;
use warnings;
use Bio::EnsEMBL::Variation::Utils::Sequence qw(get_matched_variant_alleles);
use Bio::EnsEMBL::Variation::Utils::BaseVepTabixPlugin;
use base qw(Bio::EnsEMBL::Variation::Utils::BaseVepTabixPlugin);

sub new {
  my $class = shift;
  my $self = $class->SUPER::new(@_);
  my $params = $self->params_to_hash();
  my $file = $params->{file};
  my $map = $params->{transcript_map};
  die "ERROR: PromoterAI requires file=<compact scores>\n" unless defined $file && length $file;
  die "ERROR: PromoterAI requires transcript_map=<TSV>\n" unless defined $map && length $map;
  die "ERROR: PromoterAI transcript map not found: $map\n" unless -f $map;
  $self->expand_left(0);
  $self->expand_right(0);
  $self->add_file($file);
  $self->{promoterai_file} = $file;
  $self->_load_transcript_map($map);
  return $self;
}

sub _load_transcript_map {
  my ($self, $path) = @_;
  open my $handle, '<', $path or die "ERROR: cannot read PromoterAI transcript map $path: $!\n";
  my $header = <$handle>;
  chomp $header;
  my @header = split /\t/, $header, -1;
  my %index = map { $header[$_] => $_ } 0 .. $#header;
  for my $required (qw(transcript_id transcript_base gene gene_id chrom tss_pos strand)) {
    die "ERROR: PromoterAI transcript map lacks column $required\n" unless exists $index{$required};
  }
  my (%exact, %base);
  while (my $line = <$handle>) {
    chomp $line;
    next unless length $line;
    my @value = split /\t/, $line, -1;
    my $record = {
      transcript_id => $value[$index{transcript_id}],
      transcript_base => $value[$index{transcript_base}],
      gene => $value[$index{gene}],
      gene_id => $value[$index{gene_id}],
      chrom => $value[$index{chrom}],
      tss_pos => 0 + $value[$index{tss_pos}],
      strand => $value[$index{strand}],
    };
    $exact{$record->{transcript_id}} = $record;
    push @{$base{$record->{transcript_base}}}, $record;
  }
  close $handle;
  $self->{promoterai_exact} = \%exact;
  $self->{promoterai_base} = \%base;
}

sub feature_types { return ['Transcript']; }

sub get_header_info {
  return {
    PromoterAI_score => 'PromoterAI signed promoter-effect score for this allele and transcript TSS',
    PromoterAI_TSS => 'PromoterAI source TSS coordinate on GRCh38 (1-based)',
    PromoterAI_distance => 'Variant distance from TSS in the direction of transcription',
    PromoterAI_source_transcript => 'Transcript identifier in the licensed PromoterAI TSS table',
    PromoterAI_match => 'Transcript match mode: exact_version or stable_id',
  };
}

sub _transcript_mapping {
  my ($self, $transcript, $chr) = @_;
  my $base = $transcript->stable_id;
  return unless defined $base && length $base;
  my $version = eval { $transcript->version };
  my $exact = defined $version && length $version ? "$base.$version" : $base;
  if (my $mapping = $self->{promoterai_exact}{$exact}) {
    return ($mapping, 'exact_version') if !defined $chr || $mapping->{chrom} eq $chr;
  }
  my $candidates = $self->{promoterai_base}{$base} || [];
  my @on_contig = defined $chr ? grep { $_->{chrom} eq $chr } @$candidates : @$candidates;
  return ($on_contig[0], 'stable_id') if @on_contig == 1;
  return;
}

sub run {
  my ($self, $tva) = @_;
  my $vf = $tva->variation_feature;
  my $transcript = $tva->transcript;

  # The compact score table and transcript map are Ensembl-style
  # (prepare_promoterai.py normalises their contigs), so a chr-prefixed
  # input VCF previously matched nothing — silently, for every variant.
  # Normalise the query contig the same way LoGoFunc.pm does.
  my $chr = $vf->{chr};
  return {} unless defined $chr && length $chr;
  $chr =~ s/^chr//i;
  $chr = 'MT' if uc($chr) eq 'M';

  my ($mapping, $match) = $self->_transcript_mapping($transcript, $chr);
  return {} unless $mapping;

  # SNV-only by table design (decision D5): prepare_promoterai.py stores
  # exactly the four single-base substitutions per position, so only a
  # single-base span can ever match. An Ensembl-convention insertion
  # (start = end + 1) and any multi-base span are deliberately refused here,
  # and the ACGT allele guard below rejects non-SNVs a second time. The old
  # start/end swap that made this look like range handling was dead code.
  my $start = $vf->{start};
  my $end = $vf->{end};
  return {} unless $start == $end;
  return {} unless $mapping->{chrom} eq $chr;

  my $ref = $vf->ref_allele_string;
  my $alt = $tva->variation_feature_seq;
  return {} unless defined $ref && defined $alt && $ref =~ /^[ACGT]$/i && $alt =~ /^[ACGT]$/i;
  my @data = @{$self->get_data($chr, $start, $end, $self->{promoterai_file})};
  for my $data (@data) {
    next unless $data->{tss_pos} == $mapping->{tss_pos};
    next unless $data->{strand} eq $mapping->{strand};
    for my $candidate (@{$data->{alleles}}) {
      my $matches = get_matched_variant_alleles(
        {ref => $ref, alts => [$alt], pos => $start, strand => $vf->strand},
        {ref => $data->{ref}, alts => [$candidate->{alt}], pos => $data->{start}},
      );
      next unless @$matches;
      my $direction = $mapping->{strand} eq '+' ? 1 : -1;
      return {
        PromoterAI_score => $candidate->{score},
        PromoterAI_TSS => $mapping->{tss_pos},
        PromoterAI_distance => ($start - $mapping->{tss_pos}) * $direction,
        PromoterAI_source_transcript => $mapping->{transcript_id},
        PromoterAI_match => $match,
      };
    }
  }
  return {};
}

sub parse_data {
  my ($self, $line) = @_;
  my ($chr, $start, $ref, $tss_pos, $strand, @scores) = split /\t/, $line, -1;
  my @alleles;
  my @bases = qw(A C G T);
  for my $index (0 .. $#bases) {
    next if $scores[$index] eq '.' || $bases[$index] eq $ref;
    push @alleles, {alt => $bases[$index], score => $scores[$index]};
  }
  return {
    chr => $chr,
    start => 0 + $start,
    ref => $ref,
    tss_pos => 0 + $tss_pos,
    strand => $strand,
    alleles => \@alleles,
  };
}

1;
