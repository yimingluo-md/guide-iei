"""End-to-end CSQ attribution on an allele with surviving carriers."""
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from local_service.cohort_store import ANNOTATION_PROPERTIES, SOURCE_SCORE_COLUMNS, CohortStore
from local_service.sample_library import SampleLibrary
from local_service.test_cohort_store import CSQ_FIELDS, csq


KEY = "1:100:A:G"
HIGH = {"mode": "gene", "gene": "GENE", "impacts": ["HIGH"],
        "exclude_repeat": False, "exclude_segdup": False}
SURVIVING = csq("G", 1, "missense_variant", "MODERATE", "GENE", "c.1", "p.1",
                0, 0, "", "", clinvar="Benign", lof="LC", mane="", picked="",
                loftee_50bp="PASS", loftee_50bp_changed="false", ptc_distance=0) + "|"
COMPETING = csq("G", 1, "missense_variant", "HIGH", "GENE", "c.1", "p.1",
                0.2, 38, 0.6, 0.8, clinvar="Pathogenic", lof="HC",
                repeat="LINE", segdup="1", loftee_50bp="FAIL", loftee_50bp_original="PASS",
                loftee_50bp_changed="1", ptc_distance=42, ptc_status="ok") + "|Pathogenic"
UNIQUE = COMPETING.replace("ENST_GENE", "ENST_OTHER").replace("missense_variant", "stop_gained")


def write_records(path, samples, records):
    header = [
        "##fileformat=VCFv4.2", "##reference=GRCh38", "##contig=<ID=1,length=248956422>",
        '##INFO=<ID=CSQ,Number=.,Type=String,Description="Format: '
        + "|".join([*CSQ_FIELDS, "ClinVar_CLNSIGCONF"]) + '">',
        '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">',
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t" + "\t".join(samples),
    ]
    rows = [f"1\t{pos}\t.\tA\tG\t99\tPASS\tCSQ={csqs}\tGT\t" + "\t".join(gts)
            for pos, csqs, gts in records]
    path.write_text("\n".join(header + rows) + "\n")


class AnnotationRetirementTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.db = self.root / "cohort.sqlite3"
        self.store = CohortStore(self.db)
        self.low = self.root / "low.vcf"
        self.high = self.root / "high.vcf"
        write_records(self.low, ["Low"], [(100, SURVIVING, ["0/1"])])
        write_records(self.high, ["High"], [(100, COMPETING + "," + UNIQUE, ["0/1"])])

    def annotations(self):
        return self.store.variant_detail(KEY)["annotations"]

    def high_samples(self):
        return {row["sample"] for row in self.store.query(HIGH)["rows"]}

    def assert_survivor(self):
        self.assertEqual(self.store.query({**HIGH, "mane_only": False})["total"], 0)
        self.assertEqual(self.high_samples(), set())
        row, = self.annotations()
        self.assertEqual(
            tuple(row[k] for k in ("transcript", "impact", "mane", "picked", "clinvar", "loftee",
                                    "loftee_50bp", "loftee_50bp_changed", "ptc_distance", "cadd",
                                    "gnomad_popmax", "gnomad_popmax_source", "repeat_masker", "segdup")),
            ("ENST_GENE", "MODERATE", False, False, "Benign", "LC",
             "PASS", False, 0, 0, 0, "max_af", False, False),
        )
        for field in ("alpha_missense", "spliceai", "ptc_calc_status", "loftee_50bp_original",
                      "clinvar_conflicting"):
            self.assertFalse(row[field], field)
        self.assertEqual(self.store.query({"mode": "gene", "gene": "GENE", "mane_only": False,
                                           "clinvar_pathogenic_only": True})["total"], 0)

    def test_force_reimport_changes_identity_and_competing_fields_in_both_importers(self):
        for rowwise in (False, True):
            with self.subTest(rowwise=rowwise), patch.dict(os.environ, {"IEI_COHORT_LEGACY_IMPORT": str(int(rowwise))}):
                self.store = CohortStore(self.root / f"{rowwise}.sqlite3")
                write_records(self.high, ["High"], [(100, COMPETING + "," + UNIQUE, ["0/1"])])
                self.store.import_vcf(self.low)
                self.store.import_vcf(self.high)
                self.assertEqual(self.high_samples(), {"Low", "High"})
                write_records(self.high, ["High"], [(110, COMPETING, ["0/1"])])
                self.store.import_vcf(self.high, force=True)
                self.assertEqual({r["variant_key"] for r in self.store.query(HIGH)["rows"]}, {"1:110:A:G"})
                # Retire the new HIGH allele; the original allele still carries Low.
                sample = next(s for s in self.store.list_samples() if s["name"] == "High")
                self.store.remove_samples([sample["id"]])
                self.assert_survivor()

    def test_remove_one_record_carrier_while_file_still_carries_the_same_allele(self):
        write_records(self.high, ["Low", "High", "AlsoHigh"], [
            (100, SURVIVING, ["0/1", "0/0", "0/0"]),
            (100, COMPETING + "," + UNIQUE, ["0/0", "0/1", "0/1"]),
        ])
        self.store.import_vcf(self.high)
        ids = {s["name"]: s["id"] for s in self.store.list_samples()}
        self.store.remove_samples([ids["AlsoHigh"]])
        self.assertEqual(self.high_samples(), {"Low", "High"})
        self.store.remove_samples([ids["High"]])
        self.assert_survivor()

    def test_retire_bound_observation_when_another_file_keeps_its_transcript(self):
        self.store.import_vcf(self.low)
        write_records(self.high, ["Retire", "Sibling"], [
            (100, COMPETING, ["0/1", "0/0"]),
            (100, SURVIVING.replace("ENST_GENE", "ENST_SIBLING"), ["0/0", "0/1"]),
        ])
        rich_id = self.store.import_vcf(self.high)["id"]

        def source_transcripts():
            return {o["transcript_id"] for o in self.store.query_predictions(predictor_id="loftee_ptc_50bp")
                    if o["source_file_id"] == rich_id}

        self.assertEqual(source_transcripts(), {"ENST_GENE", "ENST_SIBLING"})
        self.store.remove_samples([next(s["id"] for s in self.store.list_samples() if s["name"] == "Retire")])
        self.assertEqual(source_transcripts(), {"ENST_SIBLING"})
        self.assertEqual({r["transcript"] for r in self.annotations()}, {"ENST_GENE", "ENST_SIBLING"})
        self.assertEqual(self.high_samples(), set())

    def test_retiring_lower_impact_source_restores_previously_hidden_high(self):
        write_records(self.high, ["High"], [(100, COMPETING, ["0/1"])])
        self.store.import_vcf(self.high)
        self.store.import_vcf(self.low)
        self.assertEqual(self.high_samples(), set())
        low = next(s for s in self.store.list_samples() if s["name"] == "Low")
        self.store.remove_samples([low["id"]])
        self.assertEqual(self.high_samples(), {"High"})
        row, = self.annotations()
        self.assertTrue(row["mane"] and row["picked"] and row["loftee_50bp_changed"])
        self.assertEqual((row["clinvar"], row["loftee"], row["ptc_distance"]), ("Pathogenic", "HC", 42))

    def test_one_allele_replay_does_bounded_work_with_unrelated_transcripts(self):
        self.store.import_vcf(self.low)
        self.store.import_vcf(self.high)

        def replay_steps():
            with self.store._session() as c:
                c.execute("CREATE TEMP TABLE retiring_variants(id INTEGER PRIMARY KEY)")
                c.execute("INSERT INTO retiring_variants SELECT id FROM cohort_variants WHERE variant_key=?", (KEY,))
                ticks = 0

                def tick():
                    nonlocal ticks
                    ticks += 1
                    return 0

                c.set_progress_handler(tick, 10)
                self.store._restore_retiring_variants(c)
                c.set_progress_handler(None, 0)
                return ticks

        initial = replay_steps()
        unrelated = self.root / "unrelated.vcf"
        write_records(unrelated, ["Other"], [(pos, SURVIVING, ["0/1"]) for pos in range(1000, 2000)])
        self.store.import_vcf(unrelated)
        self.assertLess(replay_steps(), initial * 2)

    def test_interrupted_restricted_append_restores_shared_identity_and_snapshot(self):
        self.store.import_vcf(self.low)
        write_records(self.high, ["Indexed", "Add"], [
            (100, SURVIVING, ["0/1", "0/0"]),
            (100, COMPETING + "," + UNIQUE, ["0/0", "0/1"]),
            (200, COMPETING, ["0/0", "0/1"]),
        ])
        self.store.import_vcf(self.high, restrict_samples=["Indexed"])
        with self.store._session() as c:
            before = c.execute("SELECT * FROM cohort_annotation_evidence ORDER BY id").fetchall()
        with patch("local_service.cohort_store.MERGE_CHUNK_VARIANTS", 1), \
                patch.object(self.store, "_yield_to_waiting_writers", side_effect=RuntimeError("interrupted")), \
                patch.object(self.store, "_rollback_merge_journal", side_effect=sqlite3.OperationalError("offline")):
            with self.assertRaisesRegex(RuntimeError, "interrupted"):
                self.store.import_vcf(self.high, restrict_samples=["Add"])
        self.store = CohortStore(self.db)
        self.assert_survivor()
        with self.store._session() as c:
            self.assertEqual(c.execute("SELECT * FROM cohort_annotation_evidence ORDER BY id").fetchall(), before)
            self.assertEqual(c.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.store.import_vcf(self.high, restrict_samples=["Add"])
        self.assertEqual(self.high_samples(), {"Low", "Indexed", "Add"})

    def test_published_replacement_replays_after_restart(self):
        self.store.import_vcf(self.low)
        self.store.import_vcf(self.high)
        write_records(self.high, ["High"], [(100, SURVIVING, ["0/1"])])
        with patch.object(self.store, "_finish_merge_cleanup", side_effect=RuntimeError("interrupted")):
            with self.assertRaisesRegex(RuntimeError, "interrupted"):
                self.store.import_vcf(self.high, force=True)
        self.store = CohortStore(self.db)
        self.assert_survivor()
        self.assertEqual(self.store.query({"mode": "variant", "query": KEY})["individuals"], 2)

    def test_legacy_retirement_clears_unattributed_claims_until_ordinary_reimport(self):
        self.store.import_vcf(self.low)
        self.store.import_vcf(self.high)
        with self.store._session() as c:
            c.execute("DROP TABLE cohort_annotation_evidence")
            c.execute("DELETE FROM prediction_observations")
            c.execute("ALTER TABLE cohort_samples DROP COLUMN annotation_evidence_complete")
        self.store = CohortStore(self.db)
        self.assertEqual(self.store.stats()["annotation_reindex_samples"], 2)
        self.assertEqual(self.high_samples(), {"Low", "High"})
        # Even a sole remaining carrier does not establish which transcript
        # it contributed, nor its impact, flags, or clinical assertions.
        high = next(s for s in self.store.list_samples() if s["name"] == "High")
        self.store.remove_samples([high["id"]])
        row, = self.annotations()
        self.assertEqual((row["impact"], row["consequence"], row["transcript"]), ("UNKNOWN", "unannotated", ""))
        self.assertFalse(row["clinvar"])
        self.assertEqual(self.high_samples(), set())
        self.assertEqual(self.store.import_vcf(self.low)["status"], "imported")
        self.assertEqual(self.store.stats()["annotation_reindex_samples"], 0)
        self.assertEqual(self.store.import_vcf(self.low)["status"], "unchanged")
        self.assert_survivor()

    def test_score_only_database_keeps_attributable_zero_and_withholds_ambiguous_ownership(self):
        write_records(self.low, ["Keep", "Sibling"], [
            (100, SURVIVING, ["0/1", "0/1"]),
            (100, UNIQUE, ["0/0", "0/1"]),
        ])
        self.store.import_vcf(self.low)
        self.store.import_vcf(self.high)
        with self.store._session() as c:
            # Recreate the score-only format. It had file ids, but no
            # transcript properties or carrier attribution within the file.
            for row in c.execute("SELECT * FROM cohort_annotation_evidence"):
                fields = dict(zip(ANNOTATION_PROPERTIES, json.loads(row["properties"])))
                c.execute("INSERT OR IGNORE INTO cohort_source_scores VALUES(?,?,?)", (
                    row["annotation_id"], row["file_id"], json.dumps([fields[k] for k in SOURCE_SCORE_COLUMNS]),
                ))
            c.execute("DROP TABLE cohort_annotation_evidence")
            c.execute("ALTER TABLE cohort_samples DROP COLUMN annotation_evidence_complete")
        self.store = CohortStore(self.db)
        ids = {s["name"]: s["id"] for s in self.store.list_samples()}
        self.store.remove_samples([ids["High"]])
        summary = {r["transcript"]: r for r in self.annotations()}
        self.assertEqual(set(summary), {"ENST_GENE", "ENST_OTHER"})
        self.assertEqual(summary["ENST_GENE"]["cadd"], 0)
        self.assertTrue(all(row["impact"] == "UNKNOWN" and not row["mane"] for row in summary.values()))
        self.assertEqual(self.high_samples(), set())
        self.store.remove_samples([ids["Sibling"]])
        recovered, = self.annotations()
        self.assertEqual((recovered["transcript"], recovered["consequence"], recovered["cadd"]),
                         ("ENST_GENE", "missense_variant", 0))

    def test_legacy_recovery_scans_source_once_across_chunks(self):
        import builtins
        import local_service.cohort_store as module

        positions = range(100, 106)
        write_records(self.low, ["Keep", "Sibling"], [
            record for pos in positions for record in (
                (pos, SURVIVING, ["0/1", "0/0"]),
                (pos, UNIQUE, ["0/0", "0/1"]),
            )
        ])
        self.store.import_vcf(self.low)
        with self.store._session() as c:
            c.execute("DROP TABLE cohort_annotation_evidence")
            c.execute("ALTER TABLE cohort_samples DROP COLUMN annotation_evidence_complete")
        self.store = CohortStore(self.db)
        ids = {s["name"]: s["id"] for s in self.store.list_samples()}
        real_open, real_hash = builtins.open, module._full_content_sha256
        with patch("local_service.cohort_store.MERGE_CHUNK_VARIANTS", 2), \
                patch("builtins.open", wraps=real_open) as opened, \
                patch.object(module, "_full_content_sha256", wraps=real_hash) as hashed:
            self.store.remove_samples([ids["Sibling"]])
        source_opens = [call for call in opened.call_args_list if str(call.args[0]) == str(self.low)]
        self.assertLessEqual(len(source_opens), 2)  # header and record stream
        self.assertEqual(hashed.call_count, 1)
        for pos in positions:
            rows = self.store.variant_detail(f"1:{pos}:A:G")["annotations"]
            self.assertEqual([(r["transcript"], r["cadd"]) for r in rows], [("ENST_GENE", 0)])

    def test_legacy_recovery_rejects_same_size_changed_probe_without_sha(self):
        write_records(self.low, ["Keep", "Sibling"], [(100, SURVIVING, ["0/1", "0/1"])])
        self.store.import_vcf(self.low)
        with self.store._session() as c:
            c.execute("UPDATE cohort_files SET content_sha256=''")
            c.execute("DROP TABLE cohort_annotation_evidence")
            c.execute("ALTER TABLE cohort_samples DROP COLUMN annotation_evidence_complete")
        self.store = CohortStore(self.db)
        content = self.low.read_text()
        self.low.write_text(content.replace("missense_variant", "nonsense_variant"))
        ids = {s["name"]: s["id"] for s in self.store.list_samples()}
        self.store.remove_samples([ids["Sibling"]])
        row, = self.annotations()
        self.assertEqual((row["transcript"], row["consequence"], row["cadd"]), ("", "unannotated", None))

    def test_library_supersession_and_reactivation_on_a_shared_allele(self):
        self.store.import_vcf(self.low)
        library = SampleLibrary(self.root, self.store)
        payload = {"analysis_scope": "exome", "index_scope": "compact", "include_in_cohort": True}
        first = library.import_vcf(self.high, payload)["datasets"][0]
        self.assertEqual(self.high_samples(), {"Low", "High"})
        revised = self.root / "revised.vcf"
        write_records(revised, ["High"], [(100, SURVIVING, ["0/1"])])
        second = library.import_vcf(revised, payload)
        self.assertEqual(second["import_outcome"], "updated_annotation")
        self.assert_survivor()
        self.assertFalse(library.activate_version(first["id"])["already_current"])
        self.assertEqual(self.high_samples(), {"Low", "High"})
        library.activate_version(second["datasets"][0]["id"])
        self.assert_survivor()


class VariantMetadataRetirementTests(unittest.TestCase):
    key = "1:100:AT:A"
    fields = ("rsid", "unscored_indel_reasons", "original_assembly", "original_chrom",
              "original_pos", "original_ref", "original_alt")
    rich = ("rs12345", "coding_gap", "GRCh37", "chr1", 91, "AT", "A")
    sparse = (None,) * 7

    def write(self, path, samples, contributions):
        lines = ["##fileformat=VCFv4.2", "##reference=GRCh38", "##contig=<ID=1,length=248956422>",
                 '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">',
                 '##INFO=<ID=CSQ,Number=.,Type=String,Description="Format: Allele|ALLELE_NUM|Consequence|IMPACT|SYMBOL">',
                 ]
        for tag, number, kind in (("IEI_ORIGINAL_ASSEMBLY", "1", "String"), ("IEI_ORIGINAL_CHROM", "1", "String"),
                                  ("IEI_ORIGINAL_POS", "1", "Integer"), ("IEI_ORIGINAL_REF", "1", "String"),
                                  ("IEI_ORIGINAL_ALT", "A", "String"), ("IEI_UNSCORED_INDEL", "A", "String")):
            lines.append(f'##INFO=<ID={tag},Number={number},Type={kind},Description="{tag}">')
        lines.append("#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t" + "\t".join(samples))
        for meta, genotypes in contributions:
            info = ["CSQ=-|1|frameshift_variant|HIGH|GENE"]
            info.extend(f"{key}={value}" for key, value in zip(
                ("IEI_UNSCORED_INDEL", "IEI_ORIGINAL_ASSEMBLY", "IEI_ORIGINAL_CHROM", "IEI_ORIGINAL_POS",
                 "IEI_ORIGINAL_REF", "IEI_ORIGINAL_ALT"), meta[1:]) if value is not None)
            lines.append("\t".join(["1", "100", meta[0] or ".", "AT", "A", "99", "PASS",
                                    ";".join(info), "GT", *genotypes]))
        path.write_text("\n".join(lines) + "\n")

    def values(self, store):
        rows = store.query({"mode": "variant", "query": self.key})["rows"]
        self.assertTrue(rows)
        return {tuple(row[k] for k in self.fields) for row in rows}

    def test_removal_restores_only_supported_metadata_in_both_importers(self):
        for legacy in ("0", "1"):
            with self.subTest(legacy=legacy), tempfile.TemporaryDirectory() as temp, \
                    patch.dict(os.environ, {"IEI_COHORT_LEGACY_IMPORT": legacy}):
                d = Path(temp)
                store = CohortStore(d / "cohort.sqlite3")
                # Original locus is one source tuple. Newer rsIDs and reasons can win independently.
                sources = [("Plain", self.sparse), ("First", self.rich),
                           ("Second", ("rs67890", "other_reason", "GRCh38", None, 100, None, None))]
                for name, metadata in sources:
                    path = d / f"{name}.vcf"
                    self.write(path, [name], [(metadata, ["0/1"])])
                    store.import_vcf(path)
                ids = {s["name"]: s["id"] for s in store.list_samples()}
                self.assertEqual(self.values(store), {("rs67890", "other_reason", *self.rich[2:])})
                store.remove_samples([ids["First"]])
                self.assertEqual(self.values(store), {sources[2][1]})
                store.remove_samples([ids["Second"]])
                self.assertEqual(self.values(store), {self.sparse})
                self.assertEqual(store.query({"mode": "variant", "query": "rs67890"})["total"], 0)
                for name in ("First", "Second"):
                    store.import_vcf(d / f"{name}.vcf")
                store.remove_samples([next(s["id"] for s in store.list_samples() if s["name"] == "Second")])
                self.assertEqual(self.values(store), {self.rich})

    def test_shared_file_duplicate_records_retire_with_their_carriers(self):
        with tempfile.TemporaryDirectory() as temp:
            d = Path(temp)
            store = CohortStore(d / "cohort.sqlite3")
            path = d / "joint.vcf"
            self.write(path, ["Rich", "AlsoRich", "Plain"], [
                (self.rich, ["0/1", "0/1", "0/0"]), (self.sparse, ["0/0", "0/0", "0/1"])])
            store.import_vcf(path)
            ids = {s["name"]: s["id"] for s in store.list_samples()}
            store.remove_samples([ids["Rich"]])
            self.assertEqual(self.values(store), {self.rich})
            store.remove_samples([ids["AlsoRich"]])
            self.assertEqual(self.values(store), {self.sparse})
            store.import_vcf(path, restrict_samples=["Rich"])
            self.assertEqual(self.values(store), {self.rich})

    def test_interrupted_append_does_not_leave_a_metadata_contribution(self):
        with tempfile.TemporaryDirectory() as temp:
            d = Path(temp)
            path = d / "joint.vcf"
            store = CohortStore(d / "cohort.sqlite3")
            self.write(path, ["Plain", "Rich"], [(self.sparse, ["0/1", "0/0"]), (self.rich, ["0/0", "0/1"])])
            store.import_vcf(path, restrict_samples=["Plain"])
            with patch.object(store, "_yield_to_waiting_writers", side_effect=RuntimeError("interrupted")), \
                    patch.object(store, "_rollback_merge_journal", side_effect=sqlite3.OperationalError("offline")):
                with self.assertRaisesRegex(RuntimeError, "interrupted"):
                    store.import_vcf(path, restrict_samples=["Rich"])
            store = CohortStore(d / "cohort.sqlite3")
            self.assertEqual(self.values(store), {self.sparse})
            with store._session() as c:
                self.assertEqual(c.execute("SELECT COUNT(*) FROM cohort_variant_metadata").fetchone()[0], 0)
            store.import_vcf(path, restrict_samples=["Rich"])
            self.assertEqual(self.values(store), {self.rich})

    def test_upgrade_reindexes_unchanged_vcfs_to_recover_metadata(self):
        with tempfile.TemporaryDirectory() as temp:
            d = Path(temp)
            path = d / "source.vcf"
            self.write(path, ["Carrier"], [(self.rich, ["0/1"])])
            store = CohortStore(d / "cohort.sqlite3")
            store.import_vcf(path)
            with store._session() as c:
                c.execute("DROP TABLE cohort_variant_metadata")
                c.execute("ALTER TABLE cohort_samples DROP COLUMN variant_metadata_complete")
            store = CohortStore(d / "cohort.sqlite3")
            self.assertEqual(store.stats()["annotation_reindex_samples"], 1)
            store.import_vcf(path)
            self.assertEqual(store.stats()["annotation_reindex_samples"], 0)
            self.assertEqual(self.values(store), {self.rich})
            with store._session() as c:
                self.assertEqual(c.execute("SELECT COUNT(*) FROM cohort_variant_metadata").fetchone()[0], 1)
                self.assertEqual(c.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_published_replacement_recovers_after_restart(self):
        with tempfile.TemporaryDirectory() as temp:
            d = Path(temp)
            store = CohortStore(d / "cohort.sqlite3")
            for name, meta in (("Plain", self.sparse), ("Retire", self.rich)):
                path = d / f"{name}.vcf"
                self.write(path, [name], [(meta, ["0/1"])])
                store.import_vcf(path)
            path = d / "Retire.vcf"
            self.write(path, ["Retire"], [(self.sparse, ["0/1"])])
            with patch.object(store, "_finish_merge_cleanup", side_effect=RuntimeError("interrupted")):
                with self.assertRaisesRegex(RuntimeError, "interrupted"):
                    store.import_vcf(path, force=True)
            store = CohortStore(d / "cohort.sqlite3")
            self.assertEqual(self.values(store), {self.sparse})
            self.assertEqual(store.query({"mode": "variant", "query": self.rich[0]})["total"], 0)
            self.assertEqual(store.query({"mode": "variant", "query": self.key})["individuals"], 2)

    def test_original_alt_and_reason_are_allele_specific(self):
        with tempfile.TemporaryDirectory() as temp:
            d = Path(temp)
            path = d / "multi.vcf"
            store = CohortStore(d / "cohort.sqlite3")
            self.write(path, ["Carrier"], [(self.rich, ["1/2"])])
            path.write_text(path.read_text().replace("\tAT\tA\t99", "\tAT\tA,ATT\t99")
                            .replace("IEI_ORIGINAL_ALT=A\t", "IEI_ORIGINAL_ALT=A,.\t")
                            .replace("IEI_UNSCORED_INDEL=coding_gap;", "IEI_UNSCORED_INDEL=coding_gap,.;"))
            store.import_vcf(path)
            # The two Number=A fields supply only the first slot.
            self.assertEqual(self.values(store), {self.rich})
            row, = store.query({"mode": "variant", "query": "1:100:AT:ATT"})["rows"]
            self.assertIsNone(row["original_alt"])
            self.assertIsNone(row["unscored_indel_reasons"])
