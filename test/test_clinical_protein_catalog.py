#!/usr/bin/env python3
"""Focused source-gate and provenance tests for clinical protein catalogs."""

from __future__ import annotations

import csv
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
import prepare_clinical_protein_catalog as catalog  # noqa: E402


class ClinicalProteinCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def _export(self, source_type: str, source: Path):
        vcf = self.root / f"{source_type}.export.vcf"
        metadata = self.root / f"{source_type}.tsv"
        count = catalog.export_source(source_type, source, vcf, metadata)
        with metadata.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle, delimiter="\t"))
        return count, vcf.read_text(), rows

    def test_clinvar_exports_only_pathogenic_missense(self):
        source = self.root / "clinvar.vcf"
        source.write_text(
            "##fileformat=VCFv4.2\n"
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
            "1\t10\t101\tC\tT\t.\t.\tCLNSIG=Pathogenic;MC=SO:0001583|missense_variant;CLNDN=Disease_one;GENEINFO=GENE1:1\n"
            "1\t11\t102\tC\tG\t.\t.\tCLNSIG=Uncertain_significance;MC=SO:0001583|missense_variant\n"
            "1\t12\t103\tC\tA\t.\t.\tCLNSIG=Likely_pathogenic;MC=SO:0001819|synonymous_variant\n"
        )
        count, vcf, rows = self._export("clinvar", source)
        self.assertEqual(count, 1)
        self.assertIn("1\t10\tCPM000000001\tC\tT", vcf)
        self.assertEqual(rows[0]["record_id"], "101")
        self.assertEqual(rows[0]["known_gene"], "GENE1")
        self.assertEqual(rows[0]["disease"], "Disease one")

    def test_clingen_exports_only_active_pathogenic_assertions(self):
        source = self.root / "clingen.sqlite3"
        db = sqlite3.connect(source)
        db.execute(
            "CREATE TABLE assertions(chrom,pos,ref,alt,uuid,assertion,disease,gene,active)"
        )
        db.executemany(
            "INSERT INTO assertions VALUES(?,?,?,?,?,?,?,?,?)",
            [
                ("2", 20, "A", "G", "u1", "Likely Pathogenic", "D1", "GENE2", 1),
                ("2", 21, "A", "T", "u2", "VUS", "D2", "GENE2", 1),
                ("2", 22, "A", "C", "u3", "Pathogenic", "D3", "GENE2", 0),
            ],
        )
        db.commit(); db.close()
        count, _vcf, rows = self._export("clingen", source)
        self.assertEqual(count, 1)
        self.assertEqual(rows[0]["record_id"], "u1")
        self.assertEqual(rows[0]["classification"], "Likely Pathogenic")
        self.assertEqual(rows[0]["source_allele"], "2:20:A:G")

    def test_genia_requires_variant_component_and_p_or_lp(self):
        source = self.root / "genia.sqlite3"
        db = sqlite3.connect(source)
        db.execute("CREATE TABLE components(id TEXT)")
        db.execute(
            "CREATE TABLE variants(chrom,pos,ref,alt,record_id,class_code)"
        )
        db.executemany(
            "INSERT INTO variants VALUES(?,?,?,?,?,?)",
            [("3", 30, "G", "A", "g1", "P"), ("3", 31, "G", "T", "g2", "RF")],
        )
        db.commit(); db.close()
        count, _vcf, rows = self._export("genia", source)
        self.assertEqual(count, 0)
        self.assertEqual(rows, [])

        db = sqlite3.connect(source)
        db.execute("INSERT INTO components VALUES('variant_vcf')")
        db.commit(); db.close()
        count, _vcf, rows = self._export("genia", source)
        self.assertEqual(count, 1)
        self.assertEqual(rows[0]["record_id"], "g1")
        self.assertEqual(rows[0]["classification"], "Pathogenic")

    def test_reduce_joins_vep_transcript_to_source_provenance(self):
        metadata = self.root / "metadata.tsv"
        metadata.write_text(
            "synthetic_id\trecord_id\tsource_allele\tclassification\tdisease\tknown_gene\n"
            "CPM000000001\trec 1\t4:40:C:T\tPathogenic\tDisease A\tGENE0|GENE4\n"
            "CPM000000002\trec2\t4:41:C:G\tPathogenic\tDisease B\tOTHER\n"
        )
        vep = self.root / "vep.tsv"
        vep.write_text(
            "#Uploaded_variation\tSYMBOL\tProtein_position\tConsequence\tAmino_acids\tFeature\n"
            "CPM000000001\tOTHER\t99\tmissense_variant\tA/T\tENST0000.1\n"
            "CPM000000001\tGENE4\t123\tmissense_variant\tR/H\tENST0001.7\n"
            "CPM000000002\tGENE4\t124\tmissense_variant\tG/W\tENST0002.1\n"
        )
        output = self.root / "catalog.tsv"
        self.assertEqual(catalog.reduce_vep(vep, metadata, output), 1)
        rows = output.read_text().splitlines()
        self.assertEqual(rows[0], "#" + "\t".join(catalog.CATALOG_COLUMNS))
        self.assertEqual(
            rows[1],
            "GENE4\t123\tR\tH\tENST0001\trec 1\t4:40:C:T\tPathogenic\tDisease A",
        )


class PathogenicTermPolicyTests(unittest.TestCase):
    """Review M6: `pathogenic_terms` is honoured by the active catalog
    builder, matched as whole labels, and recorded in the manifest."""

    CLINVAR = (
        "##fileformat=VCFv4.2\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
        "1\t10\t101\tC\tT\t.\t.\tCLNSIG=Pathogenic;MC=SO:0001583|missense_variant\n"
        "1\t11\t102\tC\tG\t.\t.\tCLNSIG=Pathogenic|risk_factor;MC=SO:0001583|missense_variant\n"
        "1\t12\t103\tC\tA\t.\t.\tCLNSIG=Likely_pathogenic,_low_penetrance;MC=SO:0001583|missense_variant\n"
        "1\t13\t104\tC\tA\t.\t.\tCLNSIG=Pathogenic/Likely_pathogenic;MC=SO:0001583|missense_variant\n"
        "1\t14\t105\tC\tA\t.\t.\tCLNSIG=Conflicting_classifications_of_pathogenicity;MC=SO:0001583|missense_variant\n"
    )

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "clinvar.vcf"
        self.source.write_text(self.CLINVAR)

    def tearDown(self):
        self.temporary.cleanup()

    def _config(self, text: str) -> Path:
        path = self.root / "config.yaml"
        path.write_text(text)
        return path

    def _exported_ids(self, config: Path | None):
        labels, _origin = catalog.load_pathogenic_labels(config)
        metadata = self.root / "meta.tsv"
        catalog.export_source(
            "clinvar", self.source, self.root / "out.vcf", metadata, labels
        )
        with metadata.open(newline="", encoding="utf-8") as handle:
            return [row["record_id"] for row in csv.DictReader(handle, delimiter="\t")]

    def test_default_labels_equal_the_built_in_set_and_exclude_compound_values(self):
        labels, origin = catalog.load_pathogenic_labels(None)
        self.assertEqual(origin, "default")
        self.assertEqual(labels, frozenset(catalog.PATHOGENIC_LABELS))
        self.assertEqual(self._exported_ids(None), ["101", "104"])

    def test_configured_extra_label_is_honoured_exactly(self):
        config = self._config(
            "post_processing:\n"
            "  clinvar_aa_match:\n"
            "    pathogenic_terms:\n"
            "      - Pathogenic\n"
            "      - Likely_pathogenic\n"
            "      - Pathogenic|risk_factor\n"
        )
        labels, origin = catalog.load_pathogenic_labels(config)
        self.assertEqual(origin, "post_processing.clinvar_aa_match.pathogenic_terms")
        self.assertIn("pathogenic|risk factor", labels)
        # 104 (Pathogenic/Likely_pathogenic) is no longer listed, so it drops
        # out; 102 (the listed compound value) comes in; 103 stays out.
        self.assertEqual(self._exported_ids(config), ["101", "102"])

    def test_new_config_home_takes_precedence_over_the_legacy_key(self):
        config = self._config(
            "post_processing:\n"
            "  clinical_protein_match:\n"
            "    pathogenic_terms: [Pathogenic]\n"
            "  clinvar_aa_match:\n"
            "    pathogenic_terms: [Likely_pathogenic]\n"
        )
        labels, origin = catalog.load_pathogenic_labels(config)
        self.assertEqual(
            origin, "post_processing.clinical_protein_match.pathogenic_terms"
        )
        self.assertEqual(labels, frozenset({"pathogenic"}))
        self.assertEqual(self._exported_ids(config), ["101"])

    def test_labels_never_match_by_substring_or_pattern(self):
        self.assertFalse(catalog.is_pathogenic("Pathogenic|risk_factor"))
        self.assertFalse(catalog.is_pathogenic("Likely_pathogenic,_low_penetrance"))
        self.assertFalse(catalog.is_pathogenic("Pathogenic", {"pathogenic|risk factor"}))
        with self.assertRaisesRegex(ValueError, "whole labels"):
            catalog.normalize_labels(["Pathogenic*"])
        with self.assertRaisesRegex(ValueError, "at least one"):
            catalog.normalize_labels(["", "  "])
        with self.assertRaisesRegex(ValueError, "list of strings"):
            catalog.normalize_labels([1])
        config = self._config("post_processing:\n  clinvar_aa_match:\n    pathogenic_terms: Pathogenic\n")
        with self.assertRaisesRegex(ValueError, "must be a list"):
            catalog.load_pathogenic_labels(config)

    def test_slash_and_space_spellings_of_the_combined_label_are_one_entry(self):
        labels = catalog.normalize_labels(["Pathogenic/Likely_pathogenic"])
        self.assertEqual(
            labels, frozenset({"pathogenic/likely pathogenic", "pathogenic likely pathogenic"})
        )

    def test_fingerprint_and_manifest_record_the_active_labels(self):
        default_fp = catalog.labels_fingerprint(catalog.load_pathogenic_labels(None)[0])
        config = self._config(
            "post_processing:\n  clinvar_aa_match:\n    pathogenic_terms: [Pathogenic]\n"
        )
        narrow_fp = catalog.labels_fingerprint(catalog.load_pathogenic_labels(config)[0])
        self.assertNotEqual(default_fp, narrow_fp)
        self.assertEqual(len(default_fp), 12)
        # `labels` subcommand output feeds the catalog stamps.
        import io, contextlib
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            self.assertEqual(catalog.main(["labels", "--config", str(config)]), 0)
        fingerprint, origin, joined = buffer.getvalue().strip().split("\t")
        self.assertEqual(fingerprint, narrow_fp)
        self.assertEqual(origin, "post_processing.clinvar_aa_match.pathogenic_terms")
        self.assertEqual(joined, "pathogenic")
        # reduce --manifest records the same set.
        metadata = self.root / "metadata.tsv"
        metadata.write_text(
            "synthetic_id\trecord_id\tsource_allele\tclassification\tdisease\tknown_gene\n"
            "CPM000000001\t101\t1:10:C:T\tPathogenic\tD\tGENE1\n"
        )
        vep = self.root / "vep.tsv"
        vep.write_text(
            "#Uploaded_variation\tSYMBOL\tProtein_position\tConsequence\tAmino_acids\tFeature\n"
            "CPM000000001\tGENE1\t5\tmissense_variant\tR/H\tENST0001.7\n"
        )
        output = self.root / "catalog.tsv"
        manifest = self.root / "manifest.json"
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(catalog.main([
                "reduce", "--vep-tab", str(vep), "--metadata", str(metadata),
                "--output", str(output), "--manifest", str(manifest),
                "--config", str(config), "--source-type", "clinvar",
            ]), 0)
        import json
        recorded = json.loads(manifest.read_text())
        self.assertEqual(recorded["pathogenic_labels"], ["pathogenic"])
        self.assertEqual(recorded["pathogenic_labels_fingerprint"], narrow_fp)
        self.assertEqual(
            recorded["pathogenic_labels_source"],
            "post_processing.clinvar_aa_match.pathogenic_terms",
        )


if __name__ == "__main__":
    unittest.main()
