#!/usr/bin/env python3
"""Tests for advisory dbNSFP release and compatibility checks."""

from __future__ import annotations

import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from pipeline.check_dbnsfp_version import (  # noqa: E402
    build_report,
    latest_academic_version,
    version_from_path,
)


RELEASE_PAGE = """
<h2>Current Release</h2>
<p>dbNSFP v5.3.1 (January 1, 2026)
<a>README v5.3.1a</a> <a>README v5.3.1c</a></p>
<h2>Release History</h2>
<p>dbNSFP v5.3 <a>README v5.3a</a></p>
"""


def config(version="5.3.1a", path_version="5.3.1a", vep=115):
    return {
        "container": {"vep_image_tag": f"release_{vep}.1"},
        "plugins": {
            "dbNSFP": {
                "enabled": True,
                "required": True,
                "version": version,
                "path": f"/refs/dbNSFP{path_version}_grch38.gz",
            }
        },
    }


def test_latest_academic_ignores_commercial_branch():
    assert latest_academic_version(RELEASE_PAGE) == "5.3.1a"


def test_version_detected_from_filename():
    assert version_from_path("/x/dbNSFP5.3.1a_grch38.gz") == "5.3.1a"


def test_current_release_is_up_to_date():
    report = build_report(config(), release_page=RELEASE_PAGE)
    assert report["latest_academic_version"] == "5.3.1a"
    assert report["update_available"] is False
    assert not report["warnings"]


def test_older_release_recommends_update():
    report = build_report(
        config(version="5.2a", path_version="5.2a", vep=114),
        release_page=RELEASE_PAGE,
    )
    assert report["update_available"] is True


def test_filename_and_vep_mismatch_are_reported():
    report = build_report(
        config(version="5.3.1a", path_version="5.2a", vep=113),
        release_page=RELEASE_PAGE,
    )
    assert any("does not match filename" in value for value in report["warnings"])
    assert any("Ensembl 115" in value for value in report["warnings"])


if __name__ == "__main__":
    tests = [
        test_latest_academic_ignores_commercial_branch,
        test_version_detected_from_filename,
        test_current_release_is_up_to_date,
        test_older_release_recommends_update,
        test_filename_and_vep_mismatch_are_reported,
    ]
    for test in tests:
        with tempfile.TemporaryDirectory():
            test()
        print(f"PASS  {test.__name__}")
    print(f"\n{len(tests)} tests passed")
