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
        for old in vcf_header_contigs(args.vcf):
            new = old[3:] if old.startswith("chr") else old
            if new == "M":
                new = "MT"
            if old != new:
                mappings.append((old, new))
    elif vcf_style and not vcf_style.startswith("chr") and bed_style.startswith("chr"):
        for old in vcf_header_contigs(args.vcf):
            new = "chrM" if old == "MT" else f"chr{old}"
            mappings.append((old, new))

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("".join(f"{old}\t{new}\n" for old, new in mappings))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
