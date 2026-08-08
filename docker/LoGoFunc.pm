=head1 NAME

LoGoFunc - transcript- and protein-change-aware lookup of LoGoFunc predictions

=head1 SYNOPSIS

  --plugin LoGoFunc,file=/data/LoGoFuncVotingEnsemble_metadata_preds_final.csv.gz

=head1 DESCRIPTION

Reads the GRCh38 canonical-missense prediction table published at
https://doi.org/10.5281/zenodo.13835271. A result is emitted only when the
genomic allele, Ensembl transcript stable ID, amino-acid position, and amino-
acid substitution all agree. The protein checks prevent a VEP release change
from silently attaching an older canonical-transcript prediction to a changed
protein consequence.

=cut

package LoGoFunc;

use strict;
use warnings;
use Bio::EnsEMBL::Variation::Utils::Sequence qw(get_matched_variant_alleles);
use Bio::EnsEMBL::Variation::Utils::BaseVepTabixPlugin;
use base qw(Bio::EnsEMBL::Variation::Utils::BaseVepTabixPlugin);

my %MISSENSE_SO = map { $_ => 1 } qw(missense_variant);

sub new {
  my $class = shift;
  my $self = $class->SUPER::new(@_);
  my $params = $self->params_to_hash();
  my $file = $params->{file};
  die "ERROR: LoGoFunc requires file=<indexed prediction table>\n"
    unless defined $file && length $file;
  $self->expand_left(0);
  $self->expand_right(0);
  $self->cache_size(5000);
  $self->add_file($file);
  $self->{logofunc_file} = $file;
  return $self;
}

sub feature_types { return ['Transcript']; }

sub get_header_info {
  return {
    LoGoFunc_prediction => 'LoGoFunc argmax class for this canonical missense allele: Neutral, GOF, or LOF',
    LoGoFunc_neutral => 'LoGoFunc predicted probability of a neutral missense effect',
    LoGoFunc_GOF => 'LoGoFunc predicted probability of a pathogenic gain-of-function missense effect',
    LoGoFunc_LOF => 'LoGoFunc predicted probability of a pathogenic loss-of-function missense effect',
    LoGoFunc_allele_available => '1 when the genomic allele is present in LoGoFunc, including when its source transcript or protein consequence does not match this VEP consequence',
    LoGoFunc_source_transcript => 'Ensembl canonical transcript stable ID in the LoGoFunc source table',
    LoGoFunc_source_HGVSp => 'Versioned Ensembl protein HGVS in the LoGoFunc source table',
    LoGoFunc_match => 'Strict matching evidence; allele_transcript_protein means allele, transcript, residue, and amino-acid substitution agree',
  };
}

sub run {
  my ($self, $tva) = @_;
  return {} unless grep { $MISSENSE_SO{$_->SO_term} }
    @{$tva->get_all_OverlapConsequences};

  my $vf = $tva->variation_feature;
  return {} unless defined $vf->{start} && defined $vf->{end};
  return {} unless $vf->{start} == $vf->{end};

  my $transcript = $tva->transcript;
  my $transcript_id = $transcript ? $transcript->stable_id : undef;
  return {} unless defined $transcript_id && length $transcript_id;

  my $protein_position = eval {
    $tva->base_variation_feature_overlap->translation_start
  };
  my $peptide = $tva->pep_allele_string;
  return {} unless defined $protein_position && defined $peptide;
  return {} unless $peptide =~ /^([A-Z*])\/([A-Z*])$/;
  my ($protein_ref, $protein_alt) = ($1, $2);

  my $chr = $vf->{chr};
  return {} unless defined $chr && length $chr;
  $chr =~ s/^chr//i;
  return {} if $chr =~ /^(?:M|MT)$/i;

  my $ref = $vf->ref_allele_string;
  my $alt = $tva->variation_feature_seq;
  return {} unless defined $ref && defined $alt;
  return {} unless $ref =~ /^[ACGT]$/i && $alt =~ /^[ACGT]$/i;

  my @data = @{$self->get_data(
    $chr, $vf->{start}, $vf->{end}, $self->{logofunc_file}
  )};
  my @allele_matches;
  for my $candidate (@data) {
    # Compare the (normalised) stored contig against the query: without this
    # the field was stored and never read, and a future chr-prefixed table
    # would yield empty tabix results with no diagnostic anywhere.
    next if defined $candidate->{chr}
      && length $candidate->{chr}
      && $candidate->{chr} ne $chr;
    my $matches = get_matched_variant_alleles(
      {ref => $ref, alts => [$alt], pos => $vf->{start}, strand => $vf->strand},
      {ref => $candidate->{ref}, alts => [$candidate->{alt}], pos => $candidate->{start}},
    );
    next unless @$matches;
    push @allele_matches, $candidate;
    next unless $candidate->{transcript} eq $transcript_id;
    next unless $candidate->{aa_pos} == $protein_position;
    next unless $candidate->{ref_aa} eq $protein_ref;
    next unless $candidate->{alt_aa} eq $protein_alt;

    return {
      LoGoFunc_prediction => $candidate->{prediction},
      LoGoFunc_neutral => $candidate->{neutral},
      LoGoFunc_GOF => $candidate->{gof},
      LoGoFunc_LOF => $candidate->{lof},
      LoGoFunc_allele_available => 1,
      LoGoFunc_source_transcript => $candidate->{transcript},
      LoGoFunc_source_HGVSp => $candidate->{hgvsp},
      LoGoFunc_match => 'allele_transcript_protein',
    };
  }
  if (@allele_matches) {
    my %seen;
    my @transcripts = grep { !$seen{$_}++ }
      map { $_->{transcript} } @allele_matches;
    %seen = ();
    my @proteins = grep { !$seen{$_}++ }
      map { $_->{hgvsp} } @allele_matches;
    return {
      LoGoFunc_allele_available => 1,
      LoGoFunc_source_transcript => join('&', @transcripts),
      LoGoFunc_source_HGVSp => join('&', @proteins),
      LoGoFunc_match => 'allele_only',
    };
  }
  return {};
}

sub parse_data {
  my ($self, $line) = @_;
  $line =~ s/\r$//;
  my (
    $chrom, $pos, $ref, $alt, $id, $consequence, $symbol, $gene,
    $transcript, $hgvsp, $aa_pos, $ref_aa, $alt_aa, $prediction,
    $neutral, $gof, $lof,
  ) = split /\t/, $line, -1;
  # Store the contig in the same normalised form the query uses so the
  # comparison in run() holds for both bare and chr-prefixed tables.
  $chrom =~ s/^chr//i if defined $chrom;
  $ref_aa =~ s/X/*/g;
  $alt_aa =~ s/X/*/g;
  return {
    chr => $chrom,
    start => 0 + $pos,
    ref => uc($ref),
    alt => uc($alt),
    transcript => $transcript,
    hgvsp => $hgvsp,
    aa_pos => int(0 + $aa_pos),
    ref_aa => $ref_aa,
    alt_aa => $alt_aa,
    prediction => $prediction,
    neutral => $neutral,
    gof => $gof,
    lof => $lof,
  };
}

1;
