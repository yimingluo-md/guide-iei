#!/usr/bin/env python3
"""Contract tests for the typed predictor/resource registry."""

from __future__ import annotations

import copy
import json
import pathlib
import sys
from dataclasses import FrozenInstanceError

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from pipeline.predictor_registry import (  # noqa: E402
    Adapter,
    MatchDimension,
    MatchScope,
    MetricType,
    REGISTRY_PATH,
    RegistryError,
    ScoreDirection,
    load_registry,
    validate_registry_document,
)


CURRENT_SOURCE_FAMILIES = {
    "dbnsfp", "loftee", "spliceai", "repeatmasker", "segdup", "promoterai",
    "cadd_wgs", "logofunc", "clinvar", "loftee_ptc_50bp",
    "clinvar_aa_match", "liftover", "ccre", "screen_context", "clingen_erepo",
}

DBNSFP_OPTIONAL_PREDICTORS = {
    "metarnn", "primateai", "gerp", "phylop100", "phastcons100", "sift4g",
    "polyphen_hvar", "mutation_taster", "mutation_assessor", "provean", "vest4",
    "meta_svm", "meta_lr", "m_cap", "mutpred2", "mvp", "gmvp", "mpc",
    "deogen2", "bayesdel_addaf", "bayesdel_noaf", "clinpred", "list_s2",
    "varity_r", "varity_er", "esm1b", "phactboost", "mutformer", "mutscore",
    "popeve",
}


def raw_document() -> dict:
    return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))


def find(items: list[dict], identifier: str) -> dict:
    return next(item for item in items if item["id"] == identifier)


def expect_registry_error(document: dict, text: str) -> None:
    try:
        validate_registry_document(document)
    except RegistryError as exc:
        assert text in str(exc), str(exc)
    else:  # pragma: no cover - assertion path
        raise AssertionError(f"invalid registry was accepted; expected {text!r}")


def test_default_registry_loads_as_typed_immutable_contract():
    registry = load_registry()
    assert registry.schema_version == 1
    assert registry.resources and registry.annotators and registry.predictors
    assert registry.annotator("funcvep").adapter is Adapter.GENERIC_INDEXED_LOOKUP
    assert registry.predictor("funcvep").metrics[0].value_type is MetricType.FLOAT
    assert registry.predictor("funcvep").metrics[0].direction is ScoreDirection.HIGHER
    assert registry.resources_by_id is registry.resources_by_id
    assert registry.annotators_by_id is registry.annotators_by_id
    assert registry.predictors_by_id is registry.predictors_by_id
    try:
        registry.predictor("funcvep").label = "changed"  # type: ignore[misc]
    except FrozenInstanceError:
        pass
    else:  # pragma: no cover - assertion path
        raise AssertionError("typed registry definitions must be immutable")


def test_invalid_utf8_registry_is_reported_as_registry_error(tmp_path):
    path = tmp_path / "registry.json"
    path.write_bytes(b"\xff")
    try:
        load_registry(path)
    except RegistryError as exc:
        assert "could not load predictor registry" in str(exc)
    else:  # pragma: no cover - assertion path
        raise AssertionError("invalid UTF-8 registry was accepted")


def test_registry_catalogs_all_current_source_families_and_funcvep():
    registry = load_registry()
    resource_ids = set(registry.resources_by_id)
    assert CURRENT_SOURCE_FAMILIES <= resource_ids
    assert {"vep_core", "haplotype_consequences", "funcvep"} <= resource_ids

    funcvep_resource = registry.resource("funcvep")
    assert funcvep_resource.default_enabled is False
    assert funcvep_resource.license_ack_required is True
    assert funcvep_resource.distribution.value == "license_acknowledged_download"
    assert {asset.id for asset in funcvep_resource.assets} == {"scores", "manifest"}


def test_dbnsfp_optional_predictors_are_complete_and_keep_existing_columns():
    registry = load_registry()
    optional = {
        predictor.id: {metric.field for metric in predictor.metrics}
        for predictor in registry.predictors
        if predictor.resource_id == "dbnsfp" and predictor.optional
    }
    assert set(optional) == DBNSFP_OPTIONAL_PREDICTORS
    assert optional["mpc"] == {"MPC_score"}
    assert optional["esm1b"] == {"ESM1b_score", "ESM1b_pred"}
    assert optional["bayesdel_addaf"] == {
        "BayesDel_addAF_score", "BayesDel_addAF_pred",
    }
    assert optional["popeve"] == {"popEVE_score", "popEVE_pred"}
    assert {
        metric.field for metric in registry.predictor("clinvar_aa_match").metrics
    } == {"ClinVar_path_aa_match", "ClinVar_path_aa_change_match"}


def test_registry_models_existing_and_future_match_contracts():
    registry = load_registry()
    assert registry.annotator("cadd_wgs").match.scope is MatchScope.ALLELE
    assert registry.annotator("dbnsfp").match.scope is MatchScope.ALLELE_TRANSCRIPT
    assert registry.annotator("dbnsfp_allele").match.scope is MatchScope.ALLELE
    assert registry.annotator("loftee").match.scope is MatchScope.TRANSCRIPT_CONSEQUENCE
    assert registry.annotator("spliceai").match.scope is MatchScope.ALLELE_GENE_SYMBOL
    assert registry.annotator("logofunc").match.scope is MatchScope.ALLELE_TRANSCRIPT_PROTEIN
    assert registry.annotator("promoterai").match.scope is MatchScope.ALLELE_TRANSCRIPT_TSS_STRAND
    assert registry.annotator("clinvar_aa_match").match.scope is MatchScope.ALLELE
    assert registry.annotator("repeatmasker").match.scope is MatchScope.ALLELE
    assert registry.annotator("segdup").match.scope is MatchScope.ALLELE
    assert {metric.field for metric in registry.predictor("promoterai").metrics} >= {
        "PromoterAI_source_transcript", "PromoterAI_TSS", "PromoterAI_strand",
    }
    assert registry.annotator("haplotype_consequences").match.scope is MatchScope.SAMPLE_HAPLOTYPE

    funcvep = registry.annotator("funcvep")
    assert funcvep.match.scope is MatchScope.ALLELE_GENE
    assert set(funcvep.match.dimensions) == {
        MatchDimension.CHROMOSOME, MatchDimension.POSITION,
        MatchDimension.REFERENCE, MatchDimension.ALTERNATE,
        MatchDimension.ENSEMBL_GENE,
    }
    assert funcvep.match.fallback.value == "none"


def test_registry_preserves_the_real_output_contracts_for_existing_predictors():
    registry = load_registry()

    allele_dbnsfp = {
        "cadd_coding", "gerp", "phylop100", "phastcons100", "mutation_taster",
    }
    assert {
        predictor.id
        for predictor in registry.predictors
        if predictor.annotator_id == "dbnsfp_allele"
    } == allele_dbnsfp

    spliceai_fields = {
        metric.field for metric in registry.predictor("spliceai").metrics
    }
    assert spliceai_fields == {
        "SpliceAI_pred_SYMBOL",
        "SpliceAI_pred_DS_AG", "SpliceAI_pred_DS_AL",
        "SpliceAI_pred_DS_DG", "SpliceAI_pred_DS_DL",
        "SpliceAI_pred_DP_AG", "SpliceAI_pred_DP_AL",
        "SpliceAI_pred_DP_DG", "SpliceAI_pred_DP_DL",
    }

    ptc_fields = {
        metric.field for metric in registry.predictor("loftee_ptc_50bp").metrics
    }
    assert ptc_fields == {
        "PTC_cds_pos", "PTC_aa_pos", "PTC_dist_from_last_exon",
        "LoF_50_BP_RULE_original", "LoF_50_BP_RULE_PTC",
        "LoF_50_BP_RULE_changed", "PTC_dist_from_last_coding_exon",
        "LoF_50_BP_RULE_LOFTEE_anchor", "PTC_calc_status",
    }


def test_promoterai_uses_a_vep_safe_strand_encoding():
    plugin = (REGISTRY_PATH.parents[1] / "docker" / "PromoterAI.pm").read_text(
        encoding="utf-8"
    )
    assert "PromoterAI_strand => ($mapping->{strand} eq '+' ? '1' : '-1')" in plugin
    assert "PromoterAI_strand => $mapping->{strand}" not in plugin


def test_duplicate_ids_and_unknown_references_are_rejected():
    duplicate = raw_document()
    duplicate["resources"].append(copy.deepcopy(duplicate["resources"][0]))
    expect_registry_error(duplicate, "duplicate id")

    unknown = raw_document()
    find(unknown["predictors"], "funcvep")["annotator_id"] = "missing_adapter"
    expect_registry_error(unknown, "unknown annotator")

    disagreement = raw_document()
    find(disagreement["predictors"], "funcvep")["resource_id"] = "logofunc"
    expect_registry_error(disagreement, "do not agree")


def test_adapter_scope_and_dimensions_are_closed_and_consistent():
    bad_adapter = raw_document()
    find(bad_adapter["annotators"], "funcvep")["adapter"] = "arbitrary_python"
    expect_registry_error(bad_adapter, "must be one of")

    bad_scope = raw_document()
    find(bad_scope["annotators"], "funcvep")["match"]["scope"] = "whatever_matches"
    expect_registry_error(bad_scope, "must be one of")

    missing_gene = raw_document()
    find(missing_gene["annotators"], "funcvep")["match"]["dimensions"].remove(
        "ensembl_gene"
    )
    expect_registry_error(missing_gene, "do not match scope allele_gene")


def test_allele_gene_symbol_scope_supports_gene_keyed_predictors():
    document = raw_document()
    spliceai = find(document["annotators"], "spliceai")
    spliceai["match"]["scope"] = "allele_gene_symbol"
    spliceai["match"]["dimensions"] = [
        "chromosome", "position", "reference", "alternate", "gene_symbol",
    ]
    registry = validate_registry_document(document)
    match = registry.annotator("spliceai").match
    assert match.scope is MatchScope.ALLELE_GENE_SYMBOL
    assert MatchDimension.GENE_SYMBOL in match.dimensions


def test_metric_fields_types_directions_and_ranges_are_validated():
    invalid_field = raw_document()
    find(invalid_field["predictors"], "funcvep")["metrics"][0]["field"] = "bad field"
    expect_registry_error(invalid_field, "valid annotation field")

    invalid_direction = raw_document()
    find(invalid_direction["predictors"], "funcvep")["metrics"][0]["direction"] = "sideways"
    expect_registry_error(invalid_direction, "must be one of")

    categorical_direction = raw_document()
    find(categorical_direction["predictors"], "logofunc")["metrics"][0]["direction"] = "higher"
    expect_registry_error(categorical_direction, "must be none for non-numeric")

    backwards_range = raw_document()
    find(backwards_range["predictors"], "funcvep")["metrics"][0]["value_range"] = [1, 0]
    expect_registry_error(backwards_range, "minimum exceeds maximum")


def test_config_paths_are_valid_and_assets_stay_under_their_resource():
    bad_root = raw_document()
    find(bad_root["resources"], "funcvep")["config_path"] = "unknown.FuncVEP"
    expect_registry_error(bad_root, "valid config path")

    escaped_asset = raw_document()
    find(escaped_asset["resources"], "funcvep")["assets"][0]["config_path"] = (
        "plugins.LoGoFunc.file"
    )
    expect_registry_error(escaped_asset, "must be below resource config path plugins.FuncVEP")


def test_generic_indexed_adapter_uses_one_matching_plugin_block():
    nested = raw_document()
    find(nested["annotators"], "funcvep")["config_path"] = "plugins.FuncVEP.file"
    expect_registry_error(nested, "must be exactly plugins.<block>")

    disagreement = raw_document()
    find(disagreement["annotators"], "funcvep")["config_path"] = "plugins.LoGoFunc"
    expect_registry_error(
        disagreement,
        "must agree with resource funcvep config path plugins.FuncVEP",
    )


if __name__ == "__main__":
    tests = [
        test_default_registry_loads_as_typed_immutable_contract,
        test_registry_catalogs_all_current_source_families_and_funcvep,
        test_dbnsfp_optional_predictors_are_complete_and_keep_existing_columns,
        test_registry_models_existing_and_future_match_contracts,
        test_registry_preserves_the_real_output_contracts_for_existing_predictors,
        test_promoterai_uses_a_vep_safe_strand_encoding,
        test_duplicate_ids_and_unknown_references_are_rejected,
        test_adapter_scope_and_dimensions_are_closed_and_consistent,
        test_allele_gene_symbol_scope_supports_gene_keyed_predictors,
        test_metric_fields_types_directions_and_ranges_are_validated,
        test_config_paths_are_valid_and_assets_stay_under_their_resource,
        test_generic_indexed_adapter_uses_one_matching_plugin_block,
    ]
    for test in tests:
        test()
        print(f"PASS  {test.__name__}")
    print(f"\n{len(tests)} tests passed")
