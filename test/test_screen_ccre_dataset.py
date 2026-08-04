import argparse
import contextlib
import gzip
import hashlib
import json
import os
import sqlite3
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pipeline.screen_ccre_dataset import (
    AGGREGATE_STATUS,
    PARTIAL_STATUS,
    immune_relevant,
    lineage_assignment,
    lineage_for,
    prepare_dataset,
    read_experiment_lists,
    validate_alignment,
    validate_column_histograms,
)


class ScreenCcreDatasetTests(unittest.TestCase):
    def test_lineage_uses_cl_ancestry_and_boundary_safe_fallback(self):
        mast = {
            "ontology_id": "CL:child", "ontology_name": "mast cell",
            "cell_slims": ["hematopoietic cell"], "sample_type": "primary cell",
        }
        self.assertEqual(
            lineage_assignment(mast, {"CL:child": {"CL:0000097"}}),
            ("Mast cell", "cell_ontology_ancestry"),
        )
        self.assertEqual(lineage_for({
            "ontology_id": "", "ontology_name": "resident cell",
            "cell_slims": [], "sample_type": "primary cell",
        }), "Other hematopoietic/immune")
        self.assertEqual(lineage_for({
            "ontology_id": "", "ontology_name": "NK cell",
            "cell_slims": [], "sample_type": "primary cell",
        }), "NK/ILC")
        self.assertEqual(lineage_for({
            "ontology_id": "", "ontology_name": "club cell",
            "cell_slims": [], "sample_type": "primary cell",
        }), "Other hematopoietic/immune")

    def test_histogram_identity_requires_unique_columns(self):
        selection = {
            "immune_biosamples": [{"index": 0}, {"index": 1}],
            "tissues": [],
        }
        prepared = {
            "immune_matrix": {"shape": [1, 2], "path": "/not/read"},
            "tissue_matrix": {"shape": [1, 0], "path": "/not/read"},
            "summaries": {
                "immune_biosamples": {
                    "0": {"inactive": 1}, "1": {"inactive": 1},
                },
                "tissues": {},
            },
        }
        with self.assertRaisesRegex(RuntimeError, "duplicate class histograms"):
            validate_column_histograms(selection, prepared)

    def test_experiment_lists_and_ontology_selection(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive_path = root / "lists.tar.gz"
            source = root / "lists"
            source.mkdir()
            for assay in ("DNase", "H3K4me3", "H3K27ac", "CTCF", "ATAC"):
                (source / f"{assay}-List.txt").write_text(
                    f"ENCSR{assay[:3]}\tENCFF{assay[:3]}\timmune_sample\n"
                )
            with tarfile.open(archive_path, "w:gz") as archive:
                for path in source.iterdir():
                    archive.add(path, arcname=f"hg38-Experiment-Lists/{path.name}")
            lists = read_experiment_lists(archive_path)
            self.assertEqual(lists["DNase"]["immune_sample"]["signal_file_accession"], "ENCFFDNa")

        self.assertTrue(immune_relevant({
            "cell_slims": ["leukocyte"], "system_slims": [], "organ_slims": [],
            "sample_type": "primary cell",
        }))
        self.assertTrue(immune_relevant({
            "cell_slims": [], "system_slims": [], "organ_slims": ["thymus"],
            "sample_type": "tissue",
        }))
        self.assertFalse(immune_relevant({
            "cell_slims": ["neural cell"], "system_slims": ["nervous system"],
            "organ_slims": ["brain"], "sample_type": "primary cell",
        }))

    def test_prepare_compact_categorical_matrices(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            prepared = root / "prepared"
            (source / "tissues").mkdir(parents=True)
            (source / "immune").mkdir()
            (source / "tools").mkdir()
            ccre_bed = source / "GRCh38-cCREs.bed"
            ccre_bed.write_text(
                "chr1\t10\t20\tEH38D1\tEH38E1\tpELS\n"
                "chr1\t30\t40\tEH38D2\tEH38E2\tdELS\n"
            )
            tissue_name = "adipose.noccl.cCREs.bed"
            with gzip.open(source / "tissues" / f"{tissue_name}.gz", "wt") as handle:
                handle.write(
                    f"chr1\t10\t20\tEH38E1\t0\t.\t10\t20\t255,0,0\tPLS\t{AGGREGATE_STATUS}\n"
                    f"chr1\t30\t40\tEH38E2\t0\t.\t30\t40\t225,225,225\tLow-DNase\t{AGGREGATE_STATUS}\n"
                )
            immune_name = "ENCFFTEST.bigBed"
            (source / "immune" / immune_name).write_text(
                f"chr1\t10\t20\tEH38E1\t0\t.\t10\t20\t6,218,147\tCA-only\t{PARTIAL_STATUS}\n"
                f"chr1\t30\t40\tEH38E2\t0\t.\t30\t40\t255,205,0\tdELS\t{PARTIAL_STATUS}\n"
            )
            fake_tool = source / "tools" / "bigBedToBed"
            fake_tool.write_text(
                "#!/bin/sh\n"
                "[ $# -eq 0 ] && { echo 'bigBedToBed usage' >&2; exit 1; }\n"
                "cp \"$1\" \"$2\"\n"
            )
            fake_tool.chmod(0o755)
            metadata = {
                "display_name": "CD4-positive T cell", "ontology_id": "CL:1",
                "ontology_name": "T cell", "sample_type": "primary cell",
                "organ_slims": ["blood"], "system_slims": ["immune system"],
                "cell_slims": ["leukocyte", "T cell"], "life_stages": ["adult"],
                "sexes": [], "treatments": [], "diseases": [],
            }
            manifest = {
                "registry": "SCREEN Registry V4",
                "assembly": "GRCh38",
                "source": {"ccre_count": 2},
                "class_codes": {},
                "tissues": [{
                    "index": 0, "name": "adipose", "source_filename": tissue_name,
                    "source_url": "https://example/adipose",
                }],
                "immune_biosamples": [{
                    "index": 0, "screen_name": "immune", "donor_accession": "",
                    "metadata": metadata, "lineage": "T cell",
                    "evidence_tier": "partial_classification",
                    "assays_available": ["DNase", "H3K27ac"],
                    "source_filename": immune_name, "source_url": "https://example/immune",
                }],
            }
            manifest_path = source / "selection.json"
            manifest_path.write_text(json.dumps(manifest))
            args = argparse.Namespace(
                manifest=manifest_path, ccre_bed=ccre_bed, source_dir=source,
                output_dir=prepared, force=False,
            )
            with mock.patch.dict(os.environ, {"PATH": f"{source / 'tools'}:{os.environ['PATH']}"}):
                prepare_dataset(args)

            self.assertEqual((prepared / "screen.registry-v4.tissues.u8").read_bytes(), bytes([1, 0]))
            self.assertEqual((prepared / "screen.registry-v4.immune.u8").read_bytes(), bytes([7, 3]))
            with contextlib.closing(sqlite3.connect(prepared / "screen.registry-v4.catalog.sqlite3")) as connection:
                row = connection.execute(
                    "SELECT ccre_accession, overall_class FROM ccre WHERE row_index=1"
                ).fetchone()
                self.assertEqual(row, ("EH38E2", "dELS"))
                tier = connection.execute(
                    "SELECT evidence_tier FROM immune_biosample WHERE biosample_index=0"
                ).fetchone()[0]
                self.assertEqual(tier, "partial_classification")

            prepared_manifest = prepared / "screen.registry-v4.prepared.json"
            prepared_data = json.loads(prepared_manifest.read_text())
            fixture = {
                "registry": "SCREEN Registry V4",
                "assembly": "GRCh38",
                "matrix_sha256": {
                    key: prepared_data[key]["sha256"]
                    for key in ("immune_matrix", "tissue_matrix")
                },
                "immune_profiles": [{
                    "index": 0, "screen_name": "immune", "ontology_id": "CL:1",
                    "evidence_tier": "partial_classification",
                }],
                "tissues": [{"index": 0, "name": "adipose"}],
                "rows": [{
                    "row_index": 0, "chrom": "1", "start": 10, "end": 20,
                    "rdhs_accession": "EH38D1", "ccre_accession": "EH38E1",
                    "overall_class": "pELS",
                    "immune_row_sha256": hashlib.sha256(bytes([7])).hexdigest(),
                    "tissue_row_sha256": hashlib.sha256(bytes([1])).hexdigest(),
                    "immune_profile_codes": {"0": 7},
                    "tissue_codes": {"0": 1},
                }],
            }
            fixture_path = root / "alignment.json"
            fixture_path.write_text(json.dumps(fixture))
            validate_alignment(argparse.Namespace(
                fixture=fixture_path,
                selection=manifest_path,
                prepared_manifest=prepared_manifest,
            ))

            fixture["rows"][0]["immune_profile_codes"]["0"] = 1
            fixture_path.write_text(json.dumps(fixture))
            with self.assertRaisesRegex(RuntimeError, "immune column content mismatch"):
                validate_alignment(argparse.Namespace(
                    fixture=fixture_path,
                    selection=manifest_path,
                    prepared_manifest=prepared_manifest,
                ))


if __name__ == "__main__":
    unittest.main()
