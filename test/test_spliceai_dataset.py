import copy
import hashlib
import json
from pathlib import Path
import subprocess

import pytest
from pipeline import spliceai_dataset as dataset
from pipeline.starter_package import apply_starters
from pipeline.build_vep_command import build_vep_command


@pytest.fixture
def tiny_release(tmp_path, monkeypatch):
    release = json.loads(dataset.PIN.read_text())
    payloads = {}
    for row in release["files"]:
        for kind in ("vcf", "index"):
            payload = (row[kind] + " fixture").encode()
            payloads[row[kind]] = payload
            row[kind + "_bytes"] = len(payload)
            row[kind + "_sha256"] = hashlib.sha256(payload).hexdigest()
    notices = {name: name.encode() for name in dataset.NOTICES}
    payloads.update(notices)
    monkeypatch.setattr(dataset, "NOTICES", {n: hashlib.sha256(v).hexdigest() for n, v in notices.items()})
    pin = tmp_path / "pin.json"
    pin.write_text(json.dumps(release))
    monkeypatch.setattr(dataset, "PIN", pin)
    calls = []
    def download(args, **kwargs):
        calls.append(args[2])
        Path(args[3]).write_bytes(payloads[args[2].removeprefix(dataset.BASE)])
    target = tmp_path / "data with spaces/mane-v1.5/manifest.json"
    return target, download, calls


def test_install_resume_no_conversion_and_no_repeat_download(tiny_release):
    target, download, calls = tiny_release
    def interrupted(args, **kwargs):
        if len(calls) == 3:
            raise subprocess.CalledProcessError(92, args)
        download(args, **kwargs)
    with pytest.raises(subprocess.CalledProcessError):
        dataset.install(target, run=interrupted)
    assert not target.exists()
    dataset.install(target, run=download)
    assert len(calls) == 50  # each unchanged VCF/index/notice downloaded once
    assert dataset.valid(target)
    dataset.install(target, run=lambda *a, **k: pytest.fail("unexpected download"))
    assert len(list((target.parent / "data").glob("*.vcf.gz"))) == 24


def test_missing_corrupt_and_incompatible_assets(tiny_release):
    target, download, _ = tiny_release
    dataset.install(target, run=download)
    release = dataset.validate(target)
    index = target.parent / release["files"][0]["index"]
    original = index.read_bytes()
    index.write_bytes(b"x" * len(original))
    assert not dataset.valid(target)
    index.write_bytes(original)
    assert dataset.valid(target)
    index.unlink()
    assert not dataset.valid(target)
    target.write_text('{"files": []}')
    assert not dataset.valid(target)


def test_repair_redownloads_only_damaged_asset(tiny_release):
    target, download, calls = tiny_release
    dataset.install(target, run=download)
    release = dataset.validate(target)
    damaged = target.parent / release["files"][3]["index"]
    damaged.write_bytes(b"broken")
    calls.clear()
    dataset.install(target, run=download)
    assert calls == [dataset.BASE + release["files"][3]["index"]]
    assert dataset.valid(target)


def test_full_table_preferred_and_command_mounts_manifest_parent(tiny_release, tmp_path):
    target, download, _ = tiny_release
    dataset.install(target, run=download)
    cfg = {"plugins": {"SpliceAI": {"enabled": True, "required": True, "format": dataset.FORMAT, "snv": str(target)}}}
    starter = {k: tmp_path / k for k in ("alphamissense", "cadd", "spliceai")}
    adjusted = apply_starters(copy.deepcopy(cfg), starter, lambda p: Path(p) if p else None)
    assert adjusted["plugins"]["SpliceAI"] == cfg["plugins"]["SpliceAI"]
    plan = build_vep_command(cfg, "/tmp/in.vcf", "/tmp/out.vcf", check_exists=False)
    assert any(p.startswith("SpliceAIStarter,shards=") for p in plan.argv)
    assert any(Path(m.host) == target.parent for m in plan.mounts)
    (target.parent / json.loads(target.read_text())["files"][-1]["index"]).unlink()
    broken = build_vep_command(cfg, "/tmp/in.vcf", "/tmp/out.vcf", check_exists=False)
    assert any("SpliceAI installation needs repair" in e for e in broken.errors)
    assert not any(p.startswith("SpliceAIStarter,") for p in broken.argv)


def test_existing_legacy_install_is_preserved(tmp_path):
    manifest = tmp_path / "spliceai/mane-v1.5/manifest.json"
    old = manifest.parent.parent / "spliceai_scores.masked.snv.ensembl_mane_v1.4.grch38.vcf.gz"
    old.parent.mkdir()
    old.write_bytes(b"legacy")
    Path(str(old) + ".tbi").write_bytes(b"index")
    cfg = {"plugins": {"SpliceAI": {"format": dataset.FORMAT, "snv": str(manifest)}}}
    apply_starters(cfg, {}, lambda p: Path(p) if p else None)
    assert cfg["plugins"]["SpliceAI"]["snv"] == str(old)
    assert "format" not in cfg["plugins"]["SpliceAI"]


def test_installer_never_replaces_unrelated_directory(tmp_path):
    unrelated = tmp_path / "important.txt"
    unrelated.write_text("keep")
    with pytest.raises(ValueError, match="dedicated SpliceAI"):
        dataset.install(tmp_path / "manifest.json")
    assert unrelated.read_text() == "keep"
