"""Synthetic downloadable components; no public downloads or workstation data."""
import copy
import hashlib
import json
from pathlib import Path

import pytest

from pipeline.starter_package import load_plan, install, installed_paths, component_path, apply_starters


@pytest.fixture
def release(tmp_path):
    content = {}
    components = []
    for kind in ("alphamissense", "cadd", "spliceai"):
        score = kind + (".vcf.gz" if kind == "spliceai" else ".tsv.gz")
        names = [score, score + ".tbi", "preparation.json", "LICENSE-NOTICES.json"]
        if kind != "spliceai":
            names.append("indexed.manifest.json")
        files = []
        for name in names:
            data = (kind + ":" + name).encode()
            url = f"https://huggingface.co/datasets/example/starter/resolve/{'a' * 40}/{kind}/{name}"
            content[url] = data
            files.append(dict(name=name, size_bytes=len(data), sha256=hashlib.sha256(data).hexdigest(), url=url))
        components.append(dict(id=kind, version="v1", files=files))
    plan = dict(schema="guide-iei.essential-annotations/v1", release_status="ready",
                assembly="GRCh38", vep_release=113, components=components)
    path = tmp_path / "release.json"
    path.write_text(json.dumps(plan))
    return path, content


def downloader(content, calls):
    def run(args, **kwargs):
        calls.append(args)
        assert args[-2:] == ["--connections", "4"]
        Path(args[3]).write_bytes(content[args[2]])
    return run


def test_install_publishes_verified_components_and_retry_reuses_them(tmp_path, release):
    path, content = release
    plan = load_plan(path)
    root = tmp_path / "references"
    calls = []
    install(root, plan, downloader(content, calls))
    assert set(installed_paths(root, plan)) == {"alphamissense", "cadd", "spliceai"}
    assert len(calls) == 14
    install(root, plan, lambda *a, **k: pytest.fail("completed assets must not download again"))


def test_bad_download_not_published_and_retry_recovers(tmp_path, release):
    path, content = release
    plan = load_plan(path)
    bad = {key: b"corrupt" for key in content}
    root = tmp_path / "references"
    with pytest.raises(ValueError, match="verification failed"):
        install(root, plan, downloader(bad, []))
    assert not component_path(root, plan["components"][0]).exists()
    install(root, plan, downloader(content, []))
    assert len(installed_paths(root, plan)) == 3


def test_am_only_release_upgrade_preserves_other_components_and_old_results(tmp_path, release):
    from local_service.essential_setup import package_status
    path, content = release
    old = load_plan(path)
    root = tmp_path / "references"
    install(root, old, downloader(content, []))
    old_am = component_path(root, old["components"][0])
    new = copy.deepcopy(old)
    am = new["components"][0]
    am["version"] = "v2"
    for asset in am["files"]:
        url = asset["url"].replace("a" * 40, "b" * 40)
        data = content[asset["url"]] + b":corrected"
        content[url] = data
        asset.update(url=url, size_bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
    path.write_text(json.dumps(new))
    assert package_status(root, path)["missing"] == ["alphamissense"]
    calls = []
    install(root, new, downloader(content, calls))
    assert len(calls) == len(am["files"])
    assert all("/alphamissense/" in call[2] for call in calls)
    assert old_am.is_dir()
    assert package_status(root, path)["available"]
    config = apply_starters({"plugins": {}}, installed_paths(root, new), lambda p: Path(p) if p else None)
    assert "/v2/" in config["plugins"]["AlphaMissenseStarter"]["file"]


def test_changed_file_invalidates_installation_and_repair_preserves_old_copy(tmp_path, release):
    path, content = release
    plan = load_plan(path)
    root = tmp_path / "references"
    install(root, plan, downloader(content, []))
    directory = component_path(root, plan["components"][0])
    score = directory / "alphamissense.tsv.gz"
    score.write_bytes(b"x" * score.stat().st_size)
    assert "alphamissense" not in installed_paths(root, plan)
    install(root, plan, downloader(content, []))
    assert len(installed_paths(root, plan)) == 3
    assert list(directory.parent.glob("v1.replaced-*"))


@pytest.mark.parametrize("damage", ["draft", "missing", "traversal", "duplicate", "checksum", "url", "size", "mutable_revision"])
def test_reject_invalid_release_before_any_download(release, damage):
    path, _ = release
    plan = json.loads(path.read_text())
    asset = plan["components"][0]["files"][0]
    if damage == "draft": plan["release_status"] = "preparing"
    if damage == "missing": plan["components"].pop()
    if damage == "traversal": asset["name"] = "../../outside"
    if damage == "duplicate": plan["components"][0]["files"].append(asset)
    if damage == "checksum": asset["sha256"] = ""
    if damage == "url": asset["url"] = "file:///etc/passwd"
    if damage == "size": asset["size_bytes"] = -1
    if damage == "mutable_revision": asset["url"] = asset["url"].replace("a" * 40, "main")
    path.write_text(json.dumps(plan))
    with pytest.raises(ValueError): load_plan(path)


def test_partial_package_does_not_relax_readiness(tmp_path):
    config = {"plugins": {"dbNSFP": {"enabled": True, "required": True}}}
    before = copy.deepcopy(config)
    assert apply_starters(config, {"alphamissense": tmp_path}, lambda p: Path(p) if p else None) == before


def test_starter_activation_preserves_installed_full_sources_and_explicit_provider_choice(tmp_path):
    full = tmp_path / "full.vcf.gz"
    full.write_text("synthetic")
    Path(str(full) + ".tbi").write_text("synthetic index")
    paths = {key: tmp_path / key for key in ("alphamissense", "cadd", "spliceai")}
    config = {"plugins": {"dbNSFP": {"path": str(full), "enabled": False, "required": True},
                          "SpliceAI": {"snv": str(full), "enabled": False},
                          "AlphaMissenseStarter": {"file": "custom", "enabled": False}}}
    apply_starters(config, paths, lambda p: Path(p) if p else None)
    assert config["plugins"]["dbNSFP"] == {"path": str(full), "enabled": False, "required": False}
    assert config["plugins"]["SpliceAI"] == {"snv": str(full), "enabled": False}
    assert config["plugins"]["AlphaMissenseStarter"] == {"file": "custom", "enabled": False}
    assert config["plugins"]["CADDStarter"]["enabled"]


@pytest.mark.parametrize("name", ["AlphaMissenseStarter", "CADDStarter"])
def test_explicit_disabled_pathless_starter_stays_disabled(tmp_path, name):
    cfg = {"plugins": {name: {"enabled": False}}}
    paths = {key: tmp_path / key for key in ("alphamissense", "cadd", "spliceai")}
    apply_starters(cfg, paths, lambda p: Path(p) if p else None)
    assert cfg["plugins"][name] == {"enabled": False}


def test_shipped_auto_starter_defaults_activate_after_installation(tmp_path):
    import yaml
    cfg = yaml.safe_load((Path(__file__).resolve().parents[1] / "config/annotation.config.yaml").read_text())
    paths = {key: tmp_path / key for key in ("alphamissense", "cadd", "spliceai")}
    for name in ("AlphaMissenseStarter", "CADDStarter"):
        assert "enabled" not in cfg["plugins"][name]
    apply_starters(cfg, paths, lambda p: Path(p) if p else None)
    assert all(cfg["plugins"][name]["enabled"] for name in ("AlphaMissenseStarter", "CADDStarter"))


def test_one_click_orders_engine_references_starters_then_official_clinical_sources(tmp_path, release, monkeypatch):
    import yaml
    from types import SimpleNamespace
    from scripts.install_essential_annotations import prepare
    monkeypatch.setattr("scripts.install_essential_annotations.shutil.disk_usage",
                        lambda path: SimpleNamespace(free=1024**4))
    monkeypatch.setattr("scripts.install_essential_annotations.managed_colima_environment", lambda env: env)
    from local_service.essential_setup import required_files
    path, content = release
    root = tmp_path / "chosen-drive"
    root.mkdir()
    source_config = Path(__file__).resolve().parents[1] / "config/annotation.config.yaml"
    calls = []
    def run(args, **kwargs):
        name = Path(args[1]).name
        calls.append(name)
        if name == "parallel_fetch.py":
            return downloader(content, [])(args, **kwargs)
        config_path = Path(args[-1] if name == "setup_environment.sh" else args[2])
        config = yaml.safe_load(config_path.read_text())
        assert str(root) in config["reference"]["fasta"]["path"]
        resources = required_files(config, Path)
        if name == "download_references.sh":
            assert "spliceai" not in args[4]
            assert not {"ccre", "liftover", "repeatmasker", "segdup"}.intersection(args[4].split(","))
            names = set(resources) - {"ClinVar", "ClinGen"}
        elif name == "fetch_clinvar.sh":
            assert config["clinvar"]["auto_fetch"] is True
            names = {"ClinVar"}
        elif name == "update_clingen_erepo.sh":
            assert all(p.exists() for p in resources["ClinVar"])
            names = {"ClinGen"}
        else:
            names = set()
        for label in names:
            for target in resources[label]:
                if target.name == "113_GRCh38":
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text("synthetic")
    before = source_config.read_bytes()
    prepare(source_config, root, path, run=run)
    assert source_config.read_bytes() == before
    assert calls[:2] == ["setup_environment.sh", "download_references.sh"]
    assert calls[-2:] == ["fetch_clinvar.sh", "update_clingen_erepo.sh"]
    assert len(installed_paths(root, load_plan(path))) == 3
    calls.clear()
    prepare(source_config, root, path, run=run)
    assert "fetch_clinvar.sh" not in calls and "update_clingen_erepo.sh" not in calls
    assert "parallel_fetch.py" not in calls


def test_essential_setup_low_space_fails_before_starting_tools(tmp_path, release, monkeypatch):
    from types import SimpleNamespace
    from scripts.install_essential_annotations import prepare
    monkeypatch.setattr("scripts.install_essential_annotations.shutil.disk_usage",
                        lambda path: SimpleNamespace(free=0))
    with pytest.raises(ValueError, match="Not enough space"):
        prepare(Path(__file__).resolve().parents[1] / "config/annotation.config.yaml",
                tmp_path, release[0], run=lambda *a, **kw: pytest.fail("must not start tools"))


def test_unreleased_package_never_starts_environment_preparation(tmp_path):
    from scripts.install_essential_annotations import prepare
    unavailable = tmp_path / "unavailable.json"
    unavailable.write_text(json.dumps({"schema": "guide-iei.essential-annotations/v1",
        "assembly": "GRCh38", "vep_release": 113, "release_status": "preparing",
        "message": "still being prepared"}))
    with pytest.raises(ValueError, match="still being prepared"):
        prepare(Path(__file__).resolve().parents[1] / "config/annotation.config.yaml", tmp_path,
                plan_path=unavailable,
                run=lambda *a, **k: pytest.fail("must fail before installing anything"))
