#!/usr/bin/env python3
import tempfile
import time
import unittest
import zlib
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from local_service.job_progress import (
    BgzfRecordCounter,
    JobProgressTracker,
)


def bgzf_block(data: bytes) -> bytes:
    """One standards-shaped BGZF block (as bgzip writes them)."""
    compressor = zlib.compressobj(6, zlib.DEFLATED, -15)
    payload = compressor.compress(data) + compressor.flush()
    block_size = 12 + 6 + len(payload) + 8
    return (
        b"\x1f\x8b\x08\x04\x00\x00\x00\x00\x00\xff"
        + (6).to_bytes(2, "little")
        + b"BC" + (2).to_bytes(2, "little")
        + (block_size - 1).to_bytes(2, "little")
        + payload
        + zlib.crc32(data).to_bytes(4, "little")
        + len(data).to_bytes(4, "little")
    )


HEADER = b"##fileformat=VCFv4.2\n##contig=<ID=1>\n#CHROM\tPOS\n"


class BgzfCounterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "out.vcf.gz"

    def tearDown(self):
        self.temp.cleanup()

    def test_counts_records_excluding_header(self):
        records = b"".join(b"1\t%d\n" % i for i in range(100, 150))
        self.path.write_bytes(bgzf_block(HEADER) + bgzf_block(records))
        counter = BgzfRecordCounter(self.path)
        self.assertEqual(counter.advance(), 50)

    def test_incremental_growth_and_partial_trailing_block(self):
        counter = BgzfRecordCounter(self.path)
        self.path.write_bytes(bgzf_block(HEADER))
        self.assertEqual(counter.advance(), 0)
        block = bgzf_block(b"1\t100\n1\t101\n")
        # Append only half a block: nothing counted, nothing consumed.
        with self.path.open("ab") as handle:
            handle.write(block[: len(block) // 2])
        self.assertEqual(counter.advance(), 0)
        with self.path.open("ab") as handle:
            handle.write(block[len(block) // 2:])
        self.assertEqual(counter.advance(), 2)
        with self.path.open("ab") as handle:
            handle.write(bgzf_block(b"1\t102\n"))
        self.assertEqual(counter.advance(), 3)

    def test_header_split_across_blocks_and_lines_split_across_blocks(self):
        self.path.write_bytes(
            bgzf_block(HEADER[:10]) + bgzf_block(HEADER[10:] + b"1\t1")
            + bgzf_block(b"00\n1\t101\n")
        )
        counter = BgzfRecordCounter(self.path)
        self.assertEqual(counter.advance(), 2)

    def test_bgzf_eof_marker_is_harmless(self):
        empty = bgzf_block(b"")
        self.path.write_bytes(bgzf_block(HEADER) + bgzf_block(b"1\t100\n") + empty)
        self.assertEqual(BgzfRecordCounter(self.path).advance(), 1)

    def test_missing_file_reports_zero(self):
        self.assertEqual(BgzfRecordCounter(self.path).advance(), 0)

    def test_real_bgzip_output_when_available(self):
        import shutil as _shutil
        import subprocess
        bgzip = _shutil.which("bgzip")
        if not bgzip:
            self.skipTest("bgzip not available")
        raw = Path(self.temp.name) / "raw.vcf"
        raw.write_bytes(HEADER + b"".join(b"1\t%d\n" % i for i in range(1000)))
        subprocess.run([bgzip, "-c", str(raw)], stdout=self.path.open("wb"), check=True)
        self.assertEqual(BgzfRecordCounter(self.path).advance(), 1000)


class BgzfCounterHoleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "out.vcf.gz"

    def tearDown(self):
        self.temp.cleanup()

    def test_shrink_resets_and_recounts(self):
        """A rerun to the same output path: VEP truncates the stale file —
        the counter must recount from scratch, not freeze on stale data."""
        counter = BgzfRecordCounter(self.path)
        self.path.write_bytes(
            bgzf_block(HEADER)
            + bgzf_block(b"".join(b"1\t%d\n" % i for i in range(500)))
        )
        self.assertEqual(counter.advance(), 500)
        self.path.write_bytes(bgzf_block(HEADER))  # truncation by new run
        self.assertEqual(counter.advance(), 0)
        with self.path.open("ab") as handle:
            handle.write(bgzf_block(b"1\t100\n1\t101\n"))
        self.assertEqual(counter.advance(), 2)

    def test_corrupt_bsize_degrades_instead_of_raising(self):
        """A BSIZE/XLEN making the payload length negative (or -1, which
        would read the rest of the file) must degrade to retry-next-poll."""
        block = bytearray(bgzf_block(HEADER))
        for lying_size in (5, 12 + 6 + 7):  # negative and exactly -1 cases
            block[16:18] = (lying_size - 1).to_bytes(2, "little")
            self.path.write_bytes(bytes(block) + b"\x00" * 4096)
            counter = BgzfRecordCounter(self.path)
            self.assertEqual(counter.advance(), 0)  # no exception, no count
            self.assertEqual(counter.offset, 0)

    def test_byte_budget_spreads_a_large_first_poll(self):
        counter = BgzfRecordCounter(self.path)
        counter.BYTE_BUDGET = 200  # a couple of blocks per poll
        blocks = bgzf_block(HEADER) + b"".join(
            bgzf_block(b"".join(b"1\t%d\n" % i for i in range(100)))
            for _ in range(20)
        )
        self.path.write_bytes(blocks)
        first = counter.advance()
        self.assertLess(first, 2000)  # capped by the budget
        total = first
        for _ in range(50):
            total = counter.advance()
            if total == 2000:
                break
        self.assertEqual(total, 2000)  # converges over subsequent polls


class TrackerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.log = Path(self.temp.name) / "job.log"
        self.output = Path(self.temp.name) / "out.vep.vcf.gz"
        self.job = {
            "id": "job1",
            "status": "running",
            "log_path": str(self.log),
            "output_path": str(self.output),
            "input_assembly": "GRCh38",
        }
        self.tracker = JobProgressTracker()

    def tearDown(self):
        self.temp.cleanup()

    def log_write(self, text):
        with self.log.open("a") as handle:
            handle.write(text)

    def test_stage_progression_denominator_and_percent(self):
        self.log_write("[10:00:00] preflight container checks passed\n")
        snap = self.tracker.snapshot(self.job)
        self.assertEqual(snap["stage"], "input")
        self.assertEqual(snap["stage_label"], "Detecting genome assembly")
        self.assertIsNone(snap["variants_total"])
        # GRCh38 input: no liftover stage in the checklist.
        self.assertNotIn("liftover", [s["id"] for s in snap["stages"]])

        self.log_write(
            "[10:00:10] input pre-filter (FILTER=PASS coding): 500 -> 200 variants\n"
            "[10:00:11] === VEP invocation ===\n"
        )
        self.output.write_bytes(
            bgzf_block(HEADER) + bgzf_block(b"".join(b"1\t%d\n" % i for i in range(50)))
        )
        snap = self.tracker.snapshot(self.job)
        self.assertEqual(snap["stage"], "vep")
        self.assertEqual(snap["variants_total"], 200)
        self.assertEqual(snap["variants_done"], 50)
        self.assertEqual(snap["vep_percent"], 25.0)
        self.assertFalse(snap["post_processing"])

        self.log_write(
            "[10:05:00] VEP finished -> /x/out.vep.vcf.gz\n"
            "[10:05:01] === LOFTEE frameshift PTC 50-bp recomputation ===\n"
        )
        snap = self.tracker.snapshot(self.job)
        self.assertEqual(snap["stage"], "ptc50")
        self.assertTrue(snap["post_processing"])
        vep_stage = next(s for s in snap["stages"] if s["id"] == "vep")
        self.assertEqual(vep_stage["state"], "done")

    def test_percent_caps_below_hundred_until_vep_finishes(self):
        self.log_write(
            "[10:00:10] input pre-filter (x): 500 -> 100 variants\n"
            "[10:00:11] === VEP invocation ===\n"
        )
        self.output.write_bytes(
            bgzf_block(HEADER)
            + bgzf_block(b"".join(b"1\t%d\n" % i for i in range(100)))
        )
        snap = self.tracker.snapshot(self.job)
        self.assertEqual(snap["vep_percent"], 99.0)

    def test_liftover_stage_appears_for_grch37_inputs(self):
        self.job["input_assembly"] = "auto"
        self.log_write("[10:00:00] input assembly: requested=auto, resolved=GRCh37\n")
        snap = self.tracker.snapshot(self.job)
        self.assertEqual(snap["resolved_assembly"], "GRCh37")
        self.assertEqual(snap["stage_label"], "Preparing GRCh37 input for liftover")
        self.assertIn("liftover", [s["id"] for s in snap["stages"]])

    def test_resolved_grch38_is_reported_without_liftover_stage(self):
        self.job["input_assembly"] = "auto"
        self.log_write("[10:00:00] input assembly: requested=auto, resolved=GRCh38\n")
        snap = self.tracker.snapshot(self.job)
        self.assertEqual(snap["resolved_assembly"], "GRCh38")
        self.assertEqual(snap["stage_label"], "Preparing GRCh38 input")
        self.assertNotIn("liftover", [s["id"] for s in snap["stages"]])

    def test_stale_output_from_a_previous_run_is_not_counted(self):
        """The previous run's completed output sits at the path until VEP
        truncates it; its old mtime keeps the counter unattached."""
        import os as _os
        self.output.write_bytes(
            bgzf_block(HEADER)
            + bgzf_block(b"".join(b"1\t%d\n" % i for i in range(400)))
        )
        stale = time.time() - 3600
        _os.utime(self.output, (stale, stale))
        self.log_write(
            "[10:00:10] input pre-filter (x): 500 -> 200 variants\n"
            "[10:00:11] === VEP invocation ===\n"
        )
        snap = self.tracker.snapshot(self.job)
        self.assertEqual(snap["stage"], "vep")
        self.assertIsNone(snap["variants_done"])  # stale file not counted
        # VEP truncates and starts writing: counting begins fresh.
        self.output.write_bytes(bgzf_block(HEADER) + bgzf_block(b"1\t1\n"))
        snap = self.tracker.snapshot(self.job)
        self.assertEqual(snap["variants_done"], 1)

    def test_prefilter_off_denominator_is_parsed(self):
        self.log_write(
            "[10:00:10] input pre-filter OFF: 12345 variants to annotate (every record in the input VCF).\n"
            "[10:00:11] === VEP invocation ===\n"
        )
        snap = self.tracker.snapshot(self.job)
        self.assertEqual(snap["variants_total"], 12345)

    def test_tracked_state_is_bounded(self):
        self.log_write("[10:00:00] preflight container checks passed\n")
        for index in range(40):
            self.tracker.snapshot({**self.job, "id": f"job{index}"})
        self.assertLessEqual(
            len(self.tracker._states), self.tracker.MAX_TRACKED
        )

    def test_non_running_jobs_report_none_and_release_state(self):
        self.log_write("[10:00:00] preflight container checks passed\n")
        self.tracker.snapshot(self.job)
        finished = {**self.job, "status": "succeeded"}
        self.assertIsNone(self.tracker.snapshot(finished))
        self.assertEqual(self.tracker._states, {})


if __name__ == "__main__":
    unittest.main()
