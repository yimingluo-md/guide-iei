#!/usr/bin/env python3
"""Streaming structural validation before handing user VCFs to native parsers.

This checks the transport format, not biological validity or sortedness. Never
include record contents in errors: they may contain patient data.
"""
from __future__ import annotations

import argparse
import gzip
import re
import zlib
from pathlib import Path


FIXED_COLUMNS = ["#CHROM", "POS", "ID", "REF", "ALT", "QUAL", "FILTER", "INFO"]
FIELD_WHITESPACE = re.compile(r"[^\S\t]")


def validate_input_vcf(path: str | Path, *, full: bool = True) -> dict:
    path = Path(path)
    columns = None
    records = 0
    line_number = 0

    def invalid(message):
        raise ValueError(f"Invalid VCF {path.name}, line {line_number}: {message}")

    try:
        if not path.is_file():
            raise ValueError(f"Input VCF is not a readable regular file: {path}")
        opener = gzip.open if path.name.lower().endswith(".gz") else open
        with opener(path, "rt", encoding="utf-8") as handle:
            for line_number, raw in enumerate(handle, 1):
                line = raw.rstrip("\r\n")
                if "\x00" in line:
                    invalid("NUL bytes are not permitted in VCF text")
                if line_number == 1:
                    if not re.fullmatch(r"##fileformat=VCFv4\.\d+", line):
                        invalid("the first line must declare ##fileformat=VCFv4.x")
                    continue
                if columns is None:
                    if line.startswith("##"):
                        continue
                    columns = line.split("\t")
                    if columns[:8] != FIXED_COLUMNS:
                        invalid("expected tab-separated #CHROM POS ID REF ALT QUAL FILTER INFO columns; INFO is mandatory")
                    if len(columns) < 10 or columns[8] != "FORMAT":
                        invalid("pipeline requires at least one sample after the FORMAT column")
                    samples = columns[9:]
                    if any(not name or name == "." or any(c.isspace() for c in name) for name in samples):
                        invalid("sample names must be nonempty and contain no whitespace")
                    if len(set(samples)) != len(samples):
                        invalid("duplicate sample names are not allowed")
                    continue
                if line.startswith("#"):
                    invalid("unexpected header after #CHROM")
                values = line.split("\t")
                if len(values) != len(columns):
                    invalid(f"expected {len(columns)} tab-separated columns, found {len(values)}")
                if any(not value for value in values):
                    invalid("empty fields must be represented by '.'")
                if not values[1].isascii() or not values[1].isdigit():
                    invalid("POS must be an integer")
                # VCF permits position 0 for telomeric breakends.
                # Scan long cohort/INFO rows in the regex engine, not one
                # Python iteration per character during a whole-genome scan.
                if FIELD_WHITESPACE.search(line):
                    invalid("whitespace inside a field is not allowed")
                if values[8] != ".":
                    formats = values[8].split(":")
                    if any(not item for item in formats) or len(set(formats)) != len(formats):
                        invalid("FORMAT contains empty or duplicate keys")
                    if "GT" in formats and formats[0] != "GT":
                        invalid("GT must be the first FORMAT key")
                    # Trailing sample subfields may legally be omitted.
                    if any(value.count(":") + 1 > len(formats) for value in values[9:]):
                        invalid("a sample has more subfields than FORMAT declares")
                records += 1
                if not full:
                    break
    except (OSError, EOFError, UnicodeError, zlib.error) as exc:
        raise ValueError(
            f"Cannot read input VCF {path.name} near line {line_number}: "
            "the file is unreadable, truncated, corrupt, or not UTF-8 text. "
            "Obtain a complete VCF export and try again."
        ) from exc
    if columns is None:
        raise ValueError(f"Invalid VCF {path.name}: VCF column header (#CHROM) was not found")
    return {"samples": len(columns) - 9, "records_checked": records, "full_scan": full}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vcf", required=True)
    parser.add_argument("--header-only", action="store_true", help="Check header and first record; the annotation runner always checks the full file")
    args = parser.parse_args()
    try:
        result = validate_input_vcf(args.vcf, full=not args.header_only)
    except ValueError as exc:
        parser.exit(2, f"ERROR {exc}\n")
    print(f"VCF structure valid: {result['samples']} sample(s), {result['records_checked']} record(s) checked")


if __name__ == "__main__":
    main()
