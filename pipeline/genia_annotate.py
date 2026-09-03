#!/usr/bin/env python3
"""Add compact exact-allele GenIA evidence from a private local index."""

from __future__ import annotations

import argparse
import gzip
import sqlite3
import sys
from pathlib import Path
from urllib.parse import quote

try:
    from .genia_alleles import normalize_allele, normalize_chrom
except ImportError:  # direct script execution
    from genia_alleles import normalize_allele, normalize_chrom


HEADERS = (
    '##INFO=<ID=GenIA,Number=A,Type=String,Description="Exact-allele GenIA records for each ALT; multiple records for one ALT are ampersand-separated and each is encoded as ALT|record_id|short_name|classification|relevant_subjects with URL-percent-encoded fields">\n',
    '##INFO=<ID=GenIA_count,Number=A,Type=Integer,Description="Number of exact-allele GenIA records for each ALT allele">\n',
)


def open_text(path: Path, mode: str):
    return gzip.open(path, mode) if path.name.endswith(".gz") else path.open(mode)


def safe(value: object) -> str:
    return quote("" if value is None else str(value), safe="")


def open_index(
    database: Path,
    reference_path: Path | None,
    *,
    allow_unavailable: bool,
):
    """Open and validate the immutable index, or select safe strip-only mode."""
    connection = None
    reference = None
    try:
        if not database.is_file():
            raise ValueError(f"GenIA database is missing: {database}")
        connection = sqlite3.connect(
            f"{database.resolve().as_uri()}?mode=ro&immutable=1", uri=True
        )
        integrity = connection.execute("PRAGMA quick_check").fetchone()
        if not integrity or integrity[0] != "ok":
            raise ValueError("the GenIA database failed its quick check")
        component = connection.execute(
            "SELECT release_hint FROM components WHERE id='variant_vcf'"
        ).fetchone()
        if component is None:
            raise ValueError("the GenIA index has no installed variant component")
        release_hint = str(component[0] or "")
        if "GRCh38" not in release_hint:
            raise ValueError("the GenIA variant component is not identified as GRCh38")
        # Validate the complete lookup contract before opening the output.  An
        # optional but incompatible index must never yield a partially
        # annotated VCF.
        connection.execute(
            """
            SELECT record_id,short_name,class_code,relevant_subjects
            FROM variants WHERE chrom=? AND pos=? AND ref=? AND alt=? LIMIT 0
            """,
            ("1", 1, "A", "C"),
        )
        if "reference_left_aligned" in release_hint:
            if reference_path is None:
                raise ValueError(
                    "this GenIA index requires the GRCh38 reference used during installation"
                )
            try:
                from .loftee_ptc_50bp import IndexedFasta
            except ImportError:  # direct script execution
                from loftee_ptc_50bp import IndexedFasta
            reference = IndexedFasta(reference_path)
        return connection, reference
    except Exception as exc:
        if reference is not None:
            reference.close()
        if connection is not None:
            connection.close()
        if not allow_unavailable:
            if isinstance(exc, ValueError):
                raise
            raise ValueError(f"the GenIA index could not be opened: {exc}") from exc
        print(
            f"WARN  optional GenIA variant evidence is unavailable ({exc}); "
            "stale GenIA annotations will be removed",
            file=sys.stderr,
        )
        return None, None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--reference", type=Path)
    parser.add_argument(
        "--allow-unavailable",
        action="store_true",
        help="strip stale GenIA fields and succeed when the optional index is unavailable",
    )
    args = parser.parse_args()
    if args.input.resolve() == args.output.resolve():
        raise ValueError("GenIA input and output paths must be different")

    connection, reference = open_index(
        args.database,
        args.reference,
        allow_unavailable=args.allow_unavailable,
    )

    query = """
      SELECT record_id,short_name,class_code,relevant_subjects
      FROM variants WHERE chrom=? AND pos=? AND ref=? AND alt=?
      ORDER BY record_id
    """
    annotated_records = records_added = 0
    chrom_header_seen = False
    try:
        with open_text(args.input, "rt") as source, args.output.open("w", encoding="utf-8") as target:
            for line in source:
                if line.startswith("##INFO=<ID=GenIA,") or line.startswith(
                    "##INFO=<ID=GenIA_count,"
                ):
                    continue
                if line.startswith("#CHROM"):
                    chrom_header_seen = True
                    if connection is not None:
                        target.writelines(HEADERS)
                    target.write(line)
                    continue
                if line.startswith("#"):
                    target.write(line)
                    continue
                columns = line.rstrip("\r\n").split("\t")
                if len(columns) < 8:
                    raise ValueError("malformed VCF record during GenIA annotation")
                chrom = normalize_chrom(columns[0])
                source_pos, source_ref = int(columns[1]), columns[3]
                per_alt_tokens: list[list[str]] = []
                for source_alt in columns[4].split(","):
                    tokens: list[str] = []
                    if connection is not None:
                        try:
                            fetch_base = (
                                (
                                    lambda coordinate, contig=chrom: reference.fetch(
                                        contig, coordinate, coordinate
                                    )
                                )
                                if reference is not None else None
                            )
                            if reference is not None:
                                observed_ref = reference.fetch(
                                    chrom,
                                    source_pos,
                                    source_pos + len(source_ref) - 1,
                                )
                                if observed_ref.upper() != source_ref.upper():
                                    raise ValueError(
                                        "input REF does not match the configured GRCh38 reference"
                                    )
                            pos, ref, alt = normalize_allele(
                                source_pos, source_ref, source_alt, fetch_base
                            )
                            tokens = [
                                "|".join(safe(value) for value in (source_alt, *row))
                                for row in connection.execute(
                                    query, (chrom, pos, ref, alt)
                                )
                            ]
                        except (IndexError, KeyError, ValueError):
                            # Symbolic/breakend alleles, contigs absent from the
                            # reference, and REF mismatches are ineligible for
                            # an exact GenIA match; they do not invalidate the
                            # rest of an otherwise valid VCF record.
                            tokens = []
                    per_alt_tokens.append(tokens)
                retained = [
                    item for item in columns[7].split(";")
                    if item not in {"", "."}
                    and item.partition("=")[0] not in {"GenIA", "GenIA_count"}
                ]
                record_count = sum(len(tokens) for tokens in per_alt_tokens)
                if record_count:
                    prefix = ";".join(retained) + ";" if retained else ""
                    slots = ["&".join(tokens) if tokens else "." for tokens in per_alt_tokens]
                    counts = [str(len(tokens)) for tokens in per_alt_tokens]
                    columns[7] = f"{prefix}GenIA={','.join(slots)};GenIA_count={','.join(counts)}"
                    annotated_records += 1
                    records_added += record_count
                else:
                    columns[7] = ";".join(retained) if retained else "."
                target.write("\t".join(columns) + "\n")
            if not chrom_header_seen:
                raise ValueError("input does not contain a #CHROM VCF header")
    finally:
        if reference is not None:
            reference.close()
        if connection is not None:
            connection.close()
    if connection is None:
        print("GenIA unavailable; stale GenIA annotations removed")
    else:
        print(
            f"GenIA annotated {annotated_records} records with "
            f"{records_added} exact-allele record(s)"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
