#!/usr/bin/env python3
"""Audit M16: VEP writes to a run-scoped temporary file that is promoted to
the deliverable name only after validate_vep_output.py accepts it.

The runner is exercised for real up to and including the VEP invocation,
with a stand-in ``docker`` that answers the file-access probe and plays a VEP
whose output lacks the CSQ header. The previous run's deliverable (and the
sidecar pointing at it) must survive untouched, and nothing may be left under
the temporary name.
"""
from __future__ import annotations

import gzip
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]

FAKE_DOCKER = r'''#!/usr/bin/env python3
"""Stand-in for the container engine: answers `image inspect`, the perl
file-access probe (writes the .container markers), and a `sh -c "vep ..."`
invocation (writes a VCF without a CSQ header to the -o path)."""
import gzip, os, re, sys
argv = sys.argv[1:]
if argv[:2] == ["image", "inspect"]:
    sys.exit(0)
if argv[:1] != ["run"]:
    sys.exit(0)
mounts = []
for i, a in enumerate(argv):
    if a == "-v" and i + 1 < len(argv):
        host, cont, *_ = argv[i + 1].split(":")
        mounts.append((cont, host))
mounts.sort(key=lambda m: -len(m[0]))
def to_host(path):
    for cont, host in mounts:
        if path == cont or path.startswith(cont + "/"):
            return host + path[len(cont):]
    raise SystemExit("unmapped container path: " + path)
if "--entrypoint" in argv and argv[argv.index("--entrypoint") + 1] == "perl":
    tail = argv[argv.index("-e") + 2:]
    for j in range(0, len(tail), 4):
        index, mode, target, _digest = tail[j:j + 4]
        if mode == "write":
            with open(to_host(target) + ".container", "wb") as fh:
                fh.write(b"container-write-ok\n")
        print(f"{index}:OK")
    sys.exit(0)
if "-c" in argv:
    cmd = argv[argv.index("-c") + 1]
    match = re.search(r"(?:^|\s)'?-o'?\s+'?([^'\s]+)'?", cmd)
    out = to_host(match.group(1))
    body = b"##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n1\t1\t.\tA\tG\t.\tPASS\t.\n"
    with gzip.open(out, "wb") as fh:
        fh.write(body)
    sys.exit(0)
sys.exit(0)
'''


def _ensure_fixtures():
    """The same throwaway reference files test_dry_run.sh creates."""
    for rel in (
        "test/fixtures/vep_cache/homo_sapiens/113_GRCh38",
        "test/fixtures/fasta", "test/fixtures/loftee", "test/fixtures/custom",
        "test/fixtures/clinvar",
    ):
        (ROOT / rel).mkdir(parents=True, exist_ok=True)
    for rel in (
        "test/fixtures/fasta/genome.fa.gz",
        "test/fixtures/loftee/human_ancestor.fa.gz", "test/fixtures/loftee/loftee.sql",
        "test/fixtures/loftee/gerp.bw", "test/fixtures/custom/repeatmasker.bed.gz",
        "test/fixtures/clinvar/clinvar_latest.GRCh38.vcf.gz",
        # The access probe needs one real file inside the cache directory.
        "test/fixtures/vep_cache/homo_sapiens/113_GRCh38/info.txt",
    ):
        path = ROOT / rel
        if not path.exists():
            path.touch()


@unittest.skipUnless(
    shutil.which("bcftools") and shutil.which("bgzip") and shutil.which("tabix"),
    "bcftools/bgzip/tabix are needed to reach the VEP step natively",
)
class VepOutputPromotionTests(unittest.TestCase):
    def setUp(self):
        _ensure_fixtures()
        self.temp = tempfile.TemporaryDirectory()
        self.dir = pathlib.Path(self.temp.name)
        self.bin = self.dir / "bin"
        self.bin.mkdir()
        fake = self.bin / "docker"
        fake.write_text(FAKE_DOCKER)
        fake.chmod(0o755)
        self.out_dir = self.dir / "out dir"
        self.out_dir.mkdir()
        self.output = self.out_dir / "sample.vep.vcf.gz"
        self.previous = b"previous-deliverable-bytes"
        self.output.write_bytes(self.previous)
        self.sidecar = pathlib.Path(str(self.output) + ".deliverable")
        self.sidecar.write_text("sample.vep.vcf.gz\n")

    def tearDown(self):
        self.temp.cleanup()

    def _run(self):
        env = os.environ.copy()
        env["PATH"] = str(self.bin) + os.pathsep + env["PATH"]
        return subprocess.run(
            [
                "bash", str(ROOT / "scripts/run_annotation.sh"),
                "-i", "test/sample.mini.vcf", "-o", str(self.output),
                "-c", "test/test.config.yaml", "--no-clinvar", "--all-variants",
            ],
            cwd=ROOT, env=env, text=True, capture_output=True, timeout=600,
        )

    def test_unvalidated_vep_output_is_never_promoted(self):
        result = self._run()
        self.assertNotEqual(result.returncode, 0, result.stderr)
        # The pre-filter census (one pass) supplies both totals (audit M18).
        self.assertIn("input pre-filter (FILTER=PASS-or-unfiltered): 3 -> 3 variants", result.stderr)
        self.assertIn("VEP writing to ", result.stderr)
        self.assertIn("required annotation validation failed", result.stderr)
        self.assertIn("was not promoted", result.stderr)
        # The previous deliverable and its pointer are intact...
        self.assertEqual(self.output.read_bytes(), self.previous)
        self.assertTrue(self.sidecar.exists())
        # ...and the temporary output (plus VEP sidecars) is gone.
        leftovers = [p.name for p in self.out_dir.iterdir() if "vep-tmp" in p.name]
        self.assertEqual(leftovers, [])
        scratch = [p.name for p in self.out_dir.iterdir() if p.name.startswith(".guide-iei-work.")]
        self.assertEqual(scratch, [])


if __name__ == "__main__":
    unittest.main()
