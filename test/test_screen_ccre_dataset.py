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

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    import numpy  # noqa: F401 — SCREEN preparation dependency, optional for end users
    HAVE_NUMPY = True
except ImportError:
    HAVE_NUMPY = False

# NumPy is only required for the one-time SCREEN preparation task, never for
# end-user annotation or review, so its absence is a SKIP, not a failure.
requires_numpy = unittest.skipUnless(
    HAVE_NUMPY, "SKIP: NumPy not installed (only needed to prepare SCREEN context data)"
)

from pipeline import screen_ccre_dataset
from pipeline.screen_ccre_dataset import (
    AGGREGATE_STATUS,
    BIGBED_SHA256_PIN_ENV,
    PARTIAL_STATUS,
    ensure_bigbed_tool,
    ensure_verified_ucsc_binary,
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

    @requires_numpy
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

    @requires_numpy
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


class UcscBinaryVerificationTests(unittest.TestCase):
    """Audit M17: the UCSC bigBedToBed download is verified against UCSC's
    published md5 listing (or an explicit sha256 pin) BEFORE it is executed;
    a mismatch is refused and nothing executable is left behind."""

    GOOD = b"#!/bin/sh\necho 'bigBedToBed usage' >&2; exit 1\n" + b"#" * 2048
    BAD = b"#!/bin/sh\necho 'bigBedToBed usage' >&2; touch \"$(dirname \"$0\")/EXECUTED\"; exit 1\n" + b"#" * 2048

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.tools = Path(self.temp.name) / "tools"
        self.tools.mkdir()
        self.served = {"bigBedToBed": self.GOOD}
        self.executed_before_verification = []

        def fake_curl(url, output):
            name = url.rsplit("/", 1)[1]
            if name == "md5sum.txt":
                listing = "".join(
                    f"{hashlib.md5(body).hexdigest()}  {n}\n" for n, body in self.listing.items()
                )
                output.write_text(listing)
                return
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(self.served[name])

        self.listing = dict(self.served)
        self.curl = mock.patch.object(screen_ccre_dataset, "run_curl", side_effect=fake_curl)
        self.curl.start()
        self.which = mock.patch.object(screen_ccre_dataset.shutil, "which", return_value=None)
        self.which.start()
        self.env = mock.patch.dict(os.environ, {BIGBED_SHA256_PIN_ENV: ""})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.which.stop()
        self.curl.stop()
        self.temp.cleanup()

    def test_download_is_verified_against_ucsc_listing_before_it_runs(self):
        tool = ensure_bigbed_tool(self.tools.parent)
        self.assertEqual(tool, self.tools / "bigBedToBed")
        record = json.loads((self.tools / "bigBedToBed.manifest.json").read_text())
        self.assertEqual(record["sha256"], hashlib.sha256(self.GOOD).hexdigest())
        self.assertEqual(record["md5"], hashlib.md5(self.GOOD).hexdigest())
        self.assertTrue(record["verified_against"].endswith("/md5sum.txt"))
        self.assertFalse((self.tools / "bigBedToBed.unverified").exists())

    def test_mismatching_download_is_refused_and_never_executed(self):
        self.served["bigBedToBed"] = self.BAD  # what the transfer delivers
        with self.assertRaises(RuntimeError) as caught:
            ensure_bigbed_tool(self.tools.parent)
        self.assertIn("does not match UCSC's published", str(caught.exception))
        self.assertFalse((self.tools / "EXECUTED").exists(), "the unverified binary was executed")
        self.assertFalse((self.tools / "bigBedToBed").exists())
        self.assertFalse((self.tools / "bigBedToBed.unverified").exists())
        self.assertFalse((self.tools / "bigBedToBed.manifest.json").exists())

    def test_missing_listing_fails_closed_unless_pinned(self):
        self.listing = {}  # UCSC listing has no entry for the binary
        with self.assertRaises(RuntimeError) as caught:
            ensure_bigbed_tool(self.tools.parent)
        self.assertIn(BIGBED_SHA256_PIN_ENV, str(caught.exception))
        self.assertFalse((self.tools / "bigBedToBed").exists())
        with mock.patch.dict(os.environ, {BIGBED_SHA256_PIN_ENV: hashlib.sha256(self.GOOD).hexdigest()}):
            tool = ensure_bigbed_tool(self.tools.parent)
        self.assertTrue(tool.exists())
        record = json.loads((self.tools / "bigBedToBed.manifest.json").read_text())
        self.assertIn("pinned sha256", record["verified_against"])
        # A wrong pin refuses the file even when UCSC's listing would accept it.
        self.listing = dict(self.served)
        (self.tools / "bigBedToBed").unlink()
        (self.tools / "bigBedToBed.manifest.json").unlink()
        with mock.patch.dict(os.environ, {BIGBED_SHA256_PIN_ENV: "0" * 64}):
            with self.assertRaises(RuntimeError):
                ensure_bigbed_tool(self.tools.parent)
        self.assertFalse((self.tools / "bigBedToBed").exists())

    def test_cached_binary_must_satisfy_the_current_pin(self):
        # A binary verified against UCSC's listing earlier is not exempt from
        # a pin supplied now: the cache-return path compares the pin too.
        binary = self.tools / "bigBedToBed"
        ensure_verified_ucsc_binary("linux.x86_64", binary)
        self.assertTrue(binary.exists())
        with self.assertRaises(RuntimeError) as caught:
            ensure_verified_ucsc_binary("linux.x86_64", binary, pinned_sha256="0" * 64)
        self.assertIn("does not match the pinned digest", str(caught.exception))
        self.assertFalse(binary.exists(), "a cached binary failing the pin must not be kept")
        self.assertFalse((self.tools / "bigBedToBed.manifest.json").exists())
        # The right pin accepts it and is recorded as the verification basis.
        record = ensure_verified_ucsc_binary("linux.x86_64", binary, pinned_sha256=hashlib.sha256(self.GOOD).hexdigest())
        self.assertIn("pinned sha256", record["verified_against"])
        record = ensure_verified_ucsc_binary("linux.x86_64", binary, pinned_sha256=hashlib.sha256(self.GOOD).hexdigest())
        self.assertIn("pinned sha256", record["verified_against"])
        # The environment pin is honoured on the cache path as well.
        with mock.patch.dict(os.environ, {BIGBED_SHA256_PIN_ENV: "1" * 64}):
            with self.assertRaises(RuntimeError):
                ensure_verified_ucsc_binary("linux.x86_64", binary)

    def test_binary_left_by_an_older_version_is_verified_in_place(self):
        # Older versions hashed the binary after running it and never checked
        # the hash against anything: such a file must be verified (no
        # re-download when it matches) and replaced when it does not.
        binary = self.tools / "bigBedToBed"
        binary.write_bytes(self.GOOD)
        binary.chmod(0o755)
        (self.tools / "bigBedToBed.manifest.json").write_text(json.dumps({
            "source_url": "x", "sha256": hashlib.sha256(self.GOOD).hexdigest(), "size": len(self.GOOD),
        }))
        record = ensure_verified_ucsc_binary("linux.x86_64", binary)
        self.assertTrue(record["verified_against"].endswith("/md5sum.txt"))
        self.assertEqual(json.loads((self.tools / "bigBedToBed.manifest.json").read_text())["md5"], record["md5"])
        # Tampered on disk: refused, then replaced by a verified download.
        binary.write_bytes(self.BAD)
        record = ensure_verified_ucsc_binary("linux.x86_64", binary)
        self.assertEqual(binary.read_bytes(), self.GOOD)
        self.assertFalse((self.tools / "EXECUTED").exists())
        self.assertEqual(record["sha256"], hashlib.sha256(self.GOOD).hexdigest())

    def test_path_binary_cannot_bypass_an_explicit_pin(self):
        binary = self.tools / "bigBedToBed"
        binary.write_bytes(self.BAD)
        binary.chmod(0o755)
        with mock.patch.object(screen_ccre_dataset.shutil, "which", return_value=str(binary)):
            for pin in ("0" * 64, None):
                with self.subTest(pin=pin), mock.patch.dict(os.environ, {BIGBED_SHA256_PIN_ENV: "1" * 64}):
                    with self.assertRaisesRegex(RuntimeError, "does not match the pinned digest"):
                        ensure_bigbed_tool(self.tools.parent, pinned_sha256=pin)
        self.assertFalse((self.tools / "EXECUTED").exists())

    def test_matching_pin_accepts_path_binary_before_probing(self):
        binary = self.tools / "bigBedToBed"
        binary.write_bytes(self.GOOD)
        binary.chmod(0o755)
        with mock.patch.object(screen_ccre_dataset.shutil, "which", return_value=str(binary)):
            self.assertEqual(ensure_bigbed_tool(self.tools.parent, hashlib.sha256(self.GOOD).hexdigest()), binary)


if __name__ == "__main__":
    unittest.main()
