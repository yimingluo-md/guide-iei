#!/usr/bin/env python3
"""Persistent, loopback-only annotation job service.

The service intentionally uses only the Python standard library. It queues
calls to the existing preflight and annotation scripts, stores job metadata in
SQLite, and never sends VCF data over the network.
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import platform
import queue
import re
import shutil
import signal
import socketserver
import sqlite3
import subprocess
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from local_service.cohort_store import CohortStore
from local_service.phenotype_store import PhenotypeStore


SERVICE_VERSION = "0.7.0"
TERMINAL_STATUSES = {"succeeded", "failed", "cancelled", "interrupted"}
ALLOWED_PROFILES = {"local", "wsl-local"}
ANNOTATION_SOURCE_PATHS = {
    "dbnsfp": ("plugins", "dbNSFP"),
    "loftee": ("plugins", "LoF"),
    "spliceai": ("plugins", "SpliceAI"),
    "repeatmasker": ("custom_tracks", "RepeatMasker"),
    "segdup": ("custom_tracks", "SegDup"),
    "promoterai": ("custom_tracks", "promoterAI"),
    "logofunc": ("custom_tracks", "LoGoFunc"),
    "clinvar": ("custom_tracks", "ClinVar"),
    "loftee_ptc_50bp": ("post_processing", "loftee_ptc_50bp"),
    "clinvar_aa_match": ("post_processing", "clinvar_aa_match"),
}
REQUIRED_DIAGNOSTIC_SOURCES = {"dbnsfp", "loftee", "spliceai", "loftee_ptc_50bp"}
DBNSFP_OPTIONAL_PREDICTORS = [
    {"id": "sift4g", "label": "SIFT4G", "category": "Established", "columns": ["SIFT4G_score", "SIFT4G_pred"], "recommended": True},
    {"id": "polyphen_hvar", "label": "PolyPhen HVAR", "category": "Established", "columns": ["Polyphen2_HVAR_score", "Polyphen2_HVAR_pred"], "recommended": True},
    {"id": "mutation_taster", "label": "MutationTaster", "category": "Established", "columns": ["MutationTaster_score", "MutationTaster_pred"], "recommended": False},
    {"id": "mutation_assessor", "label": "MutationAssessor", "category": "Established", "columns": ["MutationAssessor_score", "MutationAssessor_pred"], "recommended": True},
    {"id": "provean", "label": "PROVEAN", "category": "Established", "columns": ["PROVEAN_score", "PROVEAN_pred"], "recommended": True},
    {"id": "vest4", "label": "VEST4", "category": "Ensemble", "columns": ["VEST4_score"], "recommended": True},
    {"id": "meta_svm", "label": "MetaSVM", "category": "Ensemble", "columns": ["MetaSVM_score", "MetaSVM_pred"], "recommended": True},
    {"id": "meta_lr", "label": "MetaLR", "category": "Ensemble", "columns": ["MetaLR_score", "MetaLR_pred"], "recommended": True},
    {"id": "m_cap", "label": "M-CAP", "category": "Ensemble", "columns": ["M-CAP_score", "M-CAP_pred"], "recommended": True},
    {"id": "mutpred2", "label": "MutPred2", "category": "Ensemble", "columns": ["MutPred2_score", "MutPred2_pred"], "recommended": True},
    {"id": "mvp", "label": "MVP", "category": "Ensemble", "columns": ["MVP_score"], "recommended": False},
    {"id": "gmvp", "label": "gMVP", "category": "Ensemble", "columns": ["gMVP_score"], "recommended": False},
    {"id": "mpc", "label": "MPC", "category": "Regional constraint", "columns": ["MPC_score"], "recommended": True},
    {"id": "deogen2", "label": "DEOGEN2", "category": "Ensemble", "columns": ["DEOGEN2_score", "DEOGEN2_pred"], "recommended": False},
    {"id": "bayesdel_addaf", "label": "BayesDel addAF", "category": "Ensemble", "columns": ["BayesDel_addAF_score", "BayesDel_addAF_pred"], "recommended": True},
    {"id": "bayesdel_noaf", "label": "BayesDel noAF", "category": "Ensemble", "columns": ["BayesDel_noAF_score", "BayesDel_noAF_pred"], "recommended": False},
    {"id": "clinpred", "label": "ClinPred", "category": "Ensemble", "columns": ["ClinPred_score", "ClinPred_pred"], "recommended": True},
    {"id": "list_s2", "label": "LIST-S2", "category": "Ensemble", "columns": ["LIST-S2_score", "LIST-S2_pred"], "recommended": False},
    {"id": "varity_r", "label": "VARITY R", "category": "Protein model", "columns": ["VARITY_R_score"], "recommended": True},
    {"id": "varity_er", "label": "VARITY ER", "category": "Protein model", "columns": ["VARITY_ER_score"], "recommended": False},
    {"id": "esm1b", "label": "ESM1b", "category": "Protein language model", "columns": ["ESM1b_score", "ESM1b_pred"], "recommended": True},
    {"id": "phactboost", "label": "PHACTboost", "category": "Protein model", "columns": ["PHACTboost_score"], "recommended": False},
    {"id": "mutformer", "label": "MutFormer", "category": "Protein language model", "columns": ["MutFormer_score"], "recommended": False},
    {"id": "mutscore", "label": "MutScore", "category": "Protein model", "columns": ["MutScore_score"], "recommended": False},
    {"id": "popeve", "label": "popEVE", "category": "Protein model", "columns": ["popEVE_score", "popEVE_pred"], "recommended": False},
]
ANNOTATION_SOURCE_SETUP = {
    "dbnsfp": {
        "setup_mode": "manual",
        "reference_url": "https://www.dbnsfp.org/download",
        "reference_label": "dbNSFP academic download registration",
        "size_hint": "approximately 50 GB after preparation",
        "instructions": [
            "Register with an institutional email at the dbNSFP academic download page.",
            "Use the emailed access code to request the current academic release links.",
            "Download and extract dbNSFP 5.3.1a for GRCh38.",
            "In a terminal, run: bash scripts/prepare_dbnsfp.sh /path/to/dbNSFP5.3.1a_unzipped_dir",
            "Return here and confirm that both the configured .gz file and its .tbi index are detected.",
        ],
    },
    "loftee": {
        "setup_mode": "bundled",
        "reference_url": "https://github.com/konradjk/loftee",
        "reference_label": "LOFTEE project",
        "size_hint": "",
        "instructions": [
            "LOFTEE code is included in the pinned VEP container.",
            "The validated GRCh38 ancestor, conservation database, and GERP resources are installed with the software reference bundle.",
            "No separate user configuration is required.",
        ],
    },
    "spliceai": {
        "setup_mode": "download",
        "download_id": "spliceai",
        "reference_url": (
            "https://ftp.ensembl.org/pub/data_files/homo_sapiens/GRCh38/"
            "variation_plugins/spliceai_scores.masked.snv.ensembl_mane_v1.4.grch38.vcf.gz"
        ),
        "reference_label": "Ensembl SpliceAI MANE v1.4 VCF",
        "size_hint": "approximately 27 GB plus index",
        "instructions": [
            "Select Download in this screen.",
            "Keep the workbench and computer running while the resumable download completes.",
            "The VCF and tabix index are saved to the configured local reference directory.",
            "The software verifies that both files are present before marking SpliceAI ready.",
        ],
    },
    "repeatmasker": {
        "setup_mode": "bundled",
        "reference_url": "https://www.repeatmasker.org/",
        "reference_label": "RepeatMasker project",
        "size_hint": "",
        "instructions": [
            "The cleaned UCSC hg38 RepeatMasker track is included in the software reference bundle.",
            "Contig names and coordinates are already normalized for the Ensembl GRCh38 VEP cache.",
        ],
    },
    "segdup": {
        "setup_mode": "bundled",
        "reference_url": (
            "https://genome.ucsc.edu/cgi-bin/hgTables?db=hg38&"
            "hgta_group=varRep&hgta_track=genomicSuperDups"
        ),
        "reference_label": "UCSC hg38 genomicSuperDups",
        "size_hint": "",
        "instructions": [
            "The cleaned UCSC hg38 segmental-duplication track is included in the software reference bundle.",
            "Contig names and coordinates are already normalized for the Ensembl GRCh38 VEP cache.",
        ],
    },
    "promoterai": {
        "setup_mode": "deferred",
        "reference_url": "",
        "reference_label": "",
        "size_hint": "",
        "instructions": [
            "promoterAI setup is intentionally deferred in this release.",
        ],
    },
    "logofunc": {
        "setup_mode": "deferred",
        "reference_url": "",
        "reference_label": "",
        "size_hint": "",
        "instructions": [
            "LoGoFunc setup is intentionally deferred in this release.",
        ],
    },
    "clinvar": {
        "setup_mode": "download",
        "download_id": "clinvar",
        "reference_url": "https://ftp.ncbi.nlm.nih.gov/pub/clinvar/vcf_GRCh38/",
        "reference_label": "NCBI ClinVar GRCh38 VCF directory",
        "size_hint": "updated weekly",
        "instructions": [
            "Select Download latest in this screen.",
            "The current NCBI GRCh38 VCF and tabix index are downloaded and release-stamped.",
            "The stable local latest copy is used by VEP and ClinVar residue matching.",
            "Automatic refresh before an annotation run can remain enabled.",
        ],
    },
    "loftee_ptc_50bp": {
        "setup_mode": "bundled",
        "reference_url": "",
        "reference_label": "",
        "size_hint": "",
        "instructions": [
            "The frameshift PTC 50-bp recomputation is implemented by this software.",
            "It uses the bundled release-matched GTF and GRCh38 FASTA; no separate dataset is required.",
        ],
    },
    "clinvar_aa_match": {
        "setup_mode": "bundled",
        "reference_url": "",
        "reference_label": "",
        "size_hint": "",
        "instructions": [
            "ClinVar pathogenic residue matching is implemented by this software.",
            "Its local residue table is rebuilt automatically from the downloaded ClinVar release.",
        ],
    },
}
RESOURCE_DOWNLOAD_COMMANDS = {
    "spliceai": ("scripts/download_references.sh", "--only", "spliceai"),
    "clinvar": ("scripts/fetch_clinvar.sh",),
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def is_wsl() -> bool:
    return bool(os.environ.get("WSL_DISTRO_NAME") or os.environ.get("WSL_INTEROP"))


def default_state_dir() -> Path:
    override = os.environ.get("IEI_WORKBENCH_STATE_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".iei-variant-review"


class JobStore:
    """Small SQLite repository; each operation owns its connection."""

    def __init__(self, database_path: Path):
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    @contextmanager
    def _session(self):
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._session() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS annotation_jobs (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    status TEXT NOT NULL,
                    profile TEXT NOT NULL,
                    input_assembly TEXT NOT NULL DEFAULT 'GRCh38',
                    input_path TEXT NOT NULL,
                    output_path TEXT NOT NULL,
                    final_output_path TEXT,
                    config_path TEXT NOT NULL,
                    coding_only INTEGER NOT NULL DEFAULT 1,
                    include_filtered INTEGER NOT NULL DEFAULT 0,
                    use_clinvar INTEGER NOT NULL DEFAULT 1,
                    command_json TEXT NOT NULL DEFAULT '[]',
                    log_path TEXT NOT NULL,
                    pid INTEGER,
                    exit_code INTEGER,
                    error TEXT
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS annotation_jobs_created_idx "
                "ON annotation_jobs(created_at DESC)"
            )
            job_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(annotation_jobs)"
                ).fetchall()
            }
            if "input_assembly" not in job_columns:
                connection.execute(
                    "ALTER TABLE annotation_jobs "
                    "ADD COLUMN input_assembly TEXT NOT NULL DEFAULT 'GRCh38'"
                )
            now = utc_now()
            connection.execute(
                """
                UPDATE annotation_jobs
                SET status = 'interrupted', updated_at = ?, finished_at = ?,
                    error = COALESCE(error, 'The local service stopped while this job was running.')
                WHERE status = 'running'
                """,
                (now, now),
            )

    def create(self, values: dict) -> dict:
        columns = ", ".join(values)
        placeholders = ", ".join("?" for _ in values)
        with self._session() as connection:
            connection.execute(
                f"INSERT INTO annotation_jobs ({columns}) VALUES ({placeholders})",
                tuple(values.values()),
            )
        return self.get(values["id"])

    def update(self, job_id: str, **values) -> dict | None:
        if not values:
            return self.get(job_id)
        values["updated_at"] = utc_now()
        assignments = ", ".join(f"{column} = ?" for column in values)
        with self._session() as connection:
            connection.execute(
                f"UPDATE annotation_jobs SET {assignments} WHERE id = ?",
                (*values.values(), job_id),
            )
        return self.get(job_id)

    def get(self, job_id: str) -> dict | None:
        with self._session() as connection:
            row = connection.execute(
                "SELECT * FROM annotation_jobs WHERE id = ?", (job_id,)
            ).fetchone()
        return self._serialize(row) if row else None

    def list(self, limit: int = 100) -> list[dict]:
        with self._session() as connection:
            rows = connection.execute(
                "SELECT * FROM annotation_jobs ORDER BY created_at DESC LIMIT ?",
                (max(1, min(limit, 500)),),
            ).fetchall()
        return [self._serialize(row) for row in rows]

    def queued_ids(self) -> list[str]:
        with self._session() as connection:
            rows = connection.execute(
                "SELECT id FROM annotation_jobs WHERE status = 'queued' ORDER BY created_at"
            ).fetchall()
        return [row["id"] for row in rows]

    @staticmethod
    def _serialize(row: sqlite3.Row) -> dict:
        result = dict(row)
        for key in ("coding_only", "include_filtered", "use_clinvar"):
            result[key] = bool(result[key])
        result["command"] = json.loads(result.pop("command_json") or "[]")
        return result


class AnnotationJobService:
    def __init__(self, pipeline_root: Path, state_dir: Path, start_worker: bool = True):
        self.pipeline_root = pipeline_root.resolve()
        self.state_dir = state_dir.resolve()
        self.logs_dir = self.state_dir / "logs"
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.resource_logs_dir = self.state_dir / "resource-logs"
        self.resource_logs_dir.mkdir(parents=True, exist_ok=True)
        self.store = JobStore(self.state_dir / "workbench.sqlite3")
        self.cohort = CohortStore(self.state_dir / "cohort.sqlite3")
        self.phenotypes = PhenotypeStore(self.state_dir / "cohort.sqlite3")
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._processes: dict[str, subprocess.Popen] = {}
        self._process_lock = threading.Lock()
        self._resource_jobs: dict[str, dict] = {}
        self._resource_processes: dict[str, subprocess.Popen] = {}
        self._resource_threads: dict[str, threading.Thread] = {}
        self._resource_lock = threading.Lock()
        self._stop = threading.Event()
        self._worker: threading.Thread | None = None
        for job_id in self.store.queued_ids():
            self._queue.put(job_id)
        if start_worker:
            self._worker = threading.Thread(
                target=self._worker_loop, name="annotation-worker", daemon=True
            )
            self._worker.start()

    def capabilities(self) -> dict:
        in_wsl = is_wsl()
        hardware = self._hardware_profile()
        annotation_profile = self._annotation_profile()
        annotation_profile["defaults"]["fork"] = hardware[
            "recommended_vep_workers"
        ]
        runtimes = [
            runtime for runtime in ("docker", "podman", "apptainer", "singularity")
            if shutil.which(runtime)
        ]
        profile = {
            "id": "wsl-local" if in_wsl else "local",
            "label": "WSL workstation" if in_wsl else "Local workstation",
            "available": bool(shutil.which("bash")),
        }
        return {
            "service": "IEI Variant Review local service",
            "version": SERVICE_VERSION,
            "platform": platform.system().lower(),
            "wsl": in_wsl,
            "pipeline_root": str(self.pipeline_root),
            "cohort_database": str(self.cohort.database_path),
            "phenotype_database": str(self.phenotypes.database_path),
            "container_runtimes": runtimes,
            "hardware": hardware,
            "profiles": [profile],
            "input_assemblies": [
                {
                    "id": "GRCh38",
                    "label": "GRCh38 / hg38 — annotate directly",
                },
                {
                    "id": "GRCh37",
                    "label": "GRCh37 / hg19 — liftover to GRCh38",
                },
                {
                    "id": "auto",
                    "label": "Detect from VCF header",
                },
            ],
            "defaults": {
                "config_path": str(self.pipeline_root / "config" / "annotation.config.yaml"),
                "coding_only": True,
                "include_filtered": False,
                "use_clinvar": True,
                "input_assembly": "auto",
            },
            "annotation_profile": annotation_profile,
        }

    def resource_downloads(self) -> list[dict]:
        with self._resource_lock:
            jobs = [
                self._resource_job_copy(job)
                for job in self._resource_jobs.values()
            ]
        return sorted(jobs, key=lambda job: job["created_at"], reverse=True)

    def start_resource_download(self, resource_id: str) -> dict:
        if resource_id not in RESOURCE_DOWNLOAD_COMMANDS:
            raise ValueError(f"resource cannot be downloaded from the UI: {resource_id}")
        with self._resource_lock:
            for job in self._resource_jobs.values():
                if (
                    job["resource_id"] == resource_id
                    and job["status"] in {"queued", "running"}
                ):
                    return self._resource_job_copy(job)
            job_id = uuid.uuid4().hex
            job = {
                "id": job_id,
                "resource_id": resource_id,
                "status": "queued",
                "progress": None,
                "message": "Waiting to start…",
                "created_at": utc_now(),
                "started_at": None,
                "finished_at": None,
                "exit_code": None,
                "error": "",
                "log_path": str(self.resource_logs_dir / f"{job_id}.log"),
            }
            self._resource_jobs[job_id] = job
            thread = threading.Thread(
                target=self._run_resource_download,
                args=(job_id,),
                name=f"resource-{resource_id}",
                daemon=True,
            )
            self._resource_threads[job_id] = thread
            thread.start()
            return self._resource_job_copy(job)

    @staticmethod
    def _resource_job_copy(job: dict) -> dict:
        result = dict(job)
        path = Path(result["log_path"])
        if path.exists():
            with path.open("rb") as handle:
                handle.seek(0, os.SEEK_END)
                size = handle.tell()
                handle.seek(max(0, size - 16_000))
                result["log"] = handle.read().decode("utf-8", errors="replace")
        else:
            result["log"] = ""
        return result

    def _update_resource_job(self, job_id: str, **values) -> None:
        with self._resource_lock:
            job = self._resource_jobs.get(job_id)
            if job:
                job.update(values)

    def _run_resource_download(self, job_id: str) -> None:
        with self._resource_lock:
            job = self._resource_jobs.get(job_id)
            if not job:
                return
            resource_id = job["resource_id"]
        specification = RESOURCE_DOWNLOAD_COMMANDS[resource_id]
        config_path = self.pipeline_root / "config" / "annotation.config.yaml"
        command = [
            "bash",
            str(self.pipeline_root / specification[0]),
            str(config_path),
            *specification[1:],
        ]
        self._update_resource_job(
            job_id,
            status="running",
            started_at=utc_now(),
            message="Starting download…",
        )
        log_path = self.resource_logs_dir / f"{job_id}.log"
        exit_code = 1
        last_line = ""
        try:
            with log_path.open("a", encoding="utf-8", buffering=1) as log:
                log.write("$ " + " ".join(json.dumps(item) for item in command) + "\n")
                process = subprocess.Popen(
                    command,
                    cwd=self.pipeline_root,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    start_new_session=(os.name == "posix"),
                )
                with self._resource_lock:
                    self._resource_processes[job_id] = process
                assert process.stdout is not None
                with process.stdout:
                    for line in process.stdout:
                        log.write(line)
                        last_line = line.strip() or last_line
                        progress_match = re.match(r"^\s*(\d+(?:\.\d+)?)%", line)
                        updates = {"message": last_line}
                        if progress_match:
                            updates["progress"] = min(
                                100.0, float(progress_match.group(1))
                            )
                        self._update_resource_job(job_id, **updates)
                exit_code = process.wait()
            if exit_code:
                raise RuntimeError(last_line or f"download exited with code {exit_code}")
            self._update_resource_job(
                job_id,
                status="succeeded",
                progress=100.0,
                message="Download complete.",
                finished_at=utc_now(),
                exit_code=0,
                error="",
            )
        except Exception as exc:
            self._update_resource_job(
                job_id,
                status="failed",
                message=last_line or "Download failed.",
                finished_at=utc_now(),
                exit_code=exit_code,
                error=str(exc),
            )
        finally:
            with self._resource_lock:
                self._resource_processes.pop(job_id, None)

    def review_file(self, job_id: str) -> Path:
        """Return only an output path already recorded for a local annotation job."""
        job = self.store.get(job_id)
        if not job:
            raise KeyError("job not found")
        candidate = Path(
            job.get("final_output_path") or job.get("output_path") or ""
        ).expanduser().resolve()
        recorded = {
            Path(value).expanduser().resolve()
            for value in (job.get("final_output_path"), job.get("output_path"))
            if value
        }
        if candidate not in recorded:
            raise ValueError("job output path is invalid")
        if not candidate.is_file():
            raise FileNotFoundError("annotated VCF output is no longer available")
        if not re.search(r"\.vcf(?:\.gz)?$", candidate.name, re.IGNORECASE):
            raise ValueError("job output is not a VCF")
        return candidate

    @staticmethod
    def _hardware_profile() -> dict:
        logical_cpus = max(1, int(os.cpu_count() or 1))
        # VEP forks are CPU- and memory-intensive. Half the logical threads,
        # capped at eight, is a conservative workstation default that leaves
        # capacity for the operating system, browser, and container runtime.
        recommended = max(1, min(8, logical_cpus // 2))
        maximum = max(recommended, min(32, max(1, logical_cpus - 1)))
        return {
            "logical_cpus": logical_cpus,
            "recommended_vep_workers": recommended,
            "max_vep_workers": maximum,
        }

    def _installed_vep_releases(self, config: dict) -> tuple[int | None, int | None]:
        container_tag = str((config.get("container") or {}).get("vep_image_tag") or "")
        container_match = re.search(r"release[_-]?(\d+)", container_tag, re.I)
        container_release = int(container_match.group(1)) if container_match else None

        cache_root = self._resolved_reference_path(
            (config.get("reference") or {}).get("vep_cache_dir")
        )
        cache_releases: list[int] = []
        species = str((config.get("reference") or {}).get("species") or "homo_sapiens")
        species_dir = cache_root / species if cache_root else None
        if species_dir and species_dir.is_dir():
            for candidate in species_dir.iterdir():
                match = re.match(r"^(\d+)_GRCh38$", candidate.name)
                if candidate.is_dir() and match:
                    cache_releases.append(int(match.group(1)))
        # A newer cache may be present after a staged or failed update. Report
        # the cache paired with the active container, not merely the newest
        # directory on disk.
        active_cache_release = (
            container_release
            if container_release in cache_releases
            else max(cache_releases) if cache_releases else None
        )
        return active_cache_release, container_release

    def submit(self, payload: dict) -> dict:
        with self._resource_lock:
            downloading = [
                job["resource_id"]
                for job in self._resource_jobs.values()
                if job["status"] in {"queued", "running"}
            ]
        if downloading:
            raise ValueError(
                "wait for annotation dataset download to finish: "
                + ", ".join(sorted(set(downloading)))
            )
        input_path = self._required_path(payload, "input_path", must_exist=True)
        output_path = self._required_path(payload, "output_path", must_exist=False)
        input_name = input_path.name.lower()
        if not (input_name.endswith(".vcf") or input_name.endswith(".vcf.gz")):
            raise ValueError("input_path must end in .vcf or .vcf.gz")
        if output_path.suffixes[-2:] != [".vcf", ".gz"]:
            raise ValueError("output_path must end in .vcf.gz")
        if input_path == output_path:
            raise ValueError("input_path and output_path must be different")
        output_path.parent.mkdir(parents=True, exist_ok=True)

        config_value = payload.get("config_path") or (
            self.pipeline_root / "config" / "annotation.config.yaml"
        )
        config_path = Path(config_value).expanduser().resolve()
        if not config_path.is_file():
            raise ValueError(f"config_path does not exist: {config_path}")

        profile = str(payload.get("profile") or ("wsl-local" if is_wsl() else "local"))
        if profile not in ALLOWED_PROFILES:
            raise ValueError(f"unsupported execution profile: {profile}")
        if profile == "wsl-local" and not is_wsl():
            raise ValueError("wsl-local is available only when the service runs inside WSL")
        input_assembly = str(payload.get("input_assembly") or "auto")
        if input_assembly not in {"GRCh38", "GRCh37", "auto"}:
            raise ValueError("input_assembly must be GRCh38, GRCh37, or auto")

        job_id = uuid.uuid4().hex
        annotation_options = payload.get("annotation_options")
        if annotation_options is not None:
            if not isinstance(annotation_options, dict):
                raise ValueError("annotation_options must be an object")
            config_path = self._write_job_config(
                job_id, config_path, annotation_options
            )
        now = utc_now()
        job = self.store.create(
            {
                "id": job_id,
                "created_at": now,
                "updated_at": now,
                "status": "queued",
                "profile": profile,
                "input_assembly": input_assembly,
                "input_path": str(input_path),
                "output_path": str(output_path),
                "config_path": str(config_path),
                "coding_only": int(bool(payload.get("coding_only", True))),
                "include_filtered": int(bool(payload.get("include_filtered", False))),
                "use_clinvar": int(bool(payload.get("use_clinvar", True))),
                "log_path": str(self.logs_dir / f"{job_id}.log"),
            }
        )
        self._queue.put(job_id)
        return job

    def stage_file(
        self, filename: str, relative_path: str, batch_id: str, length: int, stream
    ) -> dict:
        """Stream a browser-selected VCF into workstation-local job storage."""
        if length < 1:
            raise ValueError("the selected file is empty")
        if length > 2_000_000_000_000:
            raise ValueError("the selected file is too large")
        safe_batch = "".join(
            character for character in batch_id if character.isalnum() or character in "-_"
        )[:80]
        if not safe_batch:
            raise ValueError("a valid upload batch is required")
        raw_parts = Path(relative_path or filename).parts
        safe_parts = [
            part for part in raw_parts
            if part not in {"", ".", "..", "/", "\\"}
        ]
        safe_parts = [
            "".join(
                character for character in part
                if character.isalnum() or character in " ._-()"
            ).strip()
            for part in safe_parts
        ]
        safe_parts = [part for part in safe_parts if part]
        safe_name = Path(filename).name
        if not safe_parts:
            safe_parts = [safe_name]
        elif safe_parts[-1] != safe_name:
            safe_parts[-1] = safe_name
        lower_name = safe_name.lower()
        if not (lower_name.endswith(".vcf") or lower_name.endswith(".vcf.gz")):
            raise ValueError("only .vcf and .vcf.gz files can be staged")

        batch_root = (self.state_dir / "uploads" / safe_batch).resolve()
        destination = (batch_root.joinpath(*safe_parts)).resolve()
        if batch_root not in destination.parents:
            raise ValueError("invalid relative file path")
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(destination.name + ".partial")
        remaining = length
        with temporary.open("wb") as handle:
            while remaining:
                chunk = stream.read(min(1024 * 1024, remaining))
                if not chunk:
                    temporary.unlink(missing_ok=True)
                    raise ValueError("upload ended before the complete file was received")
                handle.write(chunk)
                remaining -= len(chunk)
        temporary.replace(destination)
        return {
            "path": str(destination),
            "filename": destination.name,
            "relative_path": str(Path(*safe_parts)),
            "bytes": length,
        }

    def _load_config(self, config_path: Path) -> dict:
        try:
            import yaml
        except ImportError as exc:
            raise ValueError(
                "PyYAML is required for the annotation settings screen "
                "(pip install pyyaml)."
            ) from exc
        config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        if not isinstance(config, dict):
            raise ValueError(f"annotation config is not a YAML object: {config_path}")
        return config

    def _resolved_reference_path(self, value) -> Path | None:
        if not isinstance(value, str) or not value.strip():
            return None
        path = Path(value).expanduser()
        return path.resolve() if path.is_absolute() else (self.pipeline_root / path).resolve()

    def _container_image_available(self, config: dict) -> bool:
        container = config.get("container") or {}
        runtime = str(container.get("runtime") or "docker")
        image = str(container.get("image") or "vep-annotate:latest")
        if not shutil.which(runtime):
            return False
        if runtime in {"docker", "podman"}:
            try:
                result = subprocess.run(
                    [runtime, "image", "ls", "--quiet", "--no-trunc", image],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=False,
                )
            except (OSError, subprocess.SubprocessError):
                return False
            return result.returncode == 0 and bool(result.stdout.strip())
        return Path(image).expanduser().is_file()

    def _dbnsfp_header_columns(self, config: dict) -> set[str]:
        block = ((config.get("plugins") or {}).get("dbNSFP") or {})
        path = self._resolved_reference_path(block.get("path"))
        if not path or not path.is_file():
            return set()
        opener = gzip.open if path.name.lower().endswith((".gz", ".bgz")) else open
        try:
            with opener(path, "rt", encoding="utf-8", errors="replace") as handle:
                return set(handle.readline().rstrip("\r\n").lstrip("#").split("\t"))
        except (OSError, UnicodeError):
            return set()

    def _annotation_profile(self) -> dict:
        config_path = self.pipeline_root / "config" / "annotation.config.yaml"
        labels = {
            "dbnsfp": ("dbNSFP", "AlphaMissense, CADD and additional missense predictors"),
            "loftee": ("LOFTEE", "Loss-of-function consequence confidence"),
            "spliceai": ("SpliceAI MANE", "Precomputed MANE v1.4 splice scores"),
            "repeatmasker": ("RepeatMasker", "Repeat-region overlap flag"),
            "segdup": ("Segmental duplications", "SegDup overlap flag"),
            "promoterai": ("promoterAI", "Optional licensed promoter score track"),
            "logofunc": ("LoGoFunc", "Optional functional-mechanism predictions"),
            "clinvar": ("ClinVar", "Clinical assertions; refreshed per run by default"),
            "loftee_ptc_50bp": ("Frameshift PTC 50-bp rule", "Pipeline recomputation using local GTF and FASTA"),
            "clinvar_aa_match": ("ClinVar residue match", "Known pathogenic missense at the same amino-acid residue"),
        }
        try:
            config = self._load_config(config_path)
        except ValueError as exc:
            return {
                "ready": False,
                "error": str(exc),
                "foundations": [],
                "sources": [],
                "dbnsfp_predictors": [],
                "defaults": {"fork": 8},
            }

        def source_path(source_id: str, block: dict) -> list[Path]:
            if source_id == "dbnsfp":
                values = [block.get("path")]
            elif source_id == "loftee":
                values = [
                    block.get("human_ancestor_fa"),
                    block.get("conservation_file"),
                    block.get("gerp_bigwig"),
                ]
            elif source_id == "spliceai":
                values = [block.get("snv")]
            elif source_id == "loftee_ptc_50bp":
                values = [
                    block.get("gtf"),
                    ((config.get("reference") or {}).get("fasta") or {}).get("path"),
                ]
            elif source_id == "clinvar_aa_match":
                values = []
            else:
                values = [block.get("file")]
            return [
                resolved for value in values
                if (resolved := self._resolved_reference_path(value)) is not None
            ]

        sources = []
        for source_id, location in ANNOTATION_SOURCE_PATHS.items():
            parent = config.get(location[0]) or {}
            block = parent.get(location[1]) or {}
            paths = source_path(source_id, block)
            enabled = bool(block.get("enabled", False))
            required = source_id in REQUIRED_DIAGNOSTIC_SOURCES or bool(
                block.get("required", False)
            )
            # ClinVar can be fetched when a run starts, so an absent local copy
            # is not a setup blocker.
            auto_fetch = source_id == "clinvar" and bool(
                (config.get("clinvar") or {}).get("auto_fetch", True)
            )
            installed = not paths or all(path.exists() for path in paths)
            if installed and source_id in {
                "dbnsfp", "spliceai", "repeatmasker", "segdup", "clinvar"
            }:
                installed = all(
                    path.exists()
                    and (
                        not path.name.endswith(".gz")
                        or Path(str(path) + ".tbi").exists()
                        or Path(str(path) + ".csi").exists()
                    )
                    for path in paths
                )
            available = auto_fetch or installed
            label, description = labels[source_id]
            setup = ANNOTATION_SOURCE_SETUP[source_id]
            sources.append({
                "id": source_id,
                "label": label,
                "description": description,
                "enabled": enabled,
                "required": required,
                "available": available,
                "installed": installed,
                "configured_paths": [str(path) for path in paths],
                "version": str(block.get("version") or ""),
                **setup,
                "status": (
                    "ready" if installed else
                    "required_missing" if enabled and required else
                    "optional_missing"
                ),
            })

        fasta = ((config.get("reference") or {}).get("fasta") or {})
        cache_path = self._resolved_reference_path(
            (config.get("reference") or {}).get("vep_cache_dir")
        )
        fasta_path = self._resolved_reference_path(fasta.get("path"))
        cache_release, container_release = self._installed_vep_releases(config)
        container_available = self._container_image_available(config)
        foundations = [
            {
                "id": "vep_container",
                "label": "Pinned VEP container",
                "available": container_available,
                "required": True,
                "version": container_release,
            },
            {
                "id": "vep_cache",
                "label": "Ensembl VEP cache",
                "available": bool(cache_path and cache_path.exists()),
                "required": True,
                "version": cache_release,
            },
            {
                "id": "reference_fasta",
                "label": "GRCh38 reference FASTA",
                "available": bool(fasta_path and fasta_path.exists()),
                "required": True,
                "version": None,
            },
        ]
        ready = all(
            item["available"] for item in foundations
        ) and all(
            source["available"]
            for source in sources
            if source["enabled"] and source["required"]
        )
        dbnsfp_header = self._dbnsfp_header_columns(config)
        dbnsfp_predictors = [
            {
                **predictor,
                "available": bool(dbnsfp_header) and all(
                    column in dbnsfp_header for column in predictor["columns"]
                ),
            }
            for predictor in DBNSFP_OPTIONAL_PREDICTORS
        ]
        return {
            "ready": ready,
            "error": (
                ""
                if container_available
                else "Pinned VEP 113 container is not installed. Run: bash docker/build.sh"
            ),
            "foundations": foundations,
            "sources": sources,
            "dbnsfp_predictors": dbnsfp_predictors,
            "defaults": {
                "fork": int((config.get("run") or {}).get("fork", 8)),
            },
        }

    def _write_job_config(
        self, job_id: str, base_config_path: Path, options: dict
    ) -> Path:
        config = self._load_config(base_config_path)
        for source_id in REQUIRED_DIAGNOSTIC_SOURCES:
            if options.get(source_id) is False:
                raise ValueError(
                    f"{source_id} is required by the diagnostic annotation profile"
                )
        for source_id, location in ANNOTATION_SOURCE_PATHS.items():
            if source_id not in options:
                continue
            parent = config.setdefault(location[0], {})
            block = parent.setdefault(location[1], {})
            block["enabled"] = bool(options[source_id])
        if "fork" in options:
            try:
                fork = int(options["fork"])
            except (TypeError, ValueError) as exc:
                raise ValueError("VEP workers must be a number") from exc
            if fork < 1 or fork > 64:
                raise ValueError("VEP workers must be between 1 and 64")
            config.setdefault("run", {})["fork"] = fork
        if "dbnsfp_predictors" in options:
            selected = options["dbnsfp_predictors"]
            if not isinstance(selected, list) or not all(
                isinstance(value, str) for value in selected
            ):
                raise ValueError("dbnsfp_predictors must be a list of predictor IDs")
            catalog = {
                predictor["id"]: predictor
                for predictor in DBNSFP_OPTIONAL_PREDICTORS
            }
            unknown = sorted(set(selected) - set(catalog))
            if unknown:
                raise ValueError(
                    "unsupported dbNSFP predictor selection: " + ", ".join(unknown)
                )
            header_columns = self._dbnsfp_header_columns(config)
            requested_columns = [
                column
                for predictor_id in dict.fromkeys(selected)
                for column in catalog[predictor_id]["columns"]
            ]
            unavailable = [
                column for column in requested_columns
                if column not in header_columns
            ]
            if unavailable:
                raise ValueError(
                    "selected dbNSFP fields are not available in the installed "
                    "dataset: " + ", ".join(unavailable)
                )
            dbnsfp = config.setdefault("plugins", {}).setdefault("dbNSFP", {})
            base_columns = dbnsfp.get("columns") or []
            if not isinstance(base_columns, list):
                raise ValueError(
                    "UI predictor selection requires dbNSFP columns to be a YAML list"
                )
            dbnsfp["columns"] = list(dict.fromkeys(base_columns + requested_columns))

        # These consumers historically resolve paths relative to the config
        # location. Make the two affected values absolute in the generated,
        # per-job config so its state-directory location is transparent.
        fasta = (config.get("reference") or {}).get("fasta") or {}
        if fasta.get("path"):
            fasta["path"] = str(self._resolved_reference_path(fasta["path"]))
        ptc = (config.get("post_processing") or {}).get("loftee_ptc_50bp") or {}
        if ptc.get("gtf"):
            ptc["gtf"] = str(self._resolved_reference_path(ptc["gtf"]))
        clinvar = config.get("clinvar") or {}
        if clinvar.get("dest_dir"):
            clinvar["dest_dir"] = str(
                self._resolved_reference_path(clinvar["dest_dir"])
            )

        import yaml
        config_dir = self.state_dir / "job-configs"
        config_dir.mkdir(parents=True, exist_ok=True)
        destination = config_dir / f"{job_id}.annotation.yaml"
        destination.write_text(
            "# Generated by IEI Variant Review from UI settings.\n"
            + yaml.safe_dump(config, sort_keys=False),
            encoding="utf-8",
        )
        return destination

    @staticmethod
    def _required_path(payload: dict, key: str, must_exist: bool) -> Path:
        raw = payload.get(key)
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError(f"{key} is required")
        path = Path(raw).expanduser().resolve()
        if must_exist and not path.is_file():
            raise ValueError(f"{key} does not exist: {path}")
        return path

    def cancel(self, job_id: str) -> dict:
        job = self.store.get(job_id)
        if not job:
            raise KeyError(job_id)
        if job["status"] in TERMINAL_STATUSES:
            return job
        if job["status"] == "queued":
            return self.store.update(
                job_id,
                status="cancelled",
                finished_at=utc_now(),
                error="Cancelled before execution.",
            )
        with self._process_lock:
            process = self._processes.get(job_id)
        if process and process.poll() is None:
            try:
                if os.name == "posix":
                    os.killpg(process.pid, signal.SIGTERM)
                else:
                    process.terminate()
            except ProcessLookupError:
                pass
        return self.store.update(
            job_id,
            status="cancelled",
            finished_at=utc_now(),
            error="Cancelled by the user.",
        )

    def log_tail(self, job_id: str, max_bytes: int = 64_000) -> str:
        job = self.store.get(job_id)
        if not job:
            raise KeyError(job_id)
        path = Path(job["log_path"])
        if not path.exists():
            return ""
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - max_bytes))
            return handle.read().decode("utf-8", errors="replace")

    def _commands(self, job: dict) -> list[list[str]]:
        preflight = [
            "bash",
            str(self.pipeline_root / "scripts" / "preflight.sh"),
            job["config_path"],
            job["input_path"],
            job["output_path"],
            "--input-assembly",
            job["input_assembly"],
        ]
        annotation = [
            "bash",
            str(self.pipeline_root / "scripts" / "run_annotation.sh"),
            "--input",
            job["input_path"],
            "--output",
            job["output_path"],
            "--config",
            job["config_path"],
            "--input-assembly",
            job["input_assembly"],
        ]
        if not job["coding_only"]:
            annotation.append("--all-variants")
        if job["include_filtered"]:
            annotation.append("--include-filtered")
        if not job["use_clinvar"]:
            annotation.append("--no-clinvar")
        return [preflight, annotation]

    def _worker_loop(self) -> None:
        while not self._stop.is_set():
            try:
                job_id = self._queue.get(timeout=0.25)
            except queue.Empty:
                continue
            if job_id is None:
                self._queue.task_done()
                return
            try:
                self._run_job(job_id)
            finally:
                self._queue.task_done()

    def _run_job(self, job_id: str) -> None:
        job = self.store.get(job_id)
        if not job or job["status"] != "queued":
            return
        commands = self._commands(job)
        self.store.update(
            job_id,
            status="running",
            started_at=utc_now(),
            command_json=json.dumps(commands),
            error=None,
        )
        log_path = Path(job["log_path"])
        exit_code = 0
        try:
            with log_path.open("a", encoding="utf-8", buffering=1) as log:
                log.write(f"[{utc_now()}] Job {job_id} started\n")
                for command in commands:
                    current = self.store.get(job_id)
                    if not current or current["status"] == "cancelled":
                        return
                    log.write("$ " + " ".join(json.dumps(item) for item in command) + "\n")
                    process = subprocess.Popen(
                        command,
                        cwd=self.pipeline_root,
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        start_new_session=(os.name == "posix"),
                    )
                    with self._process_lock:
                        self._processes[job_id] = process
                    self.store.update(job_id, pid=process.pid)
                    exit_code = process.wait()
                    with self._process_lock:
                        self._processes.pop(job_id, None)
                    current = self.store.get(job_id)
                    if current and current["status"] == "cancelled":
                        return
                    if exit_code:
                        raise RuntimeError(
                            f"Command failed with exit code {exit_code}. See the job log."
                        )
                final_output = self._resolve_final_output(Path(job["output_path"]))
                log.write(f"[{utc_now()}] Job completed: {final_output}\n")
            self.store.update(
                job_id,
                status="succeeded",
                finished_at=utc_now(),
                final_output_path=str(final_output),
                exit_code=0,
                pid=None,
            )
        except Exception as exc:
            current = self.store.get(job_id)
            if current and current["status"] == "cancelled":
                return
            self.store.update(
                job_id,
                status="failed",
                finished_at=utc_now(),
                exit_code=exit_code or 1,
                pid=None,
                error=str(exc),
            )
        finally:
            with self._process_lock:
                self._processes.pop(job_id, None)

    @staticmethod
    def _resolve_final_output(output_path: Path) -> Path:
        aamatch = Path(str(output_path)[:-7] + ".aamatch.vcf.gz")
        return aamatch if aamatch.exists() else output_path

    def shutdown(self) -> None:
        self._stop.set()
        with self._process_lock:
            running = list(self._processes.items())
        for job_id, process in running:
            if process.poll() is None:
                try:
                    if os.name == "posix":
                        os.killpg(process.pid, signal.SIGTERM)
                    else:
                        process.terminate()
                except ProcessLookupError:
                    pass
                self.store.update(
                    job_id,
                    status="interrupted",
                    finished_at=utc_now(),
                    error="The local service was stopped.",
                )
        with self._resource_lock:
            resource_running = list(self._resource_processes.items())
            resource_threads = list(self._resource_threads.values())
        for job_id, process in resource_running:
            if process.poll() is None:
                try:
                    if os.name == "posix":
                        os.killpg(process.pid, signal.SIGTERM)
                    else:
                        process.terminate()
                except ProcessLookupError:
                    pass
                self._update_resource_job(
                    job_id,
                    status="interrupted",
                    finished_at=utc_now(),
                    error="The local service was stopped.",
                )
        self._queue.put(None)
        if self._worker:
            self._worker.join(timeout=5)
        for thread in resource_threads:
            thread.join(timeout=2)


class WorkbenchRequestHandler(BaseHTTPRequestHandler):
    service: AnnotationJobService
    server_version = f"IEIWorkbench/{SERVICE_VERSION}"

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self._cors_headers()
        self.end_headers()

    def do_GET(self) -> None:
        parsed_url = urlparse(self.path)
        path = parsed_url.path
        query = parse_qs(parsed_url.query)
        if path == "/api/health":
            self._json({"ok": True, "version": SERVICE_VERSION})
        elif path == "/api/capabilities":
            self._json(self.service.capabilities())
        elif path == "/api/jobs":
            self._json({"jobs": self.service.store.list()})
        elif path == "/api/resource-downloads":
            self._json({"jobs": self.service.resource_downloads()})
        elif path == "/api/cohort/stats":
            self._json(self.service.cohort.stats())
        elif path.startswith("/api/cohort/import-jobs/"):
            job_id = path.removeprefix("/api/cohort/import-jobs/")
            job = self.service.cohort.get_import_job(job_id)
            self._json(
                job if job else {"error": "cohort import job not found"},
                HTTPStatus.OK if job else HTTPStatus.NOT_FOUND,
            )
        elif path == "/api/phenotypes/stats":
            self._json(self.service.phenotypes.stats())
        elif path == "/api/phenotypes/profiles":
            self._json({"profiles": self.service.phenotypes.profiles()})
        elif path == "/api/phenotypes":
            self._json({"individuals": self.service.phenotypes.list(
                query=(query.get("query") or [""])[0],
                limit=int((query.get("limit") or ["500"])[0]),
            )})
        elif path.startswith("/api/phenotypes/by-sample/"):
            sample_id = unquote(path.removeprefix("/api/phenotypes/by-sample/"))
            self._json({"individuals": self.service.phenotypes.by_sample(sample_id)})
        elif path.startswith("/api/phenotypes/individual/"):
            individual_id = unquote(path.removeprefix("/api/phenotypes/individual/"))
            record = self.service.phenotypes.get(individual_id)
            self._json(
                record if record else {"error": "individual not found"},
                HTTPStatus.OK if record else HTTPStatus.NOT_FOUND,
            )
        elif path.startswith("/api/jobs/") and path.endswith("/log"):
            job_id = path.split("/")[3]
            try:
                self._json({"job_id": job_id, "log": self.service.log_tail(job_id)})
            except KeyError:
                self._json({"error": "job not found"}, HTTPStatus.NOT_FOUND)
        elif path.startswith("/api/jobs/") and path.endswith("/review-file"):
            job_id = path.split("/")[3]
            try:
                self._file(self.service.review_file(job_id))
            except KeyError:
                self._json({"error": "job not found"}, HTTPStatus.NOT_FOUND)
            except FileNotFoundError as exc:
                self._json({"error": str(exc)}, HTTPStatus.NOT_FOUND)
            except ValueError as exc:
                self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        elif path.startswith("/api/jobs/"):
            job_id = path.split("/")[3]
            job = self.service.store.get(job_id)
            self._json(job if job else {"error": "job not found"},
                       HTTPStatus.OK if job else HTTPStatus.NOT_FOUND)
        else:
            self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            if path == "/api/annotation-files/stage":
                length = int(self.headers.get("Content-Length", "0"))
                self._json(
                    self.service.stage_file(
                        unquote(self.headers.get("X-File-Name", "")),
                        unquote(self.headers.get("X-Relative-Path", "")),
                        self.headers.get("X-Upload-Batch", ""),
                        length,
                        self.rfile,
                    ),
                    HTTPStatus.CREATED,
                )
                return
            if path == "/api/jobs":
                self._json(self.service.submit(self._body()), HTTPStatus.CREATED)
                return
            if path.startswith("/api/resource-downloads/"):
                resource_id = unquote(
                    path.removeprefix("/api/resource-downloads/")
                )
                self._json(
                    self.service.start_resource_download(resource_id),
                    HTTPStatus.ACCEPTED,
                )
                return
            if path == "/api/cohort/import":
                body = self._body()
                paths = body.get("paths")
                if not isinstance(paths, list):
                    raise ValueError("paths must be a list of VCF files or directories")
                self._json(self.service.cohort.import_paths(
                    paths,
                    recursive=bool(body.get("recursive", True)),
                    force=bool(body.get("force", False)),
                    allow_unknown_assembly=bool(
                        body.get("allow_unknown_assembly", False)
                    ),
                ))
                return
            if path == "/api/cohort/import-jobs":
                body = self._body()
                paths = body.get("paths")
                if not isinstance(paths, list):
                    raise ValueError("paths must be a list of VCF files or directories")
                self._json(self.service.cohort.start_import_paths(
                    paths,
                    recursive=bool(body.get("recursive", True)),
                    force=bool(body.get("force", False)),
                    allow_unknown_assembly=bool(
                        body.get("allow_unknown_assembly", False)
                    ),
                ), HTTPStatus.ACCEPTED)
                return
            if path == "/api/cohort/query":
                self._json(self.service.cohort.query(self._body()))
                return
            if path == "/api/phenotypes/preview":
                self._json(self.service.phenotypes.preview(
                    self._body(max_bytes=30_000_000)
                ))
                return
            if path == "/api/phenotypes/validate":
                self._json(self.service.phenotypes.validate(
                    self._body(max_bytes=30_000_000)
                ))
                return
            if path == "/api/phenotypes/import":
                self._json(self.service.phenotypes.import_records(
                    self._body(max_bytes=30_000_000)
                ))
                return
            if path == "/api/phenotypes/individual":
                self._json(
                    self.service.phenotypes.save_individual(self._body()),
                    HTTPStatus.CREATED,
                )
                return
            if path.startswith("/api/jobs/") and path.endswith("/cancel"):
                job_id = path.split("/")[3]
                self._json(self.service.cancel(job_id))
                return
            self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        except KeyError:
            self._json({"error": "job not found"}, HTTPStatus.NOT_FOUND)
        except (ValueError, json.JSONDecodeError) as exc:
            self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

    def _body(self, max_bytes: int = 1_000_000) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length > max_bytes:
            raise ValueError("request body is too large")
        raw = self.rfile.read(length)
        value = json.loads(raw or b"{}")
        if not isinstance(value, dict):
            raise ValueError("JSON body must be an object")
        return value

    def _json(self, value, status: HTTPStatus = HTTPStatus.OK) -> None:
        payload = json.dumps(value).encode("utf-8")
        self.send_response(status)
        self._cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _file(self, path: Path) -> None:
        content_type = (
            "application/gzip"
            if path.name.lower().endswith(".gz")
            else "text/plain; charset=utf-8"
        )
        self.send_response(HTTPStatus.OK)
        self._cors_headers()
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(path.stat().st_size))
        self.send_header(
            "Content-Disposition",
            f'attachment; filename="{path.name.replace(chr(34), "")}"',
        )
        self.end_headers()
        with path.open("rb") as handle:
            shutil.copyfileobj(handle, self.wfile, length=1024 * 1024)

    def _cors_headers(self) -> None:
        origin = self.headers.get("Origin")
        if origin in {"http://127.0.0.1:3000", "http://localhost:3000"}:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Expose-Headers", "Content-Disposition")
        self.send_header(
            "Access-Control-Allow-Headers",
            "Content-Type, X-File-Name, X-Relative-Path, X-Upload-Batch",
        )

    def log_message(self, fmt: str, *args) -> None:
        print(f"[local-service] {self.address_string()} {fmt % args}")


class LoopbackHTTPServer(ThreadingHTTPServer):
    """HTTP server that avoids a slow reverse-DNS lookup during local startup."""

    def server_bind(self) -> None:
        socketserver.TCPServer.server_bind(self)
        self.server_name = str(self.server_address[0])
        self.server_port = int(self.server_address[1])


def create_server(
    service: AnnotationJobService, host: str = "127.0.0.1", port: int = 43117
) -> ThreadingHTTPServer:
    handler = type(
        "ConfiguredWorkbenchRequestHandler",
        (WorkbenchRequestHandler,),
        {"service": service},
    )
    return LoopbackHTTPServer((host, port), handler)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=43117, type=int)
    parser.add_argument("--state-dir", type=Path, default=default_state_dir())
    parser.add_argument(
        "--pipeline-root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    args = parser.parse_args()
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        parser.error("this workstation service may bind only to a loopback address")

    service = AnnotationJobService(args.pipeline_root, args.state_dir)
    server = create_server(service, args.host, args.port)
    print(f"IEI local service listening on http://{args.host}:{args.port}")
    print(f"State: {service.state_dir}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        service.shutdown()


if __name__ == "__main__":
    main()
