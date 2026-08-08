"""Read-only local access to full ClinGen Evidence Repository assertions."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path


class ClinGenErepoStore:
    def __init__(self, database: Path, manifest: Path):
        self.database = database
        self.manifest = manifest
        self._status_signature: tuple[int, int, int, int] | None = None
        self._status_cache: dict | None = None

    def status(self) -> dict:
        if not self.database.is_file() or not self.manifest.is_file():
            return {"available": False, "error": "ClinGen variant-curation snapshot is not installed."}
        try:
            database_stat, manifest_stat = self.database.stat(), self.manifest.stat()
            signature = (
                database_stat.st_size, database_stat.st_mtime_ns,
                manifest_stat.st_size, manifest_stat.st_mtime_ns,
            )
            if signature == self._status_signature and self._status_cache is not None:
                return dict(self._status_cache)
            manifest = json.loads(self.manifest.read_text(encoding="utf-8"))
            with sqlite3.connect(f"file:{self.database}?mode=ro", uri=True) as connection:
                integrity = connection.execute("PRAGMA quick_check").fetchone()[0]
            if integrity != "ok":
                raise ValueError(f"database quick check: {integrity}")
            result = {
                "available": True, "error": "", "generated_utc": manifest.get("generated_utc", ""),
                "api_version": manifest.get("api_version", ""), "source_rows": int(manifest.get("source_rows", 0)),
                "active_rows": int(manifest.get("active_rows", 0)), "mapped_active_rows": int(manifest.get("mapped_active_rows", 0)),
                "mapping_rate": float(manifest.get("mapping_rate", 0)), "alleles": int(manifest.get("alleles", 0)),
                "source_sha256": manifest.get("source_sha256", ""),
            }
            self._status_signature, self._status_cache = signature, result
            return dict(result)
        except (OSError, ValueError, TypeError, json.JSONDecodeError, sqlite3.Error) as exc:
            return {"available": False, "error": f"ClinGen snapshot could not be validated: {exc}"}

    def variant(self, chrom: str, pos: int, ref: str, alt: str) -> dict:
        status = self.status()
        if not status.get("available"):
            return {**status, "assertions": []}
        query = """
          SELECT uuid,variation,clinvar_variation_id,caid,hgvs_expressions,gene,
                 disease,mondo_id,mode_of_inheritance,assertion,evidence_met,
                 evidence_not_met,interpretation_summary,pubmed,expert_panel,
                 guideline,approval_date,published_date,retracted,
                 evidence_repo_link,mapping_method
          FROM assertions WHERE chrom=? AND pos=? AND ref=? AND alt=? AND active=1
          ORDER BY disease,mode_of_inheritance,uuid
        """
        columns = [
            "uuid", "variation", "clinvar_variation_id", "caid", "hgvs_expressions", "gene",
            "disease", "mondo_id", "mode_of_inheritance", "assertion", "evidence_met",
            "evidence_not_met", "interpretation_summary", "pubmed", "expert_panel",
            "guideline", "approval_date", "published_date", "retracted",
            "evidence_repo_link", "mapping_method",
        ]
        with sqlite3.connect(f"file:{self.database}?mode=ro", uri=True) as connection:
            rows = connection.execute(query, (chrom.removeprefix("chr"), pos, ref, alt)).fetchall()
        return {**status, "assertions": [dict(zip(columns, row)) for row in rows]}
