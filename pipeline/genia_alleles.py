"""Shared VCF allele canonicalization for annotation and coverage checks."""

from __future__ import annotations

import re
from collections.abc import Sequence


def _vep_record_alts(ref: str, alts: Sequence[str]) -> Sequence[str]:
    """VEP 113 drops one shared anchor from multiallelic indels, skipping '*'."""
    if (
        len(alts) > 1
        and re.fullmatch(r"[ACGTN]+", ref, re.I) is not None
        and any(len(alt) != len(ref) for alt in alts)
        and all(
            alt in {"*", "<*>"} or (
                re.fullmatch(r"[ACGTN]+", alt, re.I) and alt[0].upper() == ref[0].upper()
            )
            for alt in alts
        )
    ):
        return [alt if alt in {"*", "<*>"} else (alt[1:] or "-") for alt in alts]
    return alts


def _vep_alt_allele(ref: str, alt: str) -> str:
    """Return VEP's minimized CSQ Allele representation for a small variant."""
    if not re.fullmatch(r"[ACGTN]+", ref, re.IGNORECASE) or not re.fullmatch(
        r"[ACGTN]+", alt, re.IGNORECASE
    ):
        return alt
    ref_sequence = ref.upper()
    alt_sequence = alt.upper()
    while ref_sequence and alt_sequence and ref_sequence[-1] == alt_sequence[-1]:
        ref_sequence = ref_sequence[:-1]
        alt_sequence = alt_sequence[:-1]
    while ref_sequence and alt_sequence and ref_sequence[0] == alt_sequence[0]:
        ref_sequence = ref_sequence[1:]
        alt_sequence = alt_sequence[1:]
    return alt_sequence or "-"


def csq_allele_index(entry: dict[str, str], ref: str, alts: Sequence[str]) -> int | None:
    """Resolve a CSQ entry only when its allele identifies one ALT."""
    number = entry.get("ALLELE_NUM", "")
    allele = entry.get("Allele", "").upper()
    # A splitter can retain record-wide CSQs and their original allele numbers.
    # On a single-ALT row the allele spelling is the only reliable discriminator.
    if len(alts) != 1 and number.isdigit():
        index = int(number) - 1
        return index if 0 <= index < len(alts) else None
    if allele in {"", "."}:
        return 0 if len(alts) == 1 and (not number or number == "." or number == "1") else None
    record_alts = _vep_record_alts(ref, alts)
    if len(alts) == 1 and re.fullmatch(r"[ACGTN]+", ref, re.I) and re.fullmatch(r"[ACGTN]+", alts[0], re.I) and alts[0][0].upper() == ref[0].upper():
        record_alts = [alts[0][1:] or "-"]
    for spellings in (alts, record_alts, [_vep_alt_allele(ref, alt) for alt in alts]):
        candidates = [index for index, spelling in enumerate(spellings) if allele == spelling.upper()]
        if candidates:
            return candidates[0] if len(candidates) == 1 else None
    return None


def normalize_chrom(value: object) -> str:
    chrom = str(value or "").strip()
    if chrom[:3].lower() == "chr":
        chrom = chrom[3:]
    if not chrom:
        raise ValueError("chromosome is empty")
    return "MT" if chrom.upper() in {"M", "MT"} else chrom.upper()


def normalize_allele(
    pos: int,
    ref: str,
    alt: str,
    fetch_base=None,
) -> tuple[int, str, str]:
    """Return a minimal, optionally repeat-left-aligned allele."""
    try:
        pos = int(pos)
    except (TypeError, ValueError) as exc:
        raise ValueError("position must be an integer") from exc
    if pos < 1:
        raise ValueError("position must be positive")
    ref, alt = ref.upper(), alt.upper()
    if not ref or not alt or not re.fullmatch(r"[ACGTN]+", ref) or not re.fullmatch(r"[ACGTN]+", alt):
        raise ValueError("REF and ALT must contain only A, C, G, T, or N")
    if ref == alt:
        raise ValueError("REF and ALT must differ")

    # Right-trim first.  When an indel reaches its one-base VCF anchor, a
    # matching final base means that the allele can be rotated one position
    # to the left.  Extending with the preceding reference base before the
    # next trim produces the canonical leftmost representation, including
    # the non-repeating anchor immediately before a homopolymer.  Keeping the
    # anchor here is important: merely rotating the inserted/deleted sequence
    # stops one base too far to the right.
    while ref[-1] == alt[-1]:
        if len(ref) > 1 and len(alt) > 1:
            ref, alt = ref[:-1], alt[:-1]
            continue
        if fetch_base is None or pos == 1:
            break
        previous = str(fetch_base(pos - 1) or "").upper()
        if not re.fullmatch(r"[ACGTN]", previous):
            raise ValueError("reference lookup did not return one DNA base")
        ref, alt, pos = previous + ref[:-1], previous + alt[:-1], pos - 1

    while len(ref) > 1 and len(alt) > 1 and ref[0] == alt[0]:
        ref, alt, pos = ref[1:], alt[1:], pos + 1
    return pos, ref, alt


def snv_key(pos: int, ref: str, alt: str) -> tuple[int, str, str] | None:
    """Identify a single substitution, allowing unchanged VCF padding on either side."""
    if len(ref) != len(alt):
        return None
    ref, alt = ref.upper(), alt.upper()
    if len(ref) == 1:
        if ref != alt and ref in "ACGT" and alt in "ACGT":
            return pos, ref, alt
        return None
    if ref == alt or not re.fullmatch(r"[ACGTN]+", ref):
        return None
    for offset, (before, after) in enumerate(zip(ref, alt)):
        if before != after:
            if before in "ACGT" and after in "ACGT" and ref[offset + 1:] == alt[offset + 1:]:
                return pos + offset, before, after
            return None
    return None
