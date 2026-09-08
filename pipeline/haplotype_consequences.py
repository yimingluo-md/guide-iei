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

try:  # package import (tests) or script execution (run_annotation.sh)
    from .loftee_ptc_50bp import allele_index, info_map, parse_csq_fields
except ImportError:  # pragma: no cover - exercised when run as a script
    from loftee_ptc_50bp import allele_index, info_map, parse_csq_fields


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
                # A truncated genotype row silently erased that variant's
                # phase state — and with stale-evidence stripping, a corrupt
                # source could then REMOVE previously confirmed
                # frame-restoration evidence. Corrupt input fails loudly.
                position = ":".join(columns[:2]) if len(columns) >= 2 else "?"
                raise ValueError(
                    f"genotype source record {position} is truncated "
                    f"({len(columns)} column(s)); re-export the VCF"
                )
            chrom, pos, record_id, ref, alt = (
                columns[0],
                columns[1],
                columns[2],
                columns[3],
                columns[4],
            )
            alt_alleles = alt.split(",")
            if record_id not in {"", "."}:
                # Candidate records carry one ALT after ``norm -m -any``; the
                # ID names that allele, so its length change is well defined.
                keys = [record_id]
                key_length_change = {
                    record_id: allele_length_change(ref, alt_alleles[0])
                    if len(alt_alleles) == 1 else None
                }
            else:
                # No ID: register per-ALT keys (raw and minimal) so a
                # multi-allelic record is reachable from per-allele lookups
                # instead of an unmatchable comma-joined key.
                keys = []
                key_length_change = {}
                for alt_allele in alt_alleles:
                    raw_key = variant_id(chrom, pos, ref, alt_allele)
                    keys.append(raw_key)
                    key_length_change[raw_key] = allele_length_change(ref, alt_allele)
                    trimmed = minimal_variant_id(chrom, pos, ref, alt_allele)
                    if trimmed != raw_key:
                        keys.append(trimmed)
                        key_length_change[trimmed] = key_length_change[raw_key]
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
                    "phase_set": next((fields[key] for key in ("PS", "PID")
                                       if fields.get(key) not in (None, "", ".")), ""),
                }
                for key in keys:
                    result[key][sample] = {
                        **sample_state,
                        "length_change": key_length_change.get(key),
                    }
    return result, samples


def allele_length_change(ref: str, alt: str) -> int | None:
    """``len(ALT) - len(REF)`` for a sequence allele; None for symbolic ones."""
    if not ref or not alt or alt.startswith("<") or "*" in alt or "[" in alt or "]" in alt:
        return None
    return len(alt) - len(ref)


def transcript_base_id(transcript: str) -> str:
    return str(transcript or "").split(".", 1)[0]


def parse_frameshift_transcripts(path: Path) -> dict[str, set[str]] | None:
    """Return variant key -> transcripts on which THAT allele is a frameshift.

    Reads the VEP ``CSQ`` annotation of the annotated VCF (the ``--input`` of
    this tool, which still carries INFO; the candidate VCF has it stripped).
    Entries are attributed to their ALT via ``ALLELE_NUM`` (falling back to
    the ``Allele`` field), never broadcast across a multi-allelic record, so
    an in-frame or substitution sibling of a frameshift allele is not marked
    frameshifting by association. Keys are registered raw and in minimal
    representation, matching the candidate IDs. Returns None when the VCF
    carries no CSQ header (nothing can be verified).
    """
    fields: list[str] | None = None
    index: dict[str, set[str]] = defaultdict(set)
    with open_text(path) as handle:
        for raw in handle:
            if raw.startswith("##INFO=<ID=CSQ,") or raw.startswith("##INFO=<ID=CSQ "):
                fields = parse_csq_fields(raw)
                continue
            if raw.startswith("#"):
                continue
            if fields is None:
                return None
            columns = raw.rstrip("\n").split("\t")
            if len(columns) < 8:
                continue
            chrom, pos, ref = columns[0], columns[1], columns[3]
            alts = columns[4].split(",")
            info = info_map(columns[7])
            csq = info.get("CSQ", "")
            if not csq:
                continue
            for entry_text in csq.split(","):
                values = entry_text.split("|")
                values += [""] * (len(fields) - len(values))
                entry = dict(zip(fields, values))
                if "frameshift_variant" not in entry.get("Consequence", "").split("&"):
                    continue
                transcript = transcript_base_id(entry.get("Feature", ""))
                if not transcript:
                    continue
                position = allele_index(entry, ref, alts)
                if position is None:
                    continue  # unattributable entry: no allele is credited
                alt_allele = alts[position]
                raw_key = variant_id(chrom, pos, ref, alt_allele)
                index[raw_key].add(transcript)
                index[minimal_variant_id(chrom, pos, ref, alt_allele)].add(transcript)
    if fields is None:
        return None
    return dict(index)


def minimal_key(key: str) -> str:
    """Minimal representation of a ``chrom:pos:ref:alt`` key (else unchanged)."""
    parts = key.rsplit(":", 3)
    if len(parts) != 4:
        return key
    return minimal_variant_id(*parts)


def classify_phase(states: list[dict[str, object]]) -> str | None:
    """Classify whether all variants can be placed on a common haplotype."""
    if not states:
        return None
    non_homozygous = [state for state in states if not state["homozygous_alt"]]
    if not non_homozygous:
        return CONFIRMED  # every variant occupies both copies

    if len(non_homozygous) == 1:
        # A single non-homozygous variant is necessarily in cis with its
        # homozygous-alt partners, because those partners are present on both
        # copies. This holds whatever the phase state of that variant —
        # unphased, phased without a phase set, or phased inside a block the
        # partners do not belong to — so it is decided before any phase-set
        # requirement (review M2: a phased het without PS previously fell
        # through to the phase-set gate and was downgraded below the unphased
        # verdict for the same genotypes).
        return PARTIAL

    if any(state["haplotypes"] is None for state in non_homozygous):
        return POSSIBLE

    phase_sets = [
        str(state["phase_set"]) if state.get("phase_set") not in (None, "", ".") else ""
        for state in non_homozygous
    ]
    populated = {value for value in phase_sets if value}
    # Cross-record phase between two or more heterozygous calls is only
    # accepted when every one of them carries the same, non-empty PS/PID.
    # VCF 4.2 §1.6.2 does declare that phased genotypes without PS belong to
    # one implicit, contig-wide phase set — that is how statistical phasers
    # (Beagle, Eagle, SHAPEIT) write their output — but an implicit block is
    # not verified phase: statistically phased haplotypes carry switch errors
    # between markers, and nothing in the record says which caller produced
    # the "|". This module therefore treats the implicit set as unverified and
    # reports POSSIBLE rather than confirming cis or trans from it.
    if len(populated) != 1 or any(not value for value in phase_sets):
        return POSSIBLE
    # Haplotype indexes are comparable only within the verified phase set.
    # Opposite indexes in different blocks cannot exclude a restoring event.
    common = {0, 1}
    for state in non_homozygous:
        common &= set(state["haplotypes"] or set())
    if not common:
        return None  # confirmed trans
    if len(non_homozygous) < len(states):
        return PARTIAL
    return CONFIRMED


def frameshifting_contributors(
    variants: list[str],
    transcript_id: str,
    frameshift_index: dict[str, set[str]],
) -> list[str]:
    """Contributors whose own allele is a frameshift on this transcript."""
    transcript = transcript_base_id(transcript_id)
    selected = []
    for key in dict.fromkeys(variants):
        transcripts = frameshift_index.get(key) or frameshift_index.get(minimal_key(key)) or ()
        if transcript in transcripts:
            selected.append(key)
    return selected


def restoring_events(
    haplosaurus_json: Path,
    genotypes: dict[str, dict[str, dict[str, object]]],
    frameshift_index: dict[str, set[str]] | None = None,
) -> tuple[dict[str, list[dict[str, str]]], Counter]:
    """Frame-restoration events per variant key, plus audit counters.

    ``frameshift_index`` (from :func:`parse_frameshift_transcripts`) enables
    the per-contributor check: a haplotype is only a restoration candidate
    when at least two of its contributing alleles are themselves
    ``frameshift_variant`` on the haplotype's transcript. Candidate selection
    upstream is record-wide (``bcftools view -i 'INFO/CSQ~"frameshift_variant"'``
    keeps whole records, and ``norm -m -any`` then admits every sibling ALT),
    so without this check an in-frame deletion plus a substitution that share a
    record with a frameshift allele could be reported FRAME_RESTORED_CONFIRMED
    (review M3). ``None`` skips the check — for unit tests of the phase logic
    only; ``main`` always supplies an index.
    """
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
                frameshifting: list[str] | None = None
                if frameshift_index is not None:
                    frameshifting = frameshifting_contributors(
                        variants, transcript_id, frameshift_index
                    )
                    if len(frameshifting) < 2:
                        # One frameshift cannot be restored by an in-frame or
                        # substitution partner; a sibling ALT admitted by the
                        # record-wide selection is not evidence.
                        counters["insufficient_frameshift_contributors"] += 1
                        continue
                counters["candidate_restoring_haplotypes"] += 1
                for sample in (haplotype.get("samples") or {}):
                    states = [
                        genotypes.get(key, {}).get(str(sample)) for key in variants
                    ]
                    if any(state is None for state in states):
                        counters["missing_genotype_haplotypes"] += 1
                        continue
                    if frameshifting is not None:
                        # Combined-frame arithmetic over the frameshifting
                        # contributors, as an independent check on the
                        # Haplosaurus flags. Only decisive when every such
                        # allele has a well-defined, frame-breaking length
                        # change (an indel straddling an exon boundary does
                        # not, and Haplosaurus then remains the arbiter).
                        changes = [
                            state["length_change"]
                            for key, state in zip(variants, states)
                            if key in frameshifting and state is not None
                        ]
                        if all(
                            isinstance(change, int) and change % 3 != 0
                            for change in changes
                        ):
                            if sum(changes) % 3 != 0:
                                counters["frame_arithmetic_mismatch_haplotypes"] += 1
                                continue
                        else:
                            counters["frame_arithmetic_unverifiable_haplotypes"] += 1
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
        "PhaseValidation=sample_GT_PS,FrameRestoration=multi_variant_protein_haplotype,"
        "ContributorCheck=per_allele_transcript_frameshift>\n"
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
                # Idempotency: a second pass replaces this tool's own header
                # and provenance lines instead of duplicating them.
                if raw.startswith(f"##INFO=<ID={INFO_KEY},") or \
                        raw.startswith("##iei_haplotype_postprocessing=<"):
                    continue
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
            # Strip a previous pass's value UNCONDITIONALLY: a record whose
            # frame-restoration events disappeared on recomputation must not
            # keep its old confirmed evidence.
            retained = [
                item for item in columns[7].split(";")
                if item not in ("", ".")
                and not item.startswith(f"{INFO_KEY}=")
            ]
            if per_record:
                value = ",".join(sorted(set(per_record)))
                retained.append(f"{INFO_KEY}={value}")
            columns[7] = ";".join(retained) if retained else "."
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
    frameshift_index = parse_frameshift_transcripts(args.input)
    if frameshift_index is None:
        # No CSQ means no per-allele consequence to verify contributors
        # against; nothing is confirmed from Haplosaurus' record-wide
        # selection alone.
        print(
            "WARN  haplotype annotation: --input carries no VEP CSQ header; "
            "frame-restoration contributors cannot be verified and no "
            "restoration evidence is written",
            file=sys.stderr,
        )
        frameshift_index = {}
    events, counters = restoring_events(
        args.haplosaurus_json, genotypes, frameshift_index
    )
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
            "contributor_check": "per_allele_transcript_frameshift",
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
