#!/usr/bin/env python3
import csv
import gzip
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))

from prepare_promoterai import SCORE_HEADER, TSS_HEADER, compact_scores, read_tss  # noqa: E402


class PreparePromoteraiTests(unittest.TestCase):
    def test_compacts_shared_tss_and_preserves_signed_scores(self):
        with tempfile.TemporaryDirectory() as temporary:
            tmp_path = Path(temporary)
            tss = tmp_path / "tss.tsv"
            with tss.open("w", newline="") as handle:
                writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
                writer.writerow(TSS_HEADER)
                writer.writerow(["chr1", 100, "1", "GENE1", "ENSG1", "ENST1.1", "protein_coding"])
                writer.writerow(["chr1", 100, "1", "GENE1", "ENSG1", "ENST2.1", "protein_coding"])
                writer.writerow(["chr1", 101, "1", "GENE1", "ENSG1", "ENST3.1", "protein_coding"])
            scores = tmp_path / "promoterAI_tss500.tsv.gz"
            with gzip.open(scores, "wt", newline="") as handle:
                writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
                writer.writerow(SCORE_HEADER)
                for alt, score in (("C", "-0.91"), ("G", "0.12"), ("T", "0.87")):
                    for transcript, tss_pos in (("ENST1.1", 100), ("ENST2.1", 100), ("ENST3.1", 101)):
                        writer.writerow(["1", 99, "A", alt, "GENE1", "ENSG1", transcript, "1", tss_pos, score])

            transcript_map = tmp_path / "map.tsv"
            representatives, transcript_rows, unique_tss = read_tss(tss, transcript_map)
            output = tmp_path / "scores.tsv"
            counts = compact_scores(scores, representatives, output, tmp_path / "work")

            self.assertEqual(transcript_rows, 3)
            self.assertEqual(unique_tss, 2)
            self.assertEqual(counts["source_rows"], 9)
            self.assertEqual(counts["shared_tss_rows_skipped"], 3)
            self.assertEqual(counts["compact_rows"], 2)
            self.assertEqual(
                output.read_text().splitlines(),
                [
                    "#chrom\tpos\tref\ttss_pos\tstrand\tscore_A\tscore_C\tscore_G\tscore_T",
                    "1\t99\tA\t100\t+\t.\t-0.91\t0.12\t0.87",
                    "1\t99\tA\t101\t+\t.\t-0.91\t0.12\t0.87",
                ],
            )
            self.assertEqual(len(transcript_map.read_text().splitlines()), 4)


if __name__ == "__main__":
    unittest.main()
