#!/usr/bin/env python3
"""Tests for phase-aware Haplosaurus frame-restoration post-processing."""

from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from pipeline.haplotype_consequences import (  # noqa: E402
    CONFIRMED,
    PARTIAL,
    POSSIBLE,
    annotate_vcf,
    classify_phase,
    parse_candidate_genotypes,
    restoring_events,
)


def state(gt: str, haplotypes, homo=False, phase_set=""):
    return {
        "gt": gt,
        "homozygous_alt": homo,
        "phased": "|" in gt,
        "haplotypes": haplotypes,
        "phase_set": phase_set,
    }


class PhaseClassificationTests(unittest.TestCase):
    def test_homozygous_pair_is_confirmed(self):
        self.assertEqual(
            classify_phase([
                state("1/1", {0, 1}, True),
                state("1/1", {0, 1}, True),
            ]),
            CONFIRMED,
        )

    def test_same_phase_heterozygous_pair_is_confirmed(self):
        self.assertEqual(
            classify_phase([
                state("0|1", {1}, phase_set="12"),
                state("0|1", {1}, phase_set="12"),
            ]),
            CONFIRMED,
        )

    def test_opposite_phase_heterozygous_pair_is_trans(self):
        self.assertIsNone(classify_phase([
            state("0|1", {1}, phase_set="12"),
            state("1|0", {0}, phase_set="12"),
        ]))

    def test_unphased_heterozygous_pair_is_possible(self):
        self.assertEqual(
            classify_phase([
                state("0/1", None),
                state("0/1", None),
            ]),
            POSSIBLE,
        )

    def test_homozygous_plus_unphased_heterozygous_is_partial(self):
        self.assertEqual(
            classify_phase([
                state("1/1", {0, 1}, True),
                state("0/1", None),
            ]),
            PARTIAL,
        )

    def test_incompatible_phase_sets_are_not_overcalled(self):
        self.assertEqual(
            classify_phase([
                state("0|1", {1}, phase_set="12"),
                state("0|1", {1}, phase_set="34"),
            ]),
            POSSIBLE,
        )


class IntegrationTests(unittest.TestCase):
    def test_restoring_haplotype_is_annotated_for_each_contributor(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            candidate = root / "candidate.vcf"
            haplo = root / "haplo.json"
            original = root / "annotated.vcf"
            output = root / "output.vcf"
            candidate.write_text(
                "##fileformat=VCFv4.2\n"
                "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\n"
                "1\t100\t1:100:A:AT\tA\tAT\t99\tPASS\t.\tGT:PS\t0|1:7\n"
                "1\t110\t1:110:AG:A\tAG\tA\t99\tPASS\t.\tGT:PS\t0|1:7\n",
                encoding="utf-8",
            )
            original.write_text(
                "##fileformat=VCFv4.2\n"
                "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\n"
                "1\t100\t.\tA\tAT\t99\tPASS\tCSQ=T|frameshift_variant\tGT:PS\t0|1:7\n"
                "1\t110\t.\tAG\tA\t99\tPASS\tCSQ=A|frameshift_variant\tGT:PS\t0|1:7\n",
                encoding="utf-8",
            )
            haplo.write_text(json.dumps({
                "transcript_id": "ENST1",
                "protein_haplotypes": [{
                    "name": "ENSP1:34AB>CD",
                    "contributing_variants": ["1:100:A:AT", "1:110:AG:A"],
                    "samples": {"S1": 1},
                    "has_indel": 1,
                    "flags": ["indel"],
                }],
            }) + "\n", encoding="utf-8")

            genotypes, _ = parse_candidate_genotypes(candidate)
            events, counts = restoring_events(haplo, genotypes)
            self.assertEqual(counts[CONFIRMED], 1)
            self.assertEqual(len(events["1:100:A:AT"]), 1)
            annotate_vcf(original, output, events)
            text = output.read_text(encoding="utf-8")
            self.assertIn("##INFO=<ID=IEI_HAPLOTYPE_FRAME", text)
            self.assertEqual(text.count("FRAME_RESTORED_CONFIRMED"), 3)

    def test_haplosaurus_frameshift_flag_is_not_restoring(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            candidate = root / "candidate.vcf"
            haplo = root / "haplo.json"
            candidate.write_text(
                "##fileformat=VCFv4.2\n"
                "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\n"
                "1\t100\tv1\tA\tAT\t99\tPASS\t.\tGT\t1/1\n"
                "1\t110\tv2\tAG\tA\t99\tPASS\t.\tGT\t1/1\n",
                encoding="utf-8",
            )
            haplo.write_text(json.dumps({
                "transcript_id": "ENST1",
                "protein_haplotypes": [{
                    "name": "ENSP1:frameshift",
                    "contributing_variants": ["v1", "v2"],
                    "samples": {"S1": 2},
                    "has_indel": 1,
                    "flags": ["indel", "frameshift"],
                }],
            }) + "\n", encoding="utf-8")
            genotypes, _ = parse_candidate_genotypes(candidate)
            events, counts = restoring_events(haplo, genotypes)
            self.assertFalse(events)
            self.assertEqual(counts["non_restoring_haplotypes"], 1)

    def test_haplosaurus_stop_change_is_not_restoring(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            candidate = root / "candidate.vcf"
            haplo = root / "haplo.json"
            candidate.write_text(
                "##fileformat=VCFv4.2\n"
                "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\n"
                "1\t100\tv1\tA\tAT\t99\tPASS\t.\tGT\t1/1\n"
                "1\t110\tv2\tAG\tA\t99\tPASS\t.\tGT\t1/1\n",
                encoding="utf-8",
            )
            haplo.write_text(json.dumps({
                "transcript_id": "ENST1",
                "protein_haplotypes": [{
                    "name": "ENSP1:stop_change",
                    "contributing_variants": ["v1", "v2"],
                    "samples": {"S1": 2},
                    "has_indel": 1,
                    "flags": ["indel", "stop_change"],
                }],
            }) + "\n", encoding="utf-8")
            genotypes, _ = parse_candidate_genotypes(candidate)
            events, counts = restoring_events(haplo, genotypes)
            self.assertFalse(events)
            self.assertEqual(counts["non_restoring_haplotypes"], 1)


if __name__ == "__main__":
    unittest.main()
