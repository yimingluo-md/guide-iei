#!/usr/bin/env python3
"""Shared manifest contract for locally prepared, tabix-indexed predictors.

The contract deliberately separates the physical table from its biological
match dimensions.  A conventional predictor can require an allele, gene,
transcript, and/or protein change without adding another bespoke VEP plugin.
Predictors with genuinely different matching semantics can still use their own
adapter while publishing the same metric and provenance metadata.
"""

from __future__ import annotations

import json
import hashlib
import math
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable


MANIFEST_SCHEMA = "guide-iei.indexed-scores/v1"
SUPPORTED_MATCH_DIMENSIONS = {
    "allele",
    "ensembl_gene_id",
    "ensembl_transcript_id",
    "protein_change",
}
VCF_FIELD_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")
REGISTRY_TO_MANIFEST_TYPE = {
    "float": "number",
    "integer": "integer",
    "category": "string",
    "text": "string",
    "boolean": "boolean",
}
MANIFEST_DIRECTION_ALIASES = {
    "none": "none",
    "higher": "higher",
    "higher_is_more_functionally_damaging": "higher",
    "lower": "lower",
    "lower_is_more_functionally_damaging": "lower",
    "absolute": "absolute",
    "absolute_magnitude_is_more_functionally_damaging": "absolute",
}


class ManifestError(ValueError):
    """Raised when an indexed-score manifest cannot be used safely."""


def _required_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ManifestError(f"{label} must be an object")
    return value


def validate_manifest(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate and return an indexed-score manifest.

    This is intentionally strict because the Perl plugin consumes the same
    document at annotation time.  Validation here catches malformed manifests
    before a multi-hour VEP job starts.
    """

    if not isinstance(payload, dict):
        raise ManifestError("manifest root must be an object")
    if payload.get("manifest_schema") != MANIFEST_SCHEMA:
        raise ManifestError(
            f"manifest_schema must be {MANIFEST_SCHEMA!r}"
        )

    resource = _required_mapping(payload.get("resource"), "resource")
    for key in ("id", "name", "release"):
        if not isinstance(resource.get(key), str) or not resource[key].strip():
            raise ManifestError(f"resource.{key} must be a non-empty string")

    if payload.get("assembly") != "GRCh38":
        raise ManifestError("indexed predictor assembly must be GRCh38")

    applicability = payload.get("applicability")
    if applicability is not None:
        applicability = _required_mapping(applicability, "applicability")
        if set(applicability) != {"consequences"}:
            raise ManifestError("applicability supports only consequences")
        consequences = applicability.get("consequences")
        if not isinstance(consequences, list) or not consequences or not all(
            isinstance(item, str) and re.fullmatch(r"[a-z][a-z0-9_]+", item)
            for item in consequences
        ):
            raise ManifestError(
                "applicability.consequences must be a non-empty SO-term list"
            )
        if len(consequences) != len(set(consequences)):
            raise ManifestError("applicability.consequences contains duplicates")

    table = _required_mapping(payload.get("table"), "table")
    columns = table.get("columns")
    if not isinstance(columns, list) or not columns or not all(
        isinstance(item, str) and item for item in columns
    ):
        raise ManifestError("table.columns must be a non-empty string list")
    if len(columns) != len(set(columns)):
        raise ManifestError("table.columns contains duplicate names")
    column_set = set(columns)

    match = _required_mapping(payload.get("match"), "match")
    required = match.get("required")
    if not isinstance(required, list) or not required:
        raise ManifestError("match.required must be a non-empty list")
    if required[0] != "allele" or "allele" not in required:
        raise ManifestError("match.required must begin with allele")
    unknown = set(required) - SUPPORTED_MATCH_DIMENSIONS
    if unknown:
        raise ManifestError(
            "unsupported match dimension(s): " + ", ".join(sorted(unknown))
        )
    if len(required) != len(set(required)):
        raise ManifestError("match.required contains duplicates")

    dimensions = _required_mapping(match.get("dimensions"), "match.dimensions")
    required_columns = []
    allele = _required_mapping(dimensions.get("allele"), "match.dimensions.allele")
    for key in ("chrom", "position", "reference", "alternate"):
        column = allele.get(key)
        if not isinstance(column, str) or column not in column_set:
            raise ManifestError(
                f"match.dimensions.allele.{key} must name a table column"
            )
        required_columns.append(column)
    allele_normalization = allele.get("normalization")
    if allele_normalization not in {None, "vep_matched_variant_alleles"}:
        raise ManifestError("unsupported allele normalization")

    for dimension in required[1:]:
        definition = _required_mapping(
            dimensions.get(dimension), f"match.dimensions.{dimension}"
        )
        column = definition.get("column")
        if not isinstance(column, str) or column not in column_set:
            raise ManifestError(
                f"match.dimensions.{dimension}.column must name a table column"
            )
        required_columns.append(column)
        normalization = definition.get("normalization")
        if normalization not in {None, "exact", "strip_version", "one_letter_protein_change"}:
            raise ManifestError(
                f"unsupported normalization for match dimension {dimension}"
            )

    outputs = payload.get("outputs")
    if not isinstance(outputs, list) or not outputs:
        raise ManifestError("outputs must be a non-empty list")
    output_ids: set[str] = set()
    for index, output in enumerate(outputs):
        output = _required_mapping(output, f"outputs[{index}]")
        field_id = output.get("id")
        column = output.get("column")
        if not isinstance(field_id, str) or not VCF_FIELD_RE.fullmatch(field_id):
            raise ManifestError(f"outputs[{index}].id is not a valid VCF field ID")
        if field_id in output_ids:
            raise ManifestError(f"duplicate output field ID: {field_id}")
        output_ids.add(field_id)
        if not isinstance(column, str) or column not in column_set:
            raise ManifestError(f"outputs[{index}].column must name a table column")
        if output.get("type") not in {"number", "integer", "string", "boolean"}:
            raise ManifestError(f"outputs[{index}].type is unsupported")
        if not isinstance(output.get("description"), str) or not output["description"]:
            raise ManifestError(f"outputs[{index}].description is required")
        minimum = output.get("minimum")
        maximum = output.get("maximum")
        if minimum is not None or maximum is not None:
            if output.get("type") not in {"number", "integer"}:
                raise ManifestError(f"outputs[{index}] ranges require a numeric type")
            if not all(
                isinstance(value, (int, float)) and not isinstance(value, bool)
                for value in (minimum, maximum)
                if value is not None
            ):
                raise ManifestError(f"outputs[{index}] range values must be numeric")
            if minimum is not None and maximum is not None and minimum > maximum:
                raise ManifestError(f"outputs[{index}] minimum exceeds maximum")
        binary_classification = output.get("binary_classification")
        if binary_classification is not None:
            binary_classification = _required_mapping(
                binary_classification, f"outputs[{index}].binary_classification"
            )
            expected_keys = {
                "threshold", "comparison", "positive_label", "negative_label",
                "threshold_set", "source_url",
            }
            if set(binary_classification) != expected_keys:
                raise ManifestError(
                    f"outputs[{index}].binary_classification must contain exactly "
                    + ", ".join(sorted(expected_keys))
                )
            threshold = binary_classification["threshold"]
            if (
                output.get("type") not in {"number", "integer"}
                or isinstance(threshold, bool)
                or not isinstance(threshold, (int, float))
                or not math.isfinite(threshold)
            ):
                raise ManifestError(
                    f"outputs[{index}].binary_classification.threshold must be finite and numeric"
                )
            if minimum is not None and threshold < minimum:
                raise ManifestError(
                    f"outputs[{index}].binary_classification.threshold is below minimum"
                )
            if maximum is not None and threshold > maximum:
                raise ManifestError(
                    f"outputs[{index}].binary_classification.threshold is above maximum"
                )
            if binary_classification["comparison"] not in {
                "greater_than_or_equal", "less_than_or_equal",
                "absolute_greater_than_or_equal",
            }:
                raise ManifestError(
                    f"outputs[{index}].binary_classification.comparison is unsupported"
                )
            for key in ("positive_label", "negative_label", "threshold_set"):
                if not isinstance(binary_classification[key], str) or not binary_classification[key].strip():
                    raise ManifestError(
                        f"outputs[{index}].binary_classification.{key} is required"
                    )
            source_url = binary_classification["source_url"]
            if not isinstance(source_url, str) or not source_url.startswith("https://"):
                raise ManifestError(
                    f"outputs[{index}].binary_classification.source_url must use https"
                )

    provenance = _required_mapping(payload.get("provenance"), "provenance")
    provenance_ids: set[str] = set()
    for key in ("match", "match_status", "source_target", "allele_available"):
        value = provenance.get(key)
        if not isinstance(value, str) or not VCF_FIELD_RE.fullmatch(value):
            raise ManifestError(f"provenance.{key} is not a valid VCF field ID")
        if value in output_ids:
            raise ManifestError(f"provenance field conflicts with output: {value}")
        if value in provenance_ids:
            raise ManifestError(f"duplicate provenance field ID: {value}")
        provenance_ids.add(value)

    files = payload.get("files")
    if files is not None:
        files = _required_mapping(files, "files")
        for role in ("data", "index"):
            metadata = _required_mapping(files.get(role), f"files.{role}")
            if not isinstance(metadata.get("name"), str) or not metadata["name"]:
                raise ManifestError(f"files.{role}.name is required")
            size = metadata.get("size")
            if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
                raise ManifestError(f"files.{role}.size must be a positive integer")
            mtime_ns = metadata.get("mtime_ns")
            if mtime_ns is not None and (
                not isinstance(mtime_ns, int) or isinstance(mtime_ns, bool) or mtime_ns <= 0
            ):
                raise ManifestError(f"files.{role}.mtime_ns must be a positive integer")
            checksum = metadata.get("sha256")
            if not isinstance(checksum, str) or not re.fullmatch(r"[0-9a-f]{64}", checksum):
                raise ManifestError(f"files.{role}.sha256 must be lowercase SHA-256")

    # All parse-critical columns must be distinct. An output may deliberately
    # reuse no match column because doing so makes source provenance ambiguous.
    if len(required_columns) != len(set(required_columns)):
        raise ManifestError("match dimensions must use distinct table columns")
    return payload


def _enum_value(value: Any) -> str:
    """Return the wire value for a registry enum without importing its module."""

    return str(getattr(value, "value", value))


def _range_label(value: tuple[Any, Any] | None) -> str:
    if value is None:
        return "unbounded"
    lower = "unbounded" if value[0] is None else repr(value[0])
    upper = "unbounded" if value[1] is None else repr(value[1])
    return f"[{lower}, {upper}]"


def validate_manifest_registry_contract(
    payload: dict[str, Any],
    predictors: Iterable[Any],
    *,
    resource: Any | None = None,
    annotator: Any | None = None,
) -> dict[str, Any]:
    """Require an indexed manifest to agree with its registry metrics.

    ``validate_manifest`` establishes the standalone file contract. This
    second layer is intentionally registry-aware and is called while building
    the VEP command and while checking an installation. ``predictors`` are the
    registry predictor definitions attached to one generic annotator. Passing
    that annotator and its resource also validates identity, assembly, license,
    match dimensions, and applicability without resource-specific field lists.
    """

    validate_manifest(payload)
    predictors = tuple(predictors)
    metrics_by_field: dict[str, tuple[Any, Any]] = {}
    for predictor in predictors:
        for metric in predictor.metrics:
            previous = metrics_by_field.get(metric.field)
            if previous is not None:
                raise ManifestError(
                    f"predictor registry field {metric.field!r} is ambiguous between "
                    f"{previous[0].id}.{previous[1].id} and {predictor.id}.{metric.id}"
                )
            metrics_by_field[metric.field] = (predictor, metric)

    outputs = {output["id"]: output for output in payload["outputs"]}
    provenance = payload["provenance"]
    if not all(isinstance(field, str) for field in provenance.values()):
        raise ManifestError("manifest provenance field IDs must be strings")
    declared_fields = set(outputs) | set(provenance.values())
    expected_fields = set(metrics_by_field)
    if declared_fields != expected_fields:
        missing = sorted(expected_fields - declared_fields)
        unexpected = sorted(declared_fields - expected_fields)
        details = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if unexpected:
            details.append("unexpected " + ", ".join(unexpected))
        raise ManifestError(
            "declared output/provenance fields do not match the predictor "
            "registry (" + "; ".join(details) + ")"
        )

    for field, output in outputs.items():
        predictor, metric = metrics_by_field[field]
        metric_name = f"{predictor.id}.{metric.id}"
        registry_type = _enum_value(metric.value_type)
        expected_type = REGISTRY_TO_MANIFEST_TYPE.get(registry_type)
        if expected_type is None:
            raise ManifestError(
                f"registry metric {metric_name!r} has unsupported value type "
                f"{registry_type!r} for an indexed manifest"
            )
        observed_type = output["type"]
        if observed_type != expected_type:
            raise ManifestError(
                f"manifest output {field!r} type {observed_type!r} does not "
                f"match registry metric {metric_name!r} value_type "
                f"{registry_type!r} (manifest type {expected_type!r})"
            )

        minimum = output.get("minimum")
        maximum = output.get("maximum")
        observed_range = (
            None if minimum is None and maximum is None else (minimum, maximum)
        )
        expected_range = (
            tuple(metric.value_range) if metric.value_range is not None else None
        )
        if observed_range != expected_range:
            raise ManifestError(
                f"manifest output {field!r} range {_range_label(observed_range)} "
                f"does not match registry metric {metric_name!r} range "
                f"{_range_label(expected_range)}"
            )

        raw_direction = output.get("direction")
        if raw_direction is None:
            observed_direction = "none"
        elif not isinstance(raw_direction, str) or (
            observed_direction := MANIFEST_DIRECTION_ALIASES.get(raw_direction)
        ) is None:
            raise ManifestError(
                f"manifest output {field!r} direction {raw_direction!r} is unsupported"
            )
        expected_direction = _enum_value(metric.direction)
        if observed_direction != expected_direction:
            raise ManifestError(
                f"manifest output {field!r} direction {observed_direction!r} "
                f"does not match registry metric {metric_name!r} direction "
                f"{expected_direction!r}"
            )

        if _enum_value(metric.role) == "provenance":
            raise ManifestError(
                f"manifest output {field!r} maps to provenance registry metric "
                f"{metric_name!r}; declare it in manifest provenance instead"
            )

        expected_binary = metric.binary_classification
        observed_binary = output.get("binary_classification")
        if expected_binary is None:
            if observed_binary is not None:
                raise ManifestError(
                    f"manifest output {field!r} has an unexpected binary classification"
                )
        else:
            expected_binary_payload = {
                "threshold": expected_binary.threshold,
                "comparison": _enum_value(expected_binary.comparison),
                "positive_label": expected_binary.positive_label,
                "negative_label": expected_binary.negative_label,
                "threshold_set": expected_binary.threshold_set,
                "source_url": expected_binary.source_url,
            }
            # Manifests created before binary classification metadata was
            # introduced remain valid: the application derives the label from
            # the current registry. If a manifest does declare a threshold,
            # however, it must agree exactly so stale cutoffs cannot masquerade
            # as the active contract.
            if observed_binary is not None and observed_binary != expected_binary_payload:
                raise ManifestError(
                    f"manifest output {field!r} binary classification does not "
                    f"match registry metric {metric_name!r}"
                )

    provenance_contracts = {
        "match": ("match", {"category", "text"}, "provenance"),
        "match_status": ("match_status", {"category", "text"}, "provenance"),
        "source_target": (None, {"category", "text"}, "provenance"),
        "allele_available": ("allele_available", {"boolean"}, "flag"),
    }
    for semantic, (expected_id, expected_types, expected_role) in (
        provenance_contracts.items()
    ):
        field = provenance[semantic]
        predictor, metric = metrics_by_field[field]
        metric_name = f"{predictor.id}.{metric.id}"
        metric_type = _enum_value(metric.value_type)
        metric_role = _enum_value(metric.role)
        id_matches = (
            metric.id == expected_id
            if expected_id is not None
            else metric.id.startswith("source_")
        )
        if (
            not id_matches
            or metric_type not in expected_types
            or metric_role != expected_role
        ):
            expected_id_label = (
                repr(expected_id) if expected_id is not None else "a source_* metric"
            )
            raise ManifestError(
                f"manifest provenance.{semantic} field {field!r} maps to registry "
                f"metric {metric_name!r} (id {metric.id!r}, type {metric_type!r}, "
                f"role {metric_role!r}); expected "
                f"id {expected_id_label}, type {'/'.join(sorted(expected_types))}, "
                f"role {expected_role!r}"
            )

    if resource is not None:
        if payload["resource"]["id"] != resource.id:
            raise ManifestError(f"resource.id must be {resource.id}")
        if resource.assembly is not None and payload.get("assembly") != resource.assembly:
            raise ManifestError(
                f"manifest assembly must be {resource.assembly} for {resource.id}"
            )
        if resource.license_ack_required and not (
            payload.get("license") or {}
        ).get("acknowledged_by_user"):
            raise ManifestError("license acknowledgment is missing")

    if annotator is not None:
        required_by_scope = {
            "allele": ["allele"],
            "allele_gene": ["allele", "ensembl_gene_id"],
            "allele_transcript": ["allele", "ensembl_transcript_id"],
            "allele_transcript_protein": [
                "allele", "ensembl_transcript_id", "protein_change",
            ],
        }
        expected_required = required_by_scope.get(_enum_value(annotator.match.scope))
        if (
            expected_required is None
            or payload["match"]["required"] != expected_required
        ):
            raise ManifestError(
                "manifest match dimensions do not match the predictor registry"
            )
        expected_consequences = sorted({
            item
            for predictor in predictors
            for item in predictor.applicability
            if item.endswith("_variant")
        })
        observed_consequences = sorted(
            (payload.get("applicability") or {}).get("consequences") or []
        )
        if observed_consequences != expected_consequences:
            raise ManifestError(
                "manifest applicability does not match the predictor registry"
            )

    return payload


def load_manifest(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ManifestError(f"cannot read indexed-score manifest {path}: {exc}") from exc
    return validate_manifest(payload)


@lru_cache(maxsize=32)
def _sha256_file_identity(
    path_text: str,
    device: int,
    inode: int,
    size: int,
    mtime_ns: int,
    ctime_ns: int,
) -> str:
    # The stat identity is deliberately part of the cache key. A content
    # replacement invalidates the entry even when its path and size stay the
    # same; successful timestamp-only copies are not re-hashed on every UI
    # status poll.
    del device, inode, size, mtime_ns, ctime_ns
    digest = hashlib.sha256()
    with Path(path_text).open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_file(path: Path, status: os.stat_result) -> str:
    return _sha256_file_identity(
        str(path.resolve()),
        status.st_dev,
        status.st_ino,
        status.st_size,
        status.st_mtime_ns,
        status.st_ctime_ns,
    )


def validate_manifest_files(
    payload: dict[str, Any],
    data_path: Path,
    index_path: Path,
) -> None:
    """Validate the installed data/index pair described by a manifest.

    File names and sizes are always checked. ``mtime_ns`` is an optional fast
    identity hint: manifests without it remain valid, while a copied file with
    a different timestamp is accepted only after its SHA-256 still matches.
    This avoids both false failures after a metadata-only copy and repeated
    multi-gigabyte hashing during ordinary status polling.
    """

    validate_manifest(payload)
    files = payload.get("files")
    if not isinstance(files, dict):
        raise ManifestError("files metadata is required")
    for role, path in (("data", data_path), ("index", index_path)):
        metadata = files.get(role)
        if not isinstance(metadata, dict):
            raise ManifestError(f"files.{role} metadata is required")
        if not path.is_file():
            raise ManifestError(f"installed {role} file is missing: {path}")
        status = path.stat()
        if metadata.get("name") != path.name:
            raise ManifestError(
                f"files.{role}.name does not match installed file {path.name}"
            )
        if metadata.get("size") != status.st_size:
            raise ManifestError(
                f"files.{role}.size does not match installed file {path.name}"
            )
        recorded_mtime = metadata.get("mtime_ns")
        if recorded_mtime is not None and recorded_mtime != status.st_mtime_ns:
            if _sha256_file(path, status) != metadata["sha256"]:
                raise ManifestError(
                    f"files.{role}.sha256 does not match installed file {path.name}"
                )


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    """Write JSON through a same-directory temporary and atomic rename."""

    validate_manifest(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
