#!/usr/bin/env python3
"""Exercise the real probe protocol without requiring Docker or patient data."""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pipeline.container_access import AccessError, AccessPath, check_access, collect_paths


class ContainerAccessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "input with spaces" / "synthetic.vcf"
        self.source.parent.mkdir()
        self.source.write_text("synthetic file content\n")
        self.output = self.root / "output with spaces"
        self.paths = [AccessPath(self.source, "input VCF"), AccessPath(self.output, "output and temporary files", True)]
        self.commands = []

    def run_probe_locally(self, command, **kwargs):
        self.commands.append(command)
        mounts = {}
        for index, arg in enumerate(command):
            if arg in ("-v", "--bind"):
                host, target, mode = command[index + 1].rsplit(":", 2)
                mounts[target] = host
        start = command.index("-e")
        args = command[start + 2:]
        for index in range(2, len(args), 4):
            for target, host in mounts.items():
                if args[index].startswith(target + "/"):
                    args[index] = host + args[index][len(target):]
                    break
        return subprocess.run(["perl", "-e", command[start + 1], *args], **kwargs)

    def test_real_protocol_reads_and_roundtrips_writes_for_all_runtimes(self):
        for runtime in ("docker", "podman", "singularity", "apptainer"):
            check_access(self.paths, runtime, "test-image", run=self.run_probe_locally)
            self.assertEqual(list(self.output.iterdir()), [])
        self.assertEqual(self.source.read_text(), "synthetic file content\n")
        for command in self.commands[:2]:
            self.assertIn("--network=none", command)
            self.assertIn("core=0:0", command)
            self.assertIn("--pull=never", command)

    def test_empty_vm_shadow_and_runtime_errors_fail_with_host_folder_guidance(self):
        def failed(command, **kwargs):
            return subprocess.CompletedProcess(command, 2, "0:FAIL\n", "mount inaccessible")
        with self.assertRaisesRegex(AccessError, "Container cannot read input VCF") as caught:
            check_access(self.paths, "docker", "test-image", run=failed)
        self.assertIn(str(self.source.parent), str(caught.exception))
        self.assertIn("Colima", str(caught.exception))
        self.assertIn("File sharing", str(caught.exception))
        self.assertIn("mount inaccessible", str(caught.exception))
        self.assertEqual(list(self.output.iterdir()), [])

    def test_not_just_success_exit_host_must_see_container_write(self):
        def fake_success(command, **kwargs):
            return subprocess.CompletedProcess(command, 0, "0:OK\n1:OK\n", "")
        with self.assertRaisesRegex(AccessError, "writes are not visible"):
            check_access(self.paths, "docker", "test-image", run=fake_success)
        self.assertEqual(list(self.output.iterdir()), [])

    def test_host_missing_and_unwritable_are_distinct_from_vm_sharing(self):
        with self.assertRaisesRegex(AccessError, "Host cannot read"):
            check_access([AccessPath(self.root / "missing", "input VCF")], "docker", "test-image")
        with patch("pipeline.container_access.tempfile.mkstemp", side_effect=PermissionError("denied")):
            with self.assertRaisesRegex(AccessError, "Host cannot write"):
                check_access([self.paths[1]], "docker", "test-image")

    def test_timeout_preserves_cleanup(self):
        def timeout(command, **kwargs):
            raise subprocess.TimeoutExpired(command, 60)
        with self.assertRaisesRegex(AccessError, "Could not run"):
            check_access(self.paths, "docker", "test-image", run=timeout)
        self.assertEqual(list(self.output.iterdir()), [])

    def test_engine_failure_is_not_misreported_as_file_sharing(self):
        def failed(command, **kwargs):
            return subprocess.CompletedProcess(command, 125, "", "Cannot connect to the Docker daemon")
        with self.assertRaisesRegex(AccessError, "not evidence of a file-sharing problem"):
            check_access(self.paths, "docker", "test-image", run=failed)
        self.assertEqual(list(self.output.iterdir()), [])

    def test_mount_startup_failure_does_not_blame_first_input(self):
        def failed(command, **kwargs):
            return subprocess.CompletedProcess(command, 125, "", "Mounts denied: /external-reference")
        with self.assertRaisesRegex(AccessError, "could not start the file-access probe") as caught:
            check_access(self.paths, "docker", "test-image", run=failed)
        self.assertIn("/external-reference", str(caught.exception))
        self.assertNotIn("Container cannot read input VCF", str(caught.exception))

    def test_readonly_cache_uses_existing_nested_file_not_writable_marker(self):
        cache = self.root / "cache/homo_sapiens/113_GRCh38"
        cache.mkdir(parents=True)
        (cache / "info.txt").write_text("fixture metadata\n")
        before = set(self.root.rglob("*"))
        check_access([AccessPath(self.root / "cache", "VEP cache")], "docker", "test-image", run=self.run_probe_locally)
        self.assertEqual(before, set(self.root.rglob("*")))
        self.assertTrue(any(arg.endswith(":ro") for arg in self.commands[0]))

    def test_collection_uses_enabled_predictors_and_resolves_symlinks(self):
        cache = self.root / "cache"
        cache.mkdir()
        fasta = self.root / "reference.fa.gz"
        fasta.write_bytes(b"fasta")
        Path(str(fasta) + ".fai").write_text("index")
        linked = self.root / "linked.fa.gz"
        linked.symlink_to(fasta)
        cfg = {"reference": {"vep_cache_dir": str(cache), "fasta": {"enabled": True, "path": str(linked)}},
               "plugins": {"dbNSFP": {"enabled": False, "path": "disabled-missing.gz"}},
               "region": {"coding_only": False}}
        paths = collect_paths(cfg, self.source, self.output / "result.vcf.gz", ROOT)
        resolved = {item.path for item in paths}
        self.assertIn(fasta.resolve(), resolved)
        self.assertIn(Path(str(fasta) + ".fai").resolve(), resolved)
        self.assertFalse(any("disabled-missing" in str(path) for path in resolved))

    def test_all_variants_does_not_probe_unused_custom_bed(self):
        cfg = {"region": {"coding_only": True, "custom_bed": "absent.bed"}}
        paths = collect_paths(cfg, self.source, self.output / "out.vcf.gz", self.root, all_variants=True)
        self.assertFalse(any("region" in item.label for item in paths))


if __name__ == "__main__":
    unittest.main()
