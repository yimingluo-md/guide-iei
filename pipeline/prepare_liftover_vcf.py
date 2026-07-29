#!/usr/bin/env python3
"""Prepare a GRCh37 VCF for conservative small-variant liftover.

The original callset is never modified. Supported records receive INFO fields
that retain their source representation; unsupported records are written to a
separate reviewable VCF with an explicit rejection reason.
"""
from __future__ import annotations

import argparse
import gzip
import json
import re
from collections import Counter
from pathlib import Path
from urllib.parse import quote


PRIMARY_CONTIG = re.compile(r"^(?:chr)?(?:[1-9]|1[0-9]|2[0-2]|X|Y|M|MT)$", re.I)
SEQUENCE = re.compile(r"^[ACGTN]+$", re.I)
INFO_DEFINITION = re.compile(r"^##INFO=<ID=([^,>]+),Number=([^,>]+),")


def text_open(path: str):
    return gzip.open(path, "rt") if path.lower().endswith(".gz") else open(path, "rt")


def rejection_reason(chrom: str, ref: str, alts: list[str], max_length: int) -> str | None:
    if not PRIMARY_CONTIG.fullmatch(chrom):
        return "NON_PRIMARY_CONTIG"
    if not SEQUENCE.fullmatch(ref):
        return "NON_SEQUENCE_REF"
    for alt in alts:
        if alt == "*":
            return "SPANNING_DELETION"
        if alt.startswith("<") or "[" in alt or "]" in alt:
            return "SYMBOLIC_OR_BREAKEND"
        if not SEQUENCE.fullmatch(alt):
            return "NON_SEQUENCE_ALT"
        if max(len(ref), len(alt)) > max_length:
            return "ALLELE_TOO_LONG"
    return None


def append_info(raw_info: str, values: list[str]) -> str:
    base = [] if raw_info in {"", "."} else [raw_info]
    return ";".join(base + values)


def remove_malformed_allele_info(
    raw_info: str, alternate_count: int, info_numbers: dict[str, str]
) -> tuple[str, list[str]]:
    """Drop invalid Number=A/R values that bcftools cannot split safely.

    These fields are already inconsistent with the record's ALT list, so
    padding, truncating, or guessing would manufacture allele-specific data.
    The variant, genotype, and every well-formed INFO value are retained.
    """
    if raw_info in {"", "."}:
        return raw_info, []
    retained: list[str] = []
    removed: list[str] = []
    for entry in raw_info.split(";"):
        key, separator, value = entry.partition("=")
        number = info_numbers.get(key)
        expected = (
            alternate_count if number == "A"
            else alternate_count + 1 if number == "R"
            else None
        )
        if separator and expected is not None and len(value.split(",")) != expected:
            removed.append(key)
        else:
            retained.append(entry)
    return ";".join(retained) or ".", removed


def ensembl_contig(value: str) -> str:
    normalized = value[3:] if value.lower().startswith("chr") else value
    return "MT" if normalized.upper() in {"M", "MT"} else normalized


def normalized_contig_header(line: str) -> str:
    return re.sub(
        r"(##contig=<ID=)([^,>]+)",
        lambda match: match.group(1) + ensembl_contig(match.group(2)),
        line,
        count=1,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--supported", required=True)
    parser.add_argument("--unsupported", required=True)
    parser.add_argument("--stats", required=True)
    parser.add_argument("--max-allele-length", type=int, default=50)
    args = parser.parse_args()

    if args.max_allele_length < 1:
        parser.error("--max-allele-length must be positive")

    supported_path = Path(args.supported)
    unsupported_path = Path(args.unsupported)
    supported_path.parent.mkdir(parents=True, exist_ok=True)
    unsupported_path.parent.mkdir(parents=True, exist_ok=True)
    total = supported = unsupported = supported_alleles = 0
    reasons: Counter[str] = Counter()
    removed_info_fields: Counter[str] = Counter()
    repaired_records = 0
    info_numbers: dict[str, str] = {}
    saw_columns = False

    with (
        text_open(args.input) as source,
        supported_path.open("w") as good,
        unsupported_path.open("w") as bad,
    ):
        for line in source:
            if line.startswith("##INFO=<"):
                definition = INFO_DEFINITION.match(line)
                if definition:
                    info_numbers[definition.group(1)] = definition.group(2)
                good.write(line)
                bad.write(line)
                continue
            if line.startswith("##reference="):
                original_reference = line.partition("=")[2].strip()
                rewritten = f"##iei_original_reference={original_reference}\n"
                good.write(rewritten)
                bad.write(line)
                continue
            if line.startswith("##contig=<"):
                good.write(normalized_contig_header(line))
                bad.write(line)
                continue
            if line.startswith("#CHROM"):
                provenance_headers = (
                    '##INFO=<ID=IEI_LIFTOVER,Number=0,Type=Flag,Description="Record converted from GRCh37 to GRCh38 by the IEI pipeline">\n'
                    '##INFO=<ID=IEI_ORIGINAL_ASSEMBLY,Number=1,Type=String,Description="Input reference assembly before liftover">\n'
                    '##INFO=<ID=IEI_ORIGINAL_CHROM,Number=1,Type=String,Description="Input chromosome before liftover">\n'
                    '##INFO=<ID=IEI_ORIGINAL_POS,Number=1,Type=Integer,Description="Input 1-based position before liftover">\n'
                    '##INFO=<ID=IEI_ORIGINAL_REF,Number=1,Type=String,Description="Input REF allele before liftover">\n'
                    '##INFO=<ID=IEI_ORIGINAL_ALT,Number=A,Type=String,Description="Input ALT allele before liftover">\n'
                    '##INFO=<ID=IEI_ORIGINAL_RECORD,Number=1,Type=Integer,Description="Stable input record ordinal used for liftover QC accounting">\n'
                )
                reject_headers = (
                    '##FILTER=<ID=IEI_UNSUPPORTED_LIFTOVER,Description="Record is outside the validated GRCh37 small-variant liftover scope">\n'
                    '##INFO=<ID=IEI_LIFTOVER_REJECT_REASON,Number=1,Type=String,Description="Reason the record was not sent to BCFtools/liftover">\n'
                )
                good.write(provenance_headers)
                bad.write(reject_headers)
                good.write(line)
                bad.write(line)
                saw_columns = True
                continue
            if line.startswith("#"):
                good.write(line)
                bad.write(line)
                continue
            if not line.strip():
                continue
            if not saw_columns:
                parser.error("VCF #CHROM header was not found before records")

            total += 1
            columns = line.rstrip("\n").split("\t")
            if len(columns) < 8:
                reason = "MALFORMED_RECORD"
            else:
                chrom, pos, _, ref, alt_raw = columns[:5]
                reason = rejection_reason(
                    chrom, ref, alt_raw.split(","), args.max_allele_length
                )
            if reason:
                unsupported += 1
                reasons[reason] += 1
                if len(columns) >= 8:
                    current_filter = columns[6]
                    columns[6] = (
                        "IEI_UNSUPPORTED_LIFTOVER"
                        if current_filter in {"", ".", "PASS"}
                        else current_filter + ";IEI_UNSUPPORTED_LIFTOVER"
                    )
                    columns[7] = append_info(
                        columns[7], [f"IEI_LIFTOVER_REJECT_REASON={reason}"]
                    )
                bad.write("\t".join(columns) + "\n")
                continue

            supported += 1
            chrom, pos, _, ref, alt_raw = columns[:5]
            alternate_count = len(alt_raw.split(","))
            supported_alleles += alternate_count
            columns[7], removed = remove_malformed_allele_info(
                columns[7], alternate_count, info_numbers
            )
            if removed:
                repaired_records += 1
                removed_info_fields.update(removed)
            original_alts = ",".join(quote(alt, safe="") for alt in alt_raw.split(","))
            columns[7] = append_info(
                columns[7],
                [
                    "IEI_LIFTOVER",
                    "IEI_ORIGINAL_ASSEMBLY=GRCh37",
                    f"IEI_ORIGINAL_CHROM={quote(chrom, safe='')}",
                    f"IEI_ORIGINAL_POS={pos}",
                    f"IEI_ORIGINAL_REF={quote(ref, safe='')}",
                    f"IEI_ORIGINAL_ALT={original_alts}",
                    f"IEI_ORIGINAL_RECORD={total}",
                ],
            )
            columns[0] = ensembl_contig(chrom)
            good.write("\t".join(columns) + "\n")

    stats = {
        "input": str(Path(args.input).resolve()),
        "total_records": total,
        "supported_records": supported,
        "supported_allele_records": supported_alleles,
        "unsupported_records": unsupported,
        "unsupported_reasons": dict(sorted(reasons.items())),
        "records_with_removed_malformed_info": repaired_records,
        "removed_malformed_info_fields": dict(sorted(removed_info_fields.items())),
        "max_allele_length": args.max_allele_length,
    }
    Path(args.stats).write_text(json.dumps(stats, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
