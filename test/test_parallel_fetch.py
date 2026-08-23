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

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from parallel_fetch import load_resume_state  # noqa: E402


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

    def call(self):
        return load_resume_state(
            self.state, self.partial, self.meta["url"], self.meta["size"],
            self.meta["etag"], self.meta["chunk_size"],
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


if __name__ == "__main__":
    unittest.main()
