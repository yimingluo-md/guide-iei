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

    def test_review_file_cache_keys_cannot_collide_across_sanitized_names(self):
        """Sample names that differ only in special characters (PAT/1 vs
        PAT?1) must never share a projection cache file — a collision serves
        one patient's variants under another patient's name."""
        from local_service.cohort_store import HtsBackend, read_vcf_header
        backend = HtsBackend.discover()
        if backend is None or not backend.native_tools.get("bcftools"):
            self.skipTest("native bcftools is required for sample projections")
        tricky = self.state / "tricky.vcf"
        tricky.write_text(
            "##fileformat=VCFv4.2\n"
            "##contig=<ID=1>\n"
            '##FILTER=<ID=PASS,Description="All filters passed">\n'
            '##INFO=<ID=CSQ,Number=.,Type=String,Description="Format: Allele">\n'
            '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n'
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tPAT 1\tPAT#1\n"
            "1\t100\t.\tA\tG\t50\tPASS\tCSQ=G\tGT\t0/1\t0/0\n"
            "1\t200\t.\tC\tT\t50\tPASS\tCSQ=T\tGT\t0/0\t0/1\n"
        )
        self.cohort.hts_backend = backend
        result = self.library.import_vcf(tricky, self.payload(include=False))
        by_sample = {d["vcf_sample_name"]: d for d in result["datasets"]}
        self.assertEqual(set(by_sample), {"PAT 1", "PAT#1"})
        first = self.library.review_file(by_sample["PAT 1"]["id"])
        second = self.library.review_file(by_sample["PAT#1"]["id"])
        self.assertNotEqual(first, second)
        self.assertEqual(read_vcf_header(first).samples, ("PAT 1",))
        self.assertEqual(read_vcf_header(second).samples, ("PAT#1",))

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

    def test_metadata_edit_never_relabels_the_co_resident_sample(self):
        """cohort_files carries one profile per indexed file; editing P1's
        capture kit must not rewrite the profile P2's cohort rows filter
        under."""
        import sqlite3
        result = self.library.import_vcf(self.vcf, self.payload(include=True))
        by_sample = {d["vcf_sample_name"]: d for d in result["datasets"]}
        with sqlite3.connect(self.database) as connection:
            before = connection.execute(
                "SELECT profile_label FROM cohort_files"
            ).fetchall()
        self.library.update_metadata(
            by_sample["P1"]["id"], {"capture_kit": "NewKit v9"}
        )
        record = self.library.get(by_sample["P1"]["id"])
        self.assertIn("NewKit v9", record["capture_kit"])
        with sqlite3.connect(self.database) as connection:
            after = connection.execute(
                "SELECT profile_label FROM cohort_files"
            ).fetchall()
        self.assertEqual(before, after)

    def test_import_and_listing_expose_cohort_sample_entry_identity(self):
        result = self.library.import_vcf(self.vcf, self.payload(include=True))
        by_name = {item["vcf_sample_name"]: item for item in result["datasets"]}
        self.assertEqual(set(by_name), {"P1", "P2"})
        self.assertTrue(all(item["cohort_sample_entry_id"] for item in by_name.values()))
        self.assertEqual(
            {item["vcf_sample_name"]: item["cohort_sample_entry_id"] for item in self.library.list()},
            {name: item["cohort_sample_entry_id"] for name, item in by_name.items()},
        )
        first = next(iter(by_name.values()))
        self.assertEqual(
            self.library.get(first["id"])["cohort_sample_entry_id"],
            first["cohort_sample_entry_id"],
        )

    def test_import_refuses_a_source_that_changes_mid_import(self):
        """The checksum and the managed copy are two reads of the source; a
        file still being written must fail the import loudly instead of
        being stored under the wrong content key."""
        original_prepare = self.cohort.prepare_managed_vcf

        def mutating_prepare(source, destination_directory, content_key):
            source.write_text(source.read_text() + "##mutated=1\n")
            return original_prepare(source, destination_directory, content_key)

        self.cohort.prepare_managed_vcf = mutating_prepare
        try:
            with self.assertRaisesRegex(ValueError, "changed while"):
                self.library.import_vcf(self.vcf, self.payload(include=False))
        finally:
            self.cohort.prepare_managed_vcf = original_prepare
        self.assertEqual(self.library.list(), [])

    def test_import_refuses_when_samples_change_between_header_and_checksum(self):
        """The sample names are read before the first checksum; a swap in
        that gap passed every checksum check while the dataset rows carried
        the OLD names over the NEW content — silently serving one patient's
        variants under another's name."""
        from local_service import sample_library as module
        swapped = self.state / "swapped-src.vcf"
        write_vcf(swapped)
        content = swapped.read_text().replace("P1", "Q1").replace("P2", "Q2")
        real = module.read_vcf_header
        state = {"swapped": False}

        def swap_after_first_read(path):
            header = real(path)
            if not state["swapped"] and Path(path) == self.vcf.resolve():
                state["swapped"] = True
                self.vcf.write_text(content)
            return header

        module.read_vcf_header = swap_after_first_read
        try:
            with self.assertRaisesRegex(ValueError, "changed while"):
                self.library.import_vcf(self.vcf, self.payload(include=False))
        finally:
            module.read_vcf_header = real
        self.assertEqual(self.library.list(), [])

    def test_corrupted_projection_cache_is_rebuilt_not_served(self):
        """The projection cache trusted existence + mtime alone: any
        overwrite advances the mtime, so garbage at the cache path was
        served verbatim. A cache hit must prove it parses as a VCF."""
        import gzip as _gzip
        from local_service.cohort_store import HtsBackend, read_vcf_header
        backend = HtsBackend.discover()
        if backend is None or not backend.native_tools.get("bcftools"):
            self.skipTest("native bcftools is required for sample projections")
        self.cohort.hts_backend = backend
        result = self.library.import_vcf(self.vcf, self.payload(include=False))
        by_sample = {d["vcf_sample_name"]: d for d in result["datasets"]}
        projected = self.library.review_file(by_sample["P1"]["id"])
        self.assertTrue(projected.name.endswith(".review.vcf.gz"))
        projected.write_bytes(b"NOT A VCF\n")
        again = self.library.review_file(by_sample["P1"]["id"])
        self.assertEqual(again, projected)
        header = read_vcf_header(again)
        self.assertEqual(header.samples, ("P1",))
        # Truncation preserves a parseable first block: the header check
        # alone served a 60%-truncated projection — the BGZF EOF-marker
        # check must catch it.
        intact = projected.read_bytes()
        projected.write_bytes(intact[: max(64, int(len(intact) * 0.6))])
        rebuilt = self.library.review_file(by_sample["P1"]["id"])
        self.assertEqual(rebuilt.read_bytes()[-28:], intact[-28:])
        # A valid VCF whose samples are WRONG for the cache key is the
        # worst corruption; the validator must reject it too.
        p2 = self.library.review_file(by_sample["P2"]["id"])
        projected.write_bytes(p2.read_bytes())
        served = self.library.review_file(by_sample["P1"]["id"])
        self.assertEqual(read_vcf_header(served).samples, ("P1",))

    def test_dedup_refreshes_the_original_path_from_the_new_source(self):
        """Re-importing identical content from the file's new location must
        update original_path — full-WGS reindexing reads it."""
        first = self.library.import_vcf(self.vcf, self.payload(include=False))
        moved = self.state / "moved" / "case.vcf"
        moved.parent.mkdir()
        moved.write_bytes(self.vcf.read_bytes())
        # A true move: the refresh HEALS dead paths, and deliberately never
        # clobbers a still-alive different location.
        self.vcf.unlink()
        again = self.library.import_vcf(moved, self.payload(include=False))
        self.assertTrue(again["deduplicated_file"])
        self.assertEqual(
            {d["id"] for d in again["datasets"]},
            {d["id"] for d in first["datasets"]},
        )
        record = self.library.get(first["datasets"][0]["id"])
        self.assertEqual(record["original_path"], str(moved.resolve()))
        # The location is a property of the CONTENT: rows imported under a
        # DIFFERENT profile must learn the live path too, or their full-WGS
        # reindex keeps reading the deleted file.
        other_payload = self.payload(include=False)
        other_payload["qc_settings"] = {"minDp": 30}
        other = self.library.import_vcf(moved, other_payload)
        moved2 = self.state / "moved-again" / "case.vcf"
        moved2.parent.mkdir()
        moved2.write_bytes(moved.read_bytes())
        moved.unlink()
        self.library.import_vcf(moved2, self.payload(include=False))
        stale_profile = self.library.get(other["datasets"][0]["id"])
        self.assertEqual(stale_profile["original_path"], str(moved2.resolve()))

    def test_cohort_indexing_failure_degrades_to_needs_repair_with_reason(self):
        """A cohort-indexing failure must not present as a failed import
        that half-landed: the datasets stay usable and carry the reason."""
        original = self.cohort.import_vcf

        def explode(*args, **kwargs):
            raise RuntimeError("synthetic cohort indexing failure")

        self.cohort.import_vcf = explode
        try:
            result = self.library.import_vcf(self.vcf, self.payload(include=True))
        finally:
            self.cohort.import_vcf = original
        self.assertEqual(len(result["datasets"]), 2)
        self.assertIsNone(result["cohort"])
        self.assertTrue(any("Repair Cohort Search" in w for w in result["warnings"]))
        record = self.library.get(result["datasets"][0]["id"])
        self.assertEqual(record["cohort_index_status"], "needs_repair")

    def test_failed_projection_never_publishes_a_partial_final_file(self):
        """A failed second-stage write must leave NO file at the final cache
        path — a partial final was previously trusted as a valid cache."""
        from local_service.cohort_store import HtsBackend
        backend = HtsBackend.discover()
        if backend is None or not backend.native_tools.get("bcftools"):
            self.skipTest("native bcftools is required for sample projections")
        result = self.library.import_vcf(self.vcf, self.payload(include=False))
        by_sample = {d["vcf_sample_name"]: d for d in result["datasets"]}

        class FailSecondStage:
            def __init__(self, inner):
                self.inner = inner
                self.native_tools = inner.native_tools
                self.calls = 0

            def run(self, tool, args):
                self.calls += 1
                if self.calls == 2:
                    class R:
                        returncode = 1
                        stderr = "synthetic carrier-stage failure"
                    return R()
                return self.inner.run(tool, args)

            def __getattr__(self, name):
                return getattr(self.inner, name)

        self.cohort.hts_backend = FailSecondStage(backend)
        try:
            with self.assertRaisesRegex(RuntimeError, "carrier filtering failed"):
                self.library.review_file(by_sample["P1"]["id"])
        finally:
            self.cohort.hts_backend = backend
        projected = [
            path for path in (self.state / "sample-library" / "files").glob("*.review.vcf.gz")
        ] if (self.state / "sample-library" / "files").is_dir() else []
        leftovers = [p for p in projected if ".staged." in p.name or ".partial." in p.name]
        self.assertEqual(leftovers, [])
        good = self.library.review_file(by_sample["P1"]["id"])
        self.assertTrue(good.is_file())

    def test_profile_takeover_marks_displaced_siblings_for_repair(self):
        """Reimporting the same file under a new profile replaces the single
        file-level cohort entry; the displaced datasets must surface as
        needing repair with the reason, not stay 'ready' over a dangling
        index."""
        first = self.library.import_vcf(self.vcf, self.payload(include=True))
        second_payload = self.payload(include=True)
        second_payload["qc_settings"] = {"minDp": 25, "minGq": 40}
        second = self.library.import_vcf(self.vcf, second_payload)
        self.assertNotEqual(
            {d["id"] for d in first["datasets"]},
            {d["id"] for d in second["datasets"]},
        )
        displaced = self.library.get(first["datasets"][0]["id"])
        self.assertEqual(displaced["cohort_index_status"], "needs_repair")
        self.assertTrue(any("marked for repair" in w for w in second["warnings"]))
        current = self.library.get(second["datasets"][0]["id"])
        self.assertEqual(current["cohort_index_status"], "ready")

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
        # Linkage repair still reaches the sibling, but "ready" now asserts
        # profile COHERENCE with the indexed file — the diverged sibling is
        # honestly shown as needing its own re-index, not silently ready
        # under someone else's profile.
        self.assertIsNotNone(sibling_after["cohort_file_id"])
        self.assertEqual(sibling_after["cohort_index_status"], "needs_repair")

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
