#!/usr/bin/env python3
import base64
import gzip
import io
import json
import os
import sqlite3
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import Mock, patch

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from local_service.test_cohort_store import FakeHtsBackend, write_parallel_vcf, write_vcf
from local_service.storage_locations import (
    StorageLocationRegistry, StorageRegistryError, _filesystem_type,
    storage_path_warning,
)
from local_service.workbench_service import AnnotationJobService, JobStore, SERVICE_VERSION, create_server


class AnnotationJobServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "pipeline"
        (self.root / "scripts").mkdir(parents=True)
        (self.root / "config").mkdir()
        (self.root / "config" / "annotation.config.yaml").write_text(
            "container:\n  vep_image_tag: release_113.4\n"
            "reference:\n  species: homo_sapiens\n"
            "  vep_cache_dir: references/vep_cache\n"
            "plugins:\n  CADD_WGS:\n    enabled: false\n    version: '1.7'\n"
            "    snv: references/cadd/whole_genome_SNVs.tsv.gz\n"
            "    indels: references/cadd/gnomad.genomes.r4.0.indel.tsv.gz\n"
            "region:\n  coding_only: true\n"
            "  bed: references/regions/coding.bed.gz\n"
            "wgs_review:\n  ccre:\n    enabled: true\n"
            "    version: SCREEN Registry V4\n"
            "    bed: references/regions/screen.ccre.bed.gz\n"
            "  gene_tss:\n"
            "    path: references/regions/gene_tss.tsv\n"
            "    ensembl_release: '113'\n"
            "    window_bp: 500000\n"
        )
        (self.root / "references" / "regions").mkdir(parents=True)
        with gzip.open(
            self.root / "references" / "regions" / "coding.bed.gz", "wt"
        ) as handle:
            handle.write("1\t0\t1000000\n")
        with gzip.open(
            self.root / "references" / "regions" / "screen.ccre.bed.gz", "wt"
        ) as handle:
            handle.write("1\t99\t100\tEH38E0000001\tpELS\n")
        (self.root / "references" / "regions" / "screen.ccre.bed.gz.tbi").write_text(
            "test index"
        )
        (self.root / "references" / "regions" / "gene_tss.tsv").write_text(
            "#assembly=GRCh38\n"
            "chrom\ttss\tgene_symbol\tgene_id\tstrand\tbiotype\n"
            "1\t90\tNEAR1\tENSG_NEAR1\t+\tprotein_coding\n"
            "1\t300\tNEAR2\tENSG_NEAR2\t-\tlncRNA\n"
        )
        (self.root / "references" / "vep_cache" / "homo_sapiens" / "113_GRCh38").mkdir(
            parents=True
        )
        self.input = self.root / "patient.vcf.gz"
        self.input.write_bytes(b"test")
        self.output = self.root / "results" / "patient.vep.vcf.gz"
        self._write_script(
            "preflight.sh",
            '#!/usr/bin/env bash\nset -eu\ntest -f "$2"\ntest "$3" != "$2"\n',
        )
        self._write_script(
            "run_annotation.sh",
            '#!/usr/bin/env bash\nset -eu\n'
            'while [[ $# -gt 0 ]]; do case "$1" in '
            '--output) output="$2"; shift 2;; *) shift;; esac; done\n'
            'mkdir -p "$(dirname "$output")"\nprintf "annotated" > "$output"\n',
        )
        self._write_script(
            "download_references.sh",
            "#!/usr/bin/env bash\nset -eu\n"
            'test "$2" = "--only"\ntest "$3" = "spliceai"\n'
            'printf " 25.0%%  test\\n"\nsleep 0.1\n'
            'printf " 75.0%%  test\\ncomplete\\n"\n',
        )
        self._write_script(
            "fetch_clinvar.sh",
            "#!/usr/bin/env bash\nset -eu\nprintf 'ClinVar ready\\n'\n",
        )
        self._write_script(
            "prepare_promoterai.sh",
            "#!/usr/bin/env bash\nset -eu\n"
            'test -s "$1/tss.tsv"\ntest -s "$1/promoterAI_tss500.tsv.gz"\n'
            'test -s "$2"\nprintf "40.0%% validating PromoterAI\\n"\n'
            # Long enough that a duplicate start reliably lands while the job
            # is still active, so the same-id dedup assertion is not a race.
            "sleep 0.5\n"
            'printf "100.0%% PromoterAI preparation complete\\n"\n',
        )
        self._write_script(
            "download_logofunc.sh",
            "#!/usr/bin/env bash\nset -eu\n"
            'test -s "$1"\nprintf "50.0%% downloading LoGoFunc\\n"\n'
            'printf "100.0%% LoGoFunc download and validation complete\\n"\n',
        )
        self._write_script(
            "download_cadd_wgs.sh",
            "#!/usr/bin/env bash\nset -eu\n"
            'test -s "$1"\nprintf "42.0%% downloading CADD SNVs\\n"\n'
            'printf "99.0%% downloading CADD indels\\n"\n'
            'printf "100.0%% CADD download and validation complete\\n"\n',
        )
        self._write_script(
            "prepare_logofunc.sh",
            "#!/usr/bin/env bash\nset -eu\n"
            'test -e "$1"\ntest -s "$2"\n'
            'printf "94.0%% verifying LoGoFunc source checksums\\n"\n'
            'printf "100.0%% LoGoFunc local source installed\\n"\n',
        )
        self.state = Path(self.temp.name) / "state"
        self.service = AnnotationJobService(self.root, self.state)

    def tearDown(self):
        self.service.shutdown()
        self.temp.cleanup()

    def test_sample_library_service_import_records_bundle_and_identity(self):
        review = self.root / "review.vcf"
        write_vcf(review)
        self.service.cohort.hts_backend = None
        result = self.service.import_sample_library({
            "sources": [{"path": str(review), "original_name": "review.vcf"}],
            "analysis_scope": "exome",
            "index_scope": "compact",
            "include_in_cohort": False,
            "qc_settings": {"minDp": 10},
            "retention_routes": ["exome region"],
        })
        self.assertEqual(len(result["datasets"]), 2)
        stored = self.service.sample_library.get(result["datasets"][0]["id"])
        self.assertEqual(stored["annotation_bundle"]["workbench_service"], SERVICE_VERSION)
        self.assertIn("foundations", stored["annotation_bundle"])
        self.assertEqual(self.service.sample_library.storage_stats()["datasets"], 2)

    def _write_script(self, name, body):
        path = self.root / "scripts" / name
        path.write_text(body)
        path.chmod(0o755)

    def _wait(self, job_id, timeout=5):
        deadline = time.time() + timeout
        while time.time() < deadline:
            job = self.service.store.get(job_id)
            if job["status"] in {"succeeded", "failed", "cancelled", "interrupted"}:
                return job
            time.sleep(0.02)
        self.fail("job did not finish")

    def _wait_resource(self, job_id, timeout=5):
        deadline = time.time() + timeout
        while time.time() < deadline:
            jobs = {
                job["id"]: job for job in self.service.resource_downloads()
            }
            job = jobs[job_id]
            if job["status"] in {"succeeded", "failed", "interrupted"}:
                return job
            time.sleep(0.02)
        self.fail("resource download did not finish")

    def _wait_storage(self, job_id, timeout=10):
        deadline = time.time() + timeout
        while time.time() < deadline:
            jobs = {job["id"]: job for job in self.service.storage_migrations()}
            job = jobs[job_id]
            if job["status"] in {"succeeded", "failed"}:
                return job
            time.sleep(0.02)
        self.fail("storage migration did not finish")

    def test_annotation_root_rebases_generated_config(self):
        annotation_root = Path(self.temp.name) / "external-annotations"
        registry = StorageLocationRegistry(
            self.root, self.state,
            registry_path=Path(self.temp.name) / "storage-bootstrap.json",
            persist=True,
        )
        registry.set_root("annotation", annotation_root)
        self.service.shutdown()
        self.service = AnnotationJobService(self.root, self.state, storage_registry=registry)
        generated = self.service._write_job_config(
            "annotation-root-test",
            self.root / "config" / "annotation.config.yaml",
            {},
        )
        content = generated.read_text()
        self.assertIn(str(annotation_root / "vep_cache"), content)
        self.assertIn(str(annotation_root / "regions" / "coding.bed.gz"), content)

    def test_storage_location_can_be_saved_for_future_data_without_switching_live_store(self):
        target = Path(self.temp.name) / "future-library"
        result = self.service.set_storage_location({"kind": "data", "path": str(target)})
        self.assertTrue(result["restart_required"])
        self.assertTrue((target / ".iei-variant-review-storage.json").is_file())
        self.assertEqual(self.service.storage_registry.root("data"), target.resolve())
        self.assertEqual(self.service.state_dir, self.state.resolve())
        self.assertTrue(result["storage"]["locations"][1]["restart_required"])

    def test_storage_configuration_reports_roots_and_used_space(self):
        configuration = self.service.storage_configuration()
        locations = {item["id"]: item for item in configuration["locations"]}
        self.assertEqual(set(locations), {"annotation", "data", "temporary"})
        self.assertEqual(locations["data"]["active_path"], str(self.state.resolve()))
        self.assertIsInstance(locations["data"]["used_bytes"], int)
        self.assertTrue(locations["temporary"]["included_with_data"])
        self.assertIsNone(locations["temporary"]["used_bytes"])

    def test_corrupt_bootstrap_recovers_from_backup_and_never_defaults(self):
        registry_path = Path(self.temp.name) / "bootstrap.json"
        registry = StorageLocationRegistry(
            self.root, self.state, registry_path=registry_path, persist=True
        )
        selected = Path(self.temp.name) / "selected-data"
        registry.set_root("data", selected, storage_id="expected-drive")
        registry_path.write_text("{truncated", encoding="utf-8")
        recovered = StorageLocationRegistry(
            self.root, self.state, registry_path=registry_path, persist=True
        )
        self.assertEqual(recovered.root("data"), selected.resolve())
        self.assertEqual(recovered.storage_id("data"), "expected-drive")
        registry_path.write_text("{bad", encoding="utf-8")
        recovered.backup_path.write_text("{also-bad", encoding="utf-8")
        with self.assertRaises(StorageRegistryError):
            StorageLocationRegistry(
                self.root, self.state, registry_path=registry_path, persist=True
            )

    def test_storage_marker_rejects_a_different_drive_at_same_path(self):
        target = Path(self.temp.name) / "marked-library"
        self.service.set_storage_location({"kind": "data", "path": str(target)})
        marker_path = target / ".iei-variant-review-storage.json"
        marker = json.loads(marker_path.read_text())
        marker["storage_id"] = "different-drive"
        marker_path.write_text(json.dumps(marker), encoding="utf-8")
        with self.assertRaises(StorageRegistryError):
            self.service.storage_registry.validate_marker(
                "data", target, required=True
            )

    def test_annotation_location_can_be_read_only(self):
        annotation = Path(self.temp.name) / "read-only-annotations"
        annotation.mkdir()
        with patch(
            "local_service.workbench_service.os.access",
            side_effect=lambda _path, mode: mode == os.R_OK,
        ):
            tested = self.service.test_storage_location({
                "kind": "annotation", "path": str(annotation),
            })
        self.assertTrue(tested["ready"])

    def test_toolbox_path_is_not_mistaken_for_box_cloud_storage(self):
        self.assertEqual(
            storage_path_warning(Path(self.temp.name) / "Toolbox" / "iei", "data"),
            "",
        )

    def test_filesystem_probe_treats_permission_errors_as_unavailable(self):
        with patch(
            "local_service.storage_locations.Path.exists",
            side_effect=PermissionError("blocked parent"),
        ):
            self.assertEqual(_filesystem_type(Path("/blocked/storage")), "")

    def test_storage_description_treats_permission_errors_as_unavailable(self):
        with patch(
            "local_service.storage_locations.Path.is_dir",
            side_effect=PermissionError("blocked parent"),
        ):
            described = self.service.storage_registry.describe("data")
        self.assertFalse(described["exists"])
        self.assertFalse(described["available"])

    def test_macos_filesystem_probe_reads_df_type_column(self):
        completed = Mock(
            returncode=0,
            stdout=(
                "Filesystem Type 512-blocks Used Available Capacity Mounted on\n"
                "/dev/disk3s5 apfs 100 25 75 25% /System/Volumes/Data\n"
            ),
        )
        with patch("local_service.storage_locations.platform.system", return_value="Darwin"), patch(
            "local_service.storage_locations.subprocess.run", return_value=completed
        ) as run:
            self.assertEqual(_filesystem_type(self.state), "apfs")
        self.assertEqual(run.call_args.args[0][:3], ["df", "-Y", "-P"])

    def test_parallel_download_credit_uses_allocated_not_apparent_size(self):
        destination = Path(self.temp.name) / "large-resource.gz"
        parallel = Path(str(destination) + ".parallel")
        with parallel.open("wb") as handle:
            handle.truncate(8 * 1024 * 1024)
            handle.seek(0)
            handle.write(b"x" * 4096)
        status = parallel.stat()
        expected = min(status.st_size, int(getattr(status, "st_blocks", 0) or 0) * 512)
        self.assertEqual(
            self.service._resumable_download_credit(destination, False), expected
        )
        self.assertLessEqual(expected, status.st_size)

    def test_bulk_download_credit_counts_existing_directory_payload(self):
        destination = Path(self.temp.name) / "existing-cache"
        destination.mkdir()
        (destination / "installed.dat").write_bytes(b"x" * 4096)
        self.assertGreaterEqual(
            self.service._resumable_download_credit(destination, True),
            4096,
        )

    def test_pending_location_change_blocks_data_mutations_until_restart(self):
        target = Path(self.temp.name) / "pending-library"
        self.service.set_storage_location({"kind": "data", "path": str(target)})
        with self.assertRaisesRegex(ValueError, "restart"):
            self.service.begin_storage_mutation()

    def test_failed_migration_removes_verified_staging_copy(self):
        target = Path(self.temp.name) / "failed-library"

        def fail_copy(_source, destination, _copied):
            destination.mkdir(parents=True, exist_ok=True)
            (destination / "partial.bin").write_bytes(b"partial")
            raise RuntimeError("simulated copy failure")

        with patch.object(self.service, "_copy_data_root_verified", side_effect=fail_copy):
            job = self.service.start_storage_migration({"kind": "data", "path": str(target)})
            completed = self._wait_storage(job["id"])
        self.assertEqual(completed["status"], "failed")
        self.assertFalse(target.exists())
        self.assertEqual(list(target.parent.glob(f".{target.name}.iei-migrating-*")), [])

    def test_post_activation_history_failure_preserves_the_destination(self):
        # Audit repro (SVC-3): a non-OSError raised by record_migration AFTER
        # set_root had activated the new root escaped to the cleanup path,
        # which rmtree'd the destination the registry now pointed at — total
        # loss of the migrated data. The destination must survive.
        target = Path(self.temp.name) / "activated-library"
        original = self.service.storage_registry.record_migration

        def flaky_history(job):
            if job.get("status") == "succeeded":
                raise TypeError("simulated history serialization failure")
            return original(job)

        with patch.object(
            self.service.storage_registry, "record_migration",
            side_effect=flaky_history,
        ):
            job = self.service.start_storage_migration({"kind": "data", "path": str(target)})
            completed = self._wait_storage(job["id"])
        self.assertTrue(target.exists(), "activated destination must never be deleted")
        self.assertEqual(completed["status"], "succeeded", completed.get("error"))

    def test_temporary_paths_are_rewritten_only_after_copy_activation(self):
        upload = self.state / "uploads" / "batch" / "patient.vcf"
        upload.parent.mkdir(parents=True)
        upload.write_text("test", encoding="utf-8")
        target = Path(self.temp.name) / "migrated-workspace"
        original = self.service._rewrite_temporary_paths

        def checked_rewrite(old_root, new_root):
            self.assertTrue(Path(new_root).is_dir())
            return original(old_root, new_root)

        with patch.object(self.service, "_rewrite_temporary_paths", side_effect=checked_rewrite):
            job = self.service.start_storage_migration({"kind": "temporary", "path": str(target)})
            completed = self._wait_storage(job["id"])
        self.assertEqual(completed["status"], "succeeded", completed.get("error"))
        self.assertTrue((target / "uploads" / "batch" / "patient.vcf").is_file())

    def test_legacy_absolute_paths_do_not_use_sql_like_wildcards(self):
        state = Path(self.temp.name) / "IEI_data"
        sibling = Path(self.temp.name) / "IEIXdata"
        sibling.mkdir()
        sibling_source = sibling / "patient.vcf"
        write_vcf(sibling_source)
        service = AnnotationJobService(self.root, state, start_worker=False)
        try:
            review = state / "uploads" / "review.vcf"
            review.parent.mkdir(parents=True)
            write_vcf(review)
            service.cohort.hts_backend = None
            imported = service.import_sample_library({
                "sources": [{"path": str(review)}],
                "analysis_scope": "exome",
                "include_in_cohort": False,
            })
            dataset_id = imported["datasets"][0]["id"]
            with sqlite3.connect(state / "cohort.sqlite3") as connection:
                connection.execute(
                    "UPDATE library_datasets SET original_path=? WHERE id=?",
                    (str(sibling_source), dataset_id),
                )
                connection.execute(
                    "DELETE FROM sample_library_meta WHERE key='portable_paths_v2'"
                )
        finally:
            service.shutdown()
        reopened = AnnotationJobService(self.root, state, start_worker=False)
        try:
            record = reopened.sample_library.get(dataset_id)
            self.assertEqual(Path(record["original_path"]).resolve(), sibling_source.resolve())
        finally:
            reopened.shutdown()

    def test_data_migration_copies_library_and_rewrites_managed_paths(self):
        review = self.root / "review-storage.vcf"
        write_vcf(review)
        self.service.cohort.hts_backend = None
        imported = self.service.import_sample_library({
            "sources": [{"path": str(review)}],
            "analysis_scope": "exome",
            "include_in_cohort": False,
        })
        target = Path(self.temp.name) / "migrated-library"
        job = self.service.start_storage_migration({"kind": "data", "path": str(target)})
        completed = self._wait_storage(job["id"])
        self.assertEqual(completed["status"], "succeeded", completed.get("error"))
        self.assertTrue((target / "cohort.sqlite3").is_file())
        self.assertTrue((target / "sample-library").is_dir())
        self.service.shutdown()
        self.service = AnnotationJobService(self.root, target, storage_registry=self.service.storage_registry)
        record = self.service.sample_library.get(imported["datasets"][0]["id"])
        self.assertIsNotNone(record)
        assert record is not None
        self.assertTrue(record["managed_path"].startswith(str(target.resolve())))
        self.assertTrue(self.service.sample_library.file(record["id"]).is_file())
        with sqlite3.connect(target / "cohort.sqlite3") as connection:
            stored = connection.execute("SELECT managed_path FROM library_datasets LIMIT 1").fetchone()[0]
        self.assertFalse(Path(stored).is_absolute())
        history = self.service.storage_migrations()
        history_record = next(item for item in history if item["id"] == job["id"])
        self.assertTrue(history_record["original_retained"])

    def test_sample_library_paths_inside_data_root_are_portable(self):
        source = self.state / "uploads" / "patient.vcf"
        source.parent.mkdir(parents=True)
        write_vcf(source)
        self.service.cohort.hts_backend = None
        imported = self.service.import_sample_library({
            "sources": [{"path": str(source)}],
            "analysis_scope": "exome",
            "include_in_cohort": False,
        })
        dataset_id = imported["datasets"][0]["id"]
        with sqlite3.connect(self.state / "cohort.sqlite3") as connection:
            stored = connection.execute(
                "SELECT original_path,managed_path FROM library_datasets WHERE id=?",
                (dataset_id,),
            ).fetchone()
        self.assertFalse(Path(stored[0]).is_absolute())
        self.assertFalse(Path(stored[1]).is_absolute())
        record = self.service.sample_library.get(dataset_id)
        self.assertEqual(record["original_path"], str(source.resolve()))

    def test_active_storage_migration_blocks_new_writes(self):
        with self.service._storage_lock:
            self.service._storage_jobs["test-migration"] = {
                "id": "test-migration",
                "status": "running",
                "created_at": "2026-01-01T00:00:00+00:00",
            }
        with self.assertRaisesRegex(ValueError, "migration is running"):
            self.service.submit({
                "input_path": str(self.input),
                "output_path": str(self.output),
            })

    def test_wsl_storage_path_accepts_windows_drive_spelling(self):
        with patch.dict(os.environ, {"WSL_DISTRO_NAME": "Ubuntu"}, clear=False):
            path = self.service._safe_storage_path(r"D:\IEI data\library")
        self.assertEqual(path, Path("/mnt/d/IEI data/library").resolve())

    def test_missing_configured_data_root_does_not_create_a_fallback_library(self):
        missing = Path(self.temp.name) / "disconnected-drive" / "library"
        registry = StorageLocationRegistry(
            self.root, self.state, persist=False,
            registry_path=Path(self.temp.name) / "bootstrap.json",
        )
        registry.set_root("data", missing)
        with self.assertRaisesRegex(ValueError, "will not create a fallback database"):
            AnnotationJobService(self.root, missing, storage_registry=registry)
        self.assertFalse(missing.exists())

    def test_ccre_context_distinguishes_overlap_from_no_overlap(self):
        overlap = self.service.ccre_context({
            "chrom": "1", "pos": 100, "ref": "A", "alt": "G",
        })
        self.assertEqual(overlap["status"], "overlap")
        self.assertEqual(overlap["overlaps"][0]["accession"], "EH38E0000001")
        self.assertEqual(
            {gene["symbol"] for gene in overlap["overlaps"][0]["nearby_genes"]},
            {"NEAR1", "NEAR2"},
        )
        no_overlap = self.service.ccre_context({
            "chrom": "1", "pos": 500, "ref": "A", "alt": "G",
        })
        self.assertEqual(no_overlap["status"], "no_overlap")

    def test_job_runs_and_persists_command(self):
        job = self.service.submit(
            {"input_path": str(self.input), "output_path": str(self.output)}
        )
        completed = self._wait(job["id"])
        self.assertEqual(completed["status"], "succeeded")
        self.assertEqual(completed["final_output_path"], str(self.output.resolve()))
        self.assertTrue(self.output.exists())
        self.assertEqual(self.service.review_file(job["id"]), self.output.resolve())
        self.assertEqual(len(completed["command"]), 2)
        self.assertIn("preflight.sh", completed["command"][0][1])

        reopened = JobStore(self.state / "workbench.sqlite3")
        self.assertEqual(reopened.get(job["id"])["status"], "succeeded")

    def test_defaults_keep_coding_pass_and_clinvar(self):
        job = self.service.submit(
            {"input_path": str(self.input), "output_path": str(self.output)}
        )
        self.assertTrue(job["coding_only"])
        self.assertFalse(job["include_filtered"])
        self.assertTrue(job["use_clinvar"])
        self.assertEqual(job["input_assembly"], "auto")
        self.assertEqual(self.service.capabilities()["defaults"]["input_assembly"], "auto")
        hardware = self.service.capabilities()["hardware"]
        self.assertGreaterEqual(hardware["logical_cpus"], 1)
        self.assertGreaterEqual(hardware["recommended_vep_workers"], 1)
        self.assertLessEqual(
            hardware["recommended_vep_workers"],
            hardware["max_vep_workers"],
        )

    def test_rejects_invalid_output_and_missing_input(self):
        with self.assertRaisesRegex(ValueError, "does not exist"):
            self.service.submit(
                {
                    "input_path": str(self.root / "missing.vcf.gz"),
                    "output_path": str(self.output),
                }
            )
        with self.assertRaisesRegex(ValueError, "must end in .vcf.gz"):
            self.service.submit(
                {"input_path": str(self.input), "output_path": str(self.root / "x.txt")}
            )
        invalid_input = self.root / "patient.txt"
        invalid_input.write_text("not a VCF")
        with self.assertRaisesRegex(ValueError, "must end in .vcf or .vcf.gz"):
            self.service.submit(
                {"input_path": str(invalid_input), "output_path": str(self.output)}
            )

    def test_worker_commands_do_not_use_a_shell_string(self):
        job = self.service.submit(
            {
                "input_path": str(self.input),
                "output_path": str(self.output),
                "coding_only": False,
                "include_filtered": True,
                "use_clinvar": False,
                "input_assembly": "GRCh37",
            }
        )
        completed = self._wait(job["id"])
        annotation = completed["command"][1]
        self.assertIn("--all-variants", annotation)
        self.assertIn("--include-filtered", annotation)
        self.assertIn("--no-clinvar", annotation)
        self.assertEqual(
            annotation[annotation.index("--input-assembly") + 1],
            "GRCh37",
        )
        self.assertIsInstance(json.loads(json.dumps(completed["command"])), list)

    def test_browser_selected_vcf_is_streamed_to_local_staging(self):
        payload = b"##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
        staged = self.service.stage_file(
            "case.vcf.gz",
            "batch-folder/case.vcf.gz",
            "test-batch",
            len(payload),
            io.BytesIO(payload),
        )
        path = Path(staged["path"])
        self.assertTrue(path.is_file())
        self.assertEqual(path.read_bytes(), payload)
        self.assertEqual(staged["relative_path"], "batch-folder/case.vcf.gz")

    def test_ui_annotation_options_create_a_per_job_config(self):
        dbnsfp = self.root / "references" / "dbnsfp.vcf.gz"
        dbnsfp.parent.mkdir(parents=True, exist_ok=True)
        dbnsfp.write_bytes(gzip.compress(
            b"#chr\tpos(1-based)\tref\talt\tCADD_phred\tMPC_score\t"
            b"ESM1b_score\tESM1b_pred\n"
        ))
        (self.root / "config" / "annotation.config.yaml").write_text(
            "container:\n  vep_image_tag: release_113.4\n"
            "reference:\n  species: homo_sapiens\n"
            "  vep_cache_dir: references/vep_cache\n"
            "region:\n  coding_only: true\n"
            "plugins:\n  dbNSFP:\n"
            "    enabled: true\n"
            "    required: true\n"
            f"    path: {json.dumps(str(dbnsfp))}\n"
            "    columns:\n      - CADD_phred\n"
        )
        job = self.service.submit({
            "input_path": str(self.input),
            "output_path": str(self.output),
            "annotation_options": {
                "repeatmasker": False,
                "segdup": False,
                "fork": 3,
                "dbnsfp_predictors": ["mpc", "esm1b"],
            },
        })
        generated = Path(job["config_path"])
        self.assertNotEqual(
            generated,
            (self.root / "config" / "annotation.config.yaml").resolve(),
        )
        self.assertTrue(generated.is_file())
        text = generated.read_text()
        self.assertIn("fork: 3", text)
        self.assertIn("- CADD_phred", text)
        self.assertIn("- MPC_score", text)
        self.assertIn("- ESM1b_score", text)
        self.assertIn("- ESM1b_pred", text)

    def test_ui_annotation_options_reject_unknown_dbnsfp_predictor(self):
        with self.assertRaisesRegex(ValueError, "unsupported dbNSFP predictor"):
            self.service.submit({
                "input_path": str(self.input),
                "output_path": str(self.output),
                "annotation_options": {
                    "dbnsfp_predictors": ["not_a_predictor"],
                },
            })

    def test_container_status_distinguishes_stopped_runtime_from_missing_image(self):
        completed = Mock(
            returncode=1,
            stdout="",
            stderr=(
                "failed to connect to the docker API at "
                "unix:///Users/test/.docker/run/docker.sock"
            ),
        )
        with patch(
            "local_service.workbench_service.shutil.which",
            return_value="/usr/local/bin/docker",
        ), patch(
            "local_service.workbench_service.subprocess.run",
            return_value=completed,
        ):
            status = self.service._container_image_status({
                "container": {"runtime": "docker", "image": "vep-annotate:latest"},
            })
        self.assertFalse(status["available"])
        self.assertEqual(status["state"], "runtime_unavailable")
        self.assertIn("not running", status["message"])
        self.assertNotIn("not installed", status["message"])

    def test_container_status_reports_missing_image_when_runtime_is_running(self):
        completed = Mock(
            returncode=1,
            stdout="[]\n",
            stderr="Error: No such image: vep-annotate:latest\n",
        )
        with patch(
            "local_service.workbench_service.shutil.which",
            return_value="/usr/local/bin/docker",
        ), patch(
            "local_service.workbench_service.subprocess.run",
            return_value=completed,
        ):
            status = self.service._container_image_status({
                "container": {"runtime": "docker", "image": "vep-annotate:latest"},
            })
        self.assertFalse(status["available"])
        self.assertEqual(status["state"], "image_missing")
        self.assertIn("bash docker/build.sh", status["message"])

    def test_stopped_runtime_is_not_duplicated_as_dataset_profile_error(self):
        stopped = {
            "available": False,
            "state": "runtime_unavailable",
            "runtime": "docker",
            "image": "vep-annotate:latest",
            "message": (
                "Docker is installed but is not running. Start Docker Desktop, "
                "then refresh this page."
            ),
        }
        with patch.object(self.service, "_container_image_status", return_value=stopped):
            profile = self.service._annotation_profile()
        self.assertFalse(profile["execution_ready"])
        self.assertEqual(profile["error"], "")
        container = next(
            item for item in profile["foundations"]
            if item["id"] == "vep_container"
        )
        self.assertEqual(container["message"], stopped["message"])
        self.assertIn("exome", profile["recommended_profiles"])
        self.assertIn("whole_genome", profile["recommended_profiles"])

    def test_installed_recommended_profile_skips_large_free_space_scan(self):
        profile = {
            "recommended_profiles": {
                "exome": {"installed": True, "missing": []},
                "whole_genome": {"installed": True, "missing": []},
            }
        }
        with patch.object(self.service, "_annotation_profile", return_value=profile), patch.object(
            self.service, "_ensure_annotation_download_space"
        ) as ensure_space, patch.object(
            self.service, "_write_resource_config", return_value=self.root / "config" / "annotation.config.yaml"
        ), patch.object(
            self.service, "_start_resource_job", return_value={"status": "queued"}
        ) as start_job:
            result = self.service.start_resource_download("recommended_wgs")
        ensure_space.assert_not_called()
        start_job.assert_called_once()
        self.assertEqual(result["status"], "queued")

    def test_spliceai_lookup_fetches_once_then_serves_from_cache(self):
        canned = {
            "variant": "8-140300616-T-G",
            "scores": [
                {
                    "DS_AG": "0.045", "DS_AL": "0.827", "DS_DG": "0.000", "DS_DL": "0.000",
                    "DP_AG": -32, "DP_AL": -2, "DP_DG": 66, "DP_DL": -147,
                    "g_name": "TRAPPC9", "t_id": "ENST00000438773.4",
                    "t_refseq_ids": ["NM_001160372.4"], "t_priority": "N", "t_strand": "-",
                },
                {
                    "DS_AG": "0.045", "DS_AL": "0.827", "DS_DG": "0.000", "DS_DL": "0.000",
                    "DP_AG": -32, "DP_AL": -2, "DP_DG": 66, "DP_DL": -147,
                    "g_name": "TRAPPC9", "t_id": "ENST00000438774.1",
                    "t_refseq_ids": [], "t_priority": "MS", "t_strand": "-",
                },
            ],
        }
        calls = []

        class FakeResponse(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        def fake_urlopen(request, timeout=0):
            calls.append(request.full_url)
            return FakeResponse(json.dumps(canned).encode("utf-8"))

        import local_service.workbench_service as module
        with patch.object(module.urllib.request, "urlopen", fake_urlopen):
            first = self.service.spliceai_lookup(
                {"chrom": "chr8", "pos": 140300616, "ref": "t", "alt": "g"}
            )
            second = self.service.spliceai_lookup(
                {"chrom": "8", "pos": 140300616, "ref": "T", "alt": "G"}
            )
        self.assertEqual(len(calls), 1)
        self.assertIn("variant=8-140300616-T-G", calls[0])
        self.assertFalse(first["cached"])
        self.assertTrue(second["cached"])
        self.assertEqual(first["source"], "Broad SpliceAI Lookup API")
        self.assertTrue(first["masked"])
        # MANE Select transcript is sorted first even though the API listed it second.
        self.assertTrue(first["transcripts"][0]["mane_select"])
        self.assertEqual(
            first["transcripts"][0]["scores"]["acceptor_loss"],
            {"delta": "0.827", "position": -2},
        )

    def test_spliceai_lookup_rejects_bad_input_and_reports_api_errors(self):
        with self.assertRaisesRegex(ValueError, "unsupported chromosome"):
            self.service.spliceai_lookup({"chrom": "chr99", "pos": 5, "ref": "A", "alt": "T"})
        with self.assertRaisesRegex(ValueError, "plain ACGT"):
            self.service.spliceai_lookup({"chrom": "1", "pos": 5, "ref": "A", "alt": "<DEL>"})

        class FakeResponse(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        import local_service.workbench_service as module
        with patch.object(
            module.urllib.request, "urlopen",
            lambda request, timeout=0: FakeResponse(
                json.dumps({"error": "rate limit exceeded"}).encode("utf-8")
            ),
        ):
            with self.assertRaisesRegex(ValueError, "rate limit exceeded"):
                self.service.spliceai_lookup(
                    {"chrom": "1", "pos": 1000, "ref": "AT", "alt": "A"}
                )

    def test_native_resource_picker_returns_selected_folder_without_user_path_typing(self):
        selected = Path(self.temp.name) / "dbNSFP5.3.1a"
        selected.mkdir()
        completed = Mock(returncode=0, stdout=str(selected) + "\n", stderr="")
        with patch(
            "local_service.workbench_service.platform.system", return_value="Darwin"
        ), patch(
            "local_service.workbench_service.subprocess.run", return_value=completed
        ) as run:
            result = self.service.choose_local_resource_source({"resource_id": "dbnsfp"})
        self.assertFalse(result["cancelled"])
        self.assertEqual(result["path"], str(selected.resolve()))
        self.assertEqual(result["selection_type"], "folder")
        self.assertEqual(run.call_args.args[0][:2], ["osascript", "-e"])

    def test_native_resource_picker_treats_cancel_as_no_selection(self):
        completed = Mock(returncode=1, stdout="", stderr="User canceled.")
        with patch(
            "local_service.workbench_service.platform.system", return_value="Darwin"
        ), patch(
            "local_service.workbench_service.subprocess.run", return_value=completed
        ):
            result = self.service.choose_local_resource_source({"resource_id": "promoterai"})
        self.assertTrue(result["cancelled"])

    def test_resource_progress_lines_become_readable_stage_messages(self):
        update = AnnotationJobService._resource_progress_update
        # A section marker sets the stage and resets the bar.
        stage, ui = update("", "[20:33:41] === reference FASTA ===\n")
        self.assertEqual(stage, "reference FASTA")
        self.assertEqual(ui, {"message": "reference FASTA…", "progress": None})
        # parallel_fetch progress carries percent, volume, and speed.
        stage, ui = update("VEP cache (release 113, GRCh38)", " 37.2%    8.6 GiB    3.2 MiB/s\n")
        self.assertEqual(ui["progress"], 37.2)
        self.assertEqual(ui["message"], "VEP cache (release 113, GRCh38) — 37% · 8.6 GiB · 3.2 MiB/s")
        # curl -# progress-bar lines carry an explicit percentage.
        stage, ui = update("reference FASTA", "########                       27.4%\n")
        self.assertEqual(ui["progress"], 27.4)
        self.assertEqual(ui["message"], "reference FASTA — 27%")
        # Raw curl transfer-table noise (no % sign) is dropped entirely.
        stage, ui = update(
            "reference FASTA",
            " 27  841M   27  230M    0     0  777k      0  0:18:27  0:05:03  0:13:24  983k\n",
        )
        self.assertIsNone(ui)
        # Ordinary log lines pass through without the timestamp.
        stage, ui = update("reference FASTA", "[20:33:45] WARN: refetching from scratch\n")
        self.assertEqual(ui, {"message": "WARN: refetching from scratch"})

    def test_service_restart_marks_process_for_supervised_relaunch(self):
        result = self.service.request_service_restart()
        self.assertTrue(result["restarting"])
        self.assertEqual(result["exit_code"], 75)
        self.assertTrue(self.service.restart_requested)

    def test_service_restart_refuses_while_work_is_running(self):
        with patch.object(
            self.service.store, "list",
            return_value=[{"id": "job-1", "status": "running"}],
        ):
            with self.assertRaises(ValueError) as context:
                self.service.request_service_restart()
        self.assertIn("before restarting", str(context.exception))
        self.assertFalse(self.service.restart_requested)

    def test_native_resource_picker_supports_storage_locations(self):
        # The Storage page's Change location editor offers Browse via the same
        # native chooser used for dbNSFP/PromoterAI source selection.
        selected = Path(self.temp.name) / "external-annotation"
        selected.mkdir()
        completed = Mock(returncode=0, stdout=str(selected) + "\n", stderr="")
        with patch(
            "local_service.workbench_service.platform.system", return_value="Darwin"
        ), patch(
            "local_service.workbench_service.subprocess.run", return_value=completed
        ):
            for resource_id in ("storage_annotation", "storage_data", "storage_temporary"):
                result = self.service.choose_local_resource_source({"resource_id": resource_id})
                self.assertFalse(result["cancelled"])
                self.assertEqual(result["selection_type"], "folder")
                self.assertEqual(result["path"], str(selected.resolve()))

    def test_user_supplied_dataset_preparation_targets_managed_annotation_storage(self):
        generated = self.service._write_resource_config("dbnsfp")
        text = generated.read_text()
        expected = self.service.annotation_root / "dbnsfp" / "dbNSFP5.4a_grch38.gz"
        self.assertIn(str(expected), text)

        generated = self.service._write_resource_config("logofunc")
        text = generated.read_text()
        self.assertIn(str(self.service.annotation_root / "logofunc"), text)

    def test_derived_aa_match_feature_reports_installed(self):
        # Regression for the P4-9 follow-up: clinvar_aa_match ships with the
        # software and its residue table is derived automatically from the
        # ClinVar release, so it has no installable paths. The "unconfigured
        # dataset => not installed" rule must not label it "Bundled file
        # missing" — while genuinely path-less datasets (e.g. ccre without a
        # configured BED) must still report not installed.
        sources = {
            source["id"]: source
            for source in self.service.capabilities()["annotation_profile"]["sources"]
        }
        self.assertTrue(sources["clinvar_aa_match"]["installed"])

    def test_annotation_scope_controls_wgs_only_sources(self):
        sources = {
            source["id"]: source
            for source in self.service.capabilities()["annotation_profile"]["sources"]
        }
        self.assertEqual(sources["promoterai"]["available_in"], ["whole_genome"])
        self.assertEqual(sources["cadd_wgs"]["available_in"], ["whole_genome"])
        self.assertEqual(sources["cadd_wgs"]["setup_mode"], "download")
        self.assertEqual(sources["cadd_wgs"]["download_id"], "cadd_wgs")
        self.assertEqual(len(sources["cadd_wgs"]["configured_paths"]), 2)
        self.assertFalse(sources["cadd_wgs"]["installed"])
        self.assertEqual(sources["ccre"]["available_in"], ["whole_genome"])
        self.assertEqual(sources["ccre"]["download_id"], "ccre")
        self.assertEqual(sources["ccre"]["setup_mode"], "bundled")
        self.assertEqual(sources["liftover"]["setup_mode"], "bundled")
        self.assertEqual(sources["dbnsfp"]["access"], "registration")
        self.assertEqual(sources["dbnsfp"]["prepare_id"], "dbnsfp")
        self.assertEqual(sources["promoterai"]["access"], "license")
        self.assertEqual(sources["logofunc"]["recommendation"], "optional")
        self.assertFalse(sources["logofunc"]["enabled"])
        self.assertTrue(sources["ccre"]["installed"])
        with self.assertRaisesRegex(ValueError, "only for whole-genome"):
            self.service.submit({
                "input_path": str(self.input),
                "output_path": str(self.output),
                "analysis_scope": "exome",
                "annotation_options": {"promoterai": True},
            })

        job = self.service.submit({
            "input_path": str(self.input),
            "output_path": str(self.output),
            "analysis_scope": "whole_genome",
            "annotation_options": {
                "promoterai": False,
                "cadd_wgs": False,
            },
        })
        self.assertEqual(job["analysis_scope"], "whole_genome")
        self.assertFalse(job["coding_only"])
        generated = Path(job["config_path"]).read_text()
        self.assertIn("coding_only: false", generated)

        cadd_job = self.service.submit({
            "input_path": str(self.input),
            "output_path": str(self.root / "results" / "patient.cadd.vep.vcf.gz"),
            "analysis_scope": "whole_genome",
            "annotation_options": {"cadd_wgs": True},
        })
        cadd_config = Path(cadd_job["config_path"]).read_text()
        self.assertRegex(cadd_config, r"CADD_WGS:\n\s+enabled: true")

    def test_wgs_review_service_prepares_filters_and_registers_output(self):
        source = self.root / "annotated-wgs.vcf"
        write_parallel_vcf(source)
        self.service.cohort.hts_backend = FakeHtsBackend()
        self.service.cohort.index_readers = 4

        progress_updates = []
        result = self.service.prefilter_wgs_review({
            "path": str(source),
            "filters": {
                "max_gnomad_popmax": 0.01,
                "min_spliceai": 0.5,
                "min_promoterai_abs": 0.8,
                "noncoding_mode": "ccre",
            },
        }, progress=progress_updates.append)

        self.assertEqual(result["reader_count"], 4)
        self.assertEqual(result["records_scanned"], 4)
        self.assertEqual(result["records_retained"], 1)
        output = self.service.wgs_review_file(result["id"])
        self.assertTrue(output.is_file())
        self.assertTrue(Path(result["index_path"]).is_file())
        phases = {update["phase"] for update in progress_updates}
        self.assertTrue({
            "preparing_index", "filtering", "merging", "compressing",
            "indexing_output", "complete",
        }.issubset(phases))
        self.assertEqual(progress_updates[-1]["progress"], 100.0)
        cached = self.service.prefilter_wgs_review({
            "path": str(source),
            "filters": {"noncoding_mode": "ccre"},
        })
        self.assertTrue(cached["cache_hit"])
        self.assertEqual(cached["records_retained"], 1)

        job = self.service.start_wgs_review({
            "path": str(source),
            "filters": {"noncoding_mode": "ccre"},
        })
        deadline = time.time() + 5
        while job["status"] in {"queued", "running"} and time.time() < deadline:
            time.sleep(0.02)
            job = self.service.get_wgs_review_job(job["id"])
        self.assertEqual(job["status"], "succeeded")
        self.assertEqual(job["phase"], "complete")
        self.assertEqual(job["progress"], 100.0)
        self.assertEqual(job["records_scanned"], 4)
        self.assertIsNotNone(job["result"])

        cohort_job = self.service.start_cohort_import({
            "paths": [str(source)],
            "import_profile": "prefiltered",
            "filters": {"noncoding_mode": "ccre"},
        })
        deadline = time.time() + 5
        while cohort_job["status"] in {"queued", "running"} and time.time() < deadline:
            time.sleep(0.02)
            cohort_job = self.service.cohort.get_import_job(cohort_job["id"])
        self.assertEqual(cohort_job["status"], "succeeded")
        self.assertEqual(cohort_job["import_profile"], "prefiltered")
        self.assertEqual(cohort_job["prefilter_records_scanned"], 4)
        self.assertEqual(cohort_job["prefilter_records_retained"], 1)
        self.assertEqual(cohort_job["result"]["stats"]["prefiltered_files"], 1)
        indexed = self.service.cohort.query({
            "mode": "variant", "query": "1:100:A:G",
        })
        self.assertEqual(indexed["rows"][0]["import_profile"], "prefiltered")
        self.assertEqual(indexed["rows"][0]["promoterai"], -0.75)
        self.assertEqual(indexed["rows"][0]["source_path"], str(source.resolve()))

    def test_resource_download_jobs_are_constrained_and_report_progress(self):
        # The disk-space preflight is real behavior but assumes real dataset
        # sizes (30-83 GiB); these fake downloads run in a temp dir that may
        # have far less free (CI runners), so it is not under test here.
        with patch.object(self.service, "_ensure_annotation_download_space"):
            first = self.service.start_resource_download("spliceai")
            same = self.service.start_resource_download("spliceai")
            self.assertEqual(first["id"], same["id"])
            completed = self._wait_resource(first["id"])
            self.assertEqual(completed["status"], "succeeded")
            self.assertEqual(completed["progress"], 100.0)
            self.assertIn("complete", completed["log"])

            clinvar = self.service.start_resource_download("clinvar")
            self.assertEqual(self._wait_resource(clinvar["id"])["status"], "succeeded")
            logofunc = self.service.start_resource_download("logofunc")
            completed_logofunc = self._wait_resource(logofunc["id"])
            self.assertEqual(completed_logofunc["status"], "succeeded")
            self.assertEqual(completed_logofunc["resource_id"], "logofunc")
            cadd = self.service.start_resource_download("cadd_wgs")
            completed_cadd = self._wait_resource(cadd["id"])
            self.assertEqual(completed_cadd["status"], "succeeded")
            self.assertEqual(completed_cadd["resource_id"], "cadd_wgs")
            self.assertIn("100.0% CADD", completed_cadd["log"])
            with self.assertRaisesRegex(ValueError, "cannot be downloaded"):
                self.service.start_resource_download("dbnsfp")

    def test_promoterai_preparation_requires_and_uses_the_two_local_files(self):
        source = self.root / "licensed-promoterai"
        source.mkdir()
        with self.assertRaisesRegex(ValueError, "missing"):
            self.service.start_promoterai_preparation({"source_dir": str(source)})
        (source / "tss.tsv").write_text("header\n")
        (source / "promoterAI_tss500.tsv.gz").write_bytes(b"gzip-placeholder")
        first = self.service.start_promoterai_preparation({"source_dir": str(source)})
        same = self.service.start_promoterai_preparation({"source_dir": str(source)})
        self.assertEqual(first["id"], same["id"])
        completed = self._wait_resource(first["id"])
        self.assertEqual(completed["resource_id"], "promoterai")
        self.assertEqual(completed["operation"], "preparation")
        self.assertEqual(completed["status"], "succeeded")
        self.assertEqual(completed["progress"], 100.0)

    def test_logofunc_preparation_accepts_an_existing_source_file_or_folder(self):
        source = self.root / "LoGoFuncVotingEnsemble_metadata_preds_final.csv.gz"
        source.write_bytes(b"source-placeholder")
        first = self.service.start_logofunc_preparation({"source_path": str(source)})
        completed = self._wait_resource(first["id"])
        self.assertEqual(completed["resource_id"], "logofunc")
        self.assertEqual(completed["operation"], "preparation")
        self.assertEqual(completed["status"], "succeeded")
        self.assertEqual(completed["progress"], 100.0)

    def test_loopback_http_health_and_job_list(self):
        server = create_server(self.service, "127.0.0.1", 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        port = server.server_address[1]
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/health", timeout=2
            ) as response:
                health = json.load(response)
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/jobs", timeout=2
            ) as response:
                jobs = json.load(response)
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/resource-downloads", timeout=2
            ) as response:
                resource_jobs = json.load(response)
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/gene-knowledge/status", timeout=2
            ) as response:
                gene_knowledge = json.load(response)
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/gene-knowledge/filters", timeout=2
            ) as response:
                gene_filters = json.load(response)
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/cohort/stats", timeout=2
            ) as response:
                cohort = json.load(response)
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/phenotypes/stats", timeout=2
            ) as response:
                phenotypes = json.load(response)
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/sample-library", timeout=2
            ) as response:
                library = json.load(response)
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/storage", timeout=2
            ) as response:
                storage = json.load(response)
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/storage/locations", timeout=2
            ) as response:
                locations = json.load(response)
            self.assertTrue(health["ok"])
            self.assertEqual(jobs, {"jobs": []})
            self.assertEqual(resource_jobs, {"jobs": []})
            self.assertFalse(gene_knowledge["omim"]["installed"])
            self.assertIn("iuis_category_genes", gene_filters)
            self.assertNotIn("clingen_classifications", gene_filters)
            self.assertEqual(cohort["individuals"], 0)
            self.assertEqual(phenotypes["individuals"], 0)
            self.assertEqual(library, {"datasets": []})
            self.assertEqual(storage["datasets"], 0)
            self.assertEqual(
                {item["id"] for item in locations["locations"]},
                {"annotation", "data", "temporary"},
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_loopback_http_cohort_import_and_query(self):
        cohort_vcf = self.root / "cohort.vep.vcf.gz"
        write_vcf(cohort_vcf)
        self.service.cohort.hts_backend = FakeHtsBackend()
        self.service.cohort.index_readers = 2
        server = create_server(self.service, "127.0.0.1", 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_address[1]}"

        def post(path, value):
            request = urllib.request.Request(
                base + path,
                data=json.dumps(value).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=5) as response:
                return json.load(response)

        try:
            import_job = post(
                "/api/cohort/import-jobs",
                {"paths": [str(cohort_vcf)]},
            )
            for _ in range(100):
                with urllib.request.urlopen(
                    base + f"/api/cohort/import-jobs/{import_job['id']}",
                    timeout=5,
                ) as response:
                    import_job = json.load(response)
                if import_job["status"] in {"succeeded", "failed"}:
                    break
                time.sleep(0.01)
            self.assertEqual(import_job["status"], "succeeded")
            self.assertEqual(import_job["processed_bytes"], import_job["total_bytes"])
            imported = import_job["result"]
            self.assertEqual(imported["imported"], 1)
            result = post(
                "/api/cohort/query",
                {"mode": "variant", "query": "1:100:A:G"},
            )
            self.assertEqual(result["total"], 1)
            self.assertEqual(result["rows"][0]["sample"], "P1")
            detail = post(
                "/api/cohort/variant-detail", {"variant_key": "1:100:A:G"}
            )
            self.assertEqual(detail["total"], 1)
            self.assertGreaterEqual(len(detail["annotations"]), 1)
            review = post("/api/cohort/review-records", {"selections": [{
                "variant_key": result["rows"][0]["variant_key"],
                "sample_entry_id": result["rows"][0]["sample_entry_id"],
            }]})
            self.assertEqual(review["resolved"], 1)
            self.assertIn("1\t100\trsExact\tA\tG", review["files"][0]["vcf"])
            self.assertNotIn("\tP2\n", review["files"][0]["vcf"])
            sample_review = post("/api/cohort/sample-review", {
                "sample_ids": [result["rows"][0]["sample_entry_id"]],
            })
            self.assertEqual(sample_review["sample_entries"], 1)
            self.assertEqual(sample_review["records"], 3)
            self.assertEqual(sample_review["analysis_scope"], "exome")
            self.assertIn("1\t400\t.\tT\tC", sample_review["files"][0]["vcf"])
            self.assertNotIn("\tP2\n", sample_review["files"][0]["vcf"])

            individual = post("/api/phenotypes/individual", {
                "individual_id": "CASE-P1",
                "sample_ids": ["P1"],
                "reported_race": ["Reported race"],
                "reported_ethnicity": ["Reported ethnicity"],
                "phenotype_summary": "Recurrent infections",
            })
            self.assertEqual(individual["individual_id"], "CASE-P1")
            with urllib.request.urlopen(
                base + "/api/phenotypes/by-sample/P1", timeout=5
            ) as response:
                by_sample = json.load(response)
            self.assertEqual(
                by_sample["individuals"][0]["reported_ethnicity"],
                ["Reported ethnicity"],
            )

            phenotype_csv = (
                b"Individual ID,Sample ID,Clinical Summary\n"
                b"CASE-P2,P2,Autoimmune manifestations\n"
            )
            upload = {
                "filename": "phenotypes.csv",
                "content_base64": base64.b64encode(phenotype_csv).decode(),
            }
            preview = post("/api/phenotypes/preview", upload)
            validation = post("/api/phenotypes/validate", {
                **upload,
                "mapping": preview["suggested_mapping"],
            })
            self.assertEqual(validation["matched_sample_ids"], ["P2"])
            imported_phenotypes = post("/api/phenotypes/import", {
                **upload,
                "mapping": preview["suggested_mapping"],
                "profile_name": "Test mapping",
            })
            self.assertEqual(imported_phenotypes["created"], 1)

            library_import = post("/api/sample-library/import", {
                "sources": [{"path": str(cohort_vcf)}],
                "analysis_scope": "exome",
                "index_scope": "compact",
                "include_in_cohort": False,
                "retention_routes": ["exome region"],
            })
            self.assertEqual(len(library_import["datasets"]), 2)
            p1_dataset = next(
                item for item in library_import["datasets"]
                if item["vcf_sample_name"] == "P1"
            )
            mapped = post(
                f"/api/sample-library/{p1_dataset['id']}/identity",
                {"mode": "existing", "individual_id": "CASE-P1"},
            )
            self.assertEqual(mapped["individual_id"], "CASE-P1")
            with urllib.request.urlopen(
                base + f"/api/sample-library/{p1_dataset['id']}/phenotype",
                timeout=5,
            ) as response:
                stable_phenotype = json.load(response)
            self.assertEqual(
                stable_phenotype["phenotype"]["individual_id"], "CASE-P1"
            )
            with urllib.request.urlopen(
                base + "/api/sample-library/profiles", timeout=5
            ) as response:
                profiles = json.load(response)["profiles"]
            self.assertEqual(profiles[0]["datasets"], 2)

            with urllib.request.urlopen(
                base + "/api/cohort/samples", timeout=5
            ) as response:
                samples = json.load(response)["samples"]
            p1 = next(sample for sample in samples if sample["name"] == "P1")
            removal = post(
                "/api/cohort/samples/remove", {"sample_ids": [p1["id"]]}
            )
            self.assertEqual(removal["removed_count"], 1)
            self.assertEqual(removal["stats"]["individuals"], 1)
            removed_query = post(
                "/api/cohort/query", {"mode": "variant", "query": "1:100:A:G"}
            )
            self.assertEqual(removed_query["total"], 0)

            repaired = post(
                f"/api/sample-library/{p1_dataset['id']}/reindex", {}
            )
            self.assertEqual(
                repaired["dataset"]["cohort_index_status"], "ready"
            )
            excluded_from_cohort = post(
                f"/api/sample-library/{p1_dataset['id']}/cohort/remove", {}
            )
            self.assertEqual(
                excluded_from_cohort["cohort_index_status"], "not_included"
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
