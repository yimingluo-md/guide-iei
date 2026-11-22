package GuideAllele;

use strict;
use warnings;
use Exporter 'import';
our @EXPORT_OK = qw(sequence_edit transcript_edit transcript_snv);

# Return the first changed genomic position and unanchored forward alleles.
# For an insertion the position is the base to the right of the breakpoint.
sub sequence_edit {
  my ($pos, $ref, $alt) = @_;
  return unless defined $pos && defined $ref && defined $alt;
  return unless $ref =~ /\A(?:[ACGTN]+|-)\z/i && $alt =~ /\A(?:[ACGTN]+|-)\z/i;
  ($ref, $alt) = map { $_ eq '-' ? '' : uc($_) } ($ref, $alt);
  return if $ref eq $alt;
  while (length($ref) && length($alt) && substr($ref,0,1) eq substr($alt,0,1)) {
    substr($ref,0,1,'');
    substr($alt,0,1,'');
    ++$pos;
  }
  while (length($ref) && length($alt) && substr($ref,-1) eq substr($alt,-1)) {
    chop $ref;
    chop $alt;
  }
  return ($pos, $ref, $alt);
}

sub transcript_edit {
  my ($tva) = @_;
  my $vf = $tva->variation_feature or return;
  my $ref = $vf->ref_allele_string;
  my $alt = $tva->variation_feature_seq;
  return unless defined $ref && defined $alt;
  if ($vf->strand == -1) {
    for ($ref, $alt) {
      $_ = reverse $_;
      tr/ACGTacgt/TGCAtgca/;
    }
  }
  return sequence_edit($vf->{start}, $ref, $alt);
}

sub transcript_snv {
  my ($tva) = @_;
  my ($pos, $ref, $alt) = transcript_edit($tva);
  return unless defined $pos && $ref =~ /^[ACGT]$/ && $alt =~ /^[ACGT]$/;
  return ($pos, $ref, $alt);
}
1;
