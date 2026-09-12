"""Workspace probes never share the app or write during readiness polls."""
import os
from pathlib import Path
import tempfile
import unittest
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from local_service import container_workspace as workspace


class ContainerWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name).resolve() / "user-data/container-work"

    def test_default_and_service_state_override(self):
        with patch.dict(os.environ, {"IEI_WORKBENCH_STATE_DIR": str(self.directory.parent)}, clear=True):
            self.assertEqual(workspace.workspace_directory(), self.directory)
            self.assertEqual(workspace.workspace_directory(self.directory / "other"), self.directory / "other/container-work")
        with patch.dict(os.environ, {"IEI_CONTAINER_WORK_DIR": str(self.directory)}, clear=True):
            self.assertEqual(workspace.workspace_directory("/different/state"), self.directory)

    def test_prepare_checks_writes_then_publishes_readiness(self):
        seen = []
        def verify(paths, runtime, image, **kwargs):
            self.assertEqual((runtime, image), ("docker", "test-image"))
            self.assertEqual(len(paths), 1)
            seen.append(paths[0])
            if paths[0].write:
                self.assertFalse((self.directory / workspace.READY).exists())
        with patch.object(workspace, "check_access", side_effect=verify):
            workspace.prepare(self.directory, "docker", "test-image")
        self.assertEqual([item.write for item in seen], [True, False])
        self.assertEqual(seen[0].path, self.directory)
        self.assertEqual(seen[1].path, self.directory / workspace.READY)
        self.assertEqual((self.directory / workspace.READY).read_bytes(), workspace.READY_BYTES)

    def test_failed_write_invalidates_old_readiness(self):
        self.directory.mkdir(parents=True)
        (self.directory / workspace.READY).write_bytes(workspace.READY_BYTES)
        with patch.object(workspace, "check_access", side_effect=workspace.AccessError("read-only data drive")):
            with self.assertRaisesRegex(workspace.AccessError, "read-only"):
                workspace.prepare(self.directory, "docker", "test-image")
        self.assertFalse((self.directory / workspace.READY).exists())

    def test_missing_marker_does_not_create_or_run_anything(self):
        with patch.object(workspace, "check_access") as check:
            with self.assertRaisesRegex(workspace.AccessError, "Retry preparation"):
                workspace.check_ready(self.directory, "docker", "test-image")
        check.assert_not_called()
        self.assertFalse(self.directory.exists())

    def test_readiness_is_read_only_and_errors_are_preserved(self):
        self.directory.mkdir(parents=True)
        marker = self.directory / workspace.READY
        marker.write_bytes(workspace.READY_BYTES)
        before = (marker.read_bytes(), marker.stat().st_mtime_ns, self.directory.stat().st_mtime_ns)
        with patch.object(workspace, "check_access", side_effect=workspace.AccessError("Docker engine unavailable")) as check:
            with self.assertRaisesRegex(workspace.AccessError, "engine unavailable"):
                workspace.check_ready(self.directory, "docker", "test-image")
        self.assertFalse(check.call_args.args[0][0].write)
        self.assertEqual(before, (marker.read_bytes(), marker.stat().st_mtime_ns, self.directory.stat().st_mtime_ns))

    def test_repair_targets_only_workspace_and_requests_write_access(self):
        with patch.object(workspace, "check_access", side_effect=[workspace.AccessError("not shared"), None, None]), \
                patch("local_service.colima_sharing.repair") as repair:
            workspace.prepare(self.directory, "docker", "test-image", repair_sharing=True)
        self.assertEqual(repair.call_args.args, ("docker", "test-image", self.directory))
        self.assertTrue(repair.call_args.kwargs["writable"])

    def test_engine_failure_never_attempts_sharing_repair(self):
        with patch.object(workspace, "check_access", side_effect=workspace.RuntimeAccessError("engine unavailable")), \
                patch("local_service.colima_sharing.repair") as repair:
            with self.assertRaisesRegex(workspace.RuntimeAccessError, "engine unavailable"):
                workspace.prepare(self.directory, "docker", "test-image", repair_sharing=True)
        repair.assert_not_called()


if __name__ == "__main__":
    unittest.main()
