#!/usr/bin/env python3
"""Build a reproducible, donor-aware immune context layer over SCREEN V4.

The original 448-column categorical SCREEN matrix remains immutable.  This
module enriches its profiles with current ENCODE experiment metadata, applies
a pinned AlphaGenome-inspired audit policy, and selects a conservative
baseline-primary-cell view.  Categorical classes are counted, never averaged.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import sqlite3
import subprocess
import time
import urllib.parse
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

try:
    from .cell_ontology import (
        CELL_ONTOLOGY_RELEASE,
        CELL_ONTOLOGY_SOURCE_URL,
        ontology_ancestors,
        parse_cell_ontology,
    )
except ImportError:  # Direct `python pipeline/screen_immune_curation.py` use.
    from cell_ontology import (  # type: ignore
        CELL_ONTOLOGY_RELEASE,
        CELL_ONTOLOGY_SOURCE_URL,
        ontology_ancestors,
        parse_cell_ontology,
    )


CLASS_LABELS = {
    0: "inactive",
    1: "PLS",
    2: "pELS",
    3: "dELS",
    4: "CA-H3K4me3",
    5: "CA-CTCF",
    6: "CA-TF",
    7: "CA",
    8: "TF",
}
TIER_RANK = {
    "accessibility_only": 1,
    "partial_classification": 2,
    "full_classification": 3,
}
ACCEPTABLE_HEALTH = {"", "unknown", "healthy", "apparently healthy", "normal"}
COMPACT_METADATA_SCHEMA_VERSION = 3
ASSAY_CAPABILITY_KEYS = {
    "DNase": "chromatin_accessibility",
    "H3K4me3": "promoter_classification",
    "H3K27ac": "enhancer_classification",
    "CTCF": "ctcf_classification",
}
IEI_LINEAGE_COVERAGE = {
    "T cell": ("T cell",),
    "B cell": ("B cell",),
    "NK/ILC": ("NK/ILC",),
    "Monocyte/macrophage": ("Monocyte/macrophage",),
    "Dendritic cell": ("Dendritic cell",),
    "Neutrophil/granulocyte": ("Granulocyte",),
    "Plasma cell": ("plasma cell",),
    "Mast cell": ("Mast cell",),
    "Hematopoietic progenitor": ("Hematopoietic progenitor",),
    "Erythroid/megakaryocyte": ("Erythroid/megakaryocyte",),
}


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def flatten_strings(value: Any) -> list[str]:
    """Collect readable scalar values from heterogeneous ENCODE objects."""
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, list):
        result: list[str] = []
        for item in value:
            result.extend(flatten_strings(item))
        return result
    if isinstance(value, dict):
        preferred = (
            "treatment_term_name", "disease_term_name", "term_name",
            "accession", "name", "summary", "description",
        )
        for key in preferred:
            if key in value:
                selected = flatten_strings(value[key])
                if selected:
                    return selected
    return []


def unique_strings(values: Iterable[Any]) -> list[str]:
    result: set[str] = set()
    for value in values:
        result.update(item.strip() for item in flatten_strings(value) if item.strip())
    return sorted(result)


def experiment_biosamples(row: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    for replicate in row.get("replicates") or []:
        if not isinstance(replicate, dict):
            continue
        biosample = (replicate.get("library") or {}).get("biosample") or {}
        if isinstance(biosample, dict):
            result.append(biosample)
    return result


def audit_categories(row: dict[str, Any]) -> dict[str, list[str]]:
    result: dict[str, set[str]] = defaultdict(set)
    for severity, entries in (row.get("audit") or {}).items():
        for entry in entries or []:
            if not isinstance(entry, dict):
                continue
            category = str(entry.get("category") or "").strip()
            if category:
                result[str(severity).upper()].add(category)
    return {severity: sorted(categories) for severity, categories in result.items()}


def compact_experiment(
    row: dict[str, Any], requested_accession: str, policy: dict[str, Any]
) -> dict[str, Any]:
    biosamples = experiment_biosamples(row)
    audits = audit_categories(row)
    critical = {
        value.casefold()
        for value in policy["rules"]["exclude_not_compliant_categories"]
    }
    error_categories = sorted({
        category for severity, categories in audits.items()
        if severity == "ERROR" for category in categories
    })
    critical_not_compliant = sorted({
        category for severity, categories in audits.items()
        if severity == "NOT_COMPLIANT"
        for category in categories if category.casefold() in critical
    })
    tolerated_audit_warnings = [
        {"severity": severity, "category": category}
        for severity, categories in sorted(audits.items())
        for category in categories
        if severity == "WARNING"
        or (severity == "NOT_COMPLIANT" and category.casefold() not in critical)
    ]

    treatments: list[Any] = []
    diseases: list[Any] = []
    health_statuses: list[Any] = []
    modifications: list[Any] = []
    synchronizations: list[Any] = []
    donors: list[Any] = []
    life_stages: list[Any] = []
    sexes: list[Any] = []
    for biosample in biosamples:
        treatments.extend(biosample.get("treatments") or [])
        diseases.append(biosample.get("disease_term_name") or [])
        health_statuses.append(biosample.get("health_status") or "")
        modifications.extend(biosample.get("applied_modifications") or [])
        modifications.extend(biosample.get("genetic_modifications") or [])
        synchronization = biosample.get("synchronization")
        if synchronization:
            synchronizations.append(synchronization)
        donor = biosample.get("donor")
        if donor:
            donors.append(donor)
        life_stages.append(biosample.get("life_stage") or "")
        sexes.append(biosample.get("sex") or "")
    for replicate in row.get("replicates") or []:
        library = (replicate or {}).get("library") or {}
        treatments.extend(library.get("treatments") or [])

    ontology = row.get("biosample_ontology") or {}
    status = str(row.get("status") or "")
    audit_pass = not error_categories and not critical_not_compliant
    return {
        "requested_accession": requested_accession,
        "resolved_accession": str(row.get("accession") or requested_accession),
        "status": status,
        "assay_title": str(row.get("assay_title") or ""),
        "biosample_summary": str(row.get("biosample_summary") or ""),
        "simple_biosample_summary": str(row.get("simple_biosample_summary") or ""),
        "ontology_id": str(ontology.get("term_id") or ""),
        "ontology_name": str(ontology.get("term_name") or ""),
        "sample_type": str(ontology.get("classification") or ""),
        "donor_accessions": unique_strings(donors),
        "life_stages": unique_strings(life_stages),
        "sexes": unique_strings(sexes),
        "treatments": unique_strings(treatments),
        "has_treatments": bool(treatments),
        "diseases": unique_strings(diseases),
        "has_disease_terms": any(bool(value) for value in diseases),
        "health_statuses": unique_strings(health_statuses),
        "modifications": unique_strings(modifications),
        "has_modifications": bool(modifications),
        "synchronizations": unique_strings(synchronizations),
        "has_synchronizations": bool(synchronizations),
        "audits": audits,
        "error_audits": error_categories,
        "critical_not_compliant_audits": critical_not_compliant,
        "tolerated_audit_warnings": tolerated_audit_warnings,
        "audit_pass": audit_pass,
        "retrieved_at": utc_now(),
    }


def query_encode_direct(accession: str) -> dict[str, Any]:
    completed = subprocess.run(
        [
            "curl", "-fsSL", "--connect-timeout", "30", "--max-time", "180",
            "--retry", "5", "--retry-all-errors", "--retry-delay", "2",
            f"https://www.encodeproject.org/experiments/{accession}/?format=json",
        ],
        check=True,
        capture_output=True,
    )
    return json.loads(completed.stdout)


def query_encode_batch(accessions: list[str]) -> list[tuple[str, dict[str, Any]]]:
    """Retrieve embedded metadata for multiple experiments in one request."""
    parameters: list[tuple[str, str]] = [("type", "Experiment")]
    parameters.extend(("accession", accession) for accession in accessions)
    for field in (
        "accession", "status", "assay_title", "biosample_summary",
        "simple_biosample_summary", "biosample_ontology", "audit",
        "replicates.library.biosample", "replicates.library.treatments",
    ):
        parameters.append(("field", field))
    parameters.extend((("format", "json"), ("limit", "all")))
    url = "https://www.encodeproject.org/search/?" + urllib.parse.urlencode(parameters)
    completed = subprocess.run(
        [
            "curl", "-fsSL", "--connect-timeout", "30", "--max-time", "240",
            "--retry", "5", "--retry-all-errors", "--retry-delay", "2", url,
        ],
        check=True,
        capture_output=True,
    )
    rows = json.loads(completed.stdout).get("@graph", [])
    by_accession = {str(row.get("accession") or ""): row for row in rows}
    result: list[tuple[str, dict[str, Any]]] = []
    for accession in accessions:
        row = by_accession.get(accession)
        if row is None:
            # Retired accessions can redirect to a replacement and therefore
            # are resolved individually without losing the requested key.
            row = query_encode_direct(accession)
        result.append((accession, row))
    return result


def unique_experiment_accessions(selection: dict[str, Any]) -> list[str]:
    return sorted({
        assay["experiment_accession"]
        for profile in selection["immune_biosamples"]
        for assay in profile["assays"].values()
        if assay is not None
    })


def enrich_metadata(args: argparse.Namespace) -> None:
    selection = json.loads(args.selection.read_text())
    policy = json.loads(args.policy.read_text())
    required = unique_experiment_accessions(selection)
    if args.output.exists():
        existing = json.loads(args.output.read_text())
        # Cache rows bake the audit-policy verdict in at write time
        # (audit_pass etc. come from compact_experiment(policy)), so a policy
        # edit must invalidate them: reusing them and then re-stamping the
        # NEW policy's sha actively defeated the integrity check downstream.
        experiments = (
            existing.get("experiments", {})
            if existing.get("compact_metadata_schema_version") == COMPACT_METADATA_SCHEMA_VERSION
            and existing.get("policy_sha256") == sha256_file(args.policy)
            else {}
        )
    else:
        experiments: dict[str, dict[str, Any]] = {}
    pending = [accession for accession in required if accession not in experiments]
    completed_count = 0
    batches = [pending[start:start + 30] for start in range(0, len(pending), 30)]
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(query_encode_batch, batch) for batch in batches]
        for future in concurrent.futures.as_completed(futures):
            for accession, row in future.result():
                experiments[accession] = compact_experiment(row, accession, policy)
                completed_count += 1
            atomic_json(args.output, {
                "schema_version": 1,
                "compact_metadata_schema_version": COMPACT_METADATA_SCHEMA_VERSION,
                "source": "ENCODE experiment REST search API",
                "created_at": utc_now(),
                "complete": False,
                "selection_sha256": sha256_file(args.selection),
                "policy_sha256": sha256_file(args.policy),
                "required_experiment_count": len(required),
                "experiments": experiments,
            })
            print(
                f"ENCODE experiment metadata: {completed_count}/{len(pending)} new "
                f"({len(experiments)}/{len(required)} total)",
                flush=True,
            )
    missing = sorted(set(required) - set(experiments))
    if missing:
        raise RuntimeError(f"metadata cache is incomplete: {len(missing)} missing")
    experiments = {accession: experiments[accession] for accession in required}
    atomic_json(args.output, {
        "schema_version": 1,
        "compact_metadata_schema_version": COMPACT_METADATA_SCHEMA_VERSION,
        "source": "ENCODE experiment REST search API",
        "created_at": utc_now(),
        "complete": True,
        "selection_sha256": sha256_file(args.selection),
        "policy_sha256": sha256_file(args.policy),
        "required_experiment_count": len(required),
        "experiments": experiments,
    })


def profile_curation(
    profile: dict[str, Any], experiments: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    metadata = profile["metadata"]
    assay_experiments = [
        (assay_name, experiments[assay["experiment_accession"]])
        for assay_name, assay in profile["assays"].items() if assay is not None
    ]
    experiment_rows = [row for _assay_name, row in assay_experiments]
    reasons: list[str] = []
    ontology_id = str(metadata.get("ontology_id") or "")
    sample_type = str(metadata.get("sample_type") or "")
    cell_slims = {str(value).casefold() for value in metadata.get("cell_slims") or []}
    lineage = profile["lineage"]
    if "leukocyte" in cell_slims or lineage == "Hematopoietic progenitor":
        immune_scope = "core_immune_or_hematopoietic"
    else:
        immune_scope = "immune_adjacent"
    if not ontology_id.startswith("CL:"):
        reasons.append("not_cell_ontology")
    if sample_type.casefold() != "primary cell":
        reasons.append("not_primary_cell")
    if immune_scope != "core_immune_or_hematopoietic":
        reasons.append("not_core_immune_or_hematopoietic_cell")
    if any(row["status"].casefold() != "released" for row in experiment_rows):
        reasons.append("experiment_not_released")
    if any(not row["audit_pass"] for row in experiment_rows):
        reasons.append("experiment_failed_audit_policy")

    treatments = set(metadata.get("treatments") or [])
    diseases = set(metadata.get("diseases") or [])
    has_treatments = bool(treatments)
    has_disease_terms = bool(diseases)
    has_modifications = False
    has_synchronizations = False
    modifications: set[str] = set()
    synchronizations: set[str] = set()
    health_statuses: set[str] = set()
    experiment_donors: set[str] = set()
    for row in experiment_rows:
        treatments.update(row["treatments"])
        diseases.update(row["diseases"])
        has_treatments = has_treatments or row["has_treatments"]
        has_disease_terms = has_disease_terms or row["has_disease_terms"]
        has_modifications = has_modifications or row["has_modifications"]
        has_synchronizations = has_synchronizations or row["has_synchronizations"]
        modifications.update(row["modifications"])
        synchronizations.update(row["synchronizations"])
        health_statuses.update(row["health_statuses"])
        experiment_donors.update(row["donor_accessions"])
    audit_warnings = [
        {
            "assay": assay_name,
            "experiment_accession": row["requested_accession"],
            **warning,
        }
        for assay_name, row in assay_experiments
        for warning in row.get("tolerated_audit_warnings", [])
    ]
    reported_unhealthy = sorted({
        value for value in health_statuses
        if value.casefold() not in ACCEPTABLE_HEALTH
    })
    if has_treatments:
        reasons.append("treated")
    if has_disease_terms or reported_unhealthy:
        reasons.append("disease_or_nonhealthy_status")
    if has_modifications:
        reasons.append("genetically_or_experimentally_modified")
    if has_synchronizations:
        reasons.append("synchronized_or_cell_cycle_arrested")

    selection_donor = str(profile.get("donor_accession") or "")
    if len(experiment_donors) > 1:
        reasons.append("inconsistent_experiment_donors")
    if selection_donor and experiment_donors and selection_donor not in experiment_donors:
        reasons.append("selection_experiment_donor_mismatch")
    donor = selection_donor or (next(iter(experiment_donors)) if len(experiment_donors) == 1 else "")
    if not donor:
        reasons.append("donor_unreported")

    return {
        "profile_index": profile["index"],
        "screen_name": profile["screen_name"],
        "display_name": metadata.get("display_name") or "",
        "ontology_id": ontology_id,
        "ontology_name": metadata.get("ontology_name") or "",
        "sample_type": sample_type,
        "lineage": lineage,
        "immune_scope": immune_scope,
        "evidence_tier": profile["evidence_tier"],
        "assays_available": profile["assays_available"],
        "donor_accession": donor,
        "life_stages": sorted({
            *metadata.get("life_stages", []),
            *(value for row in experiment_rows for value in row["life_stages"]),
        }),
        "sexes": sorted({
            *metadata.get("sexes", []),
            *(value for row in experiment_rows for value in row["sexes"]),
        }),
        "treatments": sorted(treatments),
        "has_treatments": has_treatments,
        "diseases": sorted(diseases),
        "has_disease_terms": has_disease_terms,
        "health_statuses": sorted(health_statuses),
        "modifications": sorted(modifications),
        "has_modifications": has_modifications,
        "synchronizations": sorted(synchronizations),
        "has_synchronizations": has_synchronizations,
        "experiment_accessions": sorted(row["requested_accession"] for row in experiment_rows),
        "audit_warnings": audit_warnings,
        "has_tolerated_audit_warnings": bool(audit_warnings),
        "baseline_eligible": not reasons,
        "exclusion_reasons": sorted(set(reasons)),
    }


def assay_capabilities(members: list[dict[str, Any]]) -> dict[str, int]:
    counts = {value: 0 for value in ASSAY_CAPABILITY_KEYS.values()}
    counts["any_specific_classification"] = 0
    counts["full_classification_panel"] = 0
    for member in members:
        assays = set(member.get("assays_available", []))
        if not assays:
            # Compatibility for old synthetic or exported rows that predate
            # assay-level fields. The evidence tier is less specific but safe.
            if member.get("evidence_tier") == "full_classification":
                assays = set(ASSAY_CAPABILITY_KEYS)
            elif member.get("evidence_tier") == "partial_classification":
                assays = {"DNase", "H3K27ac"}
            else:
                assays = {"DNase"}
        for assay, key in ASSAY_CAPABILITY_KEYS.items():
            counts[key] += int(assay in assays)
        specific = bool(assays & {"H3K4me3", "H3K27ac", "CTCF"})
        counts["any_specific_classification"] += int(specific)
        counts["full_classification_panel"] += int(
            set(ASSAY_CAPABILITY_KEYS).issubset(assays)
        )
    return counts


def annotate_context_hierarchy(
    contexts: list[dict[str, Any]], ontology: dict[str, Any]
) -> list[dict[str, Any]]:
    ancestors = ontology_ancestors(ontology)
    selected = {row["ontology_id"] for row in contexts}
    by_ontology = {row["ontology_id"]: row for row in contexts}
    donor_sets = {
        row["ontology_id"]: {member["donor_accession"] for member in row["members"]}
        for row in contexts
    }
    for ontology_id in selected:
        if ontology_id not in ontology["terms"]:
            raise RuntimeError(f"context term missing from pinned Cell Ontology: {ontology_id}")
        expected_name = ontology["terms"][ontology_id].get("name", "")
        by_ontology[ontology_id]["ontology_release_name"] = expected_name
        selected_ancestors = ancestors[ontology_id] & selected
        direct_parents = {
            parent for parent in selected_ancestors
            if not any(
                parent in ancestors[other]
                for other in selected_ancestors if other != parent
            )
        }
        by_ontology[ontology_id]["ancestor_context_ids"] = sorted(
            by_ontology[parent]["context_id"] for parent in selected_ancestors
        )
        by_ontology[ontology_id]["direct_parent_context_ids"] = sorted(
            by_ontology[parent]["context_id"] for parent in direct_parents
        )
    for context in contexts:
        context_id = context["context_id"]
        context["descendant_context_ids"] = sorted(
            row["context_id"] for row in contexts
            if context_id in row["ancestor_context_ids"]
        )
        context["direct_child_context_ids"] = sorted(
            row["context_id"] for row in contexts
            if context_id in row["direct_parent_context_ids"]
        )
        context["is_nested_context"] = bool(context["ancestor_context_ids"])
        context["is_summary_parent"] = bool(context["descendant_context_ids"])
        related = set(context["ancestor_context_ids"]) | set(context["descendant_context_ids"])
        context["related_contexts_sharing_donors"] = [
            {
                "context_id": by_ontology_id["context_id"],
                "shared_donor_count": len(
                    donor_sets[context["ontology_id"]] & donor_sets[by_ontology_id["ontology_id"]]
                ),
            }
            for by_ontology_id in contexts
            if by_ontology_id["context_id"] in related
            and donor_sets[context["ontology_id"]] & donor_sets[by_ontology_id["ontology_id"]]
        ]
    return contexts


def choose_contexts(
    profiles: list[dict[str, Any]], ontology: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    by_context_donor: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for profile in profiles:
        if profile["baseline_eligible"]:
            by_context_donor[(profile["ontology_id"], profile["donor_accession"])].append(profile)

    representatives: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for (ontology_id, _donor), candidates in by_context_donor.items():
        candidates.sort(
            key=lambda row: (
                -TIER_RANK[row["evidence_tier"]],
                -len(row["assays_available"]),
                row["profile_index"],
            )
        )
        selected = dict(candidates[0])
        selected["donor_representative"] = True
        selected["duplicate_profile_indices"] = [
            row["profile_index"] for row in candidates[1:]
        ]
        representatives[ontology_id].append(selected)

    contexts = []
    for ontology_id in sorted(representatives):
        members = sorted(representatives[ontology_id], key=lambda row: row["profile_index"])
        warnings = [
            warning for member in members for warning in member.get("audit_warnings", [])
        ]
        contexts.append({
            "context_id": "cl_" + ontology_id.split(":", 1)[1],
            "ontology_id": ontology_id,
            "ontology_name": members[0]["ontology_name"],
            "lineages": sorted({row["lineage"] for row in members}),
            "donor_count": len(members),
            "member_profile_indices": [row["profile_index"] for row in members],
            "tier_counts": dict(sorted(Counter(row["evidence_tier"] for row in members).items())),
            "life_stages": sorted({value for row in members for value in row["life_stages"]}),
            "sexes": sorted({value for row in members for value in row["sexes"]}),
            "assay_capable_donors": assay_capabilities(members),
            "profiles_with_tolerated_audit_warnings": sum(
                bool(row.get("audit_warnings")) for row in members
            ),
            "tolerated_audit_warning_counts": dict(sorted(Counter(
                f'{warning["severity"]}: {warning["category"]}' for warning in warnings
            ).items())),
            "members": members,
        })
    if ontology is not None:
        annotate_context_hierarchy(contexts, ontology)
    return contexts


def summarize_context_codes(codes: bytes, context: dict[str, Any]) -> dict[str, Any]:
    members = context["members"]
    member_codes = [(member, codes[member["profile_index"]]) for member in members]
    capabilities = context.get("assay_capable_donors") or assay_capabilities(members)
    exact = Counter(CLASS_LABELS[code] for _member, code in member_codes)
    positive = [(member, code) for member, code in member_codes if code != 0]
    classified = [
        (member, code) for member, code in positive
        if set(member.get("assays_available", [])) & {"H3K4me3", "H3K27ac", "CTCF"}
        or (
            not member.get("assays_available")
            and member["evidence_tier"] != "accessibility_only"
        )
    ]
    group_for_code = {
        1: "promoter", 2: "enhancer", 3: "enhancer", 4: "h3k4me3",
        5: "ctcf", 6: "tf", 7: "accessibility", 8: "tf",
    }
    groups = Counter(group_for_code[code] for _member, code in classified)
    specific = {group for group in groups if group != "accessibility"}
    if not positive and capabilities["any_specific_classification"] == 0:
        status = "no_classifying_evidence_in_context"
    elif not positive:
        status = "not_detected"
    elif not classified:
        status = "replicated_accessibility_only" if len(positive) >= 2 else "single_accessibility_only"
    elif len(specific) > 1:
        status = "mixed_specific_classes"
    elif specific:
        group = next(iter(specific))
        count = groups[group]
        suffix = "_associated" if group == "h3k4me3" else "_like"
        status = ("replicated_" if count >= 2 else "single_") + group + suffix
    else:
        status = "replicated_accessibility_supported" if len(classified) >= 2 else "single_accessibility_supported"
    classification_capable = capabilities["any_specific_classification"]
    if classification_capable == 0:
        classification_evidence_status = "unavailable"
    elif classification_capable == 1:
        classification_evidence_status = "single_donor"
    else:
        classification_evidence_status = "replicated"
    return {
        "status": status,
        "activity_detected": bool(positive),
        "classification_evidence_status": classification_evidence_status,
        "classification_capable_donors": classification_capable,
        "assay_capable_donors": capabilities,
        "donors_total": len(member_codes),
        "donors_detected": len(positive),
        "donors_classified": len(classified),
        "exact_class_counts": dict(sorted(exact.items())),
        "class_group_counts": dict(sorted(groups.items())),
        "discordant_specific_classes": len(specific) > 1,
        "profile_calls": [
            {
                "profile_index": member["profile_index"],
                "donor_accession": member["donor_accession"],
                "evidence_tier": member["evidence_tier"],
                "assays_available": member.get("assays_available", []),
                "audit_warnings": member.get("audit_warnings", []),
                "class": CLASS_LABELS[code],
            }
            for member, code in member_codes
        ],
    }


def lineage_coverage(profiles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Document important IEI lineage presence without inferring missing cells."""
    rows = []
    for label, expected_lineages in IEI_LINEAGE_COVERAGE.items():
        def matches(profile: dict[str, Any]) -> bool:
            if profile["lineage"] in expected_lineages:
                return True
            if label != "Plasma cell":
                return False
            text = " ".join((
                profile.get("screen_name", ""), profile.get("display_name", ""),
                profile.get("ontology_name", ""),
            )).casefold()
            return "plasma cell" in text

        source = [profile for profile in profiles if matches(profile)]
        eligible = [profile for profile in source if profile["baseline_eligible"]]
        exclusion_counts = Counter(
            reason for profile in source if not profile["baseline_eligible"]
            for reason in profile["exclusion_reasons"]
        )
        rows.append({
            "lineage": label,
            "source_profiles": len(source),
            "baseline_eligible_profiles": len(eligible),
            "baseline_distinct_donors": len({
                profile["donor_accession"] for profile in eligible
                if profile["donor_accession"]
            }),
            "source_exclusion_reasons": dict(sorted(exclusion_counts.items())),
            "represented_in_baseline": bool(eligible),
        })
    return rows


def donor_dependence(contexts: list[dict[str, Any]]) -> dict[str, Any]:
    donor_contexts: dict[str, set[str]] = defaultdict(set)
    for context in contexts:
        for member in context["members"]:
            donor_contexts[member["donor_accession"]].add(context["context_id"])
    nested_pairs = []
    by_id = {context["context_id"]: context for context in contexts}
    for child in contexts:
        child_donors = {member["donor_accession"] for member in child["members"]}
        for parent_id in child.get("ancestor_context_ids", []):
            parent = by_id[parent_id]
            parent_donors = {member["donor_accession"] for member in parent["members"]}
            nested_pairs.append({
                "parent_context_id": parent_id,
                "child_context_id": child["context_id"],
                "direct": parent_id in child.get("direct_parent_context_ids", []),
                "shared_donor_count": len(parent_donors & child_donors),
            })
    return {
        "baseline_distinct_donors": len(donor_contexts),
        "donors_in_multiple_contexts": sum(len(ids) > 1 for ids in donor_contexts.values()),
        "maximum_contexts_per_donor": max(map(len, donor_contexts.values()), default=0),
        "nested_context_pairs": len(nested_pairs),
        "nested_pairs_sharing_donors": sum(
            row["shared_donor_count"] > 0 for row in nested_pairs
        ),
        "relations": nested_pairs,
    }


def build_database(
    path: Path,
    profiles: list[dict[str, Any]],
    contexts: list[dict[str, Any]],
    metadata: dict[str, str],
) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.unlink(missing_ok=True)
    connection = sqlite3.connect(temporary)
    connection.executescript("""
        PRAGMA journal_mode=OFF;
        PRAGMA synchronous=OFF;
        CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE profile(
            profile_index INTEGER PRIMARY KEY,
            screen_name TEXT NOT NULL,
            display_name TEXT NOT NULL,
            ontology_id TEXT NOT NULL,
            ontology_name TEXT NOT NULL,
            sample_type TEXT NOT NULL,
            lineage TEXT NOT NULL,
            immune_scope TEXT NOT NULL,
            evidence_tier TEXT NOT NULL,
            donor_accession TEXT NOT NULL,
            assays_available_json TEXT NOT NULL,
            life_stages_json TEXT NOT NULL,
            sexes_json TEXT NOT NULL,
            experiment_accessions_json TEXT NOT NULL,
            baseline_eligible INTEGER NOT NULL,
            exclusion_reasons_json TEXT NOT NULL,
            state_metadata_json TEXT NOT NULL,
            audit_warnings_json TEXT NOT NULL
        );
        CREATE TABLE context(
            context_id TEXT PRIMARY KEY,
            ontology_id TEXT NOT NULL UNIQUE,
            ontology_name TEXT NOT NULL,
            lineages_json TEXT NOT NULL,
            donor_count INTEGER NOT NULL,
            tier_counts_json TEXT NOT NULL,
            life_stages_json TEXT NOT NULL,
            sexes_json TEXT NOT NULL,
            assay_capability_json TEXT NOT NULL,
            audit_warnings_json TEXT NOT NULL,
            hierarchy_json TEXT NOT NULL
        );
        CREATE TABLE context_member(
            context_id TEXT NOT NULL,
            donor_accession TEXT NOT NULL,
            profile_index INTEGER NOT NULL,
            evidence_tier TEXT NOT NULL,
            duplicate_profile_indices_json TEXT NOT NULL,
            PRIMARY KEY(context_id, donor_accession),
            FOREIGN KEY(context_id) REFERENCES context(context_id),
            FOREIGN KEY(profile_index) REFERENCES profile(profile_index)
        );
        CREATE TABLE context_relation(
            parent_context_id TEXT NOT NULL,
            child_context_id TEXT NOT NULL,
            direct INTEGER NOT NULL,
            shared_donor_count INTEGER NOT NULL,
            PRIMARY KEY(parent_context_id, child_context_id),
            FOREIGN KEY(parent_context_id) REFERENCES context(context_id),
            FOREIGN KEY(child_context_id) REFERENCES context(context_id)
        );
        CREATE INDEX profile_ontology ON profile(ontology_id);
        CREATE INDEX profile_baseline ON profile(baseline_eligible);
    """)
    connection.executemany("INSERT INTO metadata VALUES(?,?)", sorted(metadata.items()))
    for profile in profiles:
        state = {
            "treatments": profile["treatments"],
            "has_treatments": profile["has_treatments"],
            "diseases": profile["diseases"],
            "has_disease_terms": profile["has_disease_terms"],
            "health_statuses": profile["health_statuses"],
            "modifications": profile["modifications"],
            "has_modifications": profile["has_modifications"],
            "synchronizations": profile["synchronizations"],
            "has_synchronizations": profile["has_synchronizations"],
        }
        connection.execute(
            "INSERT INTO profile VALUES(" + ",".join("?" for _ in range(18)) + ")",
            (
                profile["profile_index"], profile["screen_name"], profile["display_name"],
                profile["ontology_id"], profile["ontology_name"], profile["sample_type"],
                profile["lineage"], profile["immune_scope"], profile["evidence_tier"],
                profile["donor_accession"],
                json.dumps(profile["assays_available"]), json.dumps(profile["life_stages"]),
                json.dumps(profile["sexes"]), json.dumps(profile["experiment_accessions"]),
                int(profile["baseline_eligible"]), json.dumps(profile["exclusion_reasons"]),
                json.dumps(state), json.dumps(profile.get("audit_warnings", [])),
            ),
        )
    for context in contexts:
        hierarchy = {
            key: context.get(key, []) if key.endswith("_ids") else context.get(key, False)
            for key in (
                "ancestor_context_ids", "direct_parent_context_ids",
                "descendant_context_ids", "direct_child_context_ids",
                "is_nested_context", "is_summary_parent",
                "related_contexts_sharing_donors",
            )
        }
        connection.execute(
            "INSERT INTO context VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                context["context_id"], context["ontology_id"], context["ontology_name"],
                json.dumps(context["lineages"]), context["donor_count"],
                json.dumps(context["tier_counts"]), json.dumps(context["life_stages"]),
                json.dumps(context["sexes"]), json.dumps(context["assay_capable_donors"]),
                json.dumps(context["tolerated_audit_warning_counts"]),
                json.dumps(hierarchy),
            ),
        )
        for member in context["members"]:
            connection.execute(
                "INSERT INTO context_member VALUES(?,?,?,?,?)",
                (
                    context["context_id"], member["donor_accession"],
                    member["profile_index"], member["evidence_tier"],
                    json.dumps(member["duplicate_profile_indices"]),
                ),
            )
    dependence = donor_dependence(contexts)
    connection.executemany(
        "INSERT INTO context_relation VALUES(?,?,?,?)",
        [
            (
                row["parent_context_id"], row["child_context_id"], int(row["direct"]),
                row["shared_donor_count"],
            )
            for row in dependence["relations"]
        ],
    )
    connection.commit()
    connection.execute("PRAGMA optimize")
    connection.close()
    temporary.replace(path)


def write_members_tsv(path: Path, contexts: list[dict[str, Any]]) -> None:
    lines = [
        "context_id\tontology_id\tontology_name\tdonor_accession\tprofile_index\t"
        "evidence_tier\tassays_available\tlife_stages\tsexes\t"
        "tolerated_audit_warnings\tduplicate_profile_indices"
    ]
    for context in contexts:
        for member in context["members"]:
            lines.append("\t".join((
                context["context_id"], context["ontology_id"], context["ontology_name"],
                member["donor_accession"], str(member["profile_index"]),
                member["evidence_tier"], ",".join(member.get("assays_available", [])),
                ",".join(member["life_stages"]),
                ",".join(member["sexes"]),
                ";".join(
                    f'{row["severity"]}: {row["category"]} ({row["experiment_accession"]})'
                    for row in member.get("audit_warnings", [])
                ),
                ",".join(map(str, member["duplicate_profile_indices"])),
            )))
    path.write_text("\n".join(lines) + "\n")


def prepare_curation(args: argparse.Namespace) -> None:
    selection = json.loads(args.selection.read_text())
    prepared = json.loads(args.prepared_manifest.read_text())
    enriched = json.loads(args.experiment_metadata.read_text())
    policy = json.loads(args.policy.read_text())
    ontology = parse_cell_ontology(args.cell_ontology)
    if not enriched.get("complete"):
        raise RuntimeError("ENCODE experiment metadata is incomplete")
    if enriched.get("selection_sha256") != sha256_file(args.selection):
        raise RuntimeError("experiment metadata does not match the selection manifest")
    if enriched.get("policy_sha256") != sha256_file(args.policy):
        raise RuntimeError("experiment metadata does not match the audit policy")
    if prepared["immune_matrix"]["shape"][1] != len(selection["immune_biosamples"]):
        raise RuntimeError("immune matrix columns do not match selection profiles")
    matrix_path = Path(prepared["immune_matrix"]["path"])
    if matrix_path.stat().st_size != prepared["immune_matrix"]["size"]:
        raise RuntimeError("immune matrix size does not match prepared manifest")
    if sha256_file(matrix_path) != prepared["immune_matrix"]["sha256"]:
        raise RuntimeError("immune matrix checksum does not match prepared manifest")

    profiles = [
        profile_curation(profile, enriched["experiments"])
        for profile in selection["immune_biosamples"]
    ]
    if [profile["profile_index"] for profile in profiles] != list(range(len(profiles))):
        raise RuntimeError("immune profile indices are not contiguous matrix columns")
    contexts = choose_contexts(profiles, ontology)
    dependence = donor_dependence(contexts)
    coverage = lineage_coverage(profiles)
    exclusions = Counter(
        reason for profile in profiles for reason in profile["exclusion_reasons"]
    )
    tier_counts = Counter(
        member["evidence_tier"] for context in contexts for member in context["members"]
    )
    retained_warnings = [
        warning for context in contexts for member in context["members"]
        for warning in member.get("audit_warnings", [])
    ]
    source_warnings = [
        warning for profile in profiles for warning in profile.get("audit_warnings", [])
    ]
    observed_classes = {}
    for collection, rows in prepared.get("summaries", {}).items():
        totals = Counter()
        for counts in rows.values():
            totals.update(counts)
        observed_classes[collection] = {
            "call_counts": dict(sorted(totals.items())),
            "observed_labels": sorted(label for label, count in totals.items() if count),
            "supported_but_unobserved_labels": sorted(
                label for label in set(CLASS_LABELS.values())
                if totals[label] == 0
            ),
        }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    database = args.output_dir / "screen.registry-v4.immune-contexts.sqlite3"
    manifest_path = args.output_dir / "screen.registry-v4.immune-contexts.json"
    members_path = args.output_dir / "screen.registry-v4.immune-context-members.tsv"
    for path in (database, manifest_path, members_path):
        if path.exists() and not args.force:
            raise RuntimeError(f"output exists; use --force to replace: {path}")
    metadata = {
        "registry": selection["registry"],
        "assembly": selection["assembly"],
        "curation_policy": policy["policy_id"],
        "selection_sha256": sha256_file(args.selection),
        "prepared_manifest_sha256": sha256_file(args.prepared_manifest),
        "experiment_metadata_sha256": sha256_file(args.experiment_metadata),
        "audit_policy_sha256": sha256_file(args.policy),
        "cell_ontology_sha256": sha256_file(args.cell_ontology),
        "cell_ontology_release": CELL_ONTOLOGY_RELEASE,
        "cell_ontology_source_url": CELL_ONTOLOGY_SOURCE_URL,
        "cell_ontology_data_version": ontology["header"].get("data-version", ""),
        "immune_matrix_sha256": prepared["immune_matrix"]["sha256"],
        "created_at": utc_now(),
    }
    build_database(database, profiles, contexts, metadata)
    write_members_tsv(members_path, contexts)
    output = {
        "schema_version": 2,
        **metadata,
        "source_paths": {
            "selection": str(args.selection.resolve()),
            "prepared_manifest": str(args.prepared_manifest.resolve()),
            "experiment_metadata": str(args.experiment_metadata.resolve()),
            "audit_policy": str(args.policy.resolve()),
            "cell_ontology": str(args.cell_ontology.resolve()),
            "immune_matrix": str(matrix_path.resolve()),
        },
        "artifacts": {
            "database": {"path": str(database.resolve()), "sha256": sha256_file(database), "size": database.stat().st_size},
            "members_tsv": {"path": str(members_path.resolve()), "sha256": sha256_file(members_path), "size": members_path.stat().st_size},
        },
        "counts": {
            "source_profiles": len(profiles),
            "baseline_eligible_profiles_before_donor_deduplication": sum(row["baseline_eligible"] for row in profiles),
            "baseline_contexts": len(contexts),
            "baseline_donor_representatives": sum(row["donor_count"] for row in contexts),
            **{key: value for key, value in dependence.items() if key != "relations"},
            "representatives_by_tier": dict(sorted(tier_counts.items())),
            "contexts_without_specific_classification_assays": sum(
                context["assay_capable_donors"]["any_specific_classification"] == 0
                for context in contexts
            ),
            "contexts_with_replicated_specific_classification_assays": sum(
                context["assay_capable_donors"]["any_specific_classification"] >= 2
                for context in contexts
            ),
            "source_profiles_with_tolerated_audit_warnings": sum(
                bool(profile.get("audit_warnings")) for profile in profiles
            ),
            "baseline_profiles_with_tolerated_audit_warnings": sum(
                bool(member.get("audit_warnings"))
                for context in contexts for member in context["members"]
            ),
            "exclusion_reasons": dict(sorted(exclusions.items())),
        },
        "tolerated_audit_warnings": {
            "policy": "surfaced as quality flags; not used as additional exclusions",
            "source_counts": dict(sorted(Counter(
                f'{warning["severity"]}: {warning["category"]}'
                for warning in source_warnings
            ).items())),
            "baseline_counts": dict(sorted(Counter(
                f'{warning["severity"]}: {warning["category"]}'
                for warning in retained_warnings
            ).items())),
        },
        "context_dependence": {
            "note": (
                "Context counts are not independent: Cell Ontology terms are nested "
                "and the same donor can occur in more than one context."
            ),
            "relations": dependence["relations"],
        },
        "iei_lineage_coverage": coverage,
        "source_class_observations": observed_classes,
        "contexts": [{key: value for key, value in context.items() if key != "members"} for context in contexts],
        "interpretation": [
            "The complete 448-profile matrix is retained; this manifest defines a conservative default view.",
            "Contexts use exact Cell Ontology CURIEs and one deterministic representative per donor.",
            "Categorical SCREEN classes are counted, not numerically averaged.",
            "Accessibility-only profiles support activity but cannot establish promoter/enhancer class.",
            "A not-detected call means not detected in the represented SCREEN profiles, not universally inactive.",
            "No activated or stimulated companion view is built; treated profiles remain in the immutable source layer only.",
            "Tolerated ENCODE warnings are surfaced per profile and context rather than silently converted into exclusions.",
        ],
    }
    atomic_json(manifest_path, output)
    print(json.dumps(output["counts"], indent=2, sort_keys=True))


def load_contexts(database: Path) -> list[dict[str, Any]]:
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    contexts = []
    for row in connection.execute("SELECT * FROM context ORDER BY ontology_name, context_id"):
        members = []
        for member in connection.execute(
            """SELECT cm.profile_index, cm.donor_accession, cm.evidence_tier,
                      cm.duplicate_profile_indices_json, p.assays_available_json,
                      p.audit_warnings_json
               FROM context_member cm JOIN profile p USING(profile_index)
               WHERE cm.context_id=? ORDER BY cm.profile_index""",
            (row["context_id"],),
        ):
            members.append({
                "profile_index": member["profile_index"],
                "donor_accession": member["donor_accession"],
                "evidence_tier": member["evidence_tier"],
                "assays_available": json.loads(member["assays_available_json"]),
                "audit_warnings": json.loads(member["audit_warnings_json"]),
                "duplicate_profile_indices": json.loads(member["duplicate_profile_indices_json"]),
            })
        hierarchy = json.loads(row["hierarchy_json"])
        contexts.append({
            "context_id": row["context_id"],
            "ontology_id": row["ontology_id"],
            "ontology_name": row["ontology_name"],
            "assay_capable_donors": json.loads(row["assay_capability_json"]),
            "tolerated_audit_warning_counts": json.loads(row["audit_warnings_json"]),
            **hierarchy,
            "members": members,
        })
    connection.close()
    return contexts


def summarize_ccre(args: argparse.Namespace) -> None:
    manifest = json.loads(args.context_manifest.read_text())
    prepared = json.loads(Path(manifest["source_paths"]["prepared_manifest"]).read_text())
    catalog = Path(prepared["catalog"]["path"])
    connection = sqlite3.connect(catalog)
    connection.row_factory = sqlite3.Row
    if args.row_index is not None:
        row = connection.execute("SELECT * FROM ccre WHERE row_index=?", (args.row_index,)).fetchone()
    else:
        row = connection.execute("SELECT * FROM ccre WHERE ccre_accession=?", (args.ccre,)).fetchone()
    connection.close()
    if row is None:
        raise RuntimeError("cCRE was not found in the Registry V4 catalog")
    columns = prepared["immune_matrix"]["shape"][1]
    with Path(prepared["immune_matrix"]["path"]).open("rb") as handle:
        handle.seek(row["row_index"] * columns)
        codes = handle.read(columns)
    if len(codes) != columns:
        raise RuntimeError("could not read complete cCRE row from immune matrix")
    contexts = load_contexts(Path(manifest["artifacts"]["database"]["path"]))
    summaries = []
    detected_donors: dict[str, set[str]] = {}
    for context in contexts:
        summary = summarize_context_codes(codes, context)
        detected_donors[context["context_id"]] = {
            call["donor_accession"] for call in summary["profile_calls"]
            if call["class"] != "inactive"
        }
        if args.include_inactive or summary["donors_detected"]:
            summaries.append({
                "context_id": context["context_id"],
                "ontology_id": context["ontology_id"],
                "ontology_name": context["ontology_name"],
                "ancestor_context_ids": context.get("ancestor_context_ids", []),
                "direct_parent_context_ids": context.get("direct_parent_context_ids", []),
                "is_nested_context": context.get("is_nested_context", False),
                "is_summary_parent": context.get("is_summary_parent", False),
                "tolerated_audit_warning_counts": context.get(
                    "tolerated_audit_warning_counts", {}
                ),
                **summary,
            })
    shown_ids = {summary["context_id"] for summary in summaries}
    nested_detection = []
    for relation in manifest.get("context_dependence", {}).get("relations", []):
        parent = relation["parent_context_id"]
        child = relation["child_context_id"]
        if parent not in shown_ids or child not in shown_ids:
            continue
        shared = detected_donors[parent] & detected_donors[child]
        nested_detection.append({
            **relation,
            "shared_detected_donor_count": len(shared),
        })
    distinct_detected = set().union(*(
        detected_donors[context_id] for context_id in shown_ids
    )) if shown_ids else set()
    print(json.dumps({
        "ccre": dict(row),
        "baseline_contexts": summaries,
        "cross_context_summary": {
            "contexts_reported": len(summaries),
            "context_donor_observations": sum(
                summary["donors_detected"] for summary in summaries
            ),
            "distinct_detected_donors": len(distinct_detected),
            "nested_context_pairs_reported": nested_detection,
            "interpretation": (
                "Context observations are not independent: ontology terms are nested "
                "and donors may occur in more than one context."
            ),
        },
    }, indent=2, sort_keys=True))


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    enrich = commands.add_parser("enrich", help="cache full ENCODE experiment metadata")
    enrich.add_argument("--selection", type=Path, required=True)
    enrich.add_argument("--policy", type=Path, required=True)
    enrich.add_argument("--output", type=Path, required=True)
    enrich.add_argument("--workers", type=int, default=8)
    enrich.set_defaults(function=enrich_metadata)
    prepare = commands.add_parser("prepare", help="build baseline immune contexts")
    prepare.add_argument("--selection", type=Path, required=True)
    prepare.add_argument("--prepared-manifest", type=Path, required=True)
    prepare.add_argument("--experiment-metadata", type=Path, required=True)
    prepare.add_argument("--policy", type=Path, required=True)
    prepare.add_argument("--cell-ontology", type=Path, required=True)
    prepare.add_argument("--output-dir", type=Path, required=True)
    prepare.add_argument("--force", action="store_true")
    prepare.set_defaults(function=prepare_curation)
    summarize = commands.add_parser("summarize-ccre", help="show donor-aware context calls")
    summarize.add_argument("--context-manifest", type=Path, required=True)
    target = summarize.add_mutually_exclusive_group(required=True)
    target.add_argument("--ccre")
    target.add_argument("--row-index", type=int)
    summarize.add_argument("--include-inactive", action="store_true")
    summarize.set_defaults(function=summarize_ccre)
    return result


def main() -> None:
    args = parser().parse_args()
    args.function(args)


if __name__ == "__main__":
    main()
