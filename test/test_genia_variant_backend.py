#!/usr/bin/env python3
"""Focused contracts for GenIA exact-allele annotation and storage."""

from __future__ import annotations

import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from local_service.cohort_store import (  # noqa: E402
    _genia_records_for_alt,
    _prediction_info_for_alt,
)
from pipeline.annotation_qc import build_report  # noqa: E402
from pipeline.genia_alleles import normalize_allele, normalize_chrom  # noqa: E402
from pipeline.genia_annotate import main as annotate_main  # noqa: E402
from pipeline.predictor_registry import (  # noqa: E402
    Adapter,
    Cardinality,
    Distribution,
    MatchScope,
    load_registry,
)


CLASS_CODES = ("P", "LP", "VUS", "LB", "B", "NC", "RF")


def expect_value_error(call, message: str) -> None:
    try:
        call()
    except ValueError as exc:
        assert message in str(exc), str(exc)
    else:  # pragma: no cover - assertion path
        raise AssertionError(f"ValueError containing {message!r} was not raised")


def write_index(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE components(id TEXT PRIMARY KEY, release_hint TEXT NOT NULL);
            CREATE TABLE variants(
              chrom TEXT NOT NULL, pos INTEGER NOT NULL, ref TEXT NOT NULL,
              alt TEXT NOT NULL, record_id TEXT NOT NULL, short_name TEXT NOT NULL,
              class_code TEXT NOT NULL, relevant_subjects INTEGER
            );
            INSERT INTO components VALUES('variant_vcf', 'GRCh38;minimal');
            """
        )
        connection.executemany(
            "INSERT INTO variants VALUES(?,?,?,?,?,?,?,?)",
            [
                ("1", 100, "A", "G", f"G-{code}", f"name {code}", code, index)
                for index, code in enumerate(CLASS_CODES)
            ]
            + [
                ("1", 100, "A", "T", "T-RF", "name,&|", "RF", 2),
                ("1", 201, "C", "T", "MINIMAL", "minimal", "VUS", None),
            ],
        )


def run_annotator(
    source: Path,
    target: Path,
    database: Path,
    *,
    allow_unavailable: bool = False,
) -> int:
    old_argv = sys.argv
    sys.argv = [
        "genia_annotate",
        "--input",
        str(source),
        "--output",
        str(target),
        "--database",
        str(database),
    ]
    if allow_unavailable:
        sys.argv.append("--allow-unavailable")
    try:
        return annotate_main()
    finally:
        sys.argv = old_argv


def parse_info(record: str) -> dict[str, str]:
    return {
        key: value
        for item in record.split("\t")[7].split(";")
        if "=" in item
        for key, value in [item.split("=", 1)]
    }


def test_normalization_is_minimal_case_insensitive_and_fully_left_aligned():
    sequence = "CAAAT"
    fetch = lambda coordinate: sequence[coordinate - 1]

    assert normalize_chrom("cHrM") == "MT"
    assert normalize_chrom("Chr1") == "1"
    assert normalize_allele(10, "AC", "AT") == (11, "C", "T")
    # Both representations move through the A repeat to the preceding C
    # anchor, not merely to the first A anchor.
    assert normalize_allele(3, "AA", "A", fetch) == (1, "CA", "C")
    assert normalize_allele(3, "A", "AA", fetch) == (1, "C", "CA")
    expect_value_error(lambda: normalize_allele(1, "A", "A"), "must differ")
    expect_value_error(lambda: normalize_allele(0, "A", "G"), "positive")


def test_annotator_is_number_a_idempotent_and_preserves_all_class_codes(tmp_path):
    database = tmp_path / "genia.sqlite3"
    write_index(database)
    source = tmp_path / "input.vcf"
    source.write_text(
        "##fileformat=VCFv4.2\n"
        '##INFO=<ID=GenIA,Number=1,Type=String,Description="stale">\n'
        '##INFO=<ID=GenIA_count,Number=1,Type=Integer,Description="stale">\n'
        '##INFO=<ID=GenIA_notes,Number=1,Type=String,Description="unrelated">\n'
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
        "1\t100\t.\tA\tG,T,*\t.\tPASS\tDP=9;GenIA=old;GenIA_count=1\n"
        "1\t200\t.\tAC\tAT\t.\tPASS\tDP=8\n",
        encoding="utf-8",
    )
    first = tmp_path / "first.vcf"
    second = tmp_path / "second.vcf"
    assert run_annotator(source, first, database) == 0
    assert run_annotator(first, second, database) == 0
    text = first.read_text(encoding="utf-8")
    assert second.read_text(encoding="utf-8") == text
    assert text.count("##INFO=<ID=GenIA,") == 1
    assert text.count("##INFO=<ID=GenIA_count,") == 1
    assert text.count("##INFO=<ID=GenIA_notes,") == 1

    records = [line for line in text.splitlines() if not line.startswith("#")]
    multi = parse_info(records[0])
    slots = multi["GenIA"].split(",")
    assert len(slots) == 3
    assert {token.split("|")[3] for token in slots[0].split("&")} == set(CLASS_CODES)
    assert all(token.startswith("G|") for token in slots[0].split("&"))
    assert slots[1].startswith("T|")
    assert "name%2C%26%7C" in slots[1]
    assert slots[2] == "."
    assert multi["GenIA_count"] == "7,1,0"
    assert parse_info(records[1])["GenIA_count"] == "1"


def test_optional_unavailable_index_strips_stale_evidence_but_required_fails(tmp_path):
    source = tmp_path / "input.vcf"
    source.write_text(
        "##fileformat=VCFv4.2\n"
        '##INFO=<ID=GenIA,Number=A,Type=String,Description="stale">\n'
        '##INFO=<ID=GenIA_count,Number=A,Type=Integer,Description="stale">\n'
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
        "1\t100\t.\tA\tG\t.\tPASS\tDP=9;GenIA=G|old;GenIA_count=1\n",
        encoding="utf-8",
    )
    missing = tmp_path / "missing.sqlite3"
    optional = tmp_path / "optional.vcf"
    assert run_annotator(
        source, optional, missing, allow_unavailable=True
    ) == 0
    optional_text = optional.read_text(encoding="utf-8")
    assert "##INFO=<ID=GenIA," not in optional_text
    assert "##INFO=<ID=GenIA_count," not in optional_text
    record = next(line for line in optional_text.splitlines() if not line.startswith("#"))
    assert record.split("\t")[7] == "DP=9"

    expect_value_error(
        lambda: run_annotator(source, tmp_path / "required.vcf", missing),
        "database is missing",
    )


def test_cohort_slicing_uses_explicit_alt_and_derives_count():
    # This deliberately looks like two Number=A comma slots, but both compact
    # records explicitly identify G. The T allele must not inherit either.
    raw = "G|one|name|P|1,G|two|name|LP|2"
    alts = ("G", "T")
    assert len(_genia_records_for_alt(raw, alt="G", alt_index=0, alts=alts)) == 2
    assert _genia_records_for_alt(raw, alt="T", alt_index=1, alts=alts) == []

    g_info = _prediction_info_for_alt(
        {"GenIA": raw, "GenIA_count": "99,99"},
        alt="G",
        alt_index=0,
        alts=alts,
    )
    t_info = _prediction_info_for_alt(
        {"GenIA": raw, "GenIA_count": "99,99"},
        alt="T",
        alt_index=1,
        alts=alts,
    )
    assert g_info["GenIA_count"] == "2"
    assert t_info["GenIA"] == ""
    assert t_info["GenIA_count"] == ""
    count_only = _prediction_info_for_alt(
        {"GenIA_count": "4,5"}, alt="T", alt_index=1, alts=alts
    )
    assert count_only["GenIA_count"] == ""


def test_qc_counts_evidence_tokens_not_untrusted_derived_count(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "plugins": {
                    "dbNSFP": {"enabled": False},
                    "LoF": {"enabled": False},
                    "SpliceAI": {"enabled": False},
                    "LoGoFunc": {"enabled": False},
                    "FuncVEP": {"enabled": False},
                },
                "post_processing": {"loftee_ptc_50bp": {"enabled": False}},
                "genia": {"enabled": True, "required": False},
                "annotation_qc": {},
            }
        ),
        encoding="utf-8",
    )
    records = "G|one|name|P|1&G|two|name|LP|2,T|three|name|RF|3"
    vcf = tmp_path / "result.vcf"
    vcf.write_text(
        "##fileformat=VCFv4.2\n"
        '##INFO=<ID=CSQ,Number=.,Type=String,Description="Format: Allele|Consequence">\n'
        '##INFO=<ID=GenIA,Number=A,Type=String,Description="test">\n'
        '##INFO=<ID=GenIA_count,Number=A,Type=Integer,Description="test">\n'
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
        f"1\t100\t.\tA\tG,T\t.\tPASS\tCSQ=G|intergenic_variant,T|intergenic_variant;"
        f"GenIA={records};GenIA_count=99,99\n",
        encoding="utf-8",
    )
    report = build_report(config, vcf)
    metric = next(
        item for item in report["metrics"]
        if item["name"] == "GenIA exact allele annotation"
    )
    assert metric["status"] == "PASS"
    assert metric["annotated_records"] == 1
    assert metric["assertions"] == 3
    # Exact-allele evidence and protein matching are independent QC metrics.
    # This fixture has no protein-match schema, but its exact evidence is valid.
    protein_metric = next(
        item for item in report["metrics"]
        if item["name"] == "GenIA P/LP protein-change and residue matching"
    )
    assert protein_metric["status"] == "SKIPPED_NOT_INSTALLED"
    assert protein_metric["annotated_records"] == 0


def test_registry_declares_independent_optional_exact_allele_contract():
    registry = load_registry()
    resource = registry.resource("genia")
    annotator = registry.annotator("genia")
    predictor = registry.predictor("genia_variant_evidence")

    assert resource.distribution is Distribution.USER_SUPPLIED
    assert resource.default_enabled is True
    assert resource.license_ack_required is False
    assert [(asset.id, asset.config_path) for asset in resource.assets] == [
        ("database", "genia.database")
    ]
    assert annotator.adapter is Adapter.POSTPROCESSOR
    assert annotator.match.scope is MatchScope.ALLELE
    assert annotator.match.cardinality is Cardinality.ZERO_OR_MANY
    assert annotator.match.fallback.value == "none"
    assert predictor.default_enabled is True
    assert predictor.optional is True
    assert [metric.field for metric in predictor.metrics] == ["GenIA", "GenIA_count"]


def test_preflight_distinguishes_gene_only_database_from_variant_component(tmp_path):
    database = tmp_path / "gene-only.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE components(id TEXT PRIMARY KEY, release_hint TEXT NOT NULL)"
        )
        connection.execute("INSERT INTO components VALUES('gei_disease', '')")
    cache = tmp_path / "cache"
    cache.mkdir()
    vcf = tmp_path / "input.vcf"
    vcf.write_text(
        "##fileformat=VCFv4.2\n"
        "##contig=<ID=1,length=248956422>\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\n"
        "1\t100\t.\tA\tG\t.\tPASS\t.\tGT\t0/1\n",
        encoding="utf-8",
    )

    def preflight(required: bool):
        config = tmp_path / f"config-{required}.yaml"
        config.write_text(
            yaml.safe_dump(
                {
                    "reference": {"assembly": "GRCh38", "vep_cache_dir": str(cache)},
                    "region": {"coding_only": False},
                    "output": {"format": "vcf", "compress": "bgzip"},
                    "container": {"runtime": "definitely-not-a-runtime"},
                    "post_processing": {"clinvar_aa_match": {"enabled": False}},
                    "genia": {
                        "enabled": True,
                        "required": required,
                        "database": str(database),
                    },
                }
            ),
            encoding="utf-8",
        )
        return subprocess.run(
            [
                "bash",
                str(Path(__file__).resolve().parents[1] / "scripts" / "preflight.sh"),
                str(config),
                str(vcf),
                str(tmp_path / f"output-{required}.vcf.gz"),
            ],
            capture_output=True,
            text=True,
        )

    optional = preflight(False)
    assert "WARN  GenIA variant component is unavailable" in optional.stderr
    assert "preflight data/config checks passed" in optional.stderr
    required = preflight(True)
    assert "ERROR GenIA variant component is unavailable" in required.stderr
    assert "preflight data/config checks passed" not in required.stderr


if __name__ == "__main__":
    test_normalization_is_minimal_case_insensitive_and_fully_left_aligned()
    test_cohort_slicing_uses_explicit_alt_and_derives_count()
    test_registry_declares_independent_optional_exact_allele_contract()
    for test in (
        test_annotator_is_number_a_idempotent_and_preserves_all_class_codes,
        test_optional_unavailable_index_strips_stale_evidence_but_required_fails,
        test_qc_counts_evidence_tokens_not_untrusted_derived_count,
        test_preflight_distinguishes_gene_only_database_from_variant_component,
    ):
        with tempfile.TemporaryDirectory() as directory:
            test(Path(directory))
        print(f"PASS  {test.__name__}")
