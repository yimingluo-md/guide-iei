#!/usr/bin/env python3
import base64
import gzip
import hashlib
import io
import json
import os
import sqlite3
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from contextlib import closing
from dataclasses import replace
from http import HTTPStatus
from pathlib import Path
from unittest.mock import Mock, patch

import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from local_service.test_cohort_store import FakeHtsBackend, write_parallel_vcf, write_vcf
from local_service.test_genia import GEI_HEADER, gei_row, write_table, write_variant_vcf
from local_service.storage_locations import (
    StorageLocationRegistry, StorageRegistryError, _filesystem_type,
    storage_path_warning,
)
from local_service.workbench_service import (
    CONTAINER_FINGERPRINT_FILES,
    EXIT_ALREADY_RUNNING,
    FUNCVEP_ARCHIVE_NAME,
    AnnotationJobService,
    JobStore,
    SERVICE_VERSION,
    ServiceAlreadyRunningError,
    create_server,
)


class AnnotationJobServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "pipeline"
        (self.root / "scripts").mkdir(parents=True)
        (self.root / "config").mkdir()
        (self.root / "docker").mkdir()
        for name in CONTAINER_FINGERPRINT_FILES:
            (self.root / "docker" / name).write_text(f"test container input: {name}\n")
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

    def test_second_instance_on_the_same_state_is_refused_before_any_recovery(self):
        """Audit repro (H7): a second service process on one state
        directory marked the live instance's running job interrupted,
        killed its downloads and deleted its resource secrets, and only then
        failed to bind the port."""
        job = self.service.store.create({
            "id": "live-job", "status": "running", "profile": "exome",
            "created_at": "now", "updated_at": "now",
            "input_path": "/x.vcf", "output_path": "/y.vcf",
            "config_path": "", "log_path": "", "pid": None,
        })
        self.assertEqual(job["status"], "running")
        secret = self.service.resource_secrets_dir / "dbnsfp-url.txt"
        secret.write_text("private\n")
        with self.assertRaises(ServiceAlreadyRunningError):
            AnnotationJobService(self.root, self.state, start_worker=False)
        # Nothing of the live instance was touched.
        self.assertEqual(self.service.store.get("live-job")["status"], "running")
        self.assertTrue(secret.is_file())
        self.assertGreater(EXIT_ALREADY_RUNNING, 0)
        self.assertNotIn(EXIT_ALREADY_RUNNING, {0, 75, 130, 143})
        # After an orderly shutdown the state can be owned again.
        self.service.shutdown()
        successor = AnnotationJobService(self.root, self.state, start_worker=False)
        try:
            self.assertEqual(successor.store.get("live-job")["status"], "interrupted")
        finally:
            successor.shutdown()
        # tearDown shuts self.service down again; that must be harmless.

    def test_orphaned_annotation_jobs_are_reclaimed_at_startup(self):
        """Audit repro (H6): the pipeline script of a job that was running
        when the service died kept running in its own session; only the row
        was marked interrupted. A pid whose command line no longer belongs to
        this pipeline (PID reuse) must be left alone."""
        import subprocess
        state = Path(self.temp.name) / "orphan-state"
        seed = AnnotationJobService(self.root, state, start_worker=False)
        seed.shutdown()
        orphan = subprocess.Popen(
            # Production commands use the service's resolved root. macOS
            # temporary paths may otherwise differ by /var vs /private/var.
            [sys.executable, "-c", "import time; time.sleep(120)", str(seed.pipeline_root)],
            start_new_session=True,
        )
        bystander = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(120)", "unrelated-process"],
            start_new_session=True,
        )
        try:
            with sqlite3.connect(state / "workbench.sqlite3") as connection:
                for job_id, pid in (("orphan", orphan.pid), ("bystander", bystander.pid)):
                    connection.execute(
                        "INSERT INTO annotation_jobs(id, status, profile, created_at, "
                        "updated_at, input_path, output_path, config_path, log_path, pid) "
                        "VALUES (?, 'running', 'exome', 'now', 'now', '/x', '/y', '', '', ?)",
                        (job_id, pid),
                    )
            time.sleep(0.2)
            reopened = AnnotationJobService(self.root, state, start_worker=False)
            try:
                deadline = time.time() + 5
                while orphan.poll() is None and time.time() < deadline:
                    time.sleep(0.05)
                self.assertIsNotNone(orphan.poll(), "the orphaned pipeline process must be stopped")
                self.assertIsNone(bystander.poll(), "an unrelated process with a reused pid must survive")
                self.assertEqual(reopened.store.get("orphan")["status"], "interrupted")
                self.assertIsNone(reopened.store.get("orphan")["pid"])
                self.assertIn("stopped at the next start", reopened.store.get("orphan")["error"])
                self.assertEqual(reopened.store.get("bystander")["status"], "interrupted")
            finally:
                reopened.shutdown()
        finally:
            for process in (orphan, bystander):
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=5)

    def test_sigterm_stops_the_service_process_cleanly(self):
        """Audit repro (H6): under the launcher the service had no SIGTERM
        handler, so `kill` ended it without shutdown(); the state lock must
        be released and the exit status must read as a clean stop."""
        import signal
        import socket
        import subprocess
        state = Path(self.temp.name) / "signal-state"
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        environment = {**os.environ, "IEI_WORKBENCH_STATE_DIR": str(state)}
        process = subprocess.Popen(
            [
                sys.executable, "-m", "local_service.workbench_service",
                "--port", str(port), "--pipeline-root", str(self.root),
                "--state-dir", str(state),
                "--storage-registry", str(Path(self.temp.name) / "signal-registry.json"),
            ],
            cwd=str(Path(__file__).resolve().parents[1]),
            env=environment, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        try:
            deadline = time.time() + 30
            ready = False
            while time.time() < deadline and process.poll() is None:
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1) as response:
                        ready = response.status == 200
                        break
                except (urllib.error.URLError, ConnectionError, OSError):
                    time.sleep(0.1)
            self.assertTrue(ready, f"service did not start: {process.stdout.read() if process.poll() is not None else ''}")
            process.send_signal(signal.SIGTERM)
            output = process.communicate(timeout=30)[0]
            self.assertEqual(process.returncode, 0, output)
            self.assertIn("received signal", output)
            # The state directory is free for a successor.
            successor = AnnotationJobService(self.root, state, start_worker=False)
            successor.shutdown()
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)

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

    def test_sample_library_reuses_exact_import_and_versions_reannotation(self):
        review = self.root / "versioned-review.vcf"
        write_vcf(review)
        self.service.cohort.hts_backend = FakeHtsBackend()
        options = {
            "sources": [{"path": str(review), "original_name": review.name}],
            "analysis_scope": "exome",
            "index_scope": "compact",
            "include_in_cohort": True,
            "qc_settings": {"minDp": 10},
        }

        first = self.service.import_sample_library(options)["imports"][0]
        repeated = self.service.import_sample_library({
            **options,
            # Exact content identity wins over a changed screen setting: this
            # is reuse, not a second retained copy or rewritten provenance.
            "qc_settings": {"minDp": 20},
        })["imports"][0]
        self.assertEqual(repeated["import_outcome"], "exact_current")
        self.assertEqual(
            [item["id"] for item in repeated["datasets"]],
            [item["id"] for item in first["datasets"]],
        )
        self.assertEqual(len(self.service.sample_library.list()), 2)

        annotated = self.root / "versioned-review-updated.vcf"
        annotated.write_text(
            review.read_text().replace(
                "##fileformat=VCFv4.2\n",
                "##fileformat=VCFv4.2\n##GUIDE_IEI_annotation_release=updated\n",
                1,
            )
        )
        inspection = self.service.inspect_sample_library({
            "sources": [{"path": str(annotated)}],
            "analysis_scope": "exome",
        })["inspections"][0]
        self.assertEqual(inspection["status"], "reannotation")

        updated = self.service.import_sample_library({
            **options,
            "sources": [{"path": str(annotated), "original_name": annotated.name}],
        })["imports"][0]
        self.assertEqual(updated["import_outcome"], "updated_annotation")
        self.assertEqual(updated["callset_id"], first["callset_id"])
        self.assertEqual(updated["version_number"], 2)
        self.assertEqual(
            {item["sample_id"] for item in updated["datasets"]},
            {item["sample_id"] for item in first["datasets"]},
        )
        rows = self.service.sample_library.list()
        self.assertEqual(len(rows), 4)
        self.assertEqual(sum(item["is_current"] for item in rows), 2)
        self.assertEqual(self.service.cohort.stats()["sample_entries"], 2)

        exact_previous = self.service.import_sample_library(options)["imports"][0]
        self.assertEqual(exact_previous["import_outcome"], "exact_previous")
        self.assertEqual(len(self.service.sample_library.list()), 4)
        with self.assertRaisesRegex(ValueError, "previous dataset version"):
            self.service.sample_library.reindex(first["datasets"][0]["id"])

        restored = self.service.sample_library.activate_version(
            first["datasets"][0]["id"]
        )
        self.assertFalse(restored["already_current"])
        self.assertTrue(all(item["is_current"] for item in restored["datasets"]))
        self.assertEqual(self.service.cohort.stats()["sample_entries"], 2)

    def test_sample_library_requires_decision_for_changed_overlapping_callset(self):
        review = self.root / "initial-callset.vcf"
        write_vcf(review)
        self.service.cohort.hts_backend = None
        first = self.service.import_sample_library({
            "sources": [{"path": str(review)}],
            "analysis_scope": "exome",
            "include_in_cohort": False,
        })["imports"][0]

        changed = self.root / "changed-callset.vcf"
        changed.write_text(review.read_text().replace("1\t200\t", "1\t201\t", 1))
        inspection = self.service.inspect_sample_library({
            "sources": [{"path": str(changed)}],
            "analysis_scope": "exome",
        })["inspections"][0]
        self.assertEqual(inspection["status"], "possible_update")
        self.assertTrue(inspection["matches"][0]["same_sample_set"])
        with self.assertRaisesRegex(ValueError, "choose Replace current version"):
            self.service.import_sample_library({
                "sources": [{"path": str(changed)}],
                "analysis_scope": "exome",
                "include_in_cohort": False,
            })

        replaced = self.service.import_sample_library({
            "sources": [{
                "path": str(changed),
                "identity_action": "replace",
                "replace_callset_id": first["callset_id"],
            }],
            "analysis_scope": "exome",
            "include_in_cohort": False,
        })["imports"][0]
        self.assertEqual(replaced["import_outcome"], "updated_version")
        self.assertEqual(replaced["callset_id"], first["callset_id"])
        self.assertEqual(replaced["version_number"], 2)

        separate = self.root / "separate-callset.vcf"
        separate.write_text(changed.read_text().replace("1\t300\t", "1\t301\t", 1))
        kept_separate = self.service.import_sample_library({
            "sources": [{
                "path": str(separate),
                "identity_action": "separate",
            }],
            "analysis_scope": "exome",
            "include_in_cohort": False,
        })["imports"][0]
        self.assertEqual(kept_separate["import_outcome"], "separate_dataset")
        self.assertNotEqual(kept_separate["callset_id"], first["callset_id"])

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

    def test_worker_survives_an_exception_that_escapes_a_job(self):
        """Audit repro (M22): a store failure raised before _run_job's own
        try block (an unplugged data drive, a locked database) killed the
        worker thread; every later job stayed "queued" silently until the
        next restart. The boundary fails that job, records the failure for
        /api/health, keeps the worker alive, and never re-runs the job."""
        import sqlite3 as sqlite_module
        original_get = self.service.store.get
        first_id: dict[str, str] = {}
        calls = {"count": 0}

        def failing_get(job_id):
            # Fail the FIRST store read the worker makes for the first job,
            # i.e. the one outside _run_job's try/except.
            if (
                threading.current_thread().name == "annotation-worker"
                and job_id == first_id.get("id") and calls["count"] == 0
            ):
                calls["count"] += 1
                raise sqlite_module.OperationalError(
                    "simulated: database or disk is full"
                )
            return original_get(job_id)

        with patch.object(self.service.store, "get", side_effect=failing_get):
            first = self.service.submit(
                {"input_path": str(self.input), "output_path": str(self.output)}
            )
            first_id["id"] = first["id"]
            failed = self._wait(first["id"])
        self.assertEqual(failed["status"], "failed")
        self.assertIn("internal error", failed["error"])
        self.assertIn("OperationalError", failed["error"])
        health = self.service.worker_health()
        self.assertTrue(health["alive"], "worker thread must survive")
        self.assertEqual(health["failures"], 1)
        self.assertIn("OperationalError", health["last_error"])

        # The next job still runs to completion on the same worker.
        second_output = self.root / "results" / "second.vep.vcf.gz"
        second = self.service.submit(
            {"input_path": str(self.input), "output_path": str(second_output)}
        )
        completed = self._wait(second["id"])
        self.assertEqual(completed["status"], "succeeded", completed.get("error"))
        # The failed job was not silently retried.
        self.assertEqual(self.service.store.get(first["id"])["status"], "failed")

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

    def test_container_status_shows_background_startup_without_starting_from_get(self):
        self.service.docker_startup.status = {"state": "starting", "message": "Starting Docker Desktop…"}
        with patch("local_service.workbench_service.subprocess.run") as run:
            status = self.service._container_image_status({"container": {"runtime": "docker"}})
        self.assertEqual(status["state"], "runtime_starting")
        self.assertFalse(status["available"])
        run.assert_not_called()

    def test_queued_annotation_waits_for_docker_startup(self):
        self.service.docker_startup.status = {"state": "starting", "message": "Starting"}
        ran = threading.Event()
        with patch.object(self.service, "_run_job", side_effect=lambda _: ran.set()):
            self.service._queue.put("test-job")
            try:
                self.assertFalse(ran.wait(.1))
                self.service.docker_startup.status = {"state": "ready", "message": "Ready"}
                self.assertTrue(ran.wait(2))
            finally:
                self.service.docker_startup.status = {"state": "ready", "message": "Ready"}

    def test_container_status_keeps_startup_failure_guidance(self):
        self.service.docker_startup.status = {"state": "failed", "message": "Open Docker Desktop; startup log: /test/log"}
        with patch("local_service.workbench_service.shutil.which", return_value="docker"), patch(
                "local_service.workbench_service.subprocess.run",
                return_value=Mock(returncode=1, stdout="", stderr="Cannot connect to the Docker daemon")):
            status = self.service._container_image_status({"container": {"runtime": "docker"}})
        self.assertEqual(status["message"], self.service.docker_startup.status["message"])

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

    def test_container_status_rejects_an_image_from_an_older_checkout(self):
        completed = Mock(returncode=0, stdout="old-fingerprint\n", stderr="")
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
        self.assertEqual(status["state"], "image_stale")
        self.assertIn("older GUIDE-IEI version", status["message"])

    def test_container_status_accepts_the_matching_source_fingerprint(self):
        fingerprint = hashlib.sha256()
        fingerprint.update(b"GUIDE-IEI container inputs v1\n")
        for name in CONTAINER_FINGERPRINT_FILES:
            content = (self.root / "docker" / name).read_bytes()
            file_hash = hashlib.sha256(content).hexdigest()
            fingerprint.update(f"{file_hash}  {name}\n".encode("utf-8"))
        completed = Mock(
            returncode=0,
            stdout=f"{fingerprint.hexdigest()}\n",
            stderr="",
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
        self.assertTrue(status["available"])
        self.assertEqual(status["state"], "ready")

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

    def test_final_output_ignores_a_stale_aamatch_sibling(self):
        """The .aamatch.vcf.gz sibling counts only when it is at least as
        new as the base output: a previous run's file must not masquerade
        as the current result."""
        out_dir = Path(self.temp.name) / "final-output"
        out_dir.mkdir()
        base = out_dir / "case.vep.vcf.gz"
        aamatch = out_dir / "case.vep.aamatch.vcf.gz"

        aamatch.write_bytes(b"OLD")
        self.assertEqual(self.service._resolve_final_output(base), aamatch)

        base.write_bytes(b"CURRENT")
        os.utime(aamatch, (1_000_000, 1_000_000))
        self.assertEqual(self.service._resolve_final_output(base), base)

        os.utime(aamatch, None)
        self.assertEqual(self.service._resolve_final_output(base), aamatch)

        # The run's own sidecar is authoritative over any mtime reasoning —
        # equal timestamps on coarse filesystems guessed wrong both ways.
        (out_dir / "case.vep.vcf.gz.deliverable").write_text("case.vep.vcf.gz\n")
        self.assertEqual(self.service._resolve_final_output(base), base)
        (out_dir / "case.vep.vcf.gz.deliverable").write_text(
            "case.vep.aamatch.vcf.gz\n"
        )
        self.assertEqual(self.service._resolve_final_output(base), aamatch)

    def test_bulk_intake_queue_processes_items_and_records_failures(self):
        vcf_dir = Path(self.temp.name) / "bulk-src"
        vcf_dir.mkdir()
        for name in ("a.vcf.gz", "b.vcf.gz", "broken.vcf.gz"):
            (vcf_dir / name).write_bytes(b"placeholder")

        # The first item blocks until released, so the single-active-job
        # refusal is asserted while the queue is provably still running —
        # on a fast machine an ungated queue of three mocked items can
        # complete before the second start_bulk_intake call.
        release_first_item = threading.Event()
        self.addCleanup(release_first_item.set)

        def fake_prefilter(payload, progress=None):
            release_first_item.wait(timeout=10)
            if "broken" in payload["path"]:
                raise ValueError("synthetic prefilter failure")
            return {"id": "rv1", "records_scanned": 100, "records_retained": 10}

        prepared = Path(self.temp.name) / "prepared.vcf.gz"
        prepared.write_bytes(b"prepared")
        with patch.object(self.service, "prefilter_wgs_review", side_effect=fake_prefilter), \
             patch.object(self.service, "wgs_review_file", return_value=prepared), \
             patch.object(self.service.sample_library, "import_vcf",
                          return_value={"datasets": [{"id": "d1"}]}):
            snapshot = self.service.start_bulk_intake({
                "paths": [str(vcf_dir)], "analysis_scope": "exome",
            })
            self.assertEqual(snapshot["job"]["total"], 3)
            with self.assertRaisesRegex(ValueError, "still running|wait"):
                self.service.start_bulk_intake({"paths": [str(vcf_dir)]})
            release_first_item.set()
            for _ in range(400):
                snapshot = self.service.bulk_intake_snapshot(snapshot["job"]["id"])
                if snapshot["job"]["status"] in {"completed", "cancelled"}:
                    break
                time.sleep(0.02)
        job = snapshot["job"]
        self.assertEqual(job["status"], "completed")
        self.assertEqual(job["succeeded"], 2)
        self.assertEqual(job["failed"], 1)
        self.assertEqual(job["datasets"], 2)
        self.assertIn("synthetic prefilter failure", job["failures"][0]["error"])
        # A finished queue no longer blocks a new one.
        exome_default = job["options"]["filters"]
        self.assertEqual(exome_default["max_gnomad_popmax"], 0.01)
        self.assertIsNone(exome_default["min_spliceai"])

    def test_cross_site_posts_are_rejected(self):
        """A malicious webpage can fire no-preflight POSTs at loopback and
        DNS rebinding defeats origin checks without Host validation — both
        must be refused; local non-browser clients (no Origin) pass."""
        server = create_server(self.service, "127.0.0.1", 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        port = server.server_address[1]
        try:
            def post(headers):
                request = urllib.request.Request(
                    f"http://127.0.0.1:{port}/api/jobs",
                    data=b"{}", method="POST", headers=headers,
                )
                try:
                    with urllib.request.urlopen(request, timeout=5) as response:
                        return response.status
                except urllib.error.HTTPError as error:
                    return error.code

            self.assertEqual(post({"Origin": "https://evil.example"}), 403)
            self.assertEqual(post({"Host": "evil.example"}), 403)

            # DNS rebinding makes an attacker's page same-origin, so GETs on
            # PHI-bearing endpoints must enforce the same Host check.
            def get(headers, route="/api/sample-library"):
                request = urllib.request.Request(
                    f"http://127.0.0.1:{port}{route}", headers=headers,
                )
                try:
                    with urllib.request.urlopen(request, timeout=5) as response:
                        return response.status
                except urllib.error.HTTPError as error:
                    return error.code

            self.assertEqual(get({"Host": "evil.example"}), 403)
            self.assertEqual(get({}), 200)
            # A legitimate loopback origin (any port) is not rejected by the
            # guard — the request proceeds into normal validation.
            self.assertNotEqual(post({"Origin": "http://127.0.0.1:3999"}), 403)
            self.assertNotEqual(post({}), 403)
        finally:
            server.shutdown()

    def test_storage_guard_treats_bulk_intake_as_active_work(self):
        """A storage migration snapshotting mid-batch would activate a copy
        missing everything the queue imported after the snapshot."""
        with closing(self.service._bulk_intake_connect()) as connection, connection:
            now = "2026-08-23T00:00:00+00:00"
            connection.execute(
                "INSERT INTO bulk_jobs (id, created_at, updated_at, status, options)"
                " VALUES ('busy', ?, ?, 'running', '{}')", (now, now))
        with self.assertRaisesRegex(ValueError, "bulk import"):
            self.service._ensure_storage_idle()
        with closing(self.service._bulk_intake_connect()) as connection, connection:
            connection.execute(
                "UPDATE bulk_jobs SET status='completed' WHERE id='busy'")
        self.service._ensure_storage_idle()

    def test_bulk_intake_refuses_to_start_during_migration_reservation(self):
        source = Path(self.temp.name) / "reserved.vcf.gz"
        source.write_bytes(b"placeholder")
        self.service._migration_reserved = True
        try:
            with self.assertRaisesRegex(ValueError, "storage migration"):
                self.service.start_bulk_intake({
                    "paths": [str(source)], "analysis_scope": "exome",
                })
        finally:
            self.service._migration_reserved = False

    def test_bulk_intake_resume_resets_interrupted_items(self):
        source = Path(self.temp.name) / "resume.vcf.gz"
        source.write_bytes(b"placeholder")
        with closing(self.service._bulk_intake_connect()) as connection, connection:
            now = "2026-08-22T00:00:00+00:00"
            connection.execute(
                "INSERT INTO bulk_jobs (id, created_at, updated_at, status, options)"
                " VALUES ('jobx', ?, ?, 'running', '{}')", (now, now))
            connection.execute(
                "INSERT INTO bulk_items (job_id, position, path, status, updated_at)"
                " VALUES ('jobx', 0, ?, 'running', ?)", (str(source), now))
        with patch.object(self.service, "_start_bulk_intake_worker") as start:
            self.service._resume_bulk_intake()
            start.assert_called_once_with("jobx")
        snapshot = self.service.bulk_intake_snapshot("jobx")
        self.assertEqual(snapshot["job"]["queued"], 1)

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

    def test_native_resource_picker_accepts_the_official_funcvep_zip(self):
        selected = self.root / "FuncVEP_and_ClinVEP_scores_all_possible_missense_variants.zip"
        selected.write_bytes(b"official-archive-placeholder")
        completed = Mock(returncode=0, stdout=str(selected) + "\n", stderr="")
        with patch(
            "local_service.workbench_service.platform.system", return_value="Darwin"
        ), patch(
            "local_service.workbench_service.subprocess.run", return_value=completed
        ) as run:
            result = self.service.choose_local_resource_source({"resource_id": "funcvep"})
        self.assertFalse(result["cancelled"])
        self.assertEqual(result["resource_id"], "funcvep")
        self.assertEqual(result["selection_type"], "file")
        self.assertEqual(result["path"], str(selected.resolve()))
        self.assertEqual(result["name"], selected.name)
        self.assertIn("choose file", run.call_args.args[0][2])
        self.assertIn("official FuncVEP .zip archive", run.call_args.args[0][2])

    def test_native_resource_picker_accepts_a_genia_component_subset(self):
        relationship = write_table(
            self.root / "authorized-relationships.data",
            GEI_HEADER,
            [gei_row()],
            delimiter=",",
        )
        variants = write_variant_vcf(self.root / "authorized-variants.vcf.gz")
        completed = Mock(
            returncode=0,
            stdout=f"{relationship}\n{variants}\n",
            stderr="",
        )
        with patch(
            "local_service.workbench_service.platform.system", return_value="Darwin"
        ), patch(
            "local_service.workbench_service.subprocess.run", return_value=completed
        ) as run:
            result = self.service.choose_local_resource_source({"resource_id": "genia"})
        self.assertFalse(result["cancelled"])
        self.assertEqual(result["selection_type"], "files")
        self.assertEqual(result["paths"], [str(relationship.resolve()), str(variants.resolve())])
        self.assertEqual(
            {item["role"] for item in result["detected"]},
            {"gei_disease", "variant_vcf"},
        )
        self.assertIn("multiple selections allowed", run.call_args.args[0][2])

    def test_genia_annotation_source_requires_the_variant_component(self):
        with (self.root / "config" / "annotation.config.yaml").open("a") as handle:
            handle.write(
                "genia:\n"
                "  enabled: true\n"
                "  required: false\n"
                "  database: references/genia/genia.sqlite3\n"
            )
        relationship = write_table(
            self.root / "authorized-relationships.data",
            GEI_HEADER,
            [gei_row()],
            delimiter=",",
        )
        self.service.gene_knowledge.install_genia([relationship])
        source = next(
            item
            for item in self.service.capabilities()["annotation_profile"]["sources"]
            if item["id"] == "genia"
        )
        self.assertFalse(source["installed"])
        self.assertEqual(source["status"], "optional_missing")
        self.assertTrue(
            self.service.gene_knowledge.status()["genia"]["capabilities"]["gene_disease"]
        )

        variants = write_variant_vcf(self.root / "authorized-variants.vcf.gz")
        self.service.gene_knowledge.install_genia([variants])
        source = next(
            item
            for item in self.service.capabilities()["annotation_profile"]["sources"]
            if item["id"] == "genia"
        )
        self.assertTrue(source["installed"])
        self.assertTrue(source["enabled"])

    def test_genia_store_uses_configured_managed_database_and_reference_paths(self):
        config_path = self.root / "config" / "annotation.config.yaml"
        config = config_path.read_text(encoding="utf-8").replace(
            "  vep_cache_dir: references/vep_cache\n",
            "  vep_cache_dir: references/vep_cache\n"
            "  fasta:\n"
            "    path: references/custom/reference.fa.gz\n",
        )
        config += (
            "genia:\n"
            "  enabled: true\n"
            "  database: references/custom/genia.sqlite3\n"
        )
        config_path.write_text(config, encoding="utf-8")
        registry = self.service.storage_registry
        annotation_root = self.service.annotation_root
        self.service.shutdown()
        self.service = AnnotationJobService(
            self.root, self.state, storage_registry=registry,
        )

        self.assertEqual(
            self.service.genia.database,
            (annotation_root / "custom" / "genia.sqlite3").resolve(),
        )
        self.assertEqual(
            self.service.genia.reference_fasta,
            (annotation_root / "custom" / "reference.fa.gz").resolve(),
        )

    def test_dbnsfp_download_accepts_safelink_and_keeps_authorized_url_secret(self):
        target = "https://dist.genos.us/academic/authorized/dbNSFP6.0b_grch38.gz"
        safe_link = (
            "https://nam02.safelinks.protection.outlook.com/"
            "?url=https%3A%2F%2Fdist.genos.us%2Facademic%2Fauthorized%2F"
            "dbNSFP6.0b_grch38.gz&data=opaque"
        )
        with patch.object(
            self.service, "_ensure_annotation_download_space"
        ) as ensure_space, patch.object(
            self.service,
            "_start_resource_job",
            return_value={"id": "dbnsfp-job", "status": "queued"},
        ) as start:
            result = self.service.start_dbnsfp_download({"download_url": safe_link})
        self.assertEqual(result["id"], "dbnsfp-job")
        ensure_space.assert_called_once()
        self.assertEqual(ensure_space.call_args.args[0], "dbnsfp_download")
        destination = ensure_space.call_args.args[1][("plugins", "dbNSFP", "path")]
        self.assertEqual(destination.name, "dbNSFP6.0b_grch38.gz")
        command = start.call_args.args[1]
        self.assertEqual(command[0], "bash")
        self.assertTrue(command[1].endswith("scripts/download_dbnsfp.sh"))
        secret_path = Path(command[2])
        self.assertEqual(secret_path.read_text().strip(), target)
        self.assertEqual(secret_path.stat().st_mode & 0o777, 0o600)
        self.assertNotIn(target, json.dumps(command))
        self.assertNotIn(safe_link, json.dumps(command))
        generated_config = Path(command[3]).read_text(encoding="utf-8")
        self.assertIn("dbNSFP6.0b_grch38.gz", generated_config)
        self.assertIn("version: 6.0b", generated_config)
        self.assertEqual(start.call_args.kwargs["sensitive_paths"], (secret_path,))
        secret_path.unlink()

    def test_dbnsfp_download_rejects_untrusted_or_wrong_release_links(self):
        cases = (
            "http://dist.genos.us/academic/code/dbNSFP5.4a_grch38.gz",
            "https://example.org/academic/code/dbNSFP5.4a_grch38.gz",
            "https://dist.genos.us/academic/code/dbNSFP6.0b_grch37.gz",
            "https://dist.genos.us/academic/code/unrelated_grch38.gz",
        )
        for value in cases:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.service.start_dbnsfp_download({"download_url": value})

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

    def test_service_restart_refuses_when_the_interface_needs_a_full_relaunch(self):
        """Audit repro (M21): with webui/.build-required pending, the exit-75
        restart would bring up the new API behind the old interface bundle."""
        flag = self.root / "webui" / ".build-required"
        flag.parent.mkdir(parents=True, exist_ok=True)
        flag.write_text("1\n")
        try:
            with self.assertRaises(ValueError) as context:
                self.service.request_service_restart()
            self.assertIn("Close the GUIDE-IEI launcher window", str(context.exception))
            self.assertFalse(self.service.restart_requested)
            self.assertTrue(
                self.service.software_updater.status()["full_relaunch_required"]
            )
        finally:
            flag.unlink()
        # Without the flag the in-app restart is accepted as before.
        self.assertTrue(self.service.request_service_restart()["restarting"])

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
        dbnsfp_card = next(
            source
            for source in self.service.capabilities()["annotation_profile"]["sources"]
            if source["id"] == "dbnsfp"
        )
        self.assertEqual(dbnsfp_card["version"], "")

        generated = self.service._write_resource_config("dbnsfp")
        text = generated.read_text()
        expected = self.service.annotation_root / "dbnsfp" / "dbNSFP5.4a_grch38.gz"
        self.assertIn(str(expected), text)

        generated = self.service._write_resource_config("logofunc")
        text = generated.read_text()
        self.assertIn(str(self.service.annotation_root / "logofunc"), text)

    def test_dbnsfp_installed_manifest_selects_the_downloaded_release(self):
        root = self.service.annotation_root / "dbnsfp"
        root.mkdir(parents=True, exist_ok=True)
        data = root / "dbNSFP6.0b_grch38.gz"
        data.write_bytes(b"data")
        Path(str(data) + ".tbi").write_bytes(b"index")
        (root / "dbnsfp.installed.json").write_text(json.dumps({
            "filename": data.name,
            "version": "6.0b",
        }))
        config = self.service._load_config(
            self.service.pipeline_root / "config" / "annotation.config.yaml"
        )
        paths = self.service._managed_preparation_paths(config, "dbnsfp")
        self.assertEqual(paths["path"], str(data))
        self.assertEqual(paths["version"], "6.0b")

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
        profile = self.service.capabilities()["annotation_profile"]
        sources = {
            source["id"]: source
            for source in profile["sources"]
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
        self.assertEqual(sources["loftee_ptc_50bp"]["download_id"], "ccre")
        self.assertIn(
            "loftee_ptc_50bp",
            profile["recommended_profiles"]["exome"]["missing"],
        )
        self.assertEqual(sources["liftover"]["setup_mode"], "bundled")
        self.assertEqual(sources["dbnsfp"]["access"], "registration")
        self.assertEqual(sources["dbnsfp"]["prepare_id"], "dbnsfp")
        self.assertEqual(sources["promoterai"]["access"], "license")
        self.assertEqual(sources["logofunc"]["recommendation"], "optional")
        self.assertFalse(sources["logofunc"]["enabled"])
        self.assertEqual(sources["funcvep"]["access"], "license")
        self.assertEqual(sources["funcvep"]["recommendation"], "optional")
        self.assertEqual(sources["funcvep"]["prepare_id"], "funcvep")
        self.assertFalse(sources["funcvep"]["enabled"])
        self.assertEqual(sources["clingen_erepo"]["label"], "ClinGen")
        expected_references = {
            "alphagenome_avi": ("AlphaGenome published manuscript", "s41586-025-10014-0"),
            "spliceai": ("SpliceAI published manuscript", "10.1016/j.cell.2018.12.015"),
            "logofunc": ("LoGoFunc published manuscript", "s13073-023-01261-9"),
            "funcvep": ("FuncVEP published manuscript", "s41588-026-02727-3"),
            "ccre": ("ENCODE Registry V4 published manuscript", "s41586-025-09909-9"),
            "screen_context": ("ENCODE Registry V4 published manuscript", "s41586-025-09909-9"),
            "clinvar": ("ClinVar website", "ncbi.nlm.nih.gov/clinvar"),
            "clingen_erepo": ("ClinGen website", "clinicalgenome.org"),
        }
        for source_id, (label, url_fragment) in expected_references.items():
            self.assertEqual(sources[source_id]["reference_label"], label)
            self.assertIn(url_fragment, sources[source_id]["reference_url"])
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

    def test_read_loop_failure_stops_and_reaps_the_download_child(self):
        """Audit repro (M24): a failure while reading the downloader's output
        marked the job failed and dropped its tracking, but the child kept
        running — invisible to crash cleanup, and a second start of the same
        download would open a second writer on the same .part file."""
        # A downloader that prints one line, then keeps working for a while.
        self._write_script(
            "download_references.sh",
            "#!/usr/bin/env bash\nset -eu\n"
            'printf " 10.0%%  test\\n"\nsleep 30\nprintf "complete\\n"\n',
        )
        seen: dict[str, object] = {}

        def failing_update(stage, line):
            raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "simulated undecodable output")

        real_popen = subprocess.Popen

        def recording_popen(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            seen["process"] = process
            return process

        with patch.object(self.service, "_ensure_annotation_download_space"), \
                patch.object(
                    type(self.service), "_resource_progress_update",
                    side_effect=failing_update,
                ), patch("local_service.workbench_service.subprocess.Popen",
                         side_effect=recording_popen):
            job = self.service.start_resource_download("spliceai")
            failed = self._wait_resource(job["id"], timeout=20)
        self.assertIn("process", seen, "child was not started")
        self.assertEqual(failed["status"], "failed")
        self.assertIn("undecodable", failed["error"])
        process = seen["process"]
        # Reaped: the child is gone, not orphaned, before tracking was cleared.
        self.assertIsNotNone(process.poll(), "download child must be stopped and reaped")
        with self.service._resource_lock:
            self.assertNotIn(job["id"], self.service._resource_processes)
        pids_path = self.service._active_resource_pids_path()
        recorded = json.loads(pids_path.read_text()) if pids_path.exists() else {}
        self.assertNotIn(job["id"], recorded)
        # A new download of the same resource starts cleanly.
        self._write_script(
            "download_references.sh",
            "#!/usr/bin/env bash\nset -eu\nprintf \"complete\\n\"\n",
        )
        with patch.object(self.service, "_ensure_annotation_download_space"):
            again = self.service.start_resource_download("spliceai")
            self.assertNotEqual(again["id"], job["id"])
            self.assertEqual(self._wait_resource(again["id"])["status"], "succeeded")

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

    def test_omim_email_block_download_stays_out_of_jobs_and_logs(self):
        secret = "private-omim-account-key"
        base = f"https://data.omim.org/downloads/{secret}"
        link_block = "\n".join([
            "OMIM data account activation",
            "https://omim.org/static/omim/data/mim2gene.txt",
            f"{base}/mimTitles.txt",
            f"{base}/genemap2.txt",
            f"{base}/morbidmap.txt",
            "https://omim.org/contact",
        ])
        content = {
            "mim2gene.txt": (
                "# MIM Number\tMIM Entry Type\tEntrez Gene ID\tApproved Gene Symbol\tEnsembl Gene ID\n"
                "164011\tgene/phenotype\t4790\tNFKB1\tENSG00000109320\n"
            ).encode(),
            "mimTitles.txt": (
                "# Prefix\tMIM Number\tPreferred Title; symbol\n"
                "*\t164011\tNUCLEAR FACTOR; NFKB1\n"
            ).encode(),
            "genemap2.txt": (
                "# Chromosome\tMim Number\tGene Symbols\tApproved Gene Symbol\tPhenotypes\n"
                "4\t164011\tNFKB1, EBP1\tNFKB1\tImmunodeficiency, 616576 (3), Autosomal dominant\n"
            ).encode(),
            "morbidmap.txt": (
                "# Phenotype\tGene Symbols\tMIM Number\tCyto Location\n"
                "Immunodeficiency, 616576 (3), Autosomal dominant\tNFKB1, EBP1\t164011\t4q24\n"
            ).encode(),
        }

        class FakeResponse(io.BytesIO):
            def __init__(self, data, url):
                super().__init__(data)
                self.headers = {"Content-Length": str(len(data))}
                self.url = url

            def geturl(self):
                return self.url

        opener = Mock()

        def open_response(request, timeout):
            del timeout
            filename = request.full_url.rsplit("/", 1)[-1]
            return FakeResponse(content[filename], request.full_url)

        opener.open.side_effect = open_response
        with patch(
            "local_service.workbench_service.urllib.request.build_opener",
            return_value=opener,
        ), patch.object(self.service, "_wait_for_omim_download", return_value=None):
            started = self.service.start_omim_download({"link_block": link_block})
            self.assertEqual(started["files"], list(content))
            self.assertNotIn(secret, json.dumps(started))
            completed = self._wait_resource(started["id"])

        self.assertEqual(completed["status"], "succeeded")
        self.assertEqual(completed["result"]["counts"], {"genes": 1, "phenotypes": 1})
        self.assertNotIn(secret, json.dumps(completed))
        self.assertNotIn(secret, completed["log"])
        self.assertTrue((self.state / "gene-knowledge" / "omim.sqlite3").is_file())
        self.assertEqual(
            list((self.state / "gene-knowledge").glob(".omim-staging-*")), []
        )
        with self.service._resource_lock:
            self.assertNotIn("_runner", self.service._resource_jobs[started["id"]])

    def test_omim_download_paces_files_and_retries_http_429(self):
        filename = "mimTitles.txt"
        secret_url = f"https://data.omim.org/downloads/private-key/{filename}"
        payload = b"# Prefix\tMIM Number\tPreferred Title; symbol\n*\t164011\tNUCLEAR FACTOR; NFKB1\n"

        class FakeResponse(io.BytesIO):
            def __init__(self):
                super().__init__(payload)
                self.headers = {"Content-Length": str(len(payload))}

            def geturl(self):
                return secret_url

        rate_limit = urllib.error.HTTPError(
            secret_url,
            HTTPStatus.TOO_MANY_REQUESTS,
            "Too Many Requests",
            {"Retry-After": "17"},
            None,
        )
        opener = Mock()
        opener.open.side_effect = [rate_limit, FakeResponse()]
        waits = []
        retries = []
        destination = self.root / filename
        with patch(
            "local_service.workbench_service.urllib.request.build_opener",
            return_value=opener,
        ), patch.object(
            self.service,
            "_wait_for_omim_download",
            side_effect=lambda seconds: waits.append(seconds),
        ):
            self.service._download_omim_file(
                secret_url,
                filename,
                destination,
                lambda _fraction: None,
                lambda delay, attempt, total: retries.append((delay, attempt, total)),
            )

        self.assertEqual(destination.read_bytes(), payload)
        self.assertEqual(waits, [17.0])
        self.assertEqual(retries, [(17.0, 1, 3)])
        self.assertEqual(opener.open.call_count, 2)

    def test_omim_http_429_exhaustion_is_not_reported_as_expired_access(self):
        filename = "mimTitles.txt"
        secret_url = f"https://data.omim.org/downloads/private-key/{filename}"
        opener = Mock()
        opener.open.side_effect = [
            urllib.error.HTTPError(
                secret_url,
                HTTPStatus.TOO_MANY_REQUESTS,
                "Too Many Requests",
                {"Retry-After": "0"},
                None,
            )
            for _ in range(4)
        ]
        with patch(
            "local_service.workbench_service.urllib.request.build_opener",
            return_value=opener,
        ), patch.object(self.service, "_wait_for_omim_download", return_value=None):
            with self.assertRaisesRegex(ValueError, "rate-limited.*automatic retries") as raised:
                self.service._download_omim_file(
                    secret_url,
                    filename,
                    self.root / filename,
                    lambda _fraction: None,
                )
        self.assertNotIn("expired", str(raised.exception))

    def test_logofunc_preparation_accepts_an_existing_source_file_or_folder(self):
        source = self.root / "LoGoFuncVotingEnsemble_metadata_preds_final.csv.gz"
        source.write_bytes(b"source-placeholder")
        first = self.service.start_logofunc_preparation({"source_path": str(source)})
        completed = self._wait_resource(first["id"])
        self.assertEqual(completed["resource_id"], "logofunc")
        self.assertEqual(completed["operation"], "preparation")
        self.assertEqual(completed["status"], "succeeded")
        self.assertEqual(completed["progress"], 100.0)

    def test_funcvep_preparation_requires_explicit_license_acknowledgement(self):
        source = self.root / "FuncVEP_and_ClinVEP_scores_all_possible_missense_variants.zip"
        source.write_bytes(b"official-archive-placeholder")
        with self.assertRaisesRegex(ValueError, "reviewed the FuncVEP license"):
            self.service.start_funcvep_preparation({
                "source_path": str(source),
                "license_accepted": False,
            })

    def test_funcvep_preparation_passes_source_config_and_acknowledgement(self):
        source = self.root / "FuncVEP_and_ClinVEP_scores_all_possible_missense_variants.zip"
        source.write_bytes(b"official-archive-placeholder")
        with patch.object(
            self.service, "_ensure_annotation_download_space"
        ) as ensure_space, patch.object(
            self.service,
            "_start_resource_job",
            return_value={"id": "funcvep-job", "status": "queued"},
        ) as start:
            result = self.service.start_funcvep_preparation({
                "source_path": str(source),
                "license_accepted": True,
            })
        self.assertEqual(result["id"], "funcvep-job")
        ensure_space.assert_called_once_with("funcvep_preparation")
        start.assert_called_once()
        self.assertEqual(start.call_args.args[0], "funcvep")
        self.assertEqual(start.call_args.args[2], "preparation")
        command = start.call_args.args[1]
        self.assertEqual(command[0], "bash")
        self.assertTrue(command[1].endswith("scripts/prepare_funcvep.sh"))
        self.assertEqual(command[2], str(source.resolve()))
        self.assertTrue(Path(command[3]).is_file())
        self.assertIn("funcvep_scores.grch38.tsv.gz", Path(command[3]).read_text())
        self.assertEqual(command[4:], ["--acknowledge-license"])
        self.assertNotIn("--remove-source-after-success", command)
        self.assertTrue(source.is_file())

    def test_avi_download_uses_managed_paths_and_rejects_retired_zip_import(self):
        source = self.root / "avi.zip"
        source.write_bytes(b"synthetic fixture")
        with patch.object(self.service, "_ensure_annotation_download_space"), \
             patch.object(self.service, "_start_resource_job", return_value={"id":"avi-job"}) as start:
            job = self.service.start_resource_download("alphagenome_avi")
        self.assertEqual(job["id"], "avi-job")
        resource, command, operation = start.call_args.args
        self.assertEqual((resource, operation), ("alphagenome_avi", "download"))
        self.assertTrue(command[1].endswith("scripts/download_avi.sh"))
        config = self.service._load_config(Path(command[2]))
        self.assertEqual(config["custom_tracks"]["AlphaGenomeAVI"]["dest_dir"],
                         str(self.service.annotation_root / "alphagenome-avi"))
        self.assertTrue(source.is_file())
        with self.assertRaisesRegex(ValueError,"Download / resume"):
            self.service.start_avi_preparation({"source_path":str(source)})

    def test_avi_is_wgs_recommended_but_not_required(self):
        with patch.object(self.service, "_container_image_status", return_value={"available":False,"state":"missing","message":"test"}):
            profile = self.service._annotation_profile()
        source = next(s for s in profile["sources"] if s["id"] == "alphagenome_avi")
        self.assertEqual(source["available_in"],["whole_genome"])
        self.assertEqual(source["recommendation"],"recommended_wgs")
        self.assertEqual(source["setup_mode"], "download")
        self.assertFalse(source.get("prepare_id"))
        self.assertFalse(source["required"])
        self.assertFalse(source["installed"])
        self.assertIn("alphagenome_avi",profile["recommended_profiles"]["whole_genome"]["missing"])
        self.assertNotIn("alphagenome_avi",profile["recommended_profiles"]["exome"]["missing"])
        self.assertNotIn("cadd_wgs",profile["recommended_profiles"]["whole_genome"]["missing"])
        source = next(item for item in profile["sources"] if item["id"] == "alphagenome_avi")
        self.assertEqual(source["description"], "Genome-wide predicted functional impact of single-nucleotide variants, shown as an AVI Phred score")

    def test_funcvep_acknowledgement_can_start_automatic_zenodo_download(self):
        with patch.object(
            self.service, "_ensure_annotation_download_space"
        ) as ensure_space, patch.object(
            self.service,
            "_start_resource_job",
            return_value={"id": "funcvep-download-job", "status": "queued"},
        ) as start:
            result = self.service.start_funcvep_preparation({
                "source_path": "",
                "license_accepted": True,
            })
        self.assertEqual(result["id"], "funcvep-download-job")
        archive = self.service.annotation_root / "funcvep" / FUNCVEP_ARCHIVE_NAME
        ensure_space.assert_called_once_with(
            "funcvep_download_preparation",
            {("plugins", "FuncVEP", "file"): archive},
        )
        command = start.call_args.args[1]
        self.assertEqual(command[0], "bash")
        self.assertTrue(command[1].endswith("scripts/download_funcvep.sh"))
        self.assertTrue(Path(command[2]).is_file())
        self.assertEqual(command[3:], ["--acknowledge-license"])

    def test_funcvep_is_installed_only_with_scores_index_and_manifest(self):
        from pipeline.funcvep_dataset import OUTPUT_HEADER, build_manifest

        def source_status():
            return next(
                source
                for source in self.service.capabilities()["annotation_profile"]["sources"]
                if source["id"] == "funcvep"
            )

        managed = self.service.annotation_root / "funcvep"
        managed.mkdir(parents=True, exist_ok=True)
        scores = managed / "funcvep_scores.grch38.tsv.gz"
        index = Path(str(scores) + ".tbi")
        manifest = managed / "funcvep.manifest.json"

        self.assertFalse(source_status()["installed"])
        scores.write_bytes(b"scores")
        self.assertFalse(source_status()["installed"])
        index.write_bytes(b"index")
        self.assertFalse(source_status()["installed"])
        manifest.write_text('{"resource": "FuncVEP"}\n')
        self.assertFalse(source_status()["installed"])
        manifest.write_bytes(b"\xff")
        self.assertFalse(source_status()["installed"])

        # Exercise the actual preparation manifest builder rather than a
        # service-specific approximation of its schema.
        with gzip.open(scores, "wt", encoding="ascii") as handle:
            handle.write(OUTPUT_HEADER)
            handle.write("1\t1\tA\tG\tENSG00000000001\t0.1\t0.2\t0.3\n")
        index.write_bytes(b"test-tabix-index")
        archive = managed / "official.zip"
        archive.write_bytes(b"official-source")
        stats = managed / "stats.json"
        stats.write_text(json.dumps({
            "archive": {
                "name": archive.name,
                "size": archive.stat().st_size,
                "md5": hashlib.md5(archive.read_bytes()).hexdigest(),
                "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
                "preserved": True,
            },
            "source_member": {"name": "scores.tsv", "size": 1, "crc32": "00000000"},
            "row_count": 1,
            "contigs": {"1": 1},
            "excluded_columns": ["ClinVEP_CTI", "ClinVEP_CTE", "ClinVEP_SP"],
        }))
        with patch(
            "pipeline.funcvep_dataset.is_bgzf", return_value=True
        ), patch(
            "pipeline.funcvep_dataset.tabix_metadata",
            return_value={
                "format": "tabix", "sequence_column": 1,
                "begin_column": 2, "end_column": 2, "contigs": ["1"],
            },
        ):
            payload = build_manifest(
                archive,
                scores,
                index,
                stats,
                installed_score_name=scores.name,
                license_acknowledged=True,
            )
        manifest.write_text(json.dumps(payload) + "\n")
        installed = source_status()
        self.assertTrue(installed["installed"])
        self.assertEqual(
            set(installed["configured_paths"]),
            {str(scores), str(manifest)},
        )

        # Extending the registry and manifest together must not require a
        # service-specific literal output-set update.
        from pipeline.predictor_registry import load_registry
        registry = load_registry()
        funcvep_predictor = registry.predictor("funcvep")
        extra_metric = replace(
            funcvep_predictor.metrics[0],
            id="secondary_cti",
            field="FuncVEP_secondary_CTI",
        )
        extended_predictor = replace(
            funcvep_predictor,
            metrics=(*funcvep_predictor.metrics, extra_metric),
        )
        extended_registry = replace(
            registry,
            predictors=tuple(
                extended_predictor if item.id == funcvep_predictor.id else item
                for item in registry.predictors
            ),
        )
        extended_payload = json.loads(json.dumps(payload))
        extended_output = dict(extended_payload["outputs"][0])
        extended_output.update({
            "id": extra_metric.field,
            "column": extra_metric.field,
            "description": "Registry-extension regression score",
        })
        extended_payload["outputs"].append(extended_output)
        extended_payload["table"]["columns"].append(extra_metric.field)
        manifest.write_text(json.dumps(extended_payload) + "\n")
        with patch(
            "local_service.workbench_service.load_predictor_registry",
            return_value=extended_registry,
        ):
            self.assertTrue(source_status()["installed"])
        manifest.write_text(json.dumps(payload) + "\n")

        from pipeline.predictor_registry import RegistryError
        with patch(
            "local_service.workbench_service.load_predictor_registry",
            side_effect=RegistryError("corrupt registry"),
        ):
            self.assertFalse(source_status()["installed"])

        # Copying a bundle may preserve bytes but not timestamps. A mismatch
        # must fall back to the recorded checksum instead of marking it broken.
        score_status = scores.stat()
        os.utime(
            scores,
            ns=(score_status.st_atime_ns, score_status.st_mtime_ns + 1_000_000),
        )
        self.assertTrue(source_status()["installed"])

        # mtime_ns is optional in the shared schema and must remain optional
        # for service installation detection too.
        without_mtime = json.loads(json.dumps(payload))
        del without_mtime["files"]["data"]["mtime_ns"]
        del without_mtime["files"]["index"]["mtime_ns"]
        manifest.write_text(json.dumps(without_mtime) + "\n")
        self.assertTrue(source_status()["installed"])

        # Installation readiness uses the same typed registry contract as
        # command startup, not merely the presence of the expected field ID.
        wrong_type = json.loads(json.dumps(payload))
        wrong_type["outputs"][0]["type"] = "string"
        manifest.write_text(json.dumps(wrong_type) + "\n")
        self.assertFalse(source_status()["installed"])

        # A same-size content change after the timestamp diverges is detected
        # by the checksum fallback.
        manifest.write_text(json.dumps(payload) + "\n")
        damaged = bytearray(scores.read_bytes())
        damaged[-1] ^= 1
        scores.write_bytes(damaged)
        self.assertFalse(source_status()["installed"])

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
                f"http://127.0.0.1:{port}/api/software-update/status", timeout=2
            ) as response:
                update_status = json.load(response)
            self.assertIn("current_version", update_status)
            self.assertFalse(update_status["rollback_available"])
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
            # The projected VCF is streamed from disk (audit M32), not embedded.
            self.assertNotIn("vcf", sample_review["files"][0])
            with urllib.request.urlopen(
                base + sample_review["files"][0]["vcf_url"], timeout=5
            ) as response:
                self.assertEqual(response.headers["Content-Type"], "text/plain; charset=utf-8")
                streamed = response.read().decode("utf-8")
            self.assertEqual(len(streamed.encode("utf-8")), sample_review["files"][0]["vcf_bytes"])
            self.assertIn("1\t400\t.\tT\tC", streamed)
            self.assertNotIn("\tP2\n", streamed)
            with self.assertRaises(urllib.error.HTTPError) as missing:
                urllib.request.urlopen(base + "/api/cohort/sample-review/nosuchtoken/0", timeout=5)
            self.assertEqual(missing.exception.code, 404)

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
