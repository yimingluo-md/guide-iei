#!/usr/bin/env python3
"""Finalize the local PromoterAI preparation manifest after BGZF indexing."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


def metadata(path: Path) -> dict:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return {"filename": path.name, "bytes": path.stat().st_size, "sha256": digest.hexdigest()}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stats", required=True, type=Path)
    parser.add_argument("--scores", required=True, type=Path)
    parser.add_argument("--index", required=True, type=Path)
    parser.add_argument("--transcript-map", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    for path in (args.stats, args.scores, args.index, args.transcript_map):
        if not path.is_file() or path.stat().st_size == 0:
            parser.error(f"prepared file is missing or empty: {path}")
    data = json.loads(args.stats.read_text())
    data.update({
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "assembly": "GRCh38",
        "coordinate_system": "1-based",
        "preparation": "transcripts sharing chrom/TSS/strand collapse to one four-allele score row",
        "derived": {
            "scores": metadata(args.scores),
            "index": metadata(args.index),
            "transcript_map": metadata(args.transcript_map),
        },
    })
    args.output.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
