import copy
import hashlib
import json
from pathlib import Path
import subprocess
import os
import shutil
import threading

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
    # Restoring bytes changes mtime: only explicit verification may renew trust.
    assert not dataset.valid(target)
    dataset.install(target, run=lambda *a, **k: pytest.fail("unchanged bytes must not download"))
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
    release = dataset.validate(target)
    assert set(plan.reference_paths) == {str(target), *(str(target.parent / row["vcf"]) for row in release["files"])}
    from pipeline.container_access import collect_paths
    probes = {str(item.path) for item in collect_paths(cfg, "/tmp/in.vcf", "/tmp/out.vcf", tmp_path)}
    assert all(str(target.parent / row[key]) in probes for row in release["files"] for key in ("vcf", "index"))
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


def test_release_install_default_matches_shipped_config():
    import yaml
    shipped = yaml.safe_load((dataset.ROOT / "config/annotation.config.yaml").read_text())
    assert dataset.full_install_config() == shipped["plugins"]["SpliceAI"]


def test_legacy_upgrade_uses_new_destination_then_activates_it(tiny_release):
    import yaml
    from local_service.test_workbench_service import AnnotationJobServiceTests
    _, download, _ = tiny_release
    case = AnnotationJobServiceTests()
    case.setUp()
    try:
        config_path = case.root / "config/annotation.config.yaml"
        config = yaml.safe_load(config_path.read_text())
        old = case.root / "references/spliceai/legacy.vcf.gz"
        old.parent.mkdir(parents=True)
        old.write_bytes(b"legacy")
        Path(str(old) + ".tbi").write_bytes(b"legacy index")
        config["plugins"]["SpliceAI"] = {"enabled": True, "required": False, "snv": str(old)}
        config_path.write_text(yaml.safe_dump(config))
        original = config_path.read_bytes()
        targets = []
        for action in ("spliceai", "recommended_wgs"):
            generated = yaml.safe_load(case.service._write_resource_config(action).read_text())
            block = generated["plugins"]["SpliceAI"]
            assert block["format"] == dataset.FORMAT
            targets.append(Path(block["snv"]))
        assert targets[0] == targets[1]
        assert targets[0].is_relative_to(case.service.annotation_root)
        profile = case.service._annotation_profile()
        assert "spliceai" in profile["recommended_profiles"]["whole_genome"]["missing"]
        dataset.install(targets[0], run=download)
        profile = case.service._annotation_profile()
        assert "spliceai" not in profile["recommended_profiles"]["whole_genome"]["missing"]
        source = next(s for s in profile["sources"] if s["id"] == "spliceai")
        assert source["installed"]
        active = case.service._prefer_installed_managed_resources(config)["plugins"]["SpliceAI"]
        assert active["format"] == dataset.FORMAT
        assert active["required"] is False
        assert case.service._resolved_reference_path(active["snv"]) == targets[0]
        assert config_path.read_bytes() == original
        assert old.read_bytes() == b"legacy"
    finally:
        case.tearDown()


def test_full_install_preserves_explicit_sharded_destination():
    custom = {"format": dataset.FORMAT, "snv": "/chosen-drive/custom/manifest.json", "enabled": False}
    assert dataset.full_install_config(custom) == custom
    assert dataset.full_install_config(custom) is not custom


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("method", ["copy2", "verified"])
def test_migration_and_chmod_never_hash_in_status_checks(tiny_release, tmp_path, monkeypatch, legacy, method):
    from local_service.workbench_service import AnnotationJobService
    target, download, _ = tiny_release
    dataset.install(target, run=download)
    receipt = target.parent / "installation.json"
    if legacy:
        saved = json.loads(receipt.read_text())
        for name, signature in saved["files"].items():
            signature.append((target.parent / name).stat().st_ctime_ns)
        receipt.write_text(json.dumps(saved))
    moved = tmp_path / "migrated"
    service = AnnotationJobService.__new__(AnnotationJobService)
    service._stop = threading.Event()
    copier = shutil.copy2 if method == "copy2" else lambda s, d: service._copy_file_verified(Path(s), Path(d))
    shutil.copytree(target.parent, moved, copy_function=copier)
    before = (moved / "installation.json").read_bytes()
    for name, _, _ in dataset.assets(json.loads(target.read_text())):
        (moved / name).chmod(0o600)
    monkeypatch.setattr(dataset, "digest", lambda p: pytest.fail("readiness must never hash"))
    for _ in range(3):
        assert dataset.valid(moved / "manifest.json")
        dataset.validate(moved / "manifest.json")
    assert (moved / "installation.json").read_bytes() == before


def test_repair_backup_cleanup_never_hashes_surviving_hard_links(tiny_release, monkeypatch):
    target, download, _ = tiny_release
    dataset.install(target, run=download)
    release = dataset.validate(target)
    (target.parent / release["files"][3]["index"]).write_bytes(b"broken")
    dataset.install(target, run=download)
    backups = list(target.parent.parent.glob(target.parent.name + ".replaced-*"))
    assert len(backups) == 1
    intact = release["files"][0]["vcf"]
    assert (target.parent / intact).stat().st_ino == (backups[0] / intact).stat().st_ino
    shutil.rmtree(backups[0])  # Only this test's temporary backup.
    monkeypatch.setattr(dataset, "digest", lambda p: pytest.fail("cleanup must not cause hashing"))
    assert dataset.valid(target)
    assert dataset.valid(target)


@pytest.mark.parametrize("damage", ["mtime", "missing", "truncated", "receipt", "legacy-malformed"])
def test_stale_or_incomplete_install_fails_fast_without_hashing(tiny_release, monkeypatch, damage):
    target, download, _ = tiny_release
    dataset.install(target, run=download)
    release = dataset.validate(target)
    file = target.parent / release["files"][0]["vcf"]
    if damage == "mtime":
        st = file.stat(); os.utime(file, ns=(st.st_atime_ns, st.st_mtime_ns + 1000000))
    elif damage == "missing": file.unlink()
    elif damage == "truncated": file.write_bytes(b"x")
    elif damage == "receipt": (target.parent / "installation.json").write_text("{}")
    else:
        receipt = json.loads((target.parent / "installation.json").read_text())
        receipt["files"][release["files"][0]["vcf"]] = [file.stat().st_size]
        (target.parent / "installation.json").write_text(json.dumps(receipt))
    monkeypatch.setattr(dataset, "digest", lambda p: pytest.fail("status must never hash changed files"))
    assert not dataset.valid(target)
    with pytest.raises(ValueError, match="needs repair"):
        dataset.validate(target)


def test_explicit_verification_hashes_all_and_repairs_same_stat_corruption(tiny_release, monkeypatch):
    from unittest.mock import Mock
    target, download, calls = tiny_release
    dataset.install(target, run=download)
    hashes = Mock(wraps=dataset.digest)
    monkeypatch.setattr(dataset, "digest", hashes)
    release = dataset.validate(target, strict=True)
    assert hashes.call_count == 50
    file = target.parent / release["files"][0]["vcf"]
    st = file.stat()
    file.write_bytes(b"x" * st.st_size)
    os.utime(file, ns=(st.st_atime_ns, st.st_mtime_ns))
    # Metadata checks cannot promise cryptographic integrity.
    assert dataset.valid(target)
    with pytest.raises(ValueError, match="checksum mismatch"):
        dataset.validate(target, strict=True)
    calls.clear()
    dataset.install(target, run=download)
    assert calls == [dataset.BASE + release["files"][0]["vcf"]]
    dataset.validate(target, strict=True)


def test_explicit_reverification_refreshes_stale_receipt_without_copying(tiny_release, monkeypatch):
    from unittest.mock import Mock
    target, download, _ = tiny_release
    dataset.install(target, run=download)
    release = dataset.validate(target)
    file = target.parent / release["files"][0]["vcf"]
    st = file.stat(); os.utime(file, ns=(st.st_atime_ns, st.st_mtime_ns + 1000000))
    hashes = Mock(wraps=dataset.digest)
    monkeypatch.setattr(dataset, "digest", hashes)
    assert not dataset.valid(target)
    dataset.install(target, run=lambda *a, **k: pytest.fail("verification must reuse files"))
    assert hashes.call_count == 50
    hashes.reset_mock()
    assert dataset.valid(target)
    assert hashes.call_count == 0
    assert file.stat().st_ino == st.st_ino
    assert not list(target.parent.parent.glob(target.parent.name + ".replaced-*"))
    assert all(len(s) == 2 for s in json.loads((target.parent / "installation.json").read_text())["files"].values())
