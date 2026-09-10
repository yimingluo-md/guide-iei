#!/usr/bin/env python3
"""Verify local signed Apple Silicon artifacts before assembly; never install or publish."""
import hashlib
import json
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def run(*args):
    return subprocess.check_output([str(arg) for arg in args], stderr=subprocess.STDOUT)


def check_metadata(metadata, version, commit):
    expected = {"version": version, "source_commit": commit, "architecture": "arm64",
                "preview": False, "uncommitted_source": False}
    for key, value in expected.items():
        if key not in metadata or type(metadata[key]) is not type(value) or metadata[key] != value:
            raise ValueError(f"Distribution metadata mismatch: {key}")
    if not isinstance(metadata.get("build_id"), str) or not metadata["build_id"]:
        raise ValueError("Missing build_id")


def check_app(app, version, commit, build_id):
    info = plistlib.loads((app / "Contents/Info.plist").read_bytes())
    if info.get("CFBundleIdentifier") != "org.guide-iei.desktop":
        raise ValueError("Not the self-contained desktop application")
    metadata = json.loads((app / "Contents/Resources/application/desktop-build.json").read_text())
    check_metadata(metadata, version, commit)
    if metadata["build_id"] != build_id:
        raise ValueError("ZIP and DMG contain different builds")
    run("codesign", "--verify", "--deep", "--strict", app)
    signature = run("codesign", "-dv", "--verbose=4", app).decode()
    if "Authority=Developer ID Application:" not in signature or "runtime" not in signature:
        raise ValueError("Developer ID hardened-runtime signature required")
    run("spctl", "--assess", "--type", "execute", app)
    run("xcrun", "stapler", "validate", app)


def verify(folder, version, commit):
    stem = f"GUIDE-IEI-macOS-{version}-arm64"
    entries = [line.split() for line in (folder / (stem + ".sha256sums.txt")).read_text().splitlines()]
    expected = {stem + suffix for suffix in (".zip", ".dmg")}
    if len(entries) != 2 or any(len(entry) != 2 for entry in entries) or {entry[1] for entry in entries} != expected:
        raise ValueError("Expected exactly the distribution ZIP and DMG checksums")
    for digest, name in entries:
        actual = hashlib.sha256()
        with (folder / name).open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                actual.update(block)
        if actual.hexdigest() != digest:
            raise ValueError(f"Checksum mismatch: {name}")
    archive = folder / (stem + ".zip")
    with zipfile.ZipFile(archive) as bundle:
        for name in bundle.namelist():
            parts = Path(name).parts
            if not parts or parts[0] != "GUIDE-IEI.app" or ".." in parts:
                raise ValueError("Unexpected ZIP path")
        metadata = json.loads(bundle.read("GUIDE-IEI.app/Contents/Resources/application/desktop-build.json"))
    check_metadata(metadata, version, commit)
    with tempfile.TemporaryDirectory(prefix="guide-iei-distribution-check-") as temporary:
        stage = Path(temporary)
        run("ditto", "-x", "-k", archive, stage)
        check_app(stage / "GUIDE-IEI.app", version, commit, metadata["build_id"])
        dmg = folder / (stem + ".dmg")
        run("codesign", "--verify", "--strict", dmg)
        run("xcrun", "stapler", "validate", dmg)
        run("spctl", "--assess", "--type", "open", "--context", "context:primary-signature", dmg)
        mount = stage / "mounted"
        mount.mkdir()
        run("hdiutil", "attach", "-readonly", "-nobrowse", "-mountpoint", mount, dmg)
        try:
            check_app(mount / "GUIDE-IEI.app", version, commit, metadata["build_id"])
        finally:
            run("hdiutil", "detach", mount)
    print(f"Verified signed, stapled ZIP and DMG: {version}, {commit}, build {metadata['build_id']}")


if __name__ == "__main__":
    if sys.platform != "darwin" or len(sys.argv) != 2:
        raise SystemExit("usage (macOS): verify_macos_distribution.py ARTIFACT_DIRECTORY")
    verify(Path(sys.argv[1]).resolve(), (ROOT / "VERSION").read_text().strip(),
           run("git", "-C", ROOT, "rev-parse", "HEAD").decode().strip())
