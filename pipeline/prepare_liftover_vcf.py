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
import math
import re
from collections import Counter
from pathlib import Path
from urllib.parse import quote


PRIMARY_CONTIG = re.compile(r"^(?:chr)?(?:[1-9]|1[0-9]|2[0-2]|X|Y|M|MT)$", re.I)
SEQUENCE = re.compile(r"^[ACGTN]+$", re.I)
INFO_DEFINITION = re.compile(r"^##INFO=<ID=([^,>]+),Number=([^,>]+),")
FORMAT_DEFINITION = re.compile(
    r"^##FORMAT=<ID=([^,>]+),Number=([^,>]+),Type=([^,>]+),"
)


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


def genotype_ploidy(raw_gt: str) -> int | None:
    """Return ploidy encoded by GT, including partially missing genotypes."""
    if raw_gt in {"", "."}:
        return None
    alleles = re.split(r"[/|]", raw_gt)
    return len(alleles) if alleles else None


def expected_format_value_counts(
    number: str, alternate_count: int, raw_gt: str
) -> set[int]:
    allele_count = alternate_count + 1
    if number == "A":
        return {alternate_count}
    if number == "R":
        return {allele_count}
    if number != "G":
        return set()
    ploidy = genotype_ploidy(raw_gt)
    if ploidy is not None:
        return {math.comb(allele_count + ploidy - 1, ploidy)}
    # A VCF can omit GT while retaining likelihoods. Haploid and diploid are
    # the common valid cases; do not guess beyond those two representations.
    return {allele_count, math.comb(allele_count + 1, 2)}


def remove_malformed_allele_format(
    columns: list[str],
    alternate_count: int,
    format_definitions: dict[str, tuple[str, str]],
) -> tuple[list[str], list[str]]:
    """Remove malformed Number=A/R/G FORMAT fields from one VCF record.

    BCFtools/liftover must remap allele-indexed arrays when an assembly allele
    changes. A malformed array makes that operation ambiguous and aborts the
    whole file. We therefore remove only the inconsistent field on the
    affected record, from every sample, while retaining GT and all other
    well-formed sample evidence.
    """
    if len(columns) < 10 or columns[8] in {"", "."}:
        return [], []
    format_keys = columns[8].split(":")
    gt_index = format_keys.index("GT") if "GT" in format_keys else None
    malformed_indices: set[int] = set()
    incompatible_indices: set[int] = set()

    for index, key in enumerate(format_keys):
        definition = format_definitions.get(key)
        if not definition or definition[0] not in {"A", "R", "G"}:
            continue
        number, value_type = definition
        encoded_widths: list[int] = []
        for sample in columns[9:]:
            values = sample.split(":")
            raw_value = values[index] if index < len(values) else "."
            encoded_widths.append(len(raw_value.split(",")))
            raw_gt = (
                values[gt_index]
                if gt_index is not None and gt_index < len(values)
                else "."
            )
            if raw_value in {"", "."}:
                continue
            expected = expected_format_value_counts(
                number, alternate_count, raw_gt
            )
            if expected and len(raw_value.split(",")) not in expected:
                malformed_indices.add(index)
                break

        if index in malformed_indices or number != "G":
            continue
        diploid_width = math.comb(alternate_count + 2, 2)
        # BCFtools/liftover 1.20 remaps Number=G arrays as diploid when a
        # target-reference allele must be introduced, and does not support
        # Number=G String values. Valid haploid (or other-ploidy) arrays can
        # therefore abort an otherwise safe conversion. Remove only that
        # field on that record; GT, GQ, AD, DP, and all compatible fields stay.
        if value_type in {"String", "Character"} or max(encoded_widths) != diploid_width:
            incompatible_indices.add(index)

    removed_indices = malformed_indices | incompatible_indices
    if not removed_indices:
        return [], []
    retained_indices = [
        index for index in range(len(format_keys)) if index not in removed_indices
    ]
    columns[8] = ":".join(format_keys[index] for index in retained_indices) or "."
    for sample_index in range(9, len(columns)):
        values = columns[sample_index].split(":")
        values.extend(["."] * (len(format_keys) - len(values)))
        columns[sample_index] = (
            ":".join(values[index] for index in retained_indices) or "."
        )
    return (
        [format_keys[index] for index in sorted(malformed_indices)],
        [format_keys[index] for index in sorted(incompatible_indices)],
    )


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
    removed_format_fields: Counter[str] = Counter()
    removed_incompatible_format_fields: Counter[str] = Counter()
    repaired_info_records = 0
    repaired_format_records = 0
    repaired_incompatible_format_records = 0
    info_numbers: dict[str, str] = {}
    format_definitions: dict[str, tuple[str, str]] = {}
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
            if line.startswith("##FORMAT=<"):
                definition = FORMAT_DEFINITION.match(line)
                if definition:
                    format_definitions[definition.group(1)] = (
                        definition.group(2), definition.group(3)
                    )
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
                repaired_info_records += 1
                removed_info_fields.update(removed)
            removed_format, removed_incompatible_format = remove_malformed_allele_format(
                columns, alternate_count, format_definitions
            )
            if removed_format:
                repaired_format_records += 1
                removed_format_fields.update(removed_format)
            if removed_incompatible_format:
                repaired_incompatible_format_records += 1
                removed_incompatible_format_fields.update(
                    removed_incompatible_format
                )
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
        "records_with_removed_malformed_info": repaired_info_records,
        "removed_malformed_info_fields": dict(sorted(removed_info_fields.items())),
        "records_with_removed_malformed_format": repaired_format_records,
        "removed_malformed_format_fields": dict(
            sorted(removed_format_fields.items())
        ),
        "records_with_removed_liftover_incompatible_format": (
            repaired_incompatible_format_records
        ),
        "removed_liftover_incompatible_format_fields": dict(
            sorted(removed_incompatible_format_fields.items())
        ),
        "max_allele_length": args.max_allele_length,
    }
    Path(args.stats).write_text(json.dumps(stats, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
