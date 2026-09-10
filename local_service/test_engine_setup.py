"""Engine setup uses disposable fixtures, never installs workstation tools."""
import json
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


if __name__ == "__main__":
    unittest.main()
