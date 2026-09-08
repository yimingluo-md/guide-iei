"""Install a large chain through the real reference script, entirely offline."""
import gzip
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
EOF = bytes.fromhex("1f8b08040000000000ff0600424302001b0003000000000000000000")


class ChainInstallationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.binary = self.root / "bin"
        self.binary.mkdir()
        # References are already supplied. Neither network nor HTS execution
        # is needed to exercise the production chain normalization/promotion.
        for name, status in (("samtools", 0), ("curl", 99), ("wget", 99)):
            tool = self.binary / name
            tool.write_text(f"#!/bin/sh\nexit {status}\n")
            tool.chmod(0o755)
        fasta = self.root / "hg19.fa.gz"
        fasta.write_bytes(gzip.compress(b">1\nA\n") + EOF)
        self.chain = self.root / "hg19ToHg38.over.chain.gz"
        self.raw = self.root / "hg19ToHg38.over.chain.ucsc.chain.gz"
        self.config = self.root / "config.yaml"
        self.config.write_text(json.dumps({
            "reference": {"assembly": "GRCh38"},
            "liftover": {"grch37_to_grch38": {
                "source_fasta": str(fasta), "chain": str(self.chain),
            }},
        }))

    def run_installer(self):
        return subprocess.run(
            ["bash", str(ROOT / "scripts/download_references.sh"), str(self.config),
             "--only", "liftover", "--skip-final-status"],
            cwd=ROOT, text=True, capture_output=True, timeout=30,
            env={**os.environ, "PATH": str(self.binary) + os.pathsep + os.environ["PATH"]},
        )

    def test_large_valid_chain_is_published(self):
        body = (b"chain 1 chr1 300001 + 0 300001 chr1 300001 + 0 300001 1\n"
                + b"1 1 1\n" * 150000 + b"1\n\n")
        self.raw.write_bytes(gzip.compress(body))
        result = self.run_installer()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(gzip.decompress(self.chain.read_bytes()), body.replace(b"chr1", b"1"))
        self.assertTrue(Path(str(self.chain) + ".sha256.local").is_file())
        self.assertFalse(self.raw.exists())

    def test_no_records_and_corrupt_input_are_never_published(self):
        for payload in (gzip.compress(b"not a chain\n"), gzip.compress(b"chain 1\n" * 20000)[:-8]):
            with self.subTest(size=len(payload)):
                self.raw.write_bytes(payload)
                result = self.run_installer()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("ERROR:", result.stderr)
                self.assertFalse(self.chain.exists())
                self.assertFalse(Path(str(self.chain) + ".tmp").exists())


if __name__ == "__main__":
    unittest.main()
