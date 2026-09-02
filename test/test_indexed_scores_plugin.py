#!/usr/bin/env python3
import json
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pipeline.indexed_scores import (  # noqa: E402
    ManifestError,
    load_manifest,
    validate_manifest,
)


def manifest_payload():
    return {
        "manifest_schema": "guide-iei.indexed-scores/v1",
        "resource": {"id": "funcvep", "name": "FuncVEP", "release": "v2"},
        "assembly": "GRCh38",
        "applicability": {"consequences": ["missense_variant"]},
        "table": {
            "columns": [
                "chrom", "position", "reference", "alternate",
                "ensembl_gene_id", "FuncVEP_CTI", "FuncVEP_CTE", "FuncVEP_SP",
            ]
        },
        "match": {
            "required": ["allele", "ensembl_gene_id"],
            "dimensions": {
                "allele": {
                    "chrom": "chrom", "position": "position",
                    "reference": "reference", "alternate": "alternate",
                },
                "ensembl_gene_id": {
                    "column": "ensembl_gene_id", "normalization": "strip_version"
                },
            },
        },
        "outputs": [
            {
                "id": "FuncVEP_CTI", "column": "FuncVEP_CTI", "type": "number",
                "minimum": 0, "maximum": 1, "description": "CTI",
            },
            {
                "id": "FuncVEP_CTE", "column": "FuncVEP_CTE", "type": "number",
                "minimum": 0, "maximum": 1, "description": "CTE",
            },
            {
                "id": "FuncVEP_SP", "column": "FuncVEP_SP", "type": "number",
                "minimum": 0, "maximum": 1, "description": "SP",
            },
        ],
        "provenance": {
            "match": "FuncVEP_match",
            "match_status": "FuncVEP_match_status",
            "source_target": "FuncVEP_source_gene",
            "allele_available": "FuncVEP_allele_available",
        },
        "files": {
            "data": {"name": "scores.tsv.gz", "size": 1, "sha256": "0" * 64},
            "index": {"name": "scores.tsv.gz.tbi", "size": 1, "sha256": "1" * 64},
        },
    }


class IndexedScoresPluginTests(unittest.TestCase):
    def test_invalid_utf8_manifest_is_reported_as_manifest_error(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "manifest.json"
            path.write_bytes(b"\xff")
            with self.assertRaisesRegex(ManifestError, "cannot read"):
                load_manifest(path)

    def test_funcvep_exact_partial_missing_and_ambiguous_behavior(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            stub = root / "stub" / "Bio" / "EnsEMBL" / "Variation" / "Utils"
            stub.mkdir(parents=True)
            (stub / "BaseVepTabixPlugin.pm").write_text(textwrap.dedent(r"""
                package Bio::EnsEMBL::Variation::Utils::BaseVepTabixPlugin;
                sub new { my ($class, @args) = @_; return bless {args => \@args}, $class; }
                sub params_to_hash {
                  my ($self) = @_; my %params;
                  for my $arg (@{$self->{args}}) {
                    next if ref($arg) || !defined $arg;
                    $params{$1} = $2 if $arg =~ /^([^=]+)=(.*)$/;
                  }
                  return \%params;
                }
                sub expand_left { $_[0]{expand_left} = $_[1] if @_ > 1; }
                sub expand_right { $_[0]{expand_right} = $_[1] if @_ > 1; }
                sub cache_size { $_[0]{cache_size} = $_[1] if @_ > 1; }
                sub add_file { $_[0]{file} = $_[1]; }
                sub get_data { $_[0]{get_data_calls}++; return $_[0]{test_data} || []; }
                1;
            """))
            (stub / "Sequence.pm").write_text(textwrap.dedent(r"""
                package Bio::EnsEMBL::Variation::Utils::Sequence;
                use Exporter 'import';
                our @EXPORT_OK = qw(get_matched_variant_alleles);
                sub get_matched_variant_alleles {
                  my ($query, $source) = @_;
                  return [] unless $query->{pos} == $source->{pos};
                  return [] unless uc($query->{ref}) eq uc($source->{ref});
                  return [] unless uc($query->{alts}[0]) eq uc($source->{alts}[0]);
                  return [[$query->{alts}[0], $source->{alts}[0]]];
                }
                1;
            """))
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps(manifest_payload()))
            data = root / "scores.tsv.gz"
            data.touch()
            harness = root / "harness.pl"
            harness.write_text(textwrap.dedent(r"""
                use strict; use warnings; use JSON::PP qw(encode_json); use IndexedScores;
                { package Gene; sub new { bless {id => $_[1]}, $_[0] } sub stable_id { $_[0]{id} } }
                { package Transcript; sub new { bless {gene => $_[1]}, $_[0] } sub get_Gene { $_[0]{gene} } sub stable_id { 'ENST1' } }
                { package VF; sub new { bless {chr=>'chr1',start=>100,end=>100}, $_[0] } sub ref_allele_string {'A'} sub strand {1} }
                { package OC; sub new { bless {term=>$_[1]}, $_[0] } sub SO_term { $_[0]{term} } }
                { package TVA;
                  sub new { bless {gene=>$_[1],alt=>($_[2] || 'T'),term=>($_[3] || 'missense_variant')}, $_[0] }
                  sub transcript { Transcript->new($_[0]{gene}) }
                  sub variation_feature { VF->new }
                  sub variation_feature_seq { $_[0]{alt} }
                  sub get_all_OverlapConsequences { [OC->new($_[0]{term})] }
                }
                my ($file, $manifest) = @ARGV;
                my $plugin = IndexedScores->new(
                  {}, "file=$file", "manifest=$manifest", "resource=funcvep"
                );
                my $gene_record = $plugin->parse_data("1\t100\tA\tT\tENSG00000000001.7\t0.11\t0.22\t0.33");
                my $other_record = $plugin->parse_data("1\t100\tA\tT\tENSG00000000002\t0.44\t0.55\t0.66");
                $plugin->{test_data} = [$gene_record, $other_record];
                my $exact = $plugin->run(TVA->new(Gene->new('ENSG00000000001.9')));
                my $partial = $plugin->run(TVA->new(Gene->new('ENSG00000000003')));
                my $missing = $plugin->run(TVA->new(undef));
                my $absent = $plugin->run(TVA->new(Gene->new('ENSG00000000001'), 'C'));
                my $calls_before_synonymous = $plugin->{get_data_calls};
                my $synonymous = $plugin->run(
                  TVA->new(Gene->new('ENSG00000000001'), 'T', 'synonymous_variant')
                );
                my $synonymous_lookups = $plugin->{get_data_calls} - $calls_before_synonymous;
                $plugin->{test_data} = [$gene_record, $gene_record];
                my $ambiguous = $plugin->run(TVA->new(Gene->new('ENSG00000000001')));
                print encode_json({exact=>$exact, partial=>$partial, missing=>$missing,
                                   absent=>$absent, ambiguous=>$ambiguous,
                                   synonymous=>$synonymous,
                                   synonymous_lookups=>$synonymous_lookups});
            """))

            result = subprocess.run(
                [
                    "perl", f"-I{root / 'stub'}", f"-I{ROOT / 'docker'}",
                    str(harness), str(data), str(manifest),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            observed = json.loads(result.stdout)
            self.assertEqual(observed["exact"]["FuncVEP_match_status"], "exact")
            self.assertEqual(observed["exact"]["FuncVEP_match"], "allele_gene")
            self.assertEqual(observed["exact"]["FuncVEP_CTE"], "0.22")
            self.assertEqual(observed["exact"]["FuncVEP_source_gene"], "ENSG00000000001")

            self.assertEqual(observed["partial"]["FuncVEP_match_status"], "partial")
            self.assertEqual(observed["partial"]["FuncVEP_match"], "allele_only")
            self.assertNotIn("FuncVEP_CTI", observed["partial"])

            self.assertEqual(observed["missing"]["FuncVEP_match_status"], "ambiguous")
            self.assertEqual(observed["missing"]["FuncVEP_match"], "query_target_unavailable")
            self.assertNotIn("FuncVEP_CTI", observed["missing"])
            self.assertEqual(observed["absent"], {})
            self.assertEqual(observed["synonymous"], {})
            self.assertEqual(observed["synonymous_lookups"], 0)

            self.assertEqual(observed["ambiguous"]["FuncVEP_match_status"], "ambiguous")
            self.assertEqual(observed["ambiguous"]["FuncVEP_match"], "multiple_exact_records")
            self.assertNotIn("FuncVEP_CTI", observed["ambiguous"])

            invalid_cases = []

            invalid = manifest_payload()
            invalid["outputs"][0]["type"] = "decimal"
            invalid_cases.append(("output type", invalid, "unsupported type"))

            invalid = manifest_payload()
            invalid["outputs"][0]["description"] = ""
            invalid_cases.append(("output description", invalid, "requires a description"))

            invalid = manifest_payload()
            invalid["outputs"][0]["minimum"] = 2
            invalid_cases.append(("output range", invalid, "minimum exceeds maximum"))

            invalid = manifest_payload()
            invalid["match"]["dimensions"]["allele"]["normalization"] = "trim_alleles"
            invalid_cases.append(("allele normalization", invalid, "unsupported allele normalization"))

            invalid = manifest_payload()
            invalid["match"]["dimensions"]["ensembl_gene_id"]["normalization"] = "fuzzy"
            invalid_cases.append(("target normalization", invalid, "unsupported normalization"))

            invalid = manifest_payload()
            invalid["provenance"]["match_status"] = invalid["provenance"]["match"]
            invalid_cases.append(("duplicate provenance", invalid, "duplicate indexed-score provenance"))

            invalid = manifest_payload()
            invalid["provenance"]["match"] = "FuncVEP_CTI"
            invalid_cases.append(("provenance conflict", invalid, "conflicts with output"))

            invalid = manifest_payload()
            invalid["files"]["data"]["size"] = 0
            invalid_cases.append(("file size", invalid, "positive integer"))

            invalid = manifest_payload()
            invalid["files"]["index"]["sha256"] = "ABC"
            invalid_cases.append(("file checksum", invalid, "lowercase SHA-256"))

            invalid = manifest_payload()
            del invalid["resource"]["release"]
            invalid_cases.append(("resource release", invalid, "resource.release"))

            command = [
                "perl", f"-I{root / 'stub'}", f"-I{ROOT / 'docker'}",
                str(harness), str(data), str(manifest),
            ]
            for label, payload, expected_error in invalid_cases:
                with self.subTest(label=label):
                    with self.assertRaises(ManifestError):
                        validate_manifest(payload)
                    manifest.write_text(json.dumps(payload))
                    rejected = subprocess.run(
                        command,
                        check=False,
                        capture_output=True,
                        text=True,
                    )
                    self.assertNotEqual(rejected.returncode, 0)
                    self.assertIn(expected_error, rejected.stderr)


if __name__ == "__main__":
    unittest.main()
