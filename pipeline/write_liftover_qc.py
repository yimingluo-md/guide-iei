#!/usr/bin/env python3
"""Write liftover QC and reproducibility provenance JSON sidecars."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote


COMPLEMENT = str.maketrans("ACGTN", "TGCAN")


def text_open(path: str):
    return gzip.open(path, "rt") if path.lower().endswith(".gz") else open(path, "rt")


def sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def info_map(raw: str) -> dict[str, str]:
    result = {}
    for item in raw.split(";"):
        key, separator, value = item.partition("=")
        if key:
            result[key] = value if separator else "1"
    return result


def count_records(path: str) -> tuple[int, Counter[str]]:
    count = 0
    filters: Counter[str] = Counter()
    with text_open(path) as handle:
        for line in handle:
            if not line.startswith("#") and line.strip():
                count += 1
                columns = line.rstrip("\n").split("\t")
                if len(columns) > 6:
                    for value in columns[6].split(";"):
                        filters[value or "."] += 1
    return count, filters


def accepted_metrics(path: str) -> dict:
    total = allele_changed = reverse_complemented = 0
    original_records: set[str] = set()
    with text_open(path) as handle:
        for line in handle:
            if line.startswith("#") or not line.strip():
                continue
            columns = line.rstrip("\n").split("\t")
            if len(columns) < 8:
                continue
            total += 1
            ref, alt = columns[3].upper(), columns[4].upper()
            info = info_map(columns[7])
            if info.get("IEI_ORIGINAL_RECORD"):
                original_records.add(info["IEI_ORIGINAL_RECORD"])
            original_ref = unquote(info.get("IEI_ORIGINAL_REF", "")).upper()
            original_alt = unquote(info.get("IEI_ORIGINAL_ALT", "")).upper()
            if original_ref and original_alt and (ref, alt) != (original_ref, original_alt):
                allele_changed += 1
                if (
                    ref == original_ref.translate(COMPLEMENT)[::-1]
                    and alt == original_alt.translate(COMPLEMENT)[::-1]
                ):
                    reverse_complemented += 1
    return {
        "lifted_records": len(original_records) if original_records else total,
        "lifted_allele_records": total,
        "allele_changed_records": allele_changed,
        "reverse_complemented_records": reverse_complemented,
    }


def file_identity(path: str, hash_file: bool = True) -> dict:
    target = Path(path)
    stat = target.stat()
    value = {
        "path": str(target.resolve()),
        "size_bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }
    if hash_file:
        value["sha256"] = sha256(str(target))
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--lifted", required=True)
    parser.add_argument("--picard-reject", required=True)
    parser.add_argument("--unsupported", required=True)
    parser.add_argument("--pre-stats", required=True)
    parser.add_argument("--chain", required=True)
    parser.add_argument("--target-dict", required=True)
    parser.add_argument("--qc-output", required=True)
    parser.add_argument("--provenance-output", required=True)
    parser.add_argument("--picard-version", default="pinned in container")
    parser.add_argument("--pipeline-version", default="unknown")
    args = parser.parse_args()

    pre = json.loads(Path(args.pre_stats).read_text())
    picard_rejected, reject_filters = count_records(args.picard_reject)
    unsupported, unsupported_filters = count_records(args.unsupported)
    accepted = accepted_metrics(args.lifted)
    attempted = pre["total_records"]
    attempted_liftover_alleles = pre.get(
        "supported_allele_records", pre["supported_records"]
    )
    accounted_liftover_alleles = (
        accepted["lifted_allele_records"] + picard_rejected
    )
    all_accounted = (
        accounted_liftover_alleles == attempted_liftover_alleles
        and unsupported == pre["unsupported_records"]
    )
    warnings = []
    if not all_accounted:
        warnings.append(
            "Accepted plus Picard-rejected allele records or unsupported records "
            "do not reconcile with the prepared input."
        )
    if pre.get("records_with_removed_malformed_info", 0):
        warnings.append(
            "Malformed allele-indexed INFO values were removed before multiallelic "
            "splitting; see removed_malformed_info_fields."
        )
    qc = {
        "source_assembly": "GRCh37",
        "target_assembly": "GRCh38",
        "attempted_records": attempted,
        "attempted_liftover_allele_records": attempted_liftover_alleles,
        **accepted,
        "picard_rejected_records": picard_rejected,
        "unsupported_records": unsupported,
        "accounted_liftover_allele_records": accounted_liftover_alleles,
        "accounted_records": attempted if all_accounted else None,
        "all_records_accounted_for": all_accounted,
        "picard_rejection_filters": dict(sorted(reject_filters.items())),
        "unsupported_reasons": pre.get("unsupported_reasons", {}),
        "unsupported_filters": dict(sorted(unsupported_filters.items())),
        "records_with_removed_malformed_info": pre.get(
            "records_with_removed_malformed_info", 0
        ),
        "removed_malformed_info_fields": pre.get(
            "removed_malformed_info_fields", {}
        ),
        "warnings": warnings,
        "artifacts": {
            "lifted_vcf": str(Path(args.lifted).resolve()),
            "picard_reject_vcf": str(Path(args.picard_reject).resolve()),
            "unsupported_vcf": str(Path(args.unsupported).resolve()),
        },
    }
    provenance = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "pipeline_version": args.pipeline_version,
        "conversion": "GRCh37/hg19 to GRCh38",
        "input": file_identity(args.input),
        "output": file_identity(args.lifted, hash_file=False),
        "chain": file_identity(args.chain),
        "target_sequence_dictionary": file_identity(args.target_dict),
        "tool": {
            "name": "Picard LiftoverVcf",
            "version": args.picard_version,
        },
        "policy": {
            "canonical_assembly": "GRCh38",
            "validated_variant_scope": "primary-contig SNVs and short indels",
            "max_allele_length": pre.get("max_allele_length"),
            "unlifted_records_are_not_interpreted_as_reference": True,
        },
        "qc": qc,
    }
    Path(args.qc_output).write_text(json.dumps(qc, indent=2, sort_keys=True) + "\n")
    Path(args.provenance_output).write_text(
        json.dumps(provenance, indent=2, sort_keys=True) + "\n"
    )
    if not qc["all_records_accounted_for"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
