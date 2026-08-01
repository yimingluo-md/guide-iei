#!/usr/bin/env python3
"""Validate and compact Illumina's licensed PromoterAI release.

The source table contains one row per alternate allele and transcript. Scores
are sequence/TSS specific, so transcripts sharing a TSS can safely share one
compact score row while a separate transcript map preserves the VEP join.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path


TSS_HEADER = [
    "chrom", "pos", "strand", "gene", "gene_id", "transcript_id",
    "transcript_type",
]
SCORE_HEADER = [
    "chrom", "pos", "ref", "alt", "gene", "gene_id", "transcript_id",
    "strand", "tss_pos", "promoterAI",
]
BASES = ("A", "C", "G", "T")


def normalized_chrom(value: str) -> str:
    value = value.strip()
    if value.lower().startswith("chr"):
        value = value[3:]
    return "MT" if value in {"M", "MT"} else value


def transcript_base(value: str) -> str:
    return value.split(".", 1)[0]


def normalized_strand(value: str) -> str:
    value = value.strip()
    return {"1": "+", "+1": "+", "+": "+", "-1": "-", "-": "-"}.get(value, value)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def open_text(path: Path):
    if path.name.lower().endswith((".gz", ".bgz")):
        raw = path.open("rb")
        zipped = gzip.GzipFile(fileobj=raw)
        return raw, io.TextIOWrapper(zipped, encoding="utf-8", newline="")
    raw = path.open("rb")
    return raw, io.TextIOWrapper(raw, encoding="utf-8", newline="")


def read_tss(path: Path, output_map: Path):
    """Return every transcript ID and its normalized genomic TSS key."""
    by_tss: dict[tuple[str, int, str], list[dict[str, str]]] = {}
    exact_rows: list[dict[str, str]] = []
    raw, text = open_text(path)
    try:
        reader = csv.DictReader(text, delimiter="\t")
        if reader.fieldnames != TSS_HEADER:
            raise ValueError(
                "unexpected tss.tsv header; expected " + "\t".join(TSS_HEADER)
            )
        for line_number, row in enumerate(reader, 2):
            try:
                chrom = normalized_chrom(row["chrom"])
                pos = int(row["pos"])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"invalid TSS coordinate on line {line_number}") from exc
            strand = normalized_strand(row["strand"])
            if not chrom or pos < 1 or strand not in {"+", "-"}:
                raise ValueError(f"invalid TSS record on line {line_number}")
            transcript = row["transcript_id"].strip()
            if not transcript:
                raise ValueError(f"missing transcript_id on line {line_number}")
            cleaned = {
                "transcript_id": transcript,
                "transcript_base": transcript_base(transcript),
                "gene": row["gene"].strip(),
                "gene_id": row["gene_id"].strip(),
                "chrom": chrom,
                "tss_pos": str(pos),
                "strand": strand,
                "transcript_type": row["transcript_type"].strip(),
            }
            exact_rows.append(cleaned)
            by_tss.setdefault((chrom, pos, strand), []).append(cleaned)
    finally:
        text.close()

    if not exact_rows:
        raise ValueError("tss.tsv has no data rows")
    duplicate_transcripts = [
        name for name, count in Counter(row["transcript_id"] for row in exact_rows).items()
        if count > 1
    ]
    if duplicate_transcripts:
        raise ValueError(
            f"tss.tsv contains duplicate transcript_id values (example: {duplicate_transcripts[0]})"
        )

    transcript_tss = {
        row["transcript_id"]: (
            row["chrom"], int(row["tss_pos"]), row["strand"]
        )
        for row in exact_rows
    }

    output_map.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "transcript_id", "transcript_base", "gene", "gene_id", "chrom",
        "tss_pos", "strand", "transcript_type",
    ]
    with output_map.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        writer.writerows(sorted(exact_rows, key=lambda row: row["transcript_id"]))
    return transcript_tss, len(exact_rows), len(by_tss)


def chrom_sort_key(chrom: str):
    if chrom.isdigit():
        return (0, int(chrom))
    return (1, {"X": 23, "Y": 24, "MT": 25}.get(chrom, 1000), chrom)


def compact_scores(
    source: Path,
    transcript_tss: dict[str, tuple[str, int, str]],
    output: Path,
    temporary_root: Path,
):
    partitions = temporary_root / "partitions"
    sorted_partitions = temporary_root / "sorted"
    partitions.mkdir(parents=True)
    sorted_partitions.mkdir(parents=True)
    handles: dict[str, io.TextIOWrapper] = {}
    selected_by_tss: dict[tuple[str, int, str], str] = {}
    counts = Counter()
    current_locus = None
    locus_groups: dict[tuple[str, int, str, str], dict[str, str]] = {}
    source_size = max(1, source.stat().st_size)
    last_report = -1

    def partition_handle(chrom: str):
        if chrom not in handles:
            safe = chrom.replace("/", "_")
            handles[chrom] = (partitions / f"{safe}.tsv").open("w", encoding="utf-8")
        return handles[chrom]

    def flush_locus() -> None:
        nonlocal current_locus, locus_groups
        if current_locus is None:
            return
        chrom, pos = current_locus
        for (ref, tss_pos, strand, transcript), scores in sorted(locus_groups.items()):
            expected_alts = set(BASES) - {ref}
            missing = expected_alts - set(scores)
            if missing:
                raise ValueError(
                    f"incomplete allele block for {transcript} at {chrom}:{pos}; "
                    f"missing {','.join(sorted(missing))}"
                )
            values = [scores.get(base, ".") for base in BASES]
            partition_handle(chrom).write(
                "\t".join([chrom, str(pos), ref, str(tss_pos), strand, *values]) + "\n"
            )
            counts["compact_rows"] += 1
        current_locus = None
        locus_groups = {}

    raw, text = open_text(source)
    try:
        reader = csv.DictReader(text, delimiter="\t")
        if reader.fieldnames != SCORE_HEADER:
            raise ValueError(
                "unexpected promoterAI_tss500.tsv.gz header; expected "
                + "\t".join(SCORE_HEADER)
            )
        for line_number, row in enumerate(reader, 2):
            counts["source_rows"] += 1
            if counts["source_rows"] % 250_000 == 0:
                percent = min(89, int(raw.tell() * 90 / source_size))
                if percent != last_report:
                    print(f"{percent:.1f}% reading licensed PromoterAI scores", flush=True)
                    last_report = percent
            transcript = row["transcript_id"].strip()
            expected = transcript_tss.get(transcript)
            if expected is None:
                counts["unmapped_transcript_rows_skipped"] += 1
                continue
            selected_transcript = selected_by_tss.get(expected)
            if selected_transcript is None:
                selected_by_tss[expected] = transcript
            elif selected_transcript != transcript:
                counts["shared_tss_rows_skipped"] += 1
                continue
            try:
                chrom = normalized_chrom(row["chrom"])
                pos = int(row["pos"])
                tss_pos = int(row["tss_pos"])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"invalid coordinate on source line {line_number}") from exc
            strand = normalized_strand(row["strand"])
            ref = row["ref"].strip().upper()
            alt = row["alt"].strip().upper()
            if (chrom, tss_pos, strand) != expected:
                raise ValueError(
                    f"TSS metadata mismatch for {transcript} on source line {line_number}"
                )
            if pos < 1 or ref not in BASES or alt not in BASES or ref == alt:
                raise ValueError(f"invalid SNV on source line {line_number}")
            try:
                score_number = float(row["promoterAI"])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"invalid promoterAI score on source line {line_number}") from exc
            if not math.isfinite(score_number):
                raise ValueError(f"non-finite promoterAI score on source line {line_number}")
            locus = (chrom, pos)
            if current_locus != locus:
                flush_locus()
                current_locus = locus
            group_key = (ref, tss_pos, strand, transcript)
            scores = locus_groups.setdefault(group_key, {})
            score = row["promoterAI"].strip()
            if alt in scores and scores[alt] != score:
                raise ValueError(
                    f"conflicting duplicate score for {transcript} at {chrom}:{pos} {ref}>{alt}"
                )
            scores[alt] = score
        flush_locus()
    finally:
        text.close()
        for handle in handles.values():
            handle.close()

    all_tss = set(transcript_tss.values())
    missing_tss = sorted(all_tss - set(selected_by_tss), key=lambda item: (chrom_sort_key(item[0]), item[1], item[2]))
    missing_fraction = len(missing_tss) / max(1, len(all_tss))
    if missing_fraction > 0.001:
        example = missing_tss[0]
        raise ValueError(
            f"score table is missing {len(missing_tss)} of {len(all_tss)} TSS groups "
            f"({missing_fraction:.2%}; example: {example[0]}:{example[1]}:{example[2]})"
        )
    if missing_tss:
        mitochondrial_missing = sum(item[0] == "MT" for item in missing_tss)
        print(
            f"WARN: licensed score table omits {len(missing_tss)} of {len(all_tss)} TSS groups "
            f"({mitochondrial_missing} mitochondrial, {len(missing_tss) - mitochondrial_missing} other); "
            "recording source omissions in the manifest",
            file=sys.stderr,
            flush=True,
        )
    counts["missing_tss"] = missing_tss
    if not counts["compact_rows"]:
        raise ValueError("score table produced no compact rows")

    sort_program = shutil.which("sort")
    if not sort_program:
        raise RuntimeError("the system sort command is required for PromoterAI preparation")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as destination:
        destination.write("#chrom\tpos\tref\ttss_pos\tstrand\tscore_A\tscore_C\tscore_G\tscore_T\n")
        chromosomes = sorted(handles, key=chrom_sort_key)
        for index, chrom in enumerate(chromosomes, 1):
            source_part = partitions / f"{chrom.replace('/', '_')}.tsv"
            sorted_part = sorted_partitions / source_part.name
            env = dict(os.environ)
            env["LC_ALL"] = "C"
            with sorted_part.open("wb") as sorted_handle:
                subprocess.run(
                    [
                        sort_program, "-t", "\t", "-k2,2n", "-k4,4n",
                        "-k5,5", "-k3,3", str(source_part),
                    ],
                    stdout=sorted_handle,
                    stderr=subprocess.PIPE,
                    env=env,
                    check=True,
                )
            with sorted_part.open("r", encoding="utf-8") as sorted_handle:
                shutil.copyfileobj(sorted_handle, destination, 8 * 1024 * 1024)
            print(
                f"{90 + (index * 8 / len(chromosomes)):.1f}% sorting compact score table",
                flush=True,
            )
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Clean and compact licensed Illumina PromoterAI score files."
    )
    parser.add_argument("--tss", required=True, type=Path)
    parser.add_argument("--scores", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--transcript-map", required=True, type=Path)
    parser.add_argument("--stats", required=True, type=Path)
    parser.add_argument("--temporary-directory", type=Path)
    args = parser.parse_args()
    for path in (args.tss, args.scores):
        if not path.is_file() or path.stat().st_size == 0:
            parser.error(f"source file is missing or empty: {path}")

    print("0.0% checking source identity", flush=True)
    source_metadata = {
        "tss": {
            "filename": args.tss.name,
            "bytes": args.tss.stat().st_size,
            "sha256": sha256_file(args.tss),
        },
        "scores": {
            "filename": args.scores.name,
            "bytes": args.scores.stat().st_size,
            "sha256": sha256_file(args.scores),
        },
    }
    print("1.0% validating transcript/TSS table", flush=True)
    transcript_tss, transcript_rows, unique_tss = read_tss(args.tss, args.transcript_map)

    temporary_parent = args.temporary_directory
    if temporary_parent:
        temporary_parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="promoterai-compact-", dir=temporary_parent
    ) as temporary:
        counts = compact_scores(
            args.scores,
            transcript_tss,
            args.output,
            Path(temporary),
        )
    stats = {
        "format_version": 1,
        "source": source_metadata,
        "transcript_rows": transcript_rows,
        "unique_tss": unique_tss,
        "source_rows": counts["source_rows"],
        "shared_tss_rows_skipped": counts["shared_tss_rows_skipped"],
        "unmapped_transcript_rows_skipped": counts["unmapped_transcript_rows_skipped"],
        "missing_tss": [
            {"chrom": chrom, "pos": pos, "strand": strand}
            for chrom, pos, strand in counts["missing_tss"]
        ],
        "compact_rows": counts["compact_rows"],
    }
    args.stats.parent.mkdir(parents=True, exist_ok=True)
    args.stats.write_text(json.dumps(stats, indent=2, sort_keys=True) + "\n")
    print("98.0% compact table complete", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)
