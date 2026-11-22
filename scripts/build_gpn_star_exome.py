#!/usr/bin/env python3
"""Build the redistributable GPN-Star-V subset from verified upstream Parquet and the stock exome BED."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import gzip
import json
from pathlib import Path
import sys

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline import gpn_star as gpn


def read_regions(bed):
    intervals = {}
    opener = gzip.open if bed.suffix == ".gz" else open
    with opener(bed, "rt") as source:
        for line in source:
            fields = line.split()
            if not fields or fields[0].startswith("#") or fields[0] in {"track", "browser"}:
                continue
            chrom, start, end = fields[:3]
            if chrom not in gpn.assets("v100"):
                continue
            ranges = intervals.setdefault(chrom, [])
            start, end = int(start), int(end)
            if not 0 <= start < end or (ranges and ranges[-1][1] >= start):
                raise ValueError(f"Expected sorted, merged BED intervals on {chrom}")
            ranges.append((start, end))
    if intervals.keys() != gpn.assets("v100").keys():
        raise ValueError("Expected exome intervals for every upstream chromosome")
    return intervals


def selected_groups(table, intervals):
    starts, ends = np.array(intervals, dtype=np.int64).T
    for idx, first, last in gpn.position_ranges(table):
        # BED [start, end) corresponds to one-based positions (start, end].
        lo, hi = ends.searchsorted(first), starts.searchsorted(last)
        if lo >= hi:
            continue
        group = table.read_row_group(idx, use_threads=False)
        pos = group["pos"].combine_chunks().to_numpy()
        left = pos.searchsorted(starts[lo:hi], side="right")
        right = pos.searchsorted(ends[lo:hi], side="right")
        mask = np.zeros(len(pos), dtype=bool)
        for a, b in zip(left, right):
            mask[a:b] = True
        if mask.any():
            yield group.filter(pa.array(mask))


def same_rows(expected, actual):
    if expected.schema != actual.schema:
        return False
    for left, right in zip(expected.columns, actual.columns):
        if pa.types.is_floating(left.type):
            # Arrow numeric equality treats NaN as unequal to itself. Verify
            # the stored bits so NaNs and their validity masks round-trip too.
            bits = pa.binary(left.type.bit_width // 8)
            left = left.combine_chunks().view(bits)
            right = right.combine_chunks().view(bits)
        if not left.equals(right):
            return False
    return True


def build_chrom(source, target, intervals, pin):
    if gpn.sha256(source) != pin["sha256"]:
        raise ValueError(f"Upstream checksum mismatch for {source.name}")
    temp = target.with_suffix(".tmp")
    with pq.ParquetFile(source) as full:
        with pq.ParquetWriter(temp, full.schema_arrow, compression="zstd") as writer:
            for group in selected_groups(full, intervals):
                writer.write_table(group)
        with pq.ParquetFile(temp) as compact:
            count = 0
            for group in selected_groups(full, intervals):
                if not same_rows(group, compact.read_row_group(count)):
                    raise ValueError(f"Read-back verification failed for {target.name} group {count}")
                count += 1
            if count == 0 or count != compact.num_row_groups:
                raise ValueError(f"Unexpected row groups in {target.name}")
            rows = compact.metadata.num_rows
    temp.replace(target)
    print(f"{target.name}: {rows:,} SNVs verified", flush=True)
    return {"size": target.stat().st_size, "sha256": gpn.sha256(target), "rows": rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Installed genome-wide v100 shard directory")
    parser.add_argument("bed", type=Path, help="Default GRCh38 Ensembl 113 BED from build_coding_bed.sh (8 bp)")
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    intervals = read_regions(args.bed)
    args.output.mkdir(parents=True, exist_ok=True)
    mask = args.output / "coding-splice.bed.gz"
    with mask.open("wb") as raw, gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as bed:
        for chrom in gpn.assets("v100"):
            for start, end in intervals[chrom]:
                bed.write(f"{chrom}\t{start}\t{end}\n".encode())

    def build(chrom):
        name = gpn.shard_name(chrom)
        return build_chrom(args.source / name, args.output / name, intervals[chrom], gpn.assets("v100")[chrom])

    with ThreadPoolExecutor(max_workers=3) as workers:
        shards = dict(zip(gpn.assets("v100"), workers.map(build, gpn.assets("v100"))))
    manifest = {"upstream_revision": gpn.REVISION, "assembly": "GRCh38", "ensembl": 113,
                "padding_bp": 8, "bed_sha256": gpn.sha256(mask), "shards": shards}
    (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    main()
