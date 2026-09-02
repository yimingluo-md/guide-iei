=head1 NAME

IndexedScores - manifest-driven exact matching for locally indexed predictors

=head1 DESCRIPTION

This reusable adapter reads a GUIDE-IEI indexed-score manifest and a BGZF/tabix
table.  Its match contract can require a normalized genomic allele followed by
an Ensembl gene, Ensembl transcript, and/or one-letter protein change.  Scores
are emitted only for one unambiguous exact match.  Allele-only and ambiguous
matches emit provenance but never scores.

=cut

package IndexedScores;

use strict;
use warnings;
use B qw(SVp_IOK SVp_NOK svref_2object);
use JSON::PP qw(decode_json);
use Bio::EnsEMBL::Variation::Utils::Sequence qw(get_matched_variant_alleles);
use Bio::EnsEMBL::Variation::Utils::BaseVepTabixPlugin;
use base qw(Bio::EnsEMBL::Variation::Utils::BaseVepTabixPlugin);

my $MANIFEST_SCHEMA = 'guide-iei.indexed-scores/v1';
my %SUPPORTED_DIMENSION = map { $_ => 1 }
  qw(allele ensembl_gene_id ensembl_transcript_id protein_change);

sub _nonempty_scalar {
  my ($value) = @_;
  return defined $value && !ref($value) && length($value);
}

sub _valid_field_id {
  my ($value) = @_;
  return _nonempty_scalar($value) && $value =~ /^[A-Za-z_][A-Za-z0-9_.]*$/;
}

sub _json_number {
  my ($value) = @_;
  return 0 if !defined $value || ref($value);
  my $flags = svref_2object(\$value)->FLAGS;
  return ($flags & (SVp_IOK | SVp_NOK)) ? 1 : 0;
}

sub _json_positive_integer {
  my ($value) = @_;
  return 0 if !defined $value || ref($value);
  my $flags = svref_2object(\$value)->FLAGS;
  return ($flags & SVp_IOK) && $value > 0;
}

sub new {
  my $class = shift;
  my $self = $class->SUPER::new(@_);
  my $params = $self->params_to_hash();
  my $file = $params->{file};
  my $manifest = $params->{manifest};
  die "ERROR: $class requires file=<indexed score table>\n"
    unless defined $file && length $file;
  die "ERROR: $class requires manifest=<indexed score manifest>\n"
    unless defined $manifest && length $manifest;
  my $expected = $params->{resource};
  die "ERROR: $class requires resource=<predictor registry id>\n"
    unless defined $expected && length $expected;
  die "ERROR: $class manifest not found: $manifest\n" unless -f $manifest;

  $self->_load_manifest($manifest);
  my $actual = $self->{indexed_manifest}{resource}{id};
  die "ERROR: $class requires resource '$expected', not '$actual'\n"
    unless $actual eq $expected;
  $self->expand_left(0);
  $self->expand_right(0);
  $self->cache_size(5000);
  $self->add_file($file);
  $self->{indexed_score_file} = $file;
  return $self;
}

sub _load_manifest {
  my ($self, $path) = @_;
  open my $handle, '<', $path
    or die "ERROR: cannot read indexed-score manifest $path: $!\n";
  local $/;
  my $raw = <$handle>;
  close $handle;
  my $manifest = eval { decode_json($raw) };
  die "ERROR: indexed-score manifest is not valid JSON: $@\n" unless $manifest;
  die "ERROR: unsupported indexed-score manifest schema\n"
    unless ($manifest->{manifest_schema} || '') eq $MANIFEST_SCHEMA;
  die "ERROR: indexed-score manifest assembly must be GRCh38\n"
    unless ($manifest->{assembly} || '') eq 'GRCh38';

  my $applicability = $manifest->{applicability};
  if (defined $applicability) {
    die "ERROR: indexed-score applicability must be an object\n"
      unless ref($applicability) eq 'HASH';
    my @keys = keys %$applicability;
    die "ERROR: indexed-score applicability supports only consequences\n"
      unless @keys == 1 && $keys[0] eq 'consequences';
    my $consequences = $applicability->{consequences};
    die "ERROR: indexed-score applicability consequences are invalid\n"
      unless ref($consequences) eq 'ARRAY' && @$consequences;
    my %seen;
    for my $term (@$consequences) {
      die "ERROR: indexed-score applicability consequence is invalid\n"
        unless defined $term && $term =~ /^[a-z][a-z0-9_]+$/ && !$seen{$term}++;
    }
    $self->{indexed_consequences} = \%seen;
  }

  my $resource = $manifest->{resource};
  die "ERROR: indexed-score manifest lacks resource metadata\n"
    unless ref($resource) eq 'HASH';
  for my $key (qw(id name release)) {
    die "ERROR: indexed-score resource.$key must be a non-empty string\n"
      unless _nonempty_scalar($resource->{$key});
  }

  my $table = $manifest->{table};
  die "ERROR: indexed-score manifest lacks table metadata\n"
    unless ref($table) eq 'HASH'
      && ref($table->{columns}) eq 'ARRAY'
      && @{$table->{columns}};
  my %column;
  for my $index (0 .. $#{$table->{columns}}) {
    my $name = $table->{columns}[$index];
    die "ERROR: indexed-score table column names must be non-empty strings\n"
      unless _nonempty_scalar($name);
    die "ERROR: duplicate indexed-score column '$name'\n" if exists $column{$name};
    $column{$name} = $index;
  }

  my $match = $manifest->{match};
  my $required = ref($match) eq 'HASH' ? $match->{required} : undef;
  die "ERROR: indexed-score manifest lacks match.required\n"
    unless ref($required) eq 'ARRAY' && @$required && $required->[0] eq 'allele';
  my $dimensions = $match->{dimensions};
  die "ERROR: indexed-score manifest lacks match.dimensions\n"
    unless ref($dimensions) eq 'HASH';
  my (%required_dimension, %required_column);
  for my $dimension (@$required) {
    die "ERROR: unsupported indexed-score match dimension '$dimension'\n"
      unless _nonempty_scalar($dimension) && $SUPPORTED_DIMENSION{$dimension};
    die "ERROR: duplicate indexed-score match dimension '$dimension'\n"
      if $required_dimension{$dimension}++;
    my $definition = $dimensions->{$dimension};
    die "ERROR: indexed-score manifest lacks dimension '$dimension'\n"
      unless ref($definition) eq 'HASH';
    if ($dimension eq 'allele') {
      for my $key (qw(chrom position reference alternate)) {
        my $name = $definition->{$key};
        die "ERROR: allele dimension lacks table column '$key'\n"
          unless _nonempty_scalar($name) && exists $column{$name};
        die "ERROR: indexed-score match dimensions reuse table column '$name'\n"
          if $required_column{$name}++;
      }
      my $normalization = $definition->{normalization};
      die "ERROR: unsupported allele normalization\n"
        if defined $normalization
          && $normalization ne 'vep_matched_variant_alleles';
    }
    else {
      my $name = $definition->{column};
      die "ERROR: match dimension '$dimension' lacks a table column\n"
        unless _nonempty_scalar($name) && exists $column{$name};
      die "ERROR: indexed-score match dimensions reuse table column '$name'\n"
        if $required_column{$name}++;
      my $normalization = $definition->{normalization};
      die "ERROR: unsupported normalization for match dimension '$dimension'\n"
        if defined $normalization
          && $normalization ne 'exact'
          && $normalization ne 'strip_version'
          && $normalization ne 'one_letter_protein_change';
    }
  }

  my $outputs = $manifest->{outputs};
  die "ERROR: indexed-score manifest lacks outputs\n"
    unless ref($outputs) eq 'ARRAY' && @$outputs;
  my %field;
  for my $output (@$outputs) {
    die "ERROR: malformed indexed-score output\n" unless ref($output) eq 'HASH';
    my $id = $output->{id};
    my $name = $output->{column};
    die "ERROR: invalid indexed-score output field\n"
      unless _valid_field_id($id);
    die "ERROR: duplicate indexed-score output '$id'\n" if $field{$id}++;
    die "ERROR: indexed-score output '$id' lacks column '$name'\n"
      unless _nonempty_scalar($name) && exists $column{$name};
    my $type = $output->{type};
    die "ERROR: indexed-score output '$id' has unsupported type\n"
      unless _nonempty_scalar($type)
        && ($type eq 'number' || $type eq 'integer'
          || $type eq 'string' || $type eq 'boolean');
    die "ERROR: indexed-score output '$id' requires a description\n"
      unless _nonempty_scalar($output->{description});
    my $minimum = $output->{minimum};
    my $maximum = $output->{maximum};
    if (defined $minimum || defined $maximum) {
      die "ERROR: indexed-score output '$id' range requires a numeric type\n"
        unless $type eq 'number' || $type eq 'integer';
      die "ERROR: indexed-score output '$id' range values must be numeric\n"
        if (defined $minimum && !_json_number($minimum))
          || (defined $maximum && !_json_number($maximum));
      die "ERROR: indexed-score output '$id' minimum exceeds maximum\n"
        if defined $minimum && defined $maximum && $minimum > $maximum;
    }
  }

  my $provenance = $manifest->{provenance};
  die "ERROR: indexed-score manifest lacks provenance fields\n"
    unless ref($provenance) eq 'HASH';
  my %provenance_field;
  for my $key (qw(match match_status source_target allele_available)) {
    my $id = $provenance->{$key};
    die "ERROR: invalid indexed-score provenance field '$key'\n"
      unless _valid_field_id($id);
    die "ERROR: indexed-score provenance field conflicts with output '$id'\n"
      if $field{$id};
    die "ERROR: duplicate indexed-score provenance field '$id'\n"
      if $provenance_field{$id}++;
  }

  my $files = $manifest->{files};
  if (defined $files) {
    die "ERROR: indexed-score files must be an object\n"
      unless ref($files) eq 'HASH';
    for my $role (qw(data index)) {
      my $metadata = $files->{$role};
      die "ERROR: indexed-score files.$role must be an object\n"
        unless ref($metadata) eq 'HASH';
      die "ERROR: indexed-score files.$role.name is required\n"
        unless _nonempty_scalar($metadata->{name});
      die "ERROR: indexed-score files.$role.size must be a positive integer\n"
        unless _json_positive_integer($metadata->{size});
      die "ERROR: indexed-score files.$role.mtime_ns must be a positive integer\n"
        if defined $metadata->{mtime_ns}
          && !_json_positive_integer($metadata->{mtime_ns});
      die "ERROR: indexed-score files.$role.sha256 must be lowercase SHA-256\n"
        unless _nonempty_scalar($metadata->{sha256})
          && $metadata->{sha256} =~ /^[0-9a-f]{64}$/;
    }
  }

  $self->{indexed_manifest} = $manifest;
  $self->{indexed_columns} = \%column;
  $self->{indexed_required} = $required;
  $self->{indexed_dimensions} = $dimensions;
  $self->{indexed_outputs} = $outputs;
  $self->{indexed_provenance} = $provenance;
}

sub feature_types { return ['Transcript']; }

sub get_header_info {
  my ($self) = @_;
  my %header;
  for my $output (@{$self->{indexed_outputs}}) {
    $header{$output->{id}} = $output->{description};
  }
  my $resource = $self->{indexed_manifest}{resource}{name};
  my $required = join(', ', @{$self->{indexed_required}});
  my $provenance = $self->{indexed_provenance};
  $header{$provenance->{allele_available}} =
    "1 when the genomic allele is present in $resource, even if its required target does not match";
  $header{$provenance->{match}} =
    "$resource match dimensions that agreed; scores require every configured dimension";
  $header{$provenance->{match_status}} =
    "$resource match status: exact, partial, or ambiguous";
  $header{$provenance->{source_target}} =
    "$resource source target(s) for the configured match contract ($required)";
  return \%header;
}

sub _strip_version {
  my ($value) = @_;
  return unless defined $value;
  $value =~ s/\.\d+$//;
  return $value;
}

sub _query_gene_id {
  my ($tva) = @_;
  my $transcript = eval { $tva->transcript };
  return unless $transcript;
  my $gene = eval { $transcript->get_Gene };
  my $id = $gene ? eval { $gene->stable_id } : undef;
  $id ||= eval { $transcript->{_gene_stable_id} };
  $id ||= eval { $transcript->{gene_stable_id} };
  return $id;
}

sub _query_transcript_id {
  my ($tva, $normalization) = @_;
  my $transcript = eval { $tva->transcript };
  return unless $transcript;
  my $id = eval { $transcript->stable_id };
  return unless defined $id && length $id;
  if (($normalization || '') ne 'strip_version') {
    my $version = eval { $transcript->version };
    $id .= ".$version" if defined $version && length $version;
  }
  return $id;
}

sub _query_protein_change {
  my ($tva) = @_;
  my $position = eval {
    $tva->base_variation_feature_overlap->translation_start
  };
  my $peptide = eval { $tva->pep_allele_string };
  return unless defined $position && defined $peptide;
  return unless $peptide =~ /^([A-Z*])\/([A-Z*])$/;
  return "$1$position$2";
}

sub _query_dimensions {
  my ($self, $tva) = @_;
  my %query;
  for my $dimension (@{$self->{indexed_required}}) {
    next if $dimension eq 'allele';
    my $definition = $self->{indexed_dimensions}{$dimension};
    my $normalization = $definition->{normalization} || '';
    my $value;
    if ($dimension eq 'ensembl_gene_id') {
      $value = _query_gene_id($tva);
    }
    elsif ($dimension eq 'ensembl_transcript_id') {
      $value = _query_transcript_id($tva, $normalization);
    }
    elsif ($dimension eq 'protein_change') {
      $value = _query_protein_change($tva);
    }
    $value = _strip_version($value) if $normalization eq 'strip_version';
    $query{$dimension} = $value if defined $value && length $value;
  }
  return \%query;
}

sub _dimension_label {
  my ($dimension) = @_;
  return 'gene' if $dimension eq 'ensembl_gene_id';
  return 'transcript' if $dimension eq 'ensembl_transcript_id';
  return 'protein' if $dimension eq 'protein_change';
  return $dimension;
}

sub _source_target {
  my ($self, $record) = @_;
  my @parts;
  for my $dimension (@{$self->{indexed_required}}) {
    next if $dimension eq 'allele';
    push @parts, $record->{dimensions}{$dimension}
      if defined $record->{dimensions}{$dimension};
  }
  return join(':', @parts);
}

sub _matched_prefix {
  my ($self, $record, $query) = @_;
  my @matched = ('allele');
  if (@{$self->{indexed_required}} > 1) {
    for my $dimension (@{$self->{indexed_required}}[1 .. $#{$self->{indexed_required}}]) {
      last unless defined $query->{$dimension};
      last unless defined $record->{dimensions}{$dimension};
      last unless $query->{$dimension} eq $record->{dimensions}{$dimension};
      push @matched, _dimension_label($dimension);
    }
  }
  return join('_', @matched);
}

sub run {
  my ($self, $tva) = @_;
  if ($self->{indexed_consequences}) {
    my $consequences = eval { $tva->get_all_OverlapConsequences } || [];
    return {} unless grep {
      my $term = eval { $_->SO_term };
      defined $term && $self->{indexed_consequences}{$term}
    } @$consequences;
  }
  my $vf = eval { $tva->variation_feature };
  return {} unless $vf;
  my $start = $vf->{start};
  my $end = $vf->{end};
  return {} unless defined $start && defined $end && $start == $end;

  my $chr = $vf->{chr};
  return {} unless defined $chr && length $chr;
  $chr =~ s/^chr//i;
  $chr = 'MT' if uc($chr) eq 'M';
  my $ref = eval { $vf->ref_allele_string };
  my $alt = eval { $tva->variation_feature_seq };
  return {} unless defined $ref && length $ref && defined $alt && length $alt;

  my @records = @{$self->get_data(
    $chr, $start, $end, $self->{indexed_score_file}
  )};
  my @allele_matches;
  for my $record (@records) {
    next if defined $record->{chr} && $record->{chr} ne $chr;
    my $matches = get_matched_variant_alleles(
      {ref => $ref, alts => [$alt], pos => $start, strand => $vf->strand},
      {ref => $record->{ref}, alts => [$record->{alt}], pos => $record->{start}},
    );
    push @allele_matches, $record if @$matches;
  }
  return {} unless @allele_matches;

  my $query = $self->_query_dimensions($tva);
  my @missing_query = grep {
    $_ ne 'allele' && !defined $query->{$_}
  } @{$self->{indexed_required}};
  my @exact;
  unless (@missing_query) {
    for my $record (@allele_matches) {
      my $ok = 1;
      for my $dimension (@{$self->{indexed_required}}) {
        next if $dimension eq 'allele';
        if (!defined $record->{dimensions}{$dimension}
            || $record->{dimensions}{$dimension} ne $query->{$dimension}) {
          $ok = 0;
          last;
        }
      }
      push @exact, $record if $ok;
    }
  }

  my $provenance = $self->{indexed_provenance};
  my %seen_target;
  my @targets = grep { length $_ && !$seen_target{$_}++ }
    map { $self->_source_target($_) } @allele_matches;
  my $target_value = join('&', @targets);
  if (@exact == 1) {
    my $record = $exact[0];
    my %result = (
      $provenance->{allele_available} => 1,
      $provenance->{match_status} => 'exact',
      $provenance->{source_target} => $self->_source_target($record),
      $provenance->{match} => join('_', map { _dimension_label($_) }
        @{$self->{indexed_required}}),
    );
    for my $output (@{$self->{indexed_outputs}}) {
      $result{$output->{id}} = $record->{values}{$output->{id}};
    }
    return \%result;
  }
  if (@exact > 1 || @missing_query) {
    return {
      $provenance->{allele_available} => 1,
      $provenance->{match_status} => 'ambiguous',
      $provenance->{source_target} => $target_value,
      $provenance->{match} => @missing_query
        ? 'query_target_unavailable'
        : 'multiple_exact_records',
    };
  }

  my @prefixes = map { $self->_matched_prefix($_, $query) } @allele_matches;
  my $best = 'allele';
  for my $prefix (@prefixes) {
    $best = $prefix if scalar(split /_/, $prefix) > scalar(split /_/, $best);
  }
  return {
    $provenance->{allele_available} => 1,
    $provenance->{match_status} => 'partial',
    $provenance->{source_target} => $target_value,
    $provenance->{match} => $best eq 'allele' ? 'allele_only' : $best,
  };
}

sub parse_data {
  my ($self, $line) = @_;
  $line =~ s/\r$//;
  my @value = split /\t/, $line, -1;
  my $columns = $self->{indexed_columns};
  my $allele = $self->{indexed_dimensions}{allele};
  my $chr = $value[$columns->{$allele->{chrom}}];
  $chr =~ s/^chr//i;
  $chr = 'MT' if uc($chr) eq 'M';
  my %dimensions;
  for my $dimension (@{$self->{indexed_required}}) {
    next if $dimension eq 'allele';
    my $definition = $self->{indexed_dimensions}{$dimension};
    my $target = $value[$columns->{$definition->{column}}];
    $target = _strip_version($target)
      if ($definition->{normalization} || '') eq 'strip_version';
    $dimensions{$dimension} = $target;
  }
  my %values = map {
    $_->{id} => $value[$columns->{$_->{column}}]
  } @{$self->{indexed_outputs}};
  return {
    chr => $chr,
    start => 0 + $value[$columns->{$allele->{position}}],
    ref => uc($value[$columns->{$allele->{reference}}]),
    alt => uc($value[$columns->{$allele->{alternate}}]),
    dimensions => \%dimensions,
    values => \%values,
  };
}

1;
