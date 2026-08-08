#!/usr/bin/env python3
"""Tests for the reproducibility manifest written beside each annotation run."""

from __future__ import annotations

import json
import pathlib
import subprocess
import tempfile

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "pipeline" / "write_run_manifest.py"


def test_manifest_records_config_hash_argv_and_cheap_reference_identity(tmp_path):
    fasta = tmp_path / "refs" / "genome.fa.gz"
    fasta.parent.mkdir()
    fasta.write_bytes(b"reference bytes")
    (tmp_path / "refs" / "genome.fa.gz.sha256").write_text("abc123  genome.fa.gz\n")
    config = tmp_path / "config.yaml"
    config.write_text(yaml.safe_dump({
        "reference": {"fasta": {"path": str(fasta)}, "vep_cache_dir": str(tmp_path / "cache")},
        "plugins": {},
        "custom_tracks": {},
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
    assert manifest["input"]["exists"] and manifest["output"]["exists"]

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


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as directory:
        test_manifest_records_config_hash_argv_and_cheap_reference_identity(
            pathlib.Path(directory)
        )
    print("PASS  test_manifest_records_config_hash_argv_and_cheap_reference_identity")
