"""First-launch state-machine tests: no downloads or real workstation tools."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from local_service.desktop_setup import prepare_engine, take_action, take_control_message


class DesktopSetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.status = self.root / "status.json"
        self.control = self.root / "control.json"
        self.calls = []
        self.ticks = 0

    def action(self, value):
        self.control.write_text(json.dumps({"action": value}))

    def run_setup(self, callback, tick=lambda: None, *, essential=False):
        def call(base, route, body=None):
            self.calls.append(route)
            return callback(route, body)
        def pause(_seconds):
            self.ticks += 1
            self.assertLess(self.ticks, 30, self.status.read_text())
            tick()
        return prepare_engine("http://127.0.0.1:1", self.root, self.status,
                              self.control, lambda: True, call, pause, essential=essential)

    def test_essential_setup_waits_for_one_explicit_click(self):
        state = {"ready": False, "started": False}
        def call(route, body):
            if route.endswith("/status"):
                if self.ticks in (1, 2):
                    self.assertTrue(json.loads(self.status.read_text())["can_prepare"])
                return {"available": state["ready"], "busy": False,
                        "package": {"installable": True}, "annotation_path": "/default/data",
                        "free_bytes": 1000, "estimated_required_bytes": 500}
            if route.endswith("/setup"):
                self.assertGreaterEqual(self.ticks, 3)
                self.assertEqual(route, "/api/essential-setup/setup")
                state["started"] = True
                return {"id": "essentials"}
            state["ready"] = True
            return {"jobs": [{"id": "essentials", "status": "succeeded"}]}
        def tick():
            if self.ticks < 3:
                self.assertFalse(state["started"])
                self.assertEqual(json.loads(self.status.read_text())["annotation_path"], "/default/data")
            if self.ticks == 3:
                self.action("prepare")
        self.assertTrue(self.run_setup(call, tick, essential=True))
        self.assertEqual(self.calls.count("/api/essential-setup/setup"), 1)

    def test_quit_arriving_while_prepare_is_read_remains_pending(self):
        self.action("prepare")
        read = Path.read_text
        def read_then_quit(path):
            value = read(path)
            self.action("quit")
            return value
        with patch.object(Path, "read_text", read_then_quit):
            self.assertEqual(take_action(self.control), "prepare")
        self.assertEqual(take_action(self.control), "quit")

    def test_control_files_recover_from_rename_read_and_unlink_errors(self):
        claimed = self.control.with_suffix(".reading")
        self.action("prepare")
        with patch.object(Path, "replace", side_effect=PermissionError("synthetic secret")):
            self.assertEqual(take_control_message(self.control), {})
        self.assertTrue(self.control.exists())

        read = Path.read_text
        def fail_claimed_read(path, *args, **kwargs):
            if path == claimed and path.exists():
                raise OSError("synthetic secret")
            return read(path, *args, **kwargs)
        with patch.object(Path, "read_text", fail_claimed_read):
            self.assertEqual(take_control_message(self.control), {})
        self.assertTrue(claimed.exists())
        self.action("quit")
        with patch.object(Path, "unlink", side_effect=PermissionError("synthetic secret")):
            self.assertEqual(take_control_message(self.control), {})
            self.assertEqual(take_control_message(self.control), {})
        self.assertEqual(take_action(self.control), "prepare")
        self.assertEqual(take_action(self.control), "quit")
        self.assertEqual(take_control_message(self.control), {})

    def test_claimed_quit_waits_for_unlink_recovery_without_replay(self):
        self.action("quit")
        with patch.object(Path, "unlink", side_effect=OSError("synthetic secret")):
            self.assertIsNone(take_action(self.control))
            self.action("prepare")
            self.assertIsNone(take_action(self.control))
        self.assertEqual(take_action(self.control), "quit")
        self.assertEqual(take_action(self.control), "prepare")
        self.assertIsNone(take_action(self.control))

    def test_returning_install_waits_for_runtime_even_if_prepare_is_clicked(self):
        for legacy in (True, False):
            with self.subTest(legacy=legacy):
                self.ticks = 0
                def call(route, body):
                    self.assertEqual(route, "/api/essential-setup/status")
                    ready = self.ticks >= 2
                    return {"engine": {"state": "available" if ready else "runtime_starting"}, "available": ready,
                            "legacy_ready": legacy, "package": {"installable": True, "available": not legacy},
                            "annotation_path": "/prepared/data", "free_bytes": 1000, "estimated_required_bytes": 0}
                def tick():
                    snapshot = json.loads(self.status.read_text())
                    self.assertEqual(snapshot["phase"], "starting")
                    self.assertFalse(snapshot["can_prepare"])
                    self.action("prepare")
                self.assertTrue(self.run_setup(call, tick, essential=True))

    def test_startup_failure_offers_preparation_without_automatically_installing(self):
        def call(route, body):
            self.assertEqual(route, "/api/essential-setup/status")
            return {"engine": {"state": "runtime_starting" if not self.ticks else "runtime_unavailable"},
                    "package": {"installable": True}, "annotation_path": "/data",
                    "free_bytes": 1000, "estimated_required_bytes": 500}
        def tick():
            if self.ticks == 2:
                self.assertTrue(json.loads(self.status.read_text())["can_prepare"])
                self.action("quit")
        self.assertFalse(self.run_setup(call, tick, essential=True))

    def test_unreleased_package_shows_explanation_without_installing(self):
        def call(route, body):
            self.assertEqual(route, "/api/essential-setup/status")
            return {"available": False, "package": {"installable": False}, "message": "Package is being prepared",
                    "annotation_path": "/default/data", "free_bytes": 1000, "estimated_required_bytes": 500}
        def tick():
            status = json.loads(self.status.read_text())
            self.assertFalse(status["can_prepare"])
            self.assertIn("being prepared", status["message"])
            self.action("quit")
        self.assertFalse(self.run_setup(call, tick, essential=True))

    def test_ready_existing_installation_does_not_download_new_package(self):
        self.assertTrue(self.run_setup(lambda *_: {"available": True,
            "annotation_path": "/ready/data", "free_bytes": 42,
            "estimated_required_bytes": 0}, essential=True))
        self.assertEqual(self.calls, ["/api/essential-setup/status"])
        self.assertEqual(json.loads(self.status.read_text())["annotation_path"], "/ready/data")

    def test_location_survives_progress_failure_and_retry_and_refreshes_on_ready(self):
        state = {"ready": False, "attempt": 0, "polls": 0}
        def call(route, body):
            if route.endswith("/status"):
                return {"available": state["ready"], "busy": False,
                    "package": {"installable": True}, "annotation_path": "/selected/data",
                    "free_bytes": 50 if state["ready"] else 100, "estimated_required_bytes": 10}
            if route.endswith("/setup"):
                state["attempt"] += 1
                return {"id": "essential"}
            state["polls"] += 1
            result = "running" if state["polls"] == 1 else "failed" if state["attempt"] == 1 else "succeeded"
            state["ready"] = result == "succeeded"
            return {"jobs": [{"id": "essential", "status": result, "message": "Dataset progress"}]}
        phases = set()
        def tick():
            snapshot = json.loads(self.status.read_text())
            phases.add(snapshot["phase"])
            self.assertEqual(snapshot["annotation_path"], "/selected/data")
            if snapshot["phase"] in {"waiting", "failed"}:
                self.action("prepare")
        self.assertTrue(self.run_setup(call, tick, essential=True))
        self.assertTrue({"waiting", "preparing", "failed"} <= phases)
        self.assertEqual(json.loads(self.status.read_text())["free_bytes"], 50)

    def test_ready_engine_is_reused_without_install_even_after_app_update(self):
        self.assertTrue(self.run_setup(lambda *_: {"available": True}))
        self.assertEqual(self.calls, ["/api/annotation-engine/status"])
        self.assertEqual(json.loads(self.status.read_text())["phase"], "ready")

    def test_clean_machine_prepares_then_verifies_before_opening(self):
        state = {"installed": False}
        def call(route, body):
            if route.endswith("/status"):
                return {"available": state["installed"], "busy": False}
            if route.endswith("/setup"):
                self.assertEqual(body, {"confirm": True})
                return {"id": "engine", "log_path": "/synthetic/setup.log"}
            state["installed"] = True
            return {"jobs": [{"id": "engine", "status": "succeeded"}]}
        self.assertTrue(self.run_setup(call))
        self.assertEqual(self.calls.count("/api/annotation-engine/setup"), 1)
        self.assertEqual(self.calls[-1], "/api/annotation-engine/status")

    def test_failure_waits_for_explicit_retry(self):
        state = {"attempts": 0}
        def call(route, body):
            if route.endswith("/status"):
                return {"available": state["attempts"] == 2, "busy": False}
            if route.endswith("/setup"):
                state["attempts"] += 1
                return {"id": "engine"}
            return {"jobs": [{"id": "engine", "status": "failed" if state["attempts"] == 1 else "succeeded", "error": "network unavailable"}]}
        def tick():
            if self.ticks == 4:
                self.assertEqual(state["attempts"], 1)
                self.action("retry")
        self.assertTrue(self.run_setup(call, tick))
        self.assertEqual(state["attempts"], 2)

    def test_quit_cancels_and_waits_for_cleanup(self):
        state = {"cancelled": False}
        def call(route, body):
            if route.endswith("/status"):
                return {"available": False, "busy": state["cancelled"] and self.ticks < 3}
            if route.endswith("/setup"):
                return {"id": "engine"}
            if route.endswith("/cancel"):
                state["cancelled"] = True
                return {"stopping": True}
            self.fail(route)
        self.assertFalse(self.run_setup(call, lambda: self.action("quit") if self.ticks == 1 else None))
        self.assertTrue(state["cancelled"])
        self.assertGreaterEqual(self.ticks, 3)
        self.assertFalse((self.root / "annotation-setup-deferred.json").exists())

    def test_old_deferral_and_skip_action_cannot_bypass_engine_preparation(self):
        (self.root / "annotation-setup-deferred.json").write_text('{"deferred": true}')
        self.action("skip")
        def call(route, body):
            if route.endswith("/status"):
                return {"available": self.ticks > 0}
            if route.endswith("/setup"):
                return {"id": "engine"}
            return {"jobs": [{"id": "engine", "status": "succeeded"}]}
        self.assertTrue(self.run_setup(call))
        self.assertIn("/api/annotation-engine/setup", self.calls)
        self.assertFalse((self.root / "annotation-setup-deferred.json").exists())

    def test_quit_does_not_install_or_remember_deferral(self):
        self.action("quit")
        self.assertFalse(self.run_setup(lambda *_: self.fail("Unexpected request")))
        self.assertFalse((self.root / "annotation-setup-deferred.json").exists())

    def test_timed_out_start_is_found_and_cancelled_before_quit(self):
        def call(route, body):
            if route.endswith("/status"):
                return {"available": False, "busy": False}
            if route.endswith("/setup"):
                raise OSError("response timed out after starting installation")
            if route.endswith("/cancel"):
                return {"stopping": True}
            return {"jobs": [{"id": "engine", "resource_id": "annotation_engine", "status": "running"}]}
        self.assertFalse(self.run_setup(call, lambda: self.action("quit")))
        self.assertIn("/api/annotation-engine/cancel", self.calls)


if __name__ == "__main__":
    unittest.main()
