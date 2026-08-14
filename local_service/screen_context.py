#!/usr/bin/env python3
"""Fast local lookup of prepared SCREEN Registry V4 context evidence.

The cell-type-agnostic Registry remains the authoritative overlap catalog.
Tissue aggregates and curated immune contexts are separate observed-evidence
layers; neither is converted into a target-gene assignment or a combined
regulatory score.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from collections import Counter
from pathlib import Path
from typing import Any



def _open_ro(path):
    """Read-only sqlite connection with a percent-encoded file: URI.

    URI mode parses '?' as the query string, '#' as a fragment, and decodes
    '%', so interpolating a raw filesystem path truncates or redirects the
    open for paths containing those characters. Path.as_uri() encodes them.
    """
    from pathlib import Path as _Path
    return sqlite3.connect(f"{_Path(path).resolve().as_uri()}?mode=ro&immutable=1", uri=True)


CLASS_LABELS = {
    0: "inactive",
    1: "PLS",
    2: "pELS",
    3: "dELS",
    4: "CA-H3K4me3",
    5: "CA-CTCF",
    6: "CA-TF",
    7: "CA",
    8: "TF",
}
CLASS_FAMILIES = {
    1: "promoter",
    2: "enhancer",
    3: "enhancer",
    4: "h3k4me3",
    5: "ctcf",
    6: "tf",
    7: "accessibility",
    8: "tf",
}


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return value


def _json_column(value: str) -> Any:
    return json.loads(value) if value else []


def _normalize_chrom(value: str) -> str:
    chrom = value.strip()
    if chrom.lower().startswith("chr"):
        chrom = chrom[3:]
    if chrom == "M":
        chrom = "MT"
    if not chrom:
        raise ValueError("chrom is required")
    return chrom


def _variant_interval(payload: dict[str, Any]) -> tuple[str, int, int]:
    chrom = _normalize_chrom(str(payload.get("chrom") or ""))
    try:
        pos = int(payload.get("pos"))
    except (TypeError, ValueError) as exc:
        raise ValueError("pos must be a positive integer") from exc
    ref = str(payload.get("ref") or "")
    if pos < 1 or not ref or ref in {".", "-"}:
        raise ValueError("a positive pos and non-empty REF allele are required")
    start0 = pos - 1
    return chrom, start0, start0 + max(1, len(ref))


class ScreenContextStore:
    """Thread-safe, lazily configured SCREEN matrix reader."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._manifest_path: Path | None = None
        self._configuration: dict[str, Any] | None = None

    def _configure(self, manifest_path: Path | None) -> dict[str, Any] | None:
        if manifest_path is None or not manifest_path.is_file():
            return None
        resolved = manifest_path.resolve()
        with self._lock:
            if self._manifest_path == resolved and self._configuration is not None:
                return self._configuration
            context_manifest = _read_json(resolved)
            prepared_path = Path(
                context_manifest.get("source_paths", {}).get("prepared_manifest", "")
            ).expanduser()
            prepared = _read_json(prepared_path)
            catalog_path = Path(prepared["catalog"]["path"]).expanduser()
            tissue_matrix = Path(prepared["tissue_matrix"]["path"]).expanduser()
            immune_matrix = Path(prepared["immune_matrix"]["path"]).expanduser()
            context_database = Path(
                context_manifest["artifacts"]["database"]["path"]
            ).expanduser()
            required = [catalog_path, tissue_matrix, immune_matrix, context_database]
            missing = [str(path) for path in required if not path.is_file()]
            if missing:
                raise ValueError("prepared SCREEN artifact is missing: " + ", ".join(missing))

            catalog = _open_ro(catalog_path)
            catalog.row_factory = sqlite3.Row
            tissues = [
                {
                    "id": f"tissue:{row['tissue_index']}",
                    "index": row["tissue_index"],
                    "name": row["name"],
                    "source_filename": row["source_filename"],
                }
                for row in catalog.execute("SELECT * FROM tissue ORDER BY tissue_index")
            ]
            catalog.close()

            context_db = _open_ro(context_database)
            context_db.row_factory = sqlite3.Row
            immune_contexts: list[dict[str, Any]] = []
            for row in context_db.execute(
                "SELECT * FROM context ORDER BY ontology_name, context_id"
            ):
                members = [
                    {
                        "profile_index": member["profile_index"],
                        "donor_accession": member["donor_accession"],
                        "evidence_tier": member["evidence_tier"],
                        "assays_available": _json_column(member["assays_available_json"]),
                        "audit_warnings": _json_column(member["audit_warnings_json"]),
                    }
                    for member in context_db.execute(
                        """SELECT cm.profile_index, cm.donor_accession,
                                  cm.evidence_tier, p.assays_available_json,
                                  p.audit_warnings_json
                           FROM context_member cm JOIN profile p USING(profile_index)
                           WHERE cm.context_id=? ORDER BY cm.profile_index""",
                        (row["context_id"],),
                    )
                ]
                hierarchy = _json_column(row["hierarchy_json"])
                immune_contexts.append({
                    "id": f"immune:{row['context_id']}",
                    "context_id": row["context_id"],
                    "ontology_id": row["ontology_id"],
                    "name": row["ontology_name"],
                    "lineages": _json_column(row["lineages_json"]),
                    "donor_count": row["donor_count"],
                    "tier_counts": _json_column(row["tier_counts_json"]),
                    "assay_capability": _json_column(row["assay_capability_json"]),
                    "audit_warning_counts": _json_column(row["audit_warnings_json"]),
                    **hierarchy,
                    "members": members,
                })
            context_db.close()
            self._manifest_path = resolved
            self._configuration = {
                "context_manifest": context_manifest,
                "prepared": prepared,
                "catalog_path": catalog_path,
                "tissue_matrix": tissue_matrix,
                "immune_matrix": immune_matrix,
                "tissues": tissues,
                "immune_contexts": immune_contexts,
            }
            return self._configuration

    @staticmethod
    def _catalog_connection(path: Path) -> sqlite3.Connection:
        connection = _open_ro(path)
        connection.row_factory = sqlite3.Row
        return connection

    def catalog(self, manifest_path: Path | None) -> dict[str, Any]:
        configured = self._configure(manifest_path)
        if configured is None:
            return {
                "available": False,
                "registry": "SCREEN Registry V4",
                "assembly": "GRCh38",
                "tissues": [],
                "immune_contexts": [],
                "presets": [],
                "message": "Prepared SCREEN tissue and immune context data are not installed.",
            }
        contexts = configured["immune_contexts"]
        public_contexts = [
            {key: value for key, value in context.items() if key != "members"}
            for context in contexts
        ]
        tissue_ids = {
            tissue["name"]: tissue["id"] for tissue in configured["tissues"]
        }
        immune_tissues = [
            tissue_ids[name] for name in (
                "blood", "bone marrow", "lymph node", "lymphoid tissue",
                "spleen", "thymus", "tonsil",
            )
            if name in tissue_ids
        ]
        broad_immune = [
            context["id"] for context in contexts
            if not context.get("is_nested_context")
        ]
        all_immune = [context["id"] for context in contexts]
        presets = [
            {"id": "immune-core", "name": "Immune core", "tissue_ids": immune_tissues, "immune_context_ids": broad_immune},
            {"id": "immune-all", "name": "Immune all", "tissue_ids": immune_tissues, "immune_context_ids": all_immune},
            {"id": "select-all", "name": "Select all", "tissue_ids": [item["id"] for item in configured["tissues"]], "immune_context_ids": all_immune},
        ]
        manifest = configured["context_manifest"]
        return {
            "available": True,
            "registry": manifest.get("registry", "SCREEN Registry V4"),
            "assembly": manifest.get("assembly", "GRCh38"),
            "created_at": manifest.get("created_at", ""),
            "tissues": configured["tissues"],
            "immune_contexts": public_contexts,
            "presets": presets,
            "scientific_caveats": [
                "Tissue aggregates and immune contexts are observed evidence, not target-gene assignments.",
                "A missing classification assay is unavailable evidence, not a negative result.",
                "Nested Cell Ontology contexts and shared donors are not statistically independent.",
            ],
        }

    @staticmethod
    def _overlaps_with_connection(
        connection: sqlite3.Connection, payload: dict[str, Any]
    ) -> list[dict[str, Any]]:
        chrom, start0, end0 = _variant_interval(payload)
        rows = connection.execute(
            """SELECT c.row_index, c.chrom, c.start, c.end,
                      c.ccre_accession, c.overall_class
               FROM ccre c JOIN contig USING(contig_id)
               WHERE contig.name=? AND c.start < ? AND c.end > ?
               ORDER BY c.start, c.end, c.ccre_accession""",
            (chrom, end0, start0),
        ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _overlaps(configured: dict[str, Any], payload: dict[str, Any]) -> list[dict[str, Any]]:
        connection = ScreenContextStore._catalog_connection(configured["catalog_path"])
        try:
            return ScreenContextStore._overlaps_with_connection(connection, payload)
        finally:
            connection.close()

    @staticmethod
    def _read_matrix_row(path: Path, columns: int, row_index: int) -> bytes:
        with path.open("rb") as handle:
            codes = ScreenContextStore._read_matrix_row_handle(
                handle, columns, row_index, path
            )
        return codes

    @staticmethod
    def _read_matrix_row_handle(handle, columns: int, row_index: int, path: Path) -> bytes:
        handle.seek(row_index * columns)
        codes = handle.read(columns)
        if len(codes) != columns:
            raise ValueError(f"incomplete SCREEN matrix row {row_index} in {path}")
        return codes

    @staticmethod
    def _state_from_counts(
        exact: Counter[str], capability: int | None = None
    ) -> tuple[str, str]:
        positive = sum(count for label, count in exact.items() if label != "inactive")
        if not positive:
            return "not_detected", "Not detected"
        families = {
            CLASS_FAMILIES[code]
            for code, label in CLASS_LABELS.items()
            if code and exact.get(label, 0) and CLASS_FAMILIES[code] != "accessibility"
        }
        if len(families) > 1:
            return "mixed", "Mixed classifications"
        if families:
            family = next(iter(families))
            labels = {
                "promoter": "Promoter-like",
                "enhancer": "Enhancer-like",
                "h3k4me3": "H3K4me3-associated",
                "ctcf": "CTCF-associated",
                "tf": "TF-associated",
            }
            state = "h3k4me3_associated" if family == "h3k4me3" else "classified"
            return state, labels[family]
        if capability == 0:
            return "accessible_classification_unavailable", "Accessible · classification unavailable"
        return "accessible_only", "Accessible only"

    def _tissue_evidence(self, configured: dict[str, Any], row_index: int) -> list[dict[str, Any]]:
        tissues = configured["tissues"]
        # Row stride must come from the stored matrix shape, not the
        # catalog row count: on a bundle/catalog skew every row after the
        # first would be read at the wrong offset (silent misalignment).
        tissue_columns = configured["prepared"]["tissue_matrix"]["shape"][1]
        codes = self._read_matrix_row(configured["tissue_matrix"], tissue_columns, row_index)
        result = []
        for tissue in tissues:
            code = codes[tissue["index"]]
            exact = Counter({CLASS_LABELS[code]: 1})
            state, label = self._state_from_counts(exact)
            result.append({
                **tissue,
                "state": state,
                "state_label": label,
                "activity_detected": code != 0,
                "class": CLASS_LABELS[code],
            })
        return result

    def _immune_evidence(self, configured: dict[str, Any], row_index: int) -> list[dict[str, Any]]:
        columns = configured["prepared"]["immune_matrix"]["shape"][1]
        codes = self._read_matrix_row(configured["immune_matrix"], columns, row_index)
        result = []
        for context in configured["immune_contexts"]:
            calls = []
            exact: Counter[str] = Counter()
            for member in context["members"]:
                code = codes[member["profile_index"]]
                class_label = CLASS_LABELS[code]
                exact[class_label] += 1
                calls.append({
                    "donor_accession": member["donor_accession"],
                    "evidence_tier": member["evidence_tier"],
                    "assays_available": member["assays_available"],
                    "class": class_label,
                    "detected": code != 0,
                })
            capability = int(context["assay_capability"].get("any_specific_classification", 0))
            state, label = self._state_from_counts(exact, capability)
            detected = [call for call in calls if call["detected"]]
            result.append({
                **{key: value for key, value in context.items() if key != "members"},
                "state": state,
                "state_label": label,
                "activity_detected": bool(detected),
                "donors_detected": len(detected),
                "classification_capable_donors": capability,
                "exact_class_counts": dict(sorted(exact.items())),
                "profile_calls": calls,
            })
        return result

    def evidence(self, manifest_path: Path | None, payload: dict[str, Any]) -> dict[str, Any]:
        configured = self._configure(manifest_path)
        if configured is None:
            return {"available": False, "status": "resource_unavailable", "overlaps": []}
        overlaps = []
        for overlap in self._overlaps(configured, payload):
            tissue = self._tissue_evidence(configured, overlap["row_index"])
            immune = self._immune_evidence(configured, overlap["row_index"])
            overlaps.append({
                "row_index": overlap["row_index"],
                "accession": overlap["ccre_accession"],
                "overall_class": overlap["overall_class"],
                "chrom": overlap["chrom"],
                "start": overlap["start"] + 1,
                "end": overlap["end"],
                "tissues": tissue,
                "immune_contexts": immune,
                "summary": {
                    "tissues_detected": sum(item["activity_detected"] for item in tissue),
                    "tissues_total": len(tissue),
                    "immune_contexts_detected": sum(item["activity_detected"] for item in immune),
                    "immune_contexts_total": len(immune),
                    "mixed_immune_contexts": sum(item["state"] == "mixed" for item in immune),
                },
            })
        manifest = configured["context_manifest"]
        return {
            "available": True,
            "status": "overlap" if overlaps else "no_overlap",
            "registry": manifest.get("registry", "SCREEN Registry V4"),
            "assembly": manifest.get("assembly", "GRCh38"),
            "overlaps": overlaps,
            "modules": {
                "observed_screen": {"available": True, "label": "SCREEN observed evidence"},
                "gene_links": {"available": False, "label": "Regulatory element–gene links", "note": "Planned for a future release"},
                "predictions": {"available": False, "label": "Gene-specific variant-effect prediction", "note": "Planned for a future release"},
            },
        }

    def filter_variants(self, manifest_path: Path | None, payload: dict[str, Any]) -> dict[str, Any]:
        configured = self._configure(manifest_path)
        variants = payload.get("variants")
        if not isinstance(variants, list):
            raise ValueError("variants must be a list")
        if len(variants) > 2_000:
            raise ValueError("at most 2000 variants may be screened per request")
        tissue_ids = {str(value) for value in payload.get("tissue_ids") or []}
        immune_ids = {str(value) for value in payload.get("immune_context_ids") or []}
        mode = str(payload.get("mode") or "any")
        if mode not in {"any", "all"}:
            raise ValueError("mode must be any or all")
        if configured is None:
            return {"available": False, "matching_keys": [], "tested": len(variants)}

        tissue_indices = {
            item["index"] for item in configured["tissues"] if item["id"] in tissue_ids
        }
        selected_contexts = [
            item for item in configured["immune_contexts"] if item["id"] in immune_ids
        ]
        expected = len(tissue_indices) + len(selected_contexts)
        if expected == 0:
            return {"available": True, "matching_keys": [], "tested": len(variants)}
        tissue_columns = len(configured["tissues"])
        immune_columns = configured["prepared"]["immune_matrix"]["shape"][1]
        matching: list[str] = []
        connection = self._catalog_connection(configured["catalog_path"])
        tissue_handle = configured["tissue_matrix"].open("rb") if tissue_indices else None
        immune_handle = configured["immune_matrix"].open("rb") if selected_contexts else None
        try:
            for variant in variants:
                key = str(variant.get("key") or "")
                passed_contexts: set[str] = set()
                for overlap in self._overlaps_with_connection(connection, variant):
                    if tissue_handle is not None:
                        codes = self._read_matrix_row_handle(
                            tissue_handle, tissue_columns, overlap["row_index"],
                            configured["tissue_matrix"],
                        )
                        passed_contexts.update(
                            f"tissue:{index}" for index in tissue_indices if codes[index] != 0
                        )
                    if immune_handle is not None:
                        codes = self._read_matrix_row_handle(
                            immune_handle, immune_columns, overlap["row_index"],
                            configured["immune_matrix"],
                        )
                        for context in selected_contexts:
                            if any(codes[member["profile_index"]] != 0 for member in context["members"]):
                                passed_contexts.add(context["id"])
                if (mode == "any" and passed_contexts) or (mode == "all" and len(passed_contexts) == expected):
                    matching.append(key)
        finally:
            connection.close()
            if tissue_handle is not None:
                tissue_handle.close()
            if immune_handle is not None:
                immune_handle.close()
        return {"available": True, "matching_keys": matching, "tested": len(variants)}
