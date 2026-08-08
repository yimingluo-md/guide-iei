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
    minimal_variant_id,
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

    def test_phased_pair_without_phase_set_is_not_confirmed(self):
        # A "|" separator with no PS/PID on either record says nothing about
        # cross-record phase; asserting cis here is the over-call the module
        # exists to prevent.
        self.assertEqual(
            classify_phase([
                state("0|1", {1}),
                state("0|1", {1}),
            ]),
            POSSIBLE,
        )

    def test_one_missing_phase_set_is_still_possible(self):
        self.assertEqual(
            classify_phase([
                state("0|1", {1}, phase_set="12"),
                state("0|1", {1}),
            ]),
            POSSIBLE,
        )


class MultiAllelicGenotypeTests(unittest.TestCase):
    def _parse(self, gt_field, fmt="GT"):
        with tempfile.TemporaryDirectory() as directory:
            candidate = pathlib.Path(directory) / "candidate.vcf"
            candidate.write_text(
                "##fileformat=VCFv4.2\n"
                "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\n"
                f"1\t100\tv1\tA\tAT,ATT\t99\tPASS\t.\t{fmt}\t{gt_field}\n",
                encoding="utf-8",
            )
            genotypes, _ = parse_candidate_genotypes(candidate)
            return genotypes["v1"]["S1"]

    def test_two_different_alt_alleles_are_not_homozygous_alt(self):
        parsed = self._parse("1/2")
        self.assertFalse(parsed["homozygous_alt"])
        self.assertIsNone(parsed["haplotypes"])

    def test_phased_two_different_alt_alleles_have_unknown_placement(self):
        # 1|2 places *some* alt on each copy but the record key covers only one
        # of them, so the carrying haplotype is still unknown.
        parsed = self._parse("1|2:7", fmt="GT:PS")
        self.assertFalse(parsed["homozygous_alt"])
        self.assertIsNone(parsed["haplotypes"])

    def test_true_homozygous_alt_still_detected(self):
        parsed = self._parse("1/1")
        self.assertTrue(parsed["homozygous_alt"])
        self.assertEqual(parsed["haplotypes"], {0, 1})

    def test_one_two_pair_with_homozygous_partner_is_partial_not_confirmed(self):
        # Audit repro (CORE-2): GT 1/2 + GT 1/1 was previously reported
        # FRAME_RESTORED_CONFIRMED, hiding a potential biallelic LoF.
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            candidate = root / "candidate.vcf"
            haplo = root / "haplo.json"
            candidate.write_text(
                "##fileformat=VCFv4.2\n"
                "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\n"
                "1\t100\tv1\tA\tAT,ATT\t99\tPASS\t.\tGT\t1/2\n"
                "1\t200\tv2\tAG\tA\t99\tPASS\t.\tGT\t1/1\n",
                encoding="utf-8",
            )
            haplo.write_text(json.dumps({
                "transcript_id": "ENST1",
                "protein_haplotypes": [{
                    "name": "ENSP1:34AB>CD",
                    "contributing_variants": ["v1", "v2"],
                    "samples": {"S1": 1},
                    "has_indel": 1,
                    "flags": ["indel"],
                }],
            }) + "\n", encoding="utf-8")
            genotypes, _ = parse_candidate_genotypes(candidate)
            events, counts = restoring_events(haplo, genotypes)
            self.assertEqual(counts[CONFIRMED], 0)
            self.assertEqual(counts[PARTIAL], 1)


class KeyContractTests(unittest.TestCase):
    def test_minimal_variant_id_trims_suffix_then_prefix(self):
        self.assertEqual(minimal_variant_id("1", "100", "AT", "ATT"), "1:100:A:AT")
        self.assertEqual(minimal_variant_id("1", "100", "CAG", "CAA"), "1:102:G:A")
        self.assertEqual(minimal_variant_id("1", "100", "A", "AT"), "1:100:A:AT")

    def test_non_minimal_record_matches_normalised_event_key(self):
        # Audit repro (CORE-9): events are keyed on post-norm minimal alleles;
        # the annotated VCF may carry the pre-norm representation.
        events = {"1:100:A:AT": [{
            "sample": "S1", "transcript": "ENST1", "status": CONFIRMED,
            "partners": "1:110:AG:A", "protein": "ENSP1",
        }]}
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            original = root / "annotated.vcf"
            output = root / "output.vcf"
            original.write_text(
                "##fileformat=VCFv4.2\n"
                "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\n"
                "1\t100\t.\tAT\tATT\t99\tPASS\t.\tGT\t0/1\n",
                encoding="utf-8",
            )
            matched = annotate_vcf(original, output, events)
            self.assertEqual(matched, {"1:100:A:AT"})
            self.assertIn("IEI_HAPLOTYPE_FRAME=", output.read_text(encoding="utf-8"))

    def test_unmatched_event_keys_are_reported_not_silently_dropped(self):
        events = {"2:500:G:GA": [{
            "sample": "S1", "transcript": "ENST1", "status": CONFIRMED,
            "partners": "", "protein": "ENSP1",
        }]}
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            original = root / "annotated.vcf"
            output = root / "output.vcf"
            original.write_text(
                "##fileformat=VCFv4.2\n"
                "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\n"
                "1\t100\t.\tA\tAT\t99\tPASS\t.\tGT\t0/1\n",
                encoding="utf-8",
            )
            matched = annotate_vcf(original, output, events)
            self.assertEqual(matched, set())

    def test_no_id_multi_allelic_candidate_registers_per_alt_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            candidate = pathlib.Path(directory) / "candidate.vcf"
            candidate.write_text(
                "##fileformat=VCFv4.2\n"
                "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\n"
                "1\t100\t.\tA\tAT,ATT\t99\tPASS\t.\tGT\t0/1\n",
                encoding="utf-8",
            )
            genotypes, _ = parse_candidate_genotypes(candidate)
            self.assertIn("1:100:A:AT", genotypes)
            self.assertIn("1:100:A:ATT", genotypes)


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
