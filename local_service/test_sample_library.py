#!/usr/bin/env python3
import tempfile
import unittest
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from local_service.cohort_store import CohortStore
from local_service.phenotype_store import PhenotypeStore
from local_service.sample_library import SampleLibrary
from local_service.test_cohort_store import write_vcf


class SampleLibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.state = Path(self.temp.name)
        self.database = self.state / "cohort.sqlite3"
        self.cohort = CohortStore(self.database)
        self.phenotypes = PhenotypeStore(self.database)
        self.library = SampleLibrary(self.state, self.cohort)
        self.vcf = self.state / "case.vcf"
        write_vcf(self.vcf)

    def tearDown(self):
        self.temp.cleanup()

    def payload(self, include=True):
        return {
            "analysis_scope": "exome",
            "index_scope": "compact",
            "include_in_cohort": include,
            "capture_kit": "Test exome",
            "qc_settings": {"minDp": 10, "minGq": 20},
            "prefilter_settings": {},
            "retention_routes": ["exome region"],
            "source_record_count": 5,
            "retained_record_count": 4,
        }

    def test_review_file_projects_the_datasets_own_sample(self):
        """Opening a dataset from a multi-sample managed source hands the
        review its own sample only: one genotype column, carrier records
        only — never the whole cohort matrix."""
        from local_service.cohort_store import HtsBackend, read_vcf_header
        backend = HtsBackend.discover()
        result = self.library.import_vcf(self.vcf, self.payload(include=False))
        by_sample = {d["vcf_sample_name"]: d for d in result["datasets"]}
        self.assertEqual(set(by_sample), {"P1", "P2"})

        if backend is None:
            # Without htslib the stored file is served; the browser-side
            # guard turns that into directions instead of a freeze.
            self.assertEqual(
                self.library.review_file(by_sample["P1"]["id"]),
                self.library.file(by_sample["P1"]["id"]),
            )
            return
        if not backend.native_tools.get("bcftools"):
            self.skipTest(
                "no native bcftools: the container backend needs the built "
                "annotation image, which this environment does not have"
            )

        self.cohort.hts_backend = backend
        projected = self.library.review_file(by_sample["P1"]["id"])
        self.assertNotEqual(projected, self.library.file(by_sample["P1"]["id"]))
        header = read_vcf_header(projected)
        self.assertEqual(header.samples, ("P1",))
        import gzip
        with gzip.open(projected, "rt", encoding="utf-8") as handle:
            records = [line for line in handle if line.strip() and not line.startswith("#")]
        # P1 carries records 1 and 3 of the fixture; record 2 is 0/0 for P1.
        positions = {line.split("\t")[1] for line in records}
        self.assertIn("100", positions)
        self.assertNotIn("200", positions)
        # The projection is cached: a second open returns the same file.
        again = self.library.review_file(by_sample["P1"]["id"])
        self.assertEqual(again, projected)

    def test_review_file_combined_projects_selected_samples_or_serves_whole_file(self):
        from local_service.cohort_store import HtsBackend, read_vcf_header
        backend = HtsBackend.discover()
        result = self.library.import_vcf(self.vcf, self.payload(include=False))
        by_sample = {d["vcf_sample_name"]: d for d in result["datasets"]}

        # Selecting every sample of the file serves the stored file whole.
        whole = self.library.review_file_combined(
            [by_sample["P1"]["id"], by_sample["P2"]["id"]]
        )
        self.assertEqual(whole, self.library.file(by_sample["P1"]["id"]))

        # A single selection delegates to the per-sample projection.
        if backend is not None and backend.native_tools.get("bcftools"):
            self.cohort.hts_backend = backend
            single = self.library.review_file_combined([by_sample["P1"]["id"]])
            self.assertEqual(read_vcf_header(single).samples, ("P1",))

        with self.assertRaisesRegex(ValueError, "at least one dataset"):
            self.library.review_file_combined([])

    def test_bulk_apply_reports_per_item_outcomes(self):
        result = self.library.import_vcf(self.vcf, self.payload(include=False))
        ids = [d["id"] for d in result["datasets"]]
        report = self.library.bulk_apply(ids + ["no-such-dataset"], "cohort_add")
        # Both datasets share one managed file: indexing the first also
        # readies the second, which is then skipped as already present.
        self.assertEqual(report["succeeded"] + report["skipped"], 2)
        self.assertEqual(report["failures"][0]["id"], "no-such-dataset")
        # Idempotent: already-indexed datasets are skipped, not failed.
        again = self.library.bulk_apply(ids, "cohort_add")
        self.assertEqual(again["skipped"], 2)
        removed = self.library.bulk_apply(ids, "remove")
        self.assertEqual(removed["succeeded"], 2)
        self.assertEqual(self.library.list(), [])
        with self.assertRaisesRegex(ValueError, "remove or cohort_add"):
            self.library.bulk_apply(ids, "explode")

    def test_persistent_import_identity_profile_and_reopen(self):
        result = self.library.import_vcf(self.vcf, self.payload(include=True))
        self.assertEqual(len(result["datasets"]), 2)
        self.assertIn("WES/exome candidate", result["profile_label"])
        self.assertEqual(self.cohort.stats()["sample_entries"], 2)
        records = self.library.list()
        self.assertEqual(len(records), 2)
        self.assertEqual({record["cohort_index_status"] for record in records}, {"ready"})
        self.assertEqual(len({record["managed_path"] for record in records}), 1)
        self.assertTrue(self.library.file(records[0]["id"]).is_file())
        mapped = self.library.map_identity(records[0]["id"], {
            "mode": "create", "individual_id": "IND-001",
        })
        self.assertEqual(mapped["individual_id"], "IND-001")
        phenotype = self.library.phenotype(records[0]["id"])
        self.assertEqual(phenotype["individual_id"], "IND-001")
        self.assertEqual(phenotype["sample_ids"], [mapped["vcf_sample_name"]])
        self.assertEqual(phenotype["reported_race"], [])
        self.assertEqual(phenotype["present_features"], [])
        profiles = self.library.profiles()
        self.assertEqual(profiles[0]["datasets"], 2)
        self.assertEqual(profiles[0]["settings_hash"], records[0]["settings_hash"])
        cohort_profiles = self.cohort.profiles()
        self.assertEqual(cohort_profiles[0]["profile_hash"], records[0]["settings_hash"])
        matched = self.cohort.query({
            "mode": "variant", "query": "1:100:A:G",
            "profile_hashes": [records[0]["settings_hash"]],
            "analysis_scopes": ["exome"],
        })
        self.assertEqual(matched["individuals"], 1)
        self.assertEqual(matched["represented_profiles"], [records[0]["settings_hash"]])
        excluded = self.cohort.query({
            "mode": "variant", "query": "1:100:A:G",
            "analysis_scopes": ["whole_genome"],
        })
        self.assertEqual(excluded["rows"], [])

    def test_cohort_membership_is_state_driven_and_repairable(self):
        imported = self.library.import_vcf(self.vcf, self.payload(include=False))
        records = self.library.list()
        self.assertEqual({record["cohort_index_status"] for record in records}, {"not_included"})

        first_id = imported["datasets"][0]["id"]
        self.library.reindex(first_id)
        records = self.library.list()
        self.assertEqual({record["cohort_index_status"] for record in records}, {"ready"})

        first = self.library.get(first_id)
        removed = self.library.exclude_from_cohort(first_id)
        self.assertEqual(removed["cohort_index_status"], "not_included")
        self.assertEqual(removed["cohort_file_id"], None)
        self.assertIsNotNone(first["cohort_file_id"])

        remaining = next(record for record in self.library.list() if record["id"] != first_id)
        cohort_sample = next(
            item for item in self.cohort.list_samples()
            if item["file_id"] == remaining["cohort_file_id"]
            and item["name"] == remaining["vcf_sample_name"]
        )
        self.cohort.remove_samples([cohort_sample["id"]])
        self.assertEqual(self.library.get(remaining["id"])["cohort_index_status"], "needs_repair")

    def test_metadata_edit_touches_only_the_addressed_dataset(self):
        # Audit repro (SVC-1): managed_path is shared by both samples of a
        # multi-sample VCF; the capture-kit edit used to rewrite siblings.
        imported = self.library.import_vcf(self.vcf, self.payload(include=True))
        first, second = imported["datasets"]
        self.library.update_metadata(first["id"], {"capture_kit": "New kit"})
        self.assertEqual(self.library.get(first["id"])["capture_kit"], "New kit")
        self.assertEqual(self.library.get(second["id"])["capture_kit"], "Test exome")

    def test_metadata_profile_rewrite_touches_only_the_addressed_dataset(self):
        # The recomputed settings hash / profile label embed the edited
        # capture kit; a managed_path-scoped rewrite stamped THIS record's
        # profile onto every sibling dataset of a multi-sample VCF.
        imported = self.library.import_vcf(self.vcf, self.payload(include=True))
        first, second = imported["datasets"]
        sibling_before = self.library.get(second["id"])
        self.library.update_metadata(first["id"], {"capture_kit": "New kit"})
        edited = self.library.get(first["id"])
        sibling_after = self.library.get(second["id"])
        self.assertEqual(edited["complete_settings"]["capture_kit"], "New kit")
        self.assertNotEqual(edited["settings_hash"], sibling_after["settings_hash"])
        self.assertEqual(sibling_after["settings_hash"], sibling_before["settings_hash"])
        self.assertEqual(sibling_after["profile_label"], sibling_before["profile_label"])

    def test_reindex_profile_rewrite_spares_diverged_sibling_profile(self):
        # Non-full reindex repairs cohort linkage for every dataset sharing
        # the managed file, but must not overwrite a diverged sibling's
        # profile identity with this record's settings.
        imported = self.library.import_vcf(self.vcf, self.payload(include=True))
        first, second = imported["datasets"]
        self.library.update_metadata(second["id"], {"capture_kit": "Sibling kit"})
        sibling_before = self.library.get(second["id"])
        self.library.reindex(first["id"])
        sibling_after = self.library.get(second["id"])
        self.assertEqual(sibling_after["settings_hash"], sibling_before["settings_hash"])
        self.assertEqual(sibling_after["profile_label"], sibling_before["profile_label"])
        # Linkage repair still reaches the sibling.
        self.assertEqual(sibling_after["cohort_index_status"], "ready")

    def test_full_wgs_reindex_does_not_repoint_sibling_linkage(self):
        # The full index holds only this dataset's re-imported source; a
        # path-scoped update repointed siblings' cohort_file_id at the new
        # file while their rows stayed in the previous cohort file.
        wgs_payload = self.payload(include=True)
        wgs_payload["analysis_scope"] = "whole_genome"
        imported = self.library.import_vcf(self.vcf, wgs_payload)
        first, second = imported["datasets"]
        sibling_before = self.library.get(second["id"])
        original_import = self.cohort.import_vcf

        def forced_new_file_import(path, **kwargs):
            result = dict(original_import(path, **kwargs))
            result["id"] = "forced-new-cohort-file"
            return result

        self.cohort.import_vcf = forced_new_file_import
        try:
            self.library.reindex(first["id"], full_wgs=True)
        finally:
            self.cohort.import_vcf = original_import
        self.assertEqual(
            self.library.get(first["id"])["cohort_file_id"], "forced-new-cohort-file",
        )
        sibling_after = self.library.get(second["id"])
        self.assertEqual(
            sibling_after["cohort_file_id"], sibling_before["cohort_file_id"],
            "sibling linkage must keep pointing at the file that holds its rows",
        )
        self.assertEqual(sibling_after["index_scope"], sibling_before["index_scope"])
        self.assertEqual(sibling_after["settings_hash"], sibling_before["settings_hash"])

    def test_import_linkage_does_not_revert_sibling_exclusion(self):
        # Audit repro (SVC-5): a later import of the same managed VCF used to
        # force include_in_cohort=1 on every dataset sharing managed_path,
        # silently reverting a deliberate cohort exclusion.
        first_import = self.library.import_vcf(self.vcf, self.payload(include=True))
        excluded_id = first_import["datasets"][0]["id"]
        self.library.exclude_from_cohort(excluded_id)
        self.assertEqual(
            self.library.get(excluded_id)["cohort_index_status"], "not_included",
        )
        other_payload = self.payload(include=True)
        other_payload["qc_settings"] = {"minDp": 25, "minGq": 30}
        self.library.import_vcf(self.vcf, other_payload)
        self.assertEqual(
            self.library.get(excluded_id)["cohort_index_status"], "not_included",
        )

    def test_full_wgs_reindex_spares_sibling_cohort_samples(self):
        # Audit repro (SVC-2): reindex(full_wgs=True) used to delete EVERY
        # cohort sample of the previous cohort file, not just this dataset's.
        wgs_payload = self.payload(include=True)
        wgs_payload["analysis_scope"] = "whole_genome"
        imported = self.library.import_vcf(self.vcf, wgs_payload)
        first, second = imported["datasets"]
        sibling_before = self.library.get(second["id"])
        original_import = self.cohort.import_vcf

        def forced_new_file_import(path, **kwargs):
            result = dict(original_import(path, **kwargs))
            result["id"] = "forced-new-cohort-file"
            return result

        self.cohort.import_vcf = forced_new_file_import
        try:
            self.library.reindex(first["id"], full_wgs=True)
        finally:
            self.cohort.import_vcf = original_import
        surviving = {
            (item["file_id"], item["name"]) for item in self.cohort.list_samples()
        }
        self.assertIn(
            (sibling_before["cohort_file_id"], sibling_before["vcf_sample_name"]),
            surviving,
            "sibling sample's cohort entry must survive a co-resident reindex",
        )

    def test_review_once_equivalent_not_persisted_and_dedup_cleanup(self):
        self.assertEqual(self.library.list(), [])
        first = self.library.import_vcf(self.vcf, self.payload(include=False))
        second = self.library.import_vcf(self.vcf, self.payload(include=False))
        self.assertFalse(first["deduplicated_file"])
        self.assertTrue(second["deduplicated_file"])
        records = self.library.list()
        self.assertEqual(len(records), 2)
        managed = Path(records[0]["managed_path"])
        for record in records[:-1]:
            self.library.remove(record["id"])
            self.assertTrue(managed.exists())
        self.library.remove(records[-1]["id"])
        self.assertFalse(managed.exists())

    def test_storage_reports_reclaimable_categories(self):
        (self.state / "uploads" / "old").mkdir(parents=True)
        (self.state / "uploads" / "old" / "upload.vcf").write_text("temporary")
        before = self.library.storage_stats()
        self.assertGreater(before["locations"]["uploads"], 0)
        result = self.library.cleanup(["uploads", "partials"])
        self.assertGreaterEqual(result["removed_files"], 1)
        self.assertEqual(result["storage"]["locations"]["uploads"], 0)

    def test_same_specimen_can_have_multiple_dataset_versions(self):
        first = self.library.import_vcf(self.vcf, self.payload(include=False))
        rerun = self.state / "case-rerun.vcf"
        write_vcf(rerun)
        rerun.write_text(rerun.read_text().replace(
            "##fileformat=VCFv4.2\n", "##fileformat=VCFv4.2\n##source=rerun\n",
        ))
        second = self.library.import_vcf(rerun, self.payload(include=False))
        first_id = first["datasets"][0]["id"]
        second_id = second["datasets"][0]["id"]
        self.library.map_identity(first_id, {"mode": "create", "individual_id": "IND-002"})
        self.library.map_identity(second_id, {"mode": "existing", "individual_id": "IND-002"})
        records = {record["id"]: record for record in self.library.list()}
        self.assertEqual(records[first_id]["sample_id"], records[second_id]["sample_id"])


if __name__ == "__main__":
    unittest.main()
