"""Mirror staging gates; synthetic files only, no network writes."""
import json

import pytest

from scripts.publish_starter_release import identity, stage


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
    audit = tmp_path / "audit.json"
    audit.write_text(json.dumps(report))
    vep = tmp_path / "vep.json"
    vep.write_text(json.dumps({"status": "PASS", "preparations": preparations}))
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


@pytest.mark.parametrize("damage", ["data", "provenance", "vep", "installation"])
def test_staging_rejects_changed_or_unvalidated_build(tmp_path, damage):
    build, audit, vep = fixtures(tmp_path)
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
    output = tmp_path / "mirror"
    with pytest.raises(ValueError):
        stage(build, audit, vep, output, "v1")
    assert not output.exists()
