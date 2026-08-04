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
        self.assertEqual(stored["annotation_bundle"]["workbench_service"], "0.10.0")
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
            self.assertTrue(health["ok"])
            self.assertEqual(jobs, {"jobs": []})
            self.assertEqual(resource_jobs, {"jobs": []})
            self.assertEqual(cohort["individuals"], 0)
            self.assertEqual(phenotypes["individuals"], 0)
            self.assertEqual(library, {"datasets": []})
            self.assertEqual(storage["datasets"], 0)
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
