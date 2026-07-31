#!/usr/bin/env python3
import base64
import gzip
import io
import json
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path

from local_service.test_cohort_store import FakeHtsBackend, write_parallel_vcf, write_vcf
from local_service.workbench_service import AnnotationJobService, JobStore, create_server


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
            "region:\n  coding_only: true\n"
            "  bed: references/regions/coding.bed.gz\n"
        )
        (self.root / "references" / "regions").mkdir(parents=True)
        with gzip.open(
            self.root / "references" / "regions" / "coding.bed.gz", "wt"
        ) as handle:
            handle.write("1\t0\t1000000\n")
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
        self.state = Path(self.temp.name) / "state"
        self.service = AnnotationJobService(self.root, self.state)

    def tearDown(self):
        self.service.shutdown()
        self.temp.cleanup()

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

    def test_annotation_scope_controls_wgs_only_sources(self):
        sources = {
            source["id"]: source
            for source in self.service.capabilities()["annotation_profile"]["sources"]
        }
        self.assertEqual(sources["promoterai"]["available_in"], ["whole_genome"])
        self.assertEqual(sources["cadd_wgs"]["available_in"], ["whole_genome"])
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
                "min_promoterai_abs": 0.5,
                "min_cadd": None,
                "genes": [],
                "gene_window_bp": 0,
            },
        }, progress=progress_updates.append)

        self.assertEqual(result["reader_count"], 4)
        self.assertEqual(result["records_scanned"], 4)
        self.assertEqual(result["records_retained"], 4)
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
            "filters": {},
        })
        self.assertTrue(cached["cache_hit"])
        self.assertEqual(cached["records_retained"], 4)

        job = self.service.start_wgs_review({
            "path": str(source),
            "filters": {},
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

    def test_resource_download_jobs_are_constrained_and_report_progress(self):
        first = self.service.start_resource_download("spliceai")
        same = self.service.start_resource_download("spliceai")
        self.assertEqual(first["id"], same["id"])
        completed = self._wait_resource(first["id"])
        self.assertEqual(completed["status"], "succeeded")
        self.assertEqual(completed["progress"], 100.0)
        self.assertIn("complete", completed["log"])

        clinvar = self.service.start_resource_download("clinvar")
        self.assertEqual(self._wait_resource(clinvar["id"])["status"], "succeeded")
        with self.assertRaisesRegex(ValueError, "cannot be downloaded"):
            self.service.start_resource_download("dbnsfp")

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
                f"http://127.0.0.1:{port}/api/cohort/stats", timeout=2
            ) as response:
                cohort = json.load(response)
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/phenotypes/stats", timeout=2
            ) as response:
                phenotypes = json.load(response)
            self.assertTrue(health["ok"])
            self.assertEqual(jobs, {"jobs": []})
            self.assertEqual(resource_jobs, {"jobs": []})
            self.assertEqual(cohort["individuals"], 0)
            self.assertEqual(phenotypes["individuals"], 0)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_loopback_http_cohort_import_and_query(self):
        cohort_vcf = self.root / "cohort.vep.vcf.gz"
        write_vcf(cohort_vcf)
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
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
