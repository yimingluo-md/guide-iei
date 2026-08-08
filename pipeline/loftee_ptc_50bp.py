#!/usr/bin/env python3
"""Recompute LOFTEE's frameshift 50-bp rule at the resulting PTC.

LOFTEE's ``LoF_info/50_BP_RULE`` is evaluated at the variant position.  A
frameshift's premature termination codon (PTC), however, lies downstream in the
new reading frame.  This postprocessor simulates the frameshift on the
release-matched local transcript model, measures from the PTC to the true final
exon-exon junction, and replaces the effective ``50_BP_RULE`` in ``LoF_info``.

No web service is used. Transcript structure comes from the configured Ensembl
GTF and sequence is fetched directly from the indexed BGZF reference FASTA.
The original verdict and all calculation provenance remain appended to CSQ.
"""

from __future__ import annotations

import argparse
import bisect
import gzip
import json
import re
import struct
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO, Iterable
from urllib.parse import unquote

import yaml


STOP_CODONS = {"TAA", "TAG", "TGA"}
APPENDED_FIELDS = (
    "PTC_cds_pos",
    "PTC_aa_pos",
    "PTC_dist_from_last_exon",
    "LoF_50_BP_RULE_original",
    "LoF_50_BP_RULE_PTC",
    "LoF_50_BP_RULE_changed",
    "PTC_dist_from_last_coding_exon",
    "LoF_50_BP_RULE_LOFTEE_anchor",
    "PTC_calc_status",
)


def open_text(path: Path, mode: str = "rt"):
    return gzip.open(path, mode) if path.name.endswith(".gz") else path.open(mode)


def revcomp(sequence: str) -> str:
    return sequence.translate(str.maketrans("ACGTNacgtn", "TGCANtgcan"))[::-1]


def parse_attributes(raw: str) -> dict[str, str]:
    return dict(re.findall(r'(\S+)\s+"([^"]*)"', raw))


def parse_csq_fields(line: str) -> list[str]:
    match = re.search(r"Format:\s*([^\">]+)", line)
    if not match:
        raise ValueError("CSQ header has no Format field list")
    return match.group(1).strip().split("|")


def update_csq_header(line: str, fields: list[str]) -> str:
    match = re.search(r"(Format:\s*)([^\">]+)", line)
    if not match:
        raise ValueError("CSQ header has no Format field list")
    new_format = "|".join(fields)
    return line[: match.start(2)] + new_format + line[match.end(2) :]


def info_map(raw: str) -> dict[str, str]:
    result = {}
    for item in raw.split(";"):
        key, separator, value = item.partition("=")
        if key:
            result[key] = value if separator else "1"
    return result


def replace_info_value(raw: str, key: str, value: str) -> str:
    items = raw.split(";") if raw not in {"", "."} else []
    replaced = False
    for index, item in enumerate(items):
        if item == key or item.startswith(key + "="):
            items[index] = f"{key}={value}"
            replaced = True
            break
    if not replaced:
        items.append(f"{key}={value}")
    return ";".join(items) if items else "."


def parse_lof_key(raw: str, key: str) -> str:
    match = re.search(rf"(?:^|[,&]){re.escape(key)}:([^,&]*)", raw or "")
    return match.group(1) if match else ""


def replace_lof_key(raw: str, key: str, value: str) -> str:
    raw = raw or ""
    pattern = re.compile(rf"(^|[,&])({re.escape(key)}):([^,&]*)")
    if pattern.search(raw):
        return pattern.sub(
            lambda match: f"{match.group(1)}{match.group(2)}:{value}", raw, count=1
        )
    separator = "&" if raw else ""
    return f"{raw}{separator}{key}:{value}"


class BgzipReader:
    """Small dependency-free BGZF random reader using a standard .gzi index."""

    def __init__(self, path: Path):
        self.path = path
        self.handle: BinaryIO = path.open("rb")
        gzi_path = Path(str(path) + ".gzi")
        with gzi_path.open("rb") as index:
            count_raw = index.read(8)
            if len(count_raw) != 8:
                raise ValueError(f"invalid GZI index: {gzi_path}")
            count = struct.unpack("<Q", count_raw)[0]
            pairs = [(0, 0)]
            for _ in range(count):
                raw = index.read(16)
                if len(raw) != 16:
                    raise ValueError(f"truncated GZI index: {gzi_path}")
                pairs.append(struct.unpack("<QQ", raw))
        self.compressed_offsets = [pair[0] for pair in pairs]
        self.uncompressed_offsets = [pair[1] for pair in pairs]

    def close(self) -> None:
        self.handle.close()

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()

    def _read_block(self, compressed_offset: int) -> bytes:
        self.handle.seek(compressed_offset)
        fixed = self.handle.read(12)
        if len(fixed) < 12 or fixed[:3] != b"\x1f\x8b\x08":
            raise ValueError(
                f"invalid BGZF block at compressed offset {compressed_offset}"
            )
        xlen = struct.unpack("<H", fixed[10:12])[0]
        extra = self.handle.read(xlen)
        bsize = None
        cursor = 0
        while cursor + 4 <= len(extra):
            subfield_id = extra[cursor : cursor + 2]
            subfield_len = struct.unpack("<H", extra[cursor + 2 : cursor + 4])[0]
            payload = extra[cursor + 4 : cursor + 4 + subfield_len]
            if subfield_id == b"BC" and subfield_len == 2:
                bsize = struct.unpack("<H", payload)[0] + 1
                break
            cursor += 4 + subfield_len
        if bsize is None:
            raise ValueError(f"gzip stream is not BGZF: {self.path}")
        already = len(fixed) + len(extra)
        remainder = self.handle.read(bsize - already)
        block = fixed + extra + remainder
        if len(block) != bsize:
            raise ValueError(f"truncated BGZF block: {self.path}")
        return gzip.decompress(block)

    def read(self, uncompressed_offset: int, length: int) -> bytes:
        if length <= 0:
            return b""
        index = bisect.bisect_right(
            self.uncompressed_offsets, uncompressed_offset
        ) - 1
        compressed = self.compressed_offsets[index]
        block_uncompressed = self.uncompressed_offsets[index]
        output = bytearray()
        while len(output) < length:
            block = self._read_block(compressed)
            start = max(0, uncompressed_offset - block_uncompressed)
            if start < len(block):
                output.extend(block[start : start + length - len(output)])
            compressed = self.handle.tell()
            block_uncompressed += len(block)
            if not block:
                break
        if len(output) < length:
            raise ValueError(
                f"requested sequence extends beyond BGZF stream: {self.path}"
            )
        return bytes(output)


class IndexedFasta:
    def __init__(self, path: Path):
        self.path = path
        self.reader = BgzipReader(path)
        self.index = {}
        fai_path = Path(str(path) + ".fai")
        with fai_path.open() as handle:
            for line in handle:
                columns = line.rstrip("\n").split("\t")
                if len(columns) >= 5:
                    self.index[columns[0]] = tuple(map(int, columns[1:5]))

    def close(self) -> None:
        self.reader.close()

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()

    def fetch(self, chrom: str, start: int, end: int) -> str:
        """Fetch a 1-based inclusive interval."""
        if chrom not in self.index:
            aliases = [chrom.removeprefix("chr"), "chr" + chrom]
            if chrom in {"MT", "M", "chrM"}:
                aliases.extend(["MT", "M", "chrM"])
            chrom = next((name for name in aliases if name in self.index), chrom)
        if chrom not in self.index:
            raise KeyError(f"contig absent from FASTA index: {chrom}")
        length, offset, line_bases, line_width = self.index[chrom]
        if start < 1 or end < start or end > length:
            raise ValueError(f"invalid FASTA interval {chrom}:{start}-{end}")
        start0 = start - 1
        wanted = end - start + 1
        within_line = start0 % line_bases
        file_offset = (
            offset + (start0 // line_bases) * line_width + within_line
        )
        newline_width = line_width - line_bases
        line_crossings = (within_line + wanted - 1) // line_bases
        raw_length = wanted + line_crossings * newline_width
        raw = self.reader.read(file_offset, raw_length)
        sequence = re.sub(rb"\s+", b"", raw).decode("ascii").upper()
        if len(sequence) != wanted:
            raise ValueError(
                f"FASTA fetch length mismatch at {chrom}:{start}-{end}: "
                f"{len(sequence)} != {wanted}"
            )
        return sequence


@dataclass
class Transcript:
    transcript_id: str
    chrom: str = ""
    version: int | None = None
    strand: int = 1
    biotype: str = ""
    exons: list[tuple[int, int]] = field(default_factory=list)
    coding_features: list[tuple[int, int]] = field(default_factory=list)
    blocks: list[tuple[int, int, int]] = field(default_factory=list)
    cds: str = ""
    utr3: str = ""
    last_coding_exon_cds: int | None = None
    last_exon_junction_cds: int | None = None
    terminal_exon_is_utr_only: bool = False
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def merge_intervals(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[list[int]] = []
    for start, end in sorted(set(intervals)):
        if merged and start <= merged[-1][1] + 1:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(start, end) for start, end in merged]


def load_transcripts(
    gtf_path: Path, wanted: set[str], fasta: IndexedFasta
) -> dict[str, Transcript]:
    models = {transcript_id: Transcript(transcript_id) for transcript_id in wanted}
    with open_text(gtf_path) as handle:
        for line in handle:
            if not line or line.startswith("#"):
                continue
            columns = line.rstrip("\n").split("\t")
            if len(columns) != 9 or columns[2] not in {
                "transcript",
                "exon",
                "CDS",
                "stop_codon",
            }:
                continue
            attributes = parse_attributes(columns[8])
            transcript_id = attributes.get("transcript_id")
            if transcript_id not in models:
                continue
            model = models[transcript_id]
            model.chrom = columns[0]
            model.strand = 1 if columns[6] == "+" else -1
            model.biotype = attributes.get(
                "transcript_biotype", attributes.get("gene_biotype", "")
            )
            version = attributes.get("transcript_version")
            if version and version.isdigit():
                model.version = int(version)
            start, end = int(columns[3]), int(columns[4])
            if columns[2] == "exon":
                model.exons.append((start, end))
            elif columns[2] in {"CDS", "stop_codon"}:
                model.coding_features.append((start, end))

    for model in models.values():
        prepare_transcript(model, fasta)
    return models


def prepare_transcript(model: Transcript, fasta: IndexedFasta) -> None:
    if not model.chrom:
        model.problems.append("transcript_not_found_in_gtf")
        return
    if not model.exons:
        model.problems.append("no_exons")
        return
    if not model.coding_features:
        model.problems.append("no_cds")
        return
    exons = merge_intervals(model.exons)
    coding = merge_intervals(model.coding_features)
    if model.strand == -1:
        exons.reverse()
        coding.reverse()
    model.exons = exons

    def sequence(intervals: list[tuple[int, int]]) -> str:
        pieces = []
        for start, end in intervals:
            value = fasta.fetch(model.chrom, start, end)
            pieces.append(value if model.strand == 1 else revcomp(value))
        return "".join(pieces)

    try:
        cdna = sequence(exons)
        cds = sequence(coding)
    except (KeyError, ValueError) as error:
        model.problems.append(f"sequence_fetch_failed:{error}")
        return

    cumulative = 0
    model.blocks = []
    for start, end in coding:
        model.blocks.append((start, end, cumulative))
        cumulative += end - start + 1
    # The coding-anchored 50 bp rule needs a junction between two coding
    # blocks. With a single merged CDS block no such junction exists, and the
    # anchor must not degenerate to CDS position 1 (which would make every
    # coding distance negative and the rule an unconditional FAIL).
    model.last_coding_exon_cds = (
        sum(end - start + 1 for start, end in coding[:-1]) + 1
        if len(coding) > 1
        else None
    )

    first_start, first_end = coding[0]
    utr5_len = 0
    first_coding_found = False
    for exon_start, exon_end in exons:
        if exon_start <= first_start <= exon_end or exon_start <= first_end <= exon_end:
            utr5_len += (
                first_start - exon_start
                if model.strand == 1
                else exon_end - first_end
            )
            first_coding_found = True
            break
        utr5_len += exon_end - exon_start + 1
    if not first_coding_found:
        model.problems.append("cds_not_within_exons")
        return

    expected = cdna[utr5_len : utr5_len + len(cds)]
    if expected != cds:
        model.problems.append("cds_not_found_at_expected_cdna_position")
    model.cds = cds
    model.utr3 = cdna[utr5_len + len(cds) :]
    last_exon_len = exons[-1][1] - exons[-1][0] + 1
    transcript_total = sum(end - start + 1 for start, end in exons)
    model.last_exon_junction_cds = (
        transcript_total - last_exon_len + 1 - utr5_len
    )
    model.terminal_exon_is_utr_only = (
        model.last_exon_junction_cds > len(cds)
    )

    if len(cds) != cumulative:
        model.problems.append("cds_exon_length_mismatch")
    if len(cds) % 3:
        model.problems.append("cds_not_multiple_of_3")
    if len(cds) < 3 or cds[-3:] not in STOP_CODONS:
        model.problems.append("cds_no_terminal_stop")
    internal = (cds[index : index + 3] for index in range(0, max(0, len(cds) - 3), 3))
    if any(codon in STOP_CODONS for codon in internal):
        model.problems.append("cds_internal_stop")


def genomic_to_cds(model: Transcript, genomic_position: int) -> int | None:
    for start, end, cumulative in model.blocks:
        if start <= genomic_position <= end:
            offset = (
                genomic_position - start
                if model.strand == 1
                else end - genomic_position
            )
            return cumulative + offset + 1
    return None


def empty_result(status: str) -> dict:
    return {
        "ptc_cds": None,
        "ptc_aa": None,
        "dist": None,
        "rule": "",
        "dist_coding": None,
        "rule_coding": "",
        "status": status,
    }


def trim_deletion_anchor(pos: int, ref: str, alt: str) -> tuple[int, str, str, bool]:
    if len(alt) == 1 and len(ref) > 1 and ref[0] == alt[0]:
        return pos + 1, ref[1:], "", True
    return pos, ref, alt, False


def score_edit(
    model: Transcript, pos: int, ref: str, alt: str, threshold: int
) -> dict:
    if not ref:
        c1 = genomic_to_cds(model, pos)
        c2 = c1
    else:
        c1 = genomic_to_cds(model, pos)
        c2 = genomic_to_cds(model, pos + len(ref) - 1)
    if c1 is None or c2 is None:
        return empty_result("outside_cds")
    cds_start, cds_end = min(c1, c2), max(c1, c2)
    if ref and cds_end - cds_start + 1 != len(ref):
        return empty_result("spans_exon_boundary")

    ref_coding = ref if model.strand == 1 else revcomp(ref)
    alt_coding = alt if model.strand == 1 else revcomp(alt)
    full = model.cds + model.utr3
    if ref_coding and full[cds_start - 1 : cds_start - 1 + len(ref_coding)] != ref_coding:
        return empty_result("ref_mismatch")
    mutated = (
        full[: cds_start - 1]
        + alt_coding
        + full[cds_start - 1 + len(ref_coding) :]
    )
    delta = len(alt_coding) - len(ref_coding)
    ptc = None
    for index in range(0, len(mutated) - 2, 3):
        if mutated[index : index + 3] in STOP_CODONS:
            ptc = index + 1
            break
    if ptc is None:
        return empty_result("no_stop_found")

    # The conventional 50/55-nt NMD rule is defined relative to a downstream
    # exon-exon junction. A single-exon transcript has no such junction. Keep
    # the reconstructed PTC for auditability, but do not manufacture a
    # distance/rule or replace LOFTEE's original annotation.
    if len(model.exons) == 1:
        return {
            "ptc_cds": ptc,
            "ptc_aa": (ptc - 1) // 3 + 1,
            "dist": None,
            "rule": "",
            "dist_coding": None,
            "rule_coding": "",
            "status": "not_applicable_single_exon_transcript",
        }

    def shifted(junction: int | None) -> int | None:
        if junction is not None and cds_start < junction:
            return junction + delta
        return junction

    true_junction = shifted(model.last_exon_junction_cds)
    coding_junction = shifted(model.last_coding_exon_cds)
    true_distance = None if true_junction is None else true_junction - ptc
    coding_distance = None if coding_junction is None else coding_junction - ptc
    return {
        "ptc_cds": ptc,
        "ptc_aa": (ptc - 1) // 3 + 1,
        "dist": true_distance,
        "rule": (
            "" if true_distance is None else "FAIL" if true_distance <= threshold else "PASS"
        ),
        "dist_coding": coding_distance,
        "rule_coding": (
            ""
            if coding_distance is None
            else "FAIL"
            if coding_distance <= threshold
            else "PASS"
        ),
        "status": (
            "ok_utr_only_terminal_exon"
            if model.terminal_exon_is_utr_only
            else "ok"
        ),
    }


def calculate(
    model: Transcript | None,
    pos: int,
    ref: str,
    alt: str,
    consequence: str,
    hgvsc: str,
    threshold: int,
) -> dict:
    if model is None:
        return empty_result("no_transcript_model")
    # LOFTEE itself only evaluates transcripts whose biotype is exactly
    # protein_coding. In particular, protein_coding_LoF transcripts have an
    # ORF disrupted on the reference haplotype, so they are not a valid intact
    # baseline for a patient-specific frameshift/PTC simulation.
    if model.biotype != "protein_coding":
        return empty_result(
            "unsupported_transcript_biotype:" + (model.biotype or "unknown")
        )
    if not model.ok:
        return empty_result("bad_transcript_model:" + "+".join(model.problems))
    if "start_lost" in consequence.split("&"):
        return empty_result("start_lost_unsupported")
    version_match = re.match(r"^ENST\d+\.(\d+)", unquote(hgvsc or ""))
    if version_match and model.version != int(version_match.group(1)):
        return empty_result("transcript_version_mismatch")
    if not ref or not alt or alt.startswith("<") or "*" in alt:
        return empty_result("unsupported_allele")

    result = score_edit(model, pos, ref.upper(), alt.upper(), threshold)
    if result["status"] == "outside_cds":
        new_pos, new_ref, new_alt, trimmed = trim_deletion_anchor(
            pos, ref.upper(), alt.upper()
        )
        if trimmed:
            fallback = score_edit(model, new_pos, new_ref, new_alt, threshold)
            if fallback["status"].startswith("ok"):
                return fallback
    return result


def allele_index(entry: dict[str, str], ref: str, alts: list[str]) -> int | None:
    raw_number = entry.get("ALLELE_NUM", "")
    if raw_number.isdigit() and 1 <= int(raw_number) <= len(alts):
        return int(raw_number) - 1
    allele = entry.get("Allele", "")
    exact = [index for index, alt in enumerate(alts) if alt == allele]
    if len(exact) == 1:
        return exact[0]
    candidates = []
    for index, alt in enumerate(alts):
        ref_min, alt_min = ref, alt
        while ref_min and alt_min and ref_min[0] == alt_min[0]:
            ref_min, alt_min = ref_min[1:], alt_min[1:]
        while ref_min and alt_min and ref_min[-1] == alt_min[-1]:
            ref_min, alt_min = ref_min[:-1], alt_min[:-1]
        if (alt_min or "-") == allele:
            candidates.append(index)
    return candidates[0] if len(candidates) == 1 else None


def collect_transcripts(vcf_path: Path) -> tuple[set[str], list[str]]:
    fields = None
    wanted: set[str] = set()
    with open_text(vcf_path) as handle:
        for line in handle:
            if line.startswith("##INFO=<ID=CSQ"):
                fields = parse_csq_fields(line)
                continue
            if line.startswith("#"):
                continue
            if fields is None:
                raise ValueError("VEP CSQ header was not found")
            info = info_map(line.rstrip("\n").split("\t")[7])
            for raw in info.get("CSQ", "").split(","):
                values = raw.split("|")
                values += [""] * (len(fields) - len(values))
                entry = dict(zip(fields, values))
                if "frameshift_variant" in entry.get("Consequence", "").split("&"):
                    transcript = entry.get("Feature", "").split(".", 1)[0]
                    if transcript.startswith("ENST"):
                        wanted.add(transcript)
    if fields is None:
        raise ValueError("VEP CSQ header was not found")
    return wanted, fields


def render(value) -> str:
    return "" if value is None else str(value)


def process_vcf(
    input_path: Path,
    output_path: Path,
    models: dict[str, Transcript],
    original_fields: list[str],
    threshold: int,
    replace_original: bool,
    reference_metadata: dict,
    reported_output: Path | None = None,
) -> dict:
    fields = list(original_fields)
    for name in APPENDED_FIELDS:
        if name not in fields:
            fields.append(name)
    original_width = len(original_fields)
    counters = Counter()
    statuses = Counter()
    transitions = Counter()
    hgvsp_checked = 0
    hgvsp_matched = 0

    # The output must honour a .gz suffix like the input read does — a plain
    # text file under a .vcf.gz name breaks every downstream gzip/tabix reader.
    with open_text(input_path) as source, open_text(output_path, "wt") as output:
        header_done = False
        for line in source:
            if line.startswith("##LOFTEE_PTC50="):
                continue
            if line.startswith("##INFO=<ID=CSQ"):
                output.write(update_csq_header(line, fields))
                continue
            if line.startswith("#CHROM") and not header_done:
                output.write(
                    "##LOFTEE_PTC50=<Method=frameshift_PTC_simulation,"
                    f"Threshold={threshold},"
                    f"Assembly={reference_metadata.get('assembly', 'unknown')},"
                    f"Ensembl={reference_metadata.get('ensembl_release', 'unknown')}>\n"
                )
                header_done = True
                output.write(line)
                continue
            if line.startswith("#"):
                output.write(line)
                continue
            columns = line.rstrip("\n").split("\t")
            if len(columns) < 8:
                raise ValueError(f"malformed VCF record: {line.rstrip()}")
            counters["records"] += 1
            chrom, pos_raw, ref, alt_raw = columns[0], columns[1], columns[3], columns[4]
            pos = int(pos_raw)
            alts = alt_raw.split(",")
            info = info_map(columns[7])
            rewritten = []
            for raw in info.get("CSQ", "").split(","):
                if not raw:
                    continue
                values = raw.split("|")
                values += [""] * (original_width - len(values))
                entry = dict(zip(original_fields, values))
                appended = {name: "" for name in APPENDED_FIELDS}
                if "frameshift_variant" in entry.get("Consequence", "").split("&"):
                    counters["frameshift_csq"] += 1
                    index = allele_index(entry, ref, alts)
                    if index is None:
                        result = empty_result("allele_mapping_failed")
                    else:
                        transcript = entry.get("Feature", "").split(".", 1)[0]
                        result = calculate(
                            models.get(transcript),
                            pos,
                            ref,
                            alts[index],
                            entry.get("Consequence", ""),
                            entry.get("HGVSc", ""),
                            threshold,
                        )
                    # On a deliberately repeated post-processing pass, retain
                    # the first/raw LOFTEE verdict rather than treating the
                    # previously corrected LoF_info value as "original".
                    original_rule = (
                        entry.get("LoF_50_BP_RULE_original", "")
                        or parse_lof_key(entry.get("LoF_info", ""), "50_BP_RULE")
                    )
                    status = result["status"]
                    statuses[status] += 1
                    successful = status.startswith("ok")
                    if successful:
                        counters["recomputed"] += 1
                        transition = f"{original_rule or 'missing'}->{result['rule']}"
                        transitions[transition] += 1
                        if original_rule and original_rule != result["rule"]:
                            counters["changed"] += 1
                        # Only rewrite LoF_info when LOFTEE actually produced
                        # one for this transcript. Writing 50_BP_RULE into an
                        # empty LoF_info would fabricate a LOFTEE-namespaced
                        # verdict for a transcript LOFTEE never scored; the
                        # recomputed rule is still published in this module's
                        # own LoF_50_BP_RULE_PTC field below.
                        if replace_original and entry.get("LoF_info"):
                            entry["LoF_info"] = replace_lof_key(
                                entry["LoF_info"], "50_BP_RULE", result["rule"]
                            )
                        hgvsp = unquote(entry.get("HGVSp", ""))
                        match = re.search(
                            r"p\.[A-Za-z]{3}(\d+)[A-Za-z]{3}fsTer(\d+)", hgvsp
                        )
                        if match:
                            hgvsp_checked += 1
                            expected_ptc = int(match.group(1)) + int(match.group(2)) - 1
                            if expected_ptc == result["ptc_aa"]:
                                hgvsp_matched += 1
                    appended.update(
                        {
                            "PTC_cds_pos": render(result["ptc_cds"]),
                            "PTC_aa_pos": render(result["ptc_aa"]),
                            "PTC_dist_from_last_exon": render(result["dist"]),
                            "LoF_50_BP_RULE_original": original_rule,
                            "LoF_50_BP_RULE_PTC": result["rule"],
                            "LoF_50_BP_RULE_changed": (
                                ""
                                if not successful or not original_rule
                                else "1"
                                if original_rule != result["rule"]
                                else "0"
                            ),
                            "PTC_dist_from_last_coding_exon": render(
                                result["dist_coding"]
                            ),
                            "LoF_50_BP_RULE_LOFTEE_anchor": result["rule_coding"],
                            "PTC_calc_status": status,
                        }
                    )
                entry.update(appended)
                rewritten.append("|".join(entry.get(name, "") for name in fields))
            if "CSQ" in info:
                columns[7] = replace_info_value(columns[7], "CSQ", ",".join(rewritten))
            output.write("\t".join(columns) + "\n")

    return {
        "schema_version": 1,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "input": str(input_path.resolve()),
        "output": str((reported_output or output_path).resolve()),
        "reference": reference_metadata,
        "threshold_bp": threshold,
        "replace_lof_info": replace_original,
        "records": counters["records"],
        "frameshift_csq": counters["frameshift_csq"],
        "recomputed": counters["recomputed"],
        "changed": counters["changed"],
        "statuses": dict(sorted(statuses.items())),
        "transitions": dict(sorted(transitions.items())),
        "hgvsp_fsTer_validation": {
            "checked": hgvsp_checked,
            "matched": hgvsp_matched,
        },
    }


def resolve_config_path(config_path: Path, value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return (config_path.resolve().parent.parent / path).resolve()


def configured_paths(config_path: Path) -> tuple[dict, Path, Path, int, bool]:
    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    block = ((config.get("post_processing") or {}).get("loftee_ptc_50bp") or {})
    gtf_value = block.get("gtf")
    fasta_value = ((config.get("reference") or {}).get("fasta") or {}).get("path")
    if not gtf_value or not fasta_value:
        raise ValueError("loftee_ptc_50bp requires configured GTF and reference FASTA")
    return (
        config,
        resolve_config_path(config_path, str(gtf_value)),
        resolve_config_path(config_path, str(fasta_value)),
        int(block.get("threshold_bp", 50)),
        bool(block.get("replace_lof_info", True)),
    )


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--reported-output",
        type=Path,
        help="final VCF path to record when --output is a temporary file",
    )
    parser.add_argument("--audit-json", type=Path)
    args = parser.parse_args(argv)
    config, gtf, fasta_path, threshold, replace_original = configured_paths(args.config)
    for required in (gtf, fasta_path, Path(str(fasta_path) + ".fai"), Path(str(fasta_path) + ".gzi")):
        if not required.is_file():
            raise SystemExit(f"required PTC 50-bp reference missing: {required}")

    wanted, fields = collect_transcripts(args.input)
    with IndexedFasta(fasta_path) as fasta:
        models = load_transcripts(gtf, wanted, fasta)
    release_match = re.search(r"\.(\d+)\.gtf\.gz$", gtf.name)
    metadata = {
        "assembly": (config.get("reference") or {}).get("assembly", "GRCh38"),
        "ensembl_release": release_match.group(1) if release_match else "unknown",
        "gtf": str(gtf),
        "fasta": str(fasta_path),
        "transcripts_requested": len(wanted),
        "transcript_models_ok": sum(model.ok for model in models.values()),
    }
    report = process_vcf(
        args.input,
        args.output,
        models,
        fields,
        threshold,
        replace_original,
        metadata,
        args.reported_output,
    )
    audit_path = args.audit_json or Path(str(args.output) + ".loftee_ptc50.audit.json")
    audit_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(
        "LOFTEE PTC 50-bp recomputation: "
        f"{report['recomputed']}/{report['frameshift_csq']} frameshift CSQ entries; "
        f"{report['changed']} changed; audit: {audit_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
