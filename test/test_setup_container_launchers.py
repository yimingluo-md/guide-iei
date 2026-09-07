#!/usr/bin/env python3
"""Offline fresh/repair/check coverage for the actual macOS setup helper."""
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "scripts/macos_container_dependencies.sh"


class LimaLauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "tools with spaces"
        (self.root / "bin").mkdir(parents=True)
        self.bundle = self.root / "lima-2.2.0/bin"
        self.bundle.mkdir(parents=True)
        for name in ("lima", "limactl"):
            path = self.bundle / name
            path.write_text("#!/bin/sh\nexit 0\n")
            path.chmod(0o755)

    def run_helper(self, mode):
        return subprocess.run([
            "bash", "-c",
            'source "$1"; ok() { echo "$1"; }; fix() { echo "$1"; }; ensure_macos_lima_launchers "$2" 2.2.0 "$3"',
            "test", str(HELPER), str(self.root), mode,
        ], text=True, capture_output=True)

    def test_fresh_install_links_both_and_second_run_is_idempotent(self):
        self.assertEqual(self.run_helper("install").returncode, 0)
        for name in ("lima", "limactl"):
            self.assertEqual((self.root / "bin" / name).resolve(), (self.bundle / name).resolve())
        before = (self.root / "bin/lima").lstat().st_mtime_ns
        self.assertEqual(self.run_helper("install").returncode, 0)
        self.assertEqual((self.root / "bin/lima").lstat().st_mtime_ns, before)

    def test_doctor_does_not_write_and_install_repairs_old_partial_stack(self):
        (self.root / "bin/limactl").symlink_to(self.bundle / "limactl")
        before = (self.root / "bin/limactl").lstat().st_mtime_ns
        self.assertNotEqual(self.run_helper("check").returncode, 0)
        self.assertFalse((self.root / "bin/lima").exists())
        self.assertEqual(self.run_helper("install").returncode, 0)
        self.assertEqual((self.root / "bin/limactl").lstat().st_mtime_ns, before)

    def test_broken_link_repaired_but_unrelated_file_preserved(self):
        launcher = self.root / "bin/lima"
        launcher.symlink_to(self.root / "gone")
        self.assertEqual(self.run_helper("install").returncode, 0)
        launcher.unlink()
        launcher.write_text("user-owned")
        self.assertNotEqual(self.run_helper("install").returncode, 0)
        self.assertEqual(launcher.read_text(), "user-owned")

    def test_missing_bundle_fails_without_creating_broken_link(self):
        (self.bundle / "lima").unlink()
        self.assertNotEqual(self.run_helper("install").returncode, 0)
        self.assertFalse((self.root / "bin/lima").is_symlink())

    def test_setup_invokes_helper_for_install_and_existing_runtime_repair(self):
        source = (ROOT / "scripts/setup_environment.sh").read_text()
        self.assertIn('ensure_macos_lima_launchers "$TOOLS_DIR" "$LIMA_VERSION" install || return 1', source)
        self.assertIn('ensure_macos_lima_launchers "$TOOLS_DIR" "$LIMA_VERSION" "$MODE"', source)


if __name__ == "__main__":
    unittest.main()
