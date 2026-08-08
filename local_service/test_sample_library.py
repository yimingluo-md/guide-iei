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
