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
import sys
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


# GRCh38 pseudoautosomal region bounds (inside the PARs X and Y are diploid).
GRCH38_X_PAR1_END = 2_781_479
GRCH38_X_PAR2_START = 155_701_383
GRCH38_Y_PAR1_END = 2_781_479
GRCH38_Y_PAR2_START = 56_887_903


def haploid_single_copy_locus(chrom: str, pos: str) -> bool:
    """True for a non-PAR X or Y locus, where a haploid call sits on the
    sample's only copy of the contig (GRCh38 coordinates — the candidate VCF
    this module reads is always GRCh38)."""
    normalized = chrom[3:] if chrom.lower().startswith("chr") else chrom
    normalized = normalized.upper()
    try:
        position = int(pos)
    except ValueError:
        return False
    if normalized == "X":
        return GRCH38_X_PAR1_END < position < GRCH38_X_PAR2_START
    if normalized == "Y":
        return GRCH38_Y_PAR1_END < position < GRCH38_Y_PAR2_START
    return False


def minimal_variant_id(chrom: str, pos: str, ref: str, alt: str) -> str:
    """Reproduce the minimal representation used for the candidate VCF's IDs.

    Candidate IDs are set after ``bcftools norm -m -any`` from the split,
    minimal alleles; the VCF handed to annotate_vcf is not normalised. Trimming
    the shared allele suffix, then the shared prefix (advancing POS), maps a
    non-minimal record onto the same key. Left-shifting through repeat runs
    still requires the reference and is reported as a miss instead.
    """
    try:
        position = int(pos)
    except ValueError:
        return variant_id(chrom, pos, ref, alt)
    reference, alternate = ref, alt
    while (
        len(reference) > 1 and len(alternate) > 1
        and reference[-1] == alternate[-1]
    ):
        reference = reference[:-1]
        alternate = alternate[:-1]
    while (
        len(reference) > 1 and len(alternate) > 1
        and reference[0] == alternate[0]
    ):
        reference = reference[1:]
        alternate = alternate[1:]
        position += 1
    return variant_id(chrom, str(position), reference, alternate)


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
            if record_id not in {"", "."}:
                keys = [record_id]
            else:
                # No ID: register per-ALT keys (raw and minimal) so a
                # multi-allelic record is reachable from per-allele lookups
                # instead of an unmatchable comma-joined key.
                keys = []
                for alt_allele in alt.split(","):
                    raw_key = variant_id(chrom, pos, ref, alt_allele)
                    keys.append(raw_key)
                    trimmed = minimal_variant_id(chrom, pos, ref, alt_allele)
                    if trimmed != raw_key:
                        keys.append(trimmed)
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
                non_ref_indexes = {value for value in alleles if value != "0"}
                homozygous_alt = (
                    len(alleles) == 2
                    and alt_copies == 2
                    and len(non_ref_indexes) == 1
                )
                phased = "|" in gt
                haplotypes: set[int] | None = None
                if len(non_ref_indexes) > 1:
                    # Two different ALT indexes (e.g. 1/2 or 1|2): the record's
                    # key covers only one of them and the GT alone cannot say
                    # which haplotype carries it, so its placement is unknown.
                    haplotypes = None
                elif homozygous_alt:
                    haplotypes = {0, 1}
                elif phased and len(alleles) == 2:
                    haplotypes = {
                        index for index, allele in enumerate(gt.split("|"))
                        if allele != "0"
                    }
                if (
                    len(alleles) == 1
                    and alt_copies == 1
                    and haploid_single_copy_locus(chrom, pos)
                ):
                    # Decision D4 (cis-by-construction): a haploid call on
                    # non-PAR X/Y occupies the sample's ONLY copy of the
                    # contig, so it is necessarily in cis with anything else
                    # the sample carries there — matching how the same call
                    # written diploid-style (1/1) already classifies. PAR
                    # loci and haploid calls on other contigs keep the
                    # conservative unknown placement.
                    homozygous_alt = True  # occupies every copy (there is one)
                    haplotypes = {0, 1}
                sample_state = {
                    "gt": gt,
                    "homozygous_alt": homozygous_alt,
                    "phased": phased,
                    "haplotypes": haplotypes,
                    "phase_set": fields.get("PS") or fields.get("PID") or "",
                }
                for key in keys:
                    result[key][sample] = sample_state
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

    if known_non_homozygous:
        phase_sets = [str(state["phase_set"]) for state in known_non_homozygous]
        populated = {value for value in phase_sets if value}
        # Cross-record phase is only trustworthy when every phased heterozygous
        # call carries the same, non-empty phase set. A bare "|" separator with
        # no PS/PID says nothing about phase between records.
        if len(populated) != 1 or any(not value for value in phase_sets):
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
            try:
                transcript = json.loads(raw)
            except ValueError:
                # A truncated container line means haplo crashed mid-write
                # (seen when its buffered output is cut by a segfault).
                # Losing one transcript's haplotype refinement must degrade
                # loudly, not kill the whole annotation deliverable: affected
                # variants simply keep their per-variant LOFTEE consequences.
                counters["unparseable_container_lines"] += 1
                print(
                    "WARN haplotype container line could not be parsed "
                    "(truncated haplo output?); its transcript's haplotype "
                    "evidence is unavailable and per-variant consequences "
                    "stand",
                    file=sys.stderr,
                )
                continue
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


def annotate_vcf(
    input_path: Path, output_path: Path, events: dict[str, list[dict[str, str]]]
) -> set[str]:
    """Write the annotated VCF; return the event keys that matched a record."""
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
    matched: set[str] = set()
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
                raw_key = variant_id(columns[0], columns[1], columns[3], alt)
                candidate_keys = [raw_key]
                trimmed = minimal_variant_id(columns[0], columns[1], columns[3], alt)
                if trimmed != raw_key:
                    candidate_keys.append(trimmed)
                for key in candidate_keys:
                    record_events = events.get(key)
                    if not record_events:
                        continue
                    matched.add(key)
                    for event in record_events:
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
    return matched


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
    matched = annotate_vcf(args.input, args.output, events)
    unmatched = sorted(set(events) - matched)
    if unmatched:
        preview = ", ".join(unmatched[:5])
        print(
            f"WARN  haplotype annotation: {len(unmatched)} event variant key(s) "
            "matched no record in --input (pre/post-normalisation key "
            f"mismatch?): {preview}",
            file=sys.stderr,
        )
    counters["unmatched_event_variants"] = len(unmatched)

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
