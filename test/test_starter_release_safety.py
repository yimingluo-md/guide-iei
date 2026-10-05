"""Small, offline release-gate regressions; never read large score tables."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("script", ["scripts/validate_starter_release.py",
    "test/test_starter_download.py", "test/test_starter_annotation_e2e.py"])
def test_optimized_python_refused_before_audit_io(script):
    result = subprocess.run([sys.executable, "-O", str(ROOT / script), "--help"], capture_output=True, text=True)
    assert result.returncode != 0
    assert "without -O or PYTHONOPTIMIZE" in result.stderr


@pytest.mark.parametrize("stale", [None, "IndexedScores.pm", "SpliceAIStarter.pm", "SpliceAI-MANE1.5-gene-map.json"])
def test_baked_starter_contract_checks_every_dependency(stale):
    spec = importlib.util.spec_from_file_location("starter_vep_audit", ROOT / "test/test_starter_annotation_e2e.py")
    audit = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(audit)
    verify_baked_plugins = audit.verify_baked_plugins
    checked = []
    def run(args, **kwargs):
        name = Path(args[-1]).name
        checked.append(name)
        sha = "0" * 64 if name == stale else hashlib.sha256((ROOT / "docker" / name).read_bytes()).hexdigest()
        return sha + "  " + args[-1]
    if stale:
        with pytest.raises(ValueError, match=stale.replace(".", r"\.")):
            verify_baked_plugins("synthetic", run=run)
    else:
        assert set(verify_baked_plugins("synthetic", run=run)) == set(checked)
        assert len(checked) == 3


def test_baked_checks_ignore_a_competing_stdlib_test_package(tmp_path):
    package = tmp_path / "test"
    package.mkdir()
    (package / "__init__.py").write_text("# Simulates the regular stdlib test package.\n")
    code = (
        # Make the simulated regular package win even when this interpreter
        # already ships a real stdlib test package (Ubuntu/python.org builds).
        "import sys; sys.path.insert(0, " + repr(str(tmp_path)) + "); import test; "
        "assert test.__file__ == " + repr(str(package / "__init__.py")) + "; "
        "import pytest; raise SystemExit(pytest.main(['-q', 'test/test_starter_release_safety.py', "
        "'-k', 'test_baked_starter_contract_checks_every_dependency']))"
    )
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "4 passed" in result.stdout
def test_source_sha_pin_cannot_fall_back_to_matching_md5(tmp_path):
    from scripts.build_starter_release import fetch
    source = tmp_path / "source"
    source.write_bytes(b"modified upstream bytes")
    with pytest.raises(ValueError, match="failed verification"):
        fetch("https://example.invalid/source", source, download=False,
              digest="0" * 64, md5=hashlib.md5(source.read_bytes()).hexdigest())


def test_full_version_audit_rejects_an_older_score(tmp_path):
    pysam = pytest.importorskip("pysam")
    from scripts.validate_starter_release import audit_am_version_upgrade
    old = "1\t12\tA\tG\tENST1.1\tK2R\t0.9\tlikely_pathogenic\tENST1.2\tisoforms"
    exact = old.replace("ENST1.1", "ENST1.2").replace("0.9", "0.1").replace("likely_pathogenic", "likely_benign")
    def table(name, rows):
        path = tmp_path / name
        with pysam.BGZFile(str(path), "wb") as handle:
            handle.write(("#chrom\tposition\n" + "\n".join(rows) + "\n").encode())
        pysam.tabix_index(str(path), seq_col=0, start_col=1, end_col=1, zerobased=False)
        return path
    baseline = table("old.gz", [old, exact])
    updated = table("new.gz", [exact])
    result = audit_am_version_upgrade(baseline, updated)
    assert result["mane_version_conflicts_resolved"] == result["verified_output_rows"] == 1
    with pytest.raises(AssertionError, match="Source score/version"):
        audit_am_version_upgrade(baseline, table("bad.gz", [old]))


@pytest.mark.parametrize("payload", ["[]", "null", '{"scientific_configuration":[],"files":[]}',
    '{"scientific_configuration":{},"files":[{}]}', "{bad-json"])
def test_bad_source_manifest_gets_actionable_cli_error(tmp_path, payload):
    for name in ("source.vcf", "mane.tsv", "models.gtf"):
        (tmp_path / name).write_text("synthetic")
    (tmp_path / "manifest.json").write_text(payload)
    result = subprocess.run([sys.executable, "-m", "pipeline.starter_annotations", "spliceai",
        "--source", str(tmp_path / "source.vcf"), "--output", str(tmp_path / "output"),
        "--release", "test", "--mane-summary", str(tmp_path / "mane.tsv"),
        "--gtf", str(tmp_path / "models.gtf"), "--source-manifest", str(tmp_path / "manifest.json")],
        cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 1
    assert "Starter preparation failed" in result.stderr
    assert "Traceback" not in result.stderr


def test_cadd_description_uses_supplied_release():
    from pipeline.starter_annotations import indexed_manifest
    description = indexed_manifest("cadd", "CADD-future-test")["outputs"][0]["description"]
    assert "CADD-future-test" in description
    assert "v1.7" not in description


@pytest.mark.parametrize("alt", ["AG", "G,T"])
def test_unexpected_spliceai_source_rows_fail_explicitly(tmp_path, alt):
    from pipeline.starter_annotations import spliceai_rows
    from collections import Counter
    source = tmp_path / "source.vcf"
    source.write_text("#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
                      f"1\t21\t.\tA\t{alt}\t.\t.\t.\n")
    with pytest.raises(ValueError, match="biallelic SNVs only"):
        list(spliceai_rows([source], {("1", 21): {"GENE"}}, Counter()))
