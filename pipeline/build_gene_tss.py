#!/usr/bin/env python3
"""Build a compact gene-level TSS table from a release-matched Ensembl GTF."""

from __future__ import annotations

import argparse
import gzip
import os
import re
import tempfile
from pathlib import Path


PRIMARY_CONTIGS = {str(value) for value in range(1, 23)} | {"X", "Y", "MT"}


def open_text(path: Path):
    return (
        gzip.open(path, "rt", encoding="utf-8", errors="replace")
        if path.name.lower().endswith((".gz", ".bgz"))
        else path.open("rt", encoding="utf-8", errors="replace")
    )


def attributes(raw: str) -> dict[str, str]:
    return {
        key: value
        for key, value in re.findall(r'(\S+)\s+"([^"]*)"', raw)
    }


def build_gene_tss(
    gtf_path: Path,
    output_path: Path,
    *,
    assembly: str,
    release: str,
) -> int:
    if not gtf_path.is_file():
        raise ValueError(f"Ensembl GTF was not found: {gtf_path}")
    rows: dict[str, tuple[str, int, str, str, str, str]] = {}
    with open_text(gtf_path) as handle:
        for line in handle:
            if not line or line.startswith("#"):
                continue
            columns = line.rstrip("\r\n").split("\t")
            if len(columns) < 9 or columns[2] != "gene":
                continue
            chrom = columns[0].removeprefix("chr")
            if chrom == "M":
                chrom = "MT"
            if chrom not in PRIMARY_CONTIGS or columns[6] not in {"+", "-"}:
                continue
            try:
                start, end = int(columns[3]), int(columns[4])
            except ValueError:
                continue
            values = attributes(columns[8])
            gene_id = values.get("gene_id", "").split(".", 1)[0]
            if not gene_id:
                continue
            gene_name = values.get("gene_name") or gene_id
            biotype = (
                values.get("gene_biotype")
                or values.get("gene_type")
                or "unknown"
            )
            tss = start if columns[6] == "+" else end
            rows[gene_id] = (
                chrom, tss, gene_name, gene_id, columns[6], biotype,
            )
    if not rows:
        raise ValueError(f"no primary-contig gene records were parsed from {gtf_path}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output_path.name}.", dir=output_path.parent
    )
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    order = {str(value): value for value in range(1, 23)} | {
        "X": 23, "Y": 24, "MT": 25,
    }
    try:
        with temporary_path.open("wt", encoding="utf-8") as output:
            output.write(f"#assembly={assembly}\n")
            output.write(f"#ensembl_release={release}\n")
            output.write(f"#source_gtf={gtf_path.name}\n")
            output.write("chrom\ttss\tgene_symbol\tgene_id\tstrand\tbiotype\n")
            for row in sorted(
                rows.values(), key=lambda value: (order[value[0]], value[1], value[3])
            ):
                output.write("\t".join(map(str, row)) + "\n")
        temporary_path.replace(output_path)
    finally:
        temporary_path.unlink(missing_ok=True)
    return len(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gtf", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--assembly", default="GRCh38")
    parser.add_argument("--release", default="113")
    args = parser.parse_args()
    count = build_gene_tss(
        args.gtf.expanduser().resolve(),
        args.output.expanduser().resolve(),
        assembly=args.assembly,
        release=args.release,
    )
    print(f"wrote {count} gene-level TSS records to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
