#!/usr/bin/env python3
"""Detect the reference assembly declared or implied by a VCF header.

Only GRCh37/hg19 and GRCh38/hg38 are in scope.  A missing/ambiguous declaration
is deliberately reported as ``unknown`` so callers can require an explicit
user choice rather than guessing from variant coordinates.
"""
from __future__ import annotations

import argparse
import gzip
import json
import re
from pathlib import Path


ASSEMBLIES = {"GRCh37", "GRCh38"}
CHR1_LENGTHS = {"249250621": "GRCh37", "248956422": "GRCh38"}


def text_open(path: str | Path):
    value = str(path)
    return gzip.open(value, "rt") if value.lower().endswith(".gz") else open(value, "rt")


def assembly_from_reference(value: str) -> str | None:
    normalized = value.lower()
    if re.search(r"(?:grch[\s_.-]*38|hg[\s_.-]*38)", normalized):
        return "GRCh38"
    if re.search(r"(?:grch[\s_.-]*37|hg[\s_.-]*19|(?:^|[/_.-])b37(?:[/_.-]|$)|hs37d5)", normalized):
        return "GRCh37"
    return None


def detect_vcf_assembly(path: str | Path) -> dict:
    evidence: list[str] = []
    warnings: list[str] = []
    candidates: set[str] = set()
    references: list[str] = []
    original_references: list[str] = []
    chr1_length: str | None = None
    target_marker: str | None = None
    liftover_source: str | None = None

    with text_open(path) as handle:
        for line in handle:
            if line.startswith("##iei_target_assembly="):
                target_marker = line.partition("=")[2].strip()
                if target_marker in ASSEMBLIES:
                    candidates.add(target_marker)
                    evidence.append(f"pipeline target marker: {target_marker}")
            elif line.startswith("##iei_liftover=<"):
                match = re.search(r"SourceAssembly=([^,>]+)", line)
                if match:
                    liftover_source = match.group(1)
            elif line.startswith("##reference="):
                reference = line.partition("=")[2].strip()
                references.append(reference)
                detected = assembly_from_reference(reference)
                if detected:
                    candidates.add(detected)
                    evidence.append(f"reference header: {reference}")
            elif line.startswith("##iei_original_reference="):
                original_references.append(line.partition("=")[2].strip())
            elif line.startswith("##contig=<"):
                payload = line.split("<", 1)[1].rsplit(">", 1)[0]
                values = dict(
                    item.split("=", 1) for item in payload.split(",") if "=" in item
                )
                contig = values.get("ID", "")
                if contig in {"1", "chr1"} and values.get("length"):
                    chr1_length = values["length"]
                    detected = CHR1_LENGTHS.get(chr1_length)
                    if detected:
                        candidates.add(detected)
                        evidence.append(f"chromosome 1 length: {chr1_length}")
                    else:
                        warnings.append(
                            f"unrecognized chromosome 1 length: {chr1_length}"
                        )
            elif line.startswith("#CHROM"):
                break
            elif not line.startswith("#"):
                break

    if len(candidates) == 1:
        assembly = next(iter(candidates))
        confidence = "high"
    elif len(candidates) > 1:
        assembly = "conflict"
        confidence = "none"
        warnings.append("VCF header contains conflicting assembly evidence")
    else:
        assembly = "unknown"
        confidence = "none"
        warnings.append(
            "VCF header does not identify GRCh37/hg19 or GRCh38/hg38"
        )
    return {
        "path": str(Path(path).resolve()),
        "assembly": assembly,
        "confidence": confidence,
        "evidence": evidence,
        "warnings": warnings,
        "reference_headers": references,
        "original_reference_headers": original_references,
        "chromosome_1_length": chr1_length,
        "pipeline_target_marker": target_marker,
        "liftover_source_assembly": liftover_source,
        "lifted_from_grch37": bool(
            target_marker == "GRCh38"
            and (
                bool(liftover_source and liftover_source.startswith("GRCh37"))
                or any(
                    assembly_from_reference(value) == "GRCh37"
                    for value in original_references
                )
            )
        ),
    }


def resolve_input_assembly(path: str | Path, requested: str) -> dict:
    if requested not in {"auto", *ASSEMBLIES}:
        raise ValueError("input assembly must be auto, GRCh37, or GRCh38")
    detected = detect_vcf_assembly(path)
    observed = detected["assembly"]
    if observed == "conflict":
        raise ValueError(
            "VCF header contains conflicting GRCh37/GRCh38 evidence; "
            "correct the header before annotation"
        )
    if requested == "auto":
        if observed not in ASSEMBLIES:
            raise ValueError(
                "input assembly is ambiguous; select GRCh38 or GRCh37/hg19 explicitly"
            )
        resolved = observed
    else:
        if observed in ASSEMBLIES and observed != requested:
            raise ValueError(
                f"input was declared as {requested}, but its VCF header indicates {observed}"
            )
        resolved = requested
    return {**detected, "requested": requested, "resolved": resolved}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vcf", required=True)
    parser.add_argument(
        "--requested", choices=("auto", "GRCh37", "GRCh38"), default="auto"
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        result = resolve_input_assembly(args.vcf, args.requested)
    except (OSError, EOFError, ValueError) as exc:
        parser.error(str(exc))
    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        print(result["resolved"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
