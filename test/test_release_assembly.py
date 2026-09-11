#!/usr/bin/env python3
"""Offline guard tests for release identity and draft-only assembly."""
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("distribution", ROOT / "scripts/verify_macos_distribution.py")
DIST = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DIST)


class ReleaseTests(unittest.TestCase):
    def test_distribution_rejects_preview_dirty_wrong_commit_and_wrong_arch(self):
        metadata = dict(version="0.6.2", source_commit="abc", architecture="arm64",
                        preview=False, uncommitted_source=False, build_id="build")
        DIST.check_metadata(metadata, "0.6.2", "abc")
        for key, value in (("version", "0.6.1"), ("source_commit", "old"),
                           ("architecture", "x86_64"), ("preview", True),
                           ("uncommitted_source", True), ("preview", 0), ("build_id", "")):
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                DIST.check_metadata(dict(metadata, **{key: value}), "0.6.2", "abc")
        for key in metadata:
            incomplete = dict(metadata)
            del incomplete[key]
            with self.subTest(missing=key), self.assertRaises(ValueError):
                DIST.check_metadata(incomplete, "0.6.2", "abc")

    def test_unsafe_release_modes_fail_before_any_mutation(self):
        for args in ([], ["--publish"], ["--draft", "--source-only"],
                     ["--macos-artifacts", "/absent"],
                     ["--source-only", "--engine-sources", "/absent"],
                     ["--source-only", "--macos-artifacts", "/absent"]):
            result = subprocess.run(["bash", str(ROOT / "scripts/make_release.sh"), *args],
                                    text=True, capture_output=True)
            self.assertNotEqual(result.returncode, 0)

    def test_source_assembly_from_clean_fixture_does_not_build_legacy_app(self):
        import shutil
        import zipfile
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "scripts").mkdir()
            shutil.copy2(ROOT / "scripts/make_release.sh", root / "scripts/make_release.sh")
            (root / "VERSION").write_text("1.2.3\n")
            (root / "CHANGELOG.md").write_text("## 1.2.3 — today\n\nSynthetic release\n")
            (root / ".gitignore").write_text("dist/\n")
            for argv in (["init", "-q"], ["add", "."],
                         ["-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "fixture"]):
                subprocess.run(["git", *argv], cwd=root, check=True, capture_output=True)
            result = subprocess.run(["bash", "scripts/make_release.sh", "--source-only"],
                                    cwd=root, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            output = root / "dist/release-1.2.3"
            self.assertEqual({p.name for p in output.iterdir()},
                             {"guide-iei-1.2.3.zip", "sha256sums.txt", "release-notes.md"})
            with zipfile.ZipFile(output / "guide-iei-1.2.3.zip") as archive:
                self.assertIn("release-manifest.txt", archive.namelist())
            self.assertEqual((output / "release-notes.md").read_text().strip(), "Synthetic release")
            again = subprocess.run(["bash", "scripts/make_release.sh", "--source-only"], cwd=root,
                                   capture_output=True, text=True)
            self.assertNotEqual(again.returncode, 0)
            self.assertIn("already exists", again.stderr)


if __name__ == "__main__":
    unittest.main()
