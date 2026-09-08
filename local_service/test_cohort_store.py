#!/usr/bin/env python3
import gzip
import os
import re
import tempfile
import time
import unittest
from unittest.mock import patch
from contextlib import contextmanager
import threading
import sqlite3
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from local_service.cohort_store import (
    CohortStore, annotation_from, parse_genotype, single_copy_locus,
)


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
    "PromoterAI_score", "PromoterAI_TSS", "PromoterAI_strand",
    "PromoterAI_source_transcript", "PromoterAI_match",
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
    promoter_score="", promoter_tss="", promoter_strand="",
    promoter_transcript="", promoter_match="",
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
        str(promoter_score), str(promoter_tss), promoter_strand,
        promoter_transcript, promoter_match,
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
    # The PromoterAI plugin writes its score together with the transcript/TSS
    # provenance; only that complete form is stored (review M5). The bare
    # record-level ``promoterAI=`` INFO score the fixture used to carry is
    # kept to prove it is withheld.
    consequence = csq(
        "G", 1, "missense_variant", "MODERATE", "NFKB1",
        "c.100A>G", "p.Lys34Arg", 0.0001, 25, 0.8, 0.01,
        promoter_score="-0.75", promoter_tss="50", promoter_strand="1",
        promoter_transcript="ENST_NFKB1.1", promoter_match="exact_version",
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
        # Real prefiltered review files always DECLARE this flag (the merge
        # step writes the header line); strict bcftools builds (observed:
        # a conda 1.24) hard-fail sort on undeclared INFO keys rather than
        # warning, so the fixture must model the declared reality.
        + '##INFO=<ID=IEI_UNSCORED_INDEL,Number=A,Type=String,Description="Unscored indel routes">\n'
        + '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n'
        + '##FORMAT=<ID=AD,Number=R,Type=Integer,Description="Allelic depths">\n'
        + '##FORMAT=<ID=DP,Number=1,Type=Integer,Description="Depth">\n'
        + '##FORMAT=<ID=GQ,Number=1,Type=Integer,Description="Genotype quality">\n'
        + "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tP1\n"
        + "".join(
            f"{chrom}\t100\t.\tA\tG\t99\tPASS\tIEI_UNSCORED_INDEL=PromoterAI_promoter;promoterAI=0.99;CSQ={consequence}"
            "\tGT:AD:DP:GQ\t0/1:10,10:20:99\n"
            for chrom in range(1, 5)
        )
    )


def review_text(store, review, index=0):
    """The projected VCF the browser streams: served from disk, not embedded."""
    url = review["files"][index]["vcf_url"]
    token, position = url.rsplit("/", 2)[-2:]
    return store.review_export_file(token, position).read_text(encoding="utf-8")


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
            # Never dropped: it carries the file-id allocator (H1).
            "cohort_meta",
        })
        restored = self.store.import_paths([str(self.vcf)])
        self.assertEqual(restored["imported"], 1)
        self.assertEqual(restored["stats"]["individuals"], 2)

    def test_variant_keys_are_representation_insensitive(self):
        """Audit repro (H5): chrom:pos:REF:ALT keys kept the caller's padding,
        so the same allele written two ways got two rows and an exact
        search for one representation missed carriers stored under the
        other."""
        from local_service.cohort_store import minimal_representation, variant_key
        self.assertEqual(variant_key("chr1", 100, "AT", "ATT"), "1:100:A:AT")
        self.assertEqual(variant_key("1", 298, "atg", "atc"), "1:300:G:C")
        self.assertEqual(variant_key("1", 100, "AT", "A"), "1:100:AT:A")
        self.assertEqual(variant_key("M", 8993, "T", "G"), "MT:8993:T:G")
        self.assertEqual(variant_key("1", 100, "A", "<DEL>"), "1:100:A:<DEL>")
        self.assertEqual(variant_key("1", 100, "A", "*"), "1:100:A:*")
        self.assertEqual(minimal_representation(5, "GATC", "GTTC"), (6, "A", "T"))

        padded = self.root / "padded.vcf"
        with gzip.open(self.vcf, "rt", encoding="utf-8") as handle:
            padded.write_text(handle.read().replace(
                "1\t100\trsExact\tA\tG\t", "1\t100\trsExact\tAT\tGT\t"
            ))
        self.store.import_vcf(padded)
        hits = self.store.query({"mode": "variant", "query": "1:100:A:G"})
        self.assertEqual(hits["total"], 1, hits["rows"])
        self.assertEqual(hits["rows"][0]["variant_key"], "1:100:A:G")
        # The stored representation is the canonical one the key was built
        # from, so display, position search and record retrieval agree
        # whichever representation a file used.
        self.assertEqual(
            (hits["rows"][0]["pos"], hits["rows"][0]["ref"], hits["rows"][0]["alt"]),
            (100, "A", "G"),
        )
        hits = self.store.query({"mode": "variant", "query": "chr1-100-AT-GT"})
        self.assertEqual(hits["total"], 1)

    def test_stored_records_resolve_for_every_representation_of_an_allele(self):
        """Audit repro (H5, P2): the index kept the FIRST file's chrom/pos/
        ref/alt for a canonical key, and review_records matched source
        records on that representation, so the other file's carrier could
        not be retrieved and a canonical position-only search returned
        zero when the padded file had been imported first."""
        import subprocess
        from local_service.cohort_store import HtsBackend
        backend = HtsBackend.discover()
        if backend is None or not backend.native_tools.get("bcftools"):
            self.skipTest("native bcftools/tabix are required for source-record retrieval")
        with gzip.open(self.vcf, "rt", encoding="utf-8") as handle:
            text = handle.read()
        variants = {
            "minimal": text.replace("\tP1\tP2", "\tS1\tS2"),
            "padded": text.replace("\tP1\tP2", "\tT1\tT2")
            .replace("1\t100\trsExact\tA\tG\t", "1\t100\trsExact\tAT\tGT\t")
            .replace("1\t300\t.\tG\tA,C\t", "1\t298\t.\tATG\tATA,ATC\t"),
        }
        for order in (("minimal", "padded"), ("padded", "minimal")):
            root = self.root / "-".join(order)
            root.mkdir()
            store = CohortStore(root / "cohort.sqlite3", hts_backend=backend)
            for name in order:
                plain = root / f"{name}.vcf"
                plain.write_text(variants[name], encoding="utf-8")
                subprocess.run(["bgzip", "-f", str(plain)], check=True)
                subprocess.run(["tabix", "-p", "vcf", "-f", f"{plain}.gz"], check=True)
                store.import_vcf(Path(f"{plain}.gz"))
            for key, expected_pos in (("1:100:A:G", 100), ("1:300:G:C", 300)):
                hits = store.query({"mode": "variant", "query": key})
                self.assertEqual(sorted(row["sample"] for row in hits["rows"]), ["S1", "T1"], order)
                self.assertEqual({row["pos"] for row in hits["rows"]}, {expected_pos}, order)
                resolved = store.review_records([
                    {"variant_key": row["variant_key"], "sample_entry_id": row["sample_entry_id"]}
                    for row in hits["rows"]
                ])
                self.assertEqual(resolved.get("unresolved"), [], (order, key, resolved.get("warnings")))
                retrieved = sorted(
                    selection["sample"] for item in resolved["files"] for selection in item["selections"]
                )
                self.assertEqual(retrieved, ["S1", "T1"], (order, key))
                # Every projected record carries the allele (in its own file's representation).
                for item in resolved["files"]:
                    self.assertTrue(any(
                        line.startswith("1\t") and not line.startswith("#") for line in item["vcf"].splitlines()
                    ), (order, key))
            # The canonical position is searchable regardless of import order.
            self.assertGreater(store.query({"mode": "variant", "query": "1:300"})["total"], 0, order)

    def test_legacy_variant_keys_are_migrated_and_merged(self):
        from local_service.cohort_store import VARIANT_KEY_FORMAT
        self.store.import_vcf(self.vcf)
        with self.store._session() as connection:
            # Simulate a pre-migration database: a padded twin of 1:100 A>G
            # stored under its raw key, carried by P2 (P1 carries the
            # canonical row), and no format stamp.
            row = connection.execute(
                "SELECT * FROM cohort_variants WHERE variant_key = '1:100:A:G'"
            ).fetchone()
            connection.execute(
                """INSERT INTO cohort_variants(variant_key, chrom, pos, ref, alt)
                   VALUES ('1:100:AT:GT', '1', 100, 'AT', 'GT')"""
            )
            twin = connection.execute(
                "SELECT id FROM cohort_variants WHERE variant_key = '1:100:AT:GT'"
            ).fetchone()[0]
            p2 = connection.execute(
                "SELECT id FROM cohort_samples WHERE name = 'P2'"
            ).fetchone()[0]
            connection.execute(
                """INSERT INTO cohort_genotypes(variant_id, sample_id, genotype, zygosity)
                   VALUES (?, ?, '0/1', 'heterozygous')""",
                (twin, p2),
            )
            connection.execute(
                "UPDATE cohort_variants SET variant_key = '1:100:AT:GT' WHERE id = ?",
                (twin,),
            )
            # A row as the interim "minimal-v1" migration left it: canonical
            # key, but the caller's padded columns (ATT>ACT at 398 is T>C at 399).
            connection.execute(
                """INSERT INTO cohort_variants(variant_key, chrom, pos, ref, alt)
                   VALUES ('1:399:T:C', '1', 398, 'ATT', 'ACT')"""
            )
            connection.execute("DELETE FROM cohort_meta WHERE key = 'variant_key_format'")
        # Re-opening runs the migration.
        from local_service.cohort_store import CohortStore
        reopened = CohortStore(self.root / "cohort.sqlite3")
        with reopened._session() as connection:
            keys = [r[0] for r in connection.execute(
                "SELECT variant_key FROM cohort_variants WHERE pos = 100 ORDER BY variant_key"
            )]
            self.assertEqual(keys, ["1:100:A:G"])
            self.assertEqual(
                tuple(connection.execute(
                    "SELECT pos, ref, alt FROM cohort_variants WHERE variant_key = '1:399:T:C'"
                ).fetchone()),
                (399, "T", "C"),
            )
            stamp = connection.execute(
                "SELECT value FROM cohort_meta WHERE key = 'variant_key_format'"
            ).fetchone()[0]
            self.assertEqual(stamp, VARIANT_KEY_FORMAT)
        hits = reopened.query({"mode": "variant", "query": "1:100:A:G"})
        self.assertEqual(sorted(r["sample"] for r in hits["rows"]), ["P1", "P2"])

    def test_cohort_file_ids_are_never_reused(self):
        """Audit repro (H1): the highest rowid was reused after the last row
        was deleted, and a DROP/recreate reset even AUTOINCREMENT."""
        first = self.store.import_vcf(self.vcf)["id"]
        with self.store._session() as connection:
            connection.execute("DELETE FROM cohort_files WHERE id = ?", (first,))
        second = self.store.import_vcf(self.vcf, force=True)["id"]
        self.assertGreater(second, first)
        self.store._reset_cohort_tables()
        third = self.store.import_vcf(self.vcf, force=True)["id"]
        self.assertGreater(third, second)

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

    def test_interrupted_pick_migration_is_completed_on_the_next_open(self):
        """Review follow-up of M30: the picked column being present used to
        stand for the backfill having run, so a database interrupted between
        adding the column and backfilling it reopened with every eligible
        row at 0 for good. The backfill is now stamped in cohort_meta; a
        stamp-less table with no pick at all is backfilled on open."""
        from local_service.cohort_store import PICKED_BACKFILL_VERSION
        many = self.root / "many.vcf"
        write_many_variant_vcf(many, 40)
        self.store.import_paths([str(many)])
        database = self.store.database_path
        with self.store._session() as connection:
            # Column present, nothing backfilled, no stamp: exactly what an
            # interruption before the backfill leaves behind.
            connection.execute("UPDATE cohort_annotations SET picked = 0, mane = 0")
            connection.execute("DELETE FROM cohort_meta WHERE key = 'picked_backfill'")
        reopened = CohortStore(database)
        with reopened._session() as connection:
            picked = connection.execute(
                "SELECT COUNT(*) FROM cohort_annotations WHERE picked = 1"
            ).fetchone()[0]
            stamp = connection.execute(
                "SELECT value FROM cohort_meta WHERE key = 'picked_backfill'"
            ).fetchone()[0]
        self.assertEqual(picked, 40)
        self.assertEqual(stamp, PICKED_BACKFILL_VERSION)

        # A stamp-less database that already carries picks (migrated by an
        # earlier build, or indexed with PICK) keeps its deliberate zeros.
        with reopened._session() as connection:
            connection.execute("UPDATE cohort_annotations SET picked = 0 WHERE id % 2 = 0")
            connection.execute("DELETE FROM cohort_meta WHERE key = 'picked_backfill'")
            expected = connection.execute(
                "SELECT COUNT(*) FROM cohort_annotations WHERE picked = 1"
            ).fetchone()[0]
        again = CohortStore(database)
        with again._session() as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM cohort_annotations WHERE picked = 1"
                ).fetchone()[0],
                expected,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT value FROM cohort_meta WHERE key = 'picked_backfill'"
                ).fetchone()[0],
                PICKED_BACKFILL_VERSION,
            )

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
        # The CSQ score with complete provenance is stored; the bare INFO
        # score (0.99) is not what the column holds.
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

    def test_managed_destination_binds_to_the_content_key(self):
        """The managed file is filed UNDER the caller's checksum: bytes
        that do not hash to that key must be refused BEFORE an existing
        healthy destination is touched — an A->B->A source swap timed
        around the old re-checks replaced a checksum-A managed file with
        B's variants."""
        source = self.root / "bind.vcf"
        write_vcf(source)
        store = CohortStore(
            self.root / "bind.sqlite3",
            enable_auto_index=True,
            hts_backend=FakeHtsBackend(),
            index_readers=2,
        )
        from local_service.sample_library import _sha256
        key_a = _sha256(source)
        managed_dir = self.root / "managed"
        destination, _, _ = store.prepare_managed_vcf(source, managed_dir, key_a)
        healthy = destination.read_bytes()
        # The source now holds B (same samples, different variants) while
        # the caller still presents A's key — the exact swap scenario.
        source.write_text(source.read_text().replace("1\t100\t", "1\t105\t"))
        with self.assertRaisesRegex(ValueError, "changed while"):
            store.prepare_managed_vcf(source, managed_dir, key_a)
        self.assertEqual(destination.read_bytes(), healthy)
        # With the CURRENT content's own key, the import proceeds.
        key_b = _sha256(source)
        dest_b, _, _ = store.prepare_managed_vcf(source, managed_dir, key_b)
        self.assertNotEqual(dest_b, destination)
        # No snapshot temporaries linger.
        self.assertEqual(list(managed_dir.glob("*.snapshot*")), [])

    def test_identity_less_rows_reindex_instead_of_passing_unchanged(self):
        """A row with neither probe nor sha cannot honestly be called
        unchanged: certifying current bytes onto rows indexed from unknown
        content presented a trust-on-first-use hash as verified."""
        import sqlite3 as _sqlite3
        source = self.root / "legacy.vcf"
        write_vcf(source)
        aligned = source.stat().st_mtime_ns // 1000 * 1000
        os.utime(source, ns=(aligned, aligned))
        database = self.root / "legacy.sqlite3"
        store = CohortStore(
            database, enable_auto_index=True,
            hts_backend=FakeHtsBackend(), index_readers=2,
        )
        store.import_vcf(source)
        with _sqlite3.connect(database) as connection:
            connection.execute(
                "UPDATE cohort_files SET content_probe='', content_sha256=''"
            )
        stat = source.stat()
        source.write_text(source.read_text().replace("1\t100\t", "1\t101\t"))
        os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        second = store.import_vcf(source)
        self.assertNotEqual(second.get("status"), "unchanged")
        self.assertEqual(
            len(store.query({"mode": "variant", "query": "1:101:A:G"})["rows"]), 1,
        )
        # The reindex itself wrote true identity; a further import with
        # nothing changed is unchanged again.
        third = store.import_vcf(source)
        self.assertEqual(third.get("status"), "unchanged")

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
        self.assertNotIn("vcf", review["files"][0], "the VCF text must not travel inside the JSON body")
        content = review_text(self.store, review)
        self.assertEqual(review["files"][0]["vcf_bytes"], len(content.encode("utf-8")))
        self.assertEqual(review["bytes"], review["files"][0]["vcf_bytes"])
        self.assertIn("#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tP1\n", content)
        self.assertNotIn("\tP2\n", content)
        self.assertIn("1\t100\trsExact\tA\tG", content)
        self.assertIn("1\t300\t.\tG\tA,C", content)
        self.assertIn("1\t400\t.\tT\tC", content)
        self.assertNotIn("1\t200\t.\tC\tT", content)

    def test_sample_review_export_is_byte_bounded_and_file_backed(self):
        """Audit M32: the record cap bounded count, not bytes. The projection
        is streamed to a file under a byte budget; exceeding it aborts with a
        clear message and leaves no partial export, and older exports are
        retired so the directory cannot grow without bound."""
        self.store.import_paths([str(self.vcf)])
        p1 = next(sample for sample in self.store.list_samples() if sample["name"] == "P1")
        review = self.store.sample_review_files([p1["id"]])
        path = self.store.review_export_file(review["export_id"], 0)
        self.assertEqual(path.parent, self.store.review_export_dir)
        self.assertTrue(path.is_file())
        # A budget smaller than this tiny projection: refused, nothing left behind.
        self.store.max_review_export_bytes = 64
        with self.assertRaisesRegex(ValueError, "browser size limit"):
            self.store.sample_review_files([p1["id"]])
        leftovers = [item for item in self.store.review_export_dir.glob("*.vcf") if item != path]
        self.assertEqual(leftovers, [])
        self.store.max_review_export_bytes = 256 * 1024 * 1024
        # Only the most recent exports are kept.
        from local_service import cohort_store as module
        tokens = [self.store.sample_review_files([p1["id"]])["export_id"] for _ in range(module.MAX_BROWSER_SAMPLE_REVIEW_EXPORTS + 2)]
        with self.assertRaises(KeyError):
            self.store.review_export_file(review["export_id"], 0)
        self.assertFalse(path.exists())
        self.assertTrue(self.store.review_export_file(tokens[-1], 0).is_file())
        with self.assertRaises(KeyError):
            self.store.review_export_file(tokens[-1], 5)
        with self.assertRaises(ValueError):
            self.store.review_export_file(tokens[-1], "x")

    def test_sample_review_export_budget_counts_utf8_bytes(self):
        """Review follow-up of M32: a text-mode write() reports characters,
        so a projection with multi-byte content was accepted past the byte
        budget (916 configured, 920 written). The budget is applied to the
        bytes on disk."""
        unicode_vcf = self.root / "unicode.vcf"
        with gzip.open(self.vcf, "rt", encoding="utf-8") as handle:
            lines = handle.read().splitlines()
        header_index = next(i for i, line in enumerate(lines) if line.startswith("#CHROM"))
        columns = lines[header_index].split("\t")
        columns[9] = "Pä–✓"  # a multi-byte sample name, repeated in the export header
        lines[header_index] = "\t".join(columns)
        unicode_vcf.write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.store.import_paths([str(unicode_vcf)])
        sample = next(
            entry for entry in self.store.list_samples() if entry["name"] == "Pä–✓"
        )
        review = self.store.sample_review_files([sample["id"]])
        path = self.store.review_export_file(review["export_id"], 0)
        size = path.stat().st_size
        characters = len(path.read_text(encoding="utf-8"))
        self.assertLess(characters, size, "the fixture must contain multi-byte characters")
        self.assertEqual(review["files"][0]["vcf_bytes"], size)
        # One byte under the true size: refused, even though the character
        # count fits comfortably.
        self.store.max_review_export_bytes = size - 1
        with self.assertRaisesRegex(ValueError, "browser size limit"):
            self.store.sample_review_files([sample["id"]])
        self.store.max_review_export_bytes = size
        self.store.sample_review_files([sample["id"]])

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

        # Audit repro (H8): the simplified probes above passed while the
        # REAL query() statement materialised a window over the entire
        # cohort_annotations table (an unscoped LoGoFunc CTE) twice per
        # search. Explain the assembled statement for every mode and reject
        # any full-table or full-index scan of a base table.
        import re
        base_table_scan = re.compile(
            r"^SCAN (source|cohort_\w+)\b|^SCAN \w+ USING (COVERING )?INDEX"
        )
        payloads = {
            "variant": {"mode": "variant", "query": "1:100:A:G"},
            "rsid": {"mode": "variant", "query": "rsExact"},
            "region": {"mode": "region", "region": "1:1-500"},
            "gene": {"mode": "gene", "gene": "NFKB1"},
            "gene_list": {"mode": "gene_list", "genes": ["NFKB1", "IL10RA"]},
        }
        for name, payload in payloads.items():
            explained = store.query(payload, explain_plan=True)
            for label in ("plan", "totals_plan"):
                offending = [
                    line for line in explained[label] if base_table_scan.match(line)
                ]
                self.assertEqual(
                    offending, [],
                    f"{name} {label} scans a base table: {offending}\n" + "\n".join(explained[label]),
                )
            # And the real query still answers.
            self.assertGreaterEqual(store.query(payload)["total"], 1, name)

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


class LegacyColumnReconciliationTests(unittest.TestCase):
    """Review follow-up: values indexed under the old semantics — a pooled
    frequency maximum, a PromoterAI score without provenance — must be
    corrected on upgrade and must not survive a re-import behind COALESCE."""

    PROVENANCE = dict(
        promoter_tss="50", promoter_strand="1",
        promoter_transcript="ENST_NFKB1.1", promoter_match="exact_version",
    )

    def _vcf(self, path: Path, *, promoter_score, with_provenance, popmax="0.0001"):
        consequence = csq(
            "G", 1, "missense_variant", "MODERATE", "NFKB1",
            "c.100A>G", "p.Lys34Arg", popmax, 25, 0.8, 0.01,
            promoter_score=promoter_score,
            **(self.PROVENANCE if with_provenance else {}),
        )
        path.write_text(
            "##fileformat=VCFv4.2\n"
            "##contig=<ID=1,length=248956422>\n"
            '##INFO=<ID=CSQ,Number=.,Type=String,Description="Format: '
            + "|".join(CSQ_FIELDS) + '">\n'
            '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n'
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tP1\n"
            f"1\t100\t.\tA\tG\t99\tPASS\tCSQ={consequence}\tGT\t0/1\n",
            encoding="utf-8",
        )

    @staticmethod
    def _row(database: Path):
        import sqlite3 as sqlite_module
        connection = sqlite_module.connect(database)
        connection.row_factory = sqlite_module.Row
        row = connection.execute(
            "SELECT promoterai, gnomad_popmax, gnomad_popmax_source "
            "FROM cohort_annotations WHERE gene = 'NFKB1'"
        ).fetchone()
        connection.close()
        return dict(row)

    def test_forced_reimport_clears_a_score_that_lost_its_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            vcf = root / "cohort.vcf"
            database = root / "cohort.sqlite3"
            self._vcf(vcf, promoter_score="0.9", with_provenance=True)
            store = CohortStore(database)
            store.import_paths([str(vcf)])
            self.assertEqual(self._row(database)["promoterai"], 0.9)

            # The same path is re-annotated: the score is now bare (no
            # provenance) -> withheld by the shared rule. The merge's
            # COALESCE used to keep the stale 0.9.
            time.sleep(0.01)
            self._vcf(vcf, promoter_score="0.9", with_provenance=False)
            os.utime(vcf, None)
            store.import_paths([str(vcf)], force=True)
            self.assertIsNone(self._row(database)["promoterai"])

            # And back: provenance restored -> score stored again.
            self._vcf(vcf, promoter_score="-0.7", with_provenance=True)
            os.utime(vcf, None)
            store.import_paths([str(vcf)], force=True)
            self.assertEqual(self._row(database)["promoterai"], -0.7)

    def test_a_second_file_with_exact_provenance_keeps_the_shared_row_scored(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "cohort.sqlite3"
            exact = root / "exact.vcf"
            bare = root / "bare.vcf"
            self._vcf(exact, promoter_score="0.9", with_provenance=True)
            self._vcf(bare, promoter_score="0.9", with_provenance=False)
            store = CohortStore(database)
            store.import_paths([str(exact)])
            store.import_paths([str(bare)])
            # Another file still contributes an exact observation for the
            # shared annotation row, so the score legitimately stays.
            self.assertEqual(self._row(database)["promoterai"], 0.9)

    def test_reopening_an_older_database_reconciles_legacy_values_once(self):
        import sqlite3 as sqlite_module
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            vcf = root / "cohort.vcf"
            database = root / "cohort.sqlite3"
            # Indexed under the new rule: bare score -> NULL, preferred
            # frequency with its source.
            self._vcf(vcf, promoter_score="0.9", with_provenance=False)
            store = CohortStore(database)
            store.import_paths([str(vcf)])
            self.assertEqual(
                self._row(database),
                {"promoterai": None, "gnomad_popmax": 0.0001,
                 "gnomad_popmax_source": "max_af"},
            )
            del store
            # Rewind the row to what the pre-fix importer stored: the
            # unverified score in the column, a pooled frequency maximum with
            # no recorded source, and no reconciliation stamp.
            connection = sqlite_module.connect(database)
            connection.execute(
                "UPDATE cohort_annotations SET promoterai = 0.9, "
                "gnomad_popmax = 0.04, gnomad_popmax_source = NULL"
            )
            connection.execute(
                "DELETE FROM cohort_meta WHERE key = 'legacy_column_format'"
            )
            connection.commit(); connection.close()

            reopened = CohortStore(database)
            # Ordinary reopen: the unverified score is gone; the pooled
            # frequency cannot be recomputed without the source record, so
            # it is kept but labelled as pre-upgrade pooled.
            self.assertEqual(
                self._row(database),
                {"promoterai": None, "gnomad_popmax": 0.04,
                 "gnomad_popmax_source": "legacy_pooled"},
            )
            with reopened._session() as connection:
                stamp = connection.execute(
                    "SELECT value FROM cohort_meta WHERE key = 'legacy_column_format'"
                ).fetchone()[0]
            self.assertEqual(stamp, "withheld-v1")
            # Ordinary re-import of the unchanged file is a no-op by design…
            reopened.import_paths([str(vcf)])
            self.assertEqual(self._row(database)["gnomad_popmax"], 0.04)
            # …a forced re-import replaces both the value and its label.
            reopened.import_paths([str(vcf)], force=True)
            self.assertEqual(
                self._row(database),
                {"promoterai": None, "gnomad_popmax": 0.0001,
                 "gnomad_popmax_source": "max_af"},
            )


class PopulationFrequencyAndPloidyTests(unittest.TestCase):
    """Review M9: frequency sources are preferred, never pooled; single-copy
    X/Y loci match homozygous and hemizygous searches alike."""

    def test_supplied_popmax_is_not_overridden_by_max_af(self):
        annotation = annotation_from({
            "gnomADe_AF_popmax": "0.001", "MAX_AF": "0.04", "gnomADe_AF": "0.0004",
        })
        self.assertEqual(annotation["gnomad_popmax"], 0.001)
        self.assertEqual(annotation["gnomad_popmax_source"], "gnomad_popmax")
        # Exome and genome popmax are both gnomAD popmax: max within the group.
        both = annotation_from({"gnomADe_AF_popmax": "0.001", "gnomADg_AF_popmax": "0.002"})
        self.assertEqual(both["gnomad_popmax"], 0.002)
        self.assertEqual(both["gnomad_popmax_source"], "gnomad_popmax")

    def test_max_af_is_used_and_labelled_only_when_no_popmax_exists(self):
        annotation = annotation_from({"MAX_AF": "0.04", "MAX_AF_POPS": "AFR", "gnomADe_AF": "0.0004"})
        self.assertEqual(annotation["gnomad_popmax"], 0.04)
        self.assertEqual(annotation["gnomad_popmax_source"], "max_af")
        global_only = annotation_from({"gnomADg_AF": "0.0003", "gnomADe_AF": "0.0004"})
        self.assertEqual(global_only["gnomad_popmax"], 0.0004)
        self.assertEqual(global_only["gnomad_popmax_source"], "gnomad_global")
        missing = annotation_from({"SYMBOL": "X"})
        self.assertIsNone(missing["gnomad_popmax"])
        self.assertEqual(missing["gnomad_popmax_source"], "")

    def test_single_copy_loci_are_recognised(self):
        self.assertTrue(single_copy_locus("X", 71_108_276))
        self.assertTrue(single_copy_locus("chrX", 71_108_276))
        self.assertTrue(single_copy_locus("Y", 2_800_000))
        self.assertFalse(single_copy_locus("X", 1_000_000))  # PAR1
        self.assertFalse(single_copy_locus("X", 155_800_000))  # PAR2
        self.assertFalse(single_copy_locus("1", 71_108_276))

    def test_hemizygous_and_homozygous_searches_meet_on_single_copy_loci(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            vcf = root / "ploidy.vcf"
            consequence = csq(
                "G", 1, "missense_variant", "MODERATE", "IL2RG",
                "c.1A>G", "p.Lys1Arg", 0.0001, 25, 0.8, 0.01,
            )
            autosomal = csq(
                "G", 1, "missense_variant", "MODERATE", "NFKB1",
                "c.1A>G", "p.Lys1Arg", 0.0001, 25, 0.8, 0.01,
            )
            vcf.write_text(
                "##fileformat=VCFv4.2\n"
                "##contig=<ID=1,length=248956422>\n"
                "##contig=<ID=X,length=156040895>\n"
                '##INFO=<ID=CSQ,Number=.,Type=String,Description="Format: '
                + "|".join(CSQ_FIELDS) + '">\n'
                '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n'
                "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tHAPLOID\tDIPLOID\n"
                # Autosome first (sorted order): never widened.
                f"1\t100\t.\tA\tG\t99\tPASS\tCSQ={autosomal}\tGT\t0/1\t1/1\n"
                # PAR1 X: diploid in everyone; a haploid call there stays
                # hemizygous and a 1/1 stays homozygous — no widening.
                f"X\t1000000\t.\tA\tG\t99\tPASS\tCSQ={consequence}\tGT\t1\t1/1\n"
                # Non-PAR X: one caller writes the male call haploid, the
                # other diploid-style.
                f"X\t71108276\t.\tA\tG\t99\tPASS\tCSQ={consequence}\tGT\t1\t1/1\n",
                encoding="utf-8",
            )
            store = CohortStore(root / "cohort.sqlite3")
            imported = store.import_paths([str(vcf)])
            self.assertEqual(imported["imported"], 1, imported)

            def hits(zygosity):
                result = store.query({
                    "mode": "gene", "gene": "IL2RG", "max_popmax": 0.01,
                    "zygosity": zygosity,
                })
                return sorted(
                    (row["pos"], row["sample"], row["zygosity"]) for row in result["rows"]
                )

            self.assertEqual(hits("hemizygous"), [
                (1000000, "HAPLOID", "hemizygous"),
                (71108276, "DIPLOID", "homozygous"),
                (71108276, "HAPLOID", "hemizygous"),
            ])
            self.assertEqual(hits("homozygous"), [
                (1000000, "DIPLOID", "homozygous"),
                (71108276, "DIPLOID", "homozygous"),
                (71108276, "HAPLOID", "hemizygous"),
            ])
            self.assertEqual(hits("heterozygous"), [])
            autosome = store.query({
                "mode": "gene", "gene": "NFKB1", "max_popmax": 0.01,
                "zygosity": "hemizygous",
            })
            self.assertEqual(autosome["rows"], [])
            plan = store.query({
                "mode": "gene", "gene": "IL2RG", "max_popmax": 0.01,
                "zygosity": "hemizygous",
            }, explain_plan=True)
            self.assertFalse(
                any("SCAN cohort_annotations" in step for step in plan["plan"]), plan
            )

            # Recorded sex narrows the widening where it is known (review
            # follow-up): once DIPLOID is recorded as female, its X 1/1 is
            # a genuine two-copy call and leaves the hemizygous search; the
            # homozygous search still lists it, and the haploid call stays
            # in both. Genotypes and stored classes are untouched.
            from local_service.phenotype_store import PhenotypeStore
            phenotypes = PhenotypeStore(root / "cohort.sqlite3")
            phenotypes.save_individual({
                "individual_id": "IND-DIPLOID", "sample_ids": ["DIPLOID"],
                "sex_at_birth": "female",
            })
            self.assertEqual(hits("hemizygous"), [
                (1000000, "HAPLOID", "hemizygous"),
                (71108276, "HAPLOID", "hemizygous"),
            ])
            self.assertEqual(hits("homozygous"), [
                (1000000, "DIPLOID", "homozygous"),
                (71108276, "DIPLOID", "homozygous"),
                (71108276, "HAPLOID", "hemizygous"),
            ])
            phenotypes.save_individual({
                "individual_id": "IND-DIPLOID", "sample_ids": ["DIPLOID"],
                "sex_at_birth": "male",
            })
            self.assertEqual(hits("hemizygous"), [
                (1000000, "HAPLOID", "hemizygous"),
                (71108276, "DIPLOID", "homozygous"),
                (71108276, "HAPLOID", "hemizygous"),
            ])
            phenotypes.save_individual({
                "individual_id": "IND-DIPLOID", "sample_ids": ["DIPLOID"],
                "sex_at_birth": "unknown",
            })
            self.assertEqual(len(hits("hemizygous")), 3)


def write_many_variant_vcf(
    path: Path, count: int, samples=("P1", "P2"), cadd: float = 25,
    promoter_score: str = "",
) -> None:
    """A plain VCF with ``count`` PASS variants carried by every sample."""
    lines = [
        "##fileformat=VCFv4.2",
        "##contig=<ID=1,length=248956422>",
        '##INFO=<ID=CSQ,Number=.,Type=String,Description="Format: ' + "|".join(CSQ_FIELDS) + '">',
        '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">',
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t" + "\t".join(samples),
    ]
    for index in range(count):
        consequence = csq(
            "G", 1, "missense_variant", "MODERATE", f"GENE{index % 7}",
            f"c.{index}A>G", f"p.Ala{index}Val", 0.0001, cadd, 0.8, 0.01,
            promoter_score=promoter_score,
            promoter_tss="1000" if promoter_score else "",
            promoter_strand="+" if promoter_score else "",
            promoter_transcript=f"ENST_GENE{index % 7}" if promoter_score else "",
            promoter_match="exact_version" if promoter_score else "",
        )
        lines.append(
            f"1\t{1000 + index * 2}\t.\tA\tG\t99\tPASS\tCSQ={consequence}\tGT\t"
            + "\t".join("0/1" for _ in samples)
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class ChunkedMergeTests(unittest.TestCase):
    """Audit M27: the final merge commits in bounded chunks, never exposes a
    partially merged file, survives interruption, and lets other writers
    through between chunks."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        # Resolved: the store records resolved paths, and macOS temp
        # directories live under a /var -> /private/var symlink.
        self.root = Path(self.temp.name).resolve()
        self.database = self.root / "cohort.sqlite3"
        self.store = CohortStore(self.database)
        self.vcf = self.root / "many.vcf"
        write_many_variant_vcf(self.vcf, 40)

    def tearDown(self):
        self.temp.cleanup()

    def _probe(self):
        connection = sqlite3.connect(self.database, timeout=5)
        connection.row_factory = sqlite3.Row
        try:
            files = connection.execute(
                "SELECT id, import_state, path FROM cohort_files ORDER BY id"
            ).fetchall()
            samples = connection.execute("SELECT COUNT(*) FROM cohort_samples").fetchone()[0]
            genotypes = connection.execute("SELECT COUNT(*) FROM cohort_genotypes").fetchone()[0]
            dangling = connection.execute(
                "SELECT COUNT(*) FROM cohort_genotypes WHERE sample_id NOT IN (SELECT id FROM cohort_samples)"
            ).fetchone()[0]
            return {
                "files": [dict(row) for row in files], "samples": samples,
                "genotypes": genotypes, "dangling": dangling,
            }
        finally:
            connection.close()

    def _journal_rows(self) -> int:
        from local_service.cohort_store import MERGE_JOURNALS
        connection = sqlite3.connect(self.database, timeout=5)
        try:
            return sum(
                connection.execute(f"SELECT COUNT(*) FROM {journal}").fetchone()[0]
                for journal in MERGE_JOURNALS
            )
        finally:
            connection.close()

    def _snapshot(self) -> dict:
        """Every stored row of the shared and per-sample tables, in a
        deterministic order and without surrogate ids, so two cohort states
        can be compared for equality."""
        connection = sqlite3.connect(self.database, timeout=5)
        connection.row_factory = sqlite3.Row
        try:
            def rows(sql):
                return [tuple(row) for row in connection.execute(sql).fetchall()]
            return {
                "variants": rows(
                    "SELECT variant_key, rsid, original_assembly, original_chrom, original_pos, "
                    "original_ref, original_alt, unscored_indel_reasons FROM cohort_variants "
                    "ORDER BY variant_key"
                ),
                "annotations": rows(
                    "SELECT v.variant_key, a.gene, a.transcript, a.hgvsc, a.hgvsp, a.consequence, "
                    "a.impact, a.gnomad_popmax, a.gnomad_popmax_source, a.cadd, a.alpha_missense, "
                    "a.spliceai, a.promoterai, a.clinvar, a.loftee, a.mane, a.picked, "
                    "a.repeat_masker, a.segdup FROM cohort_annotations a "
                    "JOIN cohort_variants v ON v.id = a.variant_id "
                    "ORDER BY v.variant_key, a.gene, a.transcript, a.hgvsc, a.hgvsp, a.consequence"
                ),
                "genotypes": rows(
                    "SELECT v.variant_key, s.name, g.genotype, g.zygosity FROM cohort_genotypes g "
                    "JOIN cohort_variants v ON v.id = g.variant_id "
                    "JOIN cohort_samples s ON s.id = g.sample_id ORDER BY v.variant_key, s.name"
                ),
                "observations": rows(
                    "SELECT v.variant_key, o.predictor_id, o.target_scope, o.target_key, "
                    "o.match_status, s.name, o.provenance_json, val.metric, val.numeric_value, "
                    "val.text_value FROM prediction_observations o "
                    "JOIN cohort_variants v ON v.id = o.variant_id "
                    "LEFT JOIN cohort_samples s ON s.id = o.sample_id "
                    "LEFT JOIN prediction_values val ON val.observation_id = o.id "
                    "ORDER BY v.variant_key, o.predictor_id, o.target_scope, o.target_key, val.metric"
                ),
                "samples": rows("SELECT file_id, name FROM cohort_samples ORDER BY file_id, name"),
                "files": rows("SELECT id, path, import_state, sample_count, variant_count FROM cohort_files ORDER BY id"),
            }
        finally:
            connection.close()

    def _secondary_indexes_present(self) -> bool:
        from local_service.cohort_store import COHORT_SECONDARY_INDEXES
        connection = sqlite3.connect(self.database, timeout=5)
        try:
            present = {
                row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'index'"
                )
            }
            return all(name in present for name in COHORT_SECONDARY_INDEXES)
        finally:
            connection.close()

    def test_evidence_reads_refuse_partial_shared_annotations_until_cleanup_finishes(self):
        from local_service import cohort_store as module
        from local_service.errors import CohortMergeBusyError
        self.store.import_vcf(self.vcf)
        sample_id = self.store.list_samples()[0]["id"]
        write_many_variant_vcf(self.vcf, 40, cadd=99)
        readers = [
            lambda: self.store.query({"mode": "gene", "gene": "GENE0"}),
            lambda: self.store.variant_detail("1:1000:A:G"),
            lambda: self.store.query_predictions(),
            lambda: self.store.filter_predictions(
                metric="cti", operator=">=", value=0.5, predictor_id="funcvep"),
            lambda: self.store.list_samples(),
            lambda: self.store.review_records([
                {"variant_key": "1:1000:A:G", "sample_entry_id": sample_id}]),
            lambda: self.store.sample_review_files([sample_id]),
        ]
        states = set()
        mixed_scores_seen = []

        def probe():
            with self.store._session() as connection:
                state, _ = self.store._pending_merge_state(connection)
                scores = {row[0] for row in connection.execute(
                    "SELECT DISTINCT cadd FROM cohort_annotations")}
            if not state:
                return
            states.add(state)
            mixed_scores_seen.append(scores == {25.0, 99.0})
            for read in readers:
                with self.assertRaises(CohortMergeBusyError):
                    read()
            # Progress remains available even when evidence reads are refused.
            self.store.stats()

        with patch.object(module, "MERGE_CHUNK_VARIANTS", 8), \
                patch.object(self.store, "_yield_to_waiting_writers", side_effect=probe):
            self.store.import_vcf(self.vcf, force=True)
        self.assertEqual(states, {"merging", "published"})
        self.assertTrue(any(mixed_scores_seen), "must exercise genuinely mixed shared rows")
        result = readers[0]()
        self.assertTrue(result["rows"])
        self.assertEqual({row["cadd"] for row in result["rows"]}, {99.0})
        self.assertEqual({row["cadd"] for row in readers[1]()["annotations"]}, {99.0})

    def test_reads_stay_blocked_during_rollback_then_return_old_scores(self):
        from local_service import cohort_store as module
        from local_service.errors import CohortMergeBusyError
        self.store.import_vcf(self.vcf)
        write_many_variant_vcf(self.vcf, 40, cadd=99)
        original_rollback = self.store._rollback_merge_journal
        checked = []

        def rollback(connection):
            with self.assertRaises(CohortMergeBusyError):
                self.store.query({"mode": "gene", "gene": "GENE0"})
            result = original_rollback(connection)
            # Even after rows are restored, orphan cleanup is still pending.
            with self.assertRaises(CohortMergeBusyError):
                self.store.query_predictions()
            checked.append(True)
            return result

        with patch.object(module, "MERGE_CHUNK_VARIANTS", 8), \
                patch.object(module.CohortStore, "_merge_stage_chunk", self._failing_after(module, 2)), \
                patch.object(self.store, "_rollback_merge_journal", side_effect=rollback):
            with self.assertRaises(RuntimeError):
                self.store.import_vcf(self.vcf, force=True)
        self.assertTrue(checked)
        rows = self.store.query({"mode": "gene", "gene": "GENE0"})["rows"]
        self.assertEqual({row["cadd"] for row in rows}, {25.0})

    def test_read_snapshot_survives_merge_start_and_nested_detail_queries(self):
        from local_service.errors import CohortMergeBusyError
        self.store.import_vcf(self.vcf)
        with self.store._read_session():
            # A writer commits AFTER the guard read, BEFORE evidence is read.
            # WAL allows this; nested reads must still use the old snapshot.
            with self.store._session() as connection:
                connection.execute("INSERT INTO cohort_meta(key, value) VALUES "
                                   "('merge_cleanup_pending', 'merging:123')")
                connection.execute("UPDATE cohort_annotations SET cadd = 99")
            result = self.store.variant_detail("1:1000:A:G")
            self.assertEqual({row["cadd"] for row in result["rows"]}, {25.0})
            self.assertEqual({row["cadd"] for row in result["annotations"]}, {25.0})

            def separate_request():
                with self.assertRaises(CohortMergeBusyError):
                    self.store.query_predictions()
                return True

            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=1) as pool:
                self.assertTrue(pool.submit(separate_request).result(timeout=5))
        with self.assertRaises(CohortMergeBusyError):
            self.store.query_predictions()
        with self.store._session() as connection:
            connection.execute("DELETE FROM cohort_meta WHERE key = 'merge_cleanup_pending'")
        self.assertEqual({row["cadd"] for row in self.store.variant_detail(
            "1:1000:A:G")["annotations"]}, {99.0})

    def test_legacy_merging_file_without_marker_also_blocks_reads(self):
        from local_service.errors import CohortMergeBusyError
        self.store.import_vcf(self.vcf)
        with self.store._session() as connection:
            connection.execute("UPDATE cohort_files SET import_state = 'merging'")
        with self.assertRaises(CohortMergeBusyError):
            self.store.query_predictions()

    def test_variant_detail_keeps_carriers_and_annotations_in_one_snapshot(self):
        self.store.import_vcf(self.vcf)
        query = self.store.query

        def query_then_writer(*args, **kwargs):
            result = query(*args, **kwargs)
            with self.store._session() as connection:
                connection.execute("UPDATE cohort_annotations SET cadd = 99")
            return result

        with patch.object(self.store, "query", side_effect=query_then_writer):
            result = self.store.variant_detail("1:1000:A:G")
        self.assertEqual({row["cadd"] for row in result["rows"]}, {25.0})
        self.assertEqual({row["cadd"] for row in result["annotations"]}, {25.0})

    def test_published_crash_keeps_read_guard_until_recovery_reconciles_evidence(self):
        from local_service import cohort_store as module
        from local_service.errors import CohortMergeBusyError
        write_many_variant_vcf(self.vcf, 40, promoter_score="0.4")
        self.store.import_vcf(self.vcf)
        write_many_variant_vcf(self.vcf, 40, cadd=99)
        cleanup = self.store._finish_merge_cleanup

        def crash_after_cleanup(connection):
            cleanup(connection)
            raise RuntimeError("power loss before final evidence reconciliation")

        with patch.object(self.store, "_finish_merge_cleanup", side_effect=crash_after_cleanup):
            with self.assertRaises(RuntimeError):
                self.store.import_vcf(self.vcf, force=True)
        with self.store._session() as connection:
            self.assertEqual(self.store._pending_merge_state(connection)[0], "published")
            self.assertGreater(connection.execute(
                "SELECT COUNT(*) FROM cohort_annotations WHERE promoterai IS NOT NULL"
            ).fetchone()[0], 0)
        with self.assertRaises(CohortMergeBusyError):
            self.store.variant_detail("1:1000:A:G")
        checked = []

        def probe():
            with self.assertRaises(CohortMergeBusyError):
                self.store.query_predictions()
            checked.append(True)

        with patch.object(module, "MERGE_CHUNK_VARIANTS", 8), \
                patch.object(module.CohortStore, "_yield_to_waiting_writers", side_effect=probe):
            reopened = CohortStore(self.database)
        self.assertTrue(checked)
        result = reopened.variant_detail("1:1000:A:G")
        self.assertEqual({row["cadd"] for row in result["annotations"]}, {99.0})
        self.assertTrue(all(row["promoterai"] is None for row in result["annotations"]))
        self.assertEqual(self._journal_rows(), 0)

    def test_merge_is_chunked_and_nothing_partial_is_visible(self):
        from local_service import cohort_store as module
        observed = []
        original = module.CohortStore.__dict__["_write_transaction"].__func__

        @contextmanager
        def observing(connection):
            with original(connection):
                yield
            observed.append(self._probe())

        with patch.object(module, "MERGE_CHUNK_VARIANTS", 8), \
                patch.object(module.CohortStore, "_write_transaction", staticmethod(observing)):
            result = self.store.import_paths([str(self.vcf)])
        self.assertEqual(result["imported"], 1)
        # 40 variants in chunks of 8 -> at least five chunk transactions plus
        # bookkeeping/publish: many short commits, not one long one.
        self.assertGreaterEqual(len(observed), 8)
        publish_index = next(
            index for index, snapshot in enumerate(observed) if snapshot["samples"] > 0
        )
        for snapshot in observed[:publish_index]:
            # Before publication: sample rows absent (so no reader can join
            # the growing genotype rows), and the file row is 'merging'
            # under a marker path no lookup matches.
            self.assertEqual(snapshot["samples"], 0)
            for row in snapshot["files"]:
                self.assertEqual(row["import_state"], "merging")
                self.assertNotEqual(row["path"], str(self.vcf))
        self.assertTrue(
            any(0 < snapshot["genotypes"] < 80 for snapshot in observed[:publish_index]),
            "genotypes must accumulate across earlier commits",
        )
        final = observed[-1]
        self.assertEqual(final["samples"], 2)
        self.assertEqual(final["genotypes"], 80)
        self.assertEqual(final["dangling"], 0)
        self.assertEqual([row["import_state"] for row in final["files"]], ["ready"])
        self.assertEqual(final["files"][0]["path"], str(self.vcf))
        stats = self.store.stats()
        self.assertEqual((stats["files"], stats["sample_entries"], stats["merging_files"]), (1, 2, 0))
        self.assertEqual(self.store.query({"mode": "gene", "gene": "GENE3"})["total"] > 0, True)

    def test_other_writers_get_through_between_chunks(self):
        """With a 0.3 s lock timeout, a writer must succeed on every attempt
        while a merge of many chunks (each slowed to 0.1 s) is running: the
        lock is released between chunks. Under the single-transaction merge
        the same probe waited for the whole merge and timed out."""
        from local_service import cohort_store as module
        original_chunk = module.CohortStore.__dict__["_merge_stage_chunk"]

        def slow_chunk(self_, *args, **kwargs):
            time.sleep(0.1)
            return original_chunk(self_, *args, **kwargs)

        failures = []
        successes = []
        stop = threading.Event()
        started = threading.Event()

        def writer():
            connection = sqlite3.connect(self.database, timeout=0.3)
            connection.execute("PRAGMA busy_timeout=300")
            while not stop.is_set():
                # What the service does around every mutating request.
                module.WRITE_COORDINATOR.enter()
                try:
                    connection.execute("BEGIN IMMEDIATE")
                    connection.execute(
                        "INSERT OR REPLACE INTO cohort_meta(key, value) VALUES ('probe', ?)",
                        (str(time.time()),),
                    )
                    connection.execute("COMMIT")
                    successes.append(time.time())
                except sqlite3.OperationalError as error:
                    failures.append(str(error))
                    try:
                        connection.execute("ROLLBACK")
                    except sqlite3.OperationalError:
                        pass
                finally:
                    module.WRITE_COORDINATOR.leave()
                started.set()
                time.sleep(0.02)
            connection.close()

        thread = threading.Thread(target=writer, daemon=True)
        thread.start()
        started.wait(2)
        with patch.object(module, "MERGE_CHUNK_VARIANTS", 2), \
                patch.object(module.CohortStore, "_merge_stage_chunk", slow_chunk):
            self.store.import_paths([str(self.vcf)])
        stop.set()
        thread.join(5)
        self.assertGreater(len(successes), 10)
        self.assertEqual(failures, [], f"a concurrent writer timed out during the merge: {failures[:3]}")

    def test_interrupted_merge_leaves_nothing_visible_and_recovers_on_open(self):
        from local_service import cohort_store as module
        original_chunk = module.CohortStore.__dict__["_merge_stage_chunk"]
        calls = {"n": 0}

        def failing_chunk(self_, *args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 3:
                raise RuntimeError("simulated disk failure mid-merge")
            return original_chunk(self_, *args, **kwargs)

        # 1. A failure the merge can clean up itself.
        with patch.object(module, "MERGE_CHUNK_VARIANTS", 5), \
                patch.object(module.CohortStore, "_merge_stage_chunk", failing_chunk):
            with self.assertRaises(RuntimeError):
                self.store.import_vcf(self.vcf)
        state = self._probe()
        self.assertEqual(state["files"], [])
        self.assertEqual((state["samples"], state["genotypes"], state["dangling"]), (0, 0, 0))
        self.assertEqual(self.store.stats()["variants"], 0)

        # 2. A hard crash: the process dies before the in-merge rollback runs.
        calls["n"] = 0

        def power_loss(self_, connection):
            raise sqlite3.OperationalError("power loss")

        with patch.object(module, "MERGE_CHUNK_VARIANTS", 5), \
                patch.object(module.CohortStore, "_merge_stage_chunk", failing_chunk), \
                patch.object(module.CohortStore, "_rollback_merge_journal", power_loss), \
                patch.object(module.CohortStore, "_finish_merge_cleanup", power_loss):
            with self.assertRaises(RuntimeError):
                self.store.import_vcf(self.vcf)
        crashed = self._probe()
        self.assertTrue(crashed["dangling"] > 0 or any(f["import_state"] == "merging" for f in crashed["files"]))
        self.assertGreater(self._journal_rows(), 0)
        # Reopening the database runs the recovery.
        reopened = CohortStore(self.database)
        recovered = self._probe()
        self.assertEqual(recovered["files"], [])
        self.assertEqual((recovered["samples"], recovered["genotypes"], recovered["dangling"]), (0, 0, 0))
        self.assertEqual(reopened.stats()["variants"], 0)
        self.assertEqual(self._journal_rows(), 0)
        # And the file imports cleanly afterwards.
        self.assertEqual(reopened.import_paths([str(self.vcf)])["imported"], 1)
        self.assertEqual(reopened.stats()["sample_entries"], 2)

    def test_forced_reimport_swaps_the_file_atomically_and_reclaims_old_rows(self):
        self.store.import_paths([str(self.vcf)])
        before = self._probe()
        old_id = before["files"][0]["id"]
        write_many_variant_vcf(self.vcf, 30, samples=("P1", "P3"))
        os.utime(self.vcf, None)
        from local_service import cohort_store as module
        seen_double = []
        original = module.CohortStore.__dict__["_write_transaction"].__func__

        @contextmanager
        def observing(connection):
            with original(connection):
                yield
            snapshot = self._probe()
            ready = [f for f in snapshot["files"] if f["import_state"] == "ready"]
            seen_double.append(len(ready))

        with patch.object(module, "MERGE_CHUNK_VARIANTS", 7), \
                patch.object(module.CohortStore, "_write_transaction", staticmethod(observing)):
            result = self.store.import_paths([str(self.vcf)], force=True)
        self.assertEqual(result["imported"], 1)
        # Exactly one published file at every commit: old until the swap, new after.
        self.assertEqual(set(seen_double), {1})
        after = self._probe()
        self.assertEqual(len(after["files"]), 1)
        self.assertNotEqual(after["files"][0]["id"], old_id)
        self.assertEqual(after["files"][0]["path"], str(self.vcf))
        self.assertEqual((after["samples"], after["genotypes"], after["dangling"]), (2, 60, 0))
        names = sorted(sample["name"] for sample in self.store.list_samples())
        self.assertEqual(names, ["P1", "P3"])
        self.assertEqual(self.store.stats()["variants"], 30)

    # -- review follow-up: a failed merge preserves the previous state ------

    def _failing_after(self, module, chunks_before_failure: int):
        original_chunk = module.CohortStore.__dict__["_merge_stage_chunk"]
        calls = {"n": 0}

        def failing_chunk(self_, *args, **kwargs):
            calls["n"] += 1
            if calls["n"] == chunks_before_failure + 1:
                raise RuntimeError("simulated disk failure mid-merge")
            return original_chunk(self_, *args, **kwargs)

        return failing_chunk

    def test_failed_forced_replacement_leaves_the_previous_cohort_untouched(self):
        """Reviewer reproduction: with the old file's annotations updated in
        place by the chunks, a replacement that failed after two chunks left
        8 of 40 annotations carrying the new file's scores. Every shared row
        must go back to its pre-image and every inserted row must go."""
        from local_service import cohort_store as module
        write_many_variant_vcf(self.vcf, 40, promoter_score="0.4")
        self.store.import_paths([str(self.vcf)])
        before = self._snapshot()
        self.assertEqual({row[9] for row in before["annotations"]}, {25.0})
        # Same loci with different scores, plus ten variants that are new.
        write_many_variant_vcf(self.vcf, 50, cadd=31, promoter_score="0.9")
        os.utime(self.vcf, None)
        with patch.object(module, "MERGE_CHUNK_VARIANTS", 5), \
                patch.object(module.CohortStore, "_merge_stage_chunk", self._failing_after(module, 2)):
            with self.assertRaises(RuntimeError):
                self.store.import_vcf(self.vcf, force=True)
        after = self._snapshot()
        self.assertEqual(after["annotations"], before["annotations"])
        self.assertEqual(after["variants"], before["variants"])
        self.assertEqual(after["genotypes"], before["genotypes"])
        self.assertEqual(after["observations"], before["observations"])
        self.assertEqual(after["samples"], before["samples"])
        self.assertEqual(after["files"], before["files"])
        self.assertEqual(self._probe()["dangling"], 0)
        self.assertEqual(self._journal_rows(), 0)
        # The only file's replacement dropped the secondary indexes in its
        # first transaction; the surviving rows must not stay unindexed.
        self.assertTrue(self._secondary_indexes_present())
        self.assertEqual(self.store.stats()["variants"], 40)
        # The retry then replaces the file completely.
        self.assertEqual(self.store.import_paths([str(self.vcf)], force=True)["imported"], 1)
        self.assertEqual({row[9] for row in self._snapshot()["annotations"]}, {31.0})
        self.assertEqual(self.store.stats()["variants"], 50)

    def test_failed_sample_restricted_reimport_keeps_the_addressed_sample(self):
        """Reviewer reproduction: the addressed sample's rows were removed
        before its replacement was written, so a failed restricted re-import
        of P1 left only P2. The old rows must survive until publication."""
        from local_service import cohort_store as module
        write_many_variant_vcf(self.vcf, 40, promoter_score="0.4")
        self.store.import_paths([str(self.vcf)])
        before = self._snapshot()
        self.assertEqual([name for _, name in before["samples"]], ["P1", "P2"])
        with patch.object(module, "MERGE_CHUNK_VARIANTS", 5), \
                patch.object(module.CohortStore, "_merge_stage_chunk", self._failing_after(module, 3)):
            with self.assertRaises(RuntimeError):
                self.store.import_vcf(self.vcf, restrict_samples=("P1",), force=True)
        after = self._snapshot()
        self.assertEqual(after, before)
        self.assertEqual(self._probe()["dangling"], 0)
        self.assertEqual(self._journal_rows(), 0)
        self.assertEqual(sorted(s["name"] for s in self.store.list_samples()), ["P1", "P2"])
        # A retry succeeds and P1 is indexed exactly once.
        self.store.import_vcf(self.vcf, restrict_samples=("P1",), force=True)
        final = self._snapshot()
        self.assertEqual(final["genotypes"], before["genotypes"])
        self.assertEqual(final["annotations"], before["annotations"])
        self.assertEqual(final["observations"], before["observations"])
        self.assertEqual([name for _, name in final["samples"]], ["P1", "P2"])
        self.assertEqual(self._probe()["dangling"], 0)

    def test_interrupted_replacement_is_rolled_back_on_the_next_open(self):
        """A crash after the chunks updated shared rows but before the
        in-process rollback ran: the journal survives in the database, and
        the next open restores the pre-images before anything reads."""
        from local_service import cohort_store as module
        self.store.import_paths([str(self.vcf)])
        before = self._snapshot()
        write_many_variant_vcf(self.vcf, 50, cadd=31)
        os.utime(self.vcf, None)

        def power_loss(self_, connection):
            raise sqlite3.OperationalError("power loss")

        with patch.object(module, "MERGE_CHUNK_VARIANTS", 5), \
                patch.object(module.CohortStore, "_merge_stage_chunk", self._failing_after(module, 3)), \
                patch.object(module.CohortStore, "_rollback_merge_journal", power_loss), \
                patch.object(module.CohortStore, "_finish_merge_cleanup", power_loss):
            with self.assertRaises(RuntimeError):
                self.store.import_vcf(self.vcf, force=True)
        crashed = self._snapshot()
        self.assertNotEqual(crashed["annotations"], before["annotations"])
        self.assertGreater(self._journal_rows(), 0)
        reopened = CohortStore(self.database)
        recovered = self._snapshot()
        self.assertEqual(recovered, before)
        self.assertEqual(self._journal_rows(), 0)
        self.assertEqual(self._probe()["dangling"], 0)
        self.assertEqual(reopened.stats()["variants"], 40)
        self.assertEqual(reopened.import_paths([str(self.vcf)], force=True)["imported"], 1)
        self.assertEqual(reopened.stats()["variants"], 50)


class CsqDecodingTests(unittest.TestCase):
    """Audit M31: the decode fast path and the zip-based CSQ parser must be
    byte-for-byte equivalent to the previous urllib-per-value form."""

    def test_decode_matches_unquote_exactly(self):
        from urllib.parse import unquote
        from local_service.cohort_store import decode
        for value in (
            "", None, "c.300+1G>C", "p.Lys34Ter", "a%2Cb", "100%25", "%3B%3D%26",
            "ENST00000357654.9", "x%zz", "%", "%2", "plain text with spaces",
        ):
            self.assertEqual(decode(value), unquote(value or ""), repr(value))

    def test_parse_csq_entries_pads_short_rows_and_decodes_every_field(self):
        from local_service.cohort_store import parse_csq_entries
        fields = ["Allele", "Consequence", "HGVSc", "Extra"]
        entries = parse_csq_entries("G|missense_variant|c.300%2B1G>C,T|stop_gained", fields)
        self.assertEqual(entries, [
            {"Allele": "G", "Consequence": "missense_variant", "HGVSc": "c.300+1G>C", "Extra": ""},
            {"Allele": "T", "Consequence": "stop_gained", "HGVSc": "", "Extra": ""},
        ])
        # Extra values beyond the header are ignored, as before.
        self.assertEqual(
            parse_csq_entries("G|x|y|z|overflow", fields),
            [{"Allele": "G", "Consequence": "x", "HGVSc": "y", "Extra": "z"}],
        )
        self.assertEqual(parse_csq_entries("", fields), [{}])
        self.assertEqual(parse_csq_entries("G|x", []), [{}])

    def test_staging_decodes_csq_only_for_carried_records(self):
        """A restricted re-index must not spend time on records the selected
        sample does not carry: the parser is invoked once per carried record."""
        from local_service import cohort_store as module
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            vcf = root / "joint.vcf"
            lines = [
                "##fileformat=VCFv4.2",
                "##contig=<ID=1,length=248956422>",
                '##INFO=<ID=CSQ,Number=.,Type=String,Description="Format: ' + "|".join(CSQ_FIELDS) + '">',
                '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">',
                "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tP1\tP2",
            ]
            consequence = csq("G", 1, "missense_variant", "MODERATE", "NFKB1",
                              "c.1A>G", "p.Ala1Val", 0.0001, 25, 0.8, 0.01)
            for index in range(10):
                p1 = "0/1" if index < 3 else "0/0"
                lines.append(f"1\t{100 + index}\t.\tA\tG\t99\tPASS\tCSQ={consequence}\tGT\t{p1}\t0/1")
            vcf.write_text("\n".join(lines) + "\n")
            header = module.read_vcf_header(vcf)
            calls = []
            original = module.parse_csq_entries

            def counting(raw, fields):
                calls.append(raw)
                return original(raw, fields)

            with patch.object(module, "parse_csq_entries", counting):
                result = module._stage_vcf_records(
                    iter(vcf.read_text().splitlines(keepends=True)), header,
                    root / "stage.sqlite3", sample_indices=(0,),
                )
            self.assertEqual(result["carrier_count"], 3)
            self.assertEqual(len(calls), 3, "CSQ decoded for non-carried records")
            calls.clear()
            (root / "stage.sqlite3").unlink()
            with patch.object(module, "parse_csq_entries", counting):
                result = module._stage_vcf_records(
                    iter(vcf.read_text().splitlines(keepends=True)), header,
                    root / "stage-all.sqlite3",
                )
            self.assertEqual(result["carrier_count"], 13)
            self.assertEqual(len(calls), 10)


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
