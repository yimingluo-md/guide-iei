#!/usr/bin/env python3
"""Indexed, conservative prefiltering for whole-genome review VCFs."""

from __future__ import annotations

import gzip
import hashlib
import json
import multiprocessing
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
    "promoterai", "promoterai_promoterai",
}
CADD_FIELDS = {
    "cadd_phred", "cadd_wgs_cadd_phred", "cadd_wgs_phred", "phred",
}


@dataclass(frozen=True)
class WgsPrefilterOptions:
    max_gnomad_popmax: float | None = 0.01
    min_spliceai: float | None = 0.5
    min_promoterai_abs: float | None = 0.5
    min_cadd: float | None = None
    genes: tuple[str, ...] = ()
    gene_window_bp: int = 0

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

        raw_genes = payload.get("genes") or []
        if not isinstance(raw_genes, list) or not all(
            isinstance(gene, str) for gene in raw_genes
        ):
            raise ValueError("genes must be a list of gene symbols")
        genes = tuple(dict.fromkeys(
            gene.strip().upper() for gene in raw_genes if gene.strip()
        ))
        if len(genes) > 5_000:
            raise ValueError("the WGS prefilter is limited to 5,000 gene symbols")
        invalid = [gene for gene in genes if not re.fullmatch(r"[A-Z0-9._-]+", gene)]
        if invalid:
            raise ValueError("invalid gene symbol: " + invalid[0])
        try:
            window = int(payload.get("gene_window_bp", 0) or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError("gene_window_bp must be a whole number") from exc
        if window < 0 or window > 1_000_000:
            raise ValueError("gene_window_bp must be between 0 and 1,000,000")
        return cls(
            max_gnomad_popmax=optional_number(
                "max_gnomad_popmax", default=0.01, minimum=0, maximum=1
            ),
            min_spliceai=optional_number(
                "min_spliceai", default=0.5, minimum=0, maximum=1
            ),
            min_promoterai_abs=optional_number(
                "min_promoterai_abs", default=0.5, minimum=0
            ),
            min_cadd=optional_number("min_cadd", default=None, minimum=0),
            genes=genes,
            gene_window_bp=window,
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
    options: WgsPrefilterOptions, retain_exome_regions: bool
) -> str:
    def value(item: float | None) -> str:
        return "Disabled" if item is None else str(item)

    return (
        "##IEI_WGS_PREFILTER=<"
        f"MaxGnomadPopmax={value(options.max_gnomad_popmax)},"
        f"MinSpliceAI={value(options.min_spliceai)},"
        f"MinPromoterAIAbs={value(options.min_promoterai_abs)},"
        f"MinCADD={value(options.min_cadd)},"
        f"GeneCount={len(options.genes)},GeneWindowBP={options.gene_window_bp},"
        "SiteFilter=PASS,MissingValues=Retain,"
        f"ExomeRegions={'AlwaysRetain' if retain_exome_regions else 'Disabled'},"
        "Transcripts=MANEThenPICKThenOnePerGene>\n"
    )


def _attribute(attributes: str, key: str) -> str:
    match = re.search(rf'(?:^|;\s*){re.escape(key)}\s+"([^"]+)"', attributes)
    return match.group(1) if match else ""


def gene_intervals(
    gtf_path: Path, genes: tuple[str, ...], window_bp: int
) -> tuple[dict[str, tuple[tuple[int, int], ...]], tuple[str, ...]]:
    """Return merged GRCh38 intervals for selected GTF gene names or IDs."""
    if not genes:
        return {}, ()
    if not gtf_path.is_file():
        raise ValueError(
            "a configured GRCh38 GTF is required for the gene-list WGS prefilter"
        )
    requested = set(genes)
    found: set[str] = set()
    raw: dict[str, list[tuple[int, int]]] = {}
    with _open_text(gtf_path) as handle:
        for line in handle:
            if not line or line.startswith("#"):
                continue
            columns = line.rstrip("\r\n").split("\t")
            if len(columns) != 9 or columns[2] != "gene":
                continue
            symbols = {
                _attribute(columns[8], "gene_name").upper(),
                _attribute(columns[8], "gene_id").split(".", 1)[0].upper(),
            } - {""}
            matched = symbols & requested
            if not matched:
                continue
            try:
                start = max(1, int(columns[3]) - window_bp)
                end = int(columns[4]) + window_bp
            except ValueError:
                continue
            chrom = normalize_chromosome(columns[0])
            raw.setdefault(chrom, []).append((start, end))
            found.update(matched)
    if not found:
        raise ValueError("none of the requested genes were found in the configured GRCh38 GTF")

    merged: dict[str, tuple[tuple[int, int], ...]] = {}
    for chrom, values in raw.items():
        combined: list[list[int]] = []
        for start, end in sorted(values):
            if combined and start <= combined[-1][1] + 1:
                combined[-1][1] = max(combined[-1][1], end)
            else:
                combined.append([start, end])
        merged[chrom] = tuple((start, end) for start, end in combined)
    return merged, tuple(sorted(requested - found))


def bed_intervals(path: Path) -> dict[str, tuple[tuple[int, int], ...]]:
    """Load and merge a BED as 1-based closed intervals by normalized contig."""
    if not path.is_file():
        raise ValueError(f"configured coding+splice BED was not found: {path}")
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
        raise ValueError(f"configured coding+splice BED contains no intervals: {path}")
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


def _position_overlaps(
    chrom: str,
    pos: int,
    intervals: dict[str, tuple[tuple[int, int], ...]],
) -> bool:
    values = intervals.get(chrom, ())
    low = 0
    high = len(values)
    while low < high:
        middle = (low + high) // 2
        if values[middle][0] <= pos:
            low = middle + 1
        else:
            high = middle
    return low > 0 and pos <= values[low - 1][1]


def _position_selected(
    chrom: str,
    pos: int,
    intervals: dict[str, tuple[tuple[int, int], ...]],
) -> bool:
    return not intervals or _position_overlaps(chrom, pos, intervals)


def record_passes(
    line: str,
    header: VcfHeader,
    options: WgsPrefilterOptions,
    intervals: dict[str, tuple[tuple[int, int], ...]],
    exome_intervals: dict[str, tuple[tuple[int, int], ...]] | None = None,
) -> bool:
    columns = line.rstrip("\r\n").split("\t")
    if len(columns) < 8:
        raise ValueError("encountered a structurally invalid VCF record")
    if columns[6] != "PASS":
        return False
    chrom = normalize_chromosome(columns[0])
    try:
        pos = int(columns[1])
    except ValueError as exc:
        raise ValueError(f"invalid VCF position: {columns[1]}") from exc
    # Whole-genome review must never lose the conventional diagnostic exome.
    # PASS variants in the configured coding+splice BED bypass every optional
    # frequency, evidence, and custom-gene prefilter below.
    if exome_intervals and _position_overlaps(chrom, pos, exome_intervals):
        return True
    if not _position_selected(chrom, pos, intervals):
        return False

    info = info_map(columns[7])
    consequences = parse_csq_entries(info.get("CSQ", ""), list(header.csq_fields))
    alts = columns[4].split(",")
    for alt_index, alt in enumerate(alts):
        matched: list[dict[str, str]] = []
        for consequence in consequences:
            allele_number = consequence.get("ALLELE_NUM", "")
            if allele_number.isdigit():
                if int(allele_number) == alt_index + 1:
                    matched.append(consequence)
            elif not consequence.get("Allele") or consequence.get("Allele") == alt:
                matched.append(consequence)
        if not matched:
            matched = consequences
        records = [info, *matched]

        frequencies = _numbers(records, AF_FIELDS)
        if (
            options.max_gnomad_popmax is not None
            and frequencies
            and max(frequencies) >= options.max_gnomad_popmax
        ):
            continue

        evidence: list[bool] = []
        for threshold, fields, absolute in (
            (options.min_spliceai, SPLICEAI_FIELDS, False),
            (options.min_promoterai_abs, PROMOTERAI_FIELDS, True),
            (options.min_cadd, CADD_FIELDS, False),
        ):
            if threshold is None:
                continue
            values = _numbers(records, fields)
            # Conservative missing-data policy: an enabled evidence source
            # never removes an allele when that source has no value for it.
            evidence.append(
                not values
                or max(abs(value) if absolute else value for value in values)
                >= threshold
            )
        if not evidence or any(evidence):
            return True
    return False


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
    intervals: dict[str, tuple[tuple[int, int], ...]],
    exome_intervals: dict[str, tuple[tuple[int, int], ...]],
    destination: Path,
) -> dict:
    scanned = 0
    retained = 0
    annotations_scanned = 0
    annotations_retained = 0
    with destination.open("wt", encoding="utf-8") as output:
        for line in backend.iter_records(source, contigs):
            scanned += 1
            if record_passes(
                line, header, options, intervals, exome_intervals
            ):
                compacted, input_count, output_count = compact_review_transcripts(
                    line, header
                )
                output.write(compacted)
                retained += 1
                annotations_scanned += input_count
                annotations_retained += output_count
    return {
        "path": str(destination),
        "records_scanned": scanned,
        "records_retained": retained,
        "annotations_scanned": annotations_scanned,
        "annotations_retained": annotations_retained,
    }


class WgsReviewStore:
    """Prepare, shard, conservatively filter, and cache indexed review VCFs."""

    def __init__(self, state_dir: Path, cohort: CohortStore):
        self.state_dir = state_dir
        self.cohort = cohort
        self.cache_dir = state_dir / "wgs-review-cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def prefilter(
        self,
        source: Path,
        options: WgsPrefilterOptions,
        gtf_path: Path,
        exome_bed_path: Path | None = None,
        progress: Callable[[dict], None] | None = None,
    ) -> dict:
        source = source.resolve()
        exome_bed_path = exome_bed_path.resolve() if exome_bed_path else None
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
        contigs = backend.list_contigs(prepared.path)
        if not contigs:
            raise ValueError("the indexed WGS VCF contains no queryable contigs")
        intervals, missing_genes = gene_intervals(
            gtf_path, options.genes, options.gene_window_bp
        )
        exome_intervals = (
            bed_intervals(exome_bed_path) if exome_bed_path else {}
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
        gtf_stat = gtf_path.stat() if options.genes else None
        fingerprint_value = {
            "review_format_version": 2,
            "source": str(source),
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
            "filters": asdict(options),
            "gtf": (
                [str(gtf_path), gtf_stat.st_size, gtf_stat.st_mtime_ns]
                if gtf_stat else None
            ),
            "exome_bed": (
                [
                    str(exome_bed_path),
                    exome_bed_path.stat().st_size,
                    exome_bed_path.stat().st_mtime_ns,
                ]
                if exome_bed_path else None
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

            def collect(executor) -> None:
                futures = {
                    executor.submit(
                        _filter_group_worker,
                        backend,
                        prepared.path,
                        (contig,),
                        header,
                        options,
                        ({contig: intervals[contig]} if contig in intervals else {}),
                        (
                            {contig: exome_intervals[contig]}
                            if contig in exome_intervals else {}
                        ),
                        temporary_root / f"shard-{index:04d}.vcf",
                    ): index
                    for index, contig in enumerate(contigs)
                }
                for future in as_completed(futures):
                    results.append(future.result())
                    if progress:
                        completed = len(results)
                        progress({
                            "phase": "filtering",
                            "progress": 10.0 + (70.0 * completed / len(contigs)),
                            "message": (
                                f"Filtered {completed} of {len(contigs)} "
                                "chromosome shards."
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
                    if line.startswith("#CHROM\t"):
                        destination.write(_filter_header(
                            options, bool(exome_bed_path)
                        ))
                    destination.write(line)
                for result in sorted(results, key=lambda item: item["path"]):
                    chunk = Path(result["path"])
                    with chunk.open("rt", encoding="utf-8") as source_handle:
                        for line in source_handle:
                            destination.write(line)
            if progress:
                progress({
                    "phase": "compressing",
                    "progress": 90.0,
                    "message": "Sorting and BGZF-compressing retained variants…",
                    "reader_count": reader_count,
                })
            backend.sort_bgzip(merged, output)
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
            "missing_genes": list(missing_genes),
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
