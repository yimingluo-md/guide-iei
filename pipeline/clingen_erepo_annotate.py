#!/usr/bin/env python3
"""Add exact allele-level ClinGen Evidence Repository annotations to a VCF."""
from __future__ import annotations

import argparse
import gzip
import sqlite3
from pathlib import Path
from urllib.parse import quote


HEADERS = (
    '##INFO=<ID=ClinGen_ERepo,Number=A,Type=String,Description="Active ClinGen expert-panel assertions for each ALT; multiple assertions for one ALT are ampersand-separated and each is encoded as ALT|UUID|CAID|assertion|disease|MONDO|MOI|panel|approval_date with URL-percent-encoded fields">\n',
    '##INFO=<ID=ClinGen_ERepo_count,Number=A,Type=Integer,Description="Number of active ClinGen Evidence Repository assertions for each ALT allele">\n',
)


def open_text(path: Path, mode: str):
    return gzip.open(path, mode) if path.name.endswith(".gz") else path.open(mode)


def safe(value: str) -> str:
    return quote(value or "", safe="")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    args = parser.parse_args()
    if not args.database.is_file():
        raise ValueError(f"ClinGen Evidence Repository database is missing: {args.database}")

    connection = sqlite3.connect(
        f"{args.database.resolve().as_uri()}?mode=ro&immutable=1", uri=True
    )
    query = """
      SELECT uuid,caid,assertion,disease,mondo_id,mode_of_inheritance,
             expert_panel,approval_date
      FROM assertions
      WHERE chrom=? AND pos=? AND ref=? AND alt=? AND active=1
      ORDER BY disease,mode_of_inheritance,uuid
    """
    annotated_records = assertions_added = 0
    try:
        with open_text(args.input, "rt") as source, args.output.open("w", encoding="utf-8") as target:
            for line in source:
                if line.startswith("##INFO=<ID=ClinGen_ERepo"):
                    continue
                if line.startswith("#CHROM"):
                    target.writelines(HEADERS)
                    target.write(line)
                    continue
                if line.startswith("#"):
                    target.write(line)
                    continue
                columns = line.rstrip("\n").split("\t")
                if len(columns) < 8:
                    raise ValueError("malformed VCF record during ClinGen annotation")
                chrom = columns[0].removeprefix("chr")
                pos, ref = int(columns[1]), columns[3]
                per_alt_tokens: list[list[str]] = []
                for alt in columns[4].split(","):
                    tokens = []
                    for row in connection.execute(query, (chrom, pos, ref, alt)):
                        tokens.append("|".join(safe(value) for value in (alt, *row)))
                    per_alt_tokens.append(tokens)
                # Strip any prior run's keys UNCONDITIONALLY: leaving them
                # in place when the new database has no match preserved
                # retracted assertions through reannotation.
                retained = [
                    item for item in columns[7].split(";")
                    if item not in {"", "."}
                    and not item.startswith("ClinGen_ERepo=")
                    and not item.startswith("ClinGen_ERepo_count=")
                ]
                assertion_count = sum(len(tokens) for tokens in per_alt_tokens)
                if assertion_count:
                    info = ";".join(retained) + ";" if retained else ""
                    # Number=A requires exactly one comma-separated slot per
                    # ALT. Keep multiple disease-specific assertions inside
                    # that ALT's slot with '&'; fields are percent encoded, so
                    # a literal ampersand cannot be confused with source data.
                    assertion_slots = [
                        "&".join(tokens) if tokens else "."
                        for tokens in per_alt_tokens
                    ]
                    count_slots = [str(len(tokens)) for tokens in per_alt_tokens]
                    columns[7] = (
                        f"{info}ClinGen_ERepo={','.join(assertion_slots)};"
                        f"ClinGen_ERepo_count={','.join(count_slots)}"
                    )
                    annotated_records += 1
                    assertions_added += assertion_count
                else:
                    columns[7] = ";".join(retained) if retained else "."
                target.write("\t".join(columns) + "\n")
    finally:
        connection.close()
    print(f"ClinGen Evidence Repository annotated {annotated_records} records with {assertions_added} disease-specific assertions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
