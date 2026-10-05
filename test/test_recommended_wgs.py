"""Exercise the WGS batch without network transfers or real dataset writes."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


class RecommendedWgsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.script = self.root / "install_recommended_wgs.sh"
        shutil.copyfile(Path(__file__).resolve().parents[1] / "scripts" / self.script.name, self.script)
        self.log = self.root / "calls"
        for name in ("download_references.sh", "download_screen_context_bundle.sh", "download_avi.sh"):
            (self.root / name).write_text(
                '#!/bin/bash\nprintf "%s\\n" "$(basename "$0") $*" >> "$CALL_LOG"\n'
                '[[ "$(basename "$0")" != "${FAIL_SCRIPT:-}" ]]\n'
            )

    def run_batch(self, only=None, fail=""):
        args = ["bash", str(self.script), "/data with spaces/config.yaml", "/external drive/screen-context"]
        if only is not None:
            args.append(only)
        return subprocess.run(args, env={**os.environ, "CALL_LOG": str(self.log), "FAIL_SCRIPT": fail},
                              capture_output=True, text=True)

    def test_default_contains_only_full_spliceai_screen_and_avi(self):
        result = self.run_batch()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.log.read_text().splitlines(), [
            "download_references.sh /data with spaces/config.yaml --only spliceai --skip-final-status",
            "download_screen_context_bundle.sh /external drive/screen-context",
            "download_avi.sh /data with spaces/config.yaml",
        ])

    def test_retry_only_missing_and_installed_noop(self):
        result = self.run_batch("alphagenome_avi")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.log.read_text().splitlines()), 1)
        self.assertEqual(self.run_batch("").returncode, 0)
        self.assertEqual(len(self.log.read_text().splitlines()), 1)

    def test_failure_stops_batch_and_invalid_list_starts_nothing(self):
        self.assertNotEqual(self.run_batch("spliceai,dbnsfp").returncode, 0)
        self.assertFalse(self.log.exists())
        self.assertNotEqual(self.run_batch(fail="download_screen_context_bundle.sh").returncode, 0)
        self.assertEqual(len(self.log.read_text().splitlines()), 2)


if __name__ == "__main__":
    unittest.main()
