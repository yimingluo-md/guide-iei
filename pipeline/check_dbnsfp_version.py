#!/usr/bin/env python3
"""Report the configured dbNSFP version and recommend available updates."""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

import yaml


DEFAULT_RELEASES_URL = "https://www.dbnsfp.org/releases/"
VERSION_RE = re.compile(r"(?<![0-9])(\d+(?:\.\d+)+(?:[a-z])?)(?![0-9a-z])", re.I)
PATH_VERSION_RE = re.compile(
    r"dbNSFP[_-]?v?(\d+(?:\.\d+)+(?:[a-z])?)", re.I
)
# Accept any branch letter (or none) — the academic filter happens after the
# match. Hardcoding the current "a" suffix made a relabelled release page
# ("README v5.4", "README_v5.4a.txt") parse to nothing, which then reported
# the same update_available=false as a genuinely current install.
README_VERSION_RE = re.compile(
    r"\bREADME[_\s]+v?(\d+(?:\.\d+)+([a-z])?)\b", re.I
)


def normalize_version(value: object) -> str | None:
    if value is None:
        return None
    match = VERSION_RE.search(str(value).strip())
    return match.group(1).lower() if match else None


def version_from_path(path: object) -> str | None:
    if not path:
        return None
    match = PATH_VERSION_RE.search(os.path.basename(str(path)))
    return match.group(1).lower() if match else None


def version_key(value: str) -> tuple[tuple[int, ...], int]:
    normalized = normalize_version(value)
    if not normalized:
        raise ValueError(f"invalid dbNSFP version: {value!r}")
    match = re.fullmatch(r"(\d+(?:\.\d+)+)([a-z]?)", normalized)
    if not match:
        raise ValueError(f"invalid dbNSFP version: {value!r}")
    numbers = tuple(int(part) for part in match.group(1).split("."))
    branch = ord(match.group(2)) - ord("a") if match.group(2) else -1
    return numbers, branch


def latest_academic_version(page: str) -> str | None:
    text = html.unescape(re.sub(r"<[^>]+>", " ", page))
    academic = [
        version.lower()
        for version, branch in README_VERSION_RE.findall(text)
        # "a" is the academic branch, "c" the commercial one; a letterless
        # version is accepted as a possible future unified release.
        if branch.lower() in {"", "a"}
    ]
    if not academic:
        return None
    return max(academic, key=version_key)


def expected_ensembl_release(dbnsfp_version: str) -> int | None:
    numbers, _ = version_key(dbnsfp_version)
    if numbers[:2] == (5, 1):
        return 113
    if numbers[:2] == (5, 2):
        return 114
    if numbers[:2] == (5, 3):
        return 115
    if numbers[:2] == (5, 4):
        # dbnsfp.org/releases: "Fully rebuilt variant set based on GENCODE
        # release 50 (Ensembl release 116, June 2026)."
        return 116
    return None


def configured_vep_release(cfg: dict) -> int | None:
    tag = str(((cfg.get("container") or {}).get("vep_image_tag") or ""))
    match = re.search(r"release[_-](\d+)", tag)
    return int(match.group(1)) if match else None


def fetch_release_page(url: str, timeout: float = 10.0) -> str:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "vep-annotate-dbnsfp-update-check/1"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def build_report(
    cfg: dict,
    *,
    release_page: str | None = None,
    check_online: bool = True,
) -> dict:
    block = ((cfg.get("plugins") or {}).get("dbNSFP") or {})
    configured = normalize_version(block.get("version"))
    path_version = version_from_path(block.get("path"))
    releases_url = str(block.get("releases_url") or DEFAULT_RELEASES_URL)
    warnings: list[str] = []

    if not configured:
        configured = path_version
        if path_version:
            warnings.append(
                "plugins.dbNSFP.version is not set; inferred the version from the filename"
            )
        else:
            warnings.append(
                "plugins.dbNSFP.version is not set and no version could be "
                "derived from the filename"
            )
    if configured and path_version and configured != path_version:
        warnings.append(
            f"configured dbNSFP version {configured} does not match filename version "
            f"{path_version}"
        )

    latest = None
    online_error = None
    latest_unknown = False
    if release_page is not None:
        latest = latest_academic_version(release_page)
        latest_unknown = latest is None
    elif check_online:
        try:
            latest = latest_academic_version(fetch_release_page(releases_url))
            latest_unknown = latest is None
        except (OSError, urllib.error.URLError, TimeoutError) as error:
            online_error = str(error)
    if latest_unknown:
        warnings.append(
            "the dbNSFP release page was retrieved but no academic version "
            "could be parsed from it; the update check is INCONCLUSIVE — this "
            "is not the same as being up to date"
        )

    update_available = bool(
        configured and latest and version_key(latest) > version_key(configured)
    )
    expected_ensembl = expected_ensembl_release(configured) if configured else None
    vep_release = configured_vep_release(cfg)
    if expected_ensembl and vep_release and expected_ensembl != vep_release:
        warnings.append(
            f"dbNSFP {configured} was built on Ensembl {expected_ensembl}, while "
            f"the configured VEP image is release {vep_release}; coordinate-level "
            "lookups remain possible, but transcript-specific fields should be "
            "validated before relying on them"
        )

    return {
        "configured_version": configured,
        "path_version": path_version,
        "latest_academic_version": latest,
        "latest_unknown": latest_unknown,
        "update_available": update_available,
        "releases_url": releases_url,
        "configured_vep_release": vep_release,
        "expected_ensembl_release": expected_ensembl,
        "warnings": warnings,
        "online_error": online_error,
    }


def print_report(report: dict) -> None:
    configured = report["configured_version"] or "unknown"
    print(f"dbNSFP configured version: {configured}")
    if report["path_version"]:
        print(f"dbNSFP filename version:   {report['path_version']}")
    if report["latest_academic_version"]:
        print(
            "dbNSFP current academic:   "
            + str(report["latest_academic_version"])
        )
    if report["update_available"]:
        print(
            "UPDATE AVAILABLE: dbNSFP "
            f"{report['latest_academic_version']} is newer than {configured}. "
            "Review the release notes and transcript/VEP compatibility before updating."
        )
    elif report["latest_academic_version"] and configured:
        print("dbNSFP update status:      up to date")
    elif report.get("latest_unknown"):
        print(
            "dbNSFP update status:      INCONCLUSIVE (release page retrieved "
            "but no version parsed)"
        )
    if report["online_error"]:
        print(
            "WARN: online dbNSFP update check was unavailable; annotation can "
            f"continue offline ({report['online_error']})"
        )
    for warning in report["warnings"]:
        print(f"WARN: {warning}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        required=True,
        help="annotation YAML configuration",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="report configured compatibility without contacting dbnsfp.org",
    )
    parser.add_argument(
        "--release-html",
        type=Path,
        help="parse a saved releases page instead of contacting dbnsfp.org",
    )
    parser.add_argument("--json", action="store_true", help="emit JSON")
    args = parser.parse_args()

    cfg = yaml.safe_load(args.config.read_text(encoding="utf-8")) or {}
    page = (
        args.release_html.read_text(encoding="utf-8")
        if args.release_html
        else None
    )
    report = build_report(
        cfg,
        release_page=page,
        check_online=not args.offline and page is None,
    )
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print_report(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
