#!/usr/bin/env python3
"""Build a bcftools rename-chrs map when VCF and BED naming styles differ."""
from __future__ import annotations

import argparse
import gzip
import re
from pathlib import Path


def text_open(path: str):
    return gzip.open(path, "rt") if path.endswith(".gz") else open(path, "rt")


def first_vcf_contig(path: str) -> str | None:
    with text_open(path) as handle:
        for line in handle:
            if not line.startswith("#"):
                return line.split("\t", 1)[0]
    return None


def first_bed_contig(path: str) -> str | None:
    with text_open(path) as handle:
        for line in handle:
            if line.strip() and not line.startswith("#"):
                return line.split("\t", 1)[0]
    return None


def vcf_header_contigs(path: str) -> list[str]:
    contigs: list[str] = []
    with text_open(path) as handle:
        for line in handle:
            if line.startswith("##contig=<"):
                match = re.search(r"ID=([^,>]+)", line)
                if match:
                    contigs.append(match.group(1))
            elif not line.startswith("#"):
                break
    return contigs


def vcf_record_contigs(path: str) -> list[str]:
    """Distinct contig names observed in the data records, in file order.

    Fallback for VCFs with no ##contig header lines (routine output of older
    callers and of bcftools view subsetting): without it, a style mismatch
    detected from the records would produce an empty rename map, which the
    driver reads as "no renaming needed" and the region filter then silently
    selects nothing.
    """
    seen: dict[str, None] = {}
    with text_open(path) as handle:
        for line in handle:
            if line.startswith("#"):
                continue
            contig = line.split("\t", 1)[0]
            if contig and contig not in seen:
                seen[contig] = None
    return list(seen)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vcf", required=True)
    parser.add_argument("--bed", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    vcf_style = first_vcf_contig(args.vcf) or ""
    bed_style = first_bed_contig(args.bed) or ""
    mappings: list[tuple[str, str]] = []
    if vcf_style.startswith("chr") and not bed_style.startswith("chr"):
        contigs = vcf_header_contigs(args.vcf) or vcf_record_contigs(args.vcf)
        for old in contigs:
            new = old[3:] if old.startswith("chr") else old
            if new == "M":
                new = "MT"
            if old != new:
                mappings.append((old, new))
    elif vcf_style and not vcf_style.startswith("chr") and bed_style.startswith("chr"):
        contigs = vcf_header_contigs(args.vcf) or vcf_record_contigs(args.vcf)
        for old in contigs:
            # Mixed naming (post-merge headers): a contig that is already
            # chr-prefixed must not become chrchr2, and identity mappings
            # are noise for bcftools annotate --rename-chrs.
            if old.startswith("chr"):
                continue
            new = "chrM" if old == "MT" else f"chr{old}"
            if old != new:
                mappings.append((old, new))

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("".join(f"{old}\t{new}\n" for old, new in mappings))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
