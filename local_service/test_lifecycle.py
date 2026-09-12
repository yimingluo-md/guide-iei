"""Quit/instance recognition without the user's service or patient storage."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from local_service import desktop_app
from local_service import test_workbench_service as fixtures
from local_service.workbench_service import create_server


class LifecycleTests(unittest.TestCase):
    setUp = fixtures.AnnotationJobServiceTests.setUp
    tearDown = fixtures.AnnotationJobServiceTests.tearDown
    _write_script = fixtures.AnnotationJobServiceTests._write_script

    def payload(self):
        return {"confirm": True, "instance_id": self.service._instance_id}

    def test_confirmation_and_instance_are_required(self):
        for payload in ({}, {"confirm": "true"}, {"confirm": True, "instance_id": "old"}):
            with self.assertRaises(ValueError):
                self.service.request_service_quit(payload)
        self.assertFalse(self.service._quit_requested)

    def test_quit_reserves_storage_and_does_not_restart(self):
        self.service._restart_requested = True
        result = self.service.request_service_quit(self.payload())
        self.assertTrue(result["quitting"])
        self.assertFalse(self.service._restart_requested)
        with self.assertRaisesRegex(ValueError, "shutting down"):
            self.service.begin_storage_mutation()

    def test_noninterruptible_operations_block_quit(self):
        for attr, value in (("_active_storage_mutations", 1), ("_migration_reserved", True),
                            ("_wgs_review_jobs", {"test": {"status": "running"}})):
            with self.subTest(attr=attr), patch.object(self.service, attr, value):
                with self.assertRaisesRegex(ValueError, "finish before quitting"):
                    self.service.request_service_quit(self.payload())
                self.assertFalse(self.service._quit_requested)
        for owner, attr in ((self.service.cohort, "has_active_import"), (self.service, "_bulk_intake_active")):
            with patch.object(owner, attr, return_value=True):
                with self.assertRaisesRegex(ValueError, "import"):
                    self.service.request_service_quit(self.payload())
        marker = self.root / "setup-active"
        marker.touch()
        with patch.dict(os.environ, {"IEI_DESKTOP_SETUP_MARKER": str(marker)}):
            with self.assertRaisesRegex(ValueError, "annotation-environment"):
                self.service.request_service_quit(self.payload())

    def test_active_counts_and_only_owned_processes_stop(self):
        # Synthetic process groups, not containers or annotation on real data.
        owned = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True)
        other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True)
        try:
            self.service._resource_processes["test"] = owned
            self.service._resource_jobs["test"] = {"id": "test", "status": "running"}
            with patch.object(self.service.store, "active_count", return_value=601):
                status = self.service.lifecycle_status()
            self.assertEqual(status["annotations"], 601)
            self.assertEqual(status["downloads"], 1)
            self.assertEqual(status["blockers"], [])
            self.service.request_service_quit(self.payload())
            self.service.shutdown()
            owned.wait(timeout=5)
            self.assertIsNone(other.poll())
            self.assertEqual(self.service._resource_jobs["test"]["status"], "interrupted")
        finally:
            for process in (owned, other):
                if process.poll() is None:
                    process.terminate()
                process.wait(timeout=5)

    def test_running_annotation_is_interrupted_and_queued_job_is_preserved(self):
        self._write_script("preflight.sh", "#!/bin/sh\nexec sleep 60\n")
        first = self.service.submit({"input_path": str(self.input), "output_path": str(self.output)})
        second = self.service.submit({"input_path": str(self.input), "output_path": str(self.output.with_name("second.vcf.gz"))})
        for _ in range(100):
            if first["id"] in self.service._processes:
                break
            time.sleep(.02)
        else:
            self.fail("Synthetic annotation did not start")
        self.service.request_service_quit(self.payload())
        observed = []
        real_killpg = os.killpg

        def terminate_and_allow_worker_to_finish(pid, sig):
            observed.append(self.service.store.get(first["id"])["status"])
            real_killpg(pid, sig)
            # Force the worker to observe the signal before shutdown returns
            # from killpg: previously it could record "failed" in this gap.
            for _ in range(100):
                if first["id"] not in self.service._processes:
                    break
                time.sleep(.01)

        with patch("local_service.workbench_service.os.killpg", side_effect=terminate_and_allow_worker_to_finish):
            self.service.shutdown()
        self.assertEqual(observed, ["interrupted"])
        self.assertEqual(self.service.store.get(first["id"])["status"], "interrupted")
        self.assertEqual(self.service.store.get(second["id"])["status"], "queued")

    def test_shutdown_between_dequeue_and_spawn_does_not_start_a_child(self):
        commands = self.service._commands

        def stop_before_spawn(job):
            result = commands(job)
            self.service._stop.set()
            return result

        with patch.object(self.service, "_commands", side_effect=stop_before_spawn):
            job = self.service.submit({"input_path": str(self.input), "output_path": str(self.output)})
            for _ in range(100):
                current = self.service.store.get(job["id"])
                if current["status"] not in {"queued", "running"}:
                    break
                time.sleep(.02)
        self.assertEqual(current["status"], "interrupted")
        self.assertNotIn(job["id"], self.service._processes)
        self.assertFalse(self.output.exists())

    def test_loopback_quit_and_build_recognition(self):
        build = self.root / "desktop-build.json"
        build.write_text(json.dumps({"build_id": "this-build"}))
        server = create_server(self.service, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            self.assertEqual(desktop_app.existing_instance(base, self.root), desktop_app.EXISTING_BUILD)
            with tempfile.TemporaryDirectory() as directory:
                self.assertEqual(desktop_app.existing_instance(base, Path(directory)), desktop_app.OTHER_BUILD)
            self.assertFalse(self.service._quit_requested)  # GET never stops work.
            body = json.dumps(self.payload()).encode()
            cross_site = urllib.request.Request(base + "/api/service/quit", data=body,
                headers={"Content-Type": "application/json", "Origin": "https://example.com"})
            with self.assertRaises(urllib.error.HTTPError):
                urllib.request.urlopen(cross_site, timeout=5)
            self.assertFalse(self.service._quit_requested)
            request = urllib.request.Request(base + "/api/service/quit", data=body,
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(request, timeout=5) as response:
                self.assertEqual(response.status, 202)
                self.assertTrue(json.load(response)["quitting"])
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_corrupt_build_metadata_does_not_break_controls(self):
        for content in ("[]", "invalid"):
            (self.root / "desktop-build.json").write_text(content)
            self.assertIsNone(self.service.lifecycle_status()["build_id"])

    def test_watch_is_read_only_lightweight_and_bounded(self):
        with patch.object(self.service, "lifecycle_status", side_effect=AssertionError("must not read databases")):
            initial = self.service.watch_lifecycle("")
            self.assertEqual(set(initial), {"service", "instance_id", "desktop_app", "quitting", "restarting"})
            self.assertFalse(initial["quitting"])
            self.assertEqual(self.service.watch_lifecycle("previous-instance"), initial)
            self.assertEqual(self.service.watch_lifecycle(self.service._instance_id, timeout=.01), initial)
            with self.assertRaisesRegex(ValueError, "Invalid"):
                self.service.watch_lifecycle("x" * 129)
            with patch.object(self.service._lifecycle_watch_slots, "acquire", return_value=False):
                with self.assertRaisesRegex(ValueError, "Too many"):
                    self.service.watch_lifecycle(self.service._instance_id)
        with self.assertRaises(ValueError):
            self.service.request_service_quit({})
        self.assertFalse(self.service._lifecycle_changed.is_set())

    def test_all_browser_watchers_receive_quit_before_listener_stops(self):
        server = create_server(self.service, port=0)
        serving = threading.Thread(target=server.serve_forever, daemon=True)
        serving.start()
        base = f"http://127.0.0.1:{server.server_port}"
        waiting = threading.Barrier(4)
        responses, errors = [], []
        real_wait = self.service._lifecycle_changed.wait

        def wait(timeout):
            waiting.wait(timeout=5)
            return real_wait(timeout)

        def browser():
            try:
                with urllib.request.urlopen(base + "/api/service/watch?instance_id=" + self.service._instance_id, timeout=5) as response:
                    responses.append(json.load(response))
            except Exception as error:
                errors.append(error)

        clients = [threading.Thread(target=browser, daemon=True) for _ in range(3)]
        try:
            request = urllib.request.Request(base + "/api/service/watch", headers={"Origin": "https://example.com"})
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(request, timeout=5)
            self.assertEqual(error.exception.code, 403)
            with patch.object(self.service._lifecycle_changed, "wait", side_effect=wait):
                for client in clients:
                    client.start()
                waiting.wait(timeout=5)
                request = urllib.request.Request(base + "/api/service/quit", data=json.dumps(self.payload()).encode(),
                    headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(request, timeout=5) as response:
                    self.assertEqual(response.status, 202)
                for client in clients:
                    client.join(timeout=5)
            self.assertEqual(errors, [])
            self.assertEqual(len(responses), 3)
            self.assertTrue(all(response["quitting"] for response in responses))
            serving.join(timeout=5)
            self.assertFalse(serving.is_alive())
        finally:
            self.service._lifecycle_changed.set()
            server.shutdown()
            server.server_close()
            serving.join(timeout=5)

    def test_legacy_unknown_and_redirected_listeners_are_not_reused(self):
        class Handler(BaseHTTPRequestHandler):
            mode = "unknown"
            paths = []

            def do_GET(self):
                self.paths.append(self.path)
                if self.mode == "redirect":
                    self.send_response(302)
                    self.send_header("Location", "/do-not-follow")
                    self.end_headers()
                    return
                self.send_response(200)
                self.end_headers()
                value = {"service": "IEI Variant Review local service"} if self.mode == "legacy" and self.path == "/api/capabilities" else {}
                self.wfile.write(json.dumps(value).encode())

            def log_message(self, *_):
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            for mode, expected in (("unknown", 44), ("legacy", 43), ("redirect", 44)):
                Handler.mode = mode
                self.assertEqual(desktop_app.existing_instance(f"http://127.0.0.1:{server.server_port}", self.root), expected)
            self.assertNotIn("/do-not-follow", Handler.paths)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
