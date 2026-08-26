#!/usr/bin/env python3
"""Resume-state validation for the parallel downloader.

The completed-range set is only trustworthy while the partial file it
describes exists at full size; trusting it after the partial vanished
recreated a zero-filled sparse file and published it as complete.
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from parallel_fetch import (  # noqa: E402
    curl_url_config,
    load_resume_state,
    migrate_resume_url_key,
    remote_metadata,
)


class ResumeStateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.partial = self.root / "data.bin.parallel"
        self.state = self.root / "data.bin.ranges.json"
        self.meta = {"url": "https://example/data.bin", "size": 4096,
                     "etag": "abc", "chunk_size": 1024}

    def tearDown(self):
        self.temp.cleanup()

    def write_state(self, completed):
        self.state.write_text(json.dumps({**self.meta, "completed": completed}))

    def call(self, last_modified=None):
        return load_resume_state(
            self.state, self.partial, self.meta["url"], self.meta["size"],
            self.meta["etag"], self.meta["chunk_size"], last_modified,
        )

    def test_valid_state_with_full_size_partial_resumes(self):
        self.write_state([0, 2])
        self.partial.write_bytes(b"\0" * 4096)
        self.assertEqual(self.call(), {0, 2})

    def test_missing_partial_discards_state_and_restarts(self):
        self.write_state([0, 1, 2, 3])
        self.assertEqual(self.call(), set())
        self.assertFalse(self.state.exists())

    def test_wrong_size_partial_discards_state_and_restarts(self):
        self.write_state([0, 1])
        self.partial.write_bytes(b"\0" * 100)
        self.assertEqual(self.call(), set())
        self.assertFalse(self.state.exists())
        self.assertFalse(self.partial.exists())

    def test_metadata_mismatch_is_a_hard_error(self):
        self.state.write_text(json.dumps({**self.meta, "size": 9999,
                                          "completed": [0]}))
        self.partial.write_bytes(b"\0" * 4096)
        with self.assertRaisesRegex(RuntimeError, "resume metadata differs"):
            self.call()

    def test_no_state_starts_fresh(self):
        self.assertEqual(self.call(), set())

    def test_moved_last_modified_invalidates_resume_state(self):
        """Without Last-Modified in the identity, a remote that changed
        while keeping its size (and serving no usable ETag) let a resume
        COMBINE ranges of two versions into one published file."""
        self.state.write_text(json.dumps({
            **self.meta, "last_modified": "Tue, 18 Aug 2026 07:00:00 GMT",
            "completed": [0, 1],
        }))
        self.partial.write_bytes(b"\0" * 4096)
        # Same date: resume proceeds.
        self.assertEqual(
            self.call("Tue, 18 Aug 2026 07:00:00 GMT"), {0, 1}
        )
        # Moved date: hard error, exactly like any other identity change.
        with self.assertRaisesRegex(RuntimeError, "resume metadata differs"):
            self.call("Wed, 19 Aug 2026 07:00:00 GMT")

    def test_legacy_state_restarts_when_the_remote_reports_a_date(self):
        """A pre-fix state cannot prove which version its ranges came
        from; resuming it against a dated remote COMBINED two versions
        into one published file. It restarts instead — one-time cost."""
        self.write_state([0, 3])
        self.partial.write_bytes(b"\0" * 4096)
        self.assertEqual(
            self.call("Wed, 19 Aug 2026 07:00:00 GMT"), set()
        )
        self.assertFalse(self.state.exists())
        self.assertFalse(self.partial.exists())

    def test_legacy_state_without_any_remote_date_keeps_old_checks(self):
        self.write_state([0, 3])
        self.partial.write_bytes(b"\0" * 4096)
        self.assertEqual(self.call(""), {0, 3})

    def test_last_modified_presence_flap_restarts_instead_of_erroring(self):
        """Absence of a date on one side is unknown, not evidence of
        change — a hard error demanded manual file surgery for a healthy
        remote whose front-end config changed."""
        self.state.write_text(json.dumps({
            **self.meta, "last_modified": "", "completed": [1],
        }))
        self.partial.write_bytes(b"\0" * 4096)
        self.assertEqual(
            self.call("Tue, 18 Aug 2026 07:00:00 GMT"), set()
        )
        self.assertFalse(self.state.exists())

    def test_private_url_is_passed_to_curl_config_not_process_arguments(self):
        private_url = "https://example.test/academic/private/data.gz?token=secret"
        completed = Mock(
            stdout=(
                "HTTP/2 206\r\n"
                "content-range: bytes 0-0/4096\r\n"
                "etag: test-etag\r\n"
                "last-modified: Wed, 26 Aug 2026 12:00:00 GMT\r\n\r\n"
            )
        )
        with patch("parallel_fetch.subprocess.run", return_value=completed) as run:
            self.assertEqual(
                remote_metadata(private_url),
                (4096, "test-etag", "Wed, 26 Aug 2026 12:00:00 GMT"),
            )
        command = run.call_args.args[0]
        self.assertNotIn(private_url, command)
        self.assertEqual(command[-2:], ["--config", "-"])
        self.assertIn(private_url, run.call_args.kwargs["input"])

    def test_curl_url_config_rejects_multiline_input(self):
        with self.assertRaises(ValueError):
            curl_url_config("https://example.test/data\nurl=https://attacker.test")

    def test_private_resume_url_migrates_to_stable_key(self):
        private_url = "https://example.test/academic/data.gz?token=expired"
        stable_key = "dbnsfp:data.gz"
        self.state.write_text(json.dumps({
            **self.meta, "url": private_url, "completed": [0, 2],
        }))
        self.partial.write_bytes(b"\0" * 4096)
        migrate_resume_url_key(self.state, "https://example.test/new", stable_key)
        persisted = json.loads(self.state.read_text())
        self.assertEqual(persisted["url"], stable_key)
        self.assertNotIn("expired", self.state.read_text())
        self.assertEqual(
            load_resume_state(
                self.state, self.partial, stable_key, self.meta["size"],
                self.meta["etag"], self.meta["chunk_size"],
            ),
            {0, 2},
        )


if __name__ == "__main__":
    unittest.main()
