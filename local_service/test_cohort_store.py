#!/usr/bin/env python3
import gzip
import os
import re
import tempfile
import time
import unittest
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from local_service.cohort_store import CohortStore, annotation_from, parse_genotype


CSQ_FIELDS = [
    "Allele", "ALLELE_NUM", "Consequence", "IMPACT", "SYMBOL", "Gene",
    "Feature", "HGVSc", "HGVSp", "MANE_SELECT", "PICK", "MAX_AF", "CADD_phred",
    "AlphaMissense_score", "SpliceAI_pred_DS_AG", "ClinVar_CLNSIG", "LoF",
    "RepeatMasker", "SegDup", "LoF_50_BP_RULE_PTC",
    "LoF_50_BP_RULE_original", "LoF_50_BP_RULE_changed",
    "PTC_dist_from_last_exon", "PTC_calc_status",
    "LoGoFunc_prediction", "LoGoFunc_neutral", "LoGoFunc_GOF",
    "LoGoFunc_LOF", "LoGoFunc_allele_available",
    "LoGoFunc_source_transcript", "LoGoFunc_source_HGVSp", "LoGoFunc_match",
]


def csq(
    allele, number, consequence, impact, gene, hgvsc, hgvsp,
    popmax, cadd, alpha, splice, clinvar="", lof="", repeat="", segdup="",
    mane="MANE", picked="1", transcript=None,
    loftee_50bp="", loftee_50bp_original="", loftee_50bp_changed="",
    ptc_distance="", ptc_status="",
    logofunc_prediction="", logofunc_neutral="", logofunc_gof="",
    logofunc_lof="", logofunc_allele_available="",
    logofunc_source_transcript="", logofunc_source_hgvsp="",
    logofunc_match="",
):
    values = [
        allele, str(number), consequence, impact, gene, f"ENSG_{gene}",
        transcript or f"ENST_{gene}", hgvsc, hgvsp, mane, picked,
        str(popmax), str(cadd),
        str(alpha), str(splice), clinvar, lof, repeat, segdup,
        loftee_50bp, loftee_50bp_original, loftee_50bp_changed,
        str(ptc_distance), ptc_status,
        logofunc_prediction, str(logofunc_neutral), str(logofunc_gof),
        str(logofunc_lof), str(logofunc_allele_available),
        logofunc_source_transcript, logofunc_source_hgvsp, logofunc_match,
    ]
    return "|".join(values)


def write_vcf(path: Path):
    records = [
        (
            "1\t100\trsExact\tA\tG\t99\tPASS\tCSQ="
            + csq(
                "G", 1, "stop_gained", "HIGH", "NFKB1", "c.100A>G", "p.Lys34Ter",
                0.00001, 38, ".", 0.01, "Pathogenic", "HC",
                loftee_50bp="FAIL", loftee_50bp_original="PASS",
                loftee_50bp_changed="1", ptc_distance=42, ptc_status="ok",
            )
            + ";IEI_UNSCORED_INDEL=SpliceAI_intronic"
            + "\tGT:AD:DP:GQ\t0/1:12,10:22:80\t0/0:20,0:20:70"
        ),
        (
            "1\t200\t.\tC\tT\t80\tPASS\tCSQ="
            + csq(
                "T", 1, "missense_variant", "MODERATE", "NFKB1", "c.200C>T",
                "p.Ala67Val", 0.0001, 26, 0.91, 0.02, "Uncertain_significance",
            )
            + "\tGT:AD:DP:GQ\t0/0:25,0:25:90\t1/1:0,18:18:75"
        ),
        (
            "1\t300\t.\tG\tA,C\t70\tPASS\tCSQ="
            + csq(
                "A", 1, "missense_variant", "MODERATE", "IL10RA", "c.300G>A",
                "p.Trp100Ter", 0.0002, 24, 0.8, 0.03,
            )
            + ","
            + csq(
                "C", 2, "splice_donor_variant", "HIGH", "NFKB1", "c.300+1G>C",
                "", 0, 32, ".", 0.95,
            )
            + "\tGT:AD:DP:GQ\t0/2:8,0,9:17:60\t0/1:7,8,0:15:55"
        ),
        (
            "1\t400\t.\tT\tC\t60\tPASS\tCSQ="
            + csq(
                "C", 1, "stop_gained", "HIGH", "NFKB1", "c.400T>C",
                "p.Tyr134Ter", 0, 35, ".", 0.1, repeat="LINE",
            )
            + "\tGT:AD:DP:GQ\t0/1:9,8:17:50\t0/0:18,0:18:65"
        ),
        (
            "1\t500\t.\tA\tT\t50\tLowQual\tCSQ="
            + csq(
                "T", 1, "stop_gained", "HIGH", "NFKB1", "c.500A>T",
                "p.Lys167Ter", 0, 36, ".", 0.1,
            )
            + "\tGT:AD:DP:GQ\t0/1:5,5:10:20\t0/0:10,0:10:30"
        ),
    ]
    text = (
        "##fileformat=VCFv4.2\n"
        "##contig=<ID=1,length=248956422>\n"
        '##INFO=<ID=IEI_UNSCORED_INDEL,Number=.,Type=String,Description="Unscored indel routes">\n'
        '##FILTER=<ID=LowQual,Description="Low quality">\n'
        '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n'
        '##FORMAT=<ID=AD,Number=R,Type=Integer,Description="Allelic depths">\n'
        '##FORMAT=<ID=DP,Number=1,Type=Integer,Description="Read depth">\n'
        '##FORMAT=<ID=GQ,Number=1,Type=Integer,Description="Genotype quality">\n'
        '##INFO=<ID=CSQ,Number=.,Type=String,Description="Consequence annotations. Format: '
        + "|".join(CSQ_FIELDS)
        + '">\n'
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tP1\tP2\n"
        + "\n".join(records)
        + "\n"
    )
    if path.name.endswith(".gz"):
        with gzip.open(path, "wt") as handle:
            handle.write(text)
    else:
        path.write_text(text)


class FakeHtsBackend:
    """Small deterministic stand-in for bcftools/tabix integration tests."""

    def __init__(self):
        self.sort_calls = 0

    def validate_index(self, path: Path):
        index = Path(f"{path}.tbi")
        return index if path.is_file() and index.is_file() else None

    def sort_bgzip(self, source: Path, output: Path):
        self.sort_calls += 1
        opener = gzip.open if source.name.endswith(".gz") else open
        with opener(source, "rt", encoding="utf-8") as source_handle:
            lines = source_handle.readlines()
        headers = [line for line in lines if line.startswith("#")]
        records = sorted(
            (line for line in lines if line and not line.startswith("#")),
            key=lambda line: (line.split("\t", 2)[0], int(line.split("\t", 2)[1])),
        )
        with gzip.open(output, "wt", encoding="utf-8") as output_handle:
            output_handle.writelines([*headers, *records])

    def create_index(self, path: Path):
        index = Path(f"{path}.tbi")
        index.write_text("fake tabix index")
        return index

    def list_contigs(self, path: Path):
        opener = gzip.open if path.name.endswith(".gz") else open
        contigs = []
        with opener(path, "rt", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("##contig=<"):
                    contigs.append(line.split("ID=", 1)[1].split(",", 1)[0].rstrip(">\n"))
        return contigs

    def iter_records(self, path: Path, contigs):
        selected_contigs = set()
        selected_regions = []
        for value in contigs:
            match = re.fullmatch(r"([^:]+):(\d+)-(\d+)", value)
            if match:
                selected_regions.append(
                    (match.group(1), int(match.group(2)), int(match.group(3)))
                )
            else:
                selected_contigs.add(value)
        opener = gzip.open if path.name.endswith(".gz") else open
        with opener(path, "rt", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("#"):
                    continue
                columns = line.split("\t", 2)
                chrom, pos = columns[0], int(columns[1])
                if chrom in selected_contigs or any(
                    chrom == region_chrom and start <= pos <= end
                    for region_chrom, start, end in selected_regions
                ):
                    yield line


class UnavailableHtsBackend(FakeHtsBackend):
    def sort_bgzip(self, source: Path, output: Path):
        raise RuntimeError("container runtime is unavailable")


def write_parallel_vcf(path: Path):
    consequence = csq(
        "G", 1, "missense_variant", "MODERATE", "NFKB1",
        "c.100A>G", "p.Lys34Arg", 0.0001, 25, 0.8, 0.01,
    )
    path.write_text(
        "##fileformat=VCFv4.2\n"
        + "".join(
            f"##contig=<ID={chrom},length={248956422 if chrom == 1 else 1000000}>\n"
            for chrom in range(1, 5)
        )
        + '##INFO=<ID=CSQ,Number=.,Type=String,Description="Format: '
        + "|".join(CSQ_FIELDS)
        + '">\n'
        + '##INFO=<ID=promoterAI,Number=1,Type=Float,Description="promoterAI score">\n'
        + '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n'
        + '##FORMAT=<ID=AD,Number=R,Type=Integer,Description="Allelic depths">\n'
        + '##FORMAT=<ID=DP,Number=1,Type=Integer,Description="Depth">\n'
        + '##FORMAT=<ID=GQ,Number=1,Type=Integer,Description="Genotype quality">\n'
        + "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tP1\n"
        + "".join(
            f"{chrom}\t100\t.\tA\tG\t99\tPASS\tIEI_UNSCORED_INDEL=PromoterAI_promoter;promoterAI=-0.75;CSQ={consequence}"
            "\tGT:AD:DP:GQ\t0/1:10,10:20:99\n"
            for chrom in range(1, 5)
        )
    )


class CohortStoreTests(unittest.TestCase):
    def test_wgs_cadd_value_precedes_dbnsfp_duplicate(self):
        annotation = annotation_from({
            "CADD_PHRED": "21.5",
            "CADD_phred": "35.0",
        })
        self.assertEqual(annotation["cadd"], 21.5)
        self.assertEqual(annotation_from({"CADD_phred": "35.0"})["cadd"], 35.0)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = CohortStore(self.root / "cohort.sqlite3")
        self.vcf = self.root / "cohort.vep.vcf.gz"
        write_vcf(self.vcf)

    def tearDown(self):
        self.temp.cleanup()

    def test_imports_pass_carriers_and_skips_unchanged_file(self):
        result = self.store.import_paths([str(self.vcf)])
        self.assertEqual(result["imported"], 1)
        imported = result["files"][0]
        self.assertEqual(imported["sample_count"], 2)
        self.assertEqual(imported["pass_records"], 4)
        self.assertEqual(imported["excluded_records"], 1)
        self.assertEqual(imported["variant_count"], 5)
        self.assertEqual(imported["carrier_count"], 5)
        self.assertEqual(result["stats"]["individuals"], 2)

        repeated = self.store.import_paths([str(self.vcf)])
        self.assertEqual(repeated["skipped"], 1)
        self.assertEqual(repeated["stats"]["carrier_observations"], 5)

    def test_full_index_preserves_whole_genome_scope_separately(self):
        imported = self.store.import_vcf(
            self.vcf, import_profile="full", analysis_scope="whole_genome"
        )
        self.assertEqual(imported["import_profile"], "full")
        self.assertEqual(imported["analysis_scope"], "whole_genome")
        row = self.store.query({"mode": "variant", "query": "rsExact"})["rows"][0]
        self.assertEqual(row["import_profile"], "full")
        self.assertEqual(row["analysis_scope"], "whole_genome")
        review = self.store.sample_review_files([row["sample_entry_id"]])
        self.assertEqual(review["analysis_scope"], "whole_genome")
        self.assertEqual(review["files"][0]["analysis_scope"], "whole_genome")

    def test_background_import_reports_file_byte_and_record_progress(self):
        job = self.store.start_import_paths([str(self.vcf)])
        for _ in range(200):
            job = self.store.get_import_job(job["id"])
            if job["status"] in {"succeeded", "failed"}:
                break
            time.sleep(0.01)
        self.assertEqual(job["status"], "succeeded")
        self.assertEqual(job["completed_files"], 1)
        self.assertEqual(job["total_files"], 1)
        self.assertEqual(job["processed_bytes"], job["total_bytes"])
        self.assertEqual(job["records_processed"], 5)
        self.assertEqual(job["pass_records"], 4)
        self.assertEqual(job["carrier_count"], 5)
        self.assertEqual(job["result"]["imported"], 1)

    def test_lists_and_removes_exact_sample_entries_then_reimports_source(self):
        self.store.import_paths([str(self.vcf)])
        samples = self.store.list_samples()
        self.assertEqual([sample["name"] for sample in samples], ["P1", "P2"])
        self.assertTrue(all(
            sample["source_path"] == str(self.vcf.resolve()) for sample in samples
        ))

        p1 = next(sample for sample in samples if sample["name"] == "P1")
        removed = self.store.remove_samples([p1["id"]])
        self.assertEqual(removed["removed_count"], 1)
        self.assertEqual(removed["stats"]["individuals"], 1)
        self.assertEqual(
            self.store.query({"mode": "variant", "query": "rsExact"})["total"],
            0,
        )
        self.assertEqual([sample["name"] for sample in self.store.list_samples()], ["P2"])

        restored = self.store.import_paths([str(self.vcf)])
        self.assertEqual(restored["imported"], 1)
        self.assertEqual(restored["stats"]["individuals"], 2)

    def test_removing_entire_cohort_uses_reset_and_preserves_phenotypes(self):
        self.store.import_paths([str(self.vcf)])
        with self.store._session() as connection:
            connection.execute(
                """
                CREATE TABLE phenotype_individuals(
                    individual_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                INSERT INTO phenotype_individuals(
                    individual_id, created_at, updated_at
                ) VALUES ('participant-1', '2026-08-02T00:00:00Z',
                          '2026-08-02T00:00:00Z')
                """
            )
        samples = self.store.list_samples()
        removed = self.store.remove_samples([row["id"] for row in samples])
        self.assertEqual(removed["removed_count"], 2)
        self.assertEqual(removed["orphan_variants_removed"], 5)
        self.assertEqual(removed["stats"]["files"], 0)
        self.assertEqual(removed["stats"]["carrier_observations"], 0)
        with self.store._session() as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM phenotype_individuals"
                ).fetchone()[0],
                1,
            )
            cohort_tables = {
                row[0] for row in connection.execute(
                    """
                    SELECT name FROM sqlite_master
                    WHERE type='table' AND name LIKE 'cohort_%'
                    """
                )
            }
        self.assertEqual(cohort_tables, {
            "cohort_files", "cohort_samples", "cohort_variants",
            "cohort_annotations", "cohort_genotypes",
        })
        restored = self.store.import_paths([str(self.vcf)])
        self.assertEqual(restored["imported"], 1)
        self.assertEqual(restored["stats"]["individuals"], 2)

    def test_prefiltered_job_records_profile_provenance_and_progress(self):
        filters = {
            "max_gnomad_popmax": 0.01,
            "min_spliceai": 0.5,
            "min_promoterai_abs": 0.5,
            "noncoding_mode": "ccre",
        }

        def prefilter(source, progress):
            progress({
                "phase": "filtering", "progress": 50,
                "records_scanned": 5, "records_retained": 4,
                "reader_count": 4,
            })
            return source, {"records_scanned": 5, "records_retained": 4}

        job = self.store.start_import_paths(
            [str(self.vcf)], import_profile="prefiltered",
            prefilter_options=filters, prefilter=prefilter,
        )
        for _ in range(200):
            job = self.store.get_import_job(job["id"])
            if job["status"] in {"succeeded", "failed"}:
                break
            time.sleep(0.01)
        self.assertEqual(job["status"], "succeeded")
        self.assertEqual(job["prefilter_records_scanned"], 5)
        self.assertEqual(job["prefilter_records_retained"], 4)
        self.assertEqual(job["processed_bytes"], job["total_bytes"])
        self.assertEqual(job["result"]["stats"]["prefiltered_files"], 1)
        row = self.store.query({"mode": "variant", "query": "rsExact"})["rows"][0]
        self.assertEqual(row["import_profile"], "prefiltered")
        self.assertEqual(row["analysis_scope"], "whole_genome")
        self.assertIsInstance(row["sample_entry_id"], int)
        self.assertEqual(row["source_path"], str(self.vcf.resolve()))

    def test_exact_variant_query_is_allele_specific_for_multiallelic_records(self):
        self.store.import_paths([str(self.vcf)])
        result = self.store.query({"mode": "variant", "query": "chr1:300:G:C"})
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["rows"][0]["sample"], "P1")
        self.assertEqual(result["rows"][0]["gene"], "NFKB1")

        other_alt = self.store.query({"mode": "variant", "query": "1-300-G-A"})
        self.assertEqual(other_alt["total"], 1)
        self.assertEqual(other_alt["rows"][0]["sample"], "P2")
        self.assertEqual(other_alt["rows"][0]["gene"], "IL10RA")

    def test_exact_rsid_returns_carriers_without_qualification_filters(self):
        self.store.import_paths([str(self.vcf)])
        result = self.store.query({"mode": "variant", "query": "rsExact"})
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["rows"][0]["sample"], "P1")
        self.assertEqual(result["rows"][0]["genotype"], "0/1")
        self.assertAlmostEqual(result["rows"][0]["allele_balance"], 10 / 22)
        self.assertEqual(result["rows"][0]["loftee_50bp"], "FAIL")
        self.assertEqual(result["rows"][0]["loftee_50bp_original"], "PASS")
        self.assertTrue(result["rows"][0]["loftee_50bp_changed"])
        self.assertEqual(result["rows"][0]["ptc_distance"], 42)
        self.assertEqual(result["rows"][0]["ptc_calc_status"], "ok")
        self.assertEqual(
            result["rows"][0]["unscored_indel_reasons"],
            "SpliceAI_intronic",
        )

    def test_cohort_review_reports_compact_fallback_when_tabix_is_unavailable(self):
        self.store.import_paths([str(self.vcf)])
        row = self.store.query({"mode": "variant", "query": "rsExact"})["rows"][0]

        review = self.store.review_records([{
            "variant_key": row["variant_key"],
            "sample_entry_id": row["sample_entry_id"],
        }])

        self.assertEqual(review["resolved"], 0)
        self.assertEqual(review["files"], [])
        self.assertEqual(review["unresolved"][0]["variant_key"], "1:100:A:G")
        self.assertIn("bcftools/tabix is unavailable", review["warnings"][0])

    def test_gene_query_applies_default_qualification_and_genotype_filters(self):
        self.store.import_paths([str(self.vcf)])
        result = self.store.query({
            "mode": "gene",
            "gene": "nfkb1",
            "max_popmax": 0.01,
        })
        self.assertEqual(result["total"], 3)
        self.assertEqual(result["individuals"], 2)
        self.assertEqual(
            {row["variant_key"] for row in result["rows"]},
            {"1:100:A:G", "1:200:C:T", "1:300:G:C"},
        )
        p1_rows = [row for row in result["rows"] if row["sample"] == "P1"]
        self.assertEqual(len(p1_rows), 2)
        self.assertTrue(all(row["sample_qualifying_variant_count"] == 2 for row in p1_rows))

        homozygous = self.store.query({
            "mode": "gene",
            "gene": "NFKB1",
            "max_popmax": 0.01,
            "zygosity": "homozygous",
        })
        self.assertEqual(homozygous["total"], 1)
        self.assertEqual(homozygous["rows"][0]["sample"], "P2")

        pathogenic = self.store.query({
            "mode": "gene",
            "gene": "NFKB1",
            "max_popmax": 0.01,
            "clinvar_pathogenic_only": True,
        })
        self.assertEqual(pathogenic["total"], 1)
        self.assertEqual(pathogenic["rows"][0]["variant_key"], "1:100:A:G")

    def test_clinical_transcript_default_uses_mane_then_pick_fallback(self):
        fallback_vcf = self.root / "fallback.vcf"
        fallback_entries = [
            csq(
                "G", 1, "missense_variant", "MODERATE", "NOMANE",
                "c.1A>G", "p.Lys1Arg", 0, 20, 0.7, 0.01,
                mane="", picked="1", transcript="ENST_PICKED",
            ),
            csq(
                "G", 1, "stop_gained", "HIGH", "NOMANE",
                "c.2A>G", "p.Lys1Ter", 0, 40, ".", 0.01,
                mane="", picked="", transcript="ENST_ALTERNATE",
            ),
        ]
        mane_entries = [
            csq(
                "T", 1, "stop_gained", "HIGH", "HASMANE",
                "c.1C>T", "p.Arg1Ter", 0, 40, ".", 0.01,
                mane="", picked="1", transcript="ENST_NONMANE_PICK",
            ),
            csq(
                "T", 1, "missense_variant", "MODERATE", "HASMANE",
                "c.2C>T", "p.Arg1Trp", 0, 20, 0.8, 0.01,
                mane="MANE", picked="", transcript="ENST_MANE",
            ),
        ]
        text = (
            "##fileformat=VCFv4.2\n"
            "##contig=<ID=1,length=248956422>\n"
            '##INFO=<ID=CSQ,Number=.,Type=String,Description="Format: '
            + "|".join(CSQ_FIELDS)
            + '">\n'
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tP1\n"
            f"1\t700\t.\tA\tG\t99\tPASS\tCSQ={','.join(fallback_entries)}"
            "\tGT:AD:DP:GQ\t0/1:10,10:20:99\n"
            f"1\t800\t.\tC\tT\t99\tPASS\tCSQ={','.join(mane_entries)}"
            "\tGT:AD:DP:GQ\t0/1:10,10:20:99\n"
        )
        fallback_vcf.write_text(text)
        self.store.import_paths([str(fallback_vcf)])

        no_mane = self.store.query({"mode": "gene", "gene": "NOMANE"})
        self.assertEqual(no_mane["total"], 1)
        self.assertEqual(no_mane["rows"][0]["transcript"], "ENST_PICKED")
        self.assertTrue(no_mane["rows"][0]["picked"])
        self.assertFalse(no_mane["rows"][0]["mane"])

        has_mane = self.store.query({"mode": "gene", "gene": "HASMANE"})
        self.assertEqual(has_mane["total"], 1)
        self.assertEqual(has_mane["rows"][0]["transcript"], "ENST_MANE")
        self.assertTrue(has_mane["rows"][0]["mane"])

        all_transcripts = self.store.query({
            "mode": "gene",
            "gene": "NOMANE",
            "mane_only": False,
            "min_cadd": 30,
        })
        self.assertEqual(all_transcripts["total"], 1)
        self.assertEqual(all_transcripts["rows"][0]["transcript"], "ENST_ALTERNATE")

        detail = self.store.variant_detail("1:700:A:G")
        self.assertEqual(detail["total"], 1)
        self.assertEqual(len(detail["annotations"]), 2)
        self.assertEqual(
            {row["transcript"] for row in detail["annotations"]},
            {"ENST_PICKED", "ENST_ALTERNATE"},
        )

    def test_logofunc_filter_requires_exact_source_match_and_preserves_mane_row(self):
        source = self.root / "logofunc.vcf"
        mane = csq(
            "G", 1, "missense_variant", "MODERATE", "LOGOGENE",
            "c.1A>G", "p.Lys1Arg", 0.0001, 25, 0.8, 0.01,
            mane="MANE", picked="1", transcript="ENST_MANE",
            logofunc_allele_available="1",
            logofunc_source_transcript="ENST_SOURCE",
            logofunc_source_hgvsp="ENSP_SOURCE:p.Lys1Arg",
            logofunc_match="allele_only",
        )
        source_match = csq(
            "G", 1, "missense_variant", "MODERATE", "LOGOGENE",
            "c.1A>G", "p.Lys1Arg", 0.0001, 25, 0.8, 0.01,
            mane="", picked="", transcript="ENST_SOURCE",
            logofunc_prediction="GOF", logofunc_neutral=0.05,
            logofunc_gof=0.9, logofunc_lof=0.05,
            logofunc_allele_available="1",
            logofunc_source_transcript="ENST_SOURCE",
            logofunc_source_hgvsp="ENSP_SOURCE:p.Lys1Arg",
            logofunc_match="allele_transcript_protein",
        )
        source.write_text(
            "##fileformat=VCFv4.2\n"
            "##contig=<ID=1,length=248956422>\n"
            '##INFO=<ID=CSQ,Number=.,Type=String,Description="Format: '
            + "|".join(CSQ_FIELDS) + '">\n'
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tP1\n"
            f"1\t950\t.\tA\tG\t99\tPASS\tCSQ={mane},{source_match}"
            "\tGT:AD:DP:GQ\t0/1:10,10:20:99\n"
        )
        self.store.import_paths([str(source)])

        result = self.store.query({
            "mode": "gene", "gene": "LOGOGENE",
            "logofunc_class": "GOF", "min_logofunc_probability": 0.8,
        })
        self.assertEqual(result["total"], 1)
        row = result["rows"][0]
        self.assertEqual(row["transcript"], "ENST_MANE")
        self.assertEqual(row["logofunc_prediction"], "GOF")
        self.assertAlmostEqual(row["logofunc_gof"], 0.9)
        self.assertEqual(row["logofunc_source_transcript"], "ENST_SOURCE")
        self.assertEqual(row["logofunc_match"], "source_transcript_match_elsewhere")

        rejected = self.store.query({
            "mode": "gene", "gene": "LOGOGENE", "logofunc_class": "LOF",
        })
        self.assertEqual(rejected["total"], 0)

        detail = self.store.variant_detail("1:950:A:G")
        exact = next(
            annotation for annotation in detail["annotations"]
            if annotation["transcript"] == "ENST_SOURCE"
        )
        self.assertEqual(exact["logofunc_match"], "allele_transcript_protein")

    def test_legacy_single_transcript_vcf_without_pick_remains_searchable(self):
        legacy_vcf = self.root / "legacy-pick-output.vcf"
        legacy_fields = [field for field in CSQ_FIELDS if field != "PICK"]
        legacy_values = csq(
            "G", 1, "missense_variant", "MODERATE", "LEGACY",
            "c.1A>G", "p.Lys1Arg", 0, 25, 0.8, 0.01,
            mane="", picked="",
        ).split("|")
        del legacy_values[CSQ_FIELDS.index("PICK")]
        legacy_vcf.write_text(
            "##fileformat=VCFv4.2\n"
            "##contig=<ID=1,length=248956422>\n"
            '##INFO=<ID=CSQ,Number=.,Type=String,Description="Format: '
            + "|".join(legacy_fields)
            + '">\n'
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tP1\n"
            f"1\t900\t.\tA\tG\t99\tPASS\tCSQ={'|'.join(legacy_values)}"
            "\tGT:AD:DP:GQ\t0/1:10,10:20:99\n"
        )
        self.store.import_paths([str(legacy_vcf)])

        result = self.store.query({"mode": "gene", "gene": "LEGACY"})
        self.assertEqual(result["total"], 1)
        self.assertTrue(result["rows"][0]["picked"])

    def test_startup_does_not_repeat_legacy_pick_migration(self):
        self.store.import_paths([str(self.vcf)])
        database = self.store.database_path
        with self.store._session() as connection:
            annotation_id = connection.execute(
                """
                SELECT MIN(id)
                FROM cohort_annotations
                GROUP BY variant_id, gene
                HAVING COUNT(*) = 1
                LIMIT 1
                """
            ).fetchone()[0]
            connection.execute(
                "UPDATE cohort_annotations SET mane = 0, picked = 0 WHERE id = ?",
                (annotation_id,),
            )

        reopened = CohortStore(database)

        with reopened._session() as connection:
            picked = connection.execute(
                "SELECT picked FROM cohort_annotations WHERE id = ?",
                (annotation_id,),
            ).fetchone()[0]
        self.assertEqual(picked, 0)

    def test_directory_import_finds_plain_and_gzipped_vcfs(self):
        plain = self.root / "second.vcf"
        write_vcf(plain)
        result = self.store.import_paths([str(self.root)], recursive=False)
        self.assertEqual(result["imported"], 2)
        self.assertEqual(result["stats"]["files"], 2)
        self.assertEqual(result["stats"]["sample_entries"], 4)

    def test_auto_prepares_indexes_and_reads_four_contigs_in_parallel(self):
        source = self.root / "parallel.vcf"
        write_parallel_vcf(source)
        backend = FakeHtsBackend()
        store = CohortStore(
            self.root / "parallel.sqlite3",
            enable_auto_index=True,
            hts_backend=backend,
            index_readers=4,
        )

        imported = store.import_vcf(source)
        self.assertEqual(imported["import_mode"], "parallel_tabix_staged")
        # index_readers=4 is capped at os.cpu_count() (2 on CI runners).
        self.assertEqual(imported["reader_count"], store.index_readers)
        self.assertGreaterEqual(imported["reader_count"], 2)
        self.assertEqual(imported["variant_count"], 4)
        self.assertEqual(imported["records_processed"], 4)
        self.assertEqual(backend.sort_calls, 1)
        self.assertTrue(Path(imported["prepared_path"]).is_file())
        self.assertTrue(Path(imported["index_path"]).is_file())
        indexed = store.query({"mode": "variant", "query": "1:100:A:G"})
        self.assertEqual(indexed["rows"][0]["promoterai"], -0.75)
        self.assertEqual(
            indexed["rows"][0]["unscored_indel_reasons"],
            "PromoterAI_promoter",
        )

        refreshed = store.import_vcf(source, force=True)
        self.assertTrue(refreshed["cache_hit"])
        self.assertEqual(backend.sort_calls, 1)

    def test_splice_hgvs_plus_coordinates_survive_import(self):
        """VEP percent-encoding never uses + for space: form-decoding it
        corrupted every intronic HGVS (c.300+1G>C became "c.300 1G>C")."""
        source = self.root / "splice.vcf"
        write_vcf(source)
        store = CohortStore(
            self.root / "splice.sqlite3",
            enable_auto_index=True,
            hts_backend=FakeHtsBackend(),
            index_readers=2,
        )
        store.import_vcf(source)
        rows = store.query({"mode": "variant", "query": "1:300:G:C"})["rows"]
        self.assertTrue(rows)
        self.assertEqual(rows[0]["hgvsc"], "c.300+1G>C")

    def test_truncated_sample_rows_are_refused_not_silently_absorbed(self):
        source = self.root / "truncated.vcf"
        write_vcf(source)
        lines = source.read_text().splitlines()
        # Drop the final sample column from one record: a truncated export.
        for index, line in enumerate(lines):
            if line.startswith("1\t200\t"):
                lines[index] = line.rsplit("\t", 1)[0]
        source.write_text("\n".join(lines) + "\n")
        store = CohortStore(
            self.root / "truncated.sqlite3",
            enable_auto_index=True,
            hts_backend=FakeHtsBackend(),
            index_readers=2,
        )
        with self.assertRaisesRegex(ValueError, "truncated"):
            store.import_vcf(source)
        # A row truncated to the nine fixed columns (no genotypes at all)
        # previously slipped past as an ignorable line.
        write_vcf(source)
        lines = source.read_text().splitlines()
        for index, line in enumerate(lines):
            if line.startswith("1\t200\t"):
                lines[index] = "\t".join(line.split("\t")[:9])
        source.write_text("\n".join(lines) + "\n")
        store_nine = CohortStore(
            self.root / "truncated9.sqlite3",
            enable_auto_index=True,
            hts_backend=FakeHtsBackend(),
            index_readers=2,
        )
        with self.assertRaisesRegex(ValueError, "truncated"):
            store_nine.import_vcf(source)

    def test_content_probe_defeats_same_size_same_mtime_swaps(self):
        source = self.root / "probe.vcf"
        write_vcf(source)
        store = CohortStore(
            self.root / "probe.sqlite3",
            enable_auto_index=True,
            hts_backend=FakeHtsBackend(),
            index_readers=2,
        )
        store.import_vcf(source)
        self.assertEqual(
            len(store.query({"mode": "variant", "query": "1:100:A:G"})["rows"]), 1,
        )
        stat = source.stat()
        # Same byte length, different content (position 100 -> 101), and the
        # original timestamps restored — the classic stale-cache defeat.
        source.write_text(source.read_text().replace("1\t100\t", "1\t101\t"))
        os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        second = store.import_vcf(source)
        self.assertNotEqual(second.get("status"), "unchanged")
        self.assertEqual(
            len(store.query({"mode": "variant", "query": "1:101:A:G"})["rows"]), 1,
        )
        self.assertEqual(
            len(store.query({"mode": "variant", "query": "1:100:A:G"})["rows"]), 0,
        )

    def test_full_hash_closes_the_probe_blind_window(self):
        """The stripe probe samples 512KiB regardless of file size, leaving
        interior blind windows. For files small enough to hash completely,
        the unchanged gate must ALSO bind the full digest — a probe-blind
        edit with restored timestamps previously returned "unchanged" and
        served the stale variant."""
        from local_service import cohort_store as module
        source = self.root / "blind.vcf"
        write_vcf(source)
        # Align the mtime to a microsecond boundary BEFORE the first import:
        # APFS truncates os.utime to microseconds, so an unaligned original
        # mtime cannot be restored exactly and the size+mtime gate alone
        # would force the re-import — the test would pass without ever
        # exercising the sha gate it exists for.
        aligned = source.stat().st_mtime_ns // 1000 * 1000
        os.utime(source, ns=(aligned, aligned))
        store = CohortStore(
            self.root / "blind.sqlite3",
            enable_auto_index=True,
            hts_backend=FakeHtsBackend(),
            index_readers=2,
        )
        real_probe = module._content_probe
        module._content_probe = lambda path, block=65536: "blind-probe"
        try:
            store.import_vcf(source)
            stat = source.stat()
            source.write_text(source.read_text().replace("1\t100\t", "1\t101\t"))
            os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns))
            second = store.import_vcf(source)
        finally:
            module._content_probe = real_probe
        self.assertNotEqual(second.get("status"), "unchanged")
        self.assertEqual(
            len(store.query({"mode": "variant", "query": "1:101:A:G"})["rows"]), 1,
        )

    def test_prepared_cache_observes_content_probe_with_native_backend(self):
        """The working-copy cache below the import gate must also see the
        probe: with a real backend it served STALE prepared bytes to the
        re-import the identity gate correctly triggered."""
        from local_service.cohort_store import HtsBackend
        backend = HtsBackend.discover()
        if backend is None or not backend.native_tools.get("bcftools"):
            self.skipTest("native bcftools is required for the prepare cache")
        source = self.root / "probe-native.vcf"
        write_vcf(source)
        store = CohortStore(
            self.root / "probe-native.sqlite3",
            enable_auto_index=True,
            hts_backend=backend,
            index_readers=2,
        )
        store.import_vcf(source)
        self.assertEqual(
            len(store.query({"mode": "variant", "query": "1:100:A:G"})["rows"]), 1,
        )
        stat = source.stat()
        source.write_text(source.read_text().replace("1\t100\t", "1\t101\t"))
        os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        store.import_vcf(source)
        self.assertEqual(
            len(store.query({"mode": "variant", "query": "1:101:A:G"})["rows"]), 1,
        )
        self.assertEqual(
            len(store.query({"mode": "variant", "query": "1:100:A:G"})["rows"]), 0,
        )

    def test_content_probe_sees_interior_changes(self):
        """Head+tail probing left a blind window: a >128KiB file changed
        only in the middle probed identically."""
        from local_service.cohort_store import _content_probe
        big = self.root / "big.bin"
        payload = bytearray(b"x" * 400_000)
        big.write_bytes(payload)
        before = _content_probe(big)
        payload[200_000:200_010] = b"CHANGED HERE"[:10]
        big.write_bytes(payload)
        self.assertNotEqual(before, _content_probe(big))

    def test_poisoned_managed_destination_is_repaired_on_reimport(self):
        """A managed copy filed under a checksum whose bytes do not match the
        prepared representation (the pre-fix stale-cache artifact) must be
        rebuilt, not trusted forever."""
        source = self.root / "poison.vcf"
        write_vcf(source)
        store = CohortStore(
            self.root / "poison.sqlite3",
            enable_auto_index=True,
            hts_backend=FakeHtsBackend(),
            index_readers=2,
        )
        managed_dir = self.root / "managed"
        import hashlib as _hashlib
        content_key = _hashlib.sha256(source.read_bytes()).hexdigest()
        # Plant poison: wrong bytes already sitting at the destination.
        managed_dir.mkdir()
        poisoned = managed_dir / f"{content_key}.vcf.gz"
        poisoned.write_bytes(b"stale poisoned bytes")
        managed_path, _, _ = store.prepare_managed_vcf(
            source, managed_dir, content_key
        )
        self.assertEqual(Path(managed_path), poisoned)
        self.assertNotEqual(poisoned.read_bytes(), b"stale poisoned bytes")

    def test_cohort_review_fetches_complete_exact_record_with_tabix(self):
        source = self.root / "review-source.vcf"
        write_vcf(source)
        store = CohortStore(
            self.root / "review.sqlite3",
            enable_auto_index=True,
            hts_backend=FakeHtsBackend(),
            index_readers=2,
        )
        store.import_vcf(source)
        row = store.query({"mode": "variant", "query": "1:100:A:G"})["rows"][0]

        review = store.review_records([{
            "variant_key": row["variant_key"],
            "sample_entry_id": row["sample_entry_id"],
        }])

        self.assertEqual(review["requested"], 1)
        self.assertEqual(review["resolved"], 1)
        self.assertEqual(review["unresolved"], [])
        self.assertEqual(len(review["files"]), 1)
        content = review["files"][0]["vcf"]
        self.assertIn("##INFO=<ID=CSQ", content)
        self.assertIn("#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tP1\n", content)
        self.assertNotIn("\tP2\n", content)
        self.assertIn("1\t100\trsExact\tA\tG", content)
        self.assertNotIn("1\t200\t", content)
        self.assertEqual(
            review["files"][0]["selections"][0]["sample_entry_id"],
            row["sample_entry_id"],
        )

        second = store.query({"mode": "variant", "query": "1:200:C:T"})["rows"][0]
        combined = store.review_records([
            {"variant_key": row["variant_key"], "sample_entry_id": row["sample_entry_id"]},
            {"variant_key": second["variant_key"], "sample_entry_id": second["sample_entry_id"]},
        ])
        self.assertEqual(combined["resolved"], 2)
        self.assertEqual(len(combined["files"]), 1)
        self.assertIn("\tP1\tP2\n", combined["files"][0]["vcf"])
        self.assertIn("1\t100\trsExact\tA\tG", combined["files"][0]["vcf"])
        self.assertIn("1\t200\t.\tC\tT", combined["files"][0]["vcf"])

    def test_sample_review_projects_complete_stored_carrier_set(self):
        self.store.import_paths([str(self.vcf)])
        p1 = next(
            sample for sample in self.store.list_samples()
            if sample["name"] == "P1"
        )

        review = self.store.sample_review_files([p1["id"]])

        self.assertEqual(review["sample_entries"], 1)
        self.assertEqual(review["carrier_observations"], 3)
        self.assertEqual(review["records"], 3)
        self.assertEqual(review["analysis_scope"], "exome")
        self.assertEqual(len(review["files"]), 1)
        self.assertEqual(review["files"][0]["import_profile"], "full")
        self.assertEqual(review["files"][0]["analysis_scope"], "exome")
        content = review["files"][0]["vcf"]
        self.assertIn("#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tP1\n", content)
        self.assertNotIn("\tP2\n", content)
        self.assertIn("1\t100\trsExact\tA\tG", content)
        self.assertIn("1\t300\t.\tG\tA,C", content)
        self.assertIn("1\t400\t.\tT\tC", content)
        self.assertNotIn("1\t200\t.\tC\tT", content)

    def test_sample_review_rejects_unknown_sample_entry(self):
        with self.assertRaisesRegex(ValueError, "sample entries were not found"):
            self.store.sample_review_files([999])

    def test_background_job_supports_spawned_parallel_readers(self):
        source = self.root / "parallel-background.vcf"
        write_parallel_vcf(source)
        store = CohortStore(
            self.root / "parallel-background.sqlite3",
            enable_auto_index=True,
            hts_backend=FakeHtsBackend(),
            index_readers=4,
        )
        job = store.start_import_paths([str(source)])
        for _ in range(400):
            job = store.get_import_job(job["id"])
            if job["status"] in {"succeeded", "failed"}:
                break
            time.sleep(0.01)
        self.assertEqual(job["status"], "succeeded")
        self.assertEqual(job["phase"], "complete")
        # index_readers=4 is capped at os.cpu_count() (2 on CI runners).
        self.assertEqual(job["result"]["files"][0]["reader_count"], store.index_readers)
        self.assertGreaterEqual(job["result"]["files"][0]["reader_count"], 2)
        self.assertEqual(job["result"]["files"][0]["variant_count"], 4)

    def test_auto_prepare_failure_falls_back_to_serial_staging(self):
        source = self.root / "fallback.vcf"
        write_vcf(source)
        store = CohortStore(
            self.root / "fallback.sqlite3",
            enable_auto_index=True,
            hts_backend=UnavailableHtsBackend(),
            index_readers=4,
        )

        imported = store.import_vcf(source)

        self.assertEqual(imported["import_mode"], "serial_staged")
        self.assertEqual(imported["reader_count"], 1)
        self.assertEqual(imported["variant_count"], 5)
        self.assertIn("using serial staged import", imported["preparation_warning"])

    @unittest.skipUnless(
        os.environ.get("IEI_RUN_HTS_INTEGRATION") == "1",
        "set IEI_RUN_HTS_INTEGRATION=1 to exercise real bcftools/tabix",
    )
    def test_real_hts_backend_prepares_and_parallelizes(self):
        source = self.root / "real-parallel.vcf"
        write_parallel_vcf(source)
        store = CohortStore(
            self.root / "real-parallel.sqlite3",
            enable_auto_index=True,
            index_readers=4,
        )
        imported = store.import_vcf(source)
        self.assertFalse(imported["preparation_warning"])
        self.assertEqual(imported["import_mode"], "parallel_tabix_staged")
        self.assertEqual(imported["reader_count"], store.index_readers)
        self.assertEqual(imported["variant_count"], 4)

    def test_gene_list_query_matches_any_listed_gene(self):
        source = self.root / "genelist.vcf"
        write_vcf(source)
        store = CohortStore(self.root / "genelist.sqlite3")
        store.import_vcf(source)
        result = store.query({
            "mode": "gene_list", "genes": "NFKB1, IL10RA\nNOSUCHGENE",
        })
        genes = {row["gene"] for row in result["rows"]}
        self.assertIn("NFKB1", genes)
        # Default qualifying filters (HIGH/MODERATE) still apply.
        self.assertTrue(all(row["impact"] in {"HIGH", "MODERATE"} for row in result["rows"]))
        with self.assertRaisesRegex(ValueError, "at least one gene"):
            store.query({"mode": "gene_list", "genes": "  ,  "})
        with self.assertRaisesRegex(ValueError, "2000-gene"):
            store.query({"mode": "gene_list", "genes": [f"G{i}" for i in range(2001)]})

    def test_region_query_returns_window_records_with_optional_filters(self):
        source = self.root / "region.vcf"
        write_vcf(source)
        store = CohortStore(self.root / "region.sqlite3")
        store.import_vcf(source)
        # Impacts cleared: a non-coding window must not silently hide
        # MODIFIER records behind the coding-default impact filter.
        result = store.query({"mode": "region", "region": "chr1:100-250", "impacts": []})
        positions = sorted({row["pos"] for row in result["rows"]})
        self.assertEqual(positions, [100, 200])
        with self.assertRaisesRegex(ValueError, "chrom:start-end"):
            store.query({"mode": "region", "region": "NFKB1"})
        with self.assertRaisesRegex(ValueError, "5 Mb"):
            store.query({"mode": "region", "region": "1:1-6000002"})

    def test_every_search_mode_uses_an_index_not_a_table_scan(self):
        """Guard against schema drift reintroducing full scans: at cohort
        scale (millions of rows) a SCAN turns millisecond queries into
        half-minute ones with no error to point at."""
        import sqlite3 as sql
        source = self.root / "plans.vcf"
        write_vcf(source)
        store = CohortStore(self.root / "plans.sqlite3")
        store.import_vcf(source)
        probes = {
            "variant_key": ("SELECT v.id FROM cohort_variants v WHERE v.variant_key = ? COLLATE NOCASE", ("1:100:A:G",)),
            "rsid": ("SELECT v.id FROM cohort_variants v WHERE v.rsid = ? COLLATE NOCASE", ("rsExact",)),
            "region": ("SELECT v.id FROM cohort_variants v WHERE v.chrom = ? AND v.pos BETWEEN ? AND ?", ("1", 1, 500)),
            "gene": ("SELECT a.id FROM cohort_annotations a WHERE a.gene = ?", ("NFKB1",)),
            "gene_list": ("SELECT a.id FROM cohort_annotations a WHERE a.gene IN (?,?)", ("NFKB1", "IL10RA")),
            "carriers": ("SELECT g.id FROM cohort_genotypes g WHERE g.variant_id = ?", (1,)),
        }
        with sql.connect(self.root / "plans.sqlite3") as connection:
            for name, (statement, parameters) in probes.items():
                plan = " | ".join(
                    row[-1] for row in connection.execute(
                        "EXPLAIN QUERY PLAN " + statement, parameters
                    )
                )
                self.assertIn("SEARCH", plan, f"{name} does not SEARCH an index: {plan}")
                self.assertNotIn("SCAN", plan.split("SEARCH")[0], f"{name} scans: {plan}")

    def test_rejects_explicit_non_grch38_contig_length(self):
        wrong = self.root / "wrong-build.vcf"
        write_vcf(wrong)
        text = wrong.read_text().replace(
            "##contig=<ID=1,length=248956422>\n",
            "##contig=<ID=1,length=249250621>\n",
        )
        wrong.write_text(text)
        result = self.store.import_paths([str(wrong)])
        self.assertEqual(result["failed"], 1)
        self.assertIn("GRCh38-only", result["files"][0]["error"])

    def test_lifted_variant_retains_original_grch37_locus(self):
        lifted = self.root / "lifted.vcf"
        write_vcf(lifted)
        text = lifted.read_text().replace(
            "##contig=<ID=1,length=248956422>\n",
            "##contig=<ID=1,length=248956422>\n"
            "##iei_target_assembly=GRCh38\n"
            "##iei_liftover=<SourceAssembly=GRCh37,TargetAssembly=GRCh38>\n",
        ).replace(
            "1\t100\trsExact\tA\tG\t99\tPASS\tCSQ=",
            "1\t100\trsExact\tA\tG\t99\tPASS\t"
            "IEI_ORIGINAL_ASSEMBLY=GRCh37;"
            "IEI_ORIGINAL_CHROM=chr1;IEI_ORIGINAL_POS=101;"
            "IEI_ORIGINAL_REF=A;IEI_ORIGINAL_ALT=G;CSQ=",
            1,
        )
        lifted.write_text(text)
        result = self.store.import_paths([str(lifted)])
        self.assertEqual(result["imported"], 1)
        row = self.store.query({"mode": "variant", "query": "rsExact"})["rows"][0]
        self.assertEqual(row["original_assembly"], "GRCh37")
        self.assertEqual(row["original_chrom"], "chr1")
        self.assertEqual(row["original_pos"], 101)


class ReannotationFlagTests(unittest.TestCase):
    def test_reannotation_can_clear_a_superseded_mane_flag(self):
        # Audit repro (SVC-13): mane/picked were merged with MAX(), so after
        # a VEP/MANE release moved the canonical transcript, BOTH the old and
        # the new transcript stayed flagged and queries returned two
        # "preferred" rows per gene. The latest import must win.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "mane-move.vcf"

            def build(mane_on_first: bool) -> None:
                entries = ",".join([
                    csq(
                        "G", 1, "missense_variant", "MODERATE", "NFKB1",
                        "c.1A>G", "p.Lys1Arg", 0.0001, 25, 0.8, 0.01,
                        mane="MANE" if mane_on_first else "",
                        picked="1" if mane_on_first else "",
                        transcript="ENST_T1",
                    ),
                    csq(
                        "G", 1, "missense_variant", "MODERATE", "NFKB1",
                        "c.1A>G", "p.Lys1Arg", 0.0001, 25, 0.8, 0.01,
                        mane="" if mane_on_first else "MANE",
                        picked="" if mane_on_first else "1",
                        transcript="ENST_T2",
                    ),
                ])
                source.write_text(
                    "##fileformat=VCFv4.2\n"
                    "##contig=<ID=1,length=248956422>\n"
                    '##INFO=<ID=CSQ,Number=.,Type=String,Description="Format: '
                    + "|".join(CSQ_FIELDS)
                    + '">\n'
                    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tP1\n"
                    "1\t100\t.\tA\tG\t99\tPASS\tCSQ=" + entries
                    + "\tGT:AD:DP:GQ\t0/1:12,10:22:80\n"
                )

            store = CohortStore(root / "cohort.sqlite3")
            build(mane_on_first=True)
            store.import_vcf(source)
            build(mane_on_first=False)
            store.import_vcf(source, force=True)

            import sqlite3 as sqlite_module
            connection = sqlite_module.connect(root / "cohort.sqlite3")
            flagged = dict(connection.execute(
                "SELECT transcript, mane FROM cohort_annotations"
            ).fetchall())
            connection.close()
            self.assertEqual(flagged.get("ENST_T1"), 0, flagged)
            self.assertEqual(flagged.get("ENST_T2"), 1, flagged)


class ParseGenotypeTests(unittest.TestCase):
    def test_half_call_is_not_hemizygous(self):
        # Audit repro (SVC-16): ./1 previously classified "hemizygous",
        # indistinguishable from a confident haploid X/Y call.
        parsed = parse_genotype("GT", "./1", 0)
        self.assertTrue(parsed["carrier"])
        self.assertEqual(parsed["zygosity"], "half_called")
        self.assertEqual(parse_genotype("GT", "1|.", 0)["zygosity"], "half_called")

    def test_true_haploid_call_is_hemizygous(self):
        self.assertEqual(parse_genotype("GT", "1", 0)["zygosity"], "hemizygous")

    def test_diploid_classes_unchanged(self):
        self.assertEqual(parse_genotype("GT", "0/1", 0)["zygosity"], "heterozygous")
        self.assertEqual(parse_genotype("GT", "1/1", 0)["zygosity"], "homozygous")
        self.assertEqual(parse_genotype("GT", "1/2", 1)["zygosity"], "heterozygous")

    def test_missing_ad_component_yields_null_balance_not_zero(self):
        # Audit repro (SVC-17): AD "12,." previously became [12, 0] and an
        # allele balance of 0.0 — indistinguishable from a genuine
        # zero-read ALT.
        parsed = parse_genotype("GT:AD", "0/1:12,.", 0)
        self.assertIsNone(parsed["allele_balance"])

    def test_numeric_ad_still_produces_balance(self):
        parsed = parse_genotype("GT:AD", "0/1:12,6", 0)
        self.assertAlmostEqual(parsed["allele_balance"], 6 / 18)

    def test_absent_ad_yields_null_balance(self):
        parsed = parse_genotype("GT:DP:GQ", "0/1:30:99", 0)
        self.assertIsNone(parsed["allele_balance"])


if __name__ == "__main__":
    unittest.main()
