#!/usr/bin/env python3
import json
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from local_service.screen_context import ScreenContextStore


class ScreenContextStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        catalog = root / "catalog.sqlite3"
        connection = sqlite3.connect(catalog)
        connection.executescript(
            """
            CREATE TABLE contig(contig_id INTEGER PRIMARY KEY, name TEXT UNIQUE);
            CREATE TABLE ccre(row_index INTEGER PRIMARY KEY, contig_id INTEGER,
              chrom TEXT, start INTEGER, end INTEGER, rdhs_accession TEXT,
              ccre_accession TEXT, overall_class TEXT);
            CREATE TABLE tissue(tissue_index INTEGER PRIMARY KEY, name TEXT,
              source_filename TEXT, source_url TEXT);
            INSERT INTO contig VALUES(1, '1');
            INSERT INTO ccre VALUES(0,1,'1',99,120,'r1','EH38E1','pELS');
            INSERT INTO tissue VALUES(0,'blood','blood.bed','https://example/blood');
            INSERT INTO tissue VALUES(1,'thymus','thymus.bed','https://example/thymus');
            """
        )
        connection.commit(); connection.close()
        contexts = root / "contexts.sqlite3"
        connection = sqlite3.connect(contexts)
        connection.executescript(
            """
            CREATE TABLE profile(profile_index INTEGER PRIMARY KEY, screen_name TEXT,
              display_name TEXT, ontology_id TEXT, ontology_name TEXT, sample_type TEXT,
              lineage TEXT, immune_scope TEXT, evidence_tier TEXT, donor_accession TEXT,
              assays_available_json TEXT, life_stages_json TEXT, sexes_json TEXT,
              experiment_accessions_json TEXT, baseline_eligible INTEGER,
              exclusion_reasons_json TEXT, state_metadata_json TEXT,
              audit_warnings_json TEXT);
            CREATE TABLE context(context_id TEXT PRIMARY KEY, ontology_id TEXT,
              ontology_name TEXT, lineages_json TEXT, donor_count INTEGER,
              tier_counts_json TEXT, life_stages_json TEXT, sexes_json TEXT,
              assay_capability_json TEXT, audit_warnings_json TEXT,
              hierarchy_json TEXT);
            CREATE TABLE context_member(context_id TEXT, donor_accession TEXT,
              profile_index INTEGER, evidence_tier TEXT,
              duplicate_profile_indices_json TEXT);
            """
        )
        profiles = [
            (0, "D1", '["DNase", "H3K27ac"]', "partial_classification"),
            (1, "D2", '["DNase", "H3K4me3"]', "partial_classification"),
            (2, "D3", '["DNase"]', "accessibility_only"),
        ]
        for index, donor, assays, tier in profiles:
            connection.execute(
                "INSERT INTO profile VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (index,"","","CL:1","T cell","primary cell","T cell","",tier,
                 donor,assays,"[]","[]","[]",1,"[]","{}","[]"),
            )
        connection.execute(
            "INSERT INTO context VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            ("cl_1","CL:1","T cell",'["T cell"]',3,
             '{"accessibility_only":1,"partial_classification":2}',"[]","[]",
             '{"chromatin_accessibility":3,"promoter_classification":1,"enhancer_classification":1,"ctcf_classification":0,"any_specific_classification":2,"full_classification_panel":0}',
             "{}",'{"ancestor_context_ids":[],"direct_parent_context_ids":[],"descendant_context_ids":[],"direct_child_context_ids":[],"is_nested_context":false,"is_summary_parent":false,"related_contexts_sharing_donors":[]}'),
        )
        for index, donor, _assays, tier in profiles:
            connection.execute(
                "INSERT INTO context_member VALUES(?,?,?,?,?)",
                ("cl_1", donor, index, tier, "[]"),
            )
        connection.commit(); connection.close()
        tissue_matrix = root / "tissues.u8"
        tissue_matrix.write_bytes(bytes([2, 0]))
        immune_matrix = root / "immune.u8"
        immune_matrix.write_bytes(bytes([1, 2, 7]))
        prepared = root / "prepared.json"
        prepared.write_text(json.dumps({
            "catalog": {"path": str(catalog)},
            "tissue_matrix": {"path": str(tissue_matrix), "shape": [1, 2]},
            "immune_matrix": {"path": str(immune_matrix), "shape": [1, 3]},
        }))
        self.manifest = root / "contexts.json"
        self.manifest.write_text(json.dumps({
            "registry": "SCREEN Registry V4", "assembly": "GRCh38",
            "source_paths": {"prepared_manifest": str(prepared)},
            "artifacts": {"database": {"path": str(contexts)}},
        }))
        self.store = ScreenContextStore()

    def tearDown(self):
        self.temp.cleanup()

    def test_relocated_bundle_uses_selected_storage_even_if_original_exists(self):
        original = self.manifest.parent
        relocated = original / "new annotation storage" / "prepared"
        relocated.mkdir(parents=True)
        for path in original.iterdir():
            if path.is_file():
                shutil.copy2(path, relocated / path.name)
        manifest_bytes = (relocated / "contexts.json").read_bytes()
        prepared_bytes = (relocated / "prepared.json").read_bytes()
        # The previous location remains present but must not supply evidence.
        (original / "tissues.u8").write_bytes(bytes([0, 0]))
        evidence = self.store.evidence(
            relocated / "contexts.json",
            {"chrom": "1", "pos": 100, "ref": "A", "alt": "G"},
        )
        self.assertEqual(evidence["overlaps"][0]["tissues"][0]["state_label"], "Enhancer-like")
        self.assertEqual((relocated / "contexts.json").read_bytes(), manifest_bytes)
        self.assertEqual((relocated / "prepared.json").read_bytes(), prepared_bytes)
        # A missing local artifact must not silently use the previous copy.
        (relocated / "tissues.u8").unlink()
        with self.assertRaisesRegex(ValueError, "Retry the SCREEN download"):
            ScreenContextStore().catalog(relocated / "contexts.json")

    def test_relative_and_foreign_absolute_paths_resolve_inside_bundle(self):
        for prefix in ("", "/unavailable/producer/", "C:\\producer\\"):
            with self.subTest(prefix=prefix):
                prepared_path = self.manifest.parent / "prepared.json"
                prepared = json.loads(prepared_path.read_text())
                for key, name in (("catalog", "catalog.sqlite3"),
                                  ("tissue_matrix", "tissues.u8"),
                                  ("immune_matrix", "immune.u8")):
                    prepared[key]["path"] = prefix + name
                prepared_path.write_text(json.dumps(prepared))
                context = json.loads(self.manifest.read_text())
                context["source_paths"]["prepared_manifest"] = prefix + "prepared.json"
                context["artifacts"]["database"]["path"] = prefix + "contexts.sqlite3"
                self.manifest.write_text(json.dumps(context))
                self.assertTrue(ScreenContextStore().catalog(self.manifest)["available"])

    def test_empty_or_escaping_bundle_path_is_rejected(self):
        for value in ("", "../prepared.json"):
            with self.subTest(value=value):
                context = json.loads(self.manifest.read_text())
                context["source_paths"]["prepared_manifest"] = value
                self.manifest.write_text(json.dumps(context))
                with self.assertRaises(ValueError):
                    self.store.catalog(self.manifest)

    def test_context_evidence_preserves_mixed_and_tissue_states(self):
        evidence = self.store.evidence(
            self.manifest, {"chrom": "chr1", "pos": 100, "ref": "A", "alt": "G"}
        )
        self.assertEqual(evidence["status"], "overlap")
        overlap = evidence["overlaps"][0]
        self.assertEqual(overlap["tissues"][0]["state_label"], "Enhancer-like")
        self.assertEqual(overlap["tissues"][1]["state"], "not_detected")
        self.assertEqual(overlap["immune_contexts"][0]["state"], "mixed")
        self.assertEqual(overlap["immune_contexts"][0]["classification_capable_donors"], 2)

    def test_positive_evidence_filter_uses_any_or_all(self):
        variants = [
            {"key": "hit", "chrom": "1", "pos": 100, "ref": "A", "alt": "G"},
            {"key": "miss", "chrom": "1", "pos": 200, "ref": "A", "alt": "G"},
        ]
        any_result = self.store.filter_variants(self.manifest, {
            "variants": variants, "tissue_ids": ["tissue:0"], "mode": "any",
        })
        self.assertEqual(any_result["matching_keys"], ["hit"])
        all_result = self.store.filter_variants(self.manifest, {
            "variants": variants, "tissue_ids": ["tissue:0", "tissue:1"], "mode": "all",
        })
        self.assertEqual(all_result["matching_keys"], [])

    def test_empty_or_stale_context_selection_is_an_error_not_zero_matches(self):
        for selection in ({}, {"tissue_ids": ["tissue:obsolete"]},
                          {"immune_context_ids": ["immune:obsolete"]}):
            with self.subTest(selection=selection):
                with self.assertRaisesRegex(ValueError, "context"):
                    self.store.filter_variants(self.manifest, {"variants": [], **selection})

    def _rebuild_with_skew(self, *, tissue_rows, matrix_rows, tissue_bytes):
        """Rewrite the catalog's tissue table and the tissue matrix so the
        catalog row count and the stored matrix width disagree."""
        root = Path(self.temp.name)
        connection = sqlite3.connect(root / "catalog.sqlite3")
        connection.execute("DELETE FROM tissue")
        connection.executemany(
            "INSERT INTO tissue VALUES(?,?,?,?)",
            [(index, name, f"{name}.bed", f"https://example/{name}") for index, name in tissue_rows],
        )
        connection.execute("DELETE FROM ccre")
        for row_index in range(matrix_rows):
            connection.execute(
                "INSERT INTO ccre VALUES(?,?,?,?,?,?,?,?)",
                (row_index, 1, "1", 99 + 100 * row_index, 120 + 100 * row_index,
                 f"r{row_index}", f"EH38E{row_index}", "pELS"),
            )
        connection.commit(); connection.close()
        (root / "tissues.u8").write_bytes(bytes(tissue_bytes))
        (root / "immune.u8").write_bytes(bytes([1, 2, 7] * matrix_rows))
        prepared = root / "prepared.json"
        prepared.write_text(json.dumps({
            "catalog": {"path": str(root / "catalog.sqlite3")},
            "tissue_matrix": {"path": str(root / "tissues.u8"), "shape": [matrix_rows, 2]},
            "immune_matrix": {"path": str(root / "immune.u8"), "shape": [matrix_rows, 3]},
        }))
        self.store = ScreenContextStore()

    def test_filter_and_detail_read_the_same_matrix_bytes_on_catalog_skew(self):
        """Audit M29: with a two-column tissue matrix and a one-entry tissue
        catalog, the filter derived its row stride from the catalog (1) while
        the detail reader used the stored shape (2), so for the second cCRE
        row the detail view reported activity the filter excluded."""
        # Two cCRE rows; tissue 0 is active only on the second row.
        self._rebuild_with_skew(
            tissue_rows=[(0, "blood")], matrix_rows=2, tissue_bytes=[0, 0, 2, 5],
        )
        second = {"key": "second", "chrom": "1", "pos": 200, "ref": "A", "alt": "G"}
        evidence = self.store.evidence(self.manifest, second)
        self.assertEqual(evidence["status"], "overlap")
        self.assertTrue(evidence["overlaps"][0]["tissues"][0]["activity_detected"])
        filtered = self.store.filter_variants(self.manifest, {
            "variants": [second], "tissue_ids": ["tissue:0"], "mode": "any",
        })
        self.assertEqual(filtered["matching_keys"], ["second"])

    def test_bundle_whose_catalog_exceeds_the_matrix_is_refused(self):
        # Three tissues in the catalog, two columns in the matrix: index 2
        # would read past every row. Refuse rather than misread.
        self._rebuild_with_skew(
            tissue_rows=[(0, "blood"), (1, "thymus"), (2, "spleen")],
            matrix_rows=1, tissue_bytes=[2, 0],
        )
        with self.assertRaises(ValueError) as caught:
            self.store.evidence(self.manifest, {"chrom": "1", "pos": 100, "ref": "A", "alt": "G"})
        self.assertIn("exceed the tissue matrix width", str(caught.exception))
        # A matrix whose file size disagrees with its declared shape is refused too.
        self._rebuild_with_skew(tissue_rows=[(0, "blood")], matrix_rows=1, tissue_bytes=[2, 0, 9])
        with self.assertRaises(ValueError) as caught:
            self.store.evidence(self.manifest, {"chrom": "1", "pos": 100, "ref": "A", "alt": "G"})
        self.assertIn("bytes but its manifest shape", str(caught.exception))

    def test_catalog_exposes_immune_core_immune_all_and_select_all_presets(self):
        catalog = self.store.catalog(self.manifest)
        presets = {item["id"]: item for item in catalog["presets"]}
        self.assertEqual(presets["immune-core"]["name"], "Immune core")
        self.assertEqual(presets["immune-all"]["name"], "Immune all")
        self.assertEqual(presets["select-all"]["name"], "Select all")
        self.assertNotIn("lymphoid", presets)
        self.assertNotIn("myeloid", presets)
        self.assertEqual(
            presets["immune-all"]["immune_context_ids"],
            ["immune:cl_1"],
        )
        self.assertEqual(
            set(presets["select-all"]["tissue_ids"]),
            {"tissue:0", "tissue:1"},
        )
        self.assertEqual(
            presets["select-all"]["immune_context_ids"],
            ["immune:cl_1"],
        )


if __name__ == "__main__":
    unittest.main()
