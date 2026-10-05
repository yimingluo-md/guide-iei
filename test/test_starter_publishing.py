"""Mirror staging gates; synthetic files only, no network writes."""
import json

import pytest

from scripts.publish_starter_release import identity, stage


def test_reuse_unchanged_components_preserves_old_pins_not_changed_scores():
    import copy
    from scripts.publish_starter_release import reuse_unchanged_components
    old = [{"id": kind, "version": "v1", "files": [
        {"name": name, "size_bytes": 1, "sha256": "old", "url": "old-url"}
        for name in ("scores.gz", "LICENSE-NOTICES.json", "preparation.json")
    ]} for kind in ("alphamissense", "cadd", "spliceai")]
    new = copy.deepcopy(old)
    for c in new:
        c["version"] = "v2"
        c["files"][-1]["sha256"] = "new-audit"
    new[0]["files"][0]["sha256"] = "corrected-score"
    result = reuse_unchanged_components(new, {"components": old})
    assert [c["version"] for c in result] == ["v2", "v1", "v1"]
    assert result[1:] == old[1:]
    new[1]["files"][1]["sha256"] = "changed-notices"
    assert reuse_unchanged_components(new, {"components": old})[1]["version"] == "v2"


def fixtures(tmp_path):
    build = tmp_path / "build"
    report = {"installation": {"status": "PASS"}}
    preparations = {}
    for kind in ("alphamissense", "cadd", "spliceai"):
        folder = build / kind
        folder.mkdir(parents=True)
        data = folder / "data.txt"
        data.write_text("synthetic public scores")
        preparation = folder / "preparation.json"
        preparation.write_text(json.dumps({"files": [identity(data)], "release_status": "development-not-for-distribution"}))
        preparations[kind] = identity(preparation)
        report[kind] = {"status": "PASS", "preparation": identity(preparation), "records": 1, "payload_bytes": data.stat().st_size}
    report["alphamissense"]["runtime_key_uniqueness"] = "PASS"
    report["alphamissense"]["version_resolution_audit"] = {"status": "PASS"}
    audit = tmp_path / "audit.json"
    audit.write_text(json.dumps(report))
    vep = tmp_path / "vep.json"
    from pathlib import Path
    cases = json.loads((Path(__file__).parent / "contracts/alphamissense_version_collisions.json").read_text())["cases"]
    vep.write_text(json.dumps({"status": "PASS", "preparations": preparations,
                              "baked_plugins": {name: identity(Path(__file__).resolve().parents[1] / "docker" / name)
                                                for name in ("IndexedScores.pm", "SpliceAIStarter.pm", "SpliceAI-MANE1.5-gene-map.json")},
                              "alphamissense_version_collision_regressions": [c["gene"] for c in cases]}))
    return build, audit, vep


def test_staging_preserves_sources_and_promotes_only_audited_payload(tmp_path):
    build, audit, vep = fixtures(tmp_path)
    before = (build / "cadd/preparation.json").read_bytes()
    output = tmp_path / "mirror"
    components = stage(build, audit, vep, output, "v1")
    assert len(components) == 3
    assert (build / "cadd/preparation.json").read_bytes() == before
    assert json.loads((output / "cadd/preparation.json").read_text())["release_status"] == "validated"
    assert "not MIT-licensed" in (output / "README.md").read_text()
    assert "license_link: https://huggingface.co/datasets/" in (output / "README.md").read_text()
    assert "[GUIDE-IEI](https://github.com/yimingluo-md/guide-iei)" in (output / "README.md").read_text()
    assert not (output / "installation.json").exists()


@pytest.mark.parametrize("damage", ["data", "provenance", "vep", "installation", "uniqueness", "version_resolution", "regressions", "plugins"])
def test_staging_rejects_changed_or_unvalidated_build(tmp_path, damage):
    build, audit, vep = fixtures(tmp_path)
    if damage == "plugins":
        report = json.loads(vep.read_text())
        report["baked_plugins"].pop("SpliceAIStarter.pm")
        vep.write_text(json.dumps(report))
    if damage == "data":
        (build / "cadd/data.txt").write_text("corrupted")
    if damage == "provenance":
        (build / "cadd/preparation.json").write_text("{}")
    if damage == "vep":
        vep.write_text(json.dumps({"status": "PASS", "preparations": {}}))
    if damage == "installation":
        report = json.loads(audit.read_text())
        report["installation"]["status"] = "FAIL"
        audit.write_text(json.dumps(report))
    if damage == "uniqueness":
        report = json.loads(audit.read_text())
        del report["alphamissense"]["runtime_key_uniqueness"]
        audit.write_text(json.dumps(report))
    if damage == "version_resolution":
        report = json.loads(audit.read_text())
        del report["alphamissense"]["version_resolution_audit"]
        audit.write_text(json.dumps(report))
    if damage == "regressions":
        report = json.loads(vep.read_text())
        report.pop("alphamissense_version_collision_regressions")
        vep.write_text(json.dumps(report))
    output = tmp_path / "mirror"
    with pytest.raises(ValueError):
        stage(build, audit, vep, output, "v1")
    assert not output.exists()
