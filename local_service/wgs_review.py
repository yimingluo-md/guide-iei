#!/usr/bin/env python3
"""Indexed, conservative prefiltering for whole-genome review VCFs."""

from __future__ import annotations

import gzip
import hashlib
import json
import multiprocessing
import os
import uuid
import re
import tempfile
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Iterable

from local_service.cohort_store import (
    CohortStore,
    HtsBackend,
    VcfHeader,
    info_map,
    normalize_chromosome,
    parse_csq_entries,
    read_vcf_header,
)


AF_FIELDS = {
    "max_af", "gnomad_af_popmax", "gnomad_popmax_af",
    "gnomadg_af_popmax", "gnomade_af_popmax",
}
SPLICEAI_FIELDS = {
    "spliceai", "spliceai_pred_ds_ag", "spliceai_pred_ds_al",
    "spliceai_pred_ds_dg", "spliceai_pred_ds_dl",
    "ds_ag", "ds_al", "ds_dg", "ds_dl",
}
PROMOTERAI_FIELDS = {
    "promoterai", "promoterai_promoterai", "promoterai_score",
}
NONCODING_MODES = {"ccre", "all", "none"}
UNSCORED_SPLICEAI_INTRONIC = "SpliceAI_intronic"
UNSCORED_PROMOTERAI_PROMOTER = "PromoterAI_promoter"
PROMOTERAI_WINDOW_BP = 500


@dataclass(frozen=True)
class WgsPrefilterOptions:
    max_gnomad_popmax: float | None = 0.01
    min_spliceai: float | None = 0.5
    min_promoterai_abs: float | None = 0.8
    noncoding_mode: str = "ccre"

    @classmethod
    def from_payload(cls, payload: dict) -> "WgsPrefilterOptions":
        def optional_number(
            key: str,
            *,
            default: float | None,
            minimum: float,
            maximum: float | None = None,
        ):
            if key not in payload:
                return default
            value = payload.get(key)
            if value is None or value == "":
                return None
            if isinstance(value, bool):
                raise ValueError(f"{key} must be a number or null")
            try:
                parsed = float(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{key} must be a number or null") from exc
            if parsed < minimum or (maximum is not None and parsed > maximum):
                upper = f" and {maximum}" if maximum is not None else ""
                raise ValueError(f"{key} must be between {minimum}{upper}")
            return parsed

        noncoding_mode = str(payload.get("noncoding_mode") or "ccre").lower()
        if noncoding_mode not in NONCODING_MODES:
            raise ValueError("noncoding_mode must be ccre, all, or none")
        return cls(
            max_gnomad_popmax=optional_number(
                "max_gnomad_popmax", default=0.01, minimum=0, maximum=1
            ),
            min_spliceai=optional_number(
                "min_spliceai", default=0.5, minimum=0, maximum=1
            ),
            min_promoterai_abs=optional_number(
                "min_promoterai_abs", default=0.8, minimum=0
            ),
            noncoding_mode=noncoding_mode,
        )


def _open_text(path: Path):
    return (
        gzip.open(path, "rt", encoding="utf-8", errors="replace")
        if path.name.lower().endswith((".gz", ".bgz"))
        else path.open("rt", encoding="utf-8", errors="replace")
    )


def _header_lines(path: Path) -> list[str]:
    lines: list[str] = []
    with _open_text(path) as handle:
        for line in handle:
            if not line.startswith("#"):
                break
            lines.append(line)
            if line.startswith("#CHROM\t"):
                return lines
    raise ValueError(f"VCF header is incomplete: {path}")


def _filter_header(
    options: WgsPrefilterOptions, retain_exome_regions: bool,
    ccre_bed_path: Path | None, promoter_map_path: Path | None,
) -> str:
    def value(item: float | None) -> str:
        return "Disabled" if item is None else str(item)

    return (
        "##INFO=<ID=IEI_UNSCORED_INDEL,Number=A,Type=String,"
        "Description=\"Precomputed score unavailable for an applicable "
        "sequence-resolved indel; ampersand joins reasons and dot means not "
        "applicable. Values: SpliceAI_intronic,PromoterAI_promoter\">\n"
        "##IEI_WGS_PREFILTER=<"
        f"MaxGnomadPopmax={value(options.max_gnomad_popmax)},"
        f"MinSpliceAI={value(options.min_spliceai)},"
        f"MinPromoterAIAbs={value(options.min_promoterai_abs)},"
        f"NoncodingMode={options.noncoding_mode},"
        "SiteFilter=PASSorUnfiltered,PopulationMissing=Retain,EvidenceMissing=DoesNotQualify,"
        f"ExomeRegions={'CandidateRoute' if retain_exome_regions else 'Disabled'},"
        f"CCREResource={ccre_bed_path.name if ccre_bed_path else 'Unavailable'},"
        f"PromoterMap={promoter_map_path.name if promoter_map_path else 'Unavailable'},"
        "UnscoredIndelSafety=Enabled,"
        "Transcripts=MANEThenPICKThenOnePerGene>\n"
    )


def bed_intervals(path: Path) -> dict[str, tuple[tuple[int, int], ...]]:
    """Load and merge a BED as 1-based closed intervals by normalized contig."""
    if not path.is_file():
        raise ValueError(f"configured BED was not found: {path}")
    raw: dict[str, list[tuple[int, int]]] = {}
    with _open_text(path) as handle:
        for line in handle:
            if not line.strip() or line.startswith(("#", "track ", "browser ")):
                continue
            columns = line.rstrip("\r\n").split("\t")
            if len(columns) < 3:
                continue
            try:
                # BED is zero-based, half-open; VCF positions are one-based.
                start = int(columns[1]) + 1
                end = int(columns[2])
            except ValueError:
                continue
            if end < start:
                continue
            raw.setdefault(normalize_chromosome(columns[0]), []).append((start, end))

    if not raw:
        raise ValueError(f"configured BED contains no intervals: {path}")
    merged: dict[str, tuple[tuple[int, int], ...]] = {}
    for chrom, values in raw.items():
        combined: list[list[int]] = []
        for start, end in sorted(values):
            if combined and start <= combined[-1][1] + 1:
                combined[-1][1] = max(combined[-1][1], end)
            else:
                combined.append([start, end])
        merged[chrom] = tuple((start, end) for start, end in combined)
    return merged


def promoterai_intervals(
    path: Path,
    window_bp: int = PROMOTERAI_WINDOW_BP,
) -> dict[str, tuple[tuple[int, int], ...]]:
    """Build merged TSS +/- window intervals from the prepared PromoterAI map."""
    if not path.is_file():
        raise ValueError(f"configured PromoterAI transcript map was not found: {path}")
    raw: dict[str, list[tuple[int, int]]] = {}
    with _open_text(path) as handle:
        header = next(handle, "").rstrip("\r\n").split("\t")
        columns = {name: index for index, name in enumerate(header)}
        if "chrom" not in columns or "tss_pos" not in columns:
            raise ValueError(
                f"PromoterAI transcript map lacks chrom/tss_pos columns: {path}"
            )
        for line in handle:
            values = line.rstrip("\r\n").split("\t")
            try:
                chrom = normalize_chromosome(values[columns["chrom"]])
                tss_pos = int(values[columns["tss_pos"]])
            except (IndexError, ValueError):
                continue
            raw.setdefault(chrom, []).append((
                max(1, tss_pos - window_bp), tss_pos + window_bp,
            ))
    if not raw:
        raise ValueError(f"PromoterAI transcript map contains no TSS records: {path}")
    merged: dict[str, tuple[tuple[int, int], ...]] = {}
    for chrom, values in raw.items():
        combined: list[list[int]] = []
        for start, end in sorted(values):
            if combined and start <= combined[-1][1] + 1:
                combined[-1][1] = max(combined[-1][1], end)
            else:
                combined.append([start, end])
        merged[chrom] = tuple((start, end) for start, end in combined)
    return merged


def _numbers(records: Iterable[dict[str, str]], names: set[str]) -> list[float]:
    """Collect numeric values from CSQ entries already matched to one ALT."""
    values: list[float] = []
    for record in records:
        for key, raw in record.items():
            if key.lower() not in names or raw in {"", ".", "-"}:
                continue
            for token in re.split(r"[,&|]", raw):
                try:
                    values.append(float(token))
                except ValueError:
                    continue
    return values


def _info_numbers(
    info: dict[str, str], names: set[str], alt_index: int, alt_count: int
) -> list[float]:
    """Collect numeric values from record-level INFO for one ALT.

    Per-allele INFO fields (Number=A) carry one comma-separated token per
    ALT in ALT order; pooling them across alleles assigns one allele's value
    to another (a rare ALT sharing a record with a common one inherits the
    common AF and is wrongly excluded). When the comma arity matches the ALT
    count, select this ALT's token; any other shape cannot be attributed and
    is pooled as before.
    """
    values: list[float] = []
    for key, raw in info.items():
        if key.lower() not in names or raw in {"", ".", "-"}:
            continue
        tokens = raw.split(",")
        if alt_count > 1 and len(tokens) == alt_count:
            tokens = [tokens[alt_index]]
        for token in tokens:
            for piece in re.split(r"[&|]", token):
                try:
                    values.append(float(piece))
                except ValueError:
                    continue
    return values


def _range_overlaps(
    chrom: str,
    start: int,
    end: int,
    intervals: dict[str, tuple[tuple[int, int], ...]],
) -> bool:
    """Return whether a one-based closed variant span intersects merged BED."""
    values = intervals.get(chrom, ())
    low = 0
    high = len(values)
    while low < high:
        middle = (low + high) // 2
        if values[middle][0] <= end:
            low = middle + 1
        else:
            high = middle
    return low > 0 and values[low - 1][1] >= start


def evaluate_record(
    line: str,
    header: VcfHeader,
    options: WgsPrefilterOptions,
    ccre_intervals: dict[str, tuple[tuple[int, int], ...]],
    exome_intervals: dict[str, tuple[tuple[int, int], ...]] | None = None,
    promoter_intervals: dict[str, tuple[tuple[int, int], ...]] | None = None,
) -> tuple[bool, tuple[tuple[str, ...], ...]]:
    columns = line.rstrip("\r\n").split("\t")
    if len(columns) < 8:
        raise ValueError("encountered a structurally invalid VCF record")
    # Site FILTER: PASS and "." (site filtering not applied upstream) are both
    # eligible; explicit failure labels are excluded. Same policy as the
    # annotation pre-filter and the exome review parser.
    if columns[6] not in ("PASS", "."):
        return False, ()
    chrom = normalize_chromosome(columns[0])
    try:
        pos = int(columns[1])
    except ValueError as exc:
        raise ValueError(f"invalid VCF position: {columns[1]}") from exc
    record_end = pos + max(1, len(columns[3])) - 1
    in_exome = bool(
        exome_intervals
        and _range_overlaps(chrom, pos, record_end, exome_intervals)
    )
    in_ccre = bool(
        ccre_intervals
        and _range_overlaps(chrom, pos, record_end, ccre_intervals)
    )
    in_promoter = bool(
        promoter_intervals
        and _range_overlaps(chrom, pos, record_end, promoter_intervals)
    )

    info = info_map(columns[7])
    consequences = parse_csq_entries(info.get("CSQ", ""), list(header.csq_fields))
    available_fields = {
        field.lower() for field in (*header.csq_fields, *header.info_fields)
    }
    spliceai_dataset_available = bool(available_fields & SPLICEAI_FIELDS)
    promoterai_dataset_available = bool(available_fields & PROMOTERAI_FIELDS)
    alts = columns[4].split(",")
    reasons_by_alt: list[tuple[str, ...]] = []
    retain_record = False
    for alt_index, alt in enumerate(alts):
        matched: list[dict[str, str]] = []
        for consequence in consequences:
            allele_number = consequence.get("ALLELE_NUM", "")
            if allele_number.isdigit():
                if int(allele_number) == alt_index + 1:
                    matched.append(consequence)
            elif not consequence.get("Allele") or consequence.get("Allele") == alt:
                matched.append(consequence)
        if not matched and len(alts) == 1:
            # A single-ALT record's consequences necessarily describe this
            # ALT even when VEP's minimised Allele string does not compare
            # equal to the raw ALT. A multi-allelic record with no allele
            # match must NOT inherit the other alleles' annotations.
            matched = consequences
        # When a multi-allelic record's entries cannot be attributed to one
        # ALT, never let the unattributable values EXCLUDE an allele (the
        # frequency gate below uses `matched` only), but do let them QUALIFY
        # the record for retention: retention is record-granular, and keeping
        # the record is the safe direction for a diagnostic prefilter.
        qualification_entries = matched or consequences

        frequencies = _numbers(matched, AF_FIELDS) + _info_numbers(
            info, AF_FIELDS, alt_index, len(alts)
        )
        if (
            options.max_gnomad_popmax is not None
            and frequencies
            and max(frequencies) > options.max_gnomad_popmax
        ):
            reasons_by_alt.append(())
            continue

        splice_values = _numbers(qualification_entries, SPLICEAI_FIELDS) + _info_numbers(
            info, SPLICEAI_FIELDS, alt_index, len(alts)
        )
        promoter_values = _numbers(qualification_entries, PROMOTERAI_FIELDS) + _info_numbers(
            info, PROMOTERAI_FIELDS, alt_index, len(alts)
        )
        splice_qualifies = bool(
            options.min_spliceai is not None
            and splice_values
            and max(splice_values) >= options.min_spliceai
        )
        promoter_qualifies = bool(
            options.min_promoterai_abs is not None
            and promoter_values
            and max(abs(value) for value in promoter_values)
            >= options.min_promoterai_abs
        )
        sequence_indel = bool(
            re.fullmatch(r"[ACGTNacgtn]+", columns[3])
            and re.fullmatch(r"[ACGTNacgtn]+", alt)
            and len(columns[3]) != len(alt)
        )
        consequence_terms = {
            term
            for consequence in qualification_entries
            for term in consequence.get("Consequence", "").split("&")
            if term
        }
        unscored_reasons: list[str] = []
        if (
            sequence_indel
            and options.min_spliceai is not None
            and spliceai_dataset_available
            and not splice_values
            and "intron_variant" in consequence_terms
        ):
            unscored_reasons.append(UNSCORED_SPLICEAI_INTRONIC)
        if (
            sequence_indel
            and options.min_promoterai_abs is not None
            and promoterai_dataset_available
            and not promoter_values
            and in_promoter
        ):
            unscored_reasons.append(UNSCORED_PROMOTERAI_PROMOTER)
        reasons_by_alt.append(tuple(unscored_reasons))
        noncoding_qualifies = (
            (options.noncoding_mode == "ccre" and in_ccre)
            or (options.noncoding_mode == "all" and not in_exome)
        )
        if (
            in_exome
            or splice_qualifies
            or promoter_qualifies
            or noncoding_qualifies
            or unscored_reasons
        ):
            retain_record = True
    return retain_record, tuple(reasons_by_alt)


def record_passes(
    line: str,
    header: VcfHeader,
    options: WgsPrefilterOptions,
    ccre_intervals: dict[str, tuple[tuple[int, int], ...]],
    exome_intervals: dict[str, tuple[tuple[int, int], ...]] | None = None,
    promoter_intervals: dict[str, tuple[tuple[int, int], ...]] | None = None,
) -> bool:
    return evaluate_record(
        line, header, options, ccre_intervals, exome_intervals,
        promoter_intervals,
    )[0]


def add_unscored_indel_info(
    line: str,
    reasons_by_alt: tuple[tuple[str, ...], ...],
) -> str:
    """Add an allele-specific INFO flag without changing any source annotation."""
    if not any(reasons_by_alt):
        return line if line.endswith("\n") else line + "\n"
    columns = line.rstrip("\r\n").split("\t")
    if len(columns) < 8:
        raise ValueError("encountered a structurally invalid VCF record")
    values = ["&".join(reasons) if reasons else "." for reasons in reasons_by_alt]
    parts = [
        part for part in columns[7].split(";")
        if part and not part.startswith("IEI_UNSCORED_INDEL=")
    ]
    parts.append("IEI_UNSCORED_INDEL=" + ",".join(values))
    columns[7] = ";".join(parts) if parts else "."
    return "\t".join(columns) + "\n"


def compact_review_transcripts(line: str, header: VcfHeader) -> tuple[str, int, int]:
    """Keep every site while bounding redundant transcript rows for the browser."""
    columns = line.rstrip("\r\n").split("\t")
    if len(columns) < 8 or not header.csq_fields:
        return line if line.endswith("\n") else line + "\n", 0, 0
    info_parts = columns[7].split(";")
    csq_index = next(
        (index for index, value in enumerate(info_parts) if value.startswith("CSQ=")),
        None,
    )
    if csq_index is None:
        return line if line.endswith("\n") else line + "\n", 0, 0
    raw = info_parts[csq_index][4:]
    if not raw:
        return line if line.endswith("\n") else line + "\n", 0, 0

    entries = raw.split(",")
    parsed = [entry.split("|") for entry in entries]
    fields = {name: index for index, name in enumerate(header.csq_fields)}

    def value(row: list[str], *names: str) -> str:
        for name in names:
            index = fields.get(name)
            if index is not None and index < len(row) and row[index] not in {"", ".", "-"}:
                return row[index]
        return ""

    groups: dict[tuple[str, str], list[int]] = {}
    for index, row in enumerate(parsed):
        allele = value(row, "ALLELE_NUM", "Allele") or "1"
        gene = value(row, "SYMBOL", "Gene", "HGNC") or "intergenic"
        groups.setdefault((allele, gene), []).append(index)

    selected: set[int] = set()
    for indexes in groups.values():
        mane = [
            index for index in indexes
            if value(parsed[index], "MANE_SELECT", "MANE_PLUS_CLINICAL")
        ]
        picked = [
            index for index in indexes
            if value(parsed[index], "PICK") not in {"", "0"}
        ]
        selected.update(mane or picked or indexes[:1])

    retained = [entry for index, entry in enumerate(entries) if index in selected]
    info_parts[csq_index] = "CSQ=" + ",".join(retained)
    columns[7] = ";".join(info_parts)
    return "\t".join(columns) + "\n", len(entries), len(retained)


def _filter_group_worker(
    backend: HtsBackend,
    source: Path,
    contigs: tuple[str, ...],
    header: VcfHeader,
    options: WgsPrefilterOptions,
    ccre_intervals: dict[str, tuple[tuple[int, int], ...]],
    exome_intervals: dict[str, tuple[tuple[int, int], ...]],
    promoter_intervals: dict[str, tuple[tuple[int, int], ...]],
    destination: Path,
) -> dict:
    scanned = 0
    retained = 0
    annotations_scanned = 0
    annotations_retained = 0
    unscored_intronic_indels = 0
    unscored_promoter_indels = 0
    with destination.open("wt", encoding="utf-8") as output:
        for line in backend.iter_records(source, contigs):
            scanned += 1
            retain, reasons_by_alt = evaluate_record(
                line, header, options, ccre_intervals, exome_intervals,
                promoter_intervals,
            )
            if retain:
                flagged = add_unscored_indel_info(line, reasons_by_alt)
                compacted, input_count, output_count = compact_review_transcripts(
                    flagged, header
                )
                output.write(compacted)
                retained += 1
                annotations_scanned += input_count
                annotations_retained += output_count
                unscored_intronic_indels += sum(
                    UNSCORED_SPLICEAI_INTRONIC in reasons
                    for reasons in reasons_by_alt
                )
                unscored_promoter_indels += sum(
                    UNSCORED_PROMOTERAI_PROMOTER in reasons
                    for reasons in reasons_by_alt
                )
    return {
        "path": str(destination),
        "records_scanned": scanned,
        "records_retained": retained,
        "annotations_scanned": annotations_scanned,
        "annotations_retained": annotations_retained,
        "unscored_intronic_indels": unscored_intronic_indels,
        "unscored_promoter_indels": unscored_promoter_indels,
    }


class WgsReviewStore:
    """Prepare, shard, conservatively filter, and cache indexed review VCFs."""

    def __init__(self, state_dir: Path, cohort: CohortStore, *, workspace_dir: Path | None = None):
        self.state_dir = state_dir.resolve()
        self.cohort = cohort
        self.workspace_dir = (workspace_dir or self.state_dir).resolve()
        self.cache_dir = self.workspace_dir / "wgs-review-cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def prefilter(
        self,
        source: Path,
        options: WgsPrefilterOptions,
        exome_bed_path: Path | None = None,
        ccre_bed_path: Path | None = None,
        promoter_map_path: Path | None = None,
        progress: Callable[[dict], None] | None = None,
    ) -> dict:
        source = source.resolve()
        exome_bed_path = exome_bed_path.resolve() if exome_bed_path else None
        ccre_bed_path = ccre_bed_path.resolve() if ccre_bed_path else None
        promoter_map_path = (
            promoter_map_path.resolve() if promoter_map_path else None
        )
        if progress:
            progress({
                "phase": "preparing_index",
                "progress": 2.0,
                "message": "Preparing or validating the BGZF/tabix index…",
                "records_scanned": 0,
                "records_retained": 0,
                "reader_count": 1,
            })
        prepared = self.cohort.prepare_vcf(source)
        backend = self.cohort.hts_backend
        if prepared.index_path is None or backend is None:
            raise RuntimeError(
                prepared.warning
                or "whole-genome review requires bcftools/tabix or the configured HTS container"
            )
        header = read_vcf_header(prepared.path)
        available_fields = {
            field.lower() for field in (*header.csq_fields, *header.info_fields)
        }
        if options.max_gnomad_popmax is not None and not (
            available_fields & AF_FIELDS
        ):
            raise ValueError(
                "gnomAD popmax filtering is enabled, but this VCF does not "
                "declare a recognized population-frequency field; per-variant "
                "missing values may pass, but a missing dataset cannot"
            )
        contigs = backend.list_contigs(prepared.path)
        if not contigs:
            raise ValueError("the indexed WGS VCF contains no queryable contigs")
        exome_intervals = (
            bed_intervals(exome_bed_path) if exome_bed_path else {}
        )
        if options.noncoding_mode == "ccre" and (
            ccre_bed_path is None or not ccre_bed_path.is_file()
        ):
            raise ValueError(
                "ENCODE cCRE is the selected noncoding mode, but the configured "
                "SCREEN cCRE BED is not installed"
            )
        active_ccre_path = (
            ccre_bed_path if options.noncoding_mode == "ccre" else None
        )
        ccre_intervals = (
            bed_intervals(active_ccre_path)
            if active_ccre_path is not None and active_ccre_path.is_file()
            else {}
        )
        active_promoter_map_path = (
            promoter_map_path
            if (
                options.min_promoterai_abs is not None
                and promoter_map_path is not None
                and promoter_map_path.is_file()
            )
            else None
        )
        promoter_intervals = (
            promoterai_intervals(active_promoter_map_path)
            if active_promoter_map_path else {}
        )
        if progress:
            progress({
                "phase": "filtering",
                "progress": 10.0,
                "message": "Filtering indexed chromosome shards…",
                "records_scanned": 0,
                "records_retained": 0,
                "reader_count": min(self.cohort.index_readers, len(contigs)),
            })

        stat = source.stat()
        from local_service.cohort_store import _content_probe, _full_content_sha256
        source_sha = _full_content_sha256(source)
        fingerprint_value = {
            # v5: worker interval slicing normalizes contig names — v4 review
            # caches built from chr-prefixed VCFs under-retained coding and
            # cCRE variants and must be rebuilt.
            "review_format_version": 6,
            "source": str(source),
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
            # First/last-block digest: defeats same-size content swaps with
            # restored timestamps without re-reading a multi-GB genome.
            # Existing caches rebuild once when this field first appears.
            "content_probe": _content_probe(source),
            # Files small enough to hash completely also bind the full
            # digest, closing the probe's interior blind windows. The key is
            # added only when a hash exists so multi-GB fingerprints (and
            # their caches) are untouched.
            **({"content_sha256": source_sha} if source_sha else {}),
            "filters": asdict(options),
            "exome_bed": (
                [
                    str(exome_bed_path),
                    exome_bed_path.stat().st_size,
                    exome_bed_path.stat().st_mtime_ns,
                ]
                if exome_bed_path else None
            ),
            "ccre_bed": (
                [
                    str(active_ccre_path),
                    active_ccre_path.stat().st_size,
                    active_ccre_path.stat().st_mtime_ns,
                ]
                if active_ccre_path and active_ccre_path.is_file() else None
            ),
            "promoter_map": (
                [
                    str(active_promoter_map_path),
                    active_promoter_map_path.stat().st_size,
                    active_promoter_map_path.stat().st_mtime_ns,
                ]
                if active_promoter_map_path else None
            ),
        }
        fingerprint = hashlib.sha256(
            json.dumps(fingerprint_value, sort_keys=True).encode()
        ).hexdigest()[:24]
        cache_root = self.cache_dir / fingerprint
        cache_root.mkdir(parents=True, exist_ok=True)
        source_stem = re.sub(r"\.vcf(?:\.gz)?$", "", source.name, flags=re.IGNORECASE)
        source_stem = re.sub(r"[^A-Za-z0-9._-]+", "_", source_stem)[:120] or "wgs"
        output = cache_root / f"{source_stem}.review.prefiltered.vcf.gz"
        metadata_path = Path(f"{output}.json")
        cached_index = backend.validate_index(output) if output.is_file() else None
        if cached_index and metadata_path.is_file():
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            metadata.update({
                "path": str(output),
                "index_path": str(cached_index),
                "cache_hit": True,
                "prepared_cache_hit": prepared.cache_hit,
            })
            if progress:
                progress({
                    "phase": "complete",
                    "progress": 100.0,
                    "message": "Reused cached WGS prefilter output.",
                    "records_scanned": metadata.get("records_scanned", 0),
                    "records_retained": metadata.get("records_retained", 0),
                    "reader_count": metadata.get("reader_count", 1),
                })
            return metadata

        for candidate in (output, Path(f"{output}.tbi"), Path(f"{output}.csi")):
            candidate.unlink(missing_ok=True)
        reader_count = min(self.cohort.index_readers, len(contigs))
        results: list[dict] = []
        with tempfile.TemporaryDirectory(
            prefix="wgs-review-", dir=self.cache_dir
        ) as temporary_directory:
            temporary_root = Path(temporary_directory)

            def interval_slice(
                intervals: dict[str, tuple[tuple[int, int], ...]],
                group: tuple[str, ...],
            ) -> dict[str, tuple[tuple[int, int], ...]]:
                # Interval dicts and evaluate_record both use NORMALIZED
                # contig names; tabix reports the VCF's raw names. Slicing by
                # the raw name silently handed every worker empty interval
                # sets on chr-prefixed VCFs, killing the exome and cCRE
                # retention routes for the entire import.
                sliced: dict[str, tuple[tuple[int, int], ...]] = {}
                for contig in group:
                    key = normalize_chromosome(contig)
                    if key in intervals:
                        sliced[key] = intervals[key]
                return sliced

            # One worker stream per reader rather than per contig: a genome
            # VCF lists 100+ alt/decoy/unplaced contigs, and in container
            # mode each shard pays a full `docker run` startup — 126 spawns
            # where reader_count streams suffice. Round-robin keeps the large
            # chromosomes spread across readers.
            contig_groups = [
                tuple(contigs[start::max(1, reader_count)])
                for start in range(max(1, reader_count))
            ]
            contig_groups = [group for group in contig_groups if group]

            def collect(executor) -> None:
                futures = {
                    executor.submit(
                        _filter_group_worker,
                        backend,
                        prepared.path,
                        group,
                        header,
                        options,
                        interval_slice(ccre_intervals, group),
                        interval_slice(exome_intervals, group),
                        interval_slice(promoter_intervals, group),
                        temporary_root / f"shard-{index:04d}.vcf",
                    ): index
                    for index, group in enumerate(contig_groups)
                }
                for future in as_completed(futures):
                    results.append(future.result())
                    if progress:
                        completed = len(results)
                        progress({
                            "phase": "filtering",
                            "progress": 10.0 + (70.0 * completed / len(contig_groups)),
                            "message": (
                                f"Filtered {completed} of {len(contig_groups)} "
                                "chromosome shard groups."
                            ),
                            "records_scanned": sum(
                                item["records_scanned"] for item in results
                            ),
                            "records_retained": sum(
                                item["records_retained"] for item in results
                            ),
                            "reader_count": reader_count,
                        })

            if reader_count > 1:
                try:
                    executor = ProcessPoolExecutor(
                        max_workers=reader_count,
                        mp_context=multiprocessing.get_context("spawn"),
                    )
                except (PermissionError, NotImplementedError):
                    executor = ThreadPoolExecutor(
                        max_workers=reader_count,
                        thread_name_prefix="wgs-review-reader",
                    )
            else:
                executor = ThreadPoolExecutor(max_workers=1)
            with executor:
                collect(executor)

            if progress:
                progress({
                    "phase": "merging",
                    "progress": 84.0,
                    "message": "Merging retained chromosome shards…",
                    "records_scanned": sum(
                        item["records_scanned"] for item in results
                    ),
                    "records_retained": sum(
                        item["records_retained"] for item in results
                    ),
                    "reader_count": reader_count,
                })
            merged = temporary_root / "merged.vcf"
            with merged.open("wt", encoding="utf-8") as destination:
                raw_header = _header_lines(prepared.path)
                for line in raw_header:
                    if (
                        line.startswith("##IEI_WGS_PREFILTER=<")
                        or line.startswith("##INFO=<ID=IEI_UNSCORED_INDEL,")
                    ):
                        continue
                    if line.startswith("#CHROM\t"):
                        destination.write(_filter_header(
                            options, bool(exome_bed_path), active_ccre_path,
                            active_promoter_map_path,
                        ))
                    destination.write(line)
                for result in sorted(results, key=lambda item: item["path"]):
                    chunk = Path(result["path"])
                    with chunk.open("rt", encoding="utf-8") as source_handle:
                        for line in source_handle:
                            destination.write(line)
                # The next step reads this file inside a freshly started
                # container; force it to disk so the bind mount cannot serve
                # a truncated view (VirtioFS caching).
                destination.flush()
                os.fsync(destination.fileno())
            if progress:
                progress({
                    "phase": "compressing",
                    "progress": 90.0,
                    "message": "Sorting and BGZF-compressing retained variants…",
                    "reader_count": reader_count,
                })
            # Stage, then publish atomically: two same-fingerprint prefilter
            # runs writing the shared cache path directly could interleave
            # and the survivor's index turned the mix into a trusted cache.
            staged_output = output.with_name(
                f".{output.name}.{uuid.uuid4().hex}.staging.vcf.gz"
            )
            backend.sort_bgzip(merged, staged_output)
            os.replace(staged_output, output)
            if progress:
                progress({
                    "phase": "indexing_output",
                    "progress": 96.0,
                    "message": "Creating the review VCF tabix/CSI index…",
                    "reader_count": reader_count,
                })
            index_path = backend.create_index(output)

        metadata = {
            "path": str(output),
            "index_path": str(index_path),
            "cache_hit": False,
            "prepared_cache_hit": prepared.cache_hit,
            "reader_count": reader_count,
            "records_scanned": sum(
                result["records_scanned"] for result in results
            ),
            "records_retained": sum(
                result["records_retained"] for result in results
            ),
            "annotations_scanned": sum(
                result["annotations_scanned"] for result in results
            ),
            "annotations_retained": sum(
                result["annotations_retained"] for result in results
            ),
            "unscored_intronic_indels": sum(
                result["unscored_intronic_indels"] for result in results
            ),
            "unscored_promoter_indels": sum(
                result["unscored_promoter_indels"] for result in results
            ),
            "noncoding_mode": options.noncoding_mode,
            "ccre_resource": str(active_ccre_path) if active_ccre_path else "",
            "promoter_map_resource": (
                str(active_promoter_map_path) if active_promoter_map_path else ""
            ),
            "promoter_indel_safety": (
                "enabled" if active_promoter_map_path
                else "disabled" if options.min_promoterai_abs is None
                else "unavailable"
            ),
            "preparation_warning": prepared.warning,
        }
        metadata_path.write_text(
            json.dumps(metadata, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        if progress:
            progress({
                "phase": "complete",
                "progress": 100.0,
                "message": "WGS indexing and prefiltering complete.",
                "records_scanned": metadata["records_scanned"],
                "records_retained": metadata["records_retained"],
                "reader_count": reader_count,
            })
        return metadata
