"""Rebuild GUIDE-IEI's genome-wide GPN-Star constraint ranks from the pinned full shards."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys

import numpy as np
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline import gpn_star as gpn

# The pinned Float32 releases store decimal millisteps. Prove that encoding is
# lossless for every finite value before using compact integer histogram bins.
SCALE = 1000
CHROMS = [str(n) for n in range(1, 23)] + ["X", "Y"]


def allele_codes(column):
    codes = {"A": 0, "C": 1, "G": 2, "T": 3}
    dictionary = np.array([codes[base] for base in column.dictionary.to_pylist()], dtype=np.uint8)
    return dictionary[column.indices.to_numpy()]


def shard_histogram(path, pin, chrom):
    if path.stat().st_size != pin["size"] or gpn.sha256(path) != pin["sha256"]:
        raise ValueError(f"Unverified shard {path}")
    counts = {}
    previous_pos, previous_ref, previous_alt = 0, -1, -1
    with pq.ParquetFile(path, read_dictionary=["chrom", "ref", "alt"]) as table:
        if table.metadata.num_rows != pin["rows"]:
            raise ValueError(f"Unexpected row count in {path}")
        for batch in table.iter_batches(batch_size=524288, columns=["chrom", "pos", "ref", "alt", "llr_calibrated"], use_threads=False):
            names, positions, refs, alts, scores = batch.columns
            if any(col.null_count for col in (names, positions, refs, alts)) or names.dictionary.to_pylist() != [chrom]:
                raise ValueError(f"Invalid allele coordinates in {path}")
            pos = positions.to_numpy()
            ref, alt = allele_codes(refs), allele_codes(alts)
            same = pos[1:] == pos[:-1]
            if (pos[0] <= 0 or np.any(ref == alt) or np.any(pos[1:] < pos[:-1])
                    or np.any(same & ((ref[1:] != ref[:-1]) | (alt[1:] <= alt[:-1])))
                    or pos[0] < previous_pos or (pos[0] == previous_pos and
                        (ref[0] != previous_ref or alt[0] <= previous_alt))):
                raise ValueError(f"Unsorted, duplicate, or invalid SNV in {path}")
            previous_pos, previous_ref, previous_alt = pos[-1], ref[-1], alt[-1]
            values = scores.to_numpy(zero_copy_only=False)
            values = values[np.isfinite(values)]
            if not values.size:
                continue
            ticks = np.rint(values.astype(np.float64) * SCALE).astype(np.int32)
            if not np.array_equal((ticks / SCALE).astype(np.float32), values):
                raise ValueError(f"Lossy score encoding in {path}")
            first = int(ticks.min())
            bins = np.bincount(ticks - first)
            for index in np.flatnonzero(bins):
                key = first + int(index)
                counts[key] = counts.get(key, 0) + int(bins[index])
    print(f"Counted {path.parent.name} chr{chrom}", flush=True)
    return counts


def recount(path, limits):
    # Independent pass: compare the actual Float32 scores with the boundary
    # scores directly. Check both the passing count and the first excluded tie.
    counts = np.zeros(len(limits), dtype=np.int64)
    with pq.ParquetFile(path) as table:
        for batch in table.iter_batches(columns=["llr_calibrated"], batch_size=1048576):
            values = batch[0].to_numpy(zero_copy_only=False)
            finite = values[np.isfinite(values)]
            for i, limit in enumerate(limits):
                counts[i] += np.count_nonzero(finite <= limit)
    return counts


def build(directory, model, workers):
    pins = gpn.assets(model)
    if set(pins) != set(CHROMS):
        raise ValueError("Expected primary chromosomes 1–22, X and Y")
    paths = [gpn.release_dir(directory, model) / gpn.shard_name(c) for c in CHROMS]
    counts = {}
    for result in workers.map(shard_histogram, paths, (pins[c] for c in CHROMS), CHROMS):
        for tick, n in result.items():
            counts[tick] = counts.get(tick, 0) + n
    first, last = min(counts), max(counts)
    cumulative = np.cumsum([counts.get(tick, 0) for tick in range(first, last + 1)], dtype=np.int64)
    total = int(cumulative[-1])
    occupied = np.flatnonzero(np.diff(cumulative, prepend=0))
    checks = []
    for denominator in (10, 100, 1000):
        edge = np.searchsorted(cumulative[occupied], total // denominator, side="right")
        checks.extend(occupied[edge - 1:edge + 1])
    checks.append(occupied[-1])
    limits = np.asarray((first + np.array(checks)) / SCALE, dtype=np.float32)
    actual = sum(workers.map(lambda path: recount(path, limits), paths))
    if not np.array_equal(actual, cumulative[checks]):
        raise ValueError(f"{model} independent tail counts disagree")
    return {"first_tick": first, "excluded_nonfinite": sum(p["rows"] for p in pins.values()) - total,
            "cumulative": cumulative.tolist()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, help="GPN-Star resource directory containing the pinned revision")
    parser.add_argument("output", type=Path)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    with ThreadPoolExecutor(max_workers=args.workers) as workers:
        models = {model: build(args.directory, model, workers) for model in gpn.MODELS.values()}
    data = {"revision": gpn.REVISION, "assembly": "GRCh38", "chromosomes": CHROMS,
            "scale": SCALE, "models": models}
    temporary = args.output.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, separators=(",", ":")) + "\n")
    temporary.replace(args.output)


if __name__ == "__main__":
    main()
