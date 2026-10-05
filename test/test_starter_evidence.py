"""Cross-language reader contracts and cohort integration."""
import json
from pathlib import Path

import pytest

from pipeline.starter_evidence import starter_observation
from local_service.cohort_store import annotation_from

CONTRACT = json.loads((Path(__file__).parent / "contracts/starter_evidence_cases.json").read_text())


@pytest.mark.parametrize("case", CONTRACT["cases"], ids=lambda c: c["name"])
def test_shared_starter_contract(case):
    record = {**CONTRACT["bases"][case["logical_id"]], **case["override"]}
    result = starter_observation(record, case["logical_id"])
    assert result["match_status"] == case["status"]
    assert result["values"] == case["values"]
    assert result["target"] == case["target"]
    assert result["provenance"] == case["provenance"]
    # The SQL score column must agree with the observation reader.
    annotation = annotation_from(record)
    column, metric = ("alpha_missense", "score") if case["logical_id"] == "alphamissense" else ("cadd", "phred")
    assert annotation[column] == case["values"].get(metric)


def test_absent_and_empty_starter_schema_are_not_equivalent():
    assert starter_observation({}, "alphamissense") is None
    record = {"AlphaMissense_score": "0.9", "CADD_phred": "40"}
    assert annotation_from(record)["alpha_missense"] == 0.9
    assert annotation_from(record)["cadd"] == 40
    record.update(StarterAM_score="", StarterCADD_phred="")
    assert annotation_from(record)["alpha_missense"] is None
    assert annotation_from(record)["cadd"] is None


def test_precedence_is_not_score_magnitude_and_wgs_cadd_still_wins():
    record = {**CONTRACT["bases"]["alphamissense"], **CONTRACT["bases"]["cadd_coding"],
              "AlphaMissense_score": "0.99", "CADD_phred": "50"}
    assert annotation_from(record)["alpha_missense"] == 0.1201
    assert annotation_from(record)["cadd"] == 23.4
    record["CADD_PHRED"] = "10"
    assert annotation_from(record)["cadd"] == 10


def test_cohort_observation_retains_provider_and_exact_annotation_binding():
    record = {**CONTRACT["bases"]["alphamissense"], "Consequence": "missense_variant"}
    annotation = annotation_from(record)
    observation = next(p for p in annotation["_predictions"] if p["predictor_id"] == "starter_alphamissense")
    assert observation["resource_id"] == "starter_alphamissense"
    assert observation["bind_annotation"] is True
    assert observation["target"]["amino_acid_change"] == "F2I"
    record["Feature"] = "ENST99999"
    annotation = annotation_from(record)
    observation = next(p for p in annotation["_predictions"] if p["predictor_id"] == "starter_alphamissense")
    assert observation["values"] == {}
    assert observation["bind_annotation"] is False


def starter_vcf(overrides=None):
    base = {"Allele": "A", "ALLELE_NUM": "1", "Consequence": "missense_variant",
            "IMPACT": "MODERATE", "SYMBOL": "GENE1", "Gene": "ENSG1", "MANE_SELECT": "NM_1.1",
            **CONTRACT["bases"]["alphamissense"], **CONTRACT["bases"]["cadd_coding"]}
    rows = [base, {**base, "StarterAM_match_status": "partial"},
            {**base, "Consequence": "intron_variant", "MANE_SELECT": "", "StarterAM_match_status": "partial"}]
    if overrides:
        rows = [{**row, **overrides} for row in rows]
    fields = list(base)
    return "\n".join([
        "##fileformat=VCFv4.2", "##reference=GRCh38",
        '##INFO=<ID=CSQ,Number=.,Type=String,Description="Format: ' + "|".join(fields) + '">',
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS",
        *(f"1\t{100 + index}\t.\tT\tA\t99\tPASS\tCSQ=" + "|".join(row.get(f, "") for f in fields)
          + "\tGT\t0/1" for index, row in enumerate(rows)), "",
    ])


def test_starter_scores_survive_sqlite_import_and_bind_to_correct_annotation(tmp_path):
    import sqlite3
    from local_service.cohort_store import CohortStore
    vcf = tmp_path / "starter.vcf"
    vcf.write_text(starter_vcf())
    database = tmp_path / "cohort.sqlite3"
    store = CohortStore(database)
    assert store.import_paths([str(vcf)])["imported"] == 1
    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            "SELECT v.pos, a.alpha_missense, a.cadd FROM cohort_annotations a "
            "JOIN cohort_variants v ON a.variant_id=v.id ORDER BY v.pos"
        ).fetchall()
        assert rows == [(100, 0.1201, 23.4), (101, None, 23.4), (102, None, 23.4)]
        observations = connection.execute(
            "SELECT v.pos, o.match_status, o.annotation_id IS NOT NULL FROM prediction_observations o "
            "JOIN cohort_variants v ON o.variant_id=v.id "
            "WHERE o.predictor_id='starter_alphamissense' ORDER BY v.pos"
        ).fetchall()
        assert observations == [(100, "exact", 1), (101, "partial", 0), (102, "partial", 0)]


def test_qc_reports_starter_coverage_separately_from_disabled_dbnsfp(tmp_path):
    import yaml
    from pipeline.annotation_qc import build_report
    config = tmp_path / "config.yaml"
    config.write_text(yaml.safe_dump({"plugins": {
        "dbNSFP": {"enabled": False}, "AlphaMissenseStarter": {"enabled": True},
        "CADDStarter": {"enabled": True},
    }}))
    vcf = tmp_path / "starter.vcf"
    vcf.write_text(starter_vcf())
    report = build_report(config, vcf)
    metrics = {m.get("field"): m for m in report["metrics"]}
    assert metrics["StarterAM_score"]["eligible_records"] == 2
    assert metrics["StarterAM_score"]["annotated_records"] == 1
    assert metrics["StarterCADD_phred"]["eligible_records"] == 2
    assert metrics["StarterCADD_phred"]["annotated_records"] == 2
    assert metrics["AlphaMissense_score"]["status"] == "SKIPPED_DISABLED"


def test_run_manifest_lists_starter_provider_contract(tmp_path):
    # This module is also a direct CLI and deliberately imports siblings.
    import sys
    import importlib.util
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "pipeline"))
    try:
        spec = importlib.util.spec_from_file_location("starter_run_manifest", root / "pipeline/write_run_manifest.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        metadata, _ = module.registry_metadata(root / "config/predictor-registry.json", {
            "plugins": {"AlphaMissenseStarter": {"enabled": True}, "dbNSFP": {"enabled": False}},
        })
        providers = metadata["configured_predictor_providers"]
        am = [p for p in providers if p["logical_id"] == "alphamissense"]
        assert len(am) == 1
        assert am[0]["resource_id"] == "starter_alphamissense"
        assert am[0]["scope"] == "allele_transcript_protein"
        assert "StarterAM_score" in am[0]["fields"]
    finally:
        sys.path.pop(0)


def test_qc_flags_duplicate_runtime_matches_even_with_passing_coverage(tmp_path):
    import yaml
    from pipeline.annotation_qc import build_report, render_html
    config = tmp_path / "config.yaml"
    config.write_text(yaml.safe_dump({"plugins": {"AlphaMissenseStarter": {"enabled": True}},
                                     "annotation_qc": {"warn_below": {"starter_alphamissense": 0}}}))
    vcf = tmp_path / "starter.vcf"
    vcf.write_text(starter_vcf({"StarterAM_match_status": "ambiguous",
                               "StarterAM_match": "multiple_exact_records"}))
    report = build_report(config, vcf)
    metric = next(m for m in report["metrics"] if m.get("field") == "StarterAM_score")
    assert metric["duplicate_match_records"] == 2
    assert metric["status"] == "WARN"
    assert "reannotate" in metric["remediation"]
    assert "2 record(s) with duplicate matches" in render_html(report)
