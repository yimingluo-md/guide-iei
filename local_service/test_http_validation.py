"""HTTP boundary tests without opening sockets or using real workstation data."""
import io
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from local_service.workbench_service import WorkbenchRequestHandler


class HttpBoundaryTests(unittest.TestCase):
    def handler(self):
        handler = object.__new__(WorkbenchRequestHandler)
        handler.server = SimpleNamespace(web_root=None)
        handler.headers = {"Host": "127.0.0.1:43117"}
        handler.service = Mock()
        handler._json = Mock()
        return handler

    def test_invalid_body_lengths_are_rejected_before_reading(self):
        for length in ("-1", "-100", "abc", "1000001"):
            with self.subTest(length=length):
                handler = self.handler()
                handler.headers["Content-Length"] = length
                handler.rfile = Mock()
                with self.assertRaises(ValueError):
                    handler._body()
                handler.rfile.read.assert_not_called()

    def test_invalid_list_limits_return_json_error(self):
        for route in ("sample-library", "cohort/samples", "phenotypes"):
            handler = self.handler()
            handler.path = f"/api/{route}?limit=invalid"
            handler.do_GET()
            self.assertEqual(handler._json.call_args.args[1], 400)

    def test_unicode_download_names_are_valid_http_headers(self):
        with tempfile.TemporaryDirectory() as directory:
            filename = '研究 "sample".vcf.gz'
            source = Path(directory) / filename
            source.write_bytes(b"synthetic content")
            handler = self.handler()
            headers = {}
            def send_header(name, value):
                value.encode("latin-1")  # BaseHTTPRequestHandler's wire encoding
                self.assertNotIn("\r", value)
                self.assertNotIn("\n", value)
                headers[name] = value
            handler.send_header = send_header
            handler.send_response = Mock()
            handler.end_headers = Mock()
            handler.wfile = io.BytesIO()
            handler._file(source)
            self.assertIn("filename*=UTF-8''", headers["Content-Disposition"])
            self.assertEqual(handler.wfile.getvalue(), b"synthetic content")


class ErrorPropagationTests(unittest.TestCase):
    """Audit M23: unexpected exceptions become a JSON 500 with a log
    reference (no dropped connection, no path in the response); a missing
    payload field is a 400 naming the field; only a genuine lookup miss is
    a 404."""

    def handler(self, method="GET", body=b"{}"):
        import json as json_module
        handler = object.__new__(WorkbenchRequestHandler)
        handler.server = SimpleNamespace(web_root=None)
        handler.headers = {
            "Host": "127.0.0.1:43117",
            "Content-Length": str(len(body)),
        }
        handler.command = method
        handler.service = Mock()
        handler.service.begin_storage_mutation = Mock()
        handler.service.end_storage_mutation = Mock()
        handler.rfile = io.BytesIO(body)
        handler._json = Mock()
        return handler

    def _status_and_payload(self, handler):
        args = handler._json.call_args.args
        return (args[1] if len(args) > 1 else 200), args[0]

    def test_cohort_merge_read_refusal_returns_retryable_json_on_get_and_post(self):
        from local_service.errors import CohortMergeBusyError
        for method, path, operation in (
            ("GET", "/api/cohort/samples", "list_samples"),
            ("POST", "/api/cohort/query", "query"),
        ):
            with self.subTest(method=method):
                handler = self.handler(method)
                handler.path = path
                getattr(handler.service.cohort, operation).side_effect = CohortMergeBusyError()
                getattr(handler, "do_" + method)()
                status, payload = self._status_and_payload(handler)
                self.assertEqual(status, 503)
                self.assertEqual(payload["kind"], "cohort_merge_busy")
                self.assertIn("retry", payload["error"])

    def test_store_and_filesystem_failures_become_json_500_with_reference(self):
        import sqlite3 as sqlite_module
        secret_path = "/Users/someone/Library/private/cohort.sqlite3"
        failures = [
            sqlite_module.OperationalError(f"unable to open database file {secret_path}"),
            OSError(30, f"Read-only file system: '{secret_path}'"),
            RuntimeError(f"unexpected state in {secret_path}"),
        ]
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                handler = self.handler("GET")
                handler.path = "/api/jobs"
                handler.service.store.list.side_effect = failure
                stderr = io.StringIO()
                with patch("sys.stderr", stderr):
                    handler.do_GET()
                status, payload = self._status_and_payload(handler)
                self.assertEqual(status, 500)
                self.assertEqual(payload["kind"], type(failure).__name__)
                self.assertIn("reference", payload)
                # The response never carries the workstation path; the log does.
                self.assertNotIn(secret_path, json_dumps(payload))
                self.assertIn(secret_path, stderr.getvalue())
                self.assertIn(payload["reference"], stderr.getvalue())

    def test_post_failures_become_json_500_and_release_the_mutation_guard(self):
        handler = self.handler("POST", b'{"input_path": "/tmp/x.vcf"}')
        handler.path = "/api/jobs"
        handler.service.submit.side_effect = OSError("disk unplugged")
        with patch("sys.stderr", io.StringIO()):
            handler.do_POST()
        status, payload = self._status_and_payload(handler)
        self.assertEqual(status, 500)
        self.assertEqual(payload["kind"], "OSError")
        handler.service.end_storage_mutation.assert_called_once()

    def test_lock_timeout_during_an_import_merge_is_a_clear_409(self):
        """Audit M27: a Sample Library/phenotype write that still times out
        while an import is merging is answered as 'import finishing', not as
        an internal error; the same timeout with no import running is."""
        import sqlite3 as sqlite_module
        handler = self.handler("POST", b'{"individual_id": "X"}')
        handler.path = "/api/phenotypes/individual"
        handler.service.phenotypes.save_individual.side_effect = sqlite_module.OperationalError("database is locked")
        handler.service.cohort.has_active_import.return_value = True
        handler.do_POST()
        status, payload = self._status_and_payload(handler)
        self.assertEqual(status, 409)
        self.assertEqual(payload["kind"], "import_finishing")
        self.assertIn("import is finishing", payload["error"])
        handler.service.end_storage_mutation.assert_called_once()

        handler = self.handler("POST", b'{"individual_id": "X"}')
        handler.path = "/api/phenotypes/individual"
        handler.service.phenotypes.save_individual.side_effect = sqlite_module.OperationalError("database is locked")
        handler.service.cohort.has_active_import.return_value = False
        with patch("sys.stderr", io.StringIO()):
            handler.do_POST()
        status, payload = self._status_and_payload(handler)
        self.assertEqual(status, 500)

    def test_missing_payload_field_is_a_400_not_job_not_found(self):
        handler = self.handler("POST", b"{}")
        handler.path = "/api/jobs"
        handler.service.submit.side_effect = KeyError("input_path")
        handler.do_POST()
        status, payload = self._status_and_payload(handler)
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"], "missing required field: input_path")

    def test_lookup_miss_is_a_404_with_its_own_message(self):
        from local_service.errors import NotFoundError
        handler = self.handler("POST", b"{}")
        handler.path = "/api/jobs"
        handler.service.submit.side_effect = NotFoundError(
            "library dataset not found"
        )
        handler.do_POST()
        status, payload = self._status_and_payload(handler)
        self.assertEqual(status, 404)
        self.assertEqual(payload["error"], "library dataset not found")

    def test_get_lookup_miss_is_a_404(self):
        from local_service.errors import NotFoundError
        handler = self.handler("GET")
        handler.path = "/api/jobs"
        handler.service.store.list.side_effect = NotFoundError("job not found: j1")
        handler.do_GET()
        status, payload = self._status_and_payload(handler)
        self.assertEqual(status, 404)
        self.assertEqual(payload["error"], "job not found: j1")


def json_dumps(value) -> str:
    import json as json_module
    return json_module.dumps(value)


if __name__ == "__main__":
    unittest.main()
