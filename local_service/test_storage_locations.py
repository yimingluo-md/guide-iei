import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from local_service.storage_locations import StorageLocationRegistry


class StorageRegistryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name).resolve()
        self.path = self.directory / "registry.json"

    def registry(self):
        return StorageLocationRegistry(self.directory, self.directory / "default", registry_path=self.path)

    def test_competing_writers_preserve_both_roots_and_migrations(self):
        first, second = self.registry(), self.registry()
        writers = [
            lambda: first.set_root("data", self.directory / "library", storage_id="drive"),
            lambda: second.set_root("annotation", self.directory / "references2"),
            lambda: first.record_migration({"id": "first"}),
            lambda: second.record_migration({"id": "second"}),
        ]
        start = threading.Barrier(len(writers))

        def update(writer):
            start.wait(timeout=10)
            writer()

        with ThreadPoolExecutor(max_workers=len(writers)) as pool:
            list(pool.map(update, writers))
        data = json.loads(self.path.read_text())
        self.assertEqual(data["revision"], 4)
        self.assertEqual(data["data_root"], str(self.directory / "library"))
        self.assertEqual(data["data_storage_id"], "drive")
        self.assertEqual(data["annotation_root"], str(self.directory / "references2"))
        self.assertEqual({row["id"] for row in data["migrations"]}, {"first", "second"})
        self.assertEqual(first.backup_path.read_bytes(), self.path.read_bytes())

    def test_failed_first_write_does_not_publish_in_memory(self):
        registry = self.registry()
        with patch.object(registry, "_atomic_write", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                registry.set_root("data", self.directory / "new", storage_id="new")
        self.assertEqual(registry.root("data"), self.directory / "default")
        self.assertEqual(registry.storage_id("data"), "")

    def test_committed_backup_survives_primary_failure_and_repairs_on_reopen(self):
        registry = self.registry()
        registry.set_root("data", self.directory / "old")
        write = registry._atomic_write

        def fail_primary(path, data):
            if path == self.path:
                raise OSError("primary unavailable")
            write(path, data)

        with patch.object(registry, "_atomic_write", side_effect=fail_primary), self.assertLogs(level="WARNING"):
            registry.set_root("data", self.directory / "new", storage_id="new")
        self.assertEqual(registry.storage_id("data"), "new")
        self.assertEqual(self.registry().root("data"), self.directory / "new")
        self.assertEqual(registry.backup_path.read_bytes(), self.path.read_bytes())
