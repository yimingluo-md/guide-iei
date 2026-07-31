#!/usr/bin/env python3
"""Separate GRCh37 reference corrections from reviewable GRCh38 variants.

BCFtools/liftover can recognize when a source ALT is the destination reference
and remaps GT plus allele-indexed fields. If every called sample is consequently
reference on GRCh38, the source record is an assembly-reference correction, not
a patient variant. Such records remain in an audit VCF and do not enter VEP.
"""
from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path
from urllib.parse import quote


def text_open(path: str):
    return gzip.open(path, "rt") if path.lower().endswith(".gz") else open(path, "rt")


def parse_info(raw: str) -> list[tuple[str, str | None]]:
    if raw in {"", "."}:
        return []
    values = []
    for item in raw.split(";"):
        key, separator, value = item.partition("=")
        if key:
            values.append((key, value if separator else None))
    return values


def info_dict(values: list[tuple[str, str | None]]) -> dict[str, str]:
    return {key: "" if value is None else value for key, value in values}


def replace_info(
    values: list[tuple[str, str | None]],
    replacements: dict[str, str | None],
) -> str:
    retained = [(key, value) for key, value in values if key not in replacements]
    retained.extend(replacements.items())
    return ";".join(
        key if value is None else f"{key}={value}" for key, value in retained
    ) or "."


def called_genotype_state(columns: list[str]) -> tuple[bool, bool]:
    """Return (has_called_genotype, has_nonreference_allele)."""
    if len(columns) < 10:
        return False, False
    format_keys = columns[8].split(":")
    try:
        gt_index = format_keys.index("GT")
    except ValueError:
        return False, False
    called = nonreference = False
    for sample in columns[9:]:
        fields = sample.split(":")
        if gt_index >= len(fields):
            continue
        alleles = fields[gt_index].replace("|", "/").split("/")
        for allele in alleles:
            if allele in {"", "."}:
                continue
            try:
                value = int(allele)
            except ValueError:
                continue
            called = True
            if value > 0:
                nonreference = True
    return called, nonreference


def source_alleles(raw: str) -> tuple[str, str] | None:
    alleles = raw.split(",")
    if len(alleles) < 2 or not alleles[0] or not alleles[1]:
        return None
    return alleles[0], alleles[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--retained", required=True)
    parser.add_argument("--reference-corrections", required=True)
    parser.add_argument("--stats", required=True)
    args = parser.parse_args()

    total = retained = corrections = swaps = new_references = 0
    unavailable_swap_genotypes = 0
    original_records: set[str] = set()
    source_allele_records: set[tuple[str, str]] = set()
    correction_original_records: set[str] = set()
    new_reference_source_alleles: set[tuple[str, str]] = set()
    saw_columns = False

    with (
        text_open(args.input) as incoming,
        Path(args.retained).open("w") as accepted,
        Path(args.reference_corrections).open("w") as audit,
    ):
        for line in incoming:
            if line.startswith("#CHROM"):
                headers = (
                    '##INFO=<ID=IEI_ASSEMBLY_ALLELE_SWAP,Number=0,Type=Flag,Description="A GRCh37 ALT became the GRCh38 REF; genotypes and allele-indexed annotations were remapped">\n'
                    '##INFO=<ID=IEI_LIFTOVER_NEW_REFERENCE,Number=0,Type=Flag,Description="GRCh38 introduced a reference allele not present among the source VCF alleles">\n'
                    '##INFO=<ID=IEI_REFERENCE_CORRECTION,Number=0,Type=Flag,Description="Source ALT became GRCh38 REF and all called samples are reference after allele-aware remapping; retained only in the liftover audit VCF">\n'
                    '##INFO=<ID=IEI_REFERENCE_CORRECTION_REASON,Number=1,Type=String,Description="Reason a lifted source record was excluded from variant annotation">\n'
                )
                accepted.write(headers)
                audit.write(headers)
                accepted.write(line)
                audit.write(line)
                saw_columns = True
                continue
            if line.startswith("#"):
                accepted.write(line)
                audit.write(line)
                continue
            if not line.strip():
                continue
            if not saw_columns:
                parser.error("VCF #CHROM header was not found before records")

            columns = line.rstrip("\n").split("\t")
            if len(columns) < 8:
                parser.error("malformed lifted VCF record")
            total += 1
            info_values = parse_info(columns[7])
            info = info_dict(info_values)
            original_record = info.get("IEI_ORIGINAL_RECORD", "")
            if original_record:
                original_records.add(original_record)
            source_allele = info.get("SRC_REF_ALT", "")
            if not source_allele:
                source_allele = ",".join(
                    (
                        info.get("IEI_ORIGINAL_REF", ""),
                        info.get("IEI_ORIGINAL_ALT", ""),
                    )
                )
            source_identity = (original_record, source_allele)
            if original_record and source_allele != ",":
                source_allele_records.add(source_identity)

            replacements: dict[str, str | None] = {}
            source = source_alleles(info.get("SRC_REF_ALT", ""))
            if source:
                source_ref, source_alt = source
                replacements["IEI_ORIGINAL_REF"] = quote(source_ref, safe="")
                replacements["IEI_ORIGINAL_ALT"] = quote(source_alt, safe="")
            if not info.get("IEI_ORIGINAL_CHROM") and info.get("SRC_CHROM"):
                replacements["IEI_ORIGINAL_CHROM"] = quote(
                    info["SRC_CHROM"], safe=""
                )
            if not info.get("IEI_ORIGINAL_POS") and info.get("SRC_POS"):
                replacements["IEI_ORIGINAL_POS"] = info["SRC_POS"]

            swap_raw = info.get("IEI_LIFTOVER_SWAP", "")
            try:
                swap = int(swap_raw) if swap_raw else 0
            except ValueError:
                swap = 0
            called, nonreference = called_genotype_state(columns)

            if swap > 0:
                if called and not nonreference:
                    corrections += 1
                    if original_record:
                        correction_original_records.add(original_record)
                    replacements["IEI_REFERENCE_CORRECTION"] = None
                    replacements["IEI_REFERENCE_CORRECTION_REASON"] = (
                        "SOURCE_ALT_IS_GRCH38_REFERENCE_ALL_CALLS_REFERENCE"
                    )
                    columns[7] = replace_info(info_values, replacements)
                    audit.write("\t".join(columns) + "\n")
                    continue
                swaps += 1
                replacements["IEI_ASSEMBLY_ALLELE_SWAP"] = None
                if not called:
                    unavailable_swap_genotypes += 1
            elif swap < 0:
                new_references += 1
                if original_record and source_allele != ",":
                    new_reference_source_alleles.add(source_identity)
                replacements["IEI_LIFTOVER_NEW_REFERENCE"] = None

            retained += 1
            columns[7] = replace_info(info_values, replacements)
            accepted.write("\t".join(columns) + "\n")

    stats = {
        "raw_lifted_allele_records": total,
        "raw_lifted_source_allele_records": len(source_allele_records),
        "raw_lifted_original_records": len(original_records),
        "retained_allele_records": retained,
        "reference_correction_allele_records": corrections,
        "reference_correction_original_records": len(correction_original_records),
        "retained_assembly_allele_swap_records": swaps,
        "new_reference_records": new_references,
        "new_reference_source_allele_records": len(new_reference_source_alleles),
        "swap_records_without_called_genotypes": unavailable_swap_genotypes,
    }
    Path(args.stats).write_text(json.dumps(stats, indent=2, sort_keys=True) + "\n")
    if retained + corrections != total:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
