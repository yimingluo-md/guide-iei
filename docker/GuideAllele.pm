package GuideAllele;

use strict;
use warnings;
use Exporter 'import';
our @EXPORT_OK = qw(sequence_edit transcript_edit transcript_snv);

# Return the first changed genomic position and unanchored forward alleles.
# For an insertion the position is the base to the right of the breakpoint.
sub sequence_edit {
  my ($pos, $ref, $alt, $format) = @_;
  return unless defined $pos && defined $ref && defined $alt;
  return unless $ref =~ /\A(?:[ACGTN]+|-)\z/i && $alt =~ /\A(?:[ACGTN]+|-)\z/i;
  ($ref, $alt) = map { $_ eq '-' ? '' : uc($_) } ($ref, $alt);
  return if $ref eq $alt;

  # VEP removes a shared VCF anchor before minimising an indel. Retain
  # its breakpoint convention even when the anchor is itself repeated.
  if (($format || '') eq 'vcf' && length($ref) != length($alt)
      && substr($ref,0,1) eq substr($alt,0,1)) {
    substr($ref,0,1,'');
    substr($alt,0,1,'');
    ++$pos;
  }
  # Trim right padding first so GAA/GA and GA/G delete the same A.
  while (length($ref) && length($alt) && substr($ref,-1) eq substr($alt,-1)) {
    chop $ref;
    chop $alt;
  }
  while (length($ref) && length($alt) && substr($ref,0,1) eq substr($alt,0,1)) {
    substr($ref,0,1,'');
    substr($alt,0,1,'');
    ++$pos;
  }
  return ($pos, $ref, $alt);
}

sub _forward_edit {
  my ($tva, $vf) = @_;
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

# VEP can minimise towards the right in a repeat. Recover the padding from
# its original VCF record only if applying this TVA's edit reproduces an ALT.
sub _input_vcf_edit {
  my ($line, $pos, $deleted, $inserted) = @_;
  return unless ref($line) eq 'ARRAY';
  my ($input_pos, $ref, $alts) = @$line[1, 3, 4];
  return unless defined $input_pos && $input_pos =~ /\A[1-9][0-9]*\z/
    && defined $ref && $ref =~ /\A[ACGTN]+\z/i && defined $alts;
  my $offset = $pos - $input_pos;
  my $alt = uc($ref);
  return unless $offset >= 0 && $offset + length($deleted) <= length($alt)
    && substr($alt, $offset, length($deleted)) eq $deleted;
  substr($alt, $offset, length($deleted), $inserted);
  return unless grep { $_ eq $alt } split /,/, uc($alts);
  return (sequence_edit($input_pos, $ref, $alt, 'vcf'), $input_pos);
}

# The fourth value is the start of the available input context for tabix.
sub transcript_edit {
  my ($tva) = @_;
  my $vf = $tva->variation_feature or return;
  my @edit = _forward_edit($tva, $vf);
  return unless @edit;
  if (length($edit[1]) != length($edit[2])) {
    my @original = _input_vcf_edit($vf->{_line}, @edit);
    return @original if @original;
  }
  return (@edit, $vf->{start});
}

sub transcript_snv {
  my ($tva) = @_;
  my $vf = $tva->variation_feature or return;
  my ($pos, $ref, $alt) = _forward_edit($tva, $vf);
  return unless defined $pos && $ref =~ /^[ACGT]$/ && $alt =~ /^[ACGT]$/;
  return ($pos, $ref, $alt);
}
1;
