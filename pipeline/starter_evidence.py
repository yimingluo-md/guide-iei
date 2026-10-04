"""Read starter evidence consistently in review, cohort storage and QC.

Fields come from the registry. Presence means the provider was evaluated, even
when its score is missing: never quietly fill such a gap from another provider.
The TypeScript implementation is pinned to the same shared contract fixtures.
Existing dbNSFP-only files keep their historical interpretation.
"""
from __future__ import annotations

import math
import re
from urllib.parse import unquote

try:
    from .predictor_registry import load_registry
except ImportError:  # annotation_qc.py direct CLI
    from predictor_registry import load_registry

_REGISTRY = load_registry()
_PROVIDERS = {
    p.logical_id: p for p in _REGISTRY.predictors
    if p.id in {"starter_alphamissense", "starter_cadd"}
}
_FIELDS = {key: {m.id: m.field for m in p.metrics} for key, p in _PROVIDERS.items()}
_AA = dict(zip(
    "Ala Arg Asn Asp Cys Gln Glu Gly His Ile Leu Lys Met Phe Pro Ser Thr Trp Tyr Val Ter".split(),
    "ARNDCQEGHILKMFPSTWYV*",
))
_EMPTY = {"", ".", "-"}
_NUMBER = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?")


def _text(value: str) -> str:
    value = unquote(value).strip()
    return "" if value in _EMPTY else value


def protein_change(record: dict[str, str]) -> str:
    amino = _text(record.get("Amino_acids", ""))
    position = _text(record.get("Protein_position", ""))
    if re.fullmatch(r"[A-Z*]/[A-Z*]", amino) and re.fullmatch(r"[1-9]\d*", position):
        return amino[0] + position + amino[-1]
    hgvs = _text(record.get("HGVSp", "")).rsplit(":", 1)[-1]
    match = re.fullmatch(r"(?:p\.)?([A-Z][a-z]{2}|[A-Z*])([1-9]\d*)([A-Z][a-z]{2}|[A-Z*])", hgvs)
    if not match:
        return ""
    left, pos, right = match.groups()
    left, right = _AA.get(left, left), _AA.get(right, right)
    return left + pos + right if len(left) == len(right) == 1 else ""


def starter_observation(record: dict[str, str], logical_id: str) -> dict | None:
    """None means absent schema; an empty observation means evaluated/no score.

    Scores must be scalar, finite, and in range. No numeric maximum or tolerant
    prefix parsing is appropriate for the exact-one-row indexed contract.
    """
    fields = _FIELDS[logical_id]
    if not any(field in record for field in fields.values()):
        return None
    raw = {metric: _text(record.get(field, "")) for metric, field in fields.items()}
    status = raw["match_status"]
    if status not in {"exact", "partial", "ambiguous", "unmatched"}:
        status = "partial" if any(raw.values()) else "unmatched"
    am = logical_id == "alphamissense"
    expected = "allele_transcript_protein" if am else "allele"
    target = {}
    if am:
        source = raw["source_target"].split(":")
        transcript = _text(record.get("Feature", "")).split(".", 1)[0]
        protein = protein_change(record)
        if len(source) == 2 and transcript and protein and source == [transcript, protein]:
            target = {"ensembl_transcript": transcript,
                      "protein_position": re.search(r"\d+", protein)[0],
                      "amino_acid_change": protein}
        elif status == "exact":
            status = "partial"
    if status == "exact" and raw["match"] != expected:
        status = "partial"
    metric = "score" if am else "phred"
    token = raw[metric]
    score = float(token) if _NUMBER.fullmatch(token) else None
    if score is not None and (not math.isfinite(score) or not 0 <= score <= (1 if am else 100)):
        score = None
    values = {}
    if status == "exact" and score is not None:
        values[metric] = score
        if am and raw["prediction"] in {"likely_benign", "ambiguous", "likely_pathogenic"}:
            values["prediction"] = raw["prediction"]
    provenance = {key: value for key, value in raw.items()
                  if key in {"match", "match_status", "source_target"} and value}
    if raw["allele_available"] in {"0", "1"}:
        provenance["allele_available"] = raw["allele_available"] == "1"
    return {"predictor_id": _PROVIDERS[logical_id].id, "match_status": status,
            "values": values, "target": target, "provenance": provenance}
