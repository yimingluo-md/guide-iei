#!/usr/bin/env python3
"""Focused tests for the annotation coverage report."""

from __future__ import annotations

import pathlib
import sys
import tempfile

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from pipeline.annotation_qc import build_report, render_html


FIELDS = [
    "Allele",
    "Consequence",
    "SYMBOL",
    "MANE_SELECT",
    "AlphaMissense_score",
    "CADD_phred",
    "LoF",
    "LoF_filter",
    "LoF_flags",
    "LoF_info",
    "SpliceAI_pred_DS_AG",
    "SpliceAI_pred_DS_AL",
    "SpliceAI_pred_DS_DG",
    "SpliceAI_pred_DS_DL",
    "ClinVar_CLNSIG",
    "RepeatMasker",
    "SegDup",
    "PTC_dist_from_last_exon",
    "LoF_50_BP_RULE_original",
    "LoF_50_BP_RULE_PTC",
    "PTC_calc_status",
    "LoGoFunc_prediction",
    "LoGoFunc_neutral",
    "LoGoFunc_GOF",
    "LoGoFunc_LOF",
    "LoGoFunc_allele_available",
    "LoGoFunc_match",
]


def write_config(path: pathlib.Path) -> None:
    path.write_text(
        yaml.safe_dump(
            {
                "plugins": {
                    "dbNSFP": {
                        "enabled": True,
                        "required": True,
                        "columns": [
                            "AlphaMissense_score",
                            "CADD_phred",
                            "REVEL_score",
                        ]
                    },
                    "LoF": {"enabled": True, "required": True},
                    "SpliceAI": {"enabled": True, "required": True},
                    "LoGoFunc": {
                        "enabled": True,
                        "required": False,
                        "version": "Zenodo 13835271 (2024-09-24)",
                    },
                },
                "annotation_qc": {
                    "critical_dbnsfp_fields": [
                        "AlphaMissense_score",
                        "CADD_phred",
                    ],
                    "warn_below": {
                        "dbnsfp_missense": 0.8,
                        "loftee_plof": 0.9,
                        "spliceai_mane_snv": 0.9,
                    },
                },
                "post_processing": {
                    "loftee_ptc_50bp": {"enabled": True, "required": True}
                },
            }
        ),
        encoding="utf-8",
    )


def csq(*values: str) -> str:
    values = list(values) + [""] * (len(FIELDS) - len(values))
    return "|".join(values)


def write_vcf(path: pathlib.Path, missing_alpha: bool = False) -> None:
    alpha = "" if missing_alpha else "0.98"
    rows = [
        (
            "17\t42322474\t.\tC\tT\t100\tPASS\tCSQ="
            + csq(
                "T",
                "missense_variant",
                "STAT3",
                "NM_139276.3",
                alpha,
                "28.1",
                "",
                "",
                "",
                "",
                "0",
                "0",
                "0",
                "0",
                "Pathogenic",
                "",
                "",
                "",
                "",
                "",
                "ok",
                "GOF",
                "0.05",
                "0.90",
                "0.05",
                "1",
                "allele_transcript_protein",
            )
            + ";ClinVar_path_aa_match=1"
        ),
        (
            "X\t71108276\t.\tC\tT\t100\tPASS\tCSQ="
            + csq(
                "T",
                "splice_donor_variant&frameshift_variant",
                "IL2RG",
                "NM_000206.3",
                "",
                "",
                "HC",
                "",
                "",
                "",
                "0",
                "0",
                "0.99",
                "0",
                "Pathogenic",
                "",
                "",
                "100",
                "PASS",
                "PASS",
                "ok",
            )
        ),
        (
            "5\t1295113\t.\tG\tA\t100\tPASS\tCSQ="
            + csq(
                "A",
                "upstream_gene_variant",
                "TERT",
                "NM_198253.3",
            )
        ),
    ]
    path.write_text(
        "##fileformat=VCFv4.2\n"
        '##INFO=<ID=CSQ,Number=.,Type=String,Description="Format: '
        + "|".join(FIELDS)
        + '">\n'
        "##INFO=<ID=ClinVar_path_aa_match,Number=1,Type=Integer,Description=\"x\">\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\n"
        + "\n".join(row + "\tGT\t0/1" for row in rows)
        + "\n",
        encoding="utf-8",
    )


def test_report_uses_annotation_specific_denominators(tmp_path):
    config = tmp_path / "config.yaml"
    vcf = tmp_path / "result.vcf"
    write_config(config)
    write_vcf(vcf)
    report = build_report(config, vcf)
    assert report["overall_status"] == "PASS"
    assert report["summary"]["records"] == 3
    assert report["summary"]["samples"] == ["S1"]
    metrics = {item["field"]: item for item in report["metrics"] if "field" in item}
    assert metrics["AlphaMissense_score"]["eligible_records"] == 1
    assert metrics["AlphaMissense_score"]["coverage"] == 1.0
    assert report["details"]["loftee"]["eligible_records"] == 1
    assert report["details"]["loftee_ptc_50bp"]["eligible_frameshift_records"] == 1
    assert report["details"]["loftee_ptc_50bp"]["recomputed_records"] == 1
    assert report["details"]["spliceai"]["eligible_mane_snv_records"] == 2
    assert report["details"]["promoterAI"]["status"] == "SKIPPED_NOT_INSTALLED"
    assert report["details"]["logofunc"]["allele_available_records"] == 1
    assert report["details"]["logofunc"]["exact_transcript_protein_match_records"] == 1
    assert report["details"]["logofunc"]["prediction_class_counts"] == {"GOF": 1}
    rendered = render_html(report)
    assert "Annotation coverage report" in rendered
    assert "Meets configured checks" in rendered


def test_missing_critical_missense_annotation_warns_and_records_example(tmp_path):
    config = tmp_path / "config.yaml"
    vcf = tmp_path / "result.vcf"
    write_config(config)
    write_vcf(vcf, missing_alpha=True)
    report = build_report(config, vcf)
    assert report["overall_status"] == "WARN"
    assert report["details"]["missing_examples"]["AlphaMissense_score"] == [
        "17-42322474-C-T"
    ]


def test_logofunc_class_comes_from_mane_entry_not_file_order(tmp_path):
    # Audit repro (CORE-19): with --flag_pick_allele_gene all transcripts are
    # retained, so the first CSQ entry is an arbitrary transcript. The class
    # histogram must follow the MANE/picked entry, not file order.
    config = tmp_path / "config.yaml"
    vcf = tmp_path / "result.vcf"
    write_config(config)
    non_mane = csq(
        "T", "missense_variant", "STAT3", "",
        "0.98", "28.1", "", "", "", "",
        "0", "0", "0", "0",
        "Pathogenic", "", "", "", "", "", "ok",
        "LOF", "0.05", "0.05", "0.90", "1", "allele_transcript_protein",
    )
    mane = csq(
        "T", "missense_variant", "STAT3", "NM_139276.3",
        "0.98", "28.1", "", "", "", "",
        "0", "0", "0", "0",
        "Pathogenic", "", "", "", "", "", "ok",
        "GOF", "0.05", "0.90", "0.05", "1", "allele_transcript_protein",
    )
    vcf.write_text(
        "##fileformat=VCFv4.2\n"
        '##INFO=<ID=CSQ,Number=.,Type=String,Description="Format: '
        + "|".join(FIELDS)
        + '">\n'
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\n"
        f"17\t42322474\t.\tC\tT\t100\tPASS\tCSQ={non_mane},{mane}\tGT\t0/1\n",
        encoding="utf-8",
    )
    report = build_report(config, vcf)
    assert report["details"]["logofunc"]["prediction_class_counts"] == {"GOF": 1}


def test_deliberate_ptc_skips_are_not_missing_coverage(tmp_path):
    # Audit repro (CORE-12): records whose only PTC statuses are documented
    # refusals were counted as missing, driving a false WARN and a
    # remediation list of variants needing no remediation.
    config = tmp_path / "config.yaml"
    vcf = tmp_path / "result.vcf"
    write_config(config)
    ok_row = csq(
        "T", "frameshift_variant", "IL2RG", "NM_000206.3",
        "", "", "HC", "", "", "",
        "0", "0", "0", "0", "Pathogenic", "", "",
        "100", "PASS", "PASS", "ok",
    )
    skip_row = csq(
        "T", "frameshift_variant", "SINGLEEXON", "NM_999999.1",
        "", "", "", "", "", "",
        "0", "0", "0", "0", "", "", "",
        "", "", "", "not_applicable_single_exon_transcript",
    )
    vcf.write_text(
        "##fileformat=VCFv4.2\n"
        '##INFO=<ID=CSQ,Number=.,Type=String,Description="Format: '
        + "|".join(FIELDS)
        + '">\n'
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\n"
        f"X\t100\t.\tCA\tC\t100\tPASS\tCSQ={ok_row}\tGT\t0/1\n"
        f"1\t200\t.\tGA\tG\t100\tPASS\tCSQ={skip_row}\tGT\t0/1\n",
        encoding="utf-8",
    )
    report = build_report(config, vcf)
    ptc = report["details"]["loftee_ptc_50bp"]
    assert ptc["eligible_frameshift_records"] == 1
    assert ptc["recomputed_records"] == 1
    assert ptc["not_applicable_records"] == 1
    assert "LOFTEE_PTC_50BP" not in report["details"]["missing_examples"]


def test_critical_field_outside_configured_columns_is_still_counted(tmp_path):
    # Audit repro (CORE-17): a critical dbNSFP field absent from
    # plugins.dbNSFP.columns reported 0% coverage forever.
    config = tmp_path / "config.yaml"
    vcf = tmp_path / "result.vcf"
    write_config(config)
    loaded = yaml.safe_load(config.read_text())
    loaded["plugins"]["dbNSFP"]["columns"] = ["AlphaMissense_score"]
    loaded["annotation_qc"]["critical_dbnsfp_fields"] = [
        "AlphaMissense_score", "CADD_phred",
    ]
    config.write_text(yaml.safe_dump(loaded))
    write_vcf(vcf)
    report = build_report(config, vcf)
    metrics = {item["field"]: item for item in report["metrics"] if "field" in item}
    assert metrics["CADD_phred"]["coverage"] == 1.0
    assert metrics["CADD_phred"]["status"] == "PASS"


def test_disabled_plugin_is_skipped_not_failed(tmp_path):
    config = tmp_path / "config.yaml"
    vcf = tmp_path / "result.vcf"
    write_config(config)
    loaded = yaml.safe_load(config.read_text())
    loaded["plugins"]["SpliceAI"]["enabled"] = False
    config.write_text(yaml.safe_dump(loaded))
    write_vcf(vcf)
    report = build_report(config, vcf)
    splice = next(item for item in report["metrics"] if item["name"].startswith("SpliceAI"))
    assert splice["status"] == "SKIPPED_DISABLED"
    assert report["overall_status"] == "PASS"


if __name__ == "__main__":
    tests = [
        test_report_uses_annotation_specific_denominators,
        test_missing_critical_missense_annotation_warns_and_records_example,
        test_logofunc_class_comes_from_mane_entry_not_file_order,
        test_deliberate_ptc_skips_are_not_missing_coverage,
        test_critical_field_outside_configured_columns_is_still_counted,
        test_disabled_plugin_is_skipped_not_failed,
    ]
    for test in tests:
        with tempfile.TemporaryDirectory() as directory:
            test(pathlib.Path(directory))
        print(f"PASS  {test.__name__}")
