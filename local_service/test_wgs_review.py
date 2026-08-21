#!/usr/bin/env python3
import gzip
import os
import tempfile
import unittest
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from local_service.cohort_store import CohortStore, VcfHeader
from local_service.test_cohort_store import FakeHtsBackend, write_parallel_vcf
from local_service.wgs_review import (
    WgsPrefilterOptions,
    WgsReviewStore,
    add_unscored_indel_info,
    bed_intervals,
    compact_review_transcripts,
    evaluate_record,
    promoterai_intervals,
    record_passes,
)


FIELDS = (
    "Allele", "ALLELE_NUM", "SYMBOL", "MAX_AF",
    "SpliceAI_pred_DS_AG", "promoterAI_promoterAI", "CADD_phred",
)
HEADER = VcfHeader(("CASE",), FIELDS, ("1",))


def variant(
    *, pos=100, af="0.001", splice="0.1", promoter="0.2", cadd="10"
):
    csq = "|".join(("G", "1", "NFKB1", af, splice, promoter, cadd))
    return (
        f"1\t{pos}\t.\tA\tG\t99\tPASS\tCSQ={csq}"
        "\tGT\t0/1\n"
    )


class WgsPrefilterTests(unittest.TestCase):
    def test_defaults_and_payload_validation(self):
        options = WgsPrefilterOptions.from_payload({})
        self.assertEqual(options.max_gnomad_popmax, 0.01)
        self.assertEqual(options.min_spliceai, 0.5)
        self.assertEqual(options.min_promoterai_abs, 0.8)
        self.assertEqual(options.noncoding_mode, "ccre")
        with self.assertRaisesRegex(ValueError, "between 0 and 1"):
            WgsPrefilterOptions.from_payload({"max_gnomad_popmax": 2})
        with self.assertRaisesRegex(ValueError, "ccre, all, or none"):
            WgsPrefilterOptions.from_payload({"noncoding_mode": "enhancer"})

    def test_population_frequency_is_a_global_gate(self):
        options = WgsPrefilterOptions()
        self.assertFalse(record_passes(
            variant(af="0.011", splice="0.9"), HEADER, options, {}
        ))
        self.assertTrue(record_passes(
            variant(af="0.01", splice="0.9"), HEADER, options, {}
        ))

    def test_candidate_routes_are_or_gates(self):
        options = WgsPrefilterOptions()
        self.assertFalse(record_passes(variant(), HEADER, options, {}))
        self.assertTrue(record_passes(
            variant(promoter="-0.9"), HEADER, options, {}
        ))
        self.assertTrue(record_passes(
            variant(pos=150), HEADER, options, {"1": ((100, 200),)}
        ))

    def test_missing_frequency_is_retained_but_missing_evidence_does_not_qualify(self):
        options = WgsPrefilterOptions()
        self.assertFalse(record_passes(
            variant(splice=".", promoter="0.1"), HEADER, options, {}
        ))
        unannotated = "1\t100\t.\tA\tG\t99\tPASS\t.\tGT\t0/1\n"
        self.assertFalse(record_passes(unannotated, HEADER, options, {}))
        self.assertTrue(record_passes(
            unannotated, HEADER, options, {"1": ((90, 110),)}
        ))
        self.assertFalse(record_passes(
            unannotated.replace("\tPASS\t", "\tLowQual\t"), HEADER, options, {}
        ))
        # "." = site filtering not applied upstream — eligible like PASS;
        # only explicit failure labels exclude (never-hard-filtered cohort
        # VCFs must not be silently emptied).
        self.assertTrue(record_passes(
            unannotated.replace("\tPASS\t", "\t.\t"), HEADER, options,
            {"1": ((90, 110),)}
        ))
        self.assertFalse(record_passes(
            unannotated.replace("\tPASS\t", "\tMONOALLELIC\t"), HEADER, options,
            {"1": ((90, 110),)}
        ))

    def test_exome_is_a_route_after_population_frequency(self):
        strict = WgsPrefilterOptions(
            max_gnomad_popmax=0.01,
            min_spliceai=0.9,
            min_promoterai_abs=0.9,
            noncoding_mode="none",
        )
        exome = {"1": ((90, 110),)}
        common_without_evidence = variant(
            pos=100, af="0.5", splice="0.1", promoter="0.1"
        )
        self.assertFalse(record_passes(
            common_without_evidence, HEADER, strict, {}, exome
        ))
        self.assertTrue(record_passes(
            variant(pos=100, af="0.001", splice="0.1", promoter="0.1"),
            HEADER, strict, {}, exome,
        ))

    def test_noncoding_modes_are_mutually_exclusive_region_routes(self):
        all_noncoding = WgsPrefilterOptions(noncoding_mode="all")
        no_noncoding = WgsPrefilterOptions(noncoding_mode="none")
        quiet = variant(pos=300, splice="0.1", promoter="0.1")
        exome = {"1": ((90, 110),)}
        self.assertTrue(record_passes(quiet, HEADER, all_noncoding, {}, exome))
        self.assertFalse(record_passes(quiet, HEADER, no_noncoding, {}, exome))

    def test_per_allele_info_frequency_is_not_pooled_across_alts(self):
        # Audit repro (SVC-7): a Number=A INFO AF of "0.30,0.00001" previously
        # excluded BOTH alleles via max(); the rare candidate allele must
        # survive the frequency gate on its own token.
        header = VcfHeader(("CASE",), FIELDS, ("1",), ("MAX_AF",))
        record = "1\t100\t.\tA\tG,T\t99\tPASS\tMAX_AF=0.30,0.00001\tGT\t1/2\n"
        options = WgsPrefilterOptions(noncoding_mode="ccre")
        self.assertTrue(record_passes(
            record, header, options, {"1": ((90, 110),)}
        ))
        common_both = "1\t100\t.\tA\tG,T\t99\tPASS\tMAX_AF=0.30,0.20\tGT\t1/2\n"
        self.assertFalse(record_passes(
            common_both, header, options, {"1": ((90, 110),)}
        ))

    def test_multi_allelic_match_failure_does_not_exclude_by_pooled_af(self):
        # Audit repro (SVC-8): with no ALLELE_NUM and a minimised Allele that
        # matches neither ALT, one allele's CSQ values must not decide the
        # other allele's fate. Unattributable AF must not exclude; the rare
        # allele retains the record through its region route.
        csq_common = "|".join(("-", "", "NFKB1", "0.30", ".", ".", "10"))
        csq_rare = "|".join(("-", "", "NFKB1", "0.00001", ".", ".", "10"))
        record = (
            "1\t100\t.\tCTT\tC,CT\t99\tPASS\t"
            f"CSQ={csq_common},{csq_rare}\tGT\t1/2\n"
        )
        options = WgsPrefilterOptions(noncoding_mode="ccre")
        self.assertTrue(record_passes(
            record, HEADER, options, {"1": ((90, 110),)}
        ))

    def test_multi_allelic_match_failure_still_qualifies_for_retention(self):
        # Retention is record-granular: an unattributable high SpliceAI score
        # must still keep the record (the safe direction), even though it can
        # no longer exclude anything.
        csq_quiet = "|".join(("-", "", "NFKB1", "0.001", "0.1", ".", "10"))
        csq_splice = "|".join(("-", "", "NFKB1", "0.001", "0.9", ".", "10"))
        record = (
            "1\t100\t.\tCTT\tC,CT\t99\tPASS\t"
            f"CSQ={csq_quiet},{csq_splice}\tGT\t1/2\n"
        )
        options = WgsPrefilterOptions(noncoding_mode="none")
        self.assertTrue(record_passes(record, HEADER, options, {}))

    def test_single_alt_match_failure_still_uses_all_consequences(self):
        # A single-ALT record's consequences describe that ALT even when the
        # minimised Allele string does not equal the raw ALT.
        csq = "|".join(("-", "", "NFKB1", "0.011", ".", ".", "10"))
        record = f"1\t100\t.\tCT\tC\t99\tPASS\tCSQ={csq}\tGT\t0/1\n"
        options = WgsPrefilterOptions(noncoding_mode="ccre")
        self.assertFalse(record_passes(
            record, HEADER, options, {"1": ((90, 110),)}
        ))

    def test_region_overlap_uses_the_full_small_variant_span(self):
        deletion = variant(pos=95, splice="0.1", promoter="0.1").replace(
            "\tA\tG\t", "\tAAAAAA\tA\t"
        )
        self.assertTrue(record_passes(
            deletion, HEADER, WgsPrefilterOptions(), {"1": ((100, 110),)}
        ))

    def test_unscored_intronic_indel_is_retained_and_flagged(self):
        fields = (
            "Allele", "ALLELE_NUM", "SYMBOL", "Consequence", "MAX_AF",
            "SpliceAI_pred_DS_AG", "promoterAI_promoterAI",
        )
        header = VcfHeader(("CASE",), fields, ("1",))
        csq = "A|1|GENE1|intron_variant|0.001|.|."
        deletion = f"1\t100\t.\tAT\tA\t99\tPASS\tCSQ={csq}\tGT\t0/1\n"
        retain, reasons = evaluate_record(
            deletion,
            header,
            WgsPrefilterOptions(noncoding_mode="none"),
            {},
        )
        self.assertTrue(retain)
        self.assertEqual(reasons, (("SpliceAI_intronic",),))
        flagged = add_unscored_indel_info(deletion, reasons)
        self.assertIn("IEI_UNSCORED_INDEL=SpliceAI_intronic", flagged)

    def test_unscored_promoter_indel_is_retained_but_snv_is_not(self):
        options = WgsPrefilterOptions(noncoding_mode="none")
        promoter = {"1": ((90, 110),)}
        deletion = variant(
            pos=100, splice=".", promoter="."
        ).replace("\tA\tG\t", "\tAT\tA\t")
        retain, reasons = evaluate_record(
            deletion, HEADER, options, {}, {}, promoter
        )
        self.assertTrue(retain)
        self.assertEqual(reasons, (("PromoterAI_promoter",),))
        self.assertFalse(record_passes(
            variant(pos=100, splice=".", promoter="."),
            HEADER, options, {}, {}, promoter,
        ))

    def test_absent_predictor_dataset_does_not_enable_missing_indel_route(self):
        header = VcfHeader(
            ("CASE",),
            ("Allele", "ALLELE_NUM", "SYMBOL", "Consequence", "MAX_AF"),
            ("1",),
        )
        csq = "A|1|GENE1|intron_variant|0.001"
        deletion = f"1\t100\t.\tAT\tA\t99\tPASS\tCSQ={csq}\tGT\t0/1\n"
        self.assertFalse(record_passes(
            deletion,
            header,
            WgsPrefilterOptions(noncoding_mode="none"),
            {},
            {},
            {"1": ((90, 110),)},
        ))

    def test_scored_intronic_indel_does_not_get_missing_score_flag(self):
        fields = (
            "Allele", "ALLELE_NUM", "SYMBOL", "Consequence", "MAX_AF",
            "SpliceAI_pred_DS_AG", "promoterAI_promoterAI",
        )
        header = VcfHeader(("CASE",), fields, ("1",))
        csq = "A|1|GENE1|intron_variant|0.001|0.1|."
        deletion = f"1\t100\t.\tAT\tA\t99\tPASS\tCSQ={csq}\tGT\t0/1\n"
        retain, reasons = evaluate_record(
            deletion, header, WgsPrefilterOptions(noncoding_mode="none"), {}
        )
        self.assertFalse(retain)
        self.assertEqual(reasons, ((),))

    def test_bed_intervals_convert_coordinates_and_merge(self):
        with tempfile.TemporaryDirectory() as directory:
            bed = Path(directory) / "coding.bed.gz"
            with gzip.open(bed, "wt") as handle:
                handle.write("chr1\t99\t110\n1\t110\t120\nchr2\t0\t1\n")
            intervals = bed_intervals(bed)
        self.assertEqual(intervals, {"1": ((100, 120),), "2": ((1, 1),)})

    def test_promoterai_transcript_map_becomes_tss_500bp_intervals(self):
        with tempfile.TemporaryDirectory() as directory:
            transcript_map = Path(directory) / "promoterai_transcripts.tsv"
            transcript_map.write_text(
                "transcript_id\tchrom\ttss_pos\n"
                "ENST1\tchr1\t1000\nENST2\t1\t1500\nENST3\t2\t100\n",
                encoding="utf-8",
            )
            intervals = promoterai_intervals(transcript_map)
        self.assertEqual(intervals["1"], ((500, 2000),))
        self.assertEqual(intervals["2"], ((1, 600),))

    def test_review_output_compacts_transcripts_without_dropping_genes(self):
        fields = (
            "Allele", "ALLELE_NUM", "SYMBOL", "Feature",
            "MANE_SELECT", "PICK",
        )
        header = VcfHeader(("CASE",), fields, ("1",))
        entries = [
            "G|1|GENE1|ENST_ALT||",
            "G|1|GENE1|ENST_MANE|NM_1|",
            "G|1|GENE2|ENST_PICK||1",
            "G|1|GENE2|ENST_ALT||",
            "G|1|GENE3|ENST_FALLBACK||",
            "G|1|GENE3|ENST_REDUNDANT||",
        ]
        line = (
            "1\t100\t.\tA\tG\t99\tPASS\tCSQ=" + ",".join(entries)
            + "\tGT\t0/1\n"
        )
        compacted, scanned, retained = compact_review_transcripts(line, header)
        self.assertEqual(scanned, 6)
        self.assertEqual(retained, 3)
        self.assertIn("ENST_MANE", compacted)
        self.assertIn("ENST_PICK", compacted)
        self.assertIn("ENST_FALLBACK", compacted)
        self.assertNotIn("ENST_REDUNDANT", compacted)

    def test_prefilter_output_persists_allele_flag_and_count(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "intronic-indel.vcf"
            fields = (
                "Allele", "ALLELE_NUM", "SYMBOL", "Consequence", "MAX_AF",
                "SpliceAI_pred_DS_AG", "promoterAI_promoterAI", "PICK",
            )
            csq = "A|1|GENE1|intron_variant|0.001|.|.|1"
            source.write_text(
                "##fileformat=VCFv4.2\n"
                "##contig=<ID=1,length=248956422>\n"
                f'##INFO=<ID=CSQ,Number=.,Type=String,Description="Format: {"|".join(fields)}">\n'
                "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tCASE\n"
                f"1\t100\t.\tAT\tA\t99\tPASS\tCSQ={csq}\tGT\t0/1\n",
                encoding="utf-8",
            )
            cohort = CohortStore(
                root / "cohort.sqlite3",
                enable_auto_index=True,
                hts_backend=FakeHtsBackend(),
            )
            result = WgsReviewStore(root, cohort).prefilter(
                source,
                WgsPrefilterOptions(noncoding_mode="none"),
            )
            with gzip.open(result["path"], "rt", encoding="utf-8") as handle:
                output = handle.read()
        self.assertEqual(result["records_retained"], 1)
        self.assertEqual(result["unscored_intronic_indels"], 1)
        self.assertEqual(result["unscored_promoter_indels"], 0)
        self.assertIn("##INFO=<ID=IEI_UNSCORED_INDEL,Number=A", output)
        self.assertIn("IEI_UNSCORED_INDEL=SpliceAI_intronic", output)

    def test_worker_interval_slices_normalize_chr_prefixed_contigs(self):
        # Regression: interval dicts are keyed by NORMALIZED contig ("1"), but
        # the per-worker slice looked contigs up by the VCF's raw name
        # ("chr1"), handing every worker empty interval sets — the exome and
        # cCRE retention routes silently retained nothing for chr-prefixed
        # VCFs while the import completed "successfully".
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "coding.vcf"
            fields = (
                "Allele", "ALLELE_NUM", "SYMBOL", "Consequence", "MAX_AF",
                "SpliceAI_pred_DS_AG", "promoterAI_promoterAI", "PICK",
            )
            csq = "A|1|GENE1|missense_variant|0.001|.|.|1"
            source.write_text(
                "##fileformat=VCFv4.2\n"
                "##contig=<ID=chr1,length=248956422>\n"
                f'##INFO=<ID=CSQ,Number=.,Type=String,Description="Format: {"|".join(fields)}">\n'
                "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tCASE\n"
                f"chr1\t100\t.\tG\tA\t99\tPASS\tCSQ={csq}\tGT\t0/1\n",
                encoding="utf-8",
            )
            exome_bed = root / "coding.bed"
            exome_bed.write_text("1\t50\t200\n", encoding="utf-8")  # Ensembl naming
            cohort = CohortStore(
                root / "cohort.sqlite3",
                enable_auto_index=True,
                hts_backend=FakeHtsBackend(),
            )
            result = WgsReviewStore(root, cohort).prefilter(
                source,
                WgsPrefilterOptions(noncoding_mode="none"),
                exome_bed_path=exome_bed,
            )
        self.assertEqual(result["records_retained"], 1)
        self.assertEqual(result["unscored_intronic_indels"], 0)

    @unittest.skipUnless(
        os.environ.get("IEI_RUN_HTS_INTEGRATION") == "1",
        "set IEI_RUN_HTS_INTEGRATION=1 to exercise real WGS BGZF/tabix intake",
    )
    def test_real_four_reader_indexed_prefilter(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "wgs.vcf"
            write_parallel_vcf(source)
            cohort = CohortStore(
                root / "cohort.sqlite3",
                enable_auto_index=True,
                index_readers=4,
            )
            result = WgsReviewStore(root, cohort).prefilter(
                source,
                WgsPrefilterOptions(noncoding_mode="all"),
                promoter_map_path=None,
            )
            self.assertEqual(result["reader_count"], 4)
            self.assertEqual(result["records_scanned"], 4)
            self.assertEqual(result["records_retained"], 4)
            self.assertTrue(Path(result["path"]).is_file())
            self.assertTrue(Path(result["index_path"]).is_file())


if __name__ == "__main__":
    unittest.main()
