#!/usr/bin/env python3
"""Reject corrupt inputs before any native/container work, using both entries."""
import gzip
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pipeline.validate_input_vcf import validate_input_vcf

HEADER = ("##fileformat=VCFv4.2\n"
          "##reference=GRCh38\n"
          "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\n")
RECORD = "1\t100\t.\tA\tG\t.\tPASS\t.\tGT:DP\t0/1:10\n"


class InputValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.vcf = self.root / "input.vcf"

    def check_text(self, text, message=None):
        self.vcf.write_text(text)
        if message:
            with self.assertRaisesRegex(ValueError, message):
                validate_input_vcf(self.vcf)
        else:
            return validate_input_vcf(self.vcf)

    def test_valid_empty_and_multiallelic_symbolic_and_missing_calls(self):
        self.assertEqual(self.check_text(HEADER)["records_checked"], 0)
        for alt, gt in [("G,T", "1/2"), ("<DEL>", "1"), ("A]2:200]", "0|1"), ("*", "."), (".", "0/0")]:
            record = RECORD.replace("\tG\t", f"\t{alt}\t").replace("0/1:10", gt)
            self.assertEqual(self.check_text(HEADER + record)["records_checked"], 1)

    def test_crlf_and_missing_trailing_format_subfield(self):
        self.check_text((HEADER + RECORD.replace("0/1:10", "0/1")).replace("\n", "\r\n"))

    def test_fixture_has_mandatory_info_and_preserves_original_calls(self):
        self.assertEqual(validate_input_vcf(ROOT / "test/sample.mini.vcf")["records_checked"], 3)
        rows = [line.split("\t") for line in (ROOT / "test/sample.mini.vcf").read_text().splitlines() if not line.startswith("#")]
        self.assertEqual([row[9] for row in rows], ["0/1", "1/1", "0/1"])

    def test_missing_info_column_duplicate_samples_and_short_late_row(self):
        self.check_text(HEADER.replace("INFO\t", "") + RECORD, "INFO is mandatory")
        self.check_text(HEADER.replace("S1\n", "S1\tS1\n"), "duplicate sample")
        self.check_text(HEADER + RECORD + "1\t100\t.\tA\tG\n", "line 5: expected 10")
        self.check_text(HEADER + RECORD.replace("\t100\t", "\tbad\t"), "POS must be")
        self.check_text(HEADER + RECORD.replace("0/1:10", "0/1:10:extra"), "more subfields")
        self.check_text(HEADER + RECORD.replace("\tPASS\t", "\tPA\x00SS\t"), "NUL bytes")

    def test_header_only_is_explicit_and_full_scan_catches_late_error(self):
        self.vcf.write_text(HEADER + RECORD + "broken\n")
        self.assertFalse(validate_input_vcf(self.vcf, full=False)["full_scan"])
        with self.assertRaises(ValueError):
            validate_input_vcf(self.vcf)

    def test_truncated_gzip_crc_and_invalid_utf8(self):
        path = self.root / "input.vcf.gz"
        encoded = gzip.compress((HEADER + RECORD * 100).encode())
        for damaged in (encoded[:-5], encoded[:-8] + b"badcrc!!", gzip.compress(HEADER.encode() + b"\xff\n")):
            path.write_bytes(damaged)
            with self.assertRaisesRegex(ValueError, "truncated, corrupt"):
                validate_input_vcf(path)
        path.write_bytes(encoded)
        self.assertEqual(validate_input_vcf(path)["records_checked"], 100)

    def test_both_entry_points_refuse_malformed_input_before_runtime(self):
        config = self.root / "config.yaml"
        config.write_text("reference:\n  assembly: GRCh38\nregion:\n  coding_only: false\ncontainer:\n  runtime: should-not-run\n")
        self.vcf.write_text(HEADER.replace("INFO\t", "") + RECORD)
        commands = [
            ["bash", str(ROOT / "scripts/preflight.sh"), str(config), str(self.vcf), str(self.root / "out.vcf.gz")],
            ["bash", str(ROOT / "scripts/run_annotation.sh"), "-c", str(config), "-i", str(self.vcf), "-o", str(self.root / "out.vcf.gz")],
        ]
        for command in commands:
            result = subprocess.run(command, text=True, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("INFO is mandatory", result.stderr)
            self.assertNotIn("container runtime not found", result.stderr)
            self.assertFalse((self.root / "out.vcf.gz").exists())


if __name__ == "__main__":
    unittest.main()
