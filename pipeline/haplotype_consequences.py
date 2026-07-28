#!/usr/bin/env python3
"""Add sample-specific frame-restoration evidence from Haplosaurus.

Haplosaurus reports transcript/protein haplotypes, but it may combine unphased
heterozygous variants.  This postprocessor therefore re-checks GT and phase in
the candidate VCF before assigning one of two deliberately distinct statuses:

* FRAME_RESTORED_CONFIRMED
* FRAME_RESTORATION_PARTIAL_CONFIRMED
* FRAME_RESTORING_POSSIBLE_UNPHASED

Only multi-variant protein haplotypes containing an indel and lacking
Haplosaurus frameshift/stop-change flags are considered restoring.
The original per-variant VEP/LOFTEE annotations are retained unchanged.
"""

from __future__ import annotations

import argparse
import gzip
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote


INFO_KEY = "IEI_HAPLOTYPE_FRAME"
CONFIRMED = "FRAME_RESTORED_CONFIRMED"
PARTIAL = "FRAME_RESTORATION_PARTIAL_CONFIRMED"
POSSIBLE = "FRAME_RESTORING_POSSIBLE_UNPHASED"


def open_text(path: Path, mode: str = "rt"):
    return gzip.open(path, mode) if path.name.endswith(".gz") else path.open(mode)


def variant_id(chrom: str, pos: str, ref: str, alt: str) -> str:
    return f"{chrom}:{pos}:{ref}:{alt}"


def parse_candidate_genotypes(
    path: Path,
) -> tuple[dict[str, dict[str, dict[str, object]]], list[str]]:
    """Return variant -> sample -> phase-aware genotype state."""
    result: dict[str, dict[str, dict[str, object]]] = defaultdict(dict)
    samples: list[str] = []
    with open_text(path) as handle:
        for raw in handle:
            line = raw.rstrip("\n")
            if line.startswith("#CHROM"):
                samples = line.split("\t")[9:]
                continue
            if not line or line.startswith("#"):
                continue
            columns = line.split("\t")
            if len(columns) < 10:
                continue
            chrom, pos, record_id, ref, alt = (
                columns[0],
                columns[1],
                columns[2],
                columns[3],
                columns[4],
            )
            key = record_id if record_id not in {"", "."} else variant_id(
                chrom, pos, ref, alt
            )
            format_fields = columns[8].split(":")
            for sample, sample_value in zip(samples, columns[9:]):
                values = sample_value.split(":")
                fields = {
                    field: values[index] if index < len(values) else ""
                    for index, field in enumerate(format_fields)
                }
                gt = fields.get("GT", "./.")
                alleles = gt.replace("|", "/").split("/")
                if not alleles or any(not value.isdigit() for value in alleles):
                    continue
                alt_copies = sum(value != "0" for value in alleles)
                if alt_copies == 0:
                    continue
                homozygous_alt = len(alleles) == 2 and alt_copies == 2
                phased = "|" in gt
                haplotypes: set[int] | None = None
                if homozygous_alt:
                    haplotypes = {0, 1}
                elif phased and len(alleles) == 2:
                    haplotypes = {
                        index for index, allele in enumerate(gt.split("|"))
                        if allele != "0"
                    }
                result[key][sample] = {
                    "gt": gt,
                    "homozygous_alt": homozygous_alt,
                    "phased": phased,
                    "haplotypes": haplotypes,
                    "phase_set": fields.get("PS") or fields.get("PID") or "",
                }
    return result, samples


def classify_phase(states: list[dict[str, object]]) -> str | None:
    """Classify whether all variants can be placed on a common haplotype."""
    if not states:
        return None
    unknown = [state for state in states if state["haplotypes"] is None]
    known_non_homozygous = [
        state for state in states
        if state["haplotypes"] is not None and not state["homozygous_alt"]
    ]

    if unknown:
        # A single unphased heterozygous variant is necessarily in cis with a
        # homozygous-alt partner because the latter is present on both copies.
        if len(unknown) == 1 and all(
            bool(state["homozygous_alt"]) for state in states if state is not unknown[0]
        ):
            return PARTIAL
        return POSSIBLE

    common = {0, 1}
    for state in states:
        common &= set(state["haplotypes"] or set())
    if not common:
        return None  # confirmed trans

    phase_sets = [str(state["phase_set"]) for state in known_non_homozygous]
    populated = {value for value in phase_sets if value}
    if len(populated) > 1 or (populated and any(not value for value in phase_sets)):
        return POSSIBLE
    if any(bool(state["homozygous_alt"]) for state in states) and any(
        not bool(state["homozygous_alt"]) for state in states
    ):
        return PARTIAL
    return CONFIRMED


def restoring_events(
    haplosaurus_json: Path,
    genotypes: dict[str, dict[str, dict[str, object]]],
) -> tuple[dict[str, list[dict[str, str]]], Counter]:
    events: dict[str, list[dict[str, str]]] = defaultdict(list)
    counters: Counter = Counter()
    seen: set[tuple[str, str, str, str]] = set()

    with open_text(haplosaurus_json) as handle:
        for raw in handle:
            if not raw.strip():
                continue
            transcript = json.loads(raw)
            transcript_id = str(transcript.get("transcript_id") or "")
            for haplotype in transcript.get("protein_haplotypes") or []:
                variants = [
                    str(value) for value in haplotype.get("contributing_variants") or []
                    if value not in {None, "", "."}
                ]
                if len(set(variants)) < 2 or not haplotype.get("has_indel"):
                    continue
                flags = {
                    str(value).lower() for value in haplotype.get("flags") or []
                }
                if (
                    "frameshift" in flags
                    or "stop_change" in flags
                    or "stop_changed" in flags
                ):
                    counters["non_restoring_haplotypes"] += 1
                    continue
                counters["candidate_restoring_haplotypes"] += 1
                for sample in (haplotype.get("samples") or {}):
                    states = [
                        genotypes.get(key, {}).get(str(sample)) for key in variants
                    ]
                    if any(state is None for state in states):
                        counters["missing_genotype_haplotypes"] += 1
                        continue
                    status = classify_phase([state for state in states if state])
                    if status is None:
                        counters["confirmed_trans_haplotypes"] += 1
                        continue
                    counters[status] += 1
                    protein = str(haplotype.get("name") or "")
                    for key in variants:
                        event_key = (key, str(sample), transcript_id, status)
                        if event_key in seen:
                            continue
                        seen.add(event_key)
                        events[key].append({
                            "sample": str(sample),
                            "transcript": transcript_id,
                            "status": status,
                            "partners": "&".join(value for value in variants if value != key),
                            "protein": protein,
                        })
    return events, counters


def encode(value: str) -> str:
    return quote(value, safe="._:-&")


def annotate_vcf(input_path: Path, output_path: Path, events: dict[str, list[dict[str, str]]]):
    info_header = (
        f'##INFO=<ID={INFO_KEY},Number=.,Type=String,Description="Sample-specific '
        "Haplosaurus frame-restoration evidence. Pipe-delimited fields: "
        "variant_id|sample|transcript|status|partner_variant_ids|protein_haplotype; "
        f"status is {CONFIRMED}, {PARTIAL}, or {POSSIBLE}. Values are percent encoded.\">\n"
    )
    provenance = (
        "##iei_haplotype_postprocessing=<Tool=Haplosaurus,"
        "PhaseValidation=sample_GT_PS,FrameRestoration=multi_variant_protein_haplotype>\n"
    )
    with open_text(input_path) as source, open_text(output_path, "wt") as target:
        for raw in source:
            if raw.startswith("#CHROM"):
                target.write(info_header)
                target.write(provenance)
                target.write(raw)
                continue
            if not raw or raw.startswith("#"):
                target.write(raw)
                continue
            columns = raw.rstrip("\n").split("\t")
            if len(columns) < 8:
                target.write(raw)
                continue
            per_record = []
            for alt in columns[4].split(","):
                key = variant_id(columns[0], columns[1], columns[3], alt)
                for event in events.get(key, []):
                    per_record.append("|".join([
                        encode(key),
                        encode(event["sample"]),
                        encode(event["transcript"]),
                        event["status"],
                        encode(event["partners"]),
                        encode(event["protein"]),
                    ]))
            if per_record:
                value = ",".join(sorted(set(per_record)))
                columns[7] = (
                    f"{columns[7]};{INFO_KEY}={value}"
                    if columns[7] not in {"", "."}
                    else f"{INFO_KEY}={value}"
                )
            target.write("\t".join(columns) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--candidate-vcf", required=True, type=Path)
    parser.add_argument("--haplosaurus-json", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--audit-json", type=Path)
    args = parser.parse_args()

    genotypes, samples = parse_candidate_genotypes(args.candidate_vcf)
    events, counters = restoring_events(args.haplosaurus_json, genotypes)
    annotate_vcf(args.input, args.output, events)

    if args.audit_json:
        payload = {
            "schema_version": 1,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "input": str(args.input),
            "candidate_vcf": str(args.candidate_vcf),
            "samples": samples,
            "counts": {
                **dict(sorted(counters.items())),
                "annotated_variants": len(events),
                "annotated_entries": sum(len(value) for value in events.values()),
            },
            "events": dict(sorted(events.items())),
        }
        args.audit_json.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
