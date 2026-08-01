#!/usr/bin/env python3
"""Tests for the public annotation regression validator."""

from __future__ import annotations

import pathlib
import sys
import tempfile

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from pipeline.validate_regression_annotations import run_validation


FIELDS = [
    "Allele",
    "Consequence",
    "SYMBOL",
    "AlphaMissense_score",
    "CADD_phred",
    "LoF",
    "SpliceAI_pred_DS_AG",
    "SpliceAI_pred_DS_AL",
    "SpliceAI_pred_DS_DG",
    "SpliceAI_pred_DS_DL",
    "ClinVar_CLNSIG",
]


def write_inputs(directory: pathlib.Path, alpha: str = "0.99"):
    config = directory / "config.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "reference": {"assembly": "GRCh38"},
                "container": {"vep_image_tag": "release_113.4"},
                "plugins": {"dbNSFP": {"version": "5.3.1a"}},
            }
        )
    )
    expected = directory / "expected.yaml"
    expected.write_text(
        yaml.safe_dump(
            {
                "resource_contract": {
                    "assembly": "GRCh38",
                    "dbNSFP": "5.3.1a",
                    "LoGoFunc": None,
                    "vep_image_tag": "release_113.4",
                },
                "variants": [
                    {
                        "id": "missense",
                        "key": "17-1-C-T",
                        "gene": "STAT3",
                        "consequence_any": ["missense_variant"],
                        "fields_nonempty": ["AlphaMissense_score", "CADD_phred"],
                        "clinvar_contains": "Pathogenic",
                        "info_equals": {"ClinVar_path_aa_match": "1"},
                        "optional_field_in": {
                            "LoGoFunc_prediction": ["GOF"],
                            "LoGoFunc_match": ["allele_transcript_protein"],
                        },
                    },
                    {
                        "id": "promoter",
                        "key": "5-2-G-A",
                        "gene": "TERT",
                        "consequence_any": ["upstream_gene_variant"],
                        "optional_any_field_nonempty": [
                            "promoterAI_promoterAI",
                            "promoterAI",
                        ],
                    },
                ],
            }
        )
    )
    vcf = directory / "out.vcf"
    missense = "|".join(
        ["T", "missense_variant", "STAT3", alpha, "31.0", "", "", "", "", "", "Pathogenic"]
    )
    promoter = "|".join(
        ["A", "upstream_gene_variant", "TERT", "", "", "", "", "", "", "", ""]
    )
    vcf.write_text(
        "##fileformat=VCFv4.2\n"
        '##INFO=<ID=CSQ,Number=.,Type=String,Description="Format: '
        + "|".join(FIELDS)
        + '">\n'
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
        f"17\t1\t.\tC\tT\t.\tPASS\tCSQ={missense};ClinVar_path_aa_match=1\n"
        f"5\t2\t.\tG\tA\t.\tPASS\tCSQ={promoter}\n"
    )
    return config, expected, vcf


def test_regression_passes_and_optional_track_is_explicit_skip(tmp_path):
    report = run_validation(*write_inputs(tmp_path))
    assert report["status"] == "PASS"
    assert report["counts"] == {"PASS": 4, "FAIL": 0, "SKIP": 2}


def test_regression_fails_when_required_predictor_is_empty(tmp_path):
    report = run_validation(*write_inputs(tmp_path, alpha=""))
    assert report["status"] == "FAIL"
    assert any(
        item["id"] == "missense" and item["status"] == "FAIL"
        for item in report["checks"]
    )


def test_installed_optional_field_contract_is_enforced(tmp_path):
    config, expected, vcf = write_inputs(tmp_path)
    content = vcf.read_text()
    content = content.replace(
        "|ClinVar_CLNSIG\">",
        "|ClinVar_CLNSIG|LoGoFunc_prediction|LoGoFunc_match\">",
    ).replace(
        "|Pathogenic;ClinVar_path_aa_match",
        "|Pathogenic|GOF|allele_transcript_protein;ClinVar_path_aa_match",
    )
    vcf.write_text(content)
    report = run_validation(config, expected, vcf)
    assert report["status"] == "PASS"
    assert report["counts"] == {"PASS": 5, "FAIL": 0, "SKIP": 1}

    vcf.write_text(content.replace(
        "|GOF|allele_transcript_protein;",
        "|LOF|allele_transcript_protein;",
    ))
    failed = run_validation(config, expected, vcf)
    assert failed["status"] == "FAIL"
    assert any(
        item["id"] == "missense" and item["status"] == "FAIL"
        for item in failed["checks"]
    )


if __name__ == "__main__":
    tests = [
        test_regression_passes_and_optional_track_is_explicit_skip,
        test_regression_fails_when_required_predictor_is_empty,
        test_installed_optional_field_contract_is_enforced,
    ]
    for test in tests:
        with tempfile.TemporaryDirectory() as directory:
            test(pathlib.Path(directory))
        print(f"PASS  {test.__name__}")
