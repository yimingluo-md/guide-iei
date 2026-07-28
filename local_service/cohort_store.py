#!/usr/bin/env python3
"""Persistent genotype-first cohort index for VEP-annotated VCF files.

The index is intentionally local and dependency-free. VCFs are parsed once
into SQLite, after which exact-variant and qualifying gene queries do not need
to reopen hundreds of source files.
"""

from __future__ import annotations

import gzip
import re
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Callable
from urllib.parse import unquote

from pipeline.vcf_assembly import detect_vcf_assembly


EMPTY = {"", ".", "-"}
IMPACT_ORDER = {"HIGH": 1, "MODERATE": 2, "LOW": 3, "MODIFIER": 4, "UNKNOWN": 5}
ALLOWED_IMPACTS = set(IMPACT_ORDER)


def utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_chromosome(value: str) -> str:
    chrom = value.strip()
    if chrom.lower().startswith("chr"):
        chrom = chrom[3:]
    return "MT" if chrom.upper() in {"M", "MT"} else chrom.upper()


def variant_key(chrom: str, pos: int, ref: str, alt: str) -> str:
    return f"{normalize_chromosome(chrom)}:{pos}:{ref.upper()}:{alt.upper()}"


def decode(value: str | None) -> str:
    return unquote((value or "").replace("+", " "))


def parse_number(value: str | None) -> float | None:
    if not value or value in EMPTY:
        return None
    try:
        parsed = float(value.split("&", 1)[0])
    except ValueError:
        return None
    return parsed


def info_map(raw: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in raw.split(";"):
        key, separator, value = item.partition("=")
        if key:
            result[key] = value if separator else "1"
    return result


def first(record: dict[str, str], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = record.get(key, "")
        if value not in EMPTY:
            return decode(value)
    return ""


def maximum(record: dict[str, str], keys: tuple[str, ...]) -> float | None:
    values: list[float] = []
    for key in keys:
        for item in record.get(key, "").replace("&", ",").split(","):
            parsed = parse_number(item)
            if parsed is not None:
                values.append(parsed)
    return max(values) if values else None


def truthy(value: str) -> bool:
    return value not in EMPTY and value != "0"

def haplotype_frame_evidence(
    raw: str, current_variant: str, sample: str
) -> dict[str, str]:
    matches = []
    for entry in (raw or "").split(","):
        fields = [decode(value) for value in entry.split("|")]
        if len(fields) < 6:
            continue
        variant, event_sample, transcript, status, partners, protein = fields[:6]
        if variant != current_variant or event_sample != sample:
            continue
        if status not in {
            "FRAME_RESTORED_CONFIRMED",
            "FRAME_RESTORATION_PARTIAL_CONFIRMED",
            "FRAME_RESTORING_POSSIBLE_UNPHASED",
        }:
            continue
        matches.append({
            "status": status,
            "partners": partners,
            "protein": protein,
            "transcript": transcript,
        })
    matches.sort(
        key=lambda value: value["status"] != "FRAME_RESTORED_CONFIRMED"
    )
    return matches[0] if matches else {
        "status": "", "partners": "", "protein": "", "transcript": ""
    }


def parse_genotype(format_value: str, sample_value: str, alt_index: int) -> dict:
    keys = format_value.split(":")
    values = sample_value.split(":")
    fields = dict(zip(keys, values))
    gt = fields.get("GT") or "./."
    allele_strings = re.split(r"[|/]", gt)
    called = [int(item) for item in allele_strings if item.isdigit()]
    allele_number = alt_index + 1
    copies = called.count(allele_number)
    if not copies:
        return {"carrier": False}

    if len(called) == 1:
        zygosity = "hemizygous"
    elif copies == len(called):
        zygosity = "homozygous"
    elif copies == 1:
        zygosity = "heterozygous"
    else:
        zygosity = "non_reference"

    ad = []
    for item in (fields.get("AD") or "").split(","):
        try:
            ad.append(int(item))
        except ValueError:
            ad.append(0)
    total_depth = sum(ad)
    allele_depth = ad[allele_number] if allele_number < len(ad) else None
    dp = parse_number(fields.get("DP"))
    gq = parse_number(fields.get("GQ"))
    return {
        "carrier": True,
        "gt": gt,
        "zygosity": zygosity,
        "phased": int("|" in gt),
        "dp": int(dp) if dp is not None else None,
        "gq": gq,
        "allele_balance": (
            allele_depth / total_depth
            if allele_depth is not None and total_depth > 0
            else None
        ),
    }


def parse_csq_entries(raw: str, fields: list[str]) -> list[dict[str, str]]:
    if not raw or not fields:
        return [{}]
    entries = []
    for item in raw.split(","):
        values = item.split("|")
        entries.append({
            field: decode(values[index]) if index < len(values) else ""
            for index, field in enumerate(fields)
        })
    return entries


def annotation_from(record: dict[str, str]) -> dict:
    impact = (first(record, ("IMPACT",)) or "UNKNOWN").upper()
    if impact not in ALLOWED_IMPACTS:
        impact = "UNKNOWN"
    return {
        "gene": (first(record, ("SYMBOL", "HGNC")) or "—").upper(),
        "gene_id": first(record, ("Gene",)),
        "transcript": first(record, ("Feature",)),
        "hgvsc": first(record, ("HGVSc",)),
        "hgvsp": first(record, ("HGVSp",)),
        "consequence": first(record, ("Consequence",)) or "unannotated",
        "impact": impact,
        "gnomad_popmax": maximum(record, (
            "gnomADg_AF_popmax", "gnomADe_AF_popmax", "gnomAD_AF_popmax",
            "gnomAD_popmax_AF", "MAX_AF", "gnomADg_AF", "gnomADe_AF", "gnomAD_AF",
        )),
        "cadd": maximum(record, ("CADD_phred", "CADD_PHRED")),
        "alpha_missense": maximum(record, ("AlphaMissense_score", "am_pathogenicity")),
        "spliceai": maximum(record, (
            "SpliceAI_pred_DS_AG", "SpliceAI_pred_DS_AL",
            "SpliceAI_pred_DS_DG", "SpliceAI_pred_DS_DL",
            "DS_AG", "DS_AL", "DS_DG", "DS_DL",
        )),
        "clinvar": first(record, ("ClinVar_CLNSIG", "CLNSIG")),
        "clinvar_conflicting": first(
            record, ("ClinVar_CLNSIGCONF", "CLNSIGCONF")
        ),
        "loftee": first(record, ("LoF", "LOFTEE")),
        "loftee_50bp": first(record, ("LoF_50_BP_RULE_PTC", "50_BP_RULE_recomputed")),
        "loftee_50bp_original": first(record, ("LoF_50_BP_RULE_original", "50_BP_RULE_original")),
        "loftee_50bp_changed": int(truthy(first(record, ("LoF_50_BP_RULE_changed", "50_BP_RULE_changed")))),
        "ptc_distance": parse_number(first(record, ("PTC_dist_from_last_exon",))),
        "ptc_calc_status": first(record, ("PTC_calc_status",)),
        "mane": int(truthy(first(record, ("MANE_SELECT", "MANE_PLUS_CLINICAL")))),
        "picked": int(truthy(first(record, ("PICK",)))),
        "repeat_masker": int(truthy(first(record, ("RepeatMasker", "REPEATMASKER")))),
        "segdup": int(truthy(first(record, ("SegDup", "SEGDUP")))),
    }


class CohortStore:
    """SQLite-backed VCF carrier index."""

    def __init__(self, database_path: Path):
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._import_jobs: dict[str, dict] = {}
        self._import_jobs_lock = threading.Lock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=60)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=60000")
        connection.execute("PRAGMA synchronous=NORMAL")
        connection.execute("PRAGMA cache_size=-65536")
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
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS cohort_files (
                    id INTEGER PRIMARY KEY,
                    path TEXT NOT NULL UNIQUE,
                    size_bytes INTEGER NOT NULL,
                    mtime_ns INTEGER NOT NULL,
                    imported_at TEXT NOT NULL,
                    sample_count INTEGER NOT NULL DEFAULT 0,
                    pass_records INTEGER NOT NULL DEFAULT 0,
                    excluded_records INTEGER NOT NULL DEFAULT 0,
                    variant_count INTEGER NOT NULL DEFAULT 0,
                    carrier_count INTEGER NOT NULL DEFAULT 0
                    ,assembly TEXT NOT NULL DEFAULT 'GRCh38'
                    ,lifted_from_assembly TEXT
                );

                CREATE TABLE IF NOT EXISTS cohort_samples (
                    id INTEGER PRIMARY KEY,
                    file_id INTEGER NOT NULL REFERENCES cohort_files(id) ON DELETE CASCADE,
                    name TEXT NOT NULL,
                    UNIQUE(file_id, name)
                );

                CREATE TABLE IF NOT EXISTS cohort_variants (
                    id INTEGER PRIMARY KEY,
                    variant_key TEXT NOT NULL UNIQUE,
                    chrom TEXT NOT NULL,
                    pos INTEGER NOT NULL,
                    ref TEXT NOT NULL,
                    alt TEXT NOT NULL,
                    rsid TEXT,
                    original_assembly TEXT,
                    original_chrom TEXT,
                    original_pos INTEGER,
                    original_ref TEXT,
                    original_alt TEXT
                );

                CREATE TABLE IF NOT EXISTS cohort_annotations (
                    id INTEGER PRIMARY KEY,
                    variant_id INTEGER NOT NULL REFERENCES cohort_variants(id) ON DELETE CASCADE,
                    gene TEXT NOT NULL,
                    gene_id TEXT,
                    transcript TEXT,
                    hgvsc TEXT,
                    hgvsp TEXT,
                    consequence TEXT NOT NULL,
                    impact TEXT NOT NULL,
                    gnomad_popmax REAL,
                    cadd REAL,
                    alpha_missense REAL,
                    spliceai REAL,
                    clinvar TEXT,
                    clinvar_conflicting TEXT,
                    loftee TEXT,
                    loftee_50bp TEXT,
                    loftee_50bp_original TEXT,
                    loftee_50bp_changed INTEGER NOT NULL DEFAULT 0,
                    ptc_distance REAL,
                    ptc_calc_status TEXT,
                    mane INTEGER NOT NULL DEFAULT 0,
                    picked INTEGER NOT NULL DEFAULT 0,
                    repeat_masker INTEGER NOT NULL DEFAULT 0,
                    segdup INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(variant_id, gene, transcript, hgvsc, hgvsp, consequence)
                );

                CREATE TABLE IF NOT EXISTS cohort_genotypes (
                    id INTEGER PRIMARY KEY,
                    variant_id INTEGER NOT NULL REFERENCES cohort_variants(id) ON DELETE CASCADE,
                    sample_id INTEGER NOT NULL REFERENCES cohort_samples(id) ON DELETE CASCADE,
                    genotype TEXT NOT NULL,
                    zygosity TEXT NOT NULL,
                    phased INTEGER NOT NULL DEFAULT 0,
                    dp INTEGER,
                    gq REAL,
                    allele_balance REAL,
                    qual REAL,
                    haplotype_frame_status TEXT,
                    haplotype_frame_partners TEXT,
                    haplotype_protein_change TEXT,
                    haplotype_transcript TEXT,
                    UNIQUE(variant_id, sample_id)
                );

                CREATE INDEX IF NOT EXISTS cohort_variants_locus_idx
                    ON cohort_variants(chrom, pos, ref, alt);
                CREATE INDEX IF NOT EXISTS cohort_variants_rsid_idx
                    ON cohort_variants(rsid);
                CREATE INDEX IF NOT EXISTS cohort_variants_rsid_nocase_idx
                    ON cohort_variants(rsid COLLATE NOCASE);
                CREATE INDEX IF NOT EXISTS cohort_annotations_gene_idx
                    ON cohort_annotations(gene, mane, impact);
                CREATE INDEX IF NOT EXISTS cohort_annotations_variant_idx
                    ON cohort_annotations(variant_id);
                CREATE INDEX IF NOT EXISTS cohort_genotypes_variant_idx
                    ON cohort_genotypes(variant_id);
                CREATE INDEX IF NOT EXISTS cohort_genotypes_sample_idx
                    ON cohort_genotypes(sample_id);
                CREATE INDEX IF NOT EXISTS cohort_samples_name_idx
                    ON cohort_samples(name);
                """
            )
            # In-place migration for cohort databases created before PICK was
            # retained in the transcript annotation model.
            annotation_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(cohort_annotations)"
                ).fetchall()
            }
            if "picked" not in annotation_columns:
                connection.execute(
                    "ALTER TABLE cohort_annotations "
                    "ADD COLUMN picked INTEGER NOT NULL DEFAULT 0"
                )
            for column, declaration in (
                ("clinvar_conflicting", "TEXT"),
                ("loftee_50bp", "TEXT"),
                ("loftee_50bp_original", "TEXT"),
                ("loftee_50bp_changed", "INTEGER NOT NULL DEFAULT 0"),
                ("ptc_distance", "REAL"),
                ("ptc_calc_status", "TEXT"),
            ):
                if column not in annotation_columns:
                    connection.execute(
                        f"ALTER TABLE cohort_annotations ADD COLUMN {column} {declaration}"
                    )
            genotype_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(cohort_genotypes)"
                ).fetchall()
            }
            for column in (
                "haplotype_frame_status",
                "haplotype_frame_partners",
                "haplotype_protein_change",
                "haplotype_transcript",
            ):
                if column not in genotype_columns:
                    connection.execute(
                        f"ALTER TABLE cohort_genotypes ADD COLUMN {column} TEXT"
                    )
            file_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(cohort_files)"
                ).fetchall()
            }
            if "assembly" not in file_columns:
                connection.execute(
                    "ALTER TABLE cohort_files "
                    "ADD COLUMN assembly TEXT NOT NULL DEFAULT 'GRCh38'"
                )
            if "lifted_from_assembly" not in file_columns:
                connection.execute(
                    "ALTER TABLE cohort_files ADD COLUMN lifted_from_assembly TEXT"
                )
            variant_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(cohort_variants)"
                ).fetchall()
            }
            for column, declaration in (
                ("original_assembly", "TEXT"),
                ("original_chrom", "TEXT"),
                ("original_pos", "INTEGER"),
                ("original_ref", "TEXT"),
                ("original_alt", "TEXT"),
            ):
                if column not in variant_columns:
                    connection.execute(
                        f"ALTER TABLE cohort_variants ADD COLUMN {column} {declaration}"
                    )
            # Legacy pipeline output used --pick and therefore had one CSQ
            # consequence but no PICK field. Recover that unambiguous fallback
            # without labelling multi-transcript external VCF annotations.
            connection.execute(
                """
                UPDATE cohort_annotations AS candidate
                SET picked = 1
                WHERE candidate.picked = 0
                  AND candidate.mane = 0
                  AND NOT EXISTS (
                    SELECT 1 FROM cohort_annotations AS mane_row
                    WHERE mane_row.variant_id = candidate.variant_id
                      AND mane_row.gene = candidate.gene
                      AND mane_row.mane = 1
                  )
                  AND 1 = (
                    SELECT COUNT(*) FROM cohort_annotations AS sibling
                    WHERE sibling.variant_id = candidate.variant_id
                      AND sibling.gene = candidate.gene
                  )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS cohort_annotations_preferred_idx
                ON cohort_annotations(gene, mane, picked, impact)
                """
            )

    def stats(self) -> dict:
        with self._session() as connection:
            row = connection.execute(
                """
                SELECT
                  (SELECT COUNT(*) FROM cohort_files) AS files,
                  (SELECT COUNT(*) FROM cohort_samples) AS sample_entries,
                  (SELECT COUNT(DISTINCT name) FROM cohort_samples) AS individuals,
                  (SELECT COUNT(*) FROM cohort_variants) AS variants,
                  (SELECT COUNT(*) FROM cohort_genotypes) AS carrier_observations
                """
            ).fetchone()
        return dict(row)

    def expand_paths(self, raw_paths: list[str], recursive: bool = True) -> list[Path]:
        candidates: list[Path] = []
        for raw in raw_paths:
            if not isinstance(raw, str) or not raw.strip():
                continue
            path = Path(raw).expanduser().resolve()
            if path.is_dir():
                iterator = path.rglob("*") if recursive else path.iterdir()
                candidates.extend(item for item in iterator if item.is_file())
            elif path.is_file():
                candidates.append(path)
            else:
                raise ValueError(f"cohort input path does not exist: {path}")
        selected = sorted({
            path for path in candidates
            if path.name.lower().endswith((".vcf", ".vcf.gz"))
        })
        if not selected:
            raise ValueError("no .vcf or .vcf.gz files were found")
        if len(selected) > 10_000:
            raise ValueError("a single cohort import is limited to 10,000 VCF files")
        return selected

    def import_paths(
        self, raw_paths: list[str], recursive: bool = True, force: bool = False,
        allow_unknown_assembly: bool = False,
    ) -> dict:
        paths = self.expand_paths(raw_paths, recursive)
        results = []
        for path in paths:
            try:
                results.append(
                    self.import_vcf(
                        path,
                        force=force,
                        allow_unknown_assembly=allow_unknown_assembly,
                    )
                )
            except Exception as error:
                results.append({
                    "path": str(path),
                    "status": "failed",
                    "error": str(error),
                })
        return {
            "files": results,
            "imported": sum(item["status"] == "imported" for item in results),
            "skipped": sum(item["status"] == "unchanged" for item in results),
            "failed": sum(item["status"] == "failed" for item in results),
            "stats": self.stats(),
        }

    def start_import_paths(
        self, raw_paths: list[str], recursive: bool = True, force: bool = False,
        allow_unknown_assembly: bool = False,
    ) -> dict:
        paths = self.expand_paths(raw_paths, recursive)
        with self._import_jobs_lock:
            if any(
                job["status"] in {"queued", "running"}
                for job in self._import_jobs.values()
            ):
                raise ValueError("another cohort import is already running")
            job_id = uuid.uuid4().hex
            job = {
                "id": job_id,
                "status": "queued",
                "created_at": utc_now(),
                "started_at": None,
                "finished_at": None,
                "total_files": len(paths),
                "completed_files": 0,
                "total_bytes": sum(path.stat().st_size for path in paths),
                "processed_bytes": 0,
                "current_path": "",
                "current_file_bytes": 0,
                "current_file_size": 0,
                "records_processed": 0,
                "pass_records": 0,
                "carrier_count": 0,
                "result": None,
                "error": "",
            }
            self._import_jobs[job_id] = job
            terminal = [
                key for key, value in self._import_jobs.items()
                if value["status"] in {"succeeded", "failed"}
            ]
            for key in terminal[:-20]:
                self._import_jobs.pop(key, None)
        thread = threading.Thread(
            target=self._run_import_job,
            args=(job_id, paths, force, allow_unknown_assembly),
            name=f"cohort-import-{job_id[:8]}",
            daemon=True,
        )
        thread.start()
        return self.get_import_job(job_id)

    def get_import_job(self, job_id: str) -> dict | None:
        with self._import_jobs_lock:
            job = self._import_jobs.get(job_id)
            return dict(job) if job else None

    def _update_import_job(self, job_id: str, **changes) -> None:
        with self._import_jobs_lock:
            if job_id in self._import_jobs:
                self._import_jobs[job_id].update(changes)

    def _run_import_job(
        self, job_id: str, paths: list[Path], force: bool,
        allow_unknown_assembly: bool,
    ) -> None:
        self._update_import_job(job_id, status="running", started_at=utc_now())
        results: list[dict] = []
        completed_bytes = 0
        total_records = 0
        total_pass = 0
        total_carriers = 0
        try:
            for index, path in enumerate(paths):
                file_size = path.stat().st_size
                file_records = 0
                file_pass = 0
                file_carriers = 0
                self._update_import_job(
                    job_id,
                    current_path=str(path),
                    current_file_bytes=0,
                    current_file_size=file_size,
                )

                def progress(update: dict) -> None:
                    nonlocal file_records, file_pass, file_carriers
                    file_records = int(update.get("records_processed", file_records))
                    file_pass = int(update.get("pass_records", file_pass))
                    file_carriers = int(update.get("carrier_count", file_carriers))
                    current_bytes = min(
                        file_size, int(update.get("processed_bytes", 0))
                    )
                    self._update_import_job(
                        job_id,
                        processed_bytes=completed_bytes + current_bytes,
                        current_file_bytes=current_bytes,
                        records_processed=total_records + file_records,
                        pass_records=total_pass + file_pass,
                        carrier_count=total_carriers + file_carriers,
                    )

                try:
                    result = self.import_vcf(
                        path,
                        force=force,
                        allow_unknown_assembly=allow_unknown_assembly,
                        progress=progress,
                    )
                except Exception as error:
                    result = {
                        "path": str(path),
                        "status": "failed",
                        "error": str(error),
                    }
                results.append(result)
                completed_bytes += file_size
                total_records += file_records
                total_pass += int(result.get("pass_records", file_pass) or 0)
                total_carriers += int(result.get("carrier_count", file_carriers) or 0)
                self._update_import_job(
                    job_id,
                    completed_files=index + 1,
                    processed_bytes=completed_bytes,
                    current_file_bytes=file_size,
                    records_processed=total_records,
                    pass_records=total_pass,
                    carrier_count=total_carriers,
                )
            result = {
                "files": results,
                "imported": sum(item["status"] == "imported" for item in results),
                "skipped": sum(item["status"] == "unchanged" for item in results),
                "failed": sum(item["status"] == "failed" for item in results),
                "stats": self.stats(),
            }
            self._update_import_job(
                job_id,
                status="succeeded",
                finished_at=utc_now(),
                current_path="",
                result=result,
            )
        except Exception as error:
            self._update_import_job(
                job_id,
                status="failed",
                finished_at=utc_now(),
                error=str(error),
            )

    def import_vcf(
        self, path: Path, force: bool = False, allow_unknown_assembly: bool = False,
        progress: Callable[[dict], None] | None = None,
    ) -> dict:
        path = path.resolve()
        assembly = detect_vcf_assembly(path)
        if assembly["assembly"] == "conflict":
            raise ValueError("VCF header contains conflicting assembly evidence")
        if assembly["assembly"] == "GRCh37":
            raise ValueError(
                "cohort indexing is GRCh38-only; liftover and re-annotate this GRCh37 VCF first"
            )
        if assembly["assembly"] == "unknown" and not allow_unknown_assembly:
            raise ValueError(
                "VCF assembly is ambiguous; confirm it is GRCh38 to index it"
            )
        stat = path.stat()
        with self._session() as connection:
            existing = connection.execute(
                "SELECT * FROM cohort_files WHERE path = ?", (str(path),)
            ).fetchone()
            if (
                existing and not force
                and existing["size_bytes"] == stat.st_size
                and existing["mtime_ns"] == stat.st_mtime_ns
            ):
                result = dict(existing)
                result.update({"status": "unchanged"})
                if progress:
                    progress({"processed_bytes": stat.st_size})
                return result

        opener = gzip.open if path.name.lower().endswith(".gz") else open
        with self._session() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if existing:
                connection.execute("DELETE FROM cohort_files WHERE id = ?", (existing["id"],))
            file_id = connection.execute(
                """
                INSERT INTO cohort_files(
                  path, size_bytes, mtime_ns, imported_at, assembly,
                  lifted_from_assembly
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    str(path), stat.st_size, stat.st_mtime_ns, utc_now(),
                    "GRCh38",
                    "GRCh37" if assembly["lifted_from_grch37"] else None,
                ),
            ).lastrowid

            samples: list[str] = []
            sample_ids: list[int] = []
            csq_fields: list[str] = []
            saw_fileformat = False
            saw_columns = False
            pass_records = 0
            excluded_records = 0
            carrier_count = 0
            variant_cache: dict[str, int] = {}
            annotation_cache: set[tuple] = set()
            records_processed = 0

            try:
                with opener(path, "rt", encoding="utf-8", errors="replace") as handle:
                    for line in handle:
                        if line.startswith("##fileformat=VCF"):
                            saw_fileformat = True
                            continue
                        if line.startswith("##contig=<"):
                            payload = line.split("<", 1)[1].rsplit(">", 1)[0]
                            values = dict(
                                item.split("=", 1)
                                for item in payload.split(",")
                                if "=" in item
                            )
                            if (
                                normalize_chromosome(values.get("ID", "")) == "1"
                                and values.get("length")
                                and values["length"] != "248956422"
                            ):
                                raise ValueError(
                                    "VCF chromosome 1 length does not match GRCh38 "
                                    f"(expected 248956422, found {values['length']})"
                                )
                            continue
                        if line.startswith("##INFO=<ID=CSQ"):
                            match = re.search(r"Format:\s*([^\">]+)", line, re.IGNORECASE)
                            if match:
                                csq_fields = match.group(1).strip().split("|")
                            continue
                        if line.startswith("#CHROM"):
                            saw_columns = True
                            samples = line.rstrip("\n").split("\t")[9:]
                            if not samples:
                                raise ValueError("cohort VCF must contain at least one sample")
                            sample_ids = [
                                connection.execute(
                                    "INSERT INTO cohort_samples(file_id, name) VALUES (?, ?)",
                                    (file_id, sample),
                                ).lastrowid
                                for sample in samples
                            ]
                            continue
                        if not line.strip() or line.startswith("#"):
                            continue
                        if not saw_columns:
                            raise ValueError("VCF #CHROM header was not found before records")

                        records_processed += 1
                        columns = line.rstrip("\n").split("\t")
                        if len(columns) < 10:
                            continue
                        chrom_raw, pos_raw, rsid, ref, alt_raw, qual_raw, filter_value, raw_info, format_value = columns[:9]
                        if filter_value != "PASS":
                            excluded_records += 1
                            continue
                        pass_records += 1
                        chrom = normalize_chromosome(chrom_raw)
                        pos = int(pos_raw)
                        info = info_map(raw_info)
                        consequences = parse_csq_entries(info.get("CSQ", ""), csq_fields)
                        sample_values = columns[9:]
                        qual = parse_number(qual_raw)

                        for alt_index, alt in enumerate(alt_raw.split(",")):
                            carrier_rows = []
                            for sample_index, sample_id in enumerate(sample_ids):
                                genotype = parse_genotype(
                                    format_value,
                                    sample_values[sample_index] if sample_index < len(sample_values) else "",
                                    alt_index,
                                )
                                if genotype["carrier"]:
                                    carrier_rows.append(
                                        (sample_id, samples[sample_index], genotype)
                                    )
                            if not carrier_rows:
                                continue

                            key = variant_key(chrom, pos, ref, alt)
                            original_alts = info.get("IEI_ORIGINAL_ALT", "").split(",")
                            original = {
                                "assembly": first(info, ("IEI_ORIGINAL_ASSEMBLY",)),
                                "chrom": first(info, ("IEI_ORIGINAL_CHROM",)),
                                "pos": int(info["IEI_ORIGINAL_POS"])
                                if info.get("IEI_ORIGINAL_POS", "").isdigit()
                                else None,
                                "ref": first(info, ("IEI_ORIGINAL_REF",)),
                                "alt": decode(
                                    original_alts[alt_index]
                                    if alt_index < len(original_alts)
                                    else (original_alts[0] if original_alts else "")
                                ),
                            }
                            variant_id = variant_cache.get(key)
                            if variant_id is None:
                                connection.execute(
                                    """
                                    INSERT INTO cohort_variants(
                                      variant_key, chrom, pos, ref, alt, rsid,
                                      original_assembly, original_chrom, original_pos,
                                      original_ref, original_alt
                                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                                    ON CONFLICT(variant_key) DO UPDATE SET
                                      rsid = CASE
                                        WHEN excluded.rsid IS NOT NULL AND excluded.rsid != '.'
                                        THEN excluded.rsid ELSE cohort_variants.rsid END,
                                      original_assembly=COALESCE(
                                        cohort_variants.original_assembly,
                                        excluded.original_assembly
                                      ),
                                      original_chrom=COALESCE(
                                        cohort_variants.original_chrom,
                                        excluded.original_chrom
                                      ),
                                      original_pos=COALESCE(
                                        cohort_variants.original_pos,
                                        excluded.original_pos
                                      ),
                                      original_ref=COALESCE(
                                        cohort_variants.original_ref,
                                        excluded.original_ref
                                      ),
                                      original_alt=COALESCE(
                                        cohort_variants.original_alt,
                                        excluded.original_alt
                                      )
                                    """,
                                    (
                                        key, chrom, pos, ref.upper(), alt.upper(),
                                        None if rsid == "." else rsid,
                                        original["assembly"] or None,
                                        original["chrom"] or None,
                                        original["pos"],
                                        original["ref"] or None,
                                        original["alt"] or None,
                                    ),
                                )
                                variant_id = connection.execute(
                                    "SELECT id FROM cohort_variants WHERE variant_key = ?", (key,)
                                ).fetchone()["id"]
                                if len(variant_cache) >= 100_000:
                                    variant_cache.clear()
                                variant_cache[key] = variant_id

                            matching = []
                            for consequence in consequences:
                                allele_number = consequence.get("ALLELE_NUM", "")
                                if allele_number.isdigit():
                                    if int(allele_number) == alt_index + 1:
                                        matching.append(consequence)
                                elif not consequence.get("Allele") or consequence.get("Allele") == alt:
                                    matching.append(consequence)
                            if not matching:
                                matching = consequences

                            annotations = [
                                annotation_from({**info, **consequence})
                                for consequence in matching
                            ]
                            if "PICK" not in csq_fields:
                                annotations_by_gene: dict[str, list[dict]] = {}
                                for annotation in annotations:
                                    annotations_by_gene.setdefault(
                                        annotation["gene"], []
                                    ).append(annotation)
                                for gene_annotations in annotations_by_gene.values():
                                    if (
                                        len(gene_annotations) == 1
                                        and not gene_annotations[0]["mane"]
                                    ):
                                        gene_annotations[0]["picked"] = 1

                            for annotation in annotations:
                                annotation_key = (
                                    variant_id, annotation["gene"], annotation["transcript"],
                                    annotation["hgvsc"], annotation["hgvsp"], annotation["consequence"],
                                )
                                if annotation_key in annotation_cache:
                                    continue
                                if len(annotation_cache) >= 100_000:
                                    annotation_cache.clear()
                                annotation_cache.add(annotation_key)
                                connection.execute(
                                    """
                                    INSERT INTO cohort_annotations(
                                      variant_id, gene, gene_id, transcript, hgvsc, hgvsp,
                                      consequence, impact, gnomad_popmax, cadd, alpha_missense,
                                      spliceai, clinvar, clinvar_conflicting,
                                      loftee, loftee_50bp,
                                      loftee_50bp_original, loftee_50bp_changed,
                                      ptc_distance, ptc_calc_status, mane, picked,
                                      repeat_masker, segdup
                                    ) VALUES (
                                      :variant_id, :gene, :gene_id, :transcript, :hgvsc, :hgvsp,
                                      :consequence, :impact, :gnomad_popmax, :cadd, :alpha_missense,
                                      :spliceai, :clinvar, :clinvar_conflicting,
                                      :loftee, :loftee_50bp,
                                      :loftee_50bp_original, :loftee_50bp_changed,
                                      :ptc_distance, :ptc_calc_status, :mane, :picked,
                                      :repeat_masker, :segdup
                                    )
                                    ON CONFLICT(
                                      variant_id, gene, transcript, hgvsc, hgvsp, consequence
                                    ) DO UPDATE SET
                                      impact=excluded.impact,
                                      gnomad_popmax=COALESCE(excluded.gnomad_popmax, cohort_annotations.gnomad_popmax),
                                      cadd=COALESCE(excluded.cadd, cohort_annotations.cadd),
                                      alpha_missense=COALESCE(excluded.alpha_missense, cohort_annotations.alpha_missense),
                                      spliceai=COALESCE(excluded.spliceai, cohort_annotations.spliceai),
                                      clinvar=COALESCE(NULLIF(excluded.clinvar, ''), cohort_annotations.clinvar),
                                      clinvar_conflicting=COALESCE(NULLIF(excluded.clinvar_conflicting, ''), cohort_annotations.clinvar_conflicting),
                                      loftee=COALESCE(NULLIF(excluded.loftee, ''), cohort_annotations.loftee),
                                      loftee_50bp=COALESCE(NULLIF(excluded.loftee_50bp, ''), cohort_annotations.loftee_50bp),
                                      loftee_50bp_original=COALESCE(NULLIF(excluded.loftee_50bp_original, ''), cohort_annotations.loftee_50bp_original),
                                      loftee_50bp_changed=MAX(excluded.loftee_50bp_changed, cohort_annotations.loftee_50bp_changed),
                                      ptc_distance=COALESCE(excluded.ptc_distance, cohort_annotations.ptc_distance),
                                      ptc_calc_status=COALESCE(NULLIF(excluded.ptc_calc_status, ''), cohort_annotations.ptc_calc_status),
                                      mane=MAX(excluded.mane, cohort_annotations.mane),
                                      picked=MAX(excluded.picked, cohort_annotations.picked),
                                      repeat_masker=MAX(excluded.repeat_masker, cohort_annotations.repeat_masker),
                                      segdup=MAX(excluded.segdup, cohort_annotations.segdup)
                                    """,
                                    {"variant_id": variant_id, **annotation},
                                )

                            for sample_id, sample_name, genotype in carrier_rows:
                                haplotype = haplotype_frame_evidence(
                                    info.get("IEI_HAPLOTYPE_FRAME", ""),
                                    f"{chrom_raw}:{pos}:{ref}:{alt}",
                                    sample_name,
                                )
                                connection.execute(
                                    """
                                    INSERT INTO cohort_genotypes(
                                      variant_id, sample_id, genotype, zygosity, phased,
                                      dp, gq, allele_balance, qual,
                                      haplotype_frame_status,
                                      haplotype_frame_partners,
                                      haplotype_protein_change,
                                      haplotype_transcript
                                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                                    ON CONFLICT(variant_id, sample_id) DO UPDATE SET
                                      genotype=excluded.genotype,
                                      zygosity=excluded.zygosity,
                                      phased=excluded.phased,
                                      dp=excluded.dp,
                                      gq=excluded.gq,
                                      allele_balance=excluded.allele_balance,
                                      qual=excluded.qual,
                                      haplotype_frame_status=excluded.haplotype_frame_status,
                                      haplotype_frame_partners=excluded.haplotype_frame_partners,
                                      haplotype_protein_change=excluded.haplotype_protein_change,
                                      haplotype_transcript=excluded.haplotype_transcript
                                    """,
                                    (
                                        variant_id, sample_id, genotype["gt"],
                                        genotype["zygosity"], genotype["phased"],
                                        genotype["dp"], genotype["gq"],
                                        genotype["allele_balance"], qual,
                                        haplotype["status"], haplotype["partners"],
                                        haplotype["protein"], haplotype["transcript"],
                                    ),
                                )
                                carrier_count += 1

                        if progress and records_processed % 5000 == 0:
                            try:
                                if path.name.lower().endswith(".gz"):
                                    processed_bytes = handle.buffer.fileobj.tell()
                                else:
                                    processed_bytes = handle.buffer.tell()
                            except (AttributeError, OSError):
                                processed_bytes = 0
                            progress({
                                "processed_bytes": processed_bytes,
                                "records_processed": records_processed,
                                "pass_records": pass_records,
                                "carrier_count": carrier_count,
                            })

                if not saw_fileformat:
                    raise ValueError("file does not begin with a VCF fileformat header")
                if not csq_fields:
                    raise ValueError("VEP CSQ Format header was not found")

                variant_count = connection.execute(
                    """
                    SELECT COUNT(DISTINCT genotype.variant_id)
                    FROM cohort_genotypes AS genotype
                    JOIN cohort_samples AS sample ON sample.id = genotype.sample_id
                    WHERE sample.file_id = ?
                    """,
                    (file_id,),
                ).fetchone()[0]
                connection.execute(
                    """
                    UPDATE cohort_files SET
                      sample_count=?, pass_records=?, excluded_records=?,
                      variant_count=?, carrier_count=?
                    WHERE id=?
                    """,
                    (
                        len(samples), pass_records, excluded_records,
                        variant_count, carrier_count, file_id,
                    ),
                )
                if existing:
                    connection.execute(
                        """
                        DELETE FROM cohort_variants
                        WHERE id NOT IN (SELECT DISTINCT variant_id FROM cohort_genotypes)
                        """
                    )
                connection.commit()
                if progress:
                    progress({
                        "processed_bytes": stat.st_size,
                        "records_processed": records_processed,
                        "pass_records": pass_records,
                        "carrier_count": carrier_count,
                    })
            except Exception:
                connection.rollback()
                raise

        return {
            "id": file_id,
            "path": str(path),
            "status": "imported",
            "sample_count": len(samples),
            "pass_records": pass_records,
            "excluded_records": excluded_records,
            "variant_count": variant_count,
            "carrier_count": carrier_count,
        }

    @staticmethod
    def _parse_variant_query(value: str) -> tuple[str, list] | None:
        cleaned = value.strip().replace(",", "")
        locus = re.fullmatch(
            r"(?:chr)?([A-Za-z0-9]+)[:\-](\d+)[:\-]([A-Za-z*]+)[:>\-]([A-Za-z*]+)",
            cleaned,
            re.IGNORECASE,
        )
        if locus:
            chrom, pos, ref, alt = locus.groups()
            return "v.variant_key = ?", [variant_key(chrom, int(pos), ref, alt)]
        position = re.fullmatch(
            r"(?:chr)?([A-Za-z0-9]+)[:\-](\d+)", cleaned, re.IGNORECASE
        )
        if position:
            chrom, pos = position.groups()
            return "v.chrom = ? AND v.pos = ?", [normalize_chromosome(chrom), int(pos)]
        return None

    def query(self, payload: dict) -> dict:
        mode = str(payload.get("mode") or "variant")
        if mode not in {"variant", "gene"}:
            raise ValueError("mode must be 'variant' or 'gene'")
        limit = max(1, min(int(payload.get("limit") or 500), 5000))
        variant_conditions: list[str] = []
        variant_parameters: list = []
        annotation_conditions: list[str] = []
        annotation_parameters: list = []
        genotype_conditions: list[str] = []
        genotype_parameters: list = []

        if mode == "variant":
            query = str(payload.get("query") or "").strip()
            if not query:
                raise ValueError("an exact variant, locus, or rsID is required")
            parsed = self._parse_variant_query(query)
            if parsed:
                clause, values = parsed
                variant_conditions.append(clause)
                variant_parameters.extend(values)
            else:
                variant_conditions.append(
                    "(v.rsid = ? COLLATE NOCASE OR v.variant_key = ? COLLATE NOCASE)"
                )
                variant_parameters.extend([query, query])
        else:
            gene = str(payload.get("gene") or payload.get("query") or "").strip().upper()
            if not gene:
                raise ValueError("gene is required")
            annotation_conditions.append("a.gene = ?")
            annotation_parameters.append(gene)

            impacts = [
                str(value).upper() for value in payload.get("impacts", ["HIGH", "MODERATE"])
                if str(value).upper() in ALLOWED_IMPACTS
            ]
            if impacts:
                annotation_conditions.append(
                    "a.impact IN (" + ",".join("?" for _ in impacts) + ")"
                )
                annotation_parameters.extend(impacts)
            if payload.get("max_popmax") is not None:
                annotation_conditions.append(
                    "(a.gnomad_popmax IS NULL OR a.gnomad_popmax <= ?)"
                )
                annotation_parameters.append(float(payload["max_popmax"]))
            for key, column in (
                ("min_cadd", "a.cadd"),
                ("min_alpha_missense", "a.alpha_missense"),
                ("min_spliceai", "a.spliceai"),
            ):
                if payload.get(key) is not None:
                    annotation_conditions.append(f"{column} >= ?")
                    annotation_parameters.append(float(payload[key]))
            if payload.get("mane_only", True):
                annotation_conditions.append(
                    "(a.mane = 1 OR (a.picked = 1 AND NOT EXISTS ("
                    "SELECT 1 FROM cohort_annotations preferred_mane "
                    "WHERE preferred_mane.variant_id = a.variant_id "
                    "AND preferred_mane.gene = a.gene "
                    "AND preferred_mane.mane = 1)))"
                )
            if payload.get("exclude_repeat", True):
                annotation_conditions.append("a.repeat_masker = 0")
            if payload.get("exclude_segdup", True):
                annotation_conditions.append("a.segdup = 0")
            if payload.get("clinvar_pathogenic_only", False):
                annotation_conditions.append(
                    "LOWER(a.clinvar) LIKE '%pathogenic%' "
                    "AND LOWER(a.clinvar) NOT LIKE '%conflict%'"
                )
            if payload.get("clinvar_conflict_pathogenic_only", False):
                annotation_conditions.append(
                    "LOWER(a.clinvar) LIKE '%conflict%' "
                    "AND LOWER(COALESCE(a.clinvar_conflicting, '')) "
                    "LIKE '%pathogenic%'"
                )
            if payload.get("exclude_confirmed_frame_restored", False):
                genotype_conditions.append(
                    "COALESCE(g.haplotype_frame_status, '') "
                    "!= 'FRAME_RESTORED_CONFIRMED'"
                )

        zygosity = str(payload.get("zygosity") or "all")
        if zygosity != "all":
            if zygosity not in {"heterozygous", "homozygous", "hemizygous"}:
                raise ValueError("unsupported zygosity filter")
            genotype_conditions.append("g.zygosity = ?")
            genotype_parameters.append(zygosity)

        variant_where = " AND ".join(variant_conditions) if variant_conditions else "1"
        annotation_where = (
            " AND ".join(annotation_conditions) if annotation_conditions else "1"
        )
        genotype_where = " AND ".join(genotype_conditions) if genotype_conditions else "1"
        parameters = (
            variant_parameters + annotation_parameters + genotype_parameters
        )
        partition = "a.variant_id, a.gene" if mode == "gene" else "a.variant_id"
        ranking = f"""
          ROW_NUMBER() OVER (
            PARTITION BY {partition}
            ORDER BY a.mane DESC,
              a.picked DESC,
              CASE a.impact
                WHEN 'HIGH' THEN 1 WHEN 'MODERATE' THEN 2
                WHEN 'LOW' THEN 3 WHEN 'MODIFIER' THEN 4 ELSE 5 END,
              a.id
          ) AS annotation_rank
        """
        base = f"""
          WITH matched_variants AS (
            SELECT v.* FROM cohort_variants v WHERE {variant_where}
          ),
          ranked AS (
            SELECT a.*, {ranking}
            FROM cohort_annotations a
            JOIN matched_variants mv ON mv.id = a.variant_id
            WHERE {annotation_where}
          )
          SELECT
            v.variant_key, v.chrom, v.pos, v.ref, v.alt, v.rsid,
            v.original_assembly, v.original_chrom, v.original_pos,
            v.original_ref, v.original_alt,
            a.gene, a.gene_id, a.transcript, a.hgvsc, a.hgvsp,
            a.consequence, a.impact, a.gnomad_popmax, a.cadd,
            a.alpha_missense, a.spliceai, a.clinvar,
            a.clinvar_conflicting, a.loftee,
            a.loftee_50bp, a.loftee_50bp_original, a.loftee_50bp_changed,
            a.ptc_distance, a.ptc_calc_status,
            a.mane, a.picked, a.repeat_masker, a.segdup,
            s.name AS sample, f.path AS source_path,
            g.genotype, g.zygosity, g.phased, g.dp, g.gq,
            g.allele_balance, g.qual, g.haplotype_frame_status,
            g.haplotype_frame_partners, g.haplotype_protein_change,
            g.haplotype_transcript,
            COUNT(*) OVER (PARTITION BY s.id) AS sample_qualifying_variant_count
          FROM cohort_genotypes g
          JOIN matched_variants v ON v.id = g.variant_id
          JOIN ranked a ON a.variant_id = v.id AND a.annotation_rank = 1
          JOIN cohort_samples s ON s.id = g.sample_id
          JOIN cohort_files f ON f.id = s.file_id
          WHERE {genotype_where}
        """
        gene_order = (
            "sample_qualifying_variant_count DESC, s.name,"
            if mode == "gene" else ""
        )
        ordered = base + f"""
          ORDER BY
            {gene_order}
            CASE a.impact
              WHEN 'HIGH' THEN 1 WHEN 'MODERATE' THEN 2
              WHEN 'LOW' THEN 3 WHEN 'MODIFIER' THEN 4 ELSE 5 END,
            COALESCE(a.gnomad_popmax, -1), v.chrom, v.pos, s.name
          LIMIT ?
        """
        with self._session() as connection:
            rows = [
                self._serialize_query_row(row)
                for row in connection.execute(ordered, (*parameters, limit)).fetchall()
            ]
            totals = connection.execute(
                f"""
                SELECT COUNT(*) AS n,
                       COUNT(DISTINCT sample) AS individuals,
                       COUNT(DISTINCT variant_key) AS variants
                FROM ({base})
                """,
                parameters,
            ).fetchone()
        return {
            "mode": mode,
            "total": totals["n"],
            "limit": limit,
            "truncated": totals["n"] > limit,
            "individuals": totals["individuals"],
            "variants": totals["variants"],
            "rows": rows,
        }

    @staticmethod
    def _serialize_query_row(row: sqlite3.Row) -> dict:
        result = dict(row)
        for key in (
            "mane", "picked", "repeat_masker", "segdup", "phased",
            "loftee_50bp_changed",
        ):
            result[key] = bool(result[key])
        return result
