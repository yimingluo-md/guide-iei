"""Shared allele canonicalization for GenIA preparation and annotation."""

from __future__ import annotations

import re


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
