#!/usr/bin/env python3
"""Local SCREEN cCRE overlap and nearby Ensembl gene-TSS context."""

from __future__ import annotations

import bisect
import gzip
import re
import threading
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from local_service.cohort_store import HtsBackend, normalize_chromosome


PRIMARY_CONTIGS = {str(value) for value in range(1, 23)} | {"X", "Y", "MT"}
CCRE_CLASS_LABELS = {
    "PLS": "promoter-like signature",
    "pELS": "proximal enhancer-like signature",
    "dELS": "distal enhancer-like signature",
    "CA-H3K4me3": "chromatin accessibility plus H3K4me3",
    "CA-CTCF": "chromatin accessibility plus CTCF",
    "CA-TF": "chromatin accessibility plus transcription-factor signal",
    "CA": "chromatin accessibility",
    "TF": "transcription-factor signal",
}


@dataclass(frozen=True)
class CcreRecord:
    chrom: str
    start: int
    end: int
    accession: str
    ccre_class: str


@dataclass(frozen=True)
class GeneTss:
    chrom: str
    tss: int
    symbol: str
    gene_id: str
    strand: str
    biotype: str


def _open_text(path: Path):
    return (
        gzip.open(path, "rt", encoding="utf-8", errors="replace")
        if path.name.lower().endswith((".gz", ".bgz"))
        else path.open("rt", encoding="utf-8", errors="replace")
    )


def _parse_ccre_lines(lines: Iterable[str], chrom: str) -> list[CcreRecord]:
    records: list[CcreRecord] = []
    for line in lines:
        if not line.strip() or line.startswith("#"):
            continue
        columns = line.rstrip("\r\n").split("\t")
        if len(columns) < 5 or normalize_chromosome(columns[0]) != chrom:
            continue
        try:
            start = int(columns[1]) + 1
            end = int(columns[2])
        except ValueError:
            continue
        if end < start:
            continue
        records.append(CcreRecord(chrom, start, end, columns[3], columns[4]))
    return records


def _signed_distance_to_interval(gene: GeneTss, start: int, end: int) -> int:
    if start <= gene.tss <= end:
        return 0
    genomic = start - gene.tss if gene.tss < start else end - gene.tss
    return genomic if gene.strand == "+" else -genomic


class CcreContextStore:
    """Thread-safe lazy indexes for per-variant local regulatory context."""

    def __init__(self, hts_backend: HtsBackend | None = None):
        self.hts_backend = hts_backend
        self._lock = threading.RLock()
        self._gene_key: tuple[str, int, int] | None = None
        self._genes: dict[str, tuple[GeneTss, ...]] = {}
        self._gene_positions: dict[str, tuple[int, ...]] = {}
        self._fallback_ccre: OrderedDict[
            tuple[str, int, int, str], tuple[CcreRecord, ...]
        ] = OrderedDict()

    def _load_genes(self, path: Path) -> None:
        stat = path.stat()
        key = (str(path), stat.st_size, stat.st_mtime_ns)
        with self._lock:
            if self._gene_key == key:
                return
            genes: dict[str, list[GeneTss]] = {}
            header: dict[str, int] = {}
            with _open_text(path) as handle:
                for line in handle:
                    if not line.strip() or line.startswith("#"):
                        continue
                    columns = line.rstrip("\r\n").split("\t")
                    if not header:
                        header = {name: index for index, name in enumerate(columns)}
                        required = {
                            "chrom", "tss", "gene_symbol", "gene_id", "strand", "biotype",
                        }
                        if not required.issubset(header):
                            raise ValueError(
                                "gene TSS table lacks required columns: "
                                + ", ".join(sorted(required - set(header)))
                            )
                        continue
                    try:
                        chrom = normalize_chromosome(columns[header["chrom"]])
                        tss = int(columns[header["tss"]])
                        strand = columns[header["strand"]]
                    except (IndexError, ValueError):
                        continue
                    if chrom not in PRIMARY_CONTIGS or strand not in {"+", "-"}:
                        continue
                    genes.setdefault(chrom, []).append(GeneTss(
                        chrom=chrom,
                        tss=tss,
                        symbol=columns[header["gene_symbol"]],
                        gene_id=columns[header["gene_id"]],
                        strand=strand,
                        biotype=columns[header["biotype"]],
                    ))
            if not genes:
                raise ValueError(f"gene TSS table contains no records: {path}")
            self._genes = {
                chrom: tuple(sorted(values, key=lambda gene: (gene.tss, gene.gene_id)))
                for chrom, values in genes.items()
            }
            self._gene_positions = {
                chrom: tuple(gene.tss for gene in values)
                for chrom, values in self._genes.items()
            }
            self._gene_key = key

    def _fallback_contig(self, path: Path, chrom: str) -> tuple[CcreRecord, ...]:
        stat = path.stat()
        key = (str(path), stat.st_size, stat.st_mtime_ns, chrom)
        with self._lock:
            cached = self._fallback_ccre.get(key)
            if cached is not None:
                self._fallback_ccre.move_to_end(key)
                return cached
        with _open_text(path) as handle:
            records = tuple(_parse_ccre_lines(handle, chrom))
        with self._lock:
            self._fallback_ccre[key] = records
            self._fallback_ccre.move_to_end(key)
            while len(self._fallback_ccre) > 3:
                self._fallback_ccre.popitem(last=False)
        return records

    def _overlaps(
        self, path: Path, chrom: str, start: int, end: int,
    ) -> list[CcreRecord]:
        records: Iterable[CcreRecord] | None = None
        if self.hts_backend is not None and (
            Path(f"{path}.tbi").is_file() or Path(f"{path}.csi").is_file()
        ):
            try:
                lines = self.hts_backend.iter_records(
                    path, (f"{chrom}:{start}-{end}",)
                )
                records = _parse_ccre_lines(lines, chrom)
            except RuntimeError:
                records = None
        if records is None:
            records = self._fallback_contig(path, chrom)
        return [
            record for record in records
            if record.start <= end and record.end >= start
        ]

    def _nearby_genes(
        self, record: CcreRecord, window_bp: int,
    ) -> list[dict]:
        genes = self._genes.get(record.chrom, ())
        positions = self._gene_positions.get(record.chrom, ())
        low = bisect.bisect_left(positions, max(1, record.start - window_bp))
        high = bisect.bisect_right(positions, record.end + window_bp)
        result = []
        for gene in genes[low:high]:
            distance = _signed_distance_to_interval(gene, record.start, record.end)
            result.append({
                "symbol": gene.symbol,
                "gene_id": gene.gene_id,
                "tss": gene.tss,
                "strand": gene.strand,
                "biotype": gene.biotype,
                "distance_bp": distance,
                "absolute_distance_bp": abs(distance),
                "relative_position": (
                    "overlaps TSS" if distance == 0
                    else "downstream of TSS" if distance > 0
                    else "upstream of TSS"
                ),
            })
        return sorted(
            result,
            key=lambda value: (
                value["absolute_distance_bp"], value["symbol"], value["gene_id"],
            ),
        )

    def query(
        self,
        *,
        chrom: str,
        pos: int,
        ref: str,
        alt: str,
        ccre_path: Path | None,
        gene_tss_path: Path | None,
        resource_version: str,
        gene_source: str,
        window_bp: int = 500_000,
    ) -> dict:
        normalized_chrom = normalize_chromosome(chrom)
        if normalized_chrom not in PRIMARY_CONTIGS:
            raise ValueError("cCRE context supports GRCh38 primary contigs only")
        if not isinstance(pos, int) or pos < 1:
            raise ValueError("variant position must be a positive integer")
        if not re.fullmatch(r"[ACGTNacgtn]+", ref or ""):
            raise ValueError("variant REF must be a sequence-resolved allele")
        if not alt:
            raise ValueError("variant ALT is required")
        variant_end = pos + max(1, len(ref)) - 1
        base = {
            "assembly": "GRCh38",
            "resource_version": resource_version,
            "gene_source": gene_source,
            "gene_window_bp": window_bp,
            "variant": {
                "chrom": normalized_chrom, "start": pos, "end": variant_end,
                "ref": ref, "alt": alt,
            },
            "interpretation_caveat": (
                "The VEP or nearest gene shown elsewhere is not necessarily regulated "
                "by an overlapping cCRE. Genes in the +/-500 kb table are proximity "
                "context only, not predicted cCRE targets."
            ),
        }
        if ccre_path is None or not ccre_path.is_file():
            return {
                **base, "resource_available": False,
                "gene_resource_available": bool(gene_tss_path and gene_tss_path.is_file()),
                "status": "resource_unavailable", "overlaps": [],
            }
        if gene_tss_path is not None and gene_tss_path.is_file():
            self._load_genes(gene_tss_path)
        else:
            with self._lock:
                self._gene_key = None
                self._genes = {}
                self._gene_positions = {}
        overlaps = self._overlaps(
            ccre_path, normalized_chrom, pos, variant_end
        )
        return {
            **base,
            "resource_available": True,
            "gene_resource_available": bool(self._genes),
            "status": "overlap" if overlaps else "no_overlap",
            "overlaps": [
                {
                    "accession": record.accession,
                    "class": record.ccre_class,
                    "class_label": CCRE_CLASS_LABELS.get(
                        record.ccre_class, record.ccre_class
                    ),
                    "chrom": record.chrom,
                    "start": record.start,
                    "end": record.end,
                    "length_bp": record.end - record.start + 1,
                    "nearby_genes": (
                        self._nearby_genes(record, window_bp) if self._genes else []
                    ),
                }
                for record in overlaps
            ],
        }
