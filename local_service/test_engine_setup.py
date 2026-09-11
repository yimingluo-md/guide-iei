"""Engine setup uses disposable fixtures, never installs workstation tools."""
import json
import os
import subprocess
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from local_service import test_workbench_service as fixtures
from local_service.workbench_service import create_server


class EngineSetupTests(unittest.TestCase):
    setUp = fixtures.AnnotationJobServiceTests.setUp
    tearDown = fixtures.AnnotationJobServiceTests.tearDown
    _write_script = fixtures.AnnotationJobServiceTests._write_script

    def wait_setup(self, job):
        for _ in range(200):
            current = next(item for item in self.service.resource_downloads() if item["id"] == job["id"])
            if current["status"] not in {"queued", "running"} and not self.service._engine_setup_reserved:
                return current
            time.sleep(.02)
        self.fail("Test setup did not finish")

    def test_consent_and_platform(self):
        with self.assertRaisesRegex(ValueError, "Confirm"):
            self.service.start_engine_setup({})
        with patch("local_service.workbench_service.platform.system", return_value="Linux"):
            with self.assertRaisesRegex(ValueError, "Windows"):
                self.service.start_engine_setup({"confirm": True})

    def test_ready_image_without_app_mount_is_not_ready_on_reopen(self):
        import subprocess
        with patch.object(self.service, "_container_image_status", return_value={"available": True}), \
                patch("local_service.workbench_service.subprocess.run", return_value=subprocess.CompletedProcess([], 1)) as run:
            result = self.service.annotation_engine_status({})
        self.assertFalse(result["available"])
        self.assertEqual(result["state"], "sharing_required")
        self.assertIn(str(self.service.pipeline_root), result["message"])
        self.assertIn("repair Colima sharing automatically", result["message"])
        self.assertIn("--pull=never", run.call_args.args[0])

    def test_mountable_image_is_ready_and_busy_setup_does_not_probe(self):
        import subprocess
        with patch.object(self.service, "_container_image_status", return_value={"available": True}), \
                patch("local_service.workbench_service.subprocess.run", return_value=subprocess.CompletedProcess([], 0)) as run:
            self.assertTrue(self.service.annotation_engine_status({})["available"])
            run.reset_mock()
            with patch.object(self.service, "_engine_setup_reserved", True):
                self.assertTrue(self.service.annotation_engine_status({})["busy"])
            run.assert_not_called()

    def test_conflicts_block_setup(self):
        for name, value in (("_active_storage_mutations", 1), ("_migration_reserved", True)):
            with patch.object(self.service, name, value), patch("local_service.workbench_service.platform.system", return_value="Darwin"):
                with self.assertRaises(ValueError):
                    self.service.start_engine_setup({"confirm": True})
        with patch.object(self.service.store, "active_count", return_value=601), patch("local_service.workbench_service.platform.system", return_value="Darwin"):
            with self.assertRaisesRegex(ValueError, "annotations"):
                self.service.start_engine_setup({"confirm": True})

    def test_setup_has_logs_validation_and_releases_reservation(self):
        self._write_script("setup_environment.sh", '#!/bin/bash\nset -eu\ntest "$1" = --install\ntest "$2" = --yes\ntest "$3" = --engine-only\ntest "$4" = --config\ntest -s "$5"\necho "=== Preparing test annotation engine ==="\nsleep 0.4\n')
        with patch("local_service.workbench_service.platform.system", return_value="Darwin"), patch.object(self.service, "_container_image_status", return_value={"available": True}):
            job = self.service.start_engine_setup({"confirm": True})
            self.assertEqual(job["resource_id"], "annotation_engine")
            self.assertNotIn("_command", job)
            with self.assertRaisesRegex(ValueError, "setup is running"):
                self.service.begin_storage_mutation()
            with self.assertRaisesRegex(ValueError, "annotation-engine setup"):
                self.service.request_service_quit({"confirm": True, "instance_id": self.service._instance_id})
            with self.assertRaises(ValueError):
                self.service.start_engine_setup({"confirm": True})
            result = self.wait_setup(job)
        self.assertEqual(result["status"], "succeeded", result)
        self.assertIn("Preparing test annotation engine", result["log"])
        self.assertFalse(list((self.state / "resource-configs").glob("annotation_engine.*.yaml")))
        self.service.begin_storage_mutation()
        self.service.end_storage_mutation()

    def test_successful_retry_adopts_recovered_host_before_validation_and_later_jobs(self):
        self._write_script("setup_environment.sh", "#!/bin/sh\nexit 0\n")
        recovered_host = "unix:///synthetic-recovered-colima/docker.sock"
        def validate(config):
            self.assertEqual(os.environ.get("DOCKER_HOST"), recovered_host)
            return {"available": True}
        with patch.dict(os.environ), \
                patch("local_service.workbench_service.platform.system", return_value="Darwin"), \
                patch("local_service.workbench_service.managed_colima_environment", side_effect=lambda env: dict(env, DOCKER_HOST=recovered_host)) as recover, \
                patch.object(self.service, "_container_image_status", side_effect=validate):
            result = self.wait_setup(self.service.start_engine_setup({"confirm": True}))
            self.assertEqual(result["status"], "succeeded", result)
            recover.assert_called_once()
            inherited = subprocess.check_output([sys.executable, "-c", "import os; print(os.environ.get('DOCKER_HOST', ''))"], text=True).strip()
            self.assertEqual(inherited, recovered_host)

    def test_failed_setup_can_be_retried_and_false_success_is_rejected(self):
        self._write_script("setup_environment.sh", "#!/bin/sh\necho test-build-failed\nexit 17\n")
        with patch("local_service.workbench_service.platform.system", return_value="Darwin"):
            first = self.wait_setup(self.service.start_engine_setup({"confirm": True}))
            self.assertEqual(first["status"], "failed")
            self.assertEqual(first["exit_code"], 17)
            self._write_script("setup_environment.sh", "#!/bin/sh\nexit 0\n")
            with patch.object(self.service, "_container_image_status", return_value={"available": False, "message": "engine still missing"}):
                second = self.wait_setup(self.service.start_engine_setup({"confirm": True}))
            self.assertEqual(second["status"], "failed")
            self.assertIn("engine still missing", second["error"])
            self.assertNotEqual(first["id"], second["id"])

    def test_sharing_repair_guidance_reaches_startup_window(self):
        message = "Other containers are running. Finish those workloads, then choose Retry preparation."
        self._write_script("setup_environment.sh", '#!/bin/sh\necho "=== Repairing container file sharing ==="\necho "GUIDE_IEI_SETUP_ERROR: ' + message + '"\necho "summary: one issue"\nexit 1\n')
        with patch("local_service.workbench_service.platform.system", return_value="Darwin"):
            result = self.wait_setup(self.service.start_engine_setup({"confirm": True}))
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["message"], message)
        self.assertEqual(result["error"], message)

    def test_http_route_requires_consent_and_rejects_cross_site(self):
        server = create_server(self.service, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{server.server_port}/api/annotation-engine/setup"
        try:
            for origin, body, status in (("https://example.com", {"confirm": True}, 403), ("http://127.0.0.1", {}, 400)):
                request = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json", "Origin": origin})
                with self.assertRaises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(request, timeout=5)
                self.assertEqual(error.exception.code, status)
            self.assertFalse(self.service._engine_setup_reserved)
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=5)

    def test_cancel_stops_only_setup_and_releases_reservation_before_review(self):
        self._write_script("setup_environment.sh", '#!/bin/sh\necho "=== Downloading container tools ==="\necho "raw noisy compiler output"\nexec sleep 60\n')
        with patch("local_service.workbench_service.platform.system", return_value="Darwin"):
            job = self.service.start_engine_setup({"confirm": True})
            for _ in range(100):
                current = self.service.resource_downloads()[0]
                if "raw noisy" in current["log"]:
                    break
                time.sleep(.02)
            self.assertEqual(current["message"], "Downloading container tools…")
            with self.assertRaises(ValueError):
                self.service.cancel_engine_setup({"job_id": job["id"]})
            self.service.cancel_engine_setup({"job_id": job["id"], "confirm": True})
            result = self.wait_setup(job)
        self.assertEqual(result["status"], "interrupted", result)
        self.assertNotIn(job["id"], self.service._resource_processes)
        self.assertNotIn("_cancel_requested", result)
        self.service.begin_storage_mutation()
        self.service.end_storage_mutation()

    def test_native_coordinator_over_http_waits_for_real_worker_cleanup(self):
        from local_service.desktop_setup import prepare_engine
        self._write_script("setup_environment.sh", '#!/bin/sh\necho "=== Preparing test engine ==="\nexec sleep 60\n')
        server = create_server(self.service, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        status_path, control_path = self.root / "startup.json", self.root / "control.json"
        ticks = []
        def tick(_seconds):
            ticks.append(1)
            self.assertLess(len(ticks), 100)
            if len(ticks) == 1:
                control_path.write_text(json.dumps({"action": "quit"}))
            time.sleep(.02)
        try:
            with patch("local_service.workbench_service.platform.system", return_value="Darwin"), patch.object(self.service, "_container_image_status", return_value={"available": False}):
                self.assertFalse(prepare_engine(f"http://127.0.0.1:{server.server_port}", self.root,
                    status_path, control_path, lambda: True, pause=tick))
            self.assertFalse(self.service._engine_setup_reserved)
            self.assertFalse(self.service._resource_processes)
            self.assertEqual(json.loads(status_path.read_text())["phase"], "stopped")
            self.service.begin_storage_mutation()
            self.service.end_storage_mutation()
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
