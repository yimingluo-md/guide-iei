#!/usr/bin/env python3
"""Typed, side-effect-free access to the annotation predictor registry.

The registry is deliberately descriptive: importing this module or loading the
default registry never enables a dataset, installs a resource, changes a job
configuration, or alters annotation output.  Runtime components can migrate to
it incrementally while the existing configuration remains authoritative.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from enum import Enum
from functools import cached_property
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, Sequence


REGISTRY_PATH = Path(__file__).resolve().parents[1] / "config" / "predictor-registry.json"

_ID_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")
_CONFIG_PATH_PATTERN = re.compile(
    r"^(?:core|reference|plugins|custom_tracks|post_processing|clingen_erepo|genia|"
    r"wgs_review|liftover)(?:\.[A-Za-z][A-Za-z0-9_]*)*$"
)
_FIELD_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.+\-]*$")
_CONFIG_ROOTS = {
    "core", "reference", "plugins", "custom_tracks", "post_processing",
    "clingen_erepo", "genia", "wgs_review", "liftover",
}


class RegistryError(ValueError):
    """Raised when a registry document violates its schema or references."""


class ResourceFamily(str, Enum):
    RUNTIME = "runtime"
    PREDICTION_DATABASE = "prediction_database"
    PREDICTOR_ASSETS = "predictor_assets"
    REGIONAL_CONTEXT = "regional_context"
    CLINICAL_EVIDENCE = "clinical_evidence"
    DERIVED_ANNOTATION = "derived_annotation"
    INPUT_TRANSFORM = "input_transform"


class Distribution(str, Enum):
    BUNDLED_RUNTIME = "bundled_runtime"
    AUTOMATIC = "automatic"
    PUBLIC_OPTIONAL = "public_optional"
    USER_SUPPLIED = "user_supplied"
    LICENSE_ACKNOWLEDGED_DOWNLOAD = "license_acknowledged_download"
    PER_RUN = "per_run"
    DERIVED = "derived"


class Adapter(str, Enum):
    VEP_BUILTIN = "vep_builtin"
    VEP_PLUGIN = "vep_plugin"
    GENERIC_INDEXED_LOOKUP = "generic_indexed_lookup"
    VEP_CUSTOM_TRACK = "vep_custom_track"
    POSTPROCESSOR = "postprocessor"
    SERVICE_LOOKUP = "service_lookup"


class MatchScope(str, Enum):
    NONE = "none"
    ALLELE = "allele"
    ALLELE_GENE = "allele_gene"
    ALLELE_GENE_SYMBOL = "allele_gene_symbol"
    ALLELE_TRANSCRIPT = "allele_transcript"
    ALLELE_TRANSCRIPT_PROTEIN = "allele_transcript_protein"
    ALLELE_TRANSCRIPT_TSS_STRAND = "allele_transcript_tss_strand"
    INTERVAL = "interval"
    TRANSCRIPT_CONSEQUENCE = "transcript_consequence"
    GENE_PROTEIN_RESIDUE = "gene_protein_residue"
    SAMPLE_HAPLOTYPE = "sample_haplotype"


class MatchDimension(str, Enum):
    CHROMOSOME = "chromosome"
    POSITION = "position"
    START = "start"
    END = "end"
    REFERENCE = "reference"
    ALTERNATE = "alternate"
    ENSEMBL_GENE = "ensembl_gene"
    GENE_SYMBOL = "gene_symbol"
    ENSEMBL_TRANSCRIPT = "ensembl_transcript"
    CONSEQUENCE = "consequence"
    PROTEIN_POSITION = "protein_position"
    AMINO_ACID_CHANGE = "amino_acid_change"
    TSS = "tss"
    STRAND = "strand"
    SAMPLE = "sample"
    PHASE_SET = "phase_set"


class TranscriptVersionPolicy(str, Enum):
    NOT_APPLICABLE = "not_applicable"
    EXACT = "exact"
    STABLE_ID = "stable_id"
    EXACT_THEN_STABLE_ID = "exact_then_stable_id"


class MatchFallback(str, Enum):
    NONE = "none"
    REPORT_ALLELE_ONLY = "report_allele_only"
    STABLE_TRANSCRIPT_ID = "stable_transcript_id"


class Cardinality(str, Enum):
    ZERO_OR_ONE = "zero_or_one"
    ZERO_OR_MANY = "zero_or_many"
    ONE_OR_MANY = "one_or_many"


class MetricType(str, Enum):
    FLOAT = "float"
    INTEGER = "integer"
    CATEGORY = "category"
    BOOLEAN = "boolean"
    TEXT = "text"


class ScoreDirection(str, Enum):
    HIGHER = "higher"
    LOWER = "lower"
    ABSOLUTE = "absolute"
    NONE = "none"


class BinaryComparison(str, Enum):
    GREATER_THAN_OR_EQUAL = "greater_than_or_equal"
    LESS_THAN_OR_EQUAL = "less_than_or_equal"
    ABSOLUTE_GREATER_THAN_OR_EQUAL = "absolute_greater_than_or_equal"


class MetricRole(str, Enum):
    SCORE = "score"
    PROBABILITY = "probability"
    PREDICTION = "prediction"
    CLASSIFICATION = "classification"
    FLAG = "flag"
    COUNT = "count"
    COORDINATE = "coordinate"
    DISTANCE = "distance"
    PROVENANCE = "provenance"


_SCOPE_DIMENSIONS: Mapping[MatchScope, frozenset[MatchDimension]] = {
    MatchScope.NONE: frozenset(),
    MatchScope.ALLELE: frozenset({
        MatchDimension.CHROMOSOME, MatchDimension.POSITION,
        MatchDimension.REFERENCE, MatchDimension.ALTERNATE,
    }),
    MatchScope.ALLELE_GENE: frozenset({
        MatchDimension.CHROMOSOME, MatchDimension.POSITION,
        MatchDimension.REFERENCE, MatchDimension.ALTERNATE,
        MatchDimension.ENSEMBL_GENE,
    }),
    MatchScope.ALLELE_GENE_SYMBOL: frozenset({
        MatchDimension.CHROMOSOME, MatchDimension.POSITION,
        MatchDimension.REFERENCE, MatchDimension.ALTERNATE,
        MatchDimension.GENE_SYMBOL,
    }),
    MatchScope.ALLELE_TRANSCRIPT: frozenset({
        MatchDimension.CHROMOSOME, MatchDimension.POSITION,
        MatchDimension.REFERENCE, MatchDimension.ALTERNATE,
        MatchDimension.ENSEMBL_TRANSCRIPT,
    }),
    MatchScope.ALLELE_TRANSCRIPT_PROTEIN: frozenset({
        MatchDimension.CHROMOSOME, MatchDimension.POSITION,
        MatchDimension.REFERENCE, MatchDimension.ALTERNATE,
        MatchDimension.ENSEMBL_TRANSCRIPT, MatchDimension.PROTEIN_POSITION,
        MatchDimension.AMINO_ACID_CHANGE,
    }),
    MatchScope.ALLELE_TRANSCRIPT_TSS_STRAND: frozenset({
        MatchDimension.CHROMOSOME, MatchDimension.POSITION,
        MatchDimension.REFERENCE, MatchDimension.ALTERNATE,
        MatchDimension.ENSEMBL_TRANSCRIPT, MatchDimension.TSS,
        MatchDimension.STRAND,
    }),
    MatchScope.INTERVAL: frozenset({
        MatchDimension.CHROMOSOME, MatchDimension.START, MatchDimension.END,
    }),
    MatchScope.TRANSCRIPT_CONSEQUENCE: frozenset({
        MatchDimension.CHROMOSOME, MatchDimension.POSITION,
        MatchDimension.REFERENCE, MatchDimension.ALTERNATE,
        MatchDimension.ENSEMBL_TRANSCRIPT, MatchDimension.CONSEQUENCE,
    }),
    MatchScope.GENE_PROTEIN_RESIDUE: frozenset({
        MatchDimension.GENE_SYMBOL, MatchDimension.PROTEIN_POSITION,
    }),
    MatchScope.SAMPLE_HAPLOTYPE: frozenset({
        MatchDimension.SAMPLE, MatchDimension.CHROMOSOME,
        MatchDimension.POSITION, MatchDimension.REFERENCE,
        MatchDimension.ALTERNATE, MatchDimension.PHASE_SET,
        MatchDimension.ENSEMBL_TRANSCRIPT,
    }),
}


@dataclass(frozen=True)
class ResourceAsset:
    id: str
    config_path: str
    role: str
    required: bool
    indexed: bool


@dataclass(frozen=True)
class ResourceDefinition:
    id: str
    label: str
    family: ResourceFamily
    config_path: str
    assembly: str | None
    distribution: Distribution
    default_enabled: bool
    license_ack_required: bool
    license_name: str
    source_url: str | None
    assets: tuple[ResourceAsset, ...]


@dataclass(frozen=True)
class MatchDefinition:
    scope: MatchScope
    dimensions: tuple[MatchDimension, ...]
    assembly: str | None
    normalize_alleles: bool
    normalize_contigs: bool
    transcript_version: TranscriptVersionPolicy
    fallback: MatchFallback
    cardinality: Cardinality


@dataclass(frozen=True)
class AnnotatorDefinition:
    id: str
    label: str
    resource_id: str
    config_path: str
    adapter: Adapter
    implementation: str
    match: MatchDefinition


@dataclass(frozen=True)
class MetricDefinition:
    id: str
    field: str
    value_type: MetricType
    direction: ScoreDirection
    role: MetricRole
    value_range: tuple[float, float] | None
    derived: bool
    filterable: bool
    binary_classification: BinaryClassificationDefinition | None


@dataclass(frozen=True)
class BinaryClassificationDefinition:
    threshold: float
    comparison: BinaryComparison
    positive_label: str
    negative_label: str
    threshold_set: str
    source_url: str


@dataclass(frozen=True)
class PredictorDefinition:
    id: str
    label: str
    category: str
    resource_id: str
    annotator_id: str
    applicability: tuple[str, ...]
    default_enabled: bool
    optional: bool
    metrics: tuple[MetricDefinition, ...]


@dataclass(frozen=True)
class PredictorRegistry:
    schema_version: int
    resources: tuple[ResourceDefinition, ...]
    annotators: tuple[AnnotatorDefinition, ...]
    predictors: tuple[PredictorDefinition, ...]

    @cached_property
    def resources_by_id(self) -> Mapping[str, ResourceDefinition]:
        return MappingProxyType({item.id: item for item in self.resources})

    @cached_property
    def annotators_by_id(self) -> Mapping[str, AnnotatorDefinition]:
        return MappingProxyType({item.id: item for item in self.annotators})

    @cached_property
    def predictors_by_id(self) -> Mapping[str, PredictorDefinition]:
        return MappingProxyType({item.id: item for item in self.predictors})

    def resource(self, resource_id: str) -> ResourceDefinition:
        try:
            return self.resources_by_id[resource_id]
        except KeyError as exc:
            raise KeyError(f"unknown resource: {resource_id}") from exc

    def annotator(self, annotator_id: str) -> AnnotatorDefinition:
        try:
            return self.annotators_by_id[annotator_id]
        except KeyError as exc:
            raise KeyError(f"unknown annotator: {annotator_id}") from exc

    def predictor(self, predictor_id: str) -> PredictorDefinition:
        try:
            return self.predictors_by_id[predictor_id]
        except KeyError as exc:
            raise KeyError(f"unknown predictor: {predictor_id}") from exc


def _mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise RegistryError(f"{path} must be an object")
    return value


def _sequence(value: Any, path: str) -> Sequence[Any]:
    if not isinstance(value, list):
        raise RegistryError(f"{path} must be an array")
    return value


def _string(value: Any, path: str, *, nonempty: bool = True) -> str:
    if not isinstance(value, str) or (nonempty and not value.strip()):
        raise RegistryError(f"{path} must be a non-empty string")
    return value


def _boolean(value: Any, path: str) -> bool:
    if not isinstance(value, bool):
        raise RegistryError(f"{path} must be a boolean")
    return value


def _identifier(value: Any, path: str) -> str:
    result = _string(value, path)
    if not _ID_PATTERN.fullmatch(result):
        raise RegistryError(f"{path} must be a lower_snake_case identifier")
    return result


def _config_path(value: Any, path: str) -> str:
    result = _string(value, path)
    if not _CONFIG_PATH_PATTERN.fullmatch(result):
        roots = ", ".join(sorted(_CONFIG_ROOTS))
        raise RegistryError(f"{path} is not a valid config path (root must be one of: {roots})")
    return result


def _enum(enum_type: type[Enum], value: Any, path: str):
    try:
        return enum_type(value)
    except (TypeError, ValueError) as exc:
        allowed = ", ".join(item.value for item in enum_type)
        raise RegistryError(f"{path} must be one of: {allowed}") from exc


def _keys(document: Mapping[str, Any], required: set[str], optional: set[str], path: str) -> None:
    missing = required - set(document)
    unknown = set(document) - required - optional
    if missing:
        raise RegistryError(f"{path} is missing: {', '.join(sorted(missing))}")
    if unknown:
        raise RegistryError(f"{path} has unknown keys: {', '.join(sorted(unknown))}")


def _unique(items: Sequence[Any], key: str, path: str) -> None:
    seen: set[Any] = set()
    for index, item in enumerate(items):
        obj = _mapping(item, f"{path}[{index}]")
        value = obj.get(key)
        if value in seen:
            raise RegistryError(f"{path} contains duplicate {key}: {value}")
        seen.add(value)


def _parse_asset(raw: Any, path: str, resource_config_path: str) -> ResourceAsset:
    obj = _mapping(raw, path)
    _keys(obj, {"id", "config_path", "role", "required", "indexed"}, set(), path)
    config_path = _config_path(obj["config_path"], f"{path}.config_path")
    if not config_path.startswith(resource_config_path + "."):
        raise RegistryError(
            f"{path}.config_path must be below resource config path {resource_config_path}"
        )
    return ResourceAsset(
        id=_identifier(obj["id"], f"{path}.id"),
        config_path=config_path,
        role=_identifier(obj["role"], f"{path}.role"),
        required=_boolean(obj["required"], f"{path}.required"),
        indexed=_boolean(obj["indexed"], f"{path}.indexed"),
    )


def _parse_resource(raw: Any, path: str) -> ResourceDefinition:
    obj = _mapping(raw, path)
    required = {
        "id", "label", "family", "config_path", "assembly", "distribution",
        "default_enabled", "license_ack_required", "license_name", "source_url", "assets",
    }
    _keys(obj, required, set(), path)
    config_path = _config_path(obj["config_path"], f"{path}.config_path")
    assembly = obj["assembly"]
    if assembly is not None:
        assembly = _string(assembly, f"{path}.assembly")
    source_url = obj["source_url"]
    if source_url is not None:
        source_url = _string(source_url, f"{path}.source_url")
        if not source_url.startswith("https://"):
            raise RegistryError(f"{path}.source_url must use https")
    assets_raw = _sequence(obj["assets"], f"{path}.assets")
    _unique(assets_raw, "id", f"{path}.assets")
    assets = tuple(
        _parse_asset(item, f"{path}.assets[{index}]", config_path)
        for index, item in enumerate(assets_raw)
    )
    return ResourceDefinition(
        id=_identifier(obj["id"], f"{path}.id"),
        label=_string(obj["label"], f"{path}.label"),
        family=_enum(ResourceFamily, obj["family"], f"{path}.family"),
        config_path=config_path,
        assembly=assembly,
        distribution=_enum(Distribution, obj["distribution"], f"{path}.distribution"),
        default_enabled=_boolean(obj["default_enabled"], f"{path}.default_enabled"),
        license_ack_required=_boolean(
            obj["license_ack_required"], f"{path}.license_ack_required"
        ),
        license_name=_string(obj["license_name"], f"{path}.license_name"),
        source_url=source_url,
        assets=assets,
    )


def _parse_match(raw: Any, path: str) -> MatchDefinition:
    obj = _mapping(raw, path)
    required = {
        "scope", "dimensions", "assembly", "normalize_alleles", "normalize_contigs",
        "transcript_version", "fallback", "cardinality",
    }
    _keys(obj, required, set(), path)
    scope = _enum(MatchScope, obj["scope"], f"{path}.scope")
    dimensions_raw = _sequence(obj["dimensions"], f"{path}.dimensions")
    dimensions = tuple(
        _enum(MatchDimension, value, f"{path}.dimensions[{index}]")
        for index, value in enumerate(dimensions_raw)
    )
    if len(set(dimensions)) != len(dimensions):
        raise RegistryError(f"{path}.dimensions contains duplicates")
    if frozenset(dimensions) != _SCOPE_DIMENSIONS[scope]:
        expected = ", ".join(sorted(item.value for item in _SCOPE_DIMENSIONS[scope])) or "(none)"
        raise RegistryError(f"{path}.dimensions do not match scope {scope.value}; expected {expected}")
    assembly = obj["assembly"]
    if assembly is not None:
        assembly = _string(assembly, f"{path}.assembly")
    transcript_version = _enum(
        TranscriptVersionPolicy, obj["transcript_version"], f"{path}.transcript_version"
    )
    transcript_scopes = {
        MatchScope.ALLELE_TRANSCRIPT, MatchScope.ALLELE_TRANSCRIPT_PROTEIN,
        MatchScope.ALLELE_TRANSCRIPT_TSS_STRAND, MatchScope.TRANSCRIPT_CONSEQUENCE,
        MatchScope.SAMPLE_HAPLOTYPE,
    }
    if scope not in transcript_scopes and transcript_version is not TranscriptVersionPolicy.NOT_APPLICABLE:
        raise RegistryError(f"{path}.transcript_version is only valid for transcript scopes")
    fallback = _enum(MatchFallback, obj["fallback"], f"{path}.fallback")
    if fallback is MatchFallback.STABLE_TRANSCRIPT_ID and scope not in transcript_scopes:
        raise RegistryError(f"{path}.fallback stable_transcript_id requires a transcript scope")
    return MatchDefinition(
        scope=scope,
        dimensions=dimensions,
        assembly=assembly,
        normalize_alleles=_boolean(obj["normalize_alleles"], f"{path}.normalize_alleles"),
        normalize_contigs=_boolean(obj["normalize_contigs"], f"{path}.normalize_contigs"),
        transcript_version=transcript_version,
        fallback=fallback,
        cardinality=_enum(Cardinality, obj["cardinality"], f"{path}.cardinality"),
    )


def _parse_annotator(
    raw: Any,
    path: str,
    resources: Mapping[str, ResourceDefinition],
) -> AnnotatorDefinition:
    obj = _mapping(raw, path)
    required = {"id", "label", "resource_id", "config_path", "adapter", "implementation", "match"}
    _keys(obj, required, set(), path)
    resource_id = _identifier(obj["resource_id"], f"{path}.resource_id")
    if resource_id not in resources:
        raise RegistryError(f"{path}.resource_id references unknown resource: {resource_id}")
    config_path = _config_path(obj["config_path"], f"{path}.config_path")
    adapter = _enum(Adapter, obj["adapter"], f"{path}.adapter")
    if adapter is Adapter.GENERIC_INDEXED_LOOKUP:
        parts = config_path.split(".")
        if len(parts) != 2 or parts[0] != "plugins":
            raise RegistryError(
                f"{path}.config_path for generic_indexed_lookup must be exactly plugins.<block>"
            )
        resource_config_path = resources[resource_id].config_path
        if config_path != resource_config_path:
            raise RegistryError(
                f"{path}.config_path for generic_indexed_lookup must agree with "
                f"resource {resource_id} config path {resource_config_path}"
            )
    return AnnotatorDefinition(
        id=_identifier(obj["id"], f"{path}.id"),
        label=_string(obj["label"], f"{path}.label"),
        resource_id=resource_id,
        config_path=config_path,
        adapter=adapter,
        implementation=_identifier(obj["implementation"], f"{path}.implementation"),
        match=_parse_match(obj["match"], f"{path}.match"),
    )


def _parse_metric(raw: Any, path: str) -> MetricDefinition:
    obj = _mapping(raw, path)
    required = {
        "id", "field", "value_type", "direction", "role", "value_range",
        "derived", "filterable",
    }
    _keys(obj, required, {"binary_classification"}, path)
    field = _string(obj["field"], f"{path}.field")
    if not _FIELD_PATTERN.fullmatch(field):
        raise RegistryError(f"{path}.field is not a valid annotation field")
    value_type = _enum(MetricType, obj["value_type"], f"{path}.value_type")
    direction = _enum(ScoreDirection, obj["direction"], f"{path}.direction")
    numeric_types = {MetricType.FLOAT, MetricType.INTEGER}
    if value_type not in numeric_types and direction is not ScoreDirection.NONE:
        raise RegistryError(f"{path}.direction must be none for non-numeric metrics")
    value_range_raw = obj["value_range"]
    value_range = None
    if value_range_raw is not None:
        values = _sequence(value_range_raw, f"{path}.value_range")
        if value_type not in numeric_types or len(values) != 2:
            raise RegistryError(f"{path}.value_range requires exactly two numbers on a numeric metric")
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in values):
            raise RegistryError(f"{path}.value_range requires exactly two numbers")
        value_range = (float(values[0]), float(values[1]))
        if value_range[0] > value_range[1]:
            raise RegistryError(f"{path}.value_range minimum exceeds maximum")
    binary_classification = None
    binary_raw = obj.get("binary_classification")
    if binary_raw is not None:
        binary_obj = _mapping(binary_raw, f"{path}.binary_classification")
        binary_required = {
            "threshold", "comparison", "positive_label", "negative_label",
            "threshold_set", "source_url",
        }
        _keys(binary_obj, binary_required, set(), f"{path}.binary_classification")
        threshold = binary_obj["threshold"]
        if isinstance(threshold, bool) or not isinstance(threshold, (int, float)):
            raise RegistryError(f"{path}.binary_classification.threshold must be numeric")
        threshold = float(threshold)
        if not math.isfinite(threshold):
            raise RegistryError(
                f"{path}.binary_classification.threshold must be finite"
            )
        if value_type not in numeric_types or direction is ScoreDirection.NONE:
            raise RegistryError(
                f"{path}.binary_classification requires a directed numeric metric"
            )
        if value_range is not None and not value_range[0] <= threshold <= value_range[1]:
            raise RegistryError(
                f"{path}.binary_classification.threshold is outside value_range"
            )
        comparison = _enum(
            BinaryComparison,
            binary_obj["comparison"],
            f"{path}.binary_classification.comparison",
        )
        expected_comparison = {
            ScoreDirection.HIGHER: BinaryComparison.GREATER_THAN_OR_EQUAL,
            ScoreDirection.LOWER: BinaryComparison.LESS_THAN_OR_EQUAL,
            ScoreDirection.ABSOLUTE: BinaryComparison.ABSOLUTE_GREATER_THAN_OR_EQUAL,
        }.get(direction)
        if comparison is not expected_comparison:
            raise RegistryError(
                f"{path}.binary_classification.comparison does not match "
                f"direction {direction.value}"
            )
        source_url = _string(
            binary_obj["source_url"], f"{path}.binary_classification.source_url"
        )
        if not source_url.startswith("https://"):
            raise RegistryError(f"{path}.binary_classification.source_url must use https")
        binary_classification = BinaryClassificationDefinition(
            threshold=threshold,
            comparison=comparison,
            positive_label=_string(
                binary_obj["positive_label"],
                f"{path}.binary_classification.positive_label",
            ),
            negative_label=_string(
                binary_obj["negative_label"],
                f"{path}.binary_classification.negative_label",
            ),
            threshold_set=_string(
                binary_obj["threshold_set"],
                f"{path}.binary_classification.threshold_set",
            ),
            source_url=source_url,
        )
    return MetricDefinition(
        id=_identifier(obj["id"], f"{path}.id"),
        field=field,
        value_type=value_type,
        direction=direction,
        role=_enum(MetricRole, obj["role"], f"{path}.role"),
        value_range=value_range,
        derived=_boolean(obj["derived"], f"{path}.derived"),
        filterable=_boolean(obj["filterable"], f"{path}.filterable"),
        binary_classification=binary_classification,
    )


def _parse_predictor(
    raw: Any, path: str, resource_ids: set[str], annotators: Mapping[str, AnnotatorDefinition]
) -> PredictorDefinition:
    obj = _mapping(raw, path)
    required = {
        "id", "label", "category", "resource_id", "annotator_id", "applicability",
        "default_enabled", "optional", "metrics",
    }
    _keys(obj, required, set(), path)
    resource_id = _identifier(obj["resource_id"], f"{path}.resource_id")
    annotator_id = _identifier(obj["annotator_id"], f"{path}.annotator_id")
    if resource_id not in resource_ids:
        raise RegistryError(f"{path}.resource_id references unknown resource: {resource_id}")
    if annotator_id not in annotators:
        raise RegistryError(f"{path}.annotator_id references unknown annotator: {annotator_id}")
    if annotators[annotator_id].resource_id != resource_id:
        raise RegistryError(f"{path} resource and annotator resource do not agree")
    applicability_raw = _sequence(obj["applicability"], f"{path}.applicability")
    applicability = tuple(
        _identifier(value, f"{path}.applicability[{index}]")
        for index, value in enumerate(applicability_raw)
    )
    if not applicability or len(set(applicability)) != len(applicability):
        raise RegistryError(f"{path}.applicability must contain unique values")
    metrics_raw = _sequence(obj["metrics"], f"{path}.metrics")
    if not metrics_raw:
        raise RegistryError(f"{path}.metrics must not be empty")
    _unique(metrics_raw, "id", f"{path}.metrics")
    _unique(metrics_raw, "field", f"{path}.metrics")
    metrics = tuple(
        _parse_metric(item, f"{path}.metrics[{index}]")
        for index, item in enumerate(metrics_raw)
    )
    return PredictorDefinition(
        id=_identifier(obj["id"], f"{path}.id"),
        label=_string(obj["label"], f"{path}.label"),
        category=_string(obj["category"], f"{path}.category"),
        resource_id=resource_id,
        annotator_id=annotator_id,
        applicability=applicability,
        default_enabled=_boolean(obj["default_enabled"], f"{path}.default_enabled"),
        optional=_boolean(obj["optional"], f"{path}.optional"),
        metrics=metrics,
    )


def validate_registry_document(document: Mapping[str, Any]) -> PredictorRegistry:
    """Validate and convert a decoded registry document into immutable types."""
    root = _mapping(document, "registry")
    _keys(root, {"schema_version", "resources", "annotators", "predictors"}, set(), "registry")
    version = root["schema_version"]
    if isinstance(version, bool) or not isinstance(version, int) or version != 1:
        raise RegistryError("registry.schema_version must be 1")

    resources_raw = _sequence(root["resources"], "registry.resources")
    annotators_raw = _sequence(root["annotators"], "registry.annotators")
    predictors_raw = _sequence(root["predictors"], "registry.predictors")
    _unique(resources_raw, "id", "registry.resources")
    _unique(annotators_raw, "id", "registry.annotators")
    _unique(predictors_raw, "id", "registry.predictors")

    resources = tuple(
        _parse_resource(item, f"registry.resources[{index}]")
        for index, item in enumerate(resources_raw)
    )
    resource_ids = {item.id for item in resources}
    resource_map = {item.id: item for item in resources}
    annotators = tuple(
        _parse_annotator(item, f"registry.annotators[{index}]", resource_map)
        for index, item in enumerate(annotators_raw)
    )
    annotator_map = {item.id: item for item in annotators}
    predictors = tuple(
        _parse_predictor(
            item, f"registry.predictors[{index}]", resource_ids, annotator_map
        )
        for index, item in enumerate(predictors_raw)
    )
    fields: dict[str, str] = {}
    for predictor in predictors:
        for metric in predictor.metrics:
            previous = fields.get(metric.field)
            if previous is not None:
                raise RegistryError(
                    f"annotation field {metric.field} is declared by both {previous} and {predictor.id}"
                )
            fields[metric.field] = predictor.id

    return PredictorRegistry(
        schema_version=version,
        resources=resources,
        annotators=annotators,
        predictors=predictors,
    )


def load_registry(path: str | Path | None = None) -> PredictorRegistry:
    """Load the default registry, or an explicitly supplied JSON document."""
    source = Path(path) if path is not None else REGISTRY_PATH
    try:
        document = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RegistryError(f"could not load predictor registry {source}: {exc}") from exc
    return validate_registry_document(document)


__all__ = [
    "Adapter", "AnnotatorDefinition", "BinaryClassificationDefinition",
    "BinaryComparison", "Cardinality", "Distribution",
    "MatchDefinition", "MatchDimension", "MatchFallback", "MatchScope",
    "MetricDefinition", "MetricRole", "MetricType", "PredictorDefinition",
    "PredictorRegistry", "REGISTRY_PATH", "RegistryError", "ResourceAsset",
    "ResourceDefinition", "ResourceFamily", "ScoreDirection",
    "TranscriptVersionPolicy", "load_registry", "validate_registry_document",
]
