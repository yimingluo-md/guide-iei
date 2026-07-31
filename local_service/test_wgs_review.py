#!/usr/bin/env python3
import gzip
import os
import tempfile
import unittest
from pathlib import Path

from local_service.cohort_store import CohortStore, VcfHeader
from local_service.test_cohort_store import write_parallel_vcf
from local_service.wgs_review import (
    WgsPrefilterOptions,
    WgsReviewStore,
    bed_intervals,
    compact_review_transcripts,
    gene_intervals,
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
        self.assertEqual(options.min_promoterai_abs, 0.5)
        self.assertIsNone(options.min_cadd)
        with self.assertRaisesRegex(ValueError, "between 0 and 1"):
            WgsPrefilterOptions.from_payload({"max_gnomad_popmax": 2})

    def test_frequency_and_gene_filters_are_and_gates(self):
        options = WgsPrefilterOptions()
        self.assertFalse(record_passes(
            variant(af="0.01", splice="0.9"), HEADER, options, {}
        ))
        self.assertFalse(record_passes(
            variant(pos=500, splice="0.9"), HEADER, options, {"1": ((1, 200),)}
        ))
        self.assertTrue(record_passes(
            variant(pos=150, splice="0.9"), HEADER, options, {"1": ((1, 200),)}
        ))

    def test_prediction_thresholds_are_or_gates(self):
        options = WgsPrefilterOptions()
        self.assertFalse(record_passes(variant(), HEADER, options, {}))
        self.assertTrue(record_passes(
            variant(promoter="-0.7"), HEADER, options, {}
        ))
        with_cadd = WgsPrefilterOptions(min_cadd=20)
        self.assertTrue(record_passes(
            variant(cadd="25"), HEADER, with_cadd, {}
        ))

    def test_missing_annotations_are_retained(self):
        options = WgsPrefilterOptions()
        self.assertTrue(record_passes(
            variant(splice=".", promoter="0.1"), HEADER, options, {}
        ))
        unannotated = "1\t100\t.\tA\tG\t99\tPASS\t.\tGT\t0/1\n"
        self.assertTrue(record_passes(unannotated, HEADER, options, {}))
        self.assertFalse(record_passes(
            unannotated.replace("\tPASS\t", "\tLowQual\t"), HEADER, options, {}
        ))

    def test_exome_regions_are_retained_before_optional_wgs_filters(self):
        strict = WgsPrefilterOptions(
            max_gnomad_popmax=0.01,
            min_spliceai=0.9,
            min_promoterai_abs=0.9,
            genes=("OTHER",),
        )
        exome = {"1": ((90, 110),)}
        outside_gene = {"1": ((500, 600),)}
        common_without_evidence = variant(
            pos=100, af="0.5", splice="0.1", promoter="0.1"
        )
        self.assertTrue(record_passes(
            common_without_evidence, HEADER, strict, outside_gene, exome
        ))
        self.assertFalse(record_passes(
            variant(pos=200, af="0.5", splice="0.1", promoter="0.1"),
            HEADER, strict, outside_gene, exome,
        ))

    def test_bed_intervals_convert_coordinates_and_merge(self):
        with tempfile.TemporaryDirectory() as directory:
            bed = Path(directory) / "coding.bed.gz"
            with gzip.open(bed, "wt") as handle:
                handle.write("chr1\t99\t110\n1\t110\t120\nchr2\t0\t1\n")
            intervals = bed_intervals(bed)
        self.assertEqual(intervals, {"1": ((100, 120),), "2": ((1, 1),)})

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

    def test_gtf_gene_windows_and_missing_symbols(self):
        with tempfile.TemporaryDirectory() as directory:
            gtf = Path(directory) / "genes.gtf.gz"
            with gzip.open(gtf, "wt") as handle:
                handle.write(
                    '1\ttest\tgene\t100\t200\t.\t+\t.\tgene_id "ENSG1.2"; '
                    'gene_name "NFKB1";\n'
                )
            intervals, missing = gene_intervals(
                gtf, ("NFKB1", "MISSING"), 25
            )
        self.assertEqual(intervals, {"1": ((75, 225),)})
        self.assertEqual(missing, ("MISSING",))

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
                WgsPrefilterOptions(),
                root / "unused.gtf",
            )
            self.assertEqual(result["reader_count"], 4)
            self.assertEqual(result["records_scanned"], 4)
            self.assertEqual(result["records_retained"], 4)
            self.assertTrue(Path(result["path"]).is_file())
            self.assertTrue(Path(result["index_path"]).is_file())


if __name__ == "__main__":
    unittest.main()
