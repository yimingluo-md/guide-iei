import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class NativeReferenceBundleManifestTests(unittest.TestCase):
    def test_manifest_is_complete_and_self_consistent(self):
        manifest = json.loads(
            (ROOT / "config" / "native_reference_bundle.json").read_text(encoding="utf-8")
        )
        paths = {item["path"] for item in manifest["files"]}
        self.assertIn("regions/screen.registry-v4.GRCh38.ccre.bed.gz", paths)
        self.assertIn("regions/screen.registry-v4.GRCh38.ccre.bed.gz.tbi", paths)
        self.assertIn("liftover/hg19.primary.fa.gz", paths)
        self.assertIn("liftover/hg19.primary.fa.gz.fai", paths)
        self.assertIn("liftover/hg19.primary.fa.gz.gzi", paths)
        self.assertIn("liftover/hg19ToGRCh38.ensembl.over.chain.gz", paths)
        self.assertEqual(
            manifest["estimated_bytes"],
            sum(item["bytes"] for item in manifest["files"]),
        )
        for item in manifest["files"]:
            self.assertGreater(item["bytes"], 0)
            self.assertRegex(item["sha256"], r"^[0-9a-f]{64}$")
            self.assertFalse(Path(item["path"]).is_absolute())
            self.assertNotIn("..", Path(item["path"]).parts)


if __name__ == "__main__":
    unittest.main()
