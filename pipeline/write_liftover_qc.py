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


def _trimmed(ref: str, alt: str) -> tuple[str, str]:
    """Strip the shared allele suffix then prefix (minimal representation)."""
    while len(ref) > 1 and len(alt) > 1 and ref[-1] == alt[-1]:
        ref, alt = ref[:-1], alt[:-1]
    while len(ref) > 1 and len(alt) > 1 and ref[0] == alt[0]:
        ref, alt = ref[1:], alt[1:]
    return ref, alt


def lifted_metrics(path: str) -> dict:
    total = allele_changed = renormalized = reverse_complemented = 0
    swaps = new_references = 0
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
                if (
                    ref == original_ref.translate(COMPLEMENT)[::-1]
                    and alt == original_alt.translate(COMPLEMENT)[::-1]
                ):
                    allele_changed += 1
                    reverse_complemented += 1
                elif _trimmed(ref, alt) == _trimmed(original_ref, original_alt):
                    # Same alleles after stripping shared padding: the
                    # post-liftover `bcftools norm -f` pass re-represented the
                    # record; this is not an assembly allele substitution.
                    # (Left-shifts through repeat runs still count as changed
                    # below — separating those needs the reference sequence.)
                    renormalized += 1
                else:
                    allele_changed += 1
            if "IEI_ASSEMBLY_ALLELE_SWAP" in info:
                swaps += 1
            if "IEI_LIFTOVER_NEW_REFERENCE" in info:
                new_references += 1
    # A record set with no IEI_ORIGINAL_RECORD provenance means the chain was
    # stripped somewhere: report that explicitly instead of silently
    # substituting the post-split allele-record count (a different quantity).
    provenance_missing = total > 0 and not original_records
    return {
        "lifted_records": len(original_records) if original_records else None,
        "provenance_missing": provenance_missing,
        "lifted_allele_records": total,
        "allele_changed_records": allele_changed,
        "renormalized_representation_records": renormalized,
        "reverse_complemented_records": reverse_complemented,
        "retained_assembly_allele_swap_records": swaps,
        "new_reference_records": new_references,
    }


def file_identity(
    path: str, hash_file: bool = True, checksum_sidecar: bool = False
) -> dict:
    target = Path(path)
    stat = target.stat()
    value = {
        "path": str(target.resolve()),
        "size_bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }
    sidecar = Path(str(target) + ".sha256.local")
    if checksum_sidecar and sidecar.is_file():
        fields = sidecar.read_text().strip().split()
        if fields:
            value["sha256"] = fields[0]
            value["sha256_source"] = str(sidecar.resolve())
    elif hash_file:
        value["sha256"] = sha256(str(target))
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--lifted-all", required=True)
    parser.add_argument("--lifted", required=True)
    parser.add_argument("--reference-corrections", required=True)
    parser.add_argument("--liftover-reject", required=True)
    parser.add_argument("--unsupported", required=True)
    parser.add_argument("--pre-stats", required=True)
    parser.add_argument("--classification-stats", required=True)
    parser.add_argument("--chain", required=True)
    parser.add_argument("--source-fasta", required=True)
    parser.add_argument("--target-fasta", required=True)
    parser.add_argument("--target-dict", required=True)
    parser.add_argument("--qc-output", required=True)
    parser.add_argument("--provenance-output", required=True)
    parser.add_argument("--bcftools-version", required=True)
    parser.add_argument("--plugin-commit", required=True)
    parser.add_argument("--pipeline-version", default="unknown")
    parser.add_argument(
        "--mt-convention-setting", default="auto",
        help="the liftover.mt_convention value the run was configured with",
    )
    parser.add_argument(
        "--bcftools-version-measured", default="",
        help="bcftools --version reported by the binary that actually ran the plugin",
    )
    args = parser.parse_args()

    pre = json.loads(Path(args.pre_stats).read_text())
    classification = json.loads(Path(args.classification_stats).read_text())
    rejected, reject_filters = count_records(args.liftover_reject)
    unsupported, unsupported_filters = count_records(args.unsupported)
    correction_count, _ = count_records(args.reference_corrections)
    accepted = lifted_metrics(args.lifted)
    raw_lifted, _ = count_records(args.lifted_all)
    raw_lifted_source_alleles = classification.get(
        "raw_lifted_source_allele_records", raw_lifted
    )
    attempted = pre["total_records"]
    attempted_liftover_alleles = pre.get(
        "supported_allele_records", pre["supported_records"]
    )
    accounted_liftover_alleles = raw_lifted_source_alleles + rejected
    all_accounted = (
        accounted_liftover_alleles == attempted_liftover_alleles
        and unsupported == pre["unsupported_records"]
        and accepted["lifted_allele_records"] + correction_count == raw_lifted
        and classification.get("raw_lifted_allele_records") == raw_lifted
    )
    warnings = []
    if not all_accounted:
        warnings.append(
            "Lifted, reference-correction, rejected, or unsupported records do "
            "not reconcile with the prepared input."
        )
    if accepted.get("provenance_missing"):
        warnings.append(
            "Lifted records carry no IEI_ORIGINAL_RECORD provenance; the "
            "per-source-record count is unavailable (reported as null), not "
            "silently substituted with the post-split allele-record count."
        )
    if correction_count:
        warnings.append(
            f"{correction_count} source call(s) became GRCh38 reference after "
            "allele-aware remapping. They were excluded from VEP and retained "
            "in the reference-correction audit VCF."
        )
    if classification.get("swap_records_without_called_genotypes", 0):
        warnings.append(
            "Some assembly allele swaps had no callable GT and were retained "
            "for review rather than classified as reference corrections."
        )
    if pre.get("records_with_removed_malformed_info", 0):
        warnings.append(
            "Malformed allele-indexed INFO values were removed before "
            "multiallelic splitting; see removed_malformed_info_fields."
        )
    if pre.get("records_with_removed_malformed_format", 0):
        warnings.append(
            "Malformed allele-indexed FORMAT values were removed only from "
            "affected records before liftover; see "
            "removed_malformed_format_fields. GT and well-formed sample fields "
            "were retained."
        )
    if pre.get("records_with_removed_liftover_incompatible_format", 0):
        warnings.append(
            "Valid non-diploid or String Number=G FORMAT values that "
            "BCFtools/liftover cannot safely remap were removed only from "
            "affected records; see "
            "removed_liftover_incompatible_format_fields. GT, GQ, AD, DP, "
            "and compatible fields were retained."
        )
    mt_convention = pre.get("mt_convention", "unresolved")
    mt_convention_source = pre.get("mt_convention_source", "unresolved")
    mt_passthrough = int(pre.get("mt_passthrough_records", 0) or 0)
    if mt_passthrough:
        warnings.append(
            f"{mt_passthrough} mitochondrial record(s) were treated as rCRS "
            "(identical to GRCh38 MT) and carried over without the hg19 chain "
            "after base-by-base verification against GRCh38 MT "
            f"(evidence: {mt_convention_source})."
        )
    if mt_convention_source == "default":
        warnings.append(
            "The input declared no mitochondrial contig length and no "
            "recognisable reference name; rCRS was assumed. Set "
            "liftover.mt_convention explicitly if this callset was aligned "
            "to UCSC hg19 chrM (NC_001807, 16,571 bp)."
        )
    qc = {
        "source_assembly": "GRCh37/hg19",
        "target_assembly": "GRCh38",
        "attempted_records": attempted,
        "attempted_liftover_allele_records": attempted_liftover_alleles,
        **accepted,
        "raw_lifted_allele_records": raw_lifted,
        "raw_lifted_source_allele_records": raw_lifted_source_alleles,
        "reference_correction_records": correction_count,
        "liftover_rejected_records": rejected,
        "unsupported_records": unsupported,
        "accounted_liftover_allele_records": accounted_liftover_alleles,
        "accounted_records": attempted if all_accounted else None,
        "all_records_accounted_for": all_accounted,
        "liftover_rejection_filters": dict(sorted(reject_filters.items())),
        "unsupported_reasons": pre.get("unsupported_reasons", {}),
        "unsupported_filters": dict(sorted(unsupported_filters.items())),
        "records_with_removed_malformed_info": pre.get(
            "records_with_removed_malformed_info", 0
        ),
        "removed_malformed_info_fields": pre.get(
            "removed_malformed_info_fields", {}
        ),
        "records_with_removed_malformed_format": pre.get(
            "records_with_removed_malformed_format", 0
        ),
        "removed_malformed_format_fields": pre.get(
            "removed_malformed_format_fields", {}
        ),
        "records_with_removed_liftover_incompatible_format": pre.get(
            "records_with_removed_liftover_incompatible_format", 0
        ),
        "removed_liftover_incompatible_format_fields": pre.get(
            "removed_liftover_incompatible_format_fields", {}
        ),
        "mt_convention": mt_convention,
        "mt_convention_source": mt_convention_source,
        "mt_contig_length": pre.get("mt_contig_length"),
        "mt_passthrough_records": mt_passthrough,
        "mt_passthrough_allele_records": int(
            pre.get("mt_passthrough_allele_records", 0) or 0
        ),
        "classification": classification,
        "warnings": warnings,
        "artifacts": {
            "lifted_vcf": str(Path(args.lifted).resolve()),
            "reference_correction_audit_vcf": str(
                Path(args.reference_corrections).resolve()
            ),
            "liftover_reject_vcf": str(Path(args.liftover_reject).resolve()),
            "unsupported_vcf": str(Path(args.unsupported).resolve()),
        },
    }
    provenance = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "pipeline_version": args.pipeline_version,
        "conversion": "GRCh37/hg19 to GRCh38",
        "input": file_identity(args.input),
        # The output's own hash makes cache reuse detect in-place damage,
        # not just size/mtime drift; its index rides along when present.
        "output": file_identity(args.lifted),
        **(
            {"output_index": file_identity(f"{args.lifted}.tbi")}
            if Path(f"{args.lifted}.tbi").is_file() else {}
        ),
        "chain": file_identity(args.chain),
        "source_reference": file_identity(
            args.source_fasta, hash_file=False, checksum_sidecar=True
        ),
        "target_reference": file_identity(args.target_fasta, hash_file=False),
        "target_sequence_dictionary": file_identity(args.target_dict),
        "tool": {
            "name": "BCFtools/liftover",
            # The configured pin (used for cache validity) and the version
            # the container actually reported; a mismatch is visible here.
            "bcftools_version": args.bcftools_version,
            "bcftools_version_measured": args.bcftools_version_measured or None,
            "bcftools_version_matches_pin": (
                (args.bcftools_version_measured == args.bcftools_version)
                if args.bcftools_version_measured else None
            ),
            "executed_in_container": True,
            "plugin_commit": args.plugin_commit,
            "publication_doi": "10.1093/bioinformatics/btae038",
        },
        "policy": {
            "canonical_assembly": "GRCh38",
            "source_reference_preset": "UCSC hg19 primary assembly",
            "mt_convention_setting": args.mt_convention_setting,
            "mt_convention": mt_convention,
            "mt_convention_source": mt_convention_source,
            "rcrs_mitochondrial_records_bypass_chain_after_grch38_verification": True,
            "validated_variant_scope": "primary-contig SNVs and short indels",
            "max_allele_length": pre.get("max_allele_length"),
            "reference_corrections_are_excluded_from_annotation": True,
            "reference_corrections_are_retained_in_audit_vcf": True,
            "unlifted_records_are_not_interpreted_as_reference": True,
            "malformed_allele_indexed_fields_are_removed_per_record": True,
        },
        "qc": qc,
    }
    Path(args.qc_output).write_text(json.dumps(qc, indent=2, sort_keys=True) + "\n")
    Path(args.provenance_output).write_text(
        json.dumps(provenance, indent=2, sort_keys=True) + "\n"
    )
    return 0 if qc["all_records_accounted_for"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
