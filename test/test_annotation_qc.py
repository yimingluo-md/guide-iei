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

FUNCVEP_FIELDS = [
    "FuncVEP_CTI",
    "FuncVEP_CTE",
    "FuncVEP_SP",
    "FuncVEP_allele_available",
    "FuncVEP_match",
    "FuncVEP_match_status",
    "FuncVEP_source_gene",
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
                    "FuncVEP": {
                        "enabled": False,
                        "required": False,
                        "version": "Zenodo 20595206 (v2)",
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


def write_funcvep_vcf(
    path: pathlib.Path,
    *,
    match_status: str = "exact",
    match: str = "allele_gene",
    scores: tuple[str, str, str] = ("0.91", "0.83", "0.74"),
) -> None:
    fields = FIELDS + FUNCVEP_FIELDS
    values = {
        "Allele": "T",
        "Consequence": "missense_variant",
        "SYMBOL": "STAT3",
        "MANE_SELECT": "ENST00000264657.10",
        "AlphaMissense_score": "0.98",
        "CADD_phred": "28.1",
        "SpliceAI_pred_DS_AG": "0",
        "SpliceAI_pred_DS_AL": "0",
        "SpliceAI_pred_DS_DG": "0",
        "SpliceAI_pred_DS_DL": "0",
        "FuncVEP_CTI": scores[0],
        "FuncVEP_CTE": scores[1],
        "FuncVEP_SP": scores[2],
        "FuncVEP_allele_available": "1",
        "FuncVEP_match": match,
        "FuncVEP_match_status": match_status,
        "FuncVEP_source_gene": "ENSG00000168610",
    }
    annotation = "|".join(values.get(field, "") for field in fields)
    path.write_text(
        "##fileformat=VCFv4.2\n"
        '##INFO=<ID=CSQ,Number=.,Type=String,Description="Format: '
        + "|".join(fields)
        + '">\n'
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\n"
        f"17\t42322474\t.\tC\tT\t100\tPASS\tCSQ={annotation}\tGT\t0/1\n",
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


def test_funcvep_exact_allele_gene_scores_are_covered(tmp_path):
    config = tmp_path / "config.yaml"
    vcf = tmp_path / "result.vcf"
    write_config(config)
    loaded = yaml.safe_load(config.read_text())
    loaded["plugins"]["FuncVEP"]["enabled"] = True
    config.write_text(yaml.safe_dump(loaded))
    write_funcvep_vcf(vcf)

    report = build_report(config, vcf)
    item = next(
        metric for metric in report["metrics"]
        if metric["name"].startswith("FuncVEP exact allele")
    )
    assert item["status"] == "PASS"
    assert item["schema_present"] is True
    assert item["eligible_records"] == 1
    assert item["annotated_records"] == 1
    assert item["coverage"] == 1.0
    assert report["details"]["funcvep"]["allele_available_records"] == 1
    assert report["details"]["funcvep"]["exact_allele_gene_match_records"] == 1
    assert report["details"]["funcvep"]["complete_score_records"] == 1
    assert report["details"]["funcvep"]["match_status_record_counts"] == {"exact": 1}
    assert report["annotation_profile"]["FuncVEP_match_contract"].startswith("exact GRCh38")
    assert "FuncVEP exact allele/gene scores: 1" in render_html(report)


def test_funcvep_partial_gene_match_warns_without_using_scores(tmp_path):
    config = tmp_path / "config.yaml"
    vcf = tmp_path / "result.vcf"
    write_config(config)
    loaded = yaml.safe_load(config.read_text())
    loaded["plugins"]["FuncVEP"]["enabled"] = True
    config.write_text(yaml.safe_dump(loaded))
    write_funcvep_vcf(
        vcf,
        match_status="partial",
        match="allele_only",
        scores=("", "", ""),
    )

    report = build_report(config, vcf)
    item = next(
        metric for metric in report["metrics"]
        if metric["name"].startswith("FuncVEP exact allele")
    )
    assert item["status"] == "WARN"
    assert item["annotated_records"] == 0
    assert report["overall_status"] == "WARN"
    assert report["details"]["funcvep"]["allele_available_records"] == 1
    assert report["details"]["funcvep"]["exact_allele_gene_match_records"] == 0
    assert report["details"]["missing_examples"]["FuncVEP_gene_match"] == [
        "17-42322474-C-T"
    ]


def test_optional_funcvep_without_schema_is_skipped(tmp_path):
    config = tmp_path / "config.yaml"
    vcf = tmp_path / "result.vcf"
    write_config(config)
    loaded = yaml.safe_load(config.read_text())
    loaded["plugins"]["FuncVEP"]["enabled"] = True
    config.write_text(yaml.safe_dump(loaded))
    write_vcf(vcf)

    report = build_report(config, vcf)
    item = next(
        metric for metric in report["metrics"]
        if metric["name"].startswith("FuncVEP exact allele")
    )
    assert item["status"] == "SKIPPED_NOT_INSTALLED"
    assert item["schema_present"] is False
    assert "FuncVEP_allele" not in report["details"]["missing_examples"]


def test_required_funcvep_without_schema_fails(tmp_path):
    config = tmp_path / "config.yaml"
    vcf = tmp_path / "result.vcf"
    write_config(config)
    loaded = yaml.safe_load(config.read_text())
    loaded["plugins"]["FuncVEP"].update({"enabled": True, "required": True})
    config.write_text(yaml.safe_dump(loaded))
    write_vcf(vcf)

    report = build_report(config, vcf)
    item = next(
        metric for metric in report["metrics"]
        if metric["name"].startswith("FuncVEP exact allele")
    )
    assert item["status"] == "FAIL"
    assert report["overall_status"] == "FAIL"


def test_clingen_qc_sums_per_alt_counts_instead_of_counting_slots(tmp_path):
    config = tmp_path / "config.yaml"
    vcf = tmp_path / "result.vcf"
    write_config(config)
    loaded = yaml.safe_load(config.read_text())
    loaded["clingen_erepo"] = {"enabled": True, "required": False}
    config.write_text(yaml.safe_dump(loaded))
    assertions = "&".join(
        f"G|u{index}|CA{index}|Pathogenic|D{index}|M{index}|AD|Panel|2026"
        for index in range(1, 4)
    )
    vcf.write_text(
        "##fileformat=VCFv4.2\n"
        '##INFO=<ID=CSQ,Number=.,Type=String,Description="Format: Allele|Consequence">\n'
        '##INFO=<ID=ClinGen_ERepo,Number=A,Type=String,Description="test">\n'
        '##INFO=<ID=ClinGen_ERepo_count,Number=A,Type=Integer,Description="test">\n'
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
        f"1\t100\t.\tA\tG,T\t99\tPASS\tCSQ=G|intergenic_variant,T|intergenic_variant;"
        f"ClinGen_ERepo={assertions},.;"
        "ClinGen_ERepo_count=3,0\n",
        encoding="utf-8",
    )
    report = build_report(config, vcf)
    metric = next(
        item for item in report["metrics"]
        if item["name"].startswith("ClinGen Evidence Repository")
    )
    assert metric["annotated_records"] == 1
    assert metric["assertions"] == 3


if __name__ == "__main__":
    tests = [
        test_report_uses_annotation_specific_denominators,
        test_missing_critical_missense_annotation_warns_and_records_example,
        test_logofunc_class_comes_from_mane_entry_not_file_order,
        test_deliberate_ptc_skips_are_not_missing_coverage,
        test_critical_field_outside_configured_columns_is_still_counted,
        test_disabled_plugin_is_skipped_not_failed,
        test_funcvep_exact_allele_gene_scores_are_covered,
        test_funcvep_partial_gene_match_warns_without_using_scores,
        test_optional_funcvep_without_schema_is_skipped,
        test_required_funcvep_without_schema_fails,
        test_clingen_qc_sums_per_alt_counts_instead_of_counting_slots,
    ]
    for test in tests:
        with tempfile.TemporaryDirectory() as directory:
            test(pathlib.Path(directory))
        print(f"PASS  {test.__name__}")
