"""First-launch state-machine tests: no downloads or real workstation tools."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from local_service.desktop_setup import prepare_engine


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

    def run_setup(self, callback, tick=lambda: None):
        def call(base, route, body=None):
            self.calls.append(route)
            return callback(route, body)
        def pause(_seconds):
            self.ticks += 1
            self.assertLess(self.ticks, 30, self.status.read_text())
            tick()
        return prepare_engine("http://127.0.0.1:1", self.root, self.status,
                              self.control, lambda: True, call, pause)

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
