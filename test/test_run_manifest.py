#!/usr/bin/env python3
"""Tests for the reproducibility manifest written beside each annotation run."""

from __future__ import annotations

import json
import hashlib
import pathlib
import subprocess
import tempfile
import sys

import pytest

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "pipeline" / "write_run_manifest.py"


def test_manifest_records_config_hash_argv_and_cheap_reference_identity(tmp_path):
    fasta = tmp_path / "refs" / "genome.fa.gz"
    fasta.parent.mkdir()
    fasta.write_bytes(b"reference bytes")
    (tmp_path / "refs" / "genome.fa.gz.sha256").write_text("abc123  genome.fa.gz\n")
    funcvep = tmp_path / "refs" / "funcvep.tsv.gz"
    funcvep.write_bytes(b"prepared FuncVEP")
    funcvep_manifest = tmp_path / "refs" / "funcvep.manifest.json"
    funcvep_manifest.write_text("{}\n")
    clingen_aa = tmp_path / "refs" / "clingen_aa_reference.tsv"
    clingen_aa.write_text("STAT3\t100\tR\tH\tENST1\n")
    genia_aa = tmp_path / "refs" / "genia_aa_reference.tsv"
    genia_aa.write_text("IL2RG\t100\tR\tH\tENST2\n")
    registry_dir = tmp_path / "config"
    registry_dir.mkdir()
    registry_path = registry_dir / "predictor-registry.json"
    registry_path.write_bytes((ROOT / "config" / "predictor-registry.json").read_bytes())
    config = tmp_path / "config.yaml"
    config.write_text(yaml.safe_dump({
        "reference": {"fasta": {"path": str(fasta)}, "vep_cache_dir": str(tmp_path / "cache")},
        "plugins": {
            "FuncVEP": {
                "enabled": True,
                "file": str(funcvep),
                "manifest": str(funcvep_manifest),
            },
        },
        # The actual pipeline gate is the parent flag; a nested flag can
        # deliberately disagree and must not change the recorded run state.
        "liftover": {
            "enabled": False,
            "grch37_to_grch38": {"enabled": True},
        },
        "custom_tracks": {},
        "clingen_erepo": {"protein_match_catalog": str(clingen_aa)},
        "genia": {"protein_match_catalog": str(genia_aa)},
    }))
    source = tmp_path / "in.vcf"
    source.write_text("##fileformat=VCFv4.2\n")
    output = tmp_path / "out.vep.vcf.gz"
    output.write_bytes(b"\x1f\x8b")
    (tmp_path / "VERSION").write_text("9.9-test\n")

    result = subprocess.run(
        [
            "python3", str(SCRIPT),
            "--config", str(config), "--base-dir", str(tmp_path),
            "--input", str(source), "--output", str(output),
            "--plan-json", json.dumps({"argv": ["vep", "-i", "in.vcf"]}),
            "--runtime", "docker", "--image", "vep-annotate:latest",
            "--image-id", "sha256:feedbeef", "--clinvar-release", "2026-08",
        ],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr

    manifest = json.loads((tmp_path / "out.vep.vcf.gz.run_manifest.json").read_text())
    assert manifest["pipeline_version"] == "9.9-test"
    assert manifest["vep_argv"] == ["vep", "-i", "in.vcf"]
    assert manifest["container"] == {
        "runtime": "docker", "image": "vep-annotate:latest", "identity": "sha256:feedbeef",
    }
    assert manifest["clinvar_release"] == "2026-08"
    assert len(manifest["config"]["sha256"]) == 64
    assert manifest["predictor_registry"]["schema_version"] == 1
    assert len(manifest["predictor_registry"]["sha256"]) == 64
    assert manifest["predictor_registry"]["predictor_count"] >= 50
    configured_resources = {
        item["id"]: item["enabled"]
        for item in manifest["predictor_registry"]["configured_resources"]
    }
    assert configured_resources["funcvep"] is True
    assert configured_resources["liftover"] is False
    assert manifest["input"]["exists"] and manifest["output"]["exists"]
    assert manifest["input"]["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()

    fasta_entry = next(
        item for item in manifest["references"] if item["path"] == str(fasta)
    )
    # cheap identity: size + mtime, plus the recorded checksum sidecar —
    # multi-GB references must never be re-hashed at run time
    assert fasta_entry["size"] == len(b"reference bytes")
    assert "mtime_ns" in fasta_entry
    assert fasta_entry["sha256_record"].startswith("abc123")
    missing_cache = next(
        item for item in manifest["references"]
        if item["path"] == str(tmp_path / "cache")
    )
    assert missing_cache["exists"] is False
    funcvep_entry = next(
        item for item in manifest["references"] if item["path"] == str(funcvep)
    )
    assert funcvep_entry["size"] == len(b"prepared FuncVEP")
    clingen_entry = next(
        item for item in manifest["references"]
        if item["path"] == str(clingen_aa)
    )
    assert clingen_entry["size"] == clingen_aa.stat().st_size
    assert clingen_entry["sha256"] == hashlib.sha256(clingen_aa.read_bytes()).hexdigest()
    genia_entry = next(
        item for item in manifest["references"]
        if item["path"] == str(genia_aa)
    )
    assert genia_entry["size"] == genia_aa.stat().st_size
    assert genia_entry["sha256"] == hashlib.sha256(genia_aa.read_bytes()).hexdigest()


@pytest.mark.parametrize("damage", ["missing", "invalid"])
@pytest.mark.parametrize("required", [False, True])
def test_invalid_sharded_spliceai_preserves_optional_run_manifest(tmp_path, damage, required):
    source_manifest = tmp_path / "spliceai/manifest.json"
    if damage == "invalid":
        source_manifest.parent.mkdir()
        source_manifest.write_text("{}")
    cfg = {"plugins": {"SpliceAI": {
        "enabled": True, "required": required, "format": "mane_v1.5_sharded",
        "snv": str(source_manifest),
    }}}
    from pipeline.build_vep_command import build_vep_command
    plan = build_vep_command(cfg, str(tmp_path / "in.vcf"), str(tmp_path / "out.vcf"), check_exists=False)
    assert bool(plan.errors) == required
    assert not any(arg.startswith("SpliceAIStarter,") for arg in plan.argv)
    config = tmp_path / "config.yaml"
    config.write_text(yaml.safe_dump(cfg))
    result = subprocess.run([
        sys.executable, str(SCRIPT), "--config", str(config), "--base-dir", str(tmp_path),
        "--input", str(tmp_path / "in.vcf"), "--output", str(tmp_path / "out.vcf"),
        "--plan-json", json.dumps({"argv": plan.argv}), "--runtime", "docker", "--image", "test",
    ], capture_output=True, text=True)
    sidecar = tmp_path / "out.vcf.run_manifest.json"
    if required:
        assert result.returncode != 0
        assert not sidecar.exists()
    else:
        assert result.returncode == 0, result.stderr
        manifest = json.loads(sidecar.read_text())
        assert manifest["vep_argv"] == plan.argv
        assert manifest["spliceai_release"]["status"] == "unavailable"
        assert manifest["spliceai_release"]["included_in_vep_command"] is False
        assert "needs repair" in manifest["spliceai_release"]["reason"]


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as directory:
        test_manifest_records_config_hash_argv_and_cheap_reference_identity(
            pathlib.Path(directory)
        )
    print("PASS  test_manifest_records_config_hash_argv_and_cheap_reference_identity")
