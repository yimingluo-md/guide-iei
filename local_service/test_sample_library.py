#!/usr/bin/env python3
import tempfile
import unittest
from unittest import mock
import sqlite3
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from local_service.cohort_store import CohortStore
from local_service.phenotype_store import PhenotypeStore
from local_service.sample_library import SampleLibrary
from local_service.test_cohort_store import FakeHtsBackend, write_vcf


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

    def test_removing_a_dataset_deletes_its_review_projection(self):
        """Audit repro (M28): the per-sample projection (that individual's
        genotypes) stayed on disk forever after the dataset was removed —
        outside every cleanup category. Removing one of two datasets sharing
        a managed file deletes only that sample's projection; removing the
        last one deletes every projection of the file."""
        from local_service.cohort_store import HtsBackend
        backend = HtsBackend.discover()
        if backend is None or not backend.native_tools.get("bcftools"):
            self.skipTest("native bcftools is required for sample projections")
        self.cohort.hts_backend = backend
        result = self.library.import_vcf(self.vcf, self.payload(include=False))
        by_sample = {d["vcf_sample_name"]: d for d in result["datasets"]}
        p1 = self.library.review_file(by_sample["P1"]["id"])
        p2 = self.library.review_file(by_sample["P2"]["id"])
        self.assertTrue(p1.exists() and p2.exists())
        self.assertNotEqual(p1, p2)
        managed = self.library.file(by_sample["P1"]["id"])
        # A stale sibling projection (another sample name, same file) should
        # go with the last dataset, not linger.
        checksum = p1.name.split(".")[0]
        stray = p1.parent / f"{checksum}.OLD.deadbeef0000.review.vcf.gz"
        stray.write_bytes(b"stale projection")

        removed = self.library.remove(by_sample["P1"]["id"])
        self.assertIn(str(p1), removed["removed_files"])
        self.assertFalse(p1.exists(), "P1's projection must be deleted with P1")
        self.assertTrue(p2.exists(), "P2's projection must survive P1's removal")
        self.assertTrue(managed.exists(), "shared managed file survives")

        removed = self.library.remove(by_sample["P2"]["id"])
        self.assertFalse(p2.exists())
        self.assertFalse(managed.exists())
        self.assertFalse(stray.exists(), "every projection of the file goes with the last dataset")

    def test_removing_a_dataset_deletes_combined_projections_that_carried_it(self):
        """Review follow-up of M28: a combined (subset) projection is named
        by its selection, not its members, so removing P1 left the cached
        P1+P2 projection — P1's genotypes — on disk while P3's dataset still
        referenced the file, and ordinary cleanup preserved it too."""
        import gzip
        from local_service.cohort_store import HtsBackend
        backend = HtsBackend.discover()
        if backend is None or not backend.native_tools.get("bcftools"):
            self.skipTest("native bcftools is required for sample projections")
        self.cohort.hts_backend = backend
        # Three samples, so that P1+P2 is a subset of the file.
        lines = self.vcf.read_text(encoding="utf-8").splitlines()
        widened = []
        for line in lines:
            if line.startswith("#CHROM"):
                widened.append(line + "\tP3")
            elif line.startswith("#") or not line.strip():
                widened.append(line)
            else:
                widened.append(line + "\t0/1:10,10:20:60")
        trio = self.state / "trio.vcf"
        trio.write_text("\n".join(widened) + "\n", encoding="utf-8")
        result = self.library.import_vcf(trio, self.payload(include=False))
        by_sample = {d["vcf_sample_name"]: d for d in result["datasets"]}
        self.assertEqual(set(by_sample), {"P1", "P2", "P3"})
        combined = self.library.review_file_combined([by_sample["P1"]["id"], by_sample["P2"]["id"]])
        self.assertIn(".subset-", combined.name)
        self.assertTrue(combined.exists())
        p2 = self.library.review_file(by_sample["P2"]["id"])

        # A stale combined projection from an earlier build that removal did
        # not catch: ordinary cleanup must recognise it by its header.
        checksum = combined.name.split(".", 1)[0]
        stale = combined.with_name(f"{checksum}.subset-0123456789abcdef.review.vcf.gz")
        with gzip.open(stale, "wt", encoding="utf-8") as handle:
            handle.write("##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tP1\tGHOST\n")
        stale_index = stale.with_name(stale.name + ".tbi")
        stale_index.write_bytes(b"index")
        summary = self.library.cleanup(["uploads"])
        self.assertFalse(stale.exists(), "a projection carrying a sample no dataset has is orphaned")
        self.assertFalse(stale_index.exists())
        self.assertTrue(combined.exists(), "a projection of current samples is not")
        self.assertTrue(p2.exists())

        removed = self.library.remove(by_sample["P1"]["id"])
        self.assertIn(str(combined), removed["removed_files"])
        self.assertFalse(combined.exists(), "the P1+P2 projection carried P1's genotypes")
        self.assertTrue(p2.exists(), "P2's own projection survives")
        self.assertTrue(self.library.file(by_sample["P3"]["id"]).exists())
        # Rebuilt on demand for the remaining samples.
        again = self.library.review_file_combined([by_sample["P2"]["id"], by_sample["P3"]["id"]])
        self.assertTrue(again.exists())

    def test_cleanup_reclaims_review_projections(self):
        """Audit M28 (cleanup half): completed projections are a rebuildable
        cache. The "projections" category removes them all; any other cleanup
        reaps the orphaned ones (no dataset references their managed file);
        the Storage figures report them."""
        result = self.library.import_vcf(self.vcf, self.payload(include=False))
        checksum = self.library.file(result["datasets"][0]["id"]).name.split(".", 1)[0]
        files = self.library.files_dir
        live = files / f"{checksum}.GRCh38.abcdef012345.review.vcf.gz"
        live_index = files / f"{checksum}.GRCh38.abcdef012345.review.vcf.gz.tbi"
        orphan = files / "0000deadbeef.GRCh38.abcdef012345.review.vcf.gz"
        for path in (live, live_index, orphan):
            path.write_bytes(b"projection bytes")
        stats = self.library.storage_stats()
        self.assertEqual(stats["locations"]["projections"], 3 * len(b"projection bytes"))

        # Uploads cleanup alone: only the orphan goes.
        summary = self.library.cleanup(["uploads"])
        self.assertFalse(orphan.exists())
        self.assertTrue(live.exists() and live_index.exists())
        self.assertGreaterEqual(summary["removed_files"], 1)

        # The projections category reclaims the live cache too (rebuilt on demand).
        self.library.cleanup(["projections"])
        self.assertFalse(live.exists())
        self.assertFalse(live_index.exists())
        self.assertEqual(self.library.storage_stats()["locations"]["projections"], 0)
        # The managed VCF itself is untouched.
        self.assertTrue(self.library.file(result["datasets"][0]["id"]).exists())

    def test_original_review_record_restores_transcripts_omitted_from_managed_vcf(self):
        fields = "Allele|Consequence|IMPACT|SYMBOL|Feature|HGVSp|MANE_SELECT|PICK"
        header = (
            "##fileformat=VCFv4.2\n"
            "##reference=GRCh38\n"
            "##contig=<ID=1,length=248956422>\n"
            f'##INFO=<ID=CSQ,Number=.,Type=String,Description="Format: {fields}">\n'
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tP1\n"
        )
        mane = "G|missense_variant|MODERATE|GENE1|ENST_MANE|p.Gly1Asp|NM_1|1"
        alternative = "G|missense_variant|MODERATE|GENE1|ENST_ALT|p.Gly1Asp||"
        original = self.state / "original.vcf"
        original.write_text(
            header + f"1\t100\t.\tA\tG\t50\tPASS\tCSQ={mane},{alternative}\tGT\t0/1\n",
            encoding="utf-8",
        )
        managed = self.state / "managed.vcf"
        managed.write_text(
            header + f"1\t100\t.\tA\tG\t50\tPASS\tCSQ={mane}\tGT\t0/1\n",
            encoding="utf-8",
        )
        payload = {
            **self.payload(include=False),
            "original_path": str(original),
            "original_name": original.name,
        }
        result = self.library.import_vcf(managed, payload)
        dataset = result["datasets"][0]
        self.cohort.hts_backend = FakeHtsBackend()
        restored = self.library.original_review_record(dataset["id"], "1:100:A:G")
        self.assertEqual(restored["sample"], "P1")
        self.assertIn(f"CSQ={mane},{alternative}", restored["vcf"])
        self.assertEqual(restored["vcf"].count("\n1\t100\t"), 1)

    def test_original_review_record_matches_padded_representations(self):
        """Audit repro (H5): the stored record can be padded (REF=AT ALT=GT)
        while the canonical key is 1:100:A:G; the lookup must compare
        canonical alleles, not raw columns."""
        fields = "Allele|Consequence|IMPACT|SYMBOL|Feature|HGVSp|MANE_SELECT|PICK"
        header = (
            "##fileformat=VCFv4.2\n"
            "##reference=GRCh38\n"
            "##contig=<ID=1,length=248956422>\n"
            f'##INFO=<ID=CSQ,Number=.,Type=String,Description="Format: {fields}">\n'
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tP1\n"
        )
        csq = "GT|missense_variant|MODERATE|GENE1|ENST_MANE|p.Gly1Asp|NM_1|1"
        original = self.state / "padded-original.vcf"
        original.write_text(header + f"1\t100\t.\tAT\tGT\t50\tPASS\tCSQ={csq}\tGT\t0/1\n")
        managed = self.state / "padded-managed.vcf"
        managed.write_text(header + f"1\t100\t.\tAT\tGT\t50\tPASS\tCSQ={csq}\tGT\t0/1\n")
        result = self.library.import_vcf(managed, {
            **self.payload(include=False),
            "original_path": str(original), "original_name": original.name,
        })
        self.cohort.hts_backend = FakeHtsBackend()
        for query in ("1:100:A:G", "1:100:AT:GT", "chr1-100-at-gt"):
            restored = self.library.original_review_record(result["datasets"][0]["id"], query)
            self.assertEqual(restored["variant_key"], "1:100:A:G", query)
            self.assertIn("\n1\t100\t.\tAT\tGT\t", restored["vcf"], query)

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
        # A changed intake setting still reuses exact content and its stored
        # provenance; healing must continue to follow a later real move.
        other_payload = self.payload(include=False)
        other_payload["qc_settings"] = {"minDp": 30}
        other = self.library.import_vcf(moved, other_payload)
        moved2 = self.state / "moved-again" / "case.vcf"
        moved2.parent.mkdir()
        moved2.write_bytes(moved.read_bytes())
        moved.unlink()
        self.library.import_vcf(moved2, self.payload(include=False))
        reused = self.library.get(other["datasets"][0]["id"])
        self.assertEqual(reused["original_path"], str(moved2.resolve()))

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

    def test_exact_reimport_with_changed_settings_reuses_stored_profile(self):
        """Screen settings cannot turn identical bytes into a duplicate.

        Exact re-import reuses both stable dataset IDs and their original
        provenance. A changed setting applies only to a genuinely new
        prepared review file.
        """
        first = self.library.import_vcf(self.vcf, self.payload(include=True))
        original = self.library.get(first["datasets"][0]["id"])
        second_payload = self.payload(include=True)
        second_payload["qc_settings"] = {"minDp": 25, "minGq": 40}
        second = self.library.import_vcf(self.vcf, second_payload)
        self.assertEqual(second["import_outcome"], "exact_current")
        self.assertEqual(
            {d["id"] for d in first["datasets"]},
            {d["id"] for d in second["datasets"]},
        )
        self.assertEqual(len(self.library.list()), 2)
        current = self.library.get(first["datasets"][0]["id"])
        self.assertEqual(current["cohort_index_status"], "ready")
        self.assertEqual(current["settings_hash"], original["settings_hash"])
        self.assertEqual(current["qc_settings"], original["qc_settings"])

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

    def test_removed_cohort_entry_never_rebinds_to_a_later_dataset(self):
        """Audit repro (H1): cohort_files.id was reused after deletion and the
        library kept the dangling id, so a LATER dataset with the same VCF
        sample name resolved as the removed one — and removing the removed
        one then deleted the later dataset's cohort rows."""
        # A single-sample dataset: removing its only entry deletes the whole
        # cohort_files row, which is exactly when SQLite hands the id out again.
        single = self.state / "single-case.vcf"
        single.write_text("".join(
            (line.rsplit("\t", 1)[0] + "\n") if line.startswith("#CHROM") or not line.startswith("#") else line
            for line in self.vcf.read_text().splitlines(keepends=True)
        ))
        imported_a = self.library.import_vcf(single, self.payload(include=True))
        self.assertEqual([d["vcf_sample_name"] for d in imported_a["datasets"]], ["P1"])
        a = self.library.get(imported_a["datasets"][0]["id"])
        a_file_id = a["cohort_file_id"]
        self.assertIsNotNone(a_file_id)

        # Remove A's entry through the Cohort Search manager path (cohort
        # store only), exactly as the HTTP endpoint used to do it.
        a_entry = next(
            item for item in self.cohort.list_samples()
            if item["file_id"] == a_file_id and item["name"] == "P1"
        )
        identities = self.library.cohort_entry_identities([a_entry["id"]])
        self.assertEqual(identities, [(a_file_id, "P1")])
        self.cohort.remove_samples([a_entry["id"]])
        self.assertEqual(self.library.get(a["id"])["cohort_index_status"], "needs_repair")

        # A separate dataset B: different content (so it is not treated as
        # an exact re-import of A's callset) but the same sample name P1.
        other = self.state / "other-case.vcf"
        write_vcf(other)
        other.write_text(other.read_text().replace("\t99\tPASS", "\t98\tPASS"))
        imported_b = self.library.import_vcf(
            other, {**self.payload(include=True), "identity_action": "separate"}
        )
        b = self.library.get(next(
            d for d in imported_b["datasets"] if d["vcf_sample_name"] == "P1"
        )["id"])
        self.assertEqual(b["cohort_index_status"], "ready")

        # The id A pointed at must never be handed out again ...
        self.assertNotEqual(b["cohort_file_id"], a_file_id)
        self.assertGreater(b["cohort_file_id"], a_file_id)
        # ... so A stays needs_repair instead of silently resolving as B.
        a_now = self.library.get(a["id"])
        self.assertEqual(a_now["cohort_index_status"], "needs_repair")
        self.assertIsNone(a_now.get("cohort_sample_entry_id"))
        # And removing A cannot touch B's cohort rows.
        self.library.remove(a["id"])
        self.assertEqual(self.library.get(b["id"])["cohort_index_status"], "ready")

    def test_cohort_manager_removal_detaches_library_linkage(self):
        imported = self.library.import_vcf(self.vcf, self.payload(include=True))
        p1 = self.library.get(next(
            d for d in imported["datasets"] if d["vcf_sample_name"] == "P1"
        )["id"])
        entry = next(
            item for item in self.cohort.list_samples()
            if item["file_id"] == p1["cohort_file_id"] and item["name"] == "P1"
        )
        identities = self.library.cohort_entry_identities([entry["id"]])
        self.cohort.remove_samples([entry["id"]])
        self.assertEqual(self.library.detach_cohort_entries(identities), 1)
        record = self.library.get(p1["id"])
        self.assertIsNone(record["cohort_file_id"])
        self.assertTrue(record["include_in_cohort"])
        self.assertEqual(record["cohort_index_status"], "needs_repair")
        # The sibling dataset (P2) in the same file is untouched.
        p2 = next(d for d in imported["datasets"] if d["vcf_sample_name"] == "P2")
        self.assertEqual(self.library.get(p2["id"])["cohort_index_status"], "ready")

    def test_upgraded_database_never_reissues_a_dangling_library_id(self):
        """Audit repro (H1, P1): a database created before the allocator
        existed can hold a library row whose cohort_file_id names a cohort
        file that is already gone. Seeding the allocator from cohort_files
        alone re-issued that id on upgrade, re-binding the removed dataset
        to the next import."""
        single = self.state / "single-legacy.vcf"
        single.write_text("".join(
            (line.rsplit("\t", 1)[0] + "\n") if line.startswith("#CHROM") or not line.startswith("#") else line
            for line in self.vcf.read_text().splitlines(keepends=True)
        ))
        a = self.library.get(self.library.import_vcf(single, self.payload(include=True))["datasets"][0]["id"])
        a_file_id = a["cohort_file_id"]
        entry = next(
            item for item in self.cohort.list_samples()
            if item["file_id"] == a_file_id and item["name"] == "P1"
        )
        # Old removal path: the cohort index is emptied, the library keeps
        # its dangling id, and the database predates cohort_meta.
        self.cohort.remove_samples([entry["id"]])
        with sqlite3.connect(self.database) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM cohort_files").fetchone()[0], 0)
            connection.execute("DROP TABLE cohort_meta")
        upgraded = CohortStore(self.database)
        library = SampleLibrary(self.state, upgraded)
        other = self.state / "other-legacy.vcf"
        write_vcf(other)
        other.write_text(other.read_text().replace("\t99\tPASS", "\t98\tPASS"))
        b = library.get(next(
            d for d in library.import_vcf(other, {**self.payload(include=True), "identity_action": "separate"})["datasets"]
            if d["vcf_sample_name"] == "P1"
        )["id"])
        self.assertGreater(b["cohort_file_id"], a_file_id)
        self.assertEqual(library.get(a["id"])["cohort_index_status"], "needs_repair")
        self.assertIsNone(library.get(a["id"]).get("cohort_sample_entry_id"))
        library.remove(a["id"])
        self.assertEqual(library.get(b["id"])["cohort_index_status"], "ready")

    def test_cohort_file_ids_survive_a_full_reset(self):
        imported = self.library.import_vcf(self.vcf, self.payload(include=True))
        before = max(
            self.library.get(d["id"])["cohort_file_id"] for d in imported["datasets"]
        )
        # Dropping and recreating the cohort tables resets sqlite_sequence
        # too; the allocator must not.
        self.cohort._reset_cohort_tables()
        other = self.state / "after-reset.vcf"
        write_vcf(other)
        other.write_text(other.read_text().replace("\t99\tPASS", "\t98\tPASS"))
        reimported = self.library.import_vcf(
            other, {**self.payload(include=True), "identity_action": "separate"}
        )
        after = min(
            self.library.get(d["id"])["cohort_file_id"] for d in reimported["datasets"]
        )
        self.assertGreater(after, before)

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

    def test_full_wgs_reindex_indexes_only_the_addressed_sample(self):
        """Audit repro (H2), REAL importer: the full-WGS reindex imported the
        whole original multi-sample VCF, so the sibling ended up in both the
        prefiltered file and the new full file — counted twice in Cohort
        Search. The two mocked tests above could not see this."""
        wgs_payload = self.payload(include=True)
        wgs_payload["analysis_scope"] = "whole_genome"
        imported = self.library.import_vcf(self.vcf, wgs_payload)
        by_name = {d["vcf_sample_name"]: d for d in imported["datasets"]}
        p1, p2 = by_name["P1"], by_name["P2"]
        p2_before = self.library.get(p2["id"])

        self.library.reindex(p1["id"], full_wgs=True)

        p1_after = self.library.get(p1["id"])
        p2_after = self.library.get(p2["id"])
        self.assertEqual(p1_after["index_scope"], "full")
        self.assertEqual(p1_after["cohort_index_status"], "ready")
        self.assertEqual(p2_after["cohort_index_status"], "ready")
        self.assertEqual(p2_after["cohort_file_id"], p2_before["cohort_file_id"])

        entries = [(item["file_id"], item["name"]) for item in self.cohort.list_samples()]
        self.assertEqual(
            [name for _, name in entries].count("P2"), 1,
            f"sibling P2 must be indexed exactly once, found {entries}",
        )
        self.assertEqual([name for _, name in entries].count("P1"), 1)
        # The full file holds only P1.
        full_file_id = p1_after["cohort_file_id"]
        self.assertEqual(
            sorted(name for file_id, name in entries if file_id == full_file_id), ["P1"],
        )
        # Carrier rows: variant 1:200 C>T is carried by P2 only (1/1); it
        # must appear in exactly one cohort file.
        hits = self.cohort.query({"mode": "variant", "query": "1:200:C:T", "limit": 50})
        self.assertEqual(hits["total"], 1, hits["rows"])
        self.assertEqual(hits["rows"][0]["sample"], "P2")

        # A sibling's own full reindex APPENDS to the same full file rather
        # than replacing it (cohort_files.path is unique).
        self.library.reindex(p2["id"], full_wgs=True)
        p1_final = self.library.get(p1["id"])
        p2_final = self.library.get(p2["id"])
        self.assertEqual(p1_final["cohort_index_status"], "ready")
        self.assertEqual(p2_final["cohort_index_status"], "ready")
        self.assertEqual(p1_final["cohort_file_id"], p2_final["cohort_file_id"])
        entries = [(item["file_id"], item["name"]) for item in self.cohort.list_samples()]
        self.assertEqual(sorted(name for _, name in entries), ["P1", "P2"])

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

    def _legacy_library(self, name="interrupted"):
        """A library whose datasets table predates the version columns."""
        state = self.state / name
        state.mkdir()
        cohort = CohortStore(state / "cohort.sqlite3")
        library = SampleLibrary(state, cohort)
        vcf = state / "legacy.vcf"
        write_vcf(vcf)
        library.import_vcf(vcf, self.payload(include=True))
        version_columns = {
            "callset_id", "callset_fingerprint", "version_id", "version_number",
            "is_current", "cohort_preferred", "supersedes_version_id",
        }
        with sqlite3.connect(state / "cohort.sqlite3") as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("DROP INDEX IF EXISTS library_datasets_current_content_idx")
            connection.execute("DROP INDEX IF EXISTS library_datasets_current_callset_idx")
            connection.execute("DROP INDEX IF EXISTS library_datasets_callset_idx")
            columns = [
                row["name"] for row in connection.execute("PRAGMA table_info(library_datasets)")
            ]
            old_columns = [column for column in columns if column not in version_columns]
            connection.execute(
                f"CREATE TABLE library_datasets_legacy AS SELECT {','.join(old_columns)} FROM library_datasets"
            )
            connection.execute("DROP TABLE library_datasets")
            connection.execute("ALTER TABLE library_datasets_legacy RENAME TO library_datasets")
            connection.execute("UPDATE library_datasets SET include_in_cohort=1")
            connection.execute("DELETE FROM sample_library_meta")
        return state, cohort

    def test_interrupted_column_migration_is_completed_on_the_next_open(self):
        """Audit M30: the old migration ran ALTER TABLE in autocommit and the
        backfill in a later transaction. Killed in between, the next open saw
        the columns and skipped the backfill: include_in_cohort stayed 1
        while cohort_preferred stayed 0 and version ids were assigned by the
        wrong rule. Simulate exactly that state (columns present, defaults
        only, no marker) and require the backfill to complete."""
        state, cohort = self._legacy_library()
        with sqlite3.connect(state / "cohort.sqlite3") as connection:
            for column, declaration in (
                ("callset_id", "TEXT NOT NULL DEFAULT ''"),
                ("callset_fingerprint", "TEXT NOT NULL DEFAULT ''"),
                ("version_id", "TEXT NOT NULL DEFAULT ''"),
                ("version_number", "INTEGER NOT NULL DEFAULT 1"),
                ("is_current", "INTEGER NOT NULL DEFAULT 1"),
                ("cohort_preferred", "INTEGER NOT NULL DEFAULT 0"),
                ("supersedes_version_id", "TEXT"),
            ):
                connection.execute(f"ALTER TABLE library_datasets ADD COLUMN {column} {declaration}")
        library = SampleLibrary(state, cohort)
        records = library.list()
        self.assertTrue(records)
        for record in records:
            self.assertEqual(record["include_in_cohort"], 1)
            self.assertEqual(record["cohort_preferred"], 1, "backfill from include_in_cohort was skipped")
            self.assertTrue(record["version_id"])
            self.assertNotEqual(record["version_id"], record["managed_checksum"],
                                "version id must come from the legacy grouping rule")
            self.assertEqual(record["callset_id"], record["managed_checksum"])
        status = library.migration_status()
        self.assertTrue(all(item["applied_at"] for item in status["migrations"]))
        self.assertEqual(status["schema_version"], "portable_paths_v2")

    def test_migration_step_is_atomic_with_its_marker(self):
        """A step that fails half-way leaves neither columns nor marker: the
        ALTER and the backfill share one transaction with the marker write."""
        state, cohort = self._legacy_library("atomic")
        original = SampleLibrary._migrate_library_versions

        def exploding(self_, connection):
            original(self_, connection)
            raise RuntimeError("simulated crash after the backfill")

        with mock.patch.object(SampleLibrary, "_migrate_library_versions", exploding):
            with self.assertRaises(RuntimeError):
                SampleLibrary(state, cohort)
        with sqlite3.connect(state / "cohort.sqlite3") as connection:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(library_datasets)")}
            self.assertNotIn("cohort_preferred", columns, "a failed step must roll back its ALTER")
            markers = {row[0] for row in connection.execute("SELECT key FROM sample_library_meta")}
            self.assertNotIn("library_versions_v1", markers)
        # The next open completes it.
        library = SampleLibrary(state, cohort)
        self.assertTrue(all(record["cohort_preferred"] == 1 for record in library.list()))

    def test_legacy_duplicate_imports_migrate_as_separate_versions(self):
        """Upgrading an old library must not mix duplicate sample cards.

        Old releases allowed the same bytes to be imported again under a
        different settings hash. The migration groups each old import batch
        into a version and makes only the newest batch current.
        """
        legacy_state = self.state / "legacy-library"
        legacy_state.mkdir()
        legacy_database = legacy_state / "cohort.sqlite3"
        legacy_cohort = CohortStore(legacy_database)
        legacy_library = SampleLibrary(legacy_state, legacy_cohort)
        legacy_vcf = legacy_state / "legacy.vcf"
        write_vcf(legacy_vcf)
        legacy_library.import_vcf(
            legacy_vcf,
            {**self.payload(include=False), "qc_settings": {"minDp": 10}},
        )

        version_columns = {
            "callset_id", "callset_fingerprint", "version_id", "version_number",
            "is_current", "cohort_preferred", "supersedes_version_id",
        }
        with sqlite3.connect(legacy_database) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("DROP INDEX library_datasets_current_content_idx")
            connection.execute("DROP INDEX library_datasets_current_callset_idx")
            rows = connection.execute("SELECT * FROM library_datasets").fetchall()
            columns = [
                row["name"] for row in connection.execute(
                    "PRAGMA table_info(library_datasets)"
                ).fetchall()
            ]
            for row in rows:
                copied_sample_id = row["sample_id"] + "-second"
                sample = connection.execute(
                    "SELECT * FROM library_samples WHERE id=?", (row["sample_id"],)
                ).fetchone()
                connection.execute(
                    "INSERT INTO library_samples(id,label,individual_id,created_at,updated_at) VALUES(?,?,?,?,?)",
                    (copied_sample_id, sample["label"], sample["individual_id"],
                     sample["created_at"], "2026-01-02T00:00:00+00:00"),
                )
                values = dict(row)
                values.update({
                    "id": row["id"] + "-second",
                    "sample_id": copied_sample_id,
                    "settings_hash": "legacy-second-profile",
                    "profile_label": "legacy second profile",
                    "imported_at": "2026-01-02T00:00:00+00:00",
                    "updated_at": "2026-01-02T00:00:00+00:00",
                })
                placeholders = ",".join("?" for _ in columns)
                connection.execute(
                    f"INSERT INTO library_datasets({','.join(columns)}) VALUES({placeholders})",
                    [values[column] for column in columns],
                )
            connection.execute(
                "UPDATE library_datasets SET imported_at='2026-01-01T00:00:00+00:00', "
                "updated_at='2026-01-01T00:00:00+00:00' WHERE settings_hash!='legacy-second-profile'"
            )
            connection.execute(
                "UPDATE library_datasets SET settings_hash='legacy-sibling-edit' "
                "WHERE settings_hash!='legacy-second-profile' AND vcf_sample_name='P1'"
            )
            old_columns = [column for column in columns if column not in version_columns]
            connection.execute(
                f"CREATE TABLE library_datasets_legacy AS SELECT {','.join(old_columns)} FROM library_datasets"
            )
            connection.execute("DROP TABLE library_datasets")
            connection.execute("ALTER TABLE library_datasets_legacy RENAME TO library_datasets")

        migrated = SampleLibrary(legacy_state, legacy_cohort)
        records = migrated.list()
        self.assertEqual(len(records), 4)
        self.assertEqual(len({record["callset_id"] for record in records}), 1)
        versions = {}
        for record in records:
            versions.setdefault(record["version_id"], []).append(record)
        self.assertEqual(len(versions), 2)
        self.assertEqual(sorted(len(members) for members in versions.values()), [2, 2])
        self.assertEqual(sum(record["is_current"] for record in records), 2)
        self.assertEqual(
            {record["version_number"] for record in records if record["is_current"]},
            {2},
        )


if __name__ == "__main__":
    unittest.main()
