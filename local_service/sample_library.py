"""Persistent Sample Library and workstation storage management.

The Sample Library owns compact review VCFs and stable sample/dataset identity.
The cohort tables remain a disposable, rebuildable genotype-first index.
"""

from __future__ import annotations

import hashlib
import gzip
import json
import os
import sqlite3
import threading
import time
import uuid
import zlib
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from local_service.errors import NotFoundError
from local_service.cohort_store import (
    CohortStore,
    normalize_chromosome,
    read_vcf_header,
    read_vcf_header_lines,
    variant_key as canonical_variant_key,
)


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
        self._import_lock = threading.RLock()
        self._checksum_cache: dict[tuple[str, int, int], str] = {}
        self._fingerprint_cache: dict[str, str] = {}
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
                  callset_id TEXT NOT NULL DEFAULT '',
                  callset_fingerprint TEXT NOT NULL DEFAULT '',
                  version_id TEXT NOT NULL DEFAULT '',
                  version_number INTEGER NOT NULL DEFAULT 1,
                  is_current INTEGER NOT NULL DEFAULT 1,
                  cohort_preferred INTEGER NOT NULL DEFAULT 0,
                  supersedes_version_id TEXT,
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
        self._run_migrations()

    # --- versioned, transactional schema migrations (audit M30) -------------
    # Each step runs inside ONE explicit transaction together with the marker
    # that records it in sample_library_meta, so an interruption (crash,
    # kill, power loss) rolls the whole step back and the next open runs it
    # again from the start. The previous column-presence logic ran ALTER TABLE
    # in autocommit and the backfill in a later transaction: a kill in between
    # left the new columns without their backfill, and the next open — seeing
    # the columns — never backfilled them (include_in_cohort=1 rows kept
    # cohort_preferred=0; version ids were assigned by the wrong rule).
    LIBRARY_MIGRATIONS: tuple[tuple[str, str], ...] = (
        ("library_versions_v1", "_migrate_library_versions"),
        ("library_current_uniqueness_v1", "_migrate_current_uniqueness"),
        ("portable_paths_v2", "_migrate_portable_paths"),
    )

    def _run_migrations(self) -> None:
        connection = sqlite3.connect(self.database_path, timeout=60, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=60000")
        try:
            for key, method_name in self.LIBRARY_MIGRATIONS:
                done = connection.execute(
                    "SELECT value FROM sample_library_meta WHERE key=?", (key,)
                ).fetchone()
                # A recorded step is trusted unless the schema visibly
                # contradicts it (columns or indexes missing, rows never
                # backfilled): then it is re-applied, so a database restored
                # from a partial copy or an older tool still converges.
                if done and not self._migration_needed(key, connection):
                    continue
                connection.execute("BEGIN IMMEDIATE")
                try:
                    getattr(self, method_name)(connection)
                    connection.execute(
                        "INSERT OR REPLACE INTO sample_library_meta(key,value) VALUES(?,?)",
                        (key, utc_now()),
                    )
                    connection.execute("COMMIT")
                except BaseException:
                    connection.execute("ROLLBACK")
                    raise
            connection.execute(
                "INSERT OR REPLACE INTO sample_library_meta(key,value) VALUES('library_schema_version',?)",
                (self.LIBRARY_MIGRATIONS[-1][0],),
            )
        finally:
            connection.close()

    @staticmethod
    def _migration_needed(key: str, connection: sqlite3.Connection) -> bool:
        if key == "library_versions_v1":
            columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(library_datasets)")
            }
            required = {
                "callset_id", "callset_fingerprint", "version_id", "version_number",
                "is_current", "cohort_preferred", "supersedes_version_id",
            }
            if not required <= columns:
                return True
            return bool(connection.execute(
                "SELECT 1 FROM library_datasets WHERE version_id='' LIMIT 1"
            ).fetchone())
        if key == "library_current_uniqueness_v1":
            indexes = {
                row["name"] for row in connection.execute("PRAGMA index_list(library_datasets)")
            }
            return not {
                "library_datasets_current_content_idx",
                "library_datasets_current_callset_idx",
                "library_datasets_callset_idx",
            } <= indexes
        return False

    def migration_status(self) -> dict:
        with self._session() as connection:
            applied = {
                row["key"]: row["value"] for row in connection.execute(
                    "SELECT key,value FROM sample_library_meta"
                )
            }
        return {
            "schema_version": applied.get("library_schema_version"),
            "migrations": [
                {"id": key, "applied_at": applied.get(key)} for key, _method in self.LIBRARY_MIGRATIONS
            ],
        }

    def _migrate_library_versions(self, connection: sqlite3.Connection) -> None:
        dataset_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(library_datasets)").fetchall()
        }
        migrations = {
            "callset_id": "TEXT NOT NULL DEFAULT ''",
            "callset_fingerprint": "TEXT NOT NULL DEFAULT ''",
            "version_id": "TEXT NOT NULL DEFAULT ''",
            "version_number": "INTEGER NOT NULL DEFAULT 1",
            "is_current": "INTEGER NOT NULL DEFAULT 1",
            "cohort_preferred": "INTEGER NOT NULL DEFAULT 0",
            "supersedes_version_id": "TEXT",
        }
        added = set()
        for column, declaration in migrations.items():
            if column not in dataset_columns:
                connection.execute(
                    f"ALTER TABLE library_datasets ADD COLUMN {column} {declaration}"
                )
                added.add(column)
        # Existing rows each represent the first known version of their
        # content-addressed callset. Fingerprints are backfilled lazily
        # only when an overlapping import is inspected; opening a large
        # existing library must never scan every VCF at startup.
        connection.execute(
            "UPDATE library_datasets SET callset_id=managed_checksum "
            "WHERE callset_id=''"
        )
        # Rows still carrying version_id='' were never backfilled: either the
        # columns were added just now, or an earlier unversioned run added
        # them and died before its backfill committed. Both cases get the
        # same backfill, restricted to those rows.
        unversioned = connection.execute(
            "SELECT COUNT(*) FROM library_datasets WHERE version_id=''"
        ).fetchone()[0]
        if unversioned:
            # Capture the user's former include choice before historical
            # versions have their live cohort linkage cleared below.
            connection.execute(
                "UPDATE library_datasets SET cohort_preferred=include_in_cohort "
                "WHERE version_id=''"
            )
            # A legacy library may contain the same managed VCF more than
            # once under different import settings. Treat each old import
            # batch as a preserved version, rather than assigning every
            # row the same version ID and mixing historical samples into
            # the active UI group. Rows written by one old import share
            # checksum and timestamp; settings are intentionally excluded
            # because a later per-sample metadata edit can change only one
            # sibling's profile hash.
            legacy_groups = connection.execute(
                """SELECT DISTINCT callset_id,managed_checksum,imported_at
                   FROM library_datasets WHERE version_id=''
                   ORDER BY callset_id,imported_at"""
            ).fetchall()
            by_callset: dict[str, list[sqlite3.Row]] = {}
            for row in legacy_groups:
                by_callset.setdefault(row["callset_id"], []).append(row)
            for callset_id, groups in by_callset.items():
                versioned = connection.execute(
                    """SELECT version_id,version_number FROM library_datasets
                       WHERE callset_id=? AND version_id!=''
                       ORDER BY version_number DESC LIMIT 1""",
                    (callset_id,),
                ).fetchone()
                previous_version_id = versioned["version_id"] if versioned else None
                base_number = int(versioned["version_number"]) if versioned else 0
                if versioned:
                    connection.execute(
                        "UPDATE library_datasets SET is_current=0,include_in_cohort=0,cohort_file_id=NULL "
                        "WHERE callset_id=? AND version_id!=''",
                        (callset_id,),
                    )
                for offset, group in enumerate(groups, start=1):
                    number = base_number + offset
                    version_id = hashlib.sha256(
                        (
                            "legacy-library-version\0"
                            f"{callset_id}\0{group['managed_checksum']}\0"
                            f"{group['imported_at']}"
                        ).encode("utf-8")
                    ).hexdigest()
                    is_current = offset == len(groups)
                    connection.execute(
                        """UPDATE library_datasets
                           SET version_id=?,version_number=?,is_current=?,
                               include_in_cohort=CASE WHEN ? THEN include_in_cohort ELSE 0 END,
                               cohort_file_id=CASE WHEN ? THEN cohort_file_id ELSE NULL END,
                               supersedes_version_id=?
                           WHERE callset_id=? AND managed_checksum=?
                             AND imported_at=? AND version_id=''""",
                        (
                            version_id, number, int(is_current), int(is_current),
                            int(is_current), previous_version_id, callset_id,
                            group["managed_checksum"], group["imported_at"],
                        ),
                    )
                    previous_version_id = version_id

    def _migrate_current_uniqueness(self, connection: sqlite3.Connection) -> None:
        # The old UNIQUE constraint included the freshly generated
        # sample_id and therefore could not stop two concurrent imports
        # from creating the same logical row. Preserve any legacy rows,
        # but mark all except the newest exact copy as historical before
        # installing effective partial uniqueness guards.
        duplicate_groups = connection.execute(
            """SELECT managed_checksum,vcf_sample_name
               FROM library_datasets WHERE is_current=1
               GROUP BY managed_checksum,vcf_sample_name HAVING COUNT(*)>1"""
        ).fetchall()
        for group in duplicate_groups:
            copies = connection.execute(
                """SELECT id FROM library_datasets
                   WHERE managed_checksum=? AND vcf_sample_name=? AND is_current=1
                   ORDER BY imported_at DESC,id DESC""",
                (group["managed_checksum"], group["vcf_sample_name"]),
            ).fetchall()
            connection.executemany(
                "UPDATE library_datasets SET is_current=0,include_in_cohort=0,cohort_file_id=NULL WHERE id=?",
                [(row["id"],) for row in copies[1:]],
            )
        connection.execute(
            """CREATE UNIQUE INDEX IF NOT EXISTS library_datasets_current_content_idx
               ON library_datasets(managed_checksum,vcf_sample_name)
               WHERE is_current=1"""
        )
        connection.execute(
            """CREATE UNIQUE INDEX IF NOT EXISTS library_datasets_current_callset_idx
               ON library_datasets(callset_id,vcf_sample_name)
               WHERE is_current=1"""
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS library_datasets_callset_idx "
            "ON library_datasets(callset_id,is_current,version_number)"
        )

    def _migrate_portable_paths(self, connection: sqlite3.Connection) -> None:
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

    @staticmethod
    def _genotype_fingerprint(path: Path) -> str:
        """Hash the biological callset while ignoring annotation-only fields.

        VEP and GUIDE-IEI post-processing may change VCF metadata, ID and INFO
        while leaving the called alleles and genotypes untouched. Hashing the
        coordinate/allele/QC columns, FORMAT and samples recognizes that case
        without conflating a genuinely changed callset. The complete byte
        checksum remains the stronger exact-file identity.
        """
        digest = hashlib.sha256()
        digest.update(b"GUIDE-IEI genotype callset fingerprint v1\n")
        opener = gzip.open if path.name.lower().endswith((".gz", ".bgz")) else open
        chromosome_header = False
        with opener(path, "rb") as handle:
            for raw in handle:
                line = raw.rstrip(b"\r\n")
                if line.startswith(b"##"):
                    continue
                if line.startswith(b"#CHROM\t"):
                    fields = line.split(b"\t")
                    if len(fields) < 10:
                        raise ValueError("VCF header has no sample columns")
                    digest.update(b"samples\0" + b"\0".join(fields[9:]) + b"\n")
                    chromosome_header = True
                    continue
                if line.startswith(b"#") or not line:
                    continue
                if not chromosome_header:
                    raise ValueError("VCF data appeared before the #CHROM header")
                fields = line.split(b"\t")
                if len(fields) < 10:
                    raise ValueError("VCF record has no FORMAT/sample columns")
                # Exclude ID (which may be populated from a reference) and
                # INFO (where predictors live). Include QUAL/FILTER and every
                # genotype field so a variant-calling or sample change yields
                # a different callset fingerprint.
                retained = (
                    fields[0], fields[1], fields[3], fields[4], fields[5],
                    fields[6], *fields[8:],
                )
                digest.update(b"\0".join(retained) + b"\n")
        if not chromosome_header:
            raise ValueError("VCF header is incomplete")
        return digest.hexdigest()

    def _fingerprint(self, path: Path, checksum: str) -> str:
        cached = self._fingerprint_cache.get(checksum)
        if cached:
            return cached
        value = self._genotype_fingerprint(path)
        # Keep the cache bounded; staged uploads are intentionally ephemeral.
        if len(self._fingerprint_cache) >= 32:
            self._fingerprint_cache.pop(next(iter(self._fingerprint_cache)))
        self._fingerprint_cache[checksum] = value
        return value

    def _checksum(self, path: Path) -> str:
        """Reuse the immediately preceding inspection hash when safe.

        Browser intake deliberately inspects identity before importing. A
        size/mtime-keyed cache avoids rereading a large VCF merely to obtain
        the same digest. Import still performs an uncached final hash after
        publishing the managed copy, so a source changed between those steps
        is rejected rather than trusted from metadata alone.
        """
        stat = path.stat()
        key = (str(path.resolve()), stat.st_size, stat.st_mtime_ns)
        cached = self._checksum_cache.get(key)
        if cached:
            return cached
        value = _sha256(path)
        if len(self._checksum_cache) >= 32:
            self._checksum_cache.pop(next(iter(self._checksum_cache)))
        self._checksum_cache[key] = value
        return value

    def _current_callsets(
        self, connection: sqlite3.Connection, analysis_scope: str,
    ) -> list[dict]:
        rows = connection.execute(
            """SELECT d.callset_id,d.callset_fingerprint,d.version_id,
                      d.version_number,d.managed_path,d.managed_checksum,
                      d.original_name,d.imported_at,d.vcf_sample_name
               FROM library_datasets d
               WHERE d.is_current=1 AND d.analysis_scope=?
               ORDER BY d.imported_at DESC,d.id""",
            (analysis_scope,),
        ).fetchall()
        grouped: dict[str, dict] = {}
        for row in rows:
            group = grouped.setdefault(row["callset_id"], {
                "callset_id": row["callset_id"],
                "callset_fingerprint": row["callset_fingerprint"] or "",
                "version_id": row["version_id"],
                "version_number": int(row["version_number"] or 1),
                "managed_path": row["managed_path"],
                "managed_checksum": row["managed_checksum"],
                "original_name": row["original_name"],
                "imported_at": row["imported_at"],
                "samples": [],
            })
            group["samples"].append(row["vcf_sample_name"])
        return list(grouped.values())

    def _backfill_callset_fingerprint(
        self, connection: sqlite3.Connection, group: dict,
    ) -> str:
        fingerprint = str(group.get("callset_fingerprint") or "")
        if fingerprint:
            return fingerprint
        try:
            managed = self._managed_path(group["managed_path"])
            checksum = str(group.get("managed_checksum") or _sha256(managed))
            fingerprint = self._fingerprint(managed, checksum)
        except (OSError, EOFError, ValueError, zlib.error):
            return ""
        connection.execute(
            "UPDATE library_datasets SET callset_fingerprint=? WHERE callset_id=?",
            (fingerprint, group["callset_id"]),
        )
        group["callset_fingerprint"] = fingerprint
        return fingerprint

    def _inspect_identity(
        self, path: Path, *, analysis_scope: str,
        checksum: str | None = None, header=None,
    ) -> dict:
        header = header or read_vcf_header(path)
        checksum = checksum or self._checksum(path)
        samples = list(header.samples)
        with self._session() as connection:
            exact_rows = connection.execute(
                """SELECT d.id,d.callset_id,d.version_id,d.version_number,
                          d.is_current,d.imported_at,d.original_name,d.vcf_sample_name
                   FROM library_datasets d
                   WHERE d.managed_checksum=?
                   ORDER BY d.is_current DESC,d.imported_at DESC""",
                (checksum,),
            ).fetchall()
            exact_by_sample: dict[str, dict] = {}
            for row in exact_rows:
                exact_by_sample.setdefault(row["vcf_sample_name"], dict(row))
            if set(exact_by_sample) == set(samples):
                exact = list(exact_by_sample.values())
                return {
                    "status": "exact_current" if all(row["is_current"] for row in exact) else "exact_previous",
                    "checksum": checksum,
                    "samples": samples,
                    "sample_count": len(samples),
                    "existing_datasets": exact,
                    "matches": [],
                }

            fingerprint = self._fingerprint(path, checksum)
            incoming = set(samples)
            groups = self._current_callsets(connection, analysis_scope)
            possible = []
            for group in groups:
                present = set(group["samples"])
                overlap = sorted(incoming & present)
                if not overlap:
                    continue
                existing_fingerprint = self._backfill_callset_fingerprint(
                    connection, group
                )
                summary = {
                    "callset_id": group["callset_id"],
                    "version_id": group["version_id"],
                    "version_number": group["version_number"],
                    "original_name": group["original_name"],
                    "imported_at": group["imported_at"],
                    "sample_count": len(present),
                    "matching_samples": overlap[:20],
                    "matching_sample_count": len(overlap),
                    "same_sample_set": present == incoming,
                }
                if existing_fingerprint and existing_fingerprint == fingerprint:
                    return {
                        "status": "reannotation",
                        "checksum": checksum,
                        "callset_fingerprint": fingerprint,
                        "samples": samples,
                        "sample_count": len(samples),
                        "match": summary,
                        "matches": [summary],
                    }
                possible.append(summary)
            possible.sort(
                key=lambda value: (
                    not value["same_sample_set"],
                    -value["matching_sample_count"],
                    value["original_name"],
                )
            )
            return {
                "status": "possible_update" if possible else "new",
                "checksum": checksum,
                "callset_fingerprint": fingerprint,
                "samples": samples,
                "sample_count": len(samples),
                "matches": possible[:10],
            }

    def inspect_vcf(self, path: Path, payload: dict) -> dict:
        path = path.expanduser().resolve()
        if not path.is_file():
            raise ValueError(f"review VCF does not exist: {path}")
        analysis_scope = str(payload.get("analysis_scope") or "exome")
        if analysis_scope not in {"exome", "whole_genome"}:
            raise ValueError("analysis_scope must be exome or whole_genome")
        return self._inspect_identity(path, analysis_scope=analysis_scope)

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

    def _remove_cohort_records(self, records: list[dict]) -> None:
        """Remove superseded sample entries from the rebuildable cohort index."""
        with self._session() as connection:
            sample_ids: set[int] = set()
            for record in records:
                if not record.get("cohort_file_id"):
                    continue
                sample_ids.update(
                    row["id"] for row in connection.execute(
                        "SELECT id FROM cohort_samples WHERE file_id=? AND name=?",
                        (record["cohort_file_id"], record["vcf_sample_name"]),
                    ).fetchall()
                )
        ordered = sorted(sample_ids)
        for start in range(0, len(ordered), 5_000):
            self.cohort.remove_samples(ordered[start:start + 5_000])

    def import_vcf(self, path: Path, payload: dict) -> dict:
        # Identity inspection, version switching, and row creation must be one
        # process-local critical section. The database partial indexes are the
        # final guard, but the lock also lets a concurrent importer receive the
        # already-created IDs instead of an IntegrityError.
        with self._import_lock:
            return self._import_vcf_locked(path, payload)

    def _import_vcf_locked(self, path: Path, payload: dict) -> dict:
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
            original_checksum = self._checksum(original_candidate)
            original_stat = original_candidate.stat()
            original_size = original_stat.st_size
            original_mtime = original_stat.st_mtime_ns

        review_checksum = self._checksum(path)
        inspection = self._inspect_identity(
            path, analysis_scope=analysis_scope,
            checksum=review_checksum, header=header,
        )
        identity_status = inspection["status"]
        identity_action = str(payload.get("identity_action") or "")
        replace_callset_id = str(payload.get("replace_callset_id") or "")
        if identity_status == "possible_update":
            allowed = {item["callset_id"] for item in inspection["matches"]}
            if identity_action == "replace" and replace_callset_id in allowed:
                callset_id = replace_callset_id
                import_outcome = "updated_version"
            elif identity_action == "separate":
                callset_id = uuid.uuid4().hex
                import_outcome = "separate_dataset"
            else:
                raise ValueError(
                    "this VCF shares sample names with an existing dataset but "
                    "its callset differs; choose Replace current version or "
                    "Keep as a separate specimen/dataset"
                )
        elif identity_status == "reannotation":
            callset_id = inspection["match"]["callset_id"]
            import_outcome = "updated_annotation"
        elif identity_status.startswith("exact_"):
            callset_id = inspection["existing_datasets"][0]["callset_id"]
            import_outcome = identity_status
        else:
            callset_id = uuid.uuid4().hex
            import_outcome = "new"
        callset_fingerprint = str(inspection.get("callset_fingerprint") or "")
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
        # The checksum above and the managed copy are two independent reads
        # of the source. If the file changed in between (still being copied
        # from a sequencer share, concurrent write), the managed bytes would
        # be stored under the wrong content key — so require stability and
        # refuse the import otherwise, removing a copy made from the moving
        # file unless identical content was already managed beforehand.
        if _sha256(path) != review_checksum:
            if not already_managed:
                Path(managed_path).unlink(missing_ok=True)
                if managed_index:
                    Path(managed_index).unlink(missing_ok=True)
            raise ValueError(
                f"{path.name} changed while it was being imported — wait for "
                "the file to finish copying, then import it again"
            )
        # The sample names were read BEFORE the first checksum, outside the
        # stability window the guard above covers: a swap in that gap passes
        # both checksum reads while the dataset rows would carry the old
        # names over the new content — permanently, since managed_checksum
        # then vouches for the wrong-named file. Bind the names to the exact
        # bytes the datasets will reference by re-reading them from the
        # managed copy.
        try:
            managed_header = read_vcf_header(Path(managed_path))
        except (OSError, EOFError, ValueError, zlib.error):
            # A managed copy that cannot even parse is the same refusal —
            # an unparseable swap must not escape as a raw error that skips
            # the cleanup below.
            managed_header = None
        if managed_header is None or managed_header.samples != header.samples:
            if not already_managed:
                Path(managed_path).unlink(missing_ok=True)
                if managed_index:
                    Path(managed_index).unlink(missing_ok=True)
            raise ValueError(
                f"{path.name} changed while it was being imported — wait for "
                "the file to finish copying, then import it again"
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
        if import_outcome == "exact_current":
            warnings.append(
                "Already in the Sample Library; GUIDE-IEI reused the existing "
                "copy and preserved its current Cohort Search membership."
            )
        elif import_outcome == "exact_previous":
            warnings.append(
                "This exact file is already retained as a previous version; "
                "the current version was not replaced."
            )
        elif import_outcome == "updated_annotation":
            warnings.append(
                "Recognized the same genotype callset with updated annotations; "
                "the prior annotation version was moved to Previous versions."
            )
        elif import_outcome == "updated_version":
            warnings.append(
                "Replaced the selected dataset with this updated version; the "
                "prior version remains available under Previous versions."
            )
        now = utc_now()
        datasets: list[dict] = []
        prior_rows: list[dict] = []
        version_id = ""
        version_number = 1

        if not identity_status.startswith("exact_"):
            with self._session() as connection:
                prior_rows = [
                    dict(row) for row in connection.execute(
                        """SELECT d.*,s.individual_id,s.label AS sample_label
                           FROM library_datasets d
                           JOIN library_samples s ON s.id=d.sample_id
                           WHERE d.callset_id=? AND d.is_current=1""",
                        (callset_id,),
                    ).fetchall()
                ]
                version_number = int(connection.execute(
                    "SELECT COALESCE(MAX(version_number),0)+1 FROM library_datasets WHERE callset_id=?",
                    (callset_id,),
                ).fetchone()[0])
            # Validation and managed-copy publication have completed. Remove
            # the old derived cohort rows before the new library version is
            # made current, so a crash or indexing failure can never leave two
            # versions counted at once.
            if prior_rows:
                self._remove_cohort_records(prior_rows)
            version_id = uuid.uuid4().hex

        with self._session() as connection:
            if original_checksum and not bool(payload.get("original_is_ephemeral")):
                # The original location is a property of the CONTENT: when the
                # same bytes are re-imported from a new home, datasets of this
                # managed file — any profile — must learn the live path, or
                # full-WGS reindexing keeps reading a deleted file. But the
                # refresh only HEALS, never clobbers: a row keeps its path
                # when that path is still alive and different (it may be the
                # raw WGS source while this import is a derived review file —
                # their original checksums differ), and staged-upload sources
                # are excluded above.
                candidates = connection.execute(
                    """SELECT id, original_path, original_checksum
                       FROM library_datasets WHERE managed_checksum=?""",
                    (review_checksum,),
                ).fetchall()
                heal = []
                for row in candidates:
                    row_checksum = row["original_checksum"] or ""
                    if row_checksum and row_checksum != original_checksum:
                        continue
                    stored = row["original_path"] or ""
                    stored_resolved = (
                        self._original_path(stored) if stored else None
                    )
                    if (
                        not stored
                        or str(stored_resolved) == original_path
                        or (stored_resolved is not None and not Path(stored_resolved).is_file())
                    ):
                        heal.append(row["id"])
                if heal:
                    connection.executemany(
                        """UPDATE library_datasets
                           SET original_name=?, original_path=?, original_checksum=?,
                               original_size_bytes=?, original_mtime_ns=?, updated_at=?
                           WHERE id=?""",
                        [
                            (original_name, original_path, original_checksum,
                             original_size, original_mtime, now, row_id)
                            for row_id in heal
                        ],
                    )
            if identity_status.startswith("exact_"):
                for vcf_sample in header.samples:
                    existing_dataset = connection.execute(
                        """SELECT d.id,d.sample_id,d.vcf_sample_name,s.individual_id,
                                  d.version_id,d.version_number,d.is_current,
                                  d.settings_hash,d.profile_label,d.complete_settings,
                                  d.prefilter_settings,d.analysis_scope,d.index_scope
                           FROM library_datasets d
                           JOIN library_samples s ON s.id=d.sample_id
                           WHERE d.managed_checksum=? AND d.vcf_sample_name=?
                           ORDER BY d.is_current DESC,d.imported_at DESC LIMIT 1""",
                        (review_checksum, vcf_sample),
                    ).fetchone()
                    if not existing_dataset:
                        raise RuntimeError("exact library identity lost during import")
                    datasets.append(dict(existing_dataset))
                version_id = str(datasets[0].get("version_id") or review_checksum)
                version_number = int(datasets[0].get("version_number") or 1)
                # An exact reimport is an OPEN/REUSE operation, not a chance
                # for the workstation's current settings to rewrite the
                # provenance of already-retained bytes.
                settings_hash = str(datasets[0]["settings_hash"])
                profile_label = str(datasets[0]["profile_label"])
                try:
                    settings = json.loads(datasets[0]["complete_settings"] or "{}")
                except (json.JSONDecodeError, TypeError):
                    settings = {}
                try:
                    prefilter_settings = json.loads(datasets[0]["prefilter_settings"] or "{}")
                except (json.JSONDecodeError, TypeError):
                    prefilter_settings = {}
                analysis_scope = str(datasets[0]["analysis_scope"])
                index_scope = str(datasets[0]["index_scope"])
            else:
                prior_by_sample = {
                    row["vcf_sample_name"]: row for row in prior_rows
                }
                if prior_rows:
                    connection.execute(
                        """UPDATE library_datasets
                           SET is_current=0,include_in_cohort=0,cohort_file_id=NULL,updated_at=?
                           WHERE callset_id=? AND is_current=1""",
                        (now, callset_id),
                    )
                for vcf_sample in header.samples:
                    prior = prior_by_sample.get(vcf_sample)
                    if prior:
                        sample_id = prior["sample_id"]
                        individual_id = prior.get("individual_id")
                    else:
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
                      retained_record_count,include_in_cohort,status,warnings,imported_at,updated_at,
                      callset_id,callset_fingerprint,version_id,version_number,is_current,
                      cohort_preferred,supersedes_version_id
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
                        callset_id, callset_fingerprint, version_id, version_number,
                        1, int(include_in_cohort),
                        prior_rows[0]["version_id"] if prior_rows else None,
                    ),
                    )
                    datasets.append({
                        "id": dataset_id, "sample_id": sample_id,
                        "vcf_sample_name": vcf_sample, "individual_id": individual_id,
                        "version_id": version_id, "version_number": version_number,
                        "is_current": 1,
                    })

        cohort_result = None
        cohort_error = ""
        # Exact re-import is deliberately state preserving. In particular, a
        # user may have removed one sample of a multi-sample callset from
        # Cohort Search; re-opening the same VCF must not silently add that
        # sample back merely because intake defaults to cohort inclusion.
        effective_include = include_in_cohort and not identity_status.startswith("exact_")
        if effective_include:
          try:
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
                # Scope the linkage to THIS import's dataset rows. managed_path
                # is not unique — every sample of a multi-sample VCF (and any
                # prior import of the same file under different settings)
                # shares it, and a path-scoped UPDATE silently repointed
                # sibling datasets and reverted deliberate cohort exclusions.
                connection.executemany(
                    "UPDATE library_datasets SET cohort_file_id=?,include_in_cohort=1,cohort_preferred=1,updated_at=? WHERE id=?",
                    [
                        (cohort_result.get("id"), utc_now(), item["id"])
                        for item in datasets
                    ],
                )
                connection.execute(
                    """UPDATE cohort_files SET profile_label=?,profile_hash=?,profile_json=?
                       WHERE id=?""",
                    (profile_label, settings_hash, _json(settings), cohort_result.get("id")),
                )
          except Exception as exc:  # noqa: BLE001 — the library import stands
            # The library rows are already committed and remain fully usable;
            # a cohort-indexing failure must not present as a failed import
            # that half-landed. The datasets surface as "needs repair" with
            # the working Repair action, and the reason travels with them.
            cohort_error = str(exc)[:300]
            warnings.append(
                f"Cohort Search indexing failed: {cohort_error} — the dataset "
                "is in the library; use 'Repair Cohort Search' on its card."
            )
            with self._session() as connection:
                connection.executemany(
                    "UPDATE library_datasets SET warnings=?, updated_at=? WHERE id=?",
                    [(_json(warnings), utc_now(), item["id"]) for item in datasets],
                )
        cohort_sample_ids = {}
        cohort_sample_ids_by_dataset = {}
        if cohort_result and cohort_result.get("id") is not None:
            with self._session() as connection:
                cohort_sample_ids = {
                    row["name"]: row["id"]
                    for row in connection.execute(
                        "SELECT id,name FROM cohort_samples WHERE file_id=?",
                        (cohort_result["id"],),
                    ).fetchall()
                }
        elif identity_status.startswith("exact_"):
            # Reuse the existing per-sample linkage without reindexing the
            # entire shared VCF (which would undo deliberate exclusions).
            with self._session() as connection:
                for item in datasets:
                    linked = connection.execute(
                        """SELECT cs.id FROM library_datasets d
                           JOIN cohort_samples cs
                             ON cs.file_id=d.cohort_file_id
                            AND cs.name=d.vcf_sample_name
                           JOIN cohort_files cf ON cf.id=cs.file_id
                           WHERE d.id=? AND d.include_in_cohort=1
                             AND cf.profile_hash=d.settings_hash
                           LIMIT 1""",
                        (item["id"],),
                    ).fetchone()
                    cohort_sample_ids_by_dataset[item["id"]] = (
                        linked["id"] if linked else None
                    )
        datasets = [
            {
                **item,
                "cohort_sample_entry_id": (
                    cohort_sample_ids.get(item["vcf_sample_name"])
                    if cohort_result else cohort_sample_ids_by_dataset.get(item["id"])
                ),
            }
            for item in datasets
        ]
        return {
            "datasets": datasets,
            "managed_path": str(managed_path),
            "deduplicated_file": already_managed,
            "profile_label": profile_label,
            "profile_hash": settings_hash,
            "callset_id": callset_id,
            "version_id": version_id,
            "version_number": version_number,
            "import_outcome": import_outcome,
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
                       (
                         SELECT cs.id FROM cohort_samples cs
                         JOIN cohort_files cf ON cf.id=cs.file_id
                         WHERE cf.id=d.cohort_file_id AND cs.name=d.vcf_sample_name
                           AND cf.profile_hash=d.settings_hash
                         LIMIT 1
                       ) AS cohort_sample_entry_id,
                       EXISTS(
                         SELECT 1 FROM cohort_files cf
                         JOIN cohort_samples cs ON cs.file_id=cf.id
                         WHERE cf.id=d.cohort_file_id AND cs.name=d.vcf_sample_name
                           AND cf.profile_hash=d.settings_hash
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
                          (
                            SELECT cs.id FROM cohort_samples cs
                            JOIN cohort_files cf ON cf.id=cs.file_id
                            WHERE cf.id=d.cohort_file_id AND cs.name=d.vcf_sample_name
                              AND cf.profile_hash=d.settings_hash
                            LIMIT 1
                          ) AS cohort_sample_entry_id,
                          EXISTS(
                            SELECT 1 FROM cohort_files cf
                            JOIN cohort_samples cs ON cs.file_id=cf.id
                            WHERE cf.id=d.cohort_file_id AND cs.name=d.vcf_sample_name
                              AND cf.profile_hash=d.settings_hash
                          ) AS cohort_index_present
                   FROM library_datasets d JOIN library_samples s ON s.id=d.sample_id
                   WHERE d.id=?""", (dataset_id,),
            ).fetchone()
        return self._serialize(row) if row else None

    def file(self, dataset_id: str) -> Path:
        record = self.get(dataset_id)
        if not record:
            raise NotFoundError(f"library dataset not found: {dataset_id}")
        path = self._managed_path(record["managed_path"])
        if not path.is_file():
            raise FileNotFoundError(f"managed review VCF is missing: {path}")
        return path

    # bcftools -Oz output is BGZF, which always ends in this fixed
    # 28-byte empty-block EOF marker; its absence means truncation.
    _BGZF_EOF = bytes.fromhex(
        "1f8b08040000000000ff0600424302001b0003000000000000000000"
    )

    @staticmethod
    def _cached_projection_valid(
        projected: Path, source: Path,
        expected_samples: tuple[str, ...] | None = None,
    ) -> bool:
        """A projection cache hit must prove it is a readable VCF.

        Freshness (mtime) alone trusted whatever bytes sat at the cache
        path — any overwrite advances the mtime, so corruption from an
        out-of-band writer or bit rot was served verbatim. Three cheap
        proofs turn a damaged cache into a silent rebuild instead of a
        broken review: the header parses, its samples are the expected
        ones (a valid VCF for the WRONG samples is the worst corruption),
        and the BGZF end-of-file marker is present — the header check only
        covers the first compressed block, while truncation removes the
        tail. A missing SOURCE is a caller-level error and surfaces from
        here rather than masquerading as a cache miss.

        Accepted limit, deliberately: a structurally valid projection with
        the right samples but WRONG interior variants is served. No
        internal path can produce one (staging is per-invocation unique,
        publishes are atomic, and the cache name binds the source's full
        checksum plus the exact sample name); forging one requires a
        same-privilege writer inside the app-private state dir, which
        could equally rewrite the database itself. Content hashing on
        every open was weighed against that threat model and declined.
        """
        source_mtime = source.stat().st_mtime
        try:
            if not (
                projected.is_file()
                and projected.stat().st_mtime >= source_mtime
            ):
                return False
            header = read_vcf_header(projected)
            if expected_samples is not None and header.samples != expected_samples:
                return False
            with projected.open("rb") as handle:
                handle.seek(-len(SampleLibrary._BGZF_EOF), os.SEEK_END)
                if handle.read() != SampleLibrary._BGZF_EOF:
                    return False
            return True
        except (OSError, EOFError, ValueError, zlib.error):
            return False

    def review_file(self, dataset_id: str) -> Path:
        """Path a review should open for this dataset.

        A managed VCF carrying many sample columns (a jointly-called family
        or cohort source) is projected down to this dataset's own sample:
        one genotype column, carrier records only — the same shape the
        cohort complete-set path produces, and the shape the review
        workspace is built for. Single-sample files are served as stored.
        Projections are cached beside the managed files, keyed by checksum
        and sample, so repeat opens are instant.
        """
        record = self.get(dataset_id)
        if not record:
            raise NotFoundError(f"library dataset not found: {dataset_id}")
        source = self._managed_path(record["managed_path"])
        if not source.is_file():
            raise FileNotFoundError(f"managed review VCF is missing: {source}")
        sample = str(record.get("vcf_sample_name") or "")
        if not sample:
            return source
        header = read_vcf_header(source)
        if len(header.samples) <= 1:
            # A single-sample managed file is served whole — so its one
            # sample must actually BE this dataset's sample. Any identity
            # drift between the library rows and the stored bytes surfaces
            # loudly here instead of silently serving another patient.
            if header.samples and header.samples[0] != sample:
                raise ValueError(
                    f"the managed review VCF carries sample "
                    f"{header.samples[0]!r} but the library records "
                    f"{sample!r} for this dataset — re-import the file"
                )
            return source
        if sample not in header.samples:
            raise ValueError(
                f"sample {sample!r} is not present in the managed review VCF"
            )
        backend = self.cohort.hts_backend
        if backend is None:
            # Without htslib the projection cannot be built; serve the stored
            # file — the browser refuses cohort-scale files with directions
            # instead of freezing, so this degrades loudly, not silently.
            return source
        checksum = str(record.get("managed_checksum") or source.stem)
        # The cache key must be collision-proof for the exact sample name:
        # lossy sanitization alone let same-file samples differing only in
        # special characters (PAT/1 vs PAT?1) share one cache file, serving
        # one patient's variants under another's name. A hash of the exact
        # name disambiguates; the sanitized prefix stays for readability.
        safe_sample = "".join(
            ch if ch.isalnum() or ch in "._-" else "_" for ch in sample
        )[:40]
        sample_digest = hashlib.sha256(sample.encode("utf-8")).hexdigest()[:12]
        projected = (
            self.files_dir
            / f"{checksum}.{safe_sample}.{sample_digest}.review.vcf.gz"
        )
        if self._cached_projection_valid(projected, source, (sample,)):
            return projected
        partial = projected.with_name(projected.name + f".{uuid.uuid4().hex}.partial.vcf.gz")
        staged = projected.with_name(projected.name + f".{uuid.uuid4().hex}.staged.vcf.gz")
        try:
            subset = backend.run(
                "bcftools",
                ["view", "-s", sample, "-O", "z", "-o", str(partial), str(source)],
            )
            if subset.returncode != 0:
                raise RuntimeError(
                    f"sample projection failed for {sample}: {subset.stderr.strip()[:400]}"
                )
            # Stage, then publish atomically: writing the final path directly
            # let two concurrent projections interleave bytes, and a failed
            # write left a partial file the cache check then trusted.
            carriers = backend.run(
                "bcftools",
                ["view", "-i", 'GT[0]="alt"', "-O", "z", "-o", str(staged), str(partial)],
            )
            if carriers.returncode != 0:
                raise RuntimeError(
                    f"carrier filtering failed for {sample}: {carriers.stderr.strip()[:400]}"
                )
            os.replace(staged, projected)
        finally:
            partial.unlink(missing_ok=True)
            staged.unlink(missing_ok=True)
        return projected

    def original_review_record(self, dataset_id: str, variant_key: str) -> dict:
        """Return one exact record from the original, un-compacted VCF.

        Managed review files intentionally omit redundant CSQ rows. When a
        reviewer opens one variant, this bounded lookup restores its complete
        transcript evidence from the original source without sending the full
        cohort VCF to the browser. This keeps library imports useful as long
        as their original VCF remains available.
        """
        record = self.get(dataset_id)
        if not record:
            raise NotFoundError(f"library dataset not found: {dataset_id}")
        parsed = self.cohort._parse_variant_query(str(variant_key or "").strip())
        if not parsed or parsed[0] != "v.variant_key = ?":
            raise ValueError("variant_key must be CHROM:POS:REF:ALT")
        canonical_key = parsed[1][0]
        chrom, pos_raw, ref, alt = canonical_key.split(":", 3)
        pos = int(pos_raw)
        source = self._original_path(record.get("original_path"))
        if not source.is_file():
            raise FileNotFoundError(
                "the original annotated VCF is no longer available; re-import it "
                "to restore omitted transcript annotations"
            )
        backend = self.cohort.hts_backend
        header = read_vcf_header(source)
        header_lines = read_vcf_header_lines(source)
        sample = str(record.get("vcf_sample_name") or "")
        if sample not in header.samples:
            raise ValueError(
                f"sample {sample!r} is absent from the original annotated VCF"
            )
        sample_index = header.samples.index(sample) + 9
        source_indexes = (Path(f"{source}.tbi"), Path(f"{source}.csi"))
        has_source_index = any(index.is_file() for index in source_indexes)
        if has_source_index and backend is None:
            raise RuntimeError(
                "bcftools/tabix is unavailable; complete transcript annotations "
                "cannot be restored from the indexed original VCF"
            )
        contigs = tuple(header.contigs)
        if not contigs and has_source_index and backend is not None:
            contigs = tuple(backend.list_contigs(source))
        contig_by_normalized = {
            normalize_chromosome(contig): contig for contig in contigs
        }
        source_contig = contig_by_normalized.get(chrom, chrom)
        selected_line = ""
        if has_source_index:
            assert backend is not None
            source_records = backend.iter_records(
                source, [f"{source_contig}:{pos}-{pos}"]
            )
            source_handle = None
        else:
            # Older staged uploads copied the VCF but not its sidecar index.
            # A bounded exome-sized scan keeps those imports repairable; never
            # scan a multi-gigabyte genome interactively.
            if source.stat().st_size > 512 * 1024 * 1024:
                raise RuntimeError(
                    "the original VCF has no tabix/CSI index and is too large "
                    "for an interactive scan; re-import it to retain compact "
                    "transcript-score summaries"
                )
            source_handle = (
                gzip.open(source, "rt", encoding="utf-8", errors="replace")
                if source.name.lower().endswith((".gz", ".bgz"))
                else source.open("rt", encoding="utf-8", errors="replace")
            )
            source_records = (line for line in source_handle if not line.startswith("#"))
        try:
            for line in source_records:
                columns = line.rstrip("\r\n").split("\t")
                if len(columns) <= sample_index:
                    continue
                try:
                    record_pos = int(columns[1])
                except (IndexError, ValueError):
                    continue
                # Compare canonical alleles: the stored key is the minimal
                # representation, the original record may be padded.
                if normalize_chromosome(columns[0]) == chrom and any(
                    canonical_variant_key(columns[0], record_pos, columns[3], record_alt) == canonical_key
                    for record_alt in columns[4].split(",")
                ):
                    selected_line = "\t".join([*columns[:9], columns[sample_index]])
                    break
        finally:
            if source_handle is not None:
                source_handle.close()
        if not selected_line:
            raise FileNotFoundError(
                f"exact allele {canonical_key} was not found in the original annotated VCF"
            )
        header_columns = header_lines[-1].split("\t")
        projected_header = [
            *header_lines[:-1],
            "\t".join([*header_columns[:9], sample]),
        ]
        return {
            "variant_key": canonical_key,
            "sample": sample,
            "name": f"library-{dataset_id[:8]}-transcripts.vcf",
            "vcf": "\n".join([*projected_header, selected_line, ""]),
        }

    def review_file_combined(self, dataset_ids: list[str]) -> Path:
        """Path a combined review should open for several datasets.

        All datasets must come from the same managed source file. Selecting
        every sample serves the stored file unchanged (cohort review mode
        parses it variant-centrically in the browser); a subset is projected
        to just those sample columns with records where any selected sample
        carries an alternate allele, cached by checksum + sample set.
        """
        ids = [str(value) for value in dataset_ids if str(value).strip()]
        if not ids:
            raise ValueError("select at least one dataset")
        if len(ids) == 1:
            return self.review_file(ids[0])
        records = []
        for dataset_id in ids:
            record = self.get(dataset_id)
            if not record:
                raise NotFoundError(f"library dataset not found: {dataset_id}")
            records.append(record)
        checksums = {str(r.get("managed_checksum") or "") for r in records}
        if len(checksums) != 1:
            raise ValueError(
                "combined review requires datasets from the same imported file"
            )
        source = self._managed_path(records[0]["managed_path"])
        if not source.is_file():
            raise FileNotFoundError(f"managed review VCF is missing: {source}")
        wanted = [str(r.get("vcf_sample_name") or "") for r in records]
        if any(not name for name in wanted):
            raise ValueError("every selected dataset needs a VCF sample name")
        header = read_vcf_header(source)
        missing = [name for name in wanted if name not in header.samples]
        if missing:
            raise ValueError(
                f"samples not present in the managed review VCF: {', '.join(missing)}"
            )
        if set(wanted) == set(header.samples):
            return source
        backend = self.cohort.hts_backend
        if backend is None:
            return source
        ordered = sorted(set(wanted))
        selection_id = hashlib.sha256("\n".join(ordered).encode("utf-8")).hexdigest()[:16]
        checksum = checksums.pop() or source.stem
        projected = self.files_dir / f"{checksum}.subset-{selection_id}.review.vcf.gz"
        if self._cached_projection_valid(projected, source, tuple(ordered)):
            return projected
        partial = projected.with_name(projected.name + f".{uuid.uuid4().hex}.partial.vcf.gz")
        staged = projected.with_name(projected.name + f".{uuid.uuid4().hex}.staged.vcf.gz")
        try:
            subset = backend.run(
                "bcftools",
                ["view", "-s", ",".join(ordered), "-O", "z", "-o", str(partial), str(source)],
            )
            if subset.returncode != 0:
                raise RuntimeError(
                    f"sample projection failed: {subset.stderr.strip()[:400]}"
                )
            carriers = backend.run(
                "bcftools",
                ["view", "-i", 'GT[*]="alt"', "-O", "z", "-o", str(staged), str(partial)],
            )
            if carriers.returncode != 0:
                raise RuntimeError(
                    f"carrier filtering failed: {carriers.stderr.strip()[:400]}"
                )
            os.replace(staged, projected)
        finally:
            partial.unlink(missing_ok=True)
            staged.unlink(missing_ok=True)
        return projected

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
                # Update only the addressed dataset row: managed_path is
                # shared by every sample extracted from a multi-sample VCF,
                # and a path-scoped UPDATE rewrote sibling samples' capture
                # kit / target BED behind the operator's back.
                clause = ",".join(f"{key}=?" for key in updates)
                connection.execute(
                    f"UPDATE library_datasets SET {clause},updated_at=? WHERE id=?",
                    (*updates.values(), utc_now(), dataset_id),
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
                # The recomputed profile embeds THIS row's capture kit and
                # target BED, so it must land only on the addressed dataset;
                # siblings sharing managed_path keep their own profile.
                connection.execute(
                    """UPDATE library_datasets SET complete_settings=?,settings_hash=?,
                           profile_label=?,updated_at=? WHERE id=?""",
                    (_json(settings), settings_hash, profile_label, utc_now(), dataset_id),
                )
                if record.get("cohort_file_id"):
                    # cohort_files carries ONE profile per indexed file. When
                    # several datasets (samples) share this managed file,
                    # rewriting it with THIS dataset's capture kit would
                    # relabel the co-resident samples' cohort rows too — so
                    # the file-level profile is only updated when this
                    # dataset is the file's sole occupant.
                    sharing = connection.execute(
                        "SELECT COUNT(*) FROM library_datasets"
                        " WHERE managed_checksum = ? AND id != ?",
                        (record["managed_checksum"], dataset_id),
                    ).fetchone()[0]
                    if sharing == 0:
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
        if not record.get("is_current", True):
            raise ValueError(
                "this is a previous dataset version; restore it before adding "
                "it to Cohort Search"
            )
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
            # Full-WGS indexing reads the ORIGINAL multi-sample VCF. Index
            # only this dataset's own column: siblings stay in their existing
            # cohort file, so no sample is ever counted twice (audit H2). A
            # sibling's later full reindex appends to the same full file.
            restrict_samples=(record["vcf_sample_name"],) if full_wgs else None,
        )
        if full_wgs and record.get("cohort_file_id") != result.get("id"):
            with self._session() as connection:
                # Remove only THIS dataset's sample from the previous cohort
                # file. The file holds one row per VCF sample, and an
                # unfiltered deletion removed every co-resident sample's
                # cohort records (then reclaimed their variants).
                previous_samples = [
                    item["id"] for item in connection.execute(
                        "SELECT id FROM cohort_samples WHERE file_id=? AND name=?",
                        (record.get("cohort_file_id"), record["vcf_sample_name"]),
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
            if full_wgs:
                # Only this dataset's sample was re-imported into the full
                # index; siblings sharing the original VCF keep their rows in
                # (and their linkage to) the previous cohort file, so only the
                # addressed dataset may be repointed.
                connection.execute(
                    """UPDATE library_datasets SET cohort_file_id=?,include_in_cohort=1,
                           cohort_preferred=1,
                           index_scope=?,complete_settings=?,settings_hash=?,profile_label=?,updated_at=?
                           WHERE id=?""",
                    (result.get("id"), index_scope, _json(profile_settings), profile_hash,
                     profile_label, utc_now(), dataset_id),
                )
            else:
                # Re-importing the managed VCF indexes every co-resident
                # sample, so cohort LINKAGE is repaired for all datasets
                # sharing the file. index_scope, however, is stamped only on
                # same-profile rows: writing this dataset's scope onto a
                # divergent sibling poisoned that sibling's own later repair
                # (a full-index dataset silently repaired as compact).
                # Whether a relinked sibling shows "ready" is decided by the
                # coherence-derived status — a divergent profile surfaces as
                # needs-repair even though its sample is in the index.
                connection.execute(
                    """UPDATE library_datasets SET cohort_file_id=?,include_in_cohort=1,
                           cohort_preferred=1,
                           updated_at=? WHERE managed_path=? AND is_current=1""",
                    (result.get("id"), utc_now(),
                     self._stored_state_path(record["managed_path"])),
                )
                connection.execute(
                    """UPDATE library_datasets SET index_scope=?,updated_at=?
                           WHERE managed_path=? AND settings_hash=? AND is_current=1""",
                    (index_scope, utc_now(),
                     self._stored_state_path(record["managed_path"]),
                     record["settings_hash"]),
                )
                # ...but profile identity stays per-dataset: a sibling may
                # have diverged (capture kit / target BED), and this record's
                # profile must not overwrite it.
                connection.execute(
                    """UPDATE library_datasets SET complete_settings=?,settings_hash=?,
                           profile_label=?,updated_at=? WHERE id=?""",
                    (_json(profile_settings), profile_hash, profile_label, utc_now(), dataset_id),
                )
            connection.execute(
                "UPDATE cohort_files SET profile_label=?,profile_hash=?,profile_json=? WHERE id=?",
                (profile_label, profile_hash, _json(profile_settings), result.get("id")),
            )
        return {"dataset": self.get(dataset_id), "cohort": result}

    def activate_version(self, dataset_id: str) -> dict:
        """Restore one historical callset version as the active version."""
        with self._import_lock:
            record = self.get(dataset_id)
            if not record:
                raise ValueError("library dataset was not found")
            if record.get("is_current"):
                return {"datasets": [record], "already_current": True}
            callset_id = record["callset_id"]
            version_id = record["version_id"]
            with self._session() as connection:
                target = [dict(row) for row in connection.execute(
                    "SELECT * FROM library_datasets WHERE callset_id=? AND version_id=?",
                    (callset_id, version_id),
                ).fetchall()]
                current = [dict(row) for row in connection.execute(
                    "SELECT * FROM library_datasets WHERE callset_id=? AND is_current=1",
                    (callset_id,),
                ).fetchall()]
            if not target:
                raise ValueError("the selected previous version is unavailable")
            managed_path = self._managed_path(target[0]["managed_path"])
            if not managed_path.is_file():
                raise ValueError(
                    f"managed review VCF is missing: {managed_path}"
                )
            include_in_cohort = any(
                bool(row.get("cohort_preferred")) for row in current
            ) or any(bool(row.get("cohort_preferred")) for row in target)
            self._remove_cohort_records(current)
            now = utc_now()
            with self._session() as connection:
                connection.execute(
                    """UPDATE library_datasets
                       SET is_current=0,include_in_cohort=0,cohort_file_id=NULL,updated_at=?
                       WHERE callset_id=? AND is_current=1""",
                    (now, callset_id),
                )
                connection.execute(
                    """UPDATE library_datasets
                       SET is_current=1,include_in_cohort=?,cohort_file_id=NULL,updated_at=?
                       WHERE callset_id=? AND version_id=?""",
                    (int(include_in_cohort), now, callset_id, version_id),
                )

            cohort_result = None
            warning = ""
            if include_in_cohort:
                try:
                    prefilter = json.loads(target[0]["prefilter_settings"] or "{}")
                    settings = json.loads(target[0]["complete_settings"] or "{}")
                    cohort_result = self.cohort.import_vcf(
                        managed_path,
                        force=True,
                        import_profile=(
                            "prefiltered"
                            if target[0]["analysis_scope"] == "whole_genome"
                            and target[0]["index_scope"] == "compact"
                            else "full"
                        ),
                        analysis_scope=target[0]["analysis_scope"],
                        prefilter_options=prefilter,
                    )
                    with self._session() as connection:
                        connection.execute(
                            """UPDATE library_datasets
                               SET cohort_file_id=?,include_in_cohort=1,cohort_preferred=1,updated_at=?
                               WHERE callset_id=? AND version_id=?""",
                            (cohort_result.get("id"), utc_now(), callset_id, version_id),
                        )
                        connection.execute(
                            """UPDATE cohort_files
                               SET profile_label=?,profile_hash=?,profile_json=? WHERE id=?""",
                            (target[0]["profile_label"], target[0]["settings_hash"],
                             _json(settings), cohort_result.get("id")),
                        )
                except Exception as exc:  # active library version remains valid
                    warning = (
                        f"Previous version restored, but Cohort Search indexing failed: "
                        f"{str(exc)[:300]} — use Repair Cohort Search."
                    )
                    with self._session() as connection:
                        connection.execute(
                            """UPDATE library_datasets SET warnings=?,updated_at=?
                               WHERE callset_id=? AND version_id=?""",
                            (_json([warning]), utc_now(), callset_id, version_id),
                        )
            with self._session() as connection:
                ids = [row["id"] for row in connection.execute(
                    "SELECT id FROM library_datasets WHERE callset_id=? AND version_id=? ORDER BY vcf_sample_name",
                    (callset_id, version_id),
                ).fetchall()]
            return {
                "datasets": [self.get(value) for value in ids],
                "cohort": cohort_result,
                "warning": warning,
                "already_current": False,
            }

    def cohort_entry_identities(self, sample_ids: list[int]) -> list[tuple[int, str]]:
        """Return (cohort_file_id, vcf_sample_name) for cohort sample entries.

        Callers that remove cohort entries directly (the Cohort Search
        manager) capture these BEFORE the removal so the library linkage that
        pointed at them can be cleared afterwards. Without that step the
        dataset keeps a dangling cohort_file_id, and a later file that
        happened to receive the same id would be resolved as this dataset.
        """
        try:
            selected = sorted({int(value) for value in sample_ids})
        except (TypeError, ValueError):
            return []
        identities: list[tuple[int, str]] = []
        with self._session() as connection:
            for start in range(0, len(selected), 900):
                chunk = selected[start:start + 900]
                placeholders = ",".join("?" for _ in chunk)
                identities.extend(
                    (int(row["file_id"]), str(row["name"]))
                    for row in connection.execute(
                        f"SELECT file_id, name FROM cohort_samples WHERE id IN ({placeholders})",
                        chunk,
                    ).fetchall()
                )
        return identities

    def detach_cohort_entries(self, identities: list[tuple[int, str]]) -> int:
        """Clear library linkage to cohort entries that no longer exist.

        The dataset stays in the library with include_in_cohort unchanged, so
        it surfaces as needs_repair with a working repair action instead of
        silently re-binding to whichever cohort file next carries that id.
        """
        detached = 0
        if not identities:
            return 0
        with self._session() as connection:
            for file_id, name in identities:
                cursor = connection.execute(
                    """UPDATE library_datasets
                       SET cohort_preferred=0,cohort_file_id=NULL,updated_at=?
                       WHERE cohort_file_id=? AND vcf_sample_name=?""",
                    (utc_now(), int(file_id), str(name)),
                )
                detached += cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else 0
        return detached

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
                   SET include_in_cohort=0,cohort_preferred=0,cohort_file_id=NULL,updated_at=?
                   WHERE cohort_file_id=? AND vcf_sample_name=?""",
                (utc_now(), cohort_file_id, record["vcf_sample_name"]),
            )
            connection.execute(
                """UPDATE library_datasets
                   SET include_in_cohort=0,cohort_preferred=0,cohort_file_id=NULL,updated_at=?
                   WHERE id=?""",
                (utc_now(), dataset_id),
            )
        return self.get(dataset_id) or {}

    def bulk_apply(self, dataset_ids: list[str], action: str) -> dict:
        """Apply remove / cohort-add to many datasets in one request.

        One request, one report — a thousand datasets must not cost a
        thousand HTTP round-trips, and an interruption must leave a
        legible outcome instead of a half-done mystery.
        """
        if action not in {"remove", "cohort_add"}:
            raise ValueError("action must be remove or cohort_add")
        ids = [str(value) for value in dataset_ids if str(value).strip()]
        if not ids:
            raise ValueError("select at least one dataset")
        succeeded: list[str] = []
        skipped: list[str] = []
        failures: list[dict] = []
        for dataset_id in ids:
            try:
                record = self.get(dataset_id)
                if not record:
                    raise NotFoundError("library dataset not found")
                if action == "cohort_add":
                    if record.get("cohort_index_status") == "ready":
                        skipped.append(dataset_id)
                        continue
                    self.reindex(dataset_id)
                else:
                    self.remove(dataset_id)
                succeeded.append(dataset_id)
            except Exception as error:  # per-item isolation is the point
                failures.append({"id": dataset_id, "error": str(error)[:300]})
        return {
            "action": action,
            "requested": len(ids),
            "succeeded": len(succeeded),
            "skipped": len(skipped),
            "failures": failures,
        }

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
        # Per-sample review projections (audit M28): these hold the removed
        # individual's genotypes and were left behind forever — outside every
        # cleanup category. Remove this sample's projection always, and every
        # projection of the managed file once no dataset references it.
        removed_files.extend(
            self._remove_projections(record, all_samples=remaining == 0)
        )
        return {"removed_dataset": dataset_id, "removed_files": removed_files}

    def _remove_projections(self, record: dict, *, all_samples: bool) -> list[str]:
        managed = record.get("managed_path") or ""
        if not managed:
            return []
        try:
            source = self._managed_path(managed)
        except (ValueError, OSError):
            return []
        checksum = str(record.get("managed_checksum") or source.stem)
        if not checksum or any(ch in checksum for ch in "*?[/\\"):
            return []
        if all_samples:
            patterns = [f"{checksum}.*.review.vcf.gz", f"{checksum}.*.review.vcf.gz.*"]
        else:
            sample = str(record.get("vcf_sample_name") or "")
            if not sample:
                return []
            digest = hashlib.sha256(sample.encode("utf-8")).hexdigest()[:12]
            patterns = [
                f"{checksum}.*.{digest}.review.vcf.gz",
                f"{checksum}.*.{digest}.review.vcf.gz.*",
                # A combined projection of several samples of this file is
                # named by the selection, not by its members: any of them
                # may include the removed sample, so all of them go (review
                # follow-up of M28). They are rebuilt on demand.
                f"{checksum}.subset-*.review.vcf.gz",
                f"{checksum}.subset-*.review.vcf.gz.*",
            ]
        removed: list[str] = []
        if not self.files_dir.exists():
            return removed
        for pattern in patterns:
            for path in sorted(self.files_dir.glob(pattern)):
                if path.is_file():
                    try:
                        path.unlink()
                        removed.append(str(path))
                    except OSError:
                        pass
        return removed

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
            current_datasets = connection.execute(
                "SELECT COUNT(*) FROM library_datasets WHERE is_current=1"
            ).fetchone()[0]
            callsets = connection.execute(
                "SELECT COUNT(DISTINCT callset_id) FROM library_datasets"
            ).fetchone()[0]
            versions = connection.execute(
                "SELECT COUNT(DISTINCT version_id) FROM library_datasets"
            ).fetchone()[0]
        locations = {
            "database": self.database_path.stat().st_size if self.database_path.exists() else 0,
            "managed_library": _directory_size(self.root),
            # Per-sample review projections (rebuilt on demand) — counted
            # inside managed_library, reported separately so the Storage page
            # shows what the "projections" cleanup category reclaims.
            "projections": sum(path.stat().st_size for path in self._projection_files()),
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
            "current_datasets": current_datasets, "callsets": callsets,
            "versions": versions,
            "managed_unique_files": managed_unique,
            "database_page_bytes": page_size * page_count,
            "database_reclaimable_bytes": page_size * freelist,
        }

    def _projection_files(self) -> list[Path]:
        """Every cached per-sample review projection (and its index files)."""
        if not self.files_dir.exists():
            return []
        found: list[Path] = []
        for pattern in ("*.review.vcf.gz", "*.review.vcf.gz.*"):
            found.extend(path for path in self.files_dir.glob(pattern) if path.is_file())
        return sorted(set(found))

    def cleanup_projections(self, *, orphaned_only: bool = False) -> list[str]:
        """Remove cached review projections (audit M28).

        Projections are derived from the managed VCFs and rebuilt on the next
        review, so the whole cache is reclaimable; ``orphaned_only`` limits
        the sweep to projections whose managed file no longer has a dataset.
        """
        referenced: dict[str, set[str]] = {}
        with self._session() as connection:
            for checksum, sample in connection.execute(
                "SELECT managed_checksum, vcf_sample_name FROM library_datasets"
            ):
                if checksum:
                    referenced.setdefault(str(checksum), set()).add(str(sample or ""))
        removed: list[str] = []
        orphaned: dict[str, bool] = {}
        for path in self._projection_files():
            if not path.name.endswith(".review.vcf.gz"):
                continue  # index files follow their data file below
            checksum = path.name.split(".", 1)[0]
            orphaned[path.name] = (
                not orphaned_only
                or checksum not in referenced
                # A projection carrying a sample this file no longer has a
                # dataset for is stale whatever its name says (review
                # follow-up of M28): the combined projections are named by
                # their selection, so their members are read from the header.
                or not self._projection_samples(path) <= referenced[checksum]
            )
        for path in self._projection_files():
            data_name = path.name if path.name.endswith(".review.vcf.gz") else (
                path.name.rsplit(".review.vcf.gz", 1)[0] + ".review.vcf.gz"
            )
            if not orphaned.get(data_name, True):
                continue
            try:
                path.unlink()
                removed.append(str(path))
            except OSError:
                pass
        return removed

    @staticmethod
    def _projection_samples(path: Path) -> set[str]:
        """Sample columns of a cached projection's header (bgzip is gzip).
        An unreadable header yields the empty set: nothing is known to be
        stale about it, and the projections category still reclaims it."""
        try:
            with gzip.open(path, "rt", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    if line.startswith("#CHROM"):
                        return set(line.rstrip("\r\n").split("\t")[9:])
                    if not line.startswith("#"):
                        break
        except (OSError, EOFError, ValueError, zlib.error):
            pass
        return set()

    def cleanup(self, categories: list[str]) -> dict:
        allowed = {"uploads", "cohort_cache", "wgs_review_cache", "partials", "configs", "projections"}
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
        selected_roots = [(category, roots[category]) for category in requested - {"partials", "projections"}]
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
        if "projections" in requested:
            removed.extend(self.cleanup_projections())
        elif requested:
            # Any cleanup also reaps projections whose managed file no longer
            # has a dataset (left by removals before this category existed).
            removed.extend(self.cleanup_projections(orphaned_only=True))
        after = self.storage_stats()
        return {"removed_files": len(removed), "freed_bytes": before["total_bytes"] - after["total_bytes"], "storage": after}

    def cleanup_partials(self) -> list[str]:
        removed = []
        for root in (self.root, self.cohort.staging_dir):
            if not root.exists():
                continue
            # Projection staging uses <name>.<uuid>.partial.vcf.gz and
            # .staged.vcf.gz suffixes; the bare "*.partial" glob matched
            # neither, so hard-killed builds accumulated forever.
            for pattern in (
                "*.partial", "*.partial.vcf.gz", "*.staged.vcf.gz",
                "*.building.vcf.gz", "*.snapshot.vcf", "*.snapshot.vcf.gz",
            ):
                for path in root.rglob(pattern):
                    if not path.is_file():
                        continue
                    if ".snapshot." in path.name:
                        # A snapshot lives for the WHOLE hash+prepare of a
                        # running import (minutes for large files) — a
                        # cleanup sweep must only reap crash leftovers,
                        # never an import in flight.
                        try:
                            age = time.time() - path.stat().st_mtime
                        except OSError:
                            continue
                        if age < 3600:
                            continue
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
                # "Ready" asserts COHERENCE, not mere linkage: the linked
                # cohort row must carry this dataset's own profile. A file
                # re-indexed under a different profile (takeover, repair of
                # a divergent sibling, a metadata edit changing this
                # dataset's profile) surfaces as needs-repair everywhere,
                # instead of a "ready" whose profile-filtered queries
                # silently return nothing.
                result["cohort_index_status"] = "ready"
            else:
                result["cohort_index_status"] = "needs_repair"
        if "is_current" in result:
            result["is_current"] = bool(result["is_current"])
        if "cohort_preferred" in result:
            result["cohort_preferred"] = bool(result["cohort_preferred"])
        return result
