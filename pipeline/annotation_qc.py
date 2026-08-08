#!/usr/bin/env python3
"""Create a machine-readable and human-readable annotation completeness report.

This is deliberately an annotation *coverage* check, not a variant pathogenicity
classifier.  It measures only records for which an annotation is biologically
applicable (for example, AlphaMissense on missense variants and LOFTEE on
predicted loss-of-function variants).
"""

from __future__ import annotations

import argparse
import gzip
import html
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import yaml


MISSING = {"", ".", "-"}
PLOF_CONSEQUENCES = {
    "frameshift_variant",
    "splice_acceptor_variant",
    "splice_donor_variant",
    "start_lost",
    "stop_gained",
}
NON_TRANSCRIPT_CONSEQUENCES = {
    "downstream_gene_variant",
    "intergenic_variant",
    "upstream_gene_variant",
}
SPLICEAI_FIELDS = (
    "SpliceAI_pred_DS_AG",
    "SpliceAI_pred_DS_AL",
    "SpliceAI_pred_DS_DG",
    "SpliceAI_pred_DS_DL",
)
DEFAULT_CRITICAL_DBNSFP = ("CADD_phred", "AlphaMissense_score")
PROMOTERAI_FIELDS = (
    "PromoterAI_score",
    "promoterAI_score",
    "promoterAI_promoterAI",
    "PromoterAI_promoterAI",
    "promoterAI",
    "PromoterAI",
)
LOGOFUNC_SCORE_FIELDS = (
    "LoGoFunc_neutral", "LoGoFunc_GOF", "LoGoFunc_LOF",
)


def open_text(path: Path):
    return gzip.open(path, "rt") if path.name.endswith(".gz") else path.open()


def present(value: str | None) -> bool:
    return value is not None and value not in MISSING


def parse_info(raw: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in raw.split(";"):
        if not item:
            continue
        if "=" in item:
            key, value = item.split("=", 1)
            result[key] = value
        else:
            result[item] = "1"
    return result


def csq_header_fields(line: str) -> list[str]:
    match = re.search(r"Format:\s*([^\">]+)", line)
    if not match:
        raise ValueError("CSQ header has no Format field list")
    return match.group(1).strip().split("|")


def csq_entries(info: dict[str, str], fields: list[str]) -> list[dict[str, str]]:
    raw = info.get("CSQ", "")
    if not raw:
        return []
    entries = []
    for annotation in raw.split(","):
        values = annotation.split("|")
        values += [""] * (len(fields) - len(values))
        entries.append(dict(zip(fields, values)))
    return entries


def has_consequence(entry: dict[str, str], expected: set[str]) -> bool:
    return bool(set(entry.get("Consequence", "").split("&")) & expected)


def is_mane(entry: dict[str, str]) -> bool:
    return any(
        present(entry.get(field))
        for field in ("MANE_SELECT", "MANE_PLUS_CLINICAL", "MANE")
    )


# PTC_calc_status values that are documented, deliberate refusals to apply
# the 50 bp rule — correct behaviour, not missing annotation coverage.
DELIBERATE_PTC_SKIP_PREFIXES = (
    "not_applicable_single_exon_transcript",
    "unsupported_transcript_biotype:",
    "transcript_version_mismatch",
    "outside_cds",
)


def preferred_entry(entries: list[dict[str, str]]) -> dict[str, str]:
    """Pick the clinically preferred CSQ entry: MANE, then PICK, then order.

    With --flag_pick_allele_gene every transcript is retained and VEP's file
    order is arbitrary, so "first entry" would attribute per-variant metrics
    to a random transcript.
    """
    for entry in entries:
        if is_mane(entry):
            return entry
    for entry in entries:
        if entry.get("PICK") == "1":
            return entry
    return entries[0]


def record_key(columns: list[str]) -> str:
    return f"{columns[0]}-{columns[1]}-{columns[3]}-{columns[4]}"


def metric(name: str, eligible: int, annotated: int, threshold: float) -> dict:
    coverage = None if eligible == 0 else annotated / eligible
    if eligible == 0:
        status = "NOT_APPLICABLE"
    elif coverage is not None and coverage < threshold:
        status = "WARN"
    else:
        status = "PASS"
    return {
        "name": name,
        "eligible_records": eligible,
        "annotated_records": annotated,
        "coverage": coverage,
        "warning_threshold": threshold,
        "status": status,
    }


def _configured_field_names(config: dict) -> dict[str, list[str]]:
    plugins = config.get("plugins") or {}
    dbnsfp = plugins.get("dbNSFP") or {}
    configured = list(dbnsfp.get("columns") or [])
    qc = config.get("annotation_qc") or {}
    critical = list(qc.get("critical_dbnsfp_fields") or DEFAULT_CRITICAL_DBNSFP)
    return {"configured_dbnsfp": configured, "critical_dbnsfp": critical}


def build_report(config_path: Path, vcf_path: Path, max_examples: int | None = None) -> dict:
    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    qc_config = config.get("annotation_qc") or {}
    thresholds = qc_config.get("warn_below") or {}
    max_examples = max_examples or int(qc_config.get("max_missing_examples", 20))
    names = _configured_field_names(config)

    counters = Counter()
    consequence_counts = Counter()
    clinvar_significance = Counter()
    missing_examples: dict[str, list[str]] = {}
    csq_fields: list[str] | None = None
    sample_names: list[str] = []
    dbnsfp_annotated = Counter()
    header_info_fields: set[str] = set()
    vep_header = None
    clinvar_aa_release = None

    def note_missing(label: str, key: str) -> None:
        bucket = missing_examples.setdefault(label, [])
        if len(bucket) < max_examples:
            bucket.append(key)

    with open_text(vcf_path) as handle:
        for line in handle:
            if line.startswith("##VEP="):
                vep_header = line.rstrip("\n")
            if line.startswith("##INFO=<ID="):
                match = re.match(r"##INFO=<ID=([^,>]+)", line)
                if match:
                    header_info_fields.add(match.group(1))
                if line.startswith("##INFO=<ID=CSQ"):
                    csq_fields = csq_header_fields(line)
                if line.startswith("##INFO=<ID=ClinVar_path_aa_match"):
                    release_match = re.search(r"ClinVar release ([^)]+)", line)
                    if release_match:
                        clinvar_aa_release = release_match.group(1)
                continue
            if line.startswith("#CHROM"):
                sample_names = line.rstrip("\n").split("\t")[9:]
                continue
            if line.startswith("#"):
                continue
            if csq_fields is None:
                raise ValueError("VEP CSQ header was not found")
            columns = line.rstrip("\n").split("\t")
            if len(columns) < 8:
                raise ValueError(f"malformed VCF record: {line.rstrip()}")
            counters["records"] += 1
            if columns[6] == "PASS":
                counters["pass_records"] += 1
            key = record_key(columns)
            info = parse_info(columns[7])
            entries = csq_entries(info, csq_fields)
            if entries:
                counters["records_with_csq"] += 1

            record_consequences: set[str] = set()
            for entry in entries:
                record_consequences.update(
                    item for item in entry.get("Consequence", "").split("&") if item
                )
            consequence_counts.update(record_consequences)

            missense = [e for e in entries if has_consequence(e, {"missense_variant"})]
            if missense:
                counters["missense_eligible"] += 1
                # Count over configured AND critical fields: a critical field
                # outside plugins.dbNSFP.columns previously reported 0%
                # coverage forever, a permanent WARN no data could clear.
                for field in {*names["configured_dbnsfp"], *names["critical_dbnsfp"]}:
                    if any(present(entry.get(field)) for entry in missense):
                        dbnsfp_annotated[field] += 1
                for field in names["critical_dbnsfp"]:
                    if not any(present(entry.get(field)) for entry in missense):
                        note_missing(field, key)

            snv = len(columns[3]) == 1 and all(
                len(alt) == 1 for alt in columns[4].split(",")
            )
            if snv and missense:
                counters["logofunc_missense_snv_eligible"] += 1
                allele_available = any(
                    entry.get("LoGoFunc_allele_available") == "1"
                    for entry in missense
                )
                exact_matches = [
                    entry for entry in missense
                    if entry.get("LoGoFunc_match") == "allele_transcript_protein"
                    and present(entry.get("LoGoFunc_prediction"))
                    and all(present(entry.get(field)) for field in LOGOFUNC_SCORE_FIELDS)
                ]
                if allele_available:
                    counters["logofunc_allele_available"] += 1
                if exact_matches:
                    counters["logofunc_exact_match"] += 1
                    counters[
                        "logofunc_class:"
                        + preferred_entry(exact_matches)["LoGoFunc_prediction"]
                    ] += 1
                elif allele_available:
                    note_missing("LoGoFunc_transcript_protein_match", key)

            plof = [e for e in entries if has_consequence(e, PLOF_CONSEQUENCES)]
            if plof:
                counters["plof_eligible"] += 1
                if any(present(entry.get("LoF")) for entry in plof):
                    counters["loftee_annotated"] += 1
                else:
                    note_missing("LOFTEE", key)
                if any(entry.get("LoF") == "HC" for entry in plof):
                    counters["loftee_hc"] += 1
                if any(entry.get("LoF") == "LC" for entry in plof):
                    counters["loftee_lc"] += 1

            frameshift = [
                entry for entry in entries if has_consequence(entry, {"frameshift_variant"})
            ]
            if frameshift:
                statuses = {
                    entry.get("PTC_calc_status", "")
                    for entry in frameshift
                    if present(entry.get("PTC_calc_status"))
                }
                for status in statuses:
                    counters[f"ptc50_status:{status}"] += 1
                recomputed = any(
                    entry.get("PTC_calc_status", "").startswith("ok")
                    and present(entry.get("LoF_50_BP_RULE_PTC"))
                    for entry in frameshift
                )
                deliberate_only = bool(statuses) and all(
                    status.startswith(DELIBERATE_PTC_SKIP_PREFIXES)
                    for status in statuses
                )
                if recomputed:
                    counters["ptc50_frameshift_eligible"] += 1
                    counters["ptc50_recomputed"] += 1
                elif deliberate_only:
                    # A documented refusal (single-exon transcript, unsupported
                    # biotype, version mismatch, outside CDS) is correct
                    # behaviour, not missing coverage. Counting these in the
                    # denominator produced false WARNs and remediation lists
                    # of variants needing no remediation.
                    counters["ptc50_not_applicable"] += 1
                else:
                    counters["ptc50_frameshift_eligible"] += 1
                    note_missing("LOFTEE_PTC_50BP", key)
                if any(entry.get("LoF_50_BP_RULE_changed") == "1" for entry in frameshift):
                    counters["ptc50_changed"] += 1

            # The bundled MANE SpliceAI table is a transcript/splice resource,
            # not a promoter/intergenic resource. A MANE-labelled upstream or
            # downstream consequence therefore must not inflate its denominator.
            mane = [
                entry
                for entry in entries
                if is_mane(entry)
                and any(
                    consequence not in NON_TRANSCRIPT_CONSEQUENCES
                    for consequence in entry.get("Consequence", "").split("&")
                    if consequence
                )
            ]
            if snv and mane:
                counters["spliceai_mane_snv_eligible"] += 1
                if any(
                    present(entry.get(field))
                    for entry in mane
                    for field in SPLICEAI_FIELDS
                ):
                    counters["spliceai_annotated"] += 1
                else:
                    note_missing("SpliceAI", key)

            clinvar_entries = [
                entry for entry in entries if present(entry.get("ClinVar_CLNSIG"))
            ]
            if clinvar_entries:
                counters["clinvar_exact_match_records"] += 1
                terms: set[str] = set()
                for entry in clinvar_entries:
                    terms.update(
                        term
                        for term in re.split(r"[&|/]", entry.get("ClinVar_CLNSIG", ""))
                        if term
                    )
                clinvar_significance.update(terms)
            if info.get("ClinVar_path_aa_match") == "1":
                counters["clinvar_pathogenic_aa_match_records"] += 1
            if present(info.get("ClinGen_ERepo")):
                counters["clingen_erepo_match_records"] += 1
                counters["clingen_erepo_assertions"] += len(
                    info.get("ClinGen_ERepo", "").split(",")
                )
            haplotype_entries = info.get("IEI_HAPLOTYPE_FRAME", "").split(",")
            haplotype_statuses = {
                item.split("|")[3]
                for item in haplotype_entries
                if len(item.split("|")) >= 4
            }
            if "FRAME_RESTORED_CONFIRMED" in haplotype_statuses:
                counters["haplotype_frame_restored_confirmed_records"] += 1
            if "FRAME_RESTORATION_PARTIAL_CONFIRMED" in haplotype_statuses:
                counters["haplotype_frame_restoration_partial_records"] += 1
            if "FRAME_RESTORING_POSSIBLE_UNPHASED" in haplotype_statuses:
                counters["haplotype_frame_restoring_possible_records"] += 1

            for field in ("RepeatMasker", "SegDup"):
                if any(present(entry.get(field)) for entry in entries):
                    counters[f"{field.lower()}_overlap_records"] += 1
            if any(
                present(entry.get(field))
                for entry in entries
                for field in PROMOTERAI_FIELDS
            ):
                counters["promoterai_annotated_records"] += 1

    if csq_fields is None:
        raise ValueError("VEP CSQ header was not found")

    metrics: list[dict] = []
    plugins = config.get("plugins") or {}
    dbnsfp_config = plugins.get("dbNSFP") or {}
    loftee_config = plugins.get("LoF") or {}
    spliceai_config = plugins.get("SpliceAI") or {}
    logofunc_config = plugins.get("LoGoFunc") or {}
    missense_n = counters["missense_eligible"]
    db_threshold = float(thresholds.get("dbnsfp_missense", 0.80))
    for field in names["critical_dbnsfp"]:
        schema_present = field in csq_fields
        item = metric(
            f"dbNSFP {field} on missense records",
            missense_n,
            dbnsfp_annotated[field],
            db_threshold,
        )
        item["field"] = field
        item["schema_present"] = schema_present
        if not dbnsfp_config.get("enabled"):
            item["status"] = "SKIPPED_DISABLED"
        elif not schema_present:
            item["status"] = (
                "FAIL"
                if dbnsfp_config.get("required")
                else "SKIPPED_NOT_INSTALLED"
            )
        metrics.append(item)

    lof_metric = metric(
        "LOFTEE on predicted loss-of-function records",
        counters["plof_eligible"],
        counters["loftee_annotated"],
        float(thresholds.get("loftee_plof", 0.90)),
    )
    lof_metric["schema_present"] = all(
        field in csq_fields for field in ("LoF", "LoF_filter", "LoF_flags", "LoF_info")
    )
    if not loftee_config.get("enabled"):
        lof_metric["status"] = "SKIPPED_DISABLED"
    elif not lof_metric["schema_present"]:
        lof_metric["status"] = (
            "FAIL" if loftee_config.get("required") else "SKIPPED_NOT_INSTALLED"
        )
    metrics.append(lof_metric)

    ptc_config = ((config.get("post_processing") or {}).get("loftee_ptc_50bp") or {})
    ptc_metric = metric(
        "PTC-based LOFTEE 50-bp rule on frameshift records",
        counters["ptc50_frameshift_eligible"],
        counters["ptc50_recomputed"],
        float(thresholds.get("loftee_ptc_frameshift", 0.90)),
    )
    ptc_metric["schema_present"] = all(
        field in csq_fields
        for field in (
            "PTC_dist_from_last_exon",
            "LoF_50_BP_RULE_original",
            "LoF_50_BP_RULE_PTC",
            "PTC_calc_status",
        )
    )
    if not ptc_config.get("enabled"):
        ptc_metric["status"] = "SKIPPED_DISABLED"
    elif not ptc_metric["schema_present"]:
        ptc_metric["status"] = (
            "FAIL" if ptc_config.get("required") else "SKIPPED_NOT_INSTALLED"
        )
    metrics.append(ptc_metric)

    haplotype_config = (
        (config.get("post_processing") or {}).get("haplotype_consequences") or {}
    )
    haplotype_schema = "IEI_HAPLOTYPE_FRAME" in header_info_fields
    haplotype_metric = {
        "name": "Sample-specific Haplosaurus consequence post-processing",
        "eligible_records": counters["ptc50_frameshift_eligible"],
        "annotated_records": (
            counters["haplotype_frame_restored_confirmed_records"]
            + counters["haplotype_frame_restoration_partial_records"]
            + counters["haplotype_frame_restoring_possible_records"]
        ),
        "coverage": None,
        "warning_threshold": None,
        "schema_present": haplotype_schema,
        "status": (
            "SKIPPED_DISABLED"
            if not haplotype_config.get("enabled")
            else "PASS"
            if haplotype_schema
            else "FAIL"
            if haplotype_config.get("required")
            else "SKIPPED_NOT_INSTALLED"
        ),
    }
    metrics.append(haplotype_metric)

    splice_metric = metric(
        "SpliceAI on MANE SNV records",
        counters["spliceai_mane_snv_eligible"],
        counters["spliceai_annotated"],
        float(thresholds.get("spliceai_mane_snv", 0.90)),
    )
    splice_metric["schema_present"] = all(field in csq_fields for field in SPLICEAI_FIELDS)
    if not spliceai_config.get("enabled"):
        splice_metric["status"] = "SKIPPED_DISABLED"
    elif not splice_metric["schema_present"]:
        splice_metric["status"] = (
            "FAIL" if spliceai_config.get("required") else "SKIPPED_NOT_INSTALLED"
        )
    metrics.append(splice_metric)

    logofunc_schema = all(
        field in csq_fields
        for field in (
            "LoGoFunc_allele_available", "LoGoFunc_match",
            "LoGoFunc_prediction", *LOGOFUNC_SCORE_FIELDS,
        )
    )
    logofunc_eligible = counters["logofunc_missense_snv_eligible"]
    logofunc_allele_metric = metric(
        "LoGoFunc allele availability on missense SNV records",
        logofunc_eligible,
        counters["logofunc_allele_available"],
        0.0,
    )
    logofunc_allele_metric["schema_present"] = logofunc_schema
    logofunc_exact_metric = metric(
        "LoGoFunc exact transcript and protein-change matches",
        counters["logofunc_allele_available"],
        counters["logofunc_exact_match"],
        0.0,
    )
    logofunc_exact_metric["schema_present"] = logofunc_schema
    for item in (logofunc_allele_metric, logofunc_exact_metric):
        if not logofunc_config.get("enabled"):
            item["status"] = "SKIPPED_DISABLED"
        elif not logofunc_schema:
            item["status"] = "SKIPPED_NOT_INSTALLED"
    if (
        logofunc_config.get("enabled")
        and logofunc_schema
        and counters["logofunc_allele_available"]
        and not counters["logofunc_exact_match"]
    ):
        logofunc_exact_metric["status"] = "WARN"
    metrics.extend((logofunc_allele_metric, logofunc_exact_metric))

    clingen_config = config.get("clingen_erepo") or {}
    clingen_schema = all(
        field in header_info_fields
        for field in ("ClinGen_ERepo", "ClinGen_ERepo_count")
    )
    metrics.append({
        "name": "ClinGen Evidence Repository exact allele annotation",
        "eligible_records": counters["records"],
        "annotated_records": counters["clingen_erepo_match_records"],
        "assertions": counters["clingen_erepo_assertions"],
        "coverage": None,
        "warning_threshold": None,
        "schema_present": clingen_schema,
        "status": (
            "SKIPPED_DISABLED" if not clingen_config.get("enabled")
            else "PASS" if clingen_schema
            else "FAIL" if clingen_config.get("required")
            else "SKIPPED_NOT_INSTALLED"
        ),
        "note": "Match count is descriptive; absence of a ClinGen assertion is not an annotation failure.",
    })

    statuses = {item["status"] for item in metrics}
    promoter_schema = any(field in csq_fields for field in PROMOTERAI_FIELDS)
    promoter_status = (
        "PASS"
        if promoter_schema and counters["promoterai_annotated_records"]
        else "WARN"
        if promoter_schema
        else "SKIPPED_NOT_INSTALLED"
    )
    # promoterAI participates in the overall verdict: an installed plugin
    # producing zero annotations previously warned only inside its detail
    # block while the banner stayed PASS.
    statuses.add(promoter_status if promoter_status in {"PASS", "WARN", "FAIL"} else "PASS")
    overall = "FAIL" if "FAIL" in statuses else "WARN" if "WARN" in statuses else "PASS"

    return {
        "schema_version": 1,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "purpose": "annotation completeness; not a clinical classification",
        "overall_status": overall,
        "input_vcf": str(vcf_path.resolve()),
        "config": str(config_path.resolve()),
        "annotation_profile": {
            "assembly": (config.get("reference") or {}).get("assembly"),
            "vep_image_tag": (config.get("container") or {}).get("vep_image_tag"),
            "vep_output_header": vep_header,
            "dbNSFP_version": (
                ((config.get("plugins") or {}).get("dbNSFP") or {}).get("version")
            ),
            "dbNSFP_required": bool(
                ((config.get("plugins") or {}).get("dbNSFP") or {}).get("required")
            ),
            "LOFTEE_required": bool(
                ((config.get("plugins") or {}).get("LoF") or {}).get("required")
            ),
            "SpliceAI_required": bool(
                ((config.get("plugins") or {}).get("SpliceAI") or {}).get("required")
            ),
            "ClinGen_ERepo_required": bool(
                (config.get("clingen_erepo") or {}).get("required")
            ),
            "LoGoFunc_version": logofunc_config.get("version"),
            "LOFTEE_PTC_50BP_required": bool(
                (
                    (config.get("post_processing") or {}).get(
                        "loftee_ptc_50bp"
                    )
                    or {}
                ).get("required")
            ),
            "LOFTEE_PTC_50BP_gtf": (
                (
                    (config.get("post_processing") or {}).get(
                        "loftee_ptc_50bp"
                    )
                    or {}
                ).get("gtf")
            ),
            "Haplosaurus_required": bool(
                (
                    (config.get("post_processing") or {}).get(
                        "haplotype_consequences"
                    )
                    or {}
                ).get("required")
            ),
            "ClinVar_aa_reference_release": clinvar_aa_release,
        },
        "summary": {
            "records": counters["records"],
            "pass_records": counters["pass_records"],
            "non_pass_records": counters["records"] - counters["pass_records"],
            "records_with_csq": counters["records_with_csq"],
            "samples": sample_names,
        },
        "metrics": metrics,
        "details": {
            "consequence_record_counts": dict(sorted(consequence_counts.items())),
            "dbnsfp_configured_field_coverage": {
                field: {
                    "annotated_records": dbnsfp_annotated[field],
                    "eligible_missense_records": missense_n,
                    "coverage": (
                        None if missense_n == 0 else dbnsfp_annotated[field] / missense_n
                    ),
                }
                for field in names["configured_dbnsfp"]
            },
            "loftee": {
                "eligible_records": counters["plof_eligible"],
                "annotated_records": counters["loftee_annotated"],
                "HC_records": counters["loftee_hc"],
                "LC_records": counters["loftee_lc"],
            },
            "loftee_ptc_50bp": {
                "eligible_frameshift_records": counters["ptc50_frameshift_eligible"],
                "recomputed_records": counters["ptc50_recomputed"],
                "not_applicable_records": counters["ptc50_not_applicable"],
                "changed_records": counters["ptc50_changed"],
                "status_record_counts": {
                    key.removeprefix("ptc50_status:"): value
                    for key, value in sorted(counters.items())
                    if key.startswith("ptc50_status:")
                },
            },
            "haplotype_consequences": {
                "schema_present": haplotype_schema,
                "confirmed_frame_restored_records": counters[
                    "haplotype_frame_restored_confirmed_records"
                ],
                "partial_frame_restoration_records": counters[
                    "haplotype_frame_restoration_partial_records"
                ],
                "possible_unphased_frame_restoring_records": counters[
                    "haplotype_frame_restoring_possible_records"
                ],
                "interpretation": (
                    "Confirmed events may be excluded from isolated LoF review; "
                    "partial and possible-unphased events remain visible."
                ),
            },
            "spliceai": {
                "eligible_mane_snv_records": counters["spliceai_mane_snv_eligible"],
                "annotated_records": counters["spliceai_annotated"],
                "scope": "configured Ensembl MANE masked SNV table; indels are not counted",
            },
            "clinvar": {
                "exact_match_records": counters["clinvar_exact_match_records"],
                "significance_terms": dict(sorted(clinvar_significance.items())),
                "pathogenic_amino_acid_match_records": counters[
                    "clinvar_pathogenic_aa_match_records"
                ],
            },
            "regions": {
                "RepeatMasker_overlap_records": counters["repeatmasker_overlap_records"],
                "SegDup_overlap_records": counters["segdup_overlap_records"],
            },
            "promoterAI": {
                "status": promoter_status,
                "schema_present": promoter_schema,
                "annotated_records": counters["promoterai_annotated_records"],
            },
            "logofunc": {
                "schema_present": logofunc_schema,
                "eligible_missense_snv_records": logofunc_eligible,
                "allele_available_records": counters["logofunc_allele_available"],
                "exact_transcript_protein_match_records": counters["logofunc_exact_match"],
                "prediction_class_counts": {
                    key.removeprefix("logofunc_class:"): value
                    for key, value in sorted(counters.items())
                    if key.startswith("logofunc_class:")
                },
                "interpretation": (
                    "Exact matches require genomic allele, Ensembl transcript stable ID, "
                    "residue position, and amino-acid substitution agreement. Missing is not neutral."
                ),
            },
            "missing_examples": missing_examples,
        },
    }


def _format_percent(value: float | None) -> str:
    return "n/a" if value is None else f"{100 * value:.1f}%"


def render_html(report: dict) -> str:
    rows = []
    for item in report["metrics"]:
        rows.append(
            "<tr>"
            f"<td>{html.escape(item['name'])}</td>"
            f"<td>{item['eligible_records']}</td>"
            f"<td>{item['annotated_records']}</td>"
            f"<td>{_format_percent(item['coverage'])}</td>"
            f"<td class='{item['status'].lower()}'>{item['status']}</td>"
            "</tr>"
        )
    summary = report["summary"]
    details = html.escape(json.dumps(report["details"], indent=2, sort_keys=True))
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Annotation completeness certificate</title>
<style>
body {{ font: 15px system-ui, sans-serif; max-width: 1100px; margin: 2rem auto; padding: 0 1rem; color: #17202a }}
table {{ border-collapse: collapse; width: 100%; margin: 1rem 0 }}
th, td {{ border: 1px solid #ccd1d1; padding: .55rem; text-align: left }}
th {{ background: #f4f6f7 }} .pass {{ color: #16733b; font-weight: 700 }}
.warn {{ color: #9a6700; font-weight: 700 }} .fail {{ color: #b42318; font-weight: 700 }}
.not_applicable {{ color: #5d6d7e }} code {{ overflow-wrap: anywhere }}
details pre {{ white-space: pre-wrap; background: #f7f9f9; padding: 1rem }}
</style></head><body>
<h1>Annotation completeness certificate</h1>
<p><strong>Overall: {html.escape(report['overall_status'])}</strong></p>
<p>This report measures annotation coverage; it is not a clinical classification.</p>
<p><code>{html.escape(report['input_vcf'])}</code></p>
<p>{summary['records']} records · {summary['pass_records']} PASS ·
{len(summary['samples'])} sample(s)</p>
<table><thead><tr><th>Check</th><th>Eligible</th><th>Annotated</th>
<th>Coverage</th><th>Status</th></tr></thead><tbody>{''.join(rows)}</tbody></table>
<p>promoterAI: {html.escape(report['details']['promoterAI']['status'])}</p>
<p>LoGoFunc exact transcript/protein matches: {report['details']['logofunc']['exact_transcript_protein_match_records']}</p>
<details><summary>Full details</summary><pre>{details}</pre></details>
<p>Created {html.escape(report['created_utc'])}</p>
</body></html>
"""


def write_report(report: dict, json_path: Path, html_path: Path) -> None:
    json_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    html_path.write_text(render_html(report), encoding="utf-8")


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--vcf", required=True, type=Path)
    parser.add_argument("--json", type=Path)
    parser.add_argument("--html", type=Path)
    parser.add_argument("--max-missing-examples", type=int)
    args = parser.parse_args(argv)
    json_path = args.json or Path(str(args.vcf) + ".annotation_qc.json")
    html_path = args.html or Path(str(args.vcf) + ".annotation_qc.html")
    report = build_report(args.config, args.vcf, args.max_missing_examples)
    write_report(report, json_path, html_path)
    print(
        f"annotation completeness: {report['overall_status']} "
        f"(JSON: {json_path}; HTML: {html_path})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
