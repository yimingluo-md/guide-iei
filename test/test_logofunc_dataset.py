import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.logofunc_dataset import install_move  # noqa: E402


class LoGoFuncManagedImportTests(unittest.TestCase):
    def test_install_move_consumes_source_and_places_file_in_managed_storage(self):
        with tempfile.TemporaryDirectory() as temporary:
            tmp_path = Path(temporary)
            source = tmp_path / "download" / "scores.csv.gz"
            destination = tmp_path / "annotations" / "scores.csv.gz"
            source.parent.mkdir()
            source.write_bytes(b"validated LoGoFunc source")

            install_move(source, destination)

            self.assertEqual(destination.read_bytes(), b"validated LoGoFunc source")
            self.assertFalse(destination.is_symlink())
            self.assertFalse(source.exists())
            self.assertEqual(destination.read_bytes(), b"validated LoGoFunc source")


if __name__ == "__main__":
    unittest.main()
