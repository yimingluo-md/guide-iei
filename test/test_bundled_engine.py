"""Bundle integrity, architecture, and transactional tag publication."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import bundled_engine as engine

ROOT = Path(__file__).resolve().parents[1]


class BundledEngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.archive = self.directory / "vep-engine.tar.gz"
        self.archive.write_bytes(b"synthetic compressed image fixture")
        self.manifest = {"schema_version": 1, "platform": "linux/arm64", "source_fingerprint": "test",
            "image_id": "sha256:" + "a" * 64, "archive": self.archive.name,
            "archive_bytes": self.archive.stat().st_size,
            "sha256": hashlib.sha256(self.archive.read_bytes()).hexdigest()}
        self.save()

    def save(self):
        (self.directory / "manifest.json").write_text(json.dumps(self.manifest))

    def test_valid_bundle_and_wrong_architecture(self):
        self.assertEqual(engine.validate_bundle(self.directory, "test", "arm64"), self.manifest)
        for expected, arch in (("old", "arm64"), ("test", "amd64")):
            with self.assertRaises(ValueError):
                engine.validate_bundle(self.directory, expected, arch)

    def test_tampering_same_size_is_rejected(self):
        self.archive.write_bytes(b"x" * self.archive.stat().st_size)
        with self.assertRaisesRegex(ValueError, "integrity"):
            engine.validate_bundle(self.directory, "test", "arm64")

    def test_paths_and_malformed_manifest_are_rejected(self):
        self.manifest["archive"] = "../outside.tar.gz"
        self.save()
        with self.assertRaises(ValueError):
            engine.validate_bundle(self.directory, "test", "arm64")
        (self.directory / "manifest.json").write_bytes(b"\xff")
        with self.assertRaises(ValueError):
            engine.validate_bundle(self.directory, "test", "arm64")

    def test_export_rejects_wrong_image_label_or_platform(self):
        info = {"Os": "linux", "Architecture": "arm64", "Id": self.manifest["image_id"],
                "Config": {"Labels": {engine.LABEL: "test"}}}
        engine.verify_image(info, "test", "arm64")
        for expected, arch in (("wrong", "arm64"), ("test", "amd64")):
            with self.assertRaises(ValueError):
                engine.verify_image(info, expected, arch)

    def test_load_tags_only_after_smoke_and_never_pulls_or_builds(self):
        events = []
        with patch.object(engine, "fingerprint", return_value="test"), \
                patch.object(engine.subprocess, "check_output", return_value="aarch64\n"), \
                patch.object(engine.subprocess, "run", side_effect=lambda args, **kw: events.append(args)), \
                patch.object(engine, "inspect", return_value={}), \
                patch.object(engine, "verify_image"), \
                patch.object(engine, "smoke", side_effect=lambda *_: events.append(["smoke"])):
            engine.load_bundle(ROOT, self.directory, "docker", "vep-annotate:latest")
        self.assertEqual(events[0][:3], ["docker", "image", "load"])
        self.assertEqual(events[1], ["smoke"])
        self.assertEqual(events[2], ["docker", "image", "tag", self.manifest["image_id"], "vep-annotate:latest"])

    def test_load_failure_does_not_change_existing_tag(self):
        with patch.object(engine, "fingerprint", return_value="test"), \
                patch.object(engine.subprocess, "check_output", return_value="arm64\n"), \
                patch.object(engine.subprocess, "run", side_effect=subprocess.CalledProcessError(1, "load")) as run:
            with self.assertRaises(subprocess.CalledProcessError):
                engine.load_bundle(ROOT, self.directory, "docker", "vep-annotate:latest")
            self.assertEqual(run.call_count, 1)

    def test_new_colima_mounts_user_workspace_not_app_and_retains_defaults(self):
        result = subprocess.run(["bash", "-uc", 'source "$1"; fake_colima() { printf "%s\\n" "$@"; }; start_managed_colima fake_colima "$2" 7 8',
            "test", str(ROOT / "scripts/macos_container_dependencies.sh"), os.environ["HOME"] + "/.iei-variant-review/container-work"],
            capture_output=True, text=True, check=True)
        arguments = result.stdout.splitlines()
        self.assertFalse(any("/Applications" in argument for argument in arguments))
        self.assertEqual(arguments.count("--mount"), 2)
        self.assertIn(os.environ["HOME"] + ":w", arguments)
        self.assertIn("/tmp/colima:w", arguments)
        self.assertNotIn("/:w", arguments)


if __name__ == "__main__":
    unittest.main()
