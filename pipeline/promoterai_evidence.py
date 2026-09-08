#!/usr/bin/env python3
"""One rule for reading PromoterAI evidence out of an annotated record.

Three readers consume the PromoterAI annotation — the cohort index
(``local_service.cohort_store.annotation_from``), the whole-genome prefilter
(``local_service.wgs_review.evaluate_record``) and the browser parser
(``webui/app/vcf.ts``, ``promoterAiObservation``). They used to carry three
different field lists, aggregations and gating rules, so one managed VCF could
show a score in Cohort Search, "No score" in Variant Review, and be retained by
the prefilter on a route the review then hid (review M5). This module is the
Python side of the shared rule; ``test/contracts/promoterai_evidence_cases.json``
pins the TypeScript side to the same answers.

The rule:

* The score is read from the first present field in :data:`SCORE_FIELDS`
  order (case-insensitive). The bundled ``PromoterAI.pm`` plugin writes
  ``PromoterAI_score``; the other names are legacy aliases. Only the first
  numeric token of a multi-valued field counts — the score is signed, so
  taking a maximum across aliases would be wrong.
* The score is **usable** (``match_status == "exact"``) only when the record
  also carries the provenance the plugin always writes with it: a
  ``PromoterAI_match`` value in :data:`EXACT_MATCH_VALUES`, a
  ``PromoterAI_source_transcript``, an unambiguous integer ``PromoterAI_TSS``
  and a ``PromoterAI_strand``. A score without that provenance cannot be tied
  to the transcript's TSS and is withheld everywhere: it is not shown, not
  stored in the cohort score column, and does not qualify a record on the
  prefilter's PromoterAI route.
* Any PromoterAI field without a usable score is ``partial``; no field at all
  is ``unmatched``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping

# Priority order; compared case-insensitively.
SCORE_FIELDS: tuple[str, ...] = (
    "PromoterAI_score",
    "promoterAI_score",
    "PromoterAI_promoterAI",
    "promoterAI_promoterAI",
    "PromoterAI",
    "promoterAI",
    "PROMOTERAI",
)
SCORE_FIELD_KEYS: tuple[str, ...] = tuple(
    dict.fromkeys(field.lower() for field in SCORE_FIELDS)
)
MATCH_FIELD = "PromoterAI_match"
SOURCE_TRANSCRIPT_FIELD = "PromoterAI_source_transcript"
TSS_FIELD = "PromoterAI_TSS"
STRAND_FIELDS: tuple[str, ...] = ("PromoterAI_strand", "STRAND")
DISTANCE_FIELD = "PromoterAI_distance"
# PromoterAI.pm writes exact_version or stable_id; the registry scope and the
# stable-transcript fallback name are accepted for normalised observations.
EXACT_MATCH_VALUES: frozenset[str] = frozenset({
    "allele_transcript_tss_strand",
    "exact_version",
    "stable_id",
    "stable_transcript_id",
})
EMPTY: frozenset[str] = frozenset({"", ".", "-"})
_STRAND_ENCODING = {"+": "+", "1": "+", "-": "-", "-1": "-"}


@dataclass(frozen=True)
class PromoterAiObservation:
    score: float | None
    match_status: str  # "exact" | "partial" | "unmatched"
    match: str = ""
    source_transcript: str = ""
    tss: int | None = None
    strand: str = ""
    distance: int | None = None

    @property
    def usable_score(self) -> float | None:
        """The score when its provenance is complete, else None."""
        return self.score if self.match_status == "exact" else None


def _tokens(raw: str) -> list[str]:
    return [token.strip() for token in raw.replace(",", "&").replace("|", "&").split("&")]


def first_number(raw: str) -> float | None:
    """First numeric token of a possibly multi-valued CSQ value."""
    if raw in EMPTY:
        return None
    for token in _tokens(raw):
        if token in EMPTY:
            continue
        try:
            return float(token)
        except ValueError:
            return None
    return None


def unambiguous_integer(raw: str) -> int | None:
    if raw in EMPTY:
        return None
    values: set[int] = set()
    for token in _tokens(raw):
        if token in EMPTY:
            continue
        try:
            number = float(token)
        except ValueError:
            continue
        if number.is_integer():
            values.add(int(number))
    return values.pop() if len(values) == 1 else None


def normalized_strand(raw: str) -> str:
    """'+' / '-' from VEP's 1/-1 or +/- encodings; '' when absent or mixed."""
    if raw in EMPTY:
        return ""
    strands = {
        _STRAND_ENCODING[token] for token in _tokens(raw) if token in _STRAND_ENCODING
    }
    return strands.pop() if len(strands) == 1 else ""


def promoterai_observation(
    record: Mapping[str, str],
    *,
    decode: Callable[[str], str] | None = None,
) -> PromoterAiObservation:
    """Read the PromoterAI evidence of one CSQ entry (or INFO map).

    ``decode`` optionally un-escapes VEP's percent-encoded values before they
    are interpreted (the cohort store stores decoded text).
    """
    lowered: dict[str, str] = {}
    for key, value in record.items():
        text = str(value or "")
        if decode is not None:
            text = decode(text)
        text = text.strip()
        lowered.setdefault(key.lower(), text)

    def field(name: str) -> str:
        value = lowered.get(name.lower(), "")
        return "" if value in EMPTY else value

    score: float | None = None
    for key in SCORE_FIELD_KEYS:
        raw = field(key)
        if raw:
            score = first_number(raw)
            break
    match = field(MATCH_FIELD)
    source_transcript = field(SOURCE_TRANSCRIPT_FIELD)
    tss = unambiguous_integer(field(TSS_FIELD))
    strand = ""
    for name in STRAND_FIELDS:
        raw = field(name)
        if raw:
            strand = normalized_strand(raw)
            break
    distance_raw = field(DISTANCE_FIELD)
    distance = unambiguous_integer(distance_raw) if distance_raw else None

    if (
        score is not None
        and match.lower() in EXACT_MATCH_VALUES
        and source_transcript
        and tss is not None
        and strand
    ):
        status = "exact"
    elif score is not None or match or source_transcript or tss is not None or strand:
        status = "partial"
    else:
        status = "unmatched"
    return PromoterAiObservation(
        score=score,
        match_status=status,
        match=match,
        source_transcript=source_transcript,
        tss=tss,
        strand=strand,
        distance=distance,
    )


def has_promoterai_schema(field_names) -> bool:
    """True when an annotation schema declares any PromoterAI score field."""
    names = {str(name).lower() for name in field_names}
    return any(key in names for key in SCORE_FIELD_KEYS)
