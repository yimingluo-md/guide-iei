#!/usr/bin/env python3
"""Dependency-free sequential extraction of small regions from a gzip FASTA.

This is an update-time fallback when samtools (or the VEP container) is not
available. It scans the reference once and stores only requested intervals.
"""
from __future__ import annotations

import argparse
import gzip
import re
from collections import defaultdict
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fasta", type=Path, required=True)
    parser.add_argument("--regions", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    requested = defaultdict(list)
    for line in args.regions.read_text(encoding="ascii").splitlines():
        match = re.fullmatch(r"(?:chr)?([^:]+):(\d+)-(\d+)", line.strip())
        if not match:
            raise ValueError(f"invalid FASTA region: {line}")
        chrom, start, end = match.group(1), int(match.group(2)), int(match.group(3))
        # Deduplicate: a region listed twice previously double-collected
        # into one bucket and aborted with a misleading byte-count error.
        if (start, end) not in requested[chrom]:
            requested[chrom].append((start, end))
    collected = {(chrom, start, end): [] for chrom, values in requested.items() for start, end in values}
    opener = gzip.open if args.fasta.name.endswith(".gz") else open
    chrom = ""
    position = 0
    with opener(args.fasta, "rt") as handle:
        for line in handle:
            if line.startswith(">"):
                chrom = line[1:].split()[0].removeprefix("chr")
                position = 0
                continue
            sequence = line.strip().upper()
            if not sequence:
                continue
            line_start, line_end = position + 1, position + len(sequence)
            for start, end in requested.get(chrom, ()):
                overlap_start, overlap_end = max(start, line_start), min(end, line_end)
                if overlap_start <= overlap_end:
                    collected[(chrom, start, end)].append(
                        sequence[overlap_start - line_start:overlap_end - line_start + 1]
                    )
            position = line_end
    with args.output.open("w", encoding="ascii") as out:
        for key in sorted(collected):
            sequence = "".join(collected[key])
            expected = key[2] - key[1] + 1
            if len(sequence) != expected:
                raise ValueError(f"reference region {key[0]}:{key[1]}-{key[2]} returned {len(sequence)} of {expected} bases")
            out.write(f">{key[0]}:{key[1]}-{key[2]}\n")
            for offset in range(0, len(sequence), 60):
                out.write(sequence[offset:offset + 60] + "\n")
    print(f"Extracted {len(collected)} reference regions by sequential FASTA scan")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
