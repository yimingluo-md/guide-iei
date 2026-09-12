"""Existing-profile repair tests use only synthetic YAML and fake processes."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from local_service import colima_sharing as sharing


class ColimaSharingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name).resolve()
        self.config_path = self.home / ".colima/custom/colima.yaml"
        self.config_path.parent.mkdir(parents=True)
        self.config = {"cpu": 7, "memory": 8, "disk": 120, "vmType": "vz",
            "mountType": "virtiofs", "docker": {"registry-mirrors": ["https://example.invalid"]},
            "mounts": [{"location": "/Volumes/reference", "writable": True},
                       {"location": str(self.home), "writable": True}],
            "kubernetes": {"enabled": False}}
        self.config_path.write_text("# user's settings\n" + yaml.safe_dump(self.config))
        self.original = self.config_path.read_bytes()
        self.root = Path("/Applications/GUIDE-IEI.app/Contents/Resources/application")
        self.commands = []
        self.command = ["/tools/colima", "start", "--profile", "custom", "--activate=false", "--save-config=false"]
        self.env = {"PATH": "/tools:/usr/bin"}
        home = patch.object(sharing.Path, "home", return_value=self.home)
        home.start(); self.addCleanup(home.stop)

    def run_command(self, command, **kwargs):
        self.commands.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="")

    def run_repair(self, run=None, probes=None, selected=None):
        with patch.object(sharing, "selected_start_command", return_value=selected or ("Colima", self.command)), \
                patch.object(sharing, "probe", side_effect=probes or [False, True]), \
                patch.object(sharing.subprocess, "run", side_effect=run or self.run_command), \
                patch.object(sharing.time, "sleep"):
            return sharing.repair("docker", "image", self.root, self.env)

    def test_repairs_existing_vm_preserving_all_settings_and_exact_backup(self):
        self.assertTrue(self.run_repair())
        updated = yaml.safe_load(self.config_path.read_bytes())
        self.assertEqual(updated.pop("mounts"), self.config["mounts"] + [{"location": "/Applications", "writable": False}])
        self.assertEqual(updated, {k: v for k, v in self.config.items() if k != "mounts"})
        backup, = self.config_path.parent.glob("colima.yaml.guide-iei-backup-*")
        self.assertEqual(backup.read_bytes(), self.original)
        self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.commands[-2:], [["/tools/colima", "stop", "--profile", "custom"], self.command])

    def test_ready_mount_never_edits_or_restarts(self):
        self.assertFalse(self.run_repair(probes=[True]))
        self.assertFalse(self.commands)
        self.assertEqual(self.config_path.read_bytes(), self.original)

    def test_running_containers_are_not_interrupted(self):
        def busy(command, **kwargs):
            self.commands.append(command)
            return subprocess.CompletedProcess(command, 0, stdout="running-container\n")
        with self.assertRaisesRegex(sharing.SharingError, "Other containers are running"):
            self.run_repair(run=busy)
        self.assertEqual(self.commands, [["docker", "ps", "--quiet"]])
        self.assertEqual(self.config_path.read_bytes(), self.original)
        self.assertFalse(list(self.config_path.parent.glob("*backup*")))

    def test_new_workload_before_stop_is_not_interrupted(self):
        def busy_second(command, **kwargs):
            self.commands.append(command)
            return subprocess.CompletedProcess(command, 0, stdout="" if len(self.commands) == 1 else "new-container")
        with self.assertRaisesRegex(sharing.SharingError, "Other containers"):
            self.run_repair(run=busy_second)
        self.assertTrue(all(command[:2] == ["docker", "ps"] for command in self.commands))
        self.assertEqual(self.config_path.read_bytes(), self.original)

    def test_failed_restart_restores_previous_settings_and_keeps_backup(self):
        def failed(command, **kwargs):
            self.run_command(command, **kwargs)
            if command == self.command:
                raise subprocess.CalledProcessError(1, command)
            return subprocess.CompletedProcess(command, 0, stdout="")
        with self.assertRaisesRegex(sharing.SharingError, "Previous sharing settings were restored"):
            self.run_repair(run=failed)
        self.assertEqual(self.config_path.read_bytes(), self.original)
        self.assertTrue(list(self.config_path.parent.glob("*backup*")))

    def test_existing_sharing_can_be_refreshed_without_rewriting_yaml(self):
        self.config["mounts"].append({"location": "/Applications", "writable": False})
        self.config_path.write_text(yaml.safe_dump(self.config))
        before = self.config_path.read_bytes()
        self.assertTrue(self.run_repair())
        self.assertEqual(self.config_path.read_bytes(), before)

    def test_defaults_preserved_when_mounts_omitted_and_app_has_spaces(self):
        planned = sharing.mount_plan({"cpu": 4}, Path("/Applications/GUIDE IEI.app/Contents/Resources/application"), self.home)
        self.assertEqual(planned["mounts"], [{"location": str(self.home), "writable": True},
            {"location": "/tmp/colima", "writable": True}, {"location": "/Applications", "writable": False}])

    def test_different_guest_mountpoint_does_not_falsely_cover_app(self):
        planned = sharing.mount_plan({"mounts": [{"location": "/Applications", "mountPoint": "/other"}]}, self.root, self.home)
        self.assertEqual(len(planned["mounts"]), 2)

    def test_kubernetes_invalid_yaml_and_symlinks_do_not_restart(self):
        for content in ("kubernetes:\n  enabled: true\n", "[", "[]"):
            self.config_path.write_text(content)
            with self.assertRaises((sharing.SharingError, yaml.YAMLError)):
                self.run_repair()
            self.assertFalse(self.commands)
        self.config_path.unlink()
        self.config_path.symlink_to(self.home / "elsewhere")
        with self.assertRaises(sharing.SharingError):
            self.run_repair()
        self.assertFalse(self.commands)

    def test_docker_desktop_offers_ui_guidance_without_editing(self):
        with self.assertRaisesRegex(sharing.SharingError, "Open Docker Desktop"):
            self.run_repair(selected=("Docker Desktop", ["open", "Docker.app"]))
        self.assertFalse(self.commands)

    def test_cannot_claim_success_when_mount_stays_unreadable(self):
        with self.assertRaisesRegex(sharing.SharingError, "access to .* still failed"):
            self.run_repair(probes=[False] * 7)

    def test_workspace_under_home_needs_no_app_mount(self):
        planned = sharing.mount_plan(self.config, self.home / ".iei-variant-review/container-work", self.home, writable=True)
        self.assertEqual(planned, self.config)

    def test_external_workspace_gets_only_its_own_writable_mount(self):
        root = Path("/Volumes/Data/guide-work")
        planned = sharing.mount_plan(self.config, root, self.home, writable=True)
        self.assertEqual(planned["mounts"], self.config["mounts"] + [{"location": str(root), "writable": True}])

    def test_workspace_repair_does_not_broaden_parent_permissions(self):
        config = {"mounts": [{"location": str(self.home), "writable": False}]}
        with self.assertRaisesRegex(sharing.SharingError, "no broader permissions"):
            sharing.mount_plan(config, self.home / "data", self.home, writable=True)
        self.assertFalse(config["mounts"][0]["writable"])
        planned = sharing.mount_plan(config, self.home, self.home, writable=True)
        self.assertTrue(planned["mounts"][0]["writable"])

    def test_selected_remote_runtime_is_never_changed(self):
        with patch.object(sharing, "probe", return_value=False), \
                patch.object(sharing, "selected_start_command", return_value=None):
            with self.assertRaisesRegex(sharing.SharingError, "selected container runtime"):
                sharing.repair("docker", "image", self.root, self.env)
        self.assertEqual(self.config_path.read_bytes(), self.original)


if __name__ == "__main__":
    unittest.main()
