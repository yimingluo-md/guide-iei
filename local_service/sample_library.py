"""Persistent Sample Library and workstation storage management.

The Sample Library owns compact review VCFs and stable sample/dataset identity.
The cohort tables remain a disposable, rebuildable genotype-first index.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from local_service.cohort_store import CohortStore, read_vcf_header


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(8 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _directory_size(path: Path) -> int:
    if not path.exists():
        return 0
    total = 0
    try:
        for root, _directories, files in os.walk(path, onerror=lambda _error: None):
            for name in files:
                item = Path(root) / name
                try:
                    if not item.is_symlink():
                        total += item.stat().st_size
                except OSError:
                    continue
    except OSError:
        pass
    return total


class SampleLibrary:
    """SQLite metadata plus content-addressed managed review VCFs."""

    def __init__(self, state_dir: Path, cohort: CohortStore, *, workspace_dir: Path | None = None):
        self.state_dir = state_dir.resolve()
        self.database_path = cohort.database_path
        self.cohort = cohort
        self.workspace_dir = (workspace_dir or self.state_dir).resolve()
        self.root = self.state_dir / "sample-library"
        self.files_dir = self.root / "files"
        self.files_dir.mkdir(parents=True, exist_ok=True)
        self._initialize()
        self.cleanup_partials()

    def _stored_state_path(self, path: Path | str | None) -> str | None:
        """Store a data-root path relatively, retaining external provenance paths."""
        if path is None:
            return None
        resolved = Path(path).expanduser().resolve()
        try:
            return resolved.relative_to(self.state_dir).as_posix()
        except ValueError:
            # External provenance paths remain readable, while paths under the
            # selected data root travel with a relocated Sample Library.
            return str(resolved)

    def _stored_original_path(self, path: Path | str | None) -> str | None:
        """Store workspace inputs portably and external provenance absolutely.

        Unlike managed review files, uploads can move with the independently
        configured temporary workspace. Storing them relative to the data root
        makes that relocation ambiguous.
        """
        if path is None:
            return None
        resolved = Path(path).expanduser().resolve()
        try:
            return "@workspace/" + resolved.relative_to(self.workspace_dir).as_posix()
        except ValueError:
            return str(resolved)

    def _original_path(self, value: str | Path | None) -> Path:
        if value is None or not str(value).strip():
            raise ValueError("original VCF path is missing")
        text = str(value)
        if text.startswith("@workspace/"):
            relative = Path(text.removeprefix("@workspace/"))
            resolved = (self.workspace_dir / relative).resolve()
            if resolved != self.workspace_dir and self.workspace_dir not in resolved.parents:
                raise ValueError("original VCF path escapes the selected temporary workspace")
            return resolved
        candidate = Path(text).expanduser()
        if candidate.is_absolute():
            return candidate.resolve()
        # Compatibility with the first portable-path implementation.
        workspace_candidate = (self.workspace_dir / candidate).resolve()
        state_candidate = (self.state_dir / candidate).resolve()
        return workspace_candidate if workspace_candidate.exists() else state_candidate

    def _state_path(self, value: str | Path | None, *, label: str = "path") -> Path:
        if value is None or not str(value).strip():
            raise ValueError(f"{label} is missing")
        candidate = Path(value).expanduser()
        if candidate.is_absolute():
            return candidate.resolve()
        resolved = (self.state_dir / candidate).resolve()
        if resolved != self.state_dir and self.state_dir not in resolved.parents:
            raise ValueError(f"{label} escapes the selected data location")
        return resolved

    def _stored_managed_path(self, path: Path | str | None) -> str | None:
        return self._stored_state_path(path)

    def _managed_path(self, value: str | Path | None) -> Path:
        return self._state_path(value, label="managed review VCF path")

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=60)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=60000")
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
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS library_samples (
                  id TEXT PRIMARY KEY,
                  label TEXT NOT NULL,
                  individual_id TEXT,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS library_datasets (
                  id TEXT PRIMARY KEY,
                  sample_id TEXT NOT NULL REFERENCES library_samples(id) ON DELETE CASCADE,
                  vcf_sample_name TEXT NOT NULL,
                  original_name TEXT NOT NULL,
                  original_path TEXT,
                  original_checksum TEXT,
                  original_size_bytes INTEGER,
                  original_mtime_ns INTEGER,
                  managed_path TEXT NOT NULL,
                  managed_index_path TEXT,
                  managed_checksum TEXT NOT NULL,
                  managed_size_bytes INTEGER NOT NULL,
                  analysis_scope TEXT NOT NULL,
                  index_scope TEXT NOT NULL DEFAULT 'compact',
                  capture_kit TEXT NOT NULL DEFAULT '',
                  target_bed TEXT NOT NULL DEFAULT '',
                  annotation_bundle TEXT NOT NULL DEFAULT '{}',
                  resource_versions TEXT NOT NULL DEFAULT '{}',
                  qc_settings TEXT NOT NULL DEFAULT '{}',
                  prefilter_settings TEXT NOT NULL DEFAULT '{}',
                  retention_routes TEXT NOT NULL DEFAULT '[]',
                  complete_settings TEXT NOT NULL DEFAULT '{}',
                  settings_hash TEXT NOT NULL,
                  profile_label TEXT NOT NULL,
                  source_record_count INTEGER,
                  retained_record_count INTEGER,
                  include_in_cohort INTEGER NOT NULL DEFAULT 1,
                  cohort_file_id INTEGER,
                  status TEXT NOT NULL DEFAULT 'ready',
                  warnings TEXT NOT NULL DEFAULT '[]',
                  imported_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL,
                  UNIQUE(sample_id, managed_checksum, vcf_sample_name, settings_hash)
                );

                CREATE INDEX IF NOT EXISTS library_samples_individual_idx
                  ON library_samples(individual_id);
                CREATE INDEX IF NOT EXISTS library_samples_label_idx
                  ON library_samples(label COLLATE NOCASE);
                CREATE INDEX IF NOT EXISTS library_datasets_sample_idx
                  ON library_datasets(sample_id);
                CREATE INDEX IF NOT EXISTS library_datasets_checksum_idx
                  ON library_datasets(managed_checksum);
                CREATE INDEX IF NOT EXISTS library_datasets_profile_idx
                  ON library_datasets(analysis_scope, index_scope, settings_hash);

                CREATE TABLE IF NOT EXISTS sample_library_meta (
                  key TEXT PRIMARY KEY,
                  value TEXT NOT NULL
                );
                """
            )
            converted = connection.execute(
                "SELECT value FROM sample_library_meta WHERE key='portable_paths_v2'"
            ).fetchone()
            if not converted:
                # Pre-storage-registry installs recorded absolute managed paths.
                # Perform lexical containment in Python: SQL LIKE treats '_' and
                # '%' as wildcards and can select an unrelated sibling path.
                rows = connection.execute(
                    "SELECT id,managed_path,managed_index_path,original_path FROM library_datasets"
                ).fetchall()
                for row in rows:
                    for column in ("managed_path", "managed_index_path"):
                        value = row[column]
                        if not value or not Path(value).is_absolute():
                            continue
                        candidate = Path(os.path.abspath(os.path.expanduser(value)))
                        try:
                            relative = candidate.relative_to(self.state_dir).as_posix()
                        except ValueError:
                            continue
                        connection.execute(
                            f"UPDATE library_datasets SET {column}=? WHERE id=?",
                            (relative, row["id"]),
                        )
                    original = row["original_path"]
                    if original and not Path(original).is_absolute() and not original.startswith("@workspace/"):
                        # Prefer the configured temporary workspace when a
                        # previous migration already copied the upload there.
                        workspace_candidate = (self.workspace_dir / original).resolve()
                        state_candidate = (self.state_dir / original).resolve()
                        absolute = workspace_candidate if workspace_candidate.exists() else state_candidate
                        connection.execute(
                            "UPDATE library_datasets SET original_path=? WHERE id=?",
                            (self._stored_original_path(absolute), row["id"]),
                        )
                connection.execute(
                    "INSERT OR REPLACE INTO sample_library_meta(key,value) VALUES('portable_paths_v2','1')"
                )

    @staticmethod
    def build_profile(
        *, analysis_scope: str, index_scope: str, qc_settings: dict,
        prefilter_settings: dict, retention_routes: list[str],
        annotation_bundle: dict, resource_versions: dict,
        capture_kit: str = "", target_bed: str = "",
        source_record_count: int | None = None,
        retained_record_count: int | None = None,
    ) -> tuple[dict, str, str]:
        settings = {
            "analysis_scope": analysis_scope,
            "index_scope": index_scope,
            "qc_settings": qc_settings or {},
            "prefilter_settings": prefilter_settings or {},
            "retention_routes": retention_routes or [],
            "annotation_bundle": annotation_bundle or {},
            "resource_versions": resource_versions or {},
            "capture_kit": capture_kit.strip(),
            "target_bed": target_bed.strip(),
        }
        settings_hash = hashlib.sha256(_json(settings).encode()).hexdigest()[:16]
        assay = "WGS" if analysis_scope == "whole_genome" else "WES/exome"
        scope = "full" if index_scope == "full" else "candidate"
        parts = [f"{assay} {scope}"]
        max_popmax = (prefilter_settings or {}).get("max_gnomad_popmax")
        if max_popmax is not None:
            parts.append(f"popmax ≤{max_popmax:g} or unavailable")
        routes = retention_routes or []
        if routes:
            parts.append(" + ".join(routes))
        if source_record_count is not None and retained_record_count is not None:
            parts.append(f"{retained_record_count:,} of {source_record_count:,} records retained")
        return settings, settings_hash, " · ".join(parts)

    def import_vcf(self, path: Path, payload: dict) -> dict:
        path = path.resolve()
        if not path.is_file():
            raise ValueError(f"review VCF does not exist: {path}")
        analysis_scope = str(payload.get("analysis_scope") or "exome")
        if analysis_scope not in {"exome", "whole_genome"}:
            raise ValueError("analysis_scope must be exome or whole_genome")
        index_scope = str(payload.get("index_scope") or "compact")
        if index_scope not in {"compact", "full"}:
            raise ValueError("index_scope must be compact or full")
        header = read_vcf_header(path)
        if not header.samples:
            raise ValueError("VCF has no sample columns")

        original_path = str(payload.get("original_path") or path)
        original_name = str(payload.get("original_name") or Path(original_path).name or path.name)
        original_candidate = Path(original_path).expanduser()
        original_checksum = ""
        original_size = None
        original_mtime = None
        if original_candidate.is_file():
            original_candidate = original_candidate.resolve()
            original_path = self._stored_original_path(original_candidate) or str(original_candidate)
            original_checksum = _sha256(original_candidate)
            original_stat = original_candidate.stat()
            original_size = original_stat.st_size
            original_mtime = original_stat.st_mtime_ns

        review_checksum = _sha256(path)
        already_managed = any(
            candidate.is_file()
            for candidate in (
                self.files_dir / f"{review_checksum}.vcf.gz",
                self.files_dir / f"{review_checksum}.vcf",
            )
        )
        managed_path, managed_index, preparation_warning = self.cohort.prepare_managed_vcf(
            path, self.files_dir, review_checksum
        )
        managed_storage_path = self._stored_managed_path(managed_path)
        managed_storage_index = self._stored_managed_path(managed_index)
        managed_stat = managed_path.stat()
        include_in_cohort = bool(payload.get("include_in_cohort", True))
        qc_settings = payload.get("qc_settings") or {}
        prefilter_settings = payload.get("prefilter_settings") or {}
        retention_routes = payload.get("retention_routes") or []
        annotation_bundle = payload.get("annotation_bundle") or {}
        resource_versions = payload.get("resource_versions") or {}
        source_count = payload.get("source_record_count")
        retained_count = payload.get("retained_record_count")
        settings, settings_hash, profile_label = self.build_profile(
            analysis_scope=analysis_scope,
            index_scope=index_scope,
            qc_settings=qc_settings,
            prefilter_settings=prefilter_settings,
            retention_routes=retention_routes,
            annotation_bundle=annotation_bundle,
            resource_versions=resource_versions,
            capture_kit=str(payload.get("capture_kit") or ""),
            target_bed=str(payload.get("target_bed") or ""),
            source_record_count=int(source_count) if source_count is not None else None,
            retained_record_count=int(retained_count) if retained_count is not None else None,
        )
        warnings = [value for value in [preparation_warning] if value]
        now = utc_now()
        datasets = []
        with self._session() as connection:
            for vcf_sample in header.samples:
                existing_dataset = connection.execute(
                    """SELECT d.id,d.sample_id,d.vcf_sample_name,s.individual_id
                       FROM library_datasets d JOIN library_samples s ON s.id=d.sample_id
                       WHERE d.managed_checksum=? AND d.vcf_sample_name=? AND d.settings_hash=?
                       ORDER BY d.imported_at DESC LIMIT 1""",
                    (review_checksum, vcf_sample, settings_hash),
                ).fetchone()
                if existing_dataset:
                    datasets.append(dict(existing_dataset))
                    continue
                individual_id = self._legacy_individual(connection, vcf_sample)
                sample_id = uuid.uuid4().hex
                connection.execute(
                    "INSERT INTO library_samples(id,label,individual_id,created_at,updated_at) VALUES(?,?,?,?,?)",
                    (sample_id, vcf_sample, individual_id, now, now),
                )
                dataset_id = uuid.uuid4().hex
                connection.execute(
                    """
                    INSERT INTO library_datasets(
                      id,sample_id,vcf_sample_name,original_name,original_path,
                      original_checksum,original_size_bytes,original_mtime_ns,
                      managed_path,managed_index_path,managed_checksum,managed_size_bytes,
                      analysis_scope,index_scope,capture_kit,target_bed,annotation_bundle,
                      resource_versions,qc_settings,prefilter_settings,retention_routes,
                      complete_settings,settings_hash,profile_label,source_record_count,
                      retained_record_count,include_in_cohort,status,warnings,imported_at,updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        dataset_id, sample_id, vcf_sample, original_name, original_path,
                        original_checksum or None, original_size, original_mtime,
                        managed_storage_path, managed_storage_index,
                        review_checksum, managed_stat.st_size, analysis_scope, index_scope,
                        str(payload.get("capture_kit") or "").strip(),
                        str(payload.get("target_bed") or "").strip(),
                        _json(annotation_bundle), _json(resource_versions), _json(qc_settings),
                        _json(prefilter_settings), _json(retention_routes), _json(settings),
                        settings_hash, profile_label,
                        int(source_count) if source_count is not None else None,
                        int(retained_count) if retained_count is not None else None,
                        int(include_in_cohort), "ready", _json(warnings), now, now,
                    ),
                )
                datasets.append({
                    "id": dataset_id, "sample_id": sample_id,
                    "vcf_sample_name": vcf_sample, "individual_id": individual_id,
                })

        cohort_result = None
        if include_in_cohort:
            cohort_result = self.cohort.import_vcf(
                managed_path,
                force=False,
                import_profile=(
                    "prefiltered"
                    if analysis_scope == "whole_genome" and index_scope == "compact"
                    else "full"
                ),
                analysis_scope=analysis_scope,
                prefilter_options=prefilter_settings,
                prefilter_metadata={
                    "records_scanned": source_count or 0,
                    "records_retained": retained_count or 0,
                },
            )
            with self._session() as connection:
                connection.execute(
                    "UPDATE library_datasets SET cohort_file_id=?,include_in_cohort=1,updated_at=? WHERE managed_path=?",
                    (cohort_result.get("id"), utc_now(), managed_storage_path),
                )
                connection.execute(
                    """UPDATE cohort_files SET profile_label=?,profile_hash=?,profile_json=?
                       WHERE id=?""",
                    (profile_label, settings_hash, _json(settings), cohort_result.get("id")),
                )
        return {
            "datasets": datasets,
            "managed_path": str(managed_path),
            "deduplicated_file": already_managed,
            "profile_label": profile_label,
            "profile_hash": settings_hash,
            "cohort": cohort_result,
            "warnings": warnings,
        }

    @staticmethod
    def _legacy_individual(connection: sqlite3.Connection, sample_name: str) -> str | None:
        try:
            rows = connection.execute(
                "SELECT individual_id FROM phenotype_sample_links WHERE sample_id=?",
                (sample_name,),
            ).fetchall()
        except sqlite3.OperationalError:
            return None
        return rows[0]["individual_id"] if len(rows) == 1 else None

    def list(self, query: str = "", limit: int = 500) -> list[dict]:
        limit = max(1, min(int(limit), 5000))
        cleaned = query.strip()
        condition = "WHERE s.label LIKE ? OR d.vcf_sample_name LIKE ? OR s.individual_id LIKE ? OR d.original_name LIKE ?" if cleaned else ""
        parameters = ((f"%{cleaned}%",) * 4 + (limit,)) if cleaned else (limit,)
        with self._session() as connection:
            rows = connection.execute(
                f"""
                SELECT d.*,s.label AS sample_label,s.individual_id,s.created_at AS sample_created_at,
                       EXISTS(
                         SELECT 1 FROM cohort_files cf
                         JOIN cohort_samples cs ON cs.file_id=cf.id
                         WHERE cf.id=d.cohort_file_id AND cs.name=d.vcf_sample_name
                       ) AS cohort_index_present
                FROM library_datasets d JOIN library_samples s ON s.id=d.sample_id
                {condition}
                ORDER BY d.imported_at DESC LIMIT ?
                """, parameters,
            ).fetchall()
        return [self._serialize(row) for row in rows]

    def get(self, dataset_id: str) -> dict | None:
        with self._session() as connection:
            row = connection.execute(
                """SELECT d.*,s.label AS sample_label,s.individual_id,
                          EXISTS(
                            SELECT 1 FROM cohort_files cf
                            JOIN cohort_samples cs ON cs.file_id=cf.id
                            WHERE cf.id=d.cohort_file_id AND cs.name=d.vcf_sample_name
                          ) AS cohort_index_present
                   FROM library_datasets d JOIN library_samples s ON s.id=d.sample_id
                   WHERE d.id=?""", (dataset_id,),
            ).fetchone()
        return self._serialize(row) if row else None

    def file(self, dataset_id: str) -> Path:
        record = self.get(dataset_id)
        if not record:
            raise KeyError(dataset_id)
        path = self._managed_path(record["managed_path"])
        if not path.is_file():
            raise FileNotFoundError(f"managed review VCF is missing: {path}")
        return path

    def map_identity(self, dataset_id: str, payload: dict) -> dict:
        mode = str(payload.get("mode") or "unavailable")
        if mode not in {"existing", "create", "unavailable"}:
            raise ValueError("mode must be existing, create, or unavailable")
        with self._session() as connection:
            row = connection.execute(
                """SELECT d.sample_id,s.label,s.individual_id FROM library_datasets d
                   JOIN library_samples s ON s.id=d.sample_id WHERE d.id=?""",
                (dataset_id,),
            ).fetchone()
            if not row:
                raise ValueError("library dataset was not found")
            individual_id = None
            if mode in {"existing", "create"}:
                individual_id = str(payload.get("individual_id") or "").strip()
                if not individual_id:
                    raise ValueError("individual_id is required")
                exists = connection.execute(
                    "SELECT 1 FROM phenotype_individuals WHERE individual_id=?",
                    (individual_id,),
                ).fetchone()
                if mode == "existing" and not exists:
                    raise ValueError("individual does not exist")
                if mode == "create" and not exists:
                    now = utc_now()
                    connection.execute(
                        """INSERT INTO phenotype_individuals(
                          individual_id,sex_at_birth,age_at_evaluation,age_at_evaluation_unit,
                          age_at_onset,age_at_onset_unit,reported_race_json,reported_ethnicity_json,
                          phenotype_summary,present_features_json,absent_features_json,current_diagnosis,
                          notes,source_date,custom_fields_json,source_name,created_at,updated_at
                        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (individual_id,"",None,"",None,"", "[]","[]","","[]","[]","","","","{}","Sample Library",now,now),
                    )
                connection.execute(
                    "INSERT OR IGNORE INTO phenotype_sample_links(individual_id,sample_id) VALUES(?,?)",
                    (individual_id, row["label"]),
                )
                existing_sample = connection.execute(
                    """SELECT id FROM library_samples
                       WHERE label=? AND individual_id=? AND id!=?
                       ORDER BY created_at LIMIT 1""",
                    (row["label"], individual_id, row["sample_id"]),
                ).fetchone()
                if existing_sample:
                    connection.execute(
                        "UPDATE library_datasets SET sample_id=?,updated_at=? WHERE id=?",
                        (existing_sample["id"], utc_now(), dataset_id),
                    )
                    connection.execute(
                        "DELETE FROM library_samples WHERE id=? AND NOT EXISTS(SELECT 1 FROM library_datasets WHERE sample_id=?)",
                        (row["sample_id"], row["sample_id"]),
                    )
                    row = {**dict(row), "sample_id": existing_sample["id"]}
            connection.execute(
                "UPDATE library_samples SET individual_id=?,updated_at=? WHERE id=?",
                (individual_id, utc_now(), row["sample_id"]),
            )
            previous = row["individual_id"]
            if previous and previous != individual_id:
                still_used = connection.execute(
                    """SELECT 1 FROM library_samples
                       WHERE label=? AND individual_id=? AND id!=? LIMIT 1""",
                    (row["label"], previous, row["sample_id"]),
                ).fetchone()
                if not still_used:
                    connection.execute(
                        "DELETE FROM phenotype_sample_links WHERE individual_id=? AND sample_id=?",
                        (previous, row["label"]),
                    )
        return self.get(dataset_id) or {}

    def update_metadata(self, dataset_id: str, payload: dict) -> dict:
        allowed = {"capture_kit", "target_bed"}
        updates = {key: str(payload[key]).strip() for key in allowed if key in payload}
        sample_label = payload.get("sample_label")
        with self._session() as connection:
            row = connection.execute(
                "SELECT sample_id,managed_path FROM library_datasets WHERE id=?", (dataset_id,)
            ).fetchone()
            if not row:
                raise ValueError("library dataset was not found")
            if updates:
                clause = ",".join(f"{key}=?" for key in updates)
                connection.execute(
                    f"UPDATE library_datasets SET {clause},updated_at=? WHERE managed_path=?",
                    (*updates.values(), utc_now(), row["managed_path"]),
                )
            if sample_label is not None:
                label = str(sample_label).strip()
                if not label:
                    raise ValueError("sample label cannot be blank")
                connection.execute(
                    "UPDATE library_samples SET label=?,updated_at=? WHERE id=?",
                    (label, utc_now(), row["sample_id"]),
                )
        record = self.get(dataset_id) or {}
        if updates and record:
            settings, settings_hash, profile_label = self.build_profile(
                analysis_scope=record["analysis_scope"],
                index_scope=record["index_scope"],
                qc_settings=record["qc_settings"],
                prefilter_settings=record["prefilter_settings"],
                retention_routes=record["retention_routes"],
                annotation_bundle=record["annotation_bundle"],
                resource_versions=record["resource_versions"],
                capture_kit=record["capture_kit"], target_bed=record["target_bed"],
                source_record_count=record["source_record_count"],
                retained_record_count=record["retained_record_count"],
            )
            with self._session() as connection:
                connection.execute(
                    """UPDATE library_datasets SET complete_settings=?,settings_hash=?,
                           profile_label=?,updated_at=? WHERE managed_path=?""",
                    (_json(settings), settings_hash, profile_label, utc_now(), self._stored_managed_path(record["managed_path"])),
                )
                if record.get("cohort_file_id"):
                    connection.execute(
                        "UPDATE cohort_files SET profile_label=?,profile_hash=?,profile_json=? WHERE id=?",
                        (profile_label, settings_hash, _json(settings), record["cohort_file_id"]),
                    )
            record = self.get(dataset_id) or {}
        return record

    def reindex(self, dataset_id: str, full_wgs: bool = False) -> dict:
        record = self.get(dataset_id)
        if not record:
            raise ValueError("library dataset was not found")
        if full_wgs:
            if record["analysis_scope"] != "whole_genome":
                raise ValueError("Full WGS indexing is available only for WGS datasets")
            source = self._original_path(record.get("original_path"))
            if not source.is_file():
                raise ValueError("the original full WGS VCF path is not available")
            path = source
            import_profile = "full"
            index_scope = "full"
            options = {}
        else:
            path = self._managed_path(record["managed_path"])
            import_profile = (
                "prefiltered"
                if record["analysis_scope"] == "whole_genome" and record["index_scope"] == "compact"
                else "full"
            )
            index_scope = record["index_scope"]
            options = record["prefilter_settings"]
        result = self.cohort.import_vcf(
            path, force=True, import_profile=import_profile,
            analysis_scope=record["analysis_scope"], prefilter_options=options,
        )
        if full_wgs and record.get("cohort_file_id") != result.get("id"):
            with self._session() as connection:
                previous_samples = [
                    item["id"] for item in connection.execute(
                        "SELECT id FROM cohort_samples WHERE file_id=?",
                        (record.get("cohort_file_id"),),
                    ).fetchall()
                ]
            for start in range(0, len(previous_samples), 5000):
                self.cohort.remove_samples(previous_samples[start:start + 5000])
        profile_settings = record["complete_settings"]
        profile_hash = record["settings_hash"]
        profile_label = record["profile_label"]
        if full_wgs:
            profile_settings, profile_hash, profile_label = self.build_profile(
                analysis_scope="whole_genome", index_scope="full",
                qc_settings=record["qc_settings"], prefilter_settings={},
                retention_routes=["all PASS carrier calls"],
                annotation_bundle=record["annotation_bundle"],
                resource_versions=record["resource_versions"],
                capture_kit=record["capture_kit"], target_bed=record["target_bed"],
            )
        with self._session() as connection:
            identity_column = "original_path" if full_wgs else "managed_path"
            identity_value = (
                self._stored_original_path(record[identity_column])
                if full_wgs else self._stored_state_path(record[identity_column])
            )
            connection.execute(
                f"""UPDATE library_datasets SET cohort_file_id=?,include_in_cohort=1,
                       index_scope=?,complete_settings=?,settings_hash=?,profile_label=?,updated_at=?
                       WHERE {identity_column}=?""",
                (result.get("id"), index_scope, _json(profile_settings), profile_hash,
                 profile_label, utc_now(), identity_value),
            )
            connection.execute(
                "UPDATE cohort_files SET profile_label=?,profile_hash=?,profile_json=? WHERE id=?",
                (profile_label, profile_hash, _json(profile_settings), result.get("id")),
            )
        return {"dataset": self.get(dataset_id), "cohort": result}

    def exclude_from_cohort(self, dataset_id: str) -> dict:
        """Remove the derived carrier entry while retaining the library dataset."""
        record = self.get(dataset_id)
        if not record:
            raise ValueError("library dataset was not found")
        cohort_file_id = record.get("cohort_file_id")
        if cohort_file_id:
            with self._session() as connection:
                samples = [
                    item["id"] for item in connection.execute(
                        "SELECT id FROM cohort_samples WHERE file_id=? AND name=?",
                        (cohort_file_id, record["vcf_sample_name"]),
                    ).fetchall()
                ]
            if samples:
                self.cohort.remove_samples(samples)
        with self._session() as connection:
            connection.execute(
                """UPDATE library_datasets
                   SET include_in_cohort=0,cohort_file_id=NULL,updated_at=?
                   WHERE cohort_file_id=? AND vcf_sample_name=?""",
                (utc_now(), cohort_file_id, record["vcf_sample_name"]),
            )
            connection.execute(
                """UPDATE library_datasets
                   SET include_in_cohort=0,cohort_file_id=NULL,updated_at=?
                   WHERE id=?""",
                (utc_now(), dataset_id),
            )
        return self.get(dataset_id) or {}

    def remove(self, dataset_id: str, remove_managed_file: bool = True) -> dict:
        record = self.get(dataset_id)
        if not record:
            raise ValueError("library dataset was not found")
        cohort_file_id = record.get("cohort_file_id")
        if cohort_file_id:
            with self._session() as connection:
                samples = [
                    item["id"] for item in connection.execute(
                        "SELECT id FROM cohort_samples WHERE file_id=? AND name=?",
                        (cohort_file_id, record["vcf_sample_name"]),
                    ).fetchall()
                ]
            if samples:
                self.cohort.remove_samples(samples)
        with self._session() as connection:
            connection.execute("DELETE FROM library_datasets WHERE id=?", (dataset_id,))
            connection.execute(
                "DELETE FROM library_samples WHERE id=? AND NOT EXISTS(SELECT 1 FROM library_datasets WHERE sample_id=?)",
                (record["sample_id"], record["sample_id"]),
            )
            remaining = connection.execute(
                "SELECT COUNT(*) FROM library_datasets WHERE managed_path=?",
                (self._stored_managed_path(record["managed_path"]),),
            ).fetchone()[0]
        removed_files = []
        if remove_managed_file and remaining == 0:
            for path_value in (record["managed_path"], record.get("managed_index_path")):
                if path_value:
                    path = self._managed_path(path_value)
                    if path.is_file():
                        path.unlink()
                        removed_files.append(str(path))
        return {"removed_dataset": dataset_id, "removed_files": removed_files}

    def phenotype(self, dataset_id: str) -> dict | None:
        record = self.get(dataset_id)
        if not record or not record.get("individual_id"):
            return None
        with self._session() as connection:
            row = connection.execute(
                "SELECT * FROM phenotype_individuals WHERE individual_id=?",
                (record["individual_id"],),
            ).fetchone()
            sample_ids = [
                item["sample_id"] for item in connection.execute(
                    "SELECT sample_id FROM phenotype_sample_links "
                    "WHERE individual_id=? ORDER BY sample_id",
                    (record["individual_id"],),
                ).fetchall()
            ]
        if not row:
            return None
        result = dict(row)
        for key, output, fallback in (
            ("reported_race_json", "reported_race", []),
            ("reported_ethnicity_json", "reported_ethnicity", []),
            ("present_features_json", "present_features", []),
            ("absent_features_json", "absent_features", []),
            ("custom_fields_json", "custom_fields", {}),
        ):
            try:
                value = json.loads(result.pop(key) or _json(fallback))
            except (json.JSONDecodeError, TypeError):
                value = fallback
            expected = dict if isinstance(fallback, dict) else list
            result[output] = value if isinstance(value, expected) else fallback
        result["sample_ids"] = sample_ids
        return result

    def profiles(self) -> list[dict]:
        with self._session() as connection:
            rows = connection.execute(
                """SELECT settings_hash,profile_label,analysis_scope,index_scope,
                          complete_settings,COUNT(*) AS datasets
                   FROM library_datasets GROUP BY settings_hash
                   ORDER BY datasets DESC,profile_label"""
            ).fetchall()
        return [self._serialize(row) for row in rows]

    def storage_stats(self) -> dict:
        with self._session() as connection:
            page_size = connection.execute("PRAGMA page_size").fetchone()[0]
            page_count = connection.execute("PRAGMA page_count").fetchone()[0]
            freelist = connection.execute("PRAGMA freelist_count").fetchone()[0]
            managed_unique = connection.execute(
                "SELECT COUNT(DISTINCT managed_checksum) FROM library_datasets"
            ).fetchone()[0]
            datasets = connection.execute("SELECT COUNT(*) FROM library_datasets").fetchone()[0]
        locations = {
            "database": self.database_path.stat().st_size if self.database_path.exists() else 0,
            "managed_library": _directory_size(self.root),
            "uploads": _directory_size(self.workspace_dir / "uploads"),
            "cohort_cache": _directory_size(self.cohort.prepared_dir),
            "wgs_review_cache": _directory_size(self.workspace_dir / "wgs-review-cache"),
            "logs": _directory_size(self.state_dir / "logs") + _directory_size(self.state_dir / "resource-logs"),
        }
        state_total = _directory_size(self.state_dir)
        workspace_within_state = self.workspace_dir == self.state_dir or self.state_dir in self.workspace_dir.parents
        state_categories = locations["database"] + locations["managed_library"] + locations["logs"]
        if workspace_within_state:
            state_categories += locations["uploads"] + locations["cohort_cache"] + locations["wgs_review_cache"]
        locations["other"] = max(0, state_total - state_categories)
        workspace_extra = 0 if workspace_within_state else _directory_size(self.workspace_dir)
        return {
            "state_dir": str(self.state_dir), "workspace_dir": str(self.workspace_dir), "locations": locations,
            "total_bytes": state_total + workspace_extra, "datasets": datasets,
            "managed_unique_files": managed_unique,
            "database_page_bytes": page_size * page_count,
            "database_reclaimable_bytes": page_size * freelist,
        }

    def cleanup(self, categories: list[str]) -> dict:
        allowed = {"uploads", "cohort_cache", "wgs_review_cache", "partials", "configs"}
        requested = set(categories)
        if not requested or not requested <= allowed:
            raise ValueError("unsupported cleanup category")
        if self.cohort.has_active_import():
            raise ValueError("wait for the active cohort import before cleaning caches")
        before = self.storage_stats()
        removed = []
        referenced = set()
        with self._session() as connection:
            for row in connection.execute("SELECT managed_path,managed_index_path FROM library_datasets"):
                referenced.update(str(self._managed_path(value)) for value in row if value)
        roots = {
            "uploads": self.workspace_dir / "uploads",
            "cohort_cache": self.cohort.prepared_dir,
            "wgs_review_cache": self.workspace_dir / "wgs-review-cache",
            "configs": self.state_dir / "job-configs",
        }
        selected_roots = [(category, roots[category]) for category in requested - {"partials"}]
        if "configs" in requested:
            selected_roots.append(("configs", self.state_dir / "resource-configs"))
        for _category, root in selected_roots:
            if root.exists():
                for path in sorted(root.rglob("*"), reverse=True):
                    if path.is_file() and str(path) not in referenced:
                        path.unlink()
                        removed.append(str(path))
                    elif path.is_dir():
                        try:
                            path.rmdir()
                        except OSError:
                            pass
        if "partials" in requested:
            removed.extend(self.cleanup_partials())
        after = self.storage_stats()
        return {"removed_files": len(removed), "freed_bytes": before["total_bytes"] - after["total_bytes"], "storage": after}

    def cleanup_partials(self) -> list[str]:
        removed = []
        for root in (self.root, self.cohort.staging_dir):
            if not root.exists():
                continue
            for path in root.rglob("*.partial"):
                if path.is_file():
                    path.unlink()
                    removed.append(str(path))
        return removed

    def compact_database(self) -> dict:
        if self.cohort.has_active_import():
            raise ValueError("wait for the active cohort import before compacting SQLite")
        with self._session() as connection:
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            connection.execute("VACUUM")
        return self.storage_stats()

    def _serialize(self, row: sqlite3.Row) -> dict:
        result = dict(row)
        for key in ("managed_path", "managed_index_path"):
            if result.get(key):
                result[key] = str(self._managed_path(result[key]))
        if result.get("original_path") and not Path(result["original_path"]).is_absolute():
            result["original_path"] = str(
                self._original_path(result["original_path"])
            )
        for key, fallback in (
            ("annotation_bundle", {}), ("resource_versions", {}),
            ("qc_settings", {}), ("prefilter_settings", {}),
            ("retention_routes", []), ("complete_settings", {}),
            ("warnings", []),
        ):
            if key in result:
                try:
                    result[key] = json.loads(result[key] or _json(fallback))
                except (json.JSONDecodeError, TypeError):
                    result[key] = fallback
        if "include_in_cohort" in result:
            result["include_in_cohort"] = bool(result["include_in_cohort"])
            index_present = bool(result.pop("cohort_index_present", False))
            if not result["include_in_cohort"]:
                result["cohort_index_status"] = "not_included"
            elif index_present:
                result["cohort_index_status"] = "ready"
            else:
                result["cohort_index_status"] = "needs_repair"
        return result
