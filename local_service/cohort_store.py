#!/usr/bin/env python3
"""Persistent genotype-first cohort index for VEP-annotated VCF files.

The index is intentionally local and dependency-free. VCFs are parsed once
into SQLite, after which exact-variant and qualifying gene queries do not need
to reopen hundreds of source files.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import multiprocessing
import os
import re
import shutil
import sqlite3
import time
import subprocess
import tempfile
import threading
import uuid
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Iterator
from urllib.parse import unquote

from pipeline.vcf_assembly import detect_vcf_assembly


EMPTY = {"", ".", "-"}
IMPACT_ORDER = {"HIGH": 1, "MODERATE": 2, "LOW": 3, "MODIFIER": 4, "UNKNOWN": 5}
ALLOWED_IMPACTS = set(IMPACT_ORDER)
DEFAULT_INDEX_READERS = 4
DEFAULT_STAGE_BATCH_RECORDS = 2_000
MAX_BROWSER_SAMPLE_REVIEW_CARRIERS = 200_000


@dataclass(frozen=True)
class VcfHeader:
    samples: tuple[str, ...]
    csq_fields: tuple[str, ...]
    contigs: tuple[str, ...]
    info_fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class PreparedVcf:
    source_path: Path
    path: Path
    index_path: Path | None
    normalized: bool
    cache_hit: bool
    warning: str = ""


class HtsBackend:
    """Run bcftools/tabix natively or through the existing HTS container."""

    def __init__(
        self,
        *,
        native_tools: dict[str, str] | None = None,
        runtime: str | None = None,
        image: str = "vep-annotate:latest",
    ):
        self.native_tools = native_tools or {}
        self.runtime = runtime
        self.image = image

    @classmethod
    def discover(cls) -> "HtsBackend | None":
        native = {
            tool: location
            for tool in ("bcftools", "tabix")
            if (location := shutil.which(tool))
        }
        if len(native) == 2:
            return cls(native_tools=native)
        requested = os.environ.get("IEI_COHORT_HTS_RUNTIME") or os.environ.get(
            "RUNTIME", "docker"
        )
        if requested not in {"docker", "podman"} or not shutil.which(requested):
            return None
        return cls(
            runtime=requested,
            image=os.environ.get("IEI_COHORT_HTS_IMAGE")
            or os.environ.get("IMAGE", "vep-annotate:latest"),
        )

    def _command(self, tool: str, arguments: list[str]) -> list[str]:
        if tool in self.native_tools:
            return [self.native_tools[tool], *arguments]
        if not self.runtime:
            raise RuntimeError(f"{tool} is unavailable")

        hosts: list[Path] = []
        mapped_arguments = list(arguments)
        for index, argument in enumerate(mapped_arguments):
            candidate = Path(argument)
            if not candidate.is_absolute():
                continue
            host_dir = candidate if candidate.is_dir() else candidate.parent
            host_dir = host_dir.resolve()
            try:
                mount_index = hosts.index(host_dir)
            except ValueError:
                hosts.append(host_dir)
                mount_index = len(hosts) - 1
            mapped = Path(f"/hts_{mount_index + 1}")
            if not candidate.is_dir():
                mapped /= candidate.name
            mapped_arguments[index] = str(mapped)

        command = [self.runtime, "run", "--rm"]
        for index, host in enumerate(hosts):
            command.extend(["-v", f"{host}:/hts_{index + 1}:rw"])
        command.extend(["--entrypoint", tool, self.image, *mapped_arguments])
        return command

    def run(self, tool: str, arguments: list[str]) -> subprocess.CompletedProcess[str]:
        attempts = 0
        while True:
            attempts += 1
            process = subprocess.run(
                self._command(tool, arguments),
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            if not process.returncode:
                return process
            # A file written by the host (or another container) moments ago can
            # be incompletely visible inside a freshly started container
            # (Docker Desktop VirtioFS bind caching; worse on FSKit-exFAT
            # drives). That truncated view fails bcftools/tabix on perfectly
            # valid files — observed as "Broken VCF record" from bcftools sort
            # and tbx_index_build failures. Settle and retry once when running
            # through a container; genuine failures fail identically again.
            if attempts == 1 and self.runtime and not self.native_tools.get(tool):
                time.sleep(5)
                continue
            detail = process.stderr.strip() or process.stdout.strip()
            raise RuntimeError(detail or f"{tool} exited with {process.returncode}")

    def validate_index(self, path: Path) -> Path | None:
        candidates = [Path(f"{path}.tbi"), Path(f"{path}.csi")]
        index = next((item for item in candidates if item.is_file()), None)
        if not index or index.stat().st_mtime_ns < path.stat().st_mtime_ns:
            return None
        try:
            self.run("tabix", ["-l", str(path)])
        except RuntimeError:
            return None
        return index

    def list_contigs(self, path: Path) -> list[str]:
        return [
            line.strip()
            for line in self.run("tabix", ["-l", str(path)]).stdout.splitlines()
            if line.strip()
        ]

    def iter_records(self, path: Path, contigs: Iterable[str]) -> Iterator[str]:
        regions = list(contigs)
        if not regions:
            return
        command = self._command("tabix", [str(path), *regions])
        process = subprocess.Popen(
            command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert process.stdout is not None
        # Drain stderr on a thread: tabix warns once per unknown contig, and a
        # region list with many of them overflows the ~64 KiB pipe buffer.
        # Reading stderr only after stdout is exhausted then deadlocks — tabix
        # blocks writing stderr while this process blocks reading stdout.
        stderr_chunks: list[str] = []
        stderr_thread = None
        if process.stderr is not None:
            stderr_thread = threading.Thread(
                target=lambda: stderr_chunks.append(process.stderr.read()),
                daemon=True,
            )
            stderr_thread.start()
        try:
            yield from process.stdout
            returncode = process.wait()
            if stderr_thread is not None:
                stderr_thread.join(timeout=10)
            if returncode:
                stderr = "".join(stderr_chunks)
                raise RuntimeError(
                    stderr.strip() or f"tabix exited with {returncode}"
                )
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait()
            process.stdout.close()
            if stderr_thread is not None and stderr_thread.is_alive():
                stderr_thread.join(timeout=5)
            if process.stderr:
                process.stderr.close()

    def sort_bgzip(self, source: Path, output: Path) -> None:
        self.run(
            "bcftools",
            ["sort", "-O", "z", "-o", str(output), str(source)],
        )

    def create_index(self, path: Path) -> Path:
        for suffix in (".tbi", ".csi"):
            candidate = Path(f"{path}{suffix}")
            if candidate.exists():
                candidate.unlink()
        try:
            self.run("tabix", ["-f", "-p", "vcf", str(path)])
        except RuntimeError as tabix_error:
            try:
                self.run("bcftools", ["index", "-f", "-c", str(path)])
            except RuntimeError:
                raise tabix_error
        index = self.validate_index(path)
        if not index:
            raise RuntimeError("the generated tabix/CSI index could not be validated")
        return index


def is_bgzf(path: Path) -> bool:
    """Return whether the first gzip member carries the BGZF BC extra field."""
    try:
        with path.open("rb") as handle:
            header = handle.read(12)
            if (
                len(header) < 12
                or header[:3] != b"\x1f\x8b\x08"
                or not header[3] & 0x04
            ):
                return False
            extra = handle.read(int.from_bytes(header[10:12], "little"))
    except OSError:
        return False
    cursor = 0
    while cursor + 4 <= len(extra):
        length = int.from_bytes(extra[cursor + 2:cursor + 4], "little")
        if extra[cursor:cursor + 2] == b"BC" and length == 2:
            return cursor + 6 <= len(extra)
        cursor += 4 + length
    return False


def read_vcf_header(path: Path) -> VcfHeader:
    opener = gzip.open if path.name.lower().endswith((".gz", ".bgz")) else open
    samples: tuple[str, ...] = ()
    csq_fields: tuple[str, ...] = ()
    contigs: list[str] = []
    info_fields: list[str] = []
    saw_fileformat = False
    with opener(path, "rt", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if line.startswith("##fileformat=VCF"):
                saw_fileformat = True
            elif line.startswith("##contig=<"):
                identifier = re.search(r"(?:^|[,<])ID=([^,>]+)", line)
                if identifier:
                    contigs.append(identifier.group(1))
                values = dict(
                    item.split("=", 1)
                    for item in line.split("<", 1)[1].rsplit(">", 1)[0].split(",")
                    if "=" in item
                )
                if (
                    normalize_chromosome(values.get("ID", "")) == "1"
                    and values.get("length")
                    and values["length"] != "248956422"
                ):
                    raise ValueError(
                        "VCF chromosome 1 length does not match GRCh38 "
                        f"(expected 248956422, found {values['length']})"
                    )
            elif line.startswith("##INFO=<ID=CSQ"):
                info_fields.append("CSQ")
                match = re.search(r"Format:\s*([^\">]+)", line, re.IGNORECASE)
                if match:
                    csq_fields = tuple(match.group(1).strip().split("|"))
            elif line.startswith("##INFO=<"):
                identifier = re.search(r"(?:^|[,<])ID=([^,>]+)", line)
                if identifier:
                    info_fields.append(identifier.group(1))
            elif line.startswith("#CHROM"):
                columns = line.rstrip("\r\n").split("\t")
                samples = tuple(columns[9:])
                break
            elif not line.startswith("#"):
                break
    if not saw_fileformat:
        raise ValueError("file does not begin with a VCF fileformat header")
    if not samples:
        raise ValueError("cohort VCF must contain at least one sample")
    if len(samples) != len(set(samples)) or any(not sample for sample in samples):
        raise ValueError("cohort VCF contains duplicate or empty sample names")
    if not csq_fields:
        raise ValueError("VEP CSQ Format header was not found")
    return VcfHeader(
        samples=samples,
        csq_fields=csq_fields,
        contigs=tuple(contigs),
        info_fields=tuple(info_fields),
    )


def read_vcf_header_lines(path: Path) -> list[str]:
    """Read the complete VCF header without decompressing the variant body."""
    opener = gzip.open if path.name.lower().endswith((".gz", ".bgz")) else open
    lines: list[str] = []
    with opener(path, "rt", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if not line.startswith("#"):
                break
            lines.append(line.rstrip("\r\n"))
            if line.startswith("#CHROM\t"):
                return lines
    raise ValueError(f"VCF header is incomplete: {path}")


def utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_chromosome(value: str) -> str:
    chrom = value.strip()
    if chrom.lower().startswith("chr"):
        chrom = chrom[3:]
    return "MT" if chrom.upper() in {"M", "MT"} else chrom.upper()


def variant_key(chrom: str, pos: int, ref: str, alt: str) -> str:
    return f"{normalize_chromosome(chrom)}:{pos}:{ref.upper()}:{alt.upper()}"


def decode(value: str | None) -> str:
    return unquote((value or "").replace("+", " "))


def parse_number(value: str | None) -> float | None:
    if not value or value in EMPTY:
        return None
    try:
        parsed = float(value.split("&", 1)[0])
    except ValueError:
        return None
    return parsed


def info_map(raw: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in raw.split(";"):
        key, separator, value = item.partition("=")
        if key:
            result[key] = value if separator else "1"
    return result


def first(record: dict[str, str], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = record.get(key, "")
        if value not in EMPTY:
            return decode(value)
    return ""


def allele_info_value(
    record: dict[str, str], key: str, alt_index: int,
) -> str:
    """Return one Number=A INFO value, treating dot as not applicable."""
    values = record.get(key, "").split(",")
    value = values[alt_index] if alt_index < len(values) else ""
    return "" if value in EMPTY else decode(value)


def maximum(record: dict[str, str], keys: tuple[str, ...]) -> float | None:
    values: list[float] = []
    for key in keys:
        for item in record.get(key, "").replace("&", ",").split(","):
            parsed = parse_number(item)
            if parsed is not None:
                values.append(parsed)
    return max(values) if values else None


def preferred_maximum(
    record: dict[str, str], key_groups: tuple[tuple[str, ...], ...]
) -> float | None:
    """Use the first source group with a value, maximizing only within it."""
    for keys in key_groups:
        value = maximum(record, keys)
        if value is not None:
            return value
    return None


def truthy(value: str) -> bool:
    return value not in EMPTY and value != "0"

def haplotype_frame_evidence(
    raw: str, current_variant: str, sample: str
) -> dict[str, str]:
    matches = []
    for entry in (raw or "").split(","):
        fields = [decode(value) for value in entry.split("|")]
        if len(fields) < 6:
            continue
        variant, event_sample, transcript, status, partners, protein = fields[:6]
        if variant != current_variant or event_sample != sample:
            continue
        if status not in {
            "FRAME_RESTORED_CONFIRMED",
            "FRAME_RESTORATION_PARTIAL_CONFIRMED",
            "FRAME_RESTORING_POSSIBLE_UNPHASED",
        }:
            continue
        matches.append({
            "status": status,
            "partners": partners,
            "protein": protein,
            "transcript": transcript,
        })
    matches.sort(
        key=lambda value: value["status"] != "FRAME_RESTORED_CONFIRMED"
    )
    return matches[0] if matches else {
        "status": "", "partners": "", "protein": "", "transcript": ""
    }


def parse_genotype(format_value: str, sample_value: str, alt_index: int) -> dict:
    keys = format_value.split(":")
    values = sample_value.split(":")
    fields = dict(zip(keys, values))
    gt = fields.get("GT") or "./."
    allele_strings = re.split(r"[|/]", gt)
    called = [int(item) for item in allele_strings if item.isdigit()]
    allele_number = alt_index + 1
    copies = called.count(allele_number)
    if not copies:
        return {"carrier": False}

    if len(allele_strings) == 1:
        zygosity = "hemizygous"
    elif len(called) < len(allele_strings):
        # A partial no-call such as ./1: the sample demonstrably carries the
        # allele, but the unresolved second allele makes this neither a
        # confident single-copy (hemizygous) nor a heterozygous call.
        zygosity = "half_called"
    elif copies == len(called):
        zygosity = "homozygous"
    elif copies == 1:
        zygosity = "heterozygous"
    else:
        zygosity = "non_reference"

    ad: list[int | None] = []
    for item in (fields.get("AD") or "").split(","):
        try:
            ad.append(int(item))
        except ValueError:
            # "." means per-allele depth was not reported — not zero reads.
            ad.append(None)
    total_depth = sum(value for value in ad if value is not None)
    allele_depth = ad[allele_number] if allele_number < len(ad) else None
    dp = parse_number(fields.get("DP"))
    gq = parse_number(fields.get("GQ"))
    return {
        "carrier": True,
        "gt": gt,
        "zygosity": zygosity,
        "phased": int("|" in gt),
        "dp": int(dp) if dp is not None else None,
        "gq": gq,
        "allele_balance": (
            allele_depth / total_depth
            if allele_depth is not None and total_depth > 0
            else None
        ),
    }


def parse_csq_entries(raw: str, fields: list[str]) -> list[dict[str, str]]:
    if not raw or not fields:
        return [{}]
    entries = []
    for item in raw.split(","):
        values = item.split("|")
        entries.append({
            field: decode(values[index]) if index < len(values) else ""
            for index, field in enumerate(fields)
        })
    return entries


def annotation_from(record: dict[str, str]) -> dict:
    impact = (first(record, ("IMPACT",)) or "UNKNOWN").upper()
    if impact not in ALLOWED_IMPACTS:
        impact = "UNKNOWN"
    return {
        "gene": (first(record, ("SYMBOL", "HGNC")) or "—").upper(),
        "gene_id": first(record, ("Gene",)),
        "transcript": first(record, ("Feature",)),
        "hgvsc": first(record, ("HGVSc",)),
        "hgvsp": first(record, ("HGVSp",)),
        "consequence": first(record, ("Consequence",)) or "unannotated",
        "impact": impact,
        "gnomad_popmax": maximum(record, (
            "gnomADg_AF_popmax", "gnomADe_AF_popmax", "gnomAD_AF_popmax",
            "gnomAD_popmax_AF", "MAX_AF", "gnomADg_AF", "gnomADe_AF", "gnomAD_AF",
        )),
        # When both sources exist on a coding SNV, the explicitly selected
        # genome-wide v1.7 plugin is authoritative. dbNSFP is the fallback for
        # exome jobs or records without a plugin value; do not combine sources
        # by taking the numerically larger score.
        "cadd": preferred_maximum(record, (
            ("CADD_PHRED", "CADD_WGS_CADD_PHRED", "CADD_WGS_PHRED"),
            ("CADD_phred",),
        )),
        "alpha_missense": maximum(record, ("AlphaMissense_score", "am_pathogenicity")),
        "spliceai": maximum(record, (
            "SpliceAI_pred_DS_AG", "SpliceAI_pred_DS_AL",
            "SpliceAI_pred_DS_DG", "SpliceAI_pred_DS_DL",
            "DS_AG", "DS_AL", "DS_DG", "DS_DL",
        )),
        "promoterai": parse_number(first(record, (
            "PromoterAI_score", "promoterAI_score", "promoterAI", "PROMOTERAI", "promoterAI_promoterAI",
        ))),
        "logofunc_prediction": first(record, ("LoGoFunc_prediction",)),
        "logofunc_neutral": parse_number(first(record, ("LoGoFunc_neutral",))),
        "logofunc_gof": parse_number(first(record, ("LoGoFunc_GOF",))),
        "logofunc_lof": parse_number(first(record, ("LoGoFunc_LOF",))),
        "logofunc_allele_available": int(truthy(first(record, ("LoGoFunc_allele_available",)))),
        "logofunc_source_transcript": first(record, ("LoGoFunc_source_transcript",)),
        "logofunc_source_hgvsp": first(record, ("LoGoFunc_source_HGVSp",)),
        "logofunc_match": first(record, ("LoGoFunc_match",)),
        "clinvar": first(record, ("ClinVar_CLNSIG", "CLNSIG")),
        "clinvar_conflicting": first(
            record, ("ClinVar_CLNSIGCONF", "CLNSIGCONF")
        ),
        "loftee": first(record, ("LoF", "LOFTEE")),
        "loftee_50bp": first(record, ("LoF_50_BP_RULE_PTC", "50_BP_RULE_recomputed")),
        "loftee_50bp_original": first(record, ("LoF_50_BP_RULE_original", "50_BP_RULE_original")),
        "loftee_50bp_changed": int(truthy(first(record, ("LoF_50_BP_RULE_changed", "50_BP_RULE_changed")))),
        "ptc_distance": parse_number(first(record, ("PTC_dist_from_last_exon",))),
        "ptc_calc_status": first(record, ("PTC_calc_status",)),
        "mane": int(truthy(first(record, ("MANE_SELECT", "MANE_PLUS_CLINICAL")))),
        "picked": int(truthy(first(record, ("PICK",)))),
        "repeat_masker": int(truthy(first(record, ("RepeatMasker", "REPEATMASKER")))),
        "segdup": int(truthy(first(record, ("SegDup", "SEGDUP")))),
    }


STAGE_VARIANT_COLUMNS = (
    "variant_key", "chrom", "pos", "ref", "alt", "rsid",
    "original_assembly", "original_chrom", "original_pos",
    "original_ref", "original_alt", "unscored_indel_reasons",
)
STAGE_ANNOTATION_COLUMNS = (
    "variant_key", "gene", "gene_id", "transcript", "hgvsc", "hgvsp",
    "consequence", "impact", "gnomad_popmax", "cadd", "alpha_missense",
    "spliceai", "promoterai", "logofunc_prediction", "logofunc_neutral",
    "logofunc_gof", "logofunc_lof", "logofunc_allele_available",
    "logofunc_source_transcript", "logofunc_source_hgvsp", "logofunc_match",
    "clinvar", "clinvar_conflicting", "loftee", "loftee_50bp",
    "loftee_50bp_original", "loftee_50bp_changed", "ptc_distance",
    "ptc_calc_status", "mane", "picked", "repeat_masker", "segdup",
)
STAGE_GENOTYPE_COLUMNS = (
    "variant_key", "sample_name", "genotype", "zygosity", "phased",
    "dp", "gq", "allele_balance", "qual", "haplotype_frame_status",
    "haplotype_frame_partners", "haplotype_protein_change",
    "haplotype_transcript",
)


def _placeholders(columns: tuple[str, ...]) -> str:
    return ",".join("?" for _ in columns)


STAGE_SCHEMA = """
PRAGMA journal_mode=OFF;
PRAGMA synchronous=OFF;
PRAGMA temp_store=MEMORY;
PRAGMA cache_size=-32768;
CREATE TABLE stage_variants (
  variant_key TEXT PRIMARY KEY,
  chrom TEXT NOT NULL,
  pos INTEGER NOT NULL,
  ref TEXT NOT NULL,
  alt TEXT NOT NULL,
  rsid TEXT,
  original_assembly TEXT,
  original_chrom TEXT,
  original_pos INTEGER,
  original_ref TEXT,
  original_alt TEXT,
  unscored_indel_reasons TEXT
) WITHOUT ROWID;
CREATE TABLE stage_annotations (
  variant_key TEXT NOT NULL,
  gene TEXT NOT NULL,
  gene_id TEXT,
  transcript TEXT NOT NULL DEFAULT '',
  hgvsc TEXT NOT NULL DEFAULT '',
  hgvsp TEXT NOT NULL DEFAULT '',
  consequence TEXT NOT NULL,
  impact TEXT NOT NULL,
  gnomad_popmax REAL,
  cadd REAL,
  alpha_missense REAL,
  spliceai REAL,
  promoterai REAL,
  logofunc_prediction TEXT,
  logofunc_neutral REAL,
  logofunc_gof REAL,
  logofunc_lof REAL,
  logofunc_allele_available INTEGER NOT NULL DEFAULT 0,
  logofunc_source_transcript TEXT,
  logofunc_source_hgvsp TEXT,
  logofunc_match TEXT,
  clinvar TEXT,
  clinvar_conflicting TEXT,
  loftee TEXT,
  loftee_50bp TEXT,
  loftee_50bp_original TEXT,
  loftee_50bp_changed INTEGER NOT NULL DEFAULT 0,
  ptc_distance REAL,
  ptc_calc_status TEXT,
  mane INTEGER NOT NULL DEFAULT 0,
  picked INTEGER NOT NULL DEFAULT 0,
  repeat_masker INTEGER NOT NULL DEFAULT 0,
  segdup INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY(variant_key, gene, transcript, hgvsc, hgvsp, consequence)
) WITHOUT ROWID;
CREATE TABLE stage_genotypes (
  variant_key TEXT NOT NULL,
  sample_name TEXT NOT NULL,
  genotype TEXT NOT NULL,
  zygosity TEXT NOT NULL,
  phased INTEGER NOT NULL DEFAULT 0,
  dp INTEGER,
  gq REAL,
  allele_balance REAL,
  qual REAL,
  haplotype_frame_status TEXT,
  haplotype_frame_partners TEXT,
  haplotype_protein_change TEXT,
  haplotype_transcript TEXT,
  PRIMARY KEY(variant_key, sample_name)
) WITHOUT ROWID;
"""


def _stage_insert_sql(table: str, columns: tuple[str, ...]) -> str:
    return (
        f"INSERT OR REPLACE INTO {table} ({','.join(columns)}) "
        f"VALUES ({_placeholders(columns)})"
    )


STAGE_VARIANT_INSERT = _stage_insert_sql("stage_variants", STAGE_VARIANT_COLUMNS)
STAGE_ANNOTATION_INSERT = _stage_insert_sql(
    "stage_annotations", STAGE_ANNOTATION_COLUMNS
)
STAGE_GENOTYPE_INSERT = _stage_insert_sql(
    "stage_genotypes", STAGE_GENOTYPE_COLUMNS
)

COHORT_SECONDARY_INDEXES = {
    "cohort_variants_locus_idx": (
        "CREATE INDEX cohort_variants_locus_idx "
        "ON cohort_variants(chrom, pos, ref, alt)"
    ),
    "cohort_variants_rsid_idx": (
        "CREATE INDEX cohort_variants_rsid_idx ON cohort_variants(rsid)"
    ),
    "cohort_variants_rsid_nocase_idx": (
        "CREATE INDEX cohort_variants_rsid_nocase_idx "
        "ON cohort_variants(rsid COLLATE NOCASE)"
    ),
    "cohort_annotations_gene_idx": (
        "CREATE INDEX cohort_annotations_gene_idx "
        "ON cohort_annotations(gene, mane, impact)"
    ),
    "cohort_annotations_variant_idx": (
        "CREATE INDEX cohort_annotations_variant_idx "
        "ON cohort_annotations(variant_id)"
    ),
    "cohort_genotypes_variant_idx": (
        "CREATE INDEX cohort_genotypes_variant_idx ON cohort_genotypes(variant_id)"
    ),
    "cohort_genotypes_sample_idx": (
        "CREATE INDEX cohort_genotypes_sample_idx ON cohort_genotypes(sample_id)"
    ),
    "cohort_samples_name_idx": (
        "CREATE INDEX cohort_samples_name_idx ON cohort_samples(name)"
    ),
    "cohort_annotations_preferred_idx": (
        "CREATE INDEX cohort_annotations_preferred_idx "
        "ON cohort_annotations(gene, mane, picked, impact)"
    ),
}


def _stage_vcf_records(
    lines: Iterable[str],
    header: VcfHeader,
    stage_path: Path,
    *,
    progress: Callable[[dict], None] | None = None,
    processed_bytes: Callable[[], int] | None = None,
    batch_records: int = DEFAULT_STAGE_BATCH_RECORDS,
) -> dict:
    """Parse VCF records into one disposable, natural-keyed SQLite stage."""
    connection = sqlite3.connect(stage_path)
    connection.executescript(STAGE_SCHEMA)
    variant_rows: dict[str, tuple] = {}
    annotation_rows: dict[tuple, tuple] = {}
    genotype_rows: dict[tuple, tuple] = {}
    records_processed = 0
    pass_records = 0
    excluded_records = 0
    carrier_count = 0

    def flush() -> None:
        if variant_rows:
            connection.executemany(STAGE_VARIANT_INSERT, variant_rows.values())
        if annotation_rows:
            connection.executemany(
                STAGE_ANNOTATION_INSERT, annotation_rows.values()
            )
        if genotype_rows:
            connection.executemany(STAGE_GENOTYPE_INSERT, genotype_rows.values())
        connection.commit()
        variant_rows.clear()
        annotation_rows.clear()
        genotype_rows.clear()

    try:
        for line in lines:
            if not line.strip() or line.startswith("#"):
                continue
            records_processed += 1
            columns = line.rstrip("\r\n").split("\t")
            if len(columns) < 10:
                continue
            (
                chrom_raw, pos_raw, rsid, ref, alt_raw, qual_raw,
                filter_value, raw_info, format_value,
            ) = columns[:9]
            if filter_value not in ("PASS", "."):
                # "." = site filtering not applied upstream (VCFv4.x); only
                # explicit failure labels exclude a record.
                excluded_records += 1
                continue
            pass_records += 1
            chrom = normalize_chromosome(chrom_raw)
            try:
                pos = int(pos_raw)
            except ValueError:
                continue
            info = info_map(raw_info)
            consequences = parse_csq_entries(
                info.get("CSQ", ""), list(header.csq_fields)
            )
            sample_values = columns[9:]
            qual = parse_number(qual_raw)

            for alt_index, alt in enumerate(alt_raw.split(",")):
                carriers: list[tuple[str, dict]] = []
                for sample_index, sample_name in enumerate(header.samples):
                    genotype = parse_genotype(
                        format_value,
                        sample_values[sample_index]
                        if sample_index < len(sample_values) else "",
                        alt_index,
                    )
                    if genotype["carrier"]:
                        carriers.append((sample_name, genotype))
                if not carriers:
                    continue

                key = variant_key(chrom, pos, ref, alt)
                original_alts = info.get("IEI_ORIGINAL_ALT", "").split(",")
                original_pos = (
                    int(info["IEI_ORIGINAL_POS"])
                    if info.get("IEI_ORIGINAL_POS", "").isdigit()
                    else None
                )
                variant_rows[key] = (
                    key, chrom, pos, ref.upper(), alt.upper(),
                    None if rsid == "." else rsid,
                    first(info, ("IEI_ORIGINAL_ASSEMBLY",)) or None,
                    first(info, ("IEI_ORIGINAL_CHROM",)) or None,
                    original_pos,
                    first(info, ("IEI_ORIGINAL_REF",)) or None,
                    decode(
                        original_alts[alt_index]
                        if alt_index < len(original_alts)
                        else (original_alts[0] if original_alts else "")
                    ) or None,
                    allele_info_value(
                        info, "IEI_UNSCORED_INDEL", alt_index
                    ) or None,
                )

                matching = []
                for consequence in consequences:
                    allele_number = consequence.get("ALLELE_NUM", "")
                    if allele_number.isdigit():
                        if int(allele_number) == alt_index + 1:
                            matching.append(consequence)
                    elif (
                        not consequence.get("Allele")
                        or consequence.get("Allele") == alt
                    ):
                        matching.append(consequence)
                if not matching:
                    matching = consequences
                annotations = [
                    annotation_from({**info, **consequence})
                    for consequence in matching
                ]
                if "PICK" not in header.csq_fields:
                    annotations_by_gene: dict[str, list[dict]] = {}
                    for annotation in annotations:
                        annotations_by_gene.setdefault(
                            annotation["gene"], []
                        ).append(annotation)
                    for gene_annotations in annotations_by_gene.values():
                        if (
                            len(gene_annotations) == 1
                            and not gene_annotations[0]["mane"]
                        ):
                            gene_annotations[0]["picked"] = 1

                for annotation in annotations:
                    annotation_tuple = (
                        key,
                        *(annotation[column] for column in STAGE_ANNOTATION_COLUMNS[1:]),
                    )
                    annotation_key = (
                        key, annotation["gene"], annotation["transcript"],
                        annotation["hgvsc"], annotation["hgvsp"],
                        annotation["consequence"],
                    )
                    annotation_rows[annotation_key] = annotation_tuple

                for sample_name, genotype in carriers:
                    haplotype = haplotype_frame_evidence(
                        info.get("IEI_HAPLOTYPE_FRAME", ""),
                        f"{chrom_raw}:{pos}:{ref}:{alt}",
                        sample_name,
                    )
                    genotype_rows[(key, sample_name)] = (
                        key, sample_name, genotype["gt"], genotype["zygosity"],
                        genotype["phased"], genotype["dp"], genotype["gq"],
                        genotype["allele_balance"], qual, haplotype["status"],
                        haplotype["partners"], haplotype["protein"],
                        haplotype["transcript"],
                    )
                    carrier_count += 1

            if records_processed % batch_records == 0:
                flush()
            if progress and records_processed % 5_000 == 0:
                progress({
                    "processed_bytes": processed_bytes() if processed_bytes else 0,
                    "records_processed": records_processed,
                    "pass_records": pass_records,
                    "carrier_count": carrier_count,
                })
        flush()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    result = {
        "stage_path": str(stage_path),
        "records_processed": records_processed,
        "pass_records": pass_records,
        "excluded_records": excluded_records,
        "carrier_count": carrier_count,
    }
    if progress:
        progress({
            **result,
            "processed_bytes": processed_bytes() if processed_bytes else 0,
        })
    return result


def _tabix_stage_worker(
    backend: HtsBackend,
    path: Path,
    contigs: tuple[str, ...],
    header: VcfHeader,
    stage_path: Path,
    batch_records: int,
) -> dict:
    """Process-safe indexed reader; each worker owns its staging database."""
    return _stage_vcf_records(
        backend.iter_records(path, contigs),
        header,
        stage_path,
        batch_records=batch_records,
    )


class CohortStore:
    """SQLite-backed VCF carrier index."""

    def __init__(
        self,
        database_path: Path,
        *,
        enable_auto_index: bool = False,
        hts_backend: HtsBackend | None = None,
        index_readers: int | None = None,
        stage_batch_records: int = DEFAULT_STAGE_BATCH_RECORDS,
        workspace_dir: Path | None = None,
    ):
        self.database_path = database_path.resolve()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.workspace_dir = (workspace_dir or self.database_path.parent).resolve()
        self.workspace_dir.mkdir(parents=True, exist_ok=True)
        self.prepared_dir = self.workspace_dir / "cohort-vcf-cache"
        self.staging_dir = self.workspace_dir / "cohort-staging"
        self.prepared_dir.mkdir(parents=True, exist_ok=True)
        self.staging_dir.mkdir(parents=True, exist_ok=True)
        self.enable_auto_index = enable_auto_index
        self.hts_backend = (
            hts_backend
            if hts_backend is not None
            else (HtsBackend.discover() if enable_auto_index else None)
        )
        configured_readers = index_readers
        if configured_readers is None:
            try:
                configured_readers = int(
                    os.environ.get("IEI_COHORT_INDEX_READERS", DEFAULT_INDEX_READERS)
                )
            except ValueError:
                configured_readers = DEFAULT_INDEX_READERS
        self.index_readers = max(
            1, min(configured_readers, os.cpu_count() or configured_readers)
        )
        self.stage_batch_records = max(100, stage_batch_records)
        self._import_jobs: dict[str, dict] = {}
        self._import_jobs_lock = threading.Lock()
        self._maintenance_lock = threading.Lock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=60)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=60000")
        connection.execute("PRAGMA synchronous=NORMAL")
        connection.execute("PRAGMA cache_size=-65536")
        return connection

    @contextmanager
    def _session(self):
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._session() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS cohort_files (
                    id INTEGER PRIMARY KEY,
                    path TEXT NOT NULL UNIQUE,
                    size_bytes INTEGER NOT NULL,
                    mtime_ns INTEGER NOT NULL,
                    imported_at TEXT NOT NULL,
                    sample_count INTEGER NOT NULL DEFAULT 0,
                    pass_records INTEGER NOT NULL DEFAULT 0,
                    excluded_records INTEGER NOT NULL DEFAULT 0,
                    variant_count INTEGER NOT NULL DEFAULT 0,
                    carrier_count INTEGER NOT NULL DEFAULT 0
                    ,assembly TEXT NOT NULL DEFAULT 'GRCh38'
                    ,lifted_from_assembly TEXT
                    ,prepared_path TEXT
                    ,index_path TEXT
                    ,import_mode TEXT NOT NULL DEFAULT 'serial'
                    ,reader_count INTEGER NOT NULL DEFAULT 1
                    ,preparation_warning TEXT NOT NULL DEFAULT ''
                    ,import_profile TEXT NOT NULL DEFAULT 'full'
                    ,analysis_scope TEXT NOT NULL DEFAULT 'unknown'
                    ,prefilter_options TEXT NOT NULL DEFAULT '{}'
                    ,prefilter_records_scanned INTEGER NOT NULL DEFAULT 0
                    ,prefilter_records_retained INTEGER NOT NULL DEFAULT 0
                    ,profile_label TEXT NOT NULL DEFAULT ''
                    ,profile_hash TEXT NOT NULL DEFAULT ''
                    ,profile_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS cohort_samples (
                    id INTEGER PRIMARY KEY,
                    file_id INTEGER NOT NULL REFERENCES cohort_files(id) ON DELETE CASCADE,
                    name TEXT NOT NULL,
                    UNIQUE(file_id, name)
                );

                CREATE TABLE IF NOT EXISTS cohort_variants (
                    id INTEGER PRIMARY KEY,
                    variant_key TEXT NOT NULL UNIQUE,
                    chrom TEXT NOT NULL,
                    pos INTEGER NOT NULL,
                    ref TEXT NOT NULL,
                    alt TEXT NOT NULL,
                    rsid TEXT,
                    original_assembly TEXT,
                    original_chrom TEXT,
                    original_pos INTEGER,
                    original_ref TEXT,
                    original_alt TEXT,
                    unscored_indel_reasons TEXT
                );

                CREATE TABLE IF NOT EXISTS cohort_annotations (
                    id INTEGER PRIMARY KEY,
                    variant_id INTEGER NOT NULL REFERENCES cohort_variants(id) ON DELETE CASCADE,
                    gene TEXT NOT NULL,
                    gene_id TEXT,
                    transcript TEXT,
                    hgvsc TEXT,
                    hgvsp TEXT,
                    consequence TEXT NOT NULL,
                    impact TEXT NOT NULL,
                    gnomad_popmax REAL,
                    cadd REAL,
                    alpha_missense REAL,
                    spliceai REAL,
                    promoterai REAL,
                    logofunc_prediction TEXT,
                    logofunc_neutral REAL,
                    logofunc_gof REAL,
                    logofunc_lof REAL,
                    logofunc_allele_available INTEGER NOT NULL DEFAULT 0,
                    logofunc_source_transcript TEXT,
                    logofunc_source_hgvsp TEXT,
                    logofunc_match TEXT,
                    clinvar TEXT,
                    clinvar_conflicting TEXT,
                    loftee TEXT,
                    loftee_50bp TEXT,
                    loftee_50bp_original TEXT,
                    loftee_50bp_changed INTEGER NOT NULL DEFAULT 0,
                    ptc_distance REAL,
                    ptc_calc_status TEXT,
                    mane INTEGER NOT NULL DEFAULT 0,
                    picked INTEGER NOT NULL DEFAULT 0,
                    repeat_masker INTEGER NOT NULL DEFAULT 0,
                    segdup INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(variant_id, gene, transcript, hgvsc, hgvsp, consequence)
                );

                CREATE TABLE IF NOT EXISTS cohort_genotypes (
                    id INTEGER PRIMARY KEY,
                    variant_id INTEGER NOT NULL REFERENCES cohort_variants(id) ON DELETE CASCADE,
                    sample_id INTEGER NOT NULL REFERENCES cohort_samples(id) ON DELETE CASCADE,
                    genotype TEXT NOT NULL,
                    zygosity TEXT NOT NULL,
                    phased INTEGER NOT NULL DEFAULT 0,
                    dp INTEGER,
                    gq REAL,
                    allele_balance REAL,
                    qual REAL,
                    haplotype_frame_status TEXT,
                    haplotype_frame_partners TEXT,
                    haplotype_protein_change TEXT,
                    haplotype_transcript TEXT,
                    UNIQUE(variant_id, sample_id)
                );

                CREATE INDEX IF NOT EXISTS cohort_variants_locus_idx
                    ON cohort_variants(chrom, pos, ref, alt);
                CREATE INDEX IF NOT EXISTS cohort_variants_rsid_idx
                    ON cohort_variants(rsid);
                CREATE INDEX IF NOT EXISTS cohort_variants_rsid_nocase_idx
                    ON cohort_variants(rsid COLLATE NOCASE);
                CREATE INDEX IF NOT EXISTS cohort_annotations_gene_idx
                    ON cohort_annotations(gene, mane, impact);
                CREATE INDEX IF NOT EXISTS cohort_annotations_variant_idx
                    ON cohort_annotations(variant_id);
                CREATE INDEX IF NOT EXISTS cohort_genotypes_variant_idx
                    ON cohort_genotypes(variant_id);
                CREATE INDEX IF NOT EXISTS cohort_genotypes_sample_idx
                    ON cohort_genotypes(sample_id);
                CREATE INDEX IF NOT EXISTS cohort_samples_name_idx
                    ON cohort_samples(name);
                """
            )
            # In-place migration for cohort databases created before PICK was
            # retained in the transcript annotation model.
            annotation_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(cohort_annotations)"
                ).fetchall()
            }
            picked_column_added = "picked" not in annotation_columns
            if picked_column_added:
                connection.execute(
                    "ALTER TABLE cohort_annotations "
                    "ADD COLUMN picked INTEGER NOT NULL DEFAULT 0"
                )
            for column, declaration in (
                ("clinvar_conflicting", "TEXT"),
                ("loftee_50bp", "TEXT"),
                ("loftee_50bp_original", "TEXT"),
                ("loftee_50bp_changed", "INTEGER NOT NULL DEFAULT 0"),
                ("ptc_distance", "REAL"),
                ("ptc_calc_status", "TEXT"),
                ("promoterai", "REAL"),
                ("logofunc_prediction", "TEXT"),
                ("logofunc_neutral", "REAL"),
                ("logofunc_gof", "REAL"),
                ("logofunc_lof", "REAL"),
                ("logofunc_allele_available", "INTEGER NOT NULL DEFAULT 0"),
                ("logofunc_source_transcript", "TEXT"),
                ("logofunc_source_hgvsp", "TEXT"),
                ("logofunc_match", "TEXT"),
            ):
                if column not in annotation_columns:
                    connection.execute(
                        f"ALTER TABLE cohort_annotations ADD COLUMN {column} {declaration}"
                    )
            genotype_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(cohort_genotypes)"
                ).fetchall()
            }
            for column in (
                "haplotype_frame_status",
                "haplotype_frame_partners",
                "haplotype_protein_change",
                "haplotype_transcript",
            ):
                if column not in genotype_columns:
                    connection.execute(
                        f"ALTER TABLE cohort_genotypes ADD COLUMN {column} TEXT"
                    )
            file_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(cohort_files)"
                ).fetchall()
            }
            if "assembly" not in file_columns:
                connection.execute(
                    "ALTER TABLE cohort_files "
                    "ADD COLUMN assembly TEXT NOT NULL DEFAULT 'GRCh38'"
                )
            if "lifted_from_assembly" not in file_columns:
                connection.execute(
                    "ALTER TABLE cohort_files ADD COLUMN lifted_from_assembly TEXT"
                )
            for column, declaration in (
                ("prepared_path", "TEXT"),
                ("index_path", "TEXT"),
                ("import_mode", "TEXT NOT NULL DEFAULT 'serial'"),
                ("reader_count", "INTEGER NOT NULL DEFAULT 1"),
                ("preparation_warning", "TEXT NOT NULL DEFAULT ''"),
                ("import_profile", "TEXT NOT NULL DEFAULT 'full'"),
                ("analysis_scope", "TEXT NOT NULL DEFAULT 'unknown'"),
                ("prefilter_options", "TEXT NOT NULL DEFAULT '{}'"),
                ("prefilter_records_scanned", "INTEGER NOT NULL DEFAULT 0"),
                ("prefilter_records_retained", "INTEGER NOT NULL DEFAULT 0"),
                ("profile_label", "TEXT NOT NULL DEFAULT ''"),
                ("profile_hash", "TEXT NOT NULL DEFAULT ''"),
                ("profile_json", "TEXT NOT NULL DEFAULT '{}'"),
            ):
                if column not in file_columns:
                    connection.execute(
                        f"ALTER TABLE cohort_files ADD COLUMN {column} {declaration}"
                    )
            if "analysis_scope" not in file_columns:
                # One-shot backfill for databases created before the column
                # existed. Running this on every startup silently reverted any
                # deliberate operator re-scoping of a prefiltered file.
                connection.execute(
                    "UPDATE cohort_files SET analysis_scope = 'whole_genome' "
                    "WHERE import_profile = 'prefiltered' "
                    "AND analysis_scope != 'whole_genome'"
                )
            variant_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(cohort_variants)"
                ).fetchall()
            }
            for column, declaration in (
                ("original_assembly", "TEXT"),
                ("original_chrom", "TEXT"),
                ("original_pos", "INTEGER"),
                ("original_ref", "TEXT"),
                ("original_alt", "TEXT"),
                ("unscored_indel_reasons", "TEXT"),
            ):
                if column not in variant_columns:
                    connection.execute(
                        f"ALTER TABLE cohort_variants ADD COLUMN {column} {declaration}"
                    )
            if picked_column_added:
                # Legacy pipeline output used --pick and therefore had one CSQ
                # consequence but no PICK field. Recover that unambiguous
                # fallback once, as part of the PICK column migration. Running
                # this correlated update on every startup scans the complete
                # annotation table and is prohibitive for WGS databases.
                connection.execute(
                    """
                    UPDATE cohort_annotations AS candidate
                    SET picked = 1
                    WHERE candidate.picked = 0
                      AND candidate.mane = 0
                      AND NOT EXISTS (
                        SELECT 1 FROM cohort_annotations AS mane_row
                        WHERE mane_row.variant_id = candidate.variant_id
                          AND mane_row.gene = candidate.gene
                          AND mane_row.mane = 1
                      )
                      AND 1 = (
                        SELECT COUNT(*) FROM cohort_annotations AS sibling
                        WHERE sibling.variant_id = candidate.variant_id
                          AND sibling.gene = candidate.gene
                      )
                    """
                )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS cohort_annotations_preferred_idx
                ON cohort_annotations(gene, mane, picked, impact)
                """
            )

    def stats(self) -> dict:
        with self._session() as connection:
            row = connection.execute(
                """
                SELECT
                  (SELECT COUNT(*) FROM cohort_files) AS files,
                  (SELECT COUNT(*) FROM cohort_samples) AS sample_entries,
                  (SELECT COUNT(DISTINCT name) FROM cohort_samples) AS individuals,
                  (SELECT COUNT(*) FROM cohort_variants) AS variants,
                  (SELECT COUNT(*) FROM cohort_genotypes) AS carrier_observations,
                  (SELECT COUNT(*) FROM cohort_files
                   WHERE import_profile = 'full') AS full_files,
                  (SELECT COUNT(*) FROM cohort_files
                   WHERE import_profile = 'prefiltered') AS prefiltered_files
                """
            ).fetchone()
        return dict(row)

    def has_active_import(self) -> bool:
        with self._import_jobs_lock:
            return any(
                job["status"] in {"queued", "running"}
                for job in self._import_jobs.values()
            )

    def profiles(self) -> list[dict]:
        """Return import profiles without deriving cohort allele frequencies."""
        with self._session() as connection:
            rows = connection.execute(
                """
                SELECT COALESCE(NULLIF(profile_hash, ''),
                                import_profile || ':' || analysis_scope) AS profile_hash,
                       COALESCE(NULLIF(profile_label, ''),
                                CASE WHEN analysis_scope='whole_genome' THEN 'WGS' ELSE 'WES/exome' END
                                || ' ' || CASE WHEN import_profile='full' THEN 'full' ELSE 'candidate' END
                       ) AS profile_label,
                       analysis_scope,import_profile,profile_json,
                       COUNT(*) AS files, SUM(sample_count) AS sample_entries
                FROM cohort_files
                GROUP BY 1,2,3,4,5
                ORDER BY sample_entries DESC,profile_label
                """
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            try:
                item["settings"] = json.loads(item.pop("profile_json") or "{}")
            except json.JSONDecodeError:
                item["settings"] = {}
            result.append(item)
        return result

    def list_samples(self, query: str = "", limit: int = 500) -> list[dict]:
        """List exact sample entries so duplicate names remain distinguishable."""
        limit = max(1, min(int(limit), 5_000))
        cleaned = query.strip()
        where = "WHERE s.name LIKE ? COLLATE NOCASE" if cleaned else ""
        parameters: tuple = (f"%{cleaned}%", limit) if cleaned else (limit,)
        with self._session() as connection:
            rows = connection.execute(
                f"""
                SELECT s.id, s.name, s.file_id, f.path AS source_path,
                       f.import_profile, f.analysis_scope, f.imported_at,
                       f.profile_label,f.profile_hash,
                       COUNT(g.id) AS carrier_observations
                FROM cohort_samples s
                JOIN cohort_files f ON f.id = s.file_id
                LEFT JOIN cohort_genotypes g ON g.sample_id = s.id
                {where}
                GROUP BY s.id
                ORDER BY s.name COLLATE NOCASE, f.path
                LIMIT ?
                """,
                parameters,
            ).fetchall()
        return [dict(row) for row in rows]

    def remove_samples(self, sample_ids: list[int]) -> dict:
        """Remove selected sample entries and reclaim variants with no carriers."""
        with self._import_jobs_lock:
            if any(
                job["status"] in {"queued", "running"}
                for job in self._import_jobs.values()
            ):
                raise ValueError(
                    "wait for the active cohort import to finish before removing samples"
                )
        if not isinstance(sample_ids, list):
            raise ValueError("sample_ids must be a list")
        try:
            selected = sorted({int(value) for value in sample_ids})
        except (TypeError, ValueError) as error:
            raise ValueError("sample_ids must contain integers") from error
        if not selected:
            raise ValueError("select at least one sample to remove")
        if len(selected) > 5_000:
            raise ValueError("a single removal is limited to 5,000 sample entries")
        # Non-blocking acquire keeps the fast-fail contract while making the
        # exclusion real: the import worker's write phase holds this same
        # lock, so an import submitted between the early check above and this
        # point either blocks before writing or makes this raise — it can no
        # longer write genotypes for variants the removal is deleting.
        if not self._maintenance_lock.acquire(blocking=False):
            raise ValueError(
                "wait for the active cohort import or maintenance operation "
                "to finish before removing samples"
            )
        try:
            with self._import_jobs_lock:
                if any(
                    job["status"] in {"queued", "running"}
                    for job in self._import_jobs.values()
                ):
                    raise ValueError(
                        "wait for the active cohort import to finish before removing samples"
                    )
            return self._remove_samples(selected)
        finally:
            self._maintenance_lock.release()

    def _reset_cohort_tables(self) -> None:
        """Quickly clear the cohort index while preserving phenotype tables.

        Dropping empty-bound cohort b-trees avoids millions of row-level
        foreign-key cascades when every indexed sample is being removed. The
        database pages remain reusable by a later import; a VACUUM is not run
        because reclaiming filesystem space would itself be a long operation.
        """
        connection = self._connect()
        try:
            connection.execute("PRAGMA foreign_keys=OFF")
            connection.executescript(
                """
                BEGIN EXCLUSIVE;
                DROP TABLE cohort_genotypes;
                DROP TABLE cohort_annotations;
                DROP TABLE cohort_variants;
                DROP TABLE cohort_samples;
                DROP TABLE cohort_files;
                COMMIT;
                """
            )
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        self._initialize()
        with self._session() as connection:
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def _remove_samples(self, selected: list[int]) -> dict:
        placeholders = ",".join("?" for _ in selected)
        with self._session() as connection:
            rows = connection.execute(
                f"""
                SELECT s.id, s.name, s.file_id, f.path AS source_path
                FROM cohort_samples s
                JOIN cohort_files f ON f.id = s.file_id
                WHERE s.id IN ({placeholders})
                ORDER BY s.name, f.path
                """,
                selected,
            ).fetchall()
            if len(rows) != len(selected):
                found = {row["id"] for row in rows}
                missing = [value for value in selected if value not in found]
                raise ValueError(
                    "sample entries were not found: " + ", ".join(map(str, missing))
                )
            cohort_counts = connection.execute(
                """
                SELECT (SELECT COUNT(*) FROM cohort_samples) AS samples,
                       (SELECT COUNT(*) FROM cohort_variants) AS variants
                """
            ).fetchone()
        if cohort_counts["samples"] == len(selected):
            self._reset_cohort_tables()
            return {
                "removed": [dict(row) for row in rows],
                "removed_count": len(rows),
                "orphan_variants_removed": cohort_counts["variants"],
                "stats": self.stats(),
            }

        with self._session() as connection:
            file_ids = sorted({row["file_id"] for row in rows})
            connection.execute(
                f"DELETE FROM cohort_samples WHERE id IN ({placeholders})",
                selected,
            )
            for file_id in file_ids:
                counts = connection.execute(
                    """
                    SELECT COUNT(DISTINCT s.id) AS sample_count,
                           COUNT(DISTINCT g.variant_id) AS variant_count,
                           COUNT(g.id) AS carrier_count
                    FROM cohort_files f
                    LEFT JOIN cohort_samples s ON s.file_id = f.id
                    LEFT JOIN cohort_genotypes g ON g.sample_id = s.id
                    WHERE f.id = ?
                    """,
                    (file_id,),
                ).fetchone()
                if counts["sample_count"] == 0:
                    connection.execute(
                        "DELETE FROM cohort_files WHERE id = ?", (file_id,)
                    )
                else:
                    connection.execute(
                        """
                        UPDATE cohort_files SET sample_count=?, variant_count=?,
                                                carrier_count=?, mtime_ns=-1
                        WHERE id=?
                        """,
                        (
                            counts["sample_count"], counts["variant_count"],
                            counts["carrier_count"], file_id,
                        ),
                    )
            orphaned = connection.execute(
                """
                SELECT COUNT(*) FROM cohort_variants v
                WHERE NOT EXISTS (
                  SELECT 1 FROM cohort_genotypes g WHERE g.variant_id = v.id
                )
                """
            ).fetchone()[0]
            connection.execute(
                """
                DELETE FROM cohort_variants
                WHERE NOT EXISTS (
                  SELECT 1 FROM cohort_genotypes
                  WHERE cohort_genotypes.variant_id = cohort_variants.id
                )
                """
            )
        return {
            "removed": [dict(row) for row in rows],
            "removed_count": len(rows),
            "orphan_variants_removed": orphaned,
            "stats": self.stats(),
        }

    def expand_paths(self, raw_paths: list[str], recursive: bool = True) -> list[Path]:
        candidates: list[Path] = []
        for raw in raw_paths:
            if not isinstance(raw, str) or not raw.strip():
                continue
            path = Path(raw).expanduser().resolve()
            if path.is_dir():
                iterator = path.rglob("*") if recursive else path.iterdir()
                candidates.extend(item for item in iterator if item.is_file())
            elif path.is_file():
                candidates.append(path)
            else:
                raise ValueError(f"cohort input path does not exist: {path}")
        selected = sorted({
            path for path in candidates
            if path.name.lower().endswith((".vcf", ".vcf.gz"))
        })
        if not selected:
            raise ValueError("no .vcf or .vcf.gz files were found")
        if len(selected) > 10_000:
            raise ValueError("a single cohort import is limited to 10,000 VCF files")
        return selected

    def import_paths(
        self, raw_paths: list[str], recursive: bool = True, force: bool = False,
        allow_unknown_assembly: bool = False,
    ) -> dict:
        paths = self.expand_paths(raw_paths, recursive)
        results = []
        for path in paths:
            try:
                results.append(
                    self.import_vcf(
                        path,
                        force=force,
                        allow_unknown_assembly=allow_unknown_assembly,
                    )
                )
            except Exception as error:
                results.append({
                    "path": str(path),
                    "status": "failed",
                    "error": str(error),
                })
        return {
            "files": results,
            "imported": sum(item["status"] == "imported" for item in results),
            "skipped": sum(item["status"] == "unchanged" for item in results),
            "failed": sum(item["status"] == "failed" for item in results),
            "stats": self.stats(),
        }

    def start_import_paths(
        self, raw_paths: list[str], recursive: bool = True, force: bool = False,
        allow_unknown_assembly: bool = False,
        import_profile: str = "full",
        analysis_scope: str = "exome",
        prefilter_options: dict | None = None,
        prefilter: Callable[[Path, Callable[[dict], None]], tuple[Path, dict]] | None = None,
    ) -> dict:
        if import_profile not in {"full", "prefiltered"}:
            raise ValueError("import_profile must be 'full' or 'prefiltered'")
        if analysis_scope not in {"exome", "whole_genome"}:
            raise ValueError("analysis_scope must be 'exome' or 'whole_genome'")
        if import_profile == "prefiltered" and prefilter is None:
            raise ValueError("prefiltered cohort import requires a prefilter")
        if import_profile == "prefiltered":
            analysis_scope = "whole_genome"
        paths = self.expand_paths(raw_paths, recursive)
        with self._import_jobs_lock:
            if any(
                job["status"] in {"queued", "running"}
                for job in self._import_jobs.values()
            ):
                raise ValueError("another cohort import is already running")
            job_id = uuid.uuid4().hex
            job = {
                "id": job_id,
                "status": "queued",
                "created_at": utc_now(),
                "started_at": None,
                "finished_at": None,
                "total_files": len(paths),
                "completed_files": 0,
                "total_bytes": sum(path.stat().st_size for path in paths),
                "processed_bytes": 0,
                "current_path": "",
                "current_file_bytes": 0,
                "current_file_size": 0,
                "records_processed": 0,
                "pass_records": 0,
                "carrier_count": 0,
                "phase": "queued",
                "reader_count": 1,
                "prepared_path": "",
                "import_profile": import_profile,
                "analysis_scope": analysis_scope,
                "prefilter_options": prefilter_options or {},
                "prefilter_records_scanned": 0,
                "prefilter_records_retained": 0,
                "result": None,
                "error": "",
            }
            self._import_jobs[job_id] = job
            terminal = [
                key for key, value in self._import_jobs.items()
                if value["status"] in {"succeeded", "failed"}
            ]
            for key in terminal[:-20]:
                self._import_jobs.pop(key, None)
        thread = threading.Thread(
            target=self._run_import_job,
            args=(
                job_id, paths, force, allow_unknown_assembly, import_profile,
                analysis_scope, prefilter_options or {}, prefilter,
            ),
            name=f"cohort-import-{job_id[:8]}",
            daemon=True,
        )
        thread.start()
        return self.get_import_job(job_id)

    def get_import_job(self, job_id: str) -> dict | None:
        with self._import_jobs_lock:
            job = self._import_jobs.get(job_id)
            return dict(job) if job else None

    def _update_import_job(self, job_id: str, **changes) -> None:
        with self._import_jobs_lock:
            if job_id in self._import_jobs:
                self._import_jobs[job_id].update(changes)

    def _run_import_job(
        self, job_id: str, paths: list[Path], force: bool,
        allow_unknown_assembly: bool,
        import_profile: str,
        analysis_scope: str,
        prefilter_options: dict,
        prefilter: Callable[[Path, Callable[[dict], None]], tuple[Path, dict]] | None,
    ) -> None:
        self._update_import_job(
            job_id, status="running", phase="preparing", started_at=utc_now()
        )
        results: list[dict] = []
        completed_bytes = 0
        total_records = 0
        total_pass = 0
        total_carriers = 0
        total_prefilter_scanned = 0
        total_prefilter_retained = 0
        # Mutual exclusion with remove_samples: holding the maintenance lock
        # for the write phase means a removal can never delete variants while
        # this job is inserting their genotypes (and vice versa).
        self._maintenance_lock.acquire()
        try:
            for index, path in enumerate(paths):
                file_size = path.stat().st_size
                file_records = 0
                file_pass = 0
                file_carriers = 0
                file_prefilter_scanned = 0
                file_prefilter_retained = 0
                import_path = path
                prefilter_metadata: dict = {}
                self._update_import_job(
                    job_id,
                    current_path=str(path),
                    current_file_bytes=0,
                    current_file_size=file_size,
                    phase="preparing",
                    reader_count=1,
                    prepared_path="",
                )

                if prefilter is not None:
                    # This iteration's running totals are bound as defaults:
                    # captured by name they would silently attribute bytes and
                    # records to the wrong file if the callback ever fired
                    # after the loop advanced (ruff B023).
                    def prefilter_progress(
                        update: dict,
                        *,
                        _completed_bytes=completed_bytes,
                        _file_size=file_size,
                        _total_scanned=total_prefilter_scanned,
                        _total_retained=total_prefilter_retained,
                    ) -> None:
                        nonlocal file_prefilter_scanned, file_prefilter_retained
                        file_prefilter_scanned = int(
                            update.get("records_scanned", file_prefilter_scanned)
                        )
                        file_prefilter_retained = int(
                            update.get("records_retained", file_prefilter_retained)
                        )
                        percent = max(
                            0.0, min(100.0, float(update.get("progress", 0) or 0))
                        )
                        self._update_import_job(
                            job_id,
                            processed_bytes=_completed_bytes + int(
                                _file_size * 0.65 * percent / 100.0
                            ),
                            current_file_bytes=int(
                                _file_size * 0.65 * percent / 100.0
                            ),
                            phase=str(update.get("phase") or "prefiltering"),
                            reader_count=int(update.get("reader_count", 1) or 1),
                            prefilter_records_scanned=(
                                _total_scanned + file_prefilter_scanned
                            ),
                            prefilter_records_retained=(
                                _total_retained + file_prefilter_retained
                            ),
                        )

                    try:
                        import_path, prefilter_metadata = prefilter(
                            path, prefilter_progress
                        )
                    except Exception as error:
                        results.append({
                            "path": str(path),
                            "status": "failed",
                            "import_profile": import_profile,
                            "prefilter_records_scanned": file_prefilter_scanned,
                            "prefilter_records_retained": file_prefilter_retained,
                            "error": str(error),
                        })
                        completed_bytes += file_size
                        total_prefilter_scanned += file_prefilter_scanned
                        total_prefilter_retained += file_prefilter_retained
                        self._update_import_job(
                            job_id,
                            completed_files=index + 1,
                            processed_bytes=completed_bytes,
                            current_file_bytes=file_size,
                            prefilter_records_scanned=total_prefilter_scanned,
                            prefilter_records_retained=total_prefilter_retained,
                        )
                        continue
                    file_prefilter_scanned = int(
                        prefilter_metadata.get(
                            "records_scanned", file_prefilter_scanned
                        )
                    )
                    file_prefilter_retained = int(
                        prefilter_metadata.get(
                            "records_retained", file_prefilter_retained
                        )
                    )

                # Same default-arg binding rationale as prefilter_progress.
                def progress(
                    update: dict,
                    *,
                    _completed_bytes=completed_bytes,
                    _file_size=file_size,
                    _total_records=total_records,
                    _total_pass=total_pass,
                    _total_carriers=total_carriers,
                ) -> None:
                    nonlocal file_records, file_pass, file_carriers
                    file_records = int(update.get("records_processed", file_records))
                    file_pass = int(update.get("pass_records", file_pass))
                    file_carriers = int(update.get("carrier_count", file_carriers))
                    import_bytes = min(
                        _file_size, int(update.get("processed_bytes", 0))
                    )
                    current_bytes = (
                        int(_file_size * 0.65 + import_bytes * 0.35)
                        if prefilter is not None else import_bytes
                    )
                    changes = {
                        "processed_bytes": _completed_bytes + current_bytes,
                        "current_file_bytes": current_bytes,
                        "records_processed": _total_records + file_records,
                        "pass_records": _total_pass + file_pass,
                        "carrier_count": _total_carriers + file_carriers,
                        "phase": str(update.get("phase") or "indexing"),
                        "reader_count": int(update.get("reader_count", 1) or 1),
                    }
                    if update.get("prepared_path"):
                        changes["prepared_path"] = str(update["prepared_path"])
                    self._update_import_job(job_id, **changes)

                try:
                    result = self.import_vcf(
                        import_path,
                        force=force,
                        allow_unknown_assembly=allow_unknown_assembly,
                        progress=progress,
                        source_path=path,
                        import_profile=import_profile,
                        analysis_scope=analysis_scope,
                        prefilter_options=prefilter_options,
                        prefilter_metadata=prefilter_metadata,
                    )
                except Exception as error:
                    result = {
                        "path": str(path),
                        "status": "failed",
                        "error": str(error),
                    }
                results.append(result)
                completed_bytes += file_size
                total_records += file_records
                total_pass += int(result.get("pass_records", file_pass) or 0)
                total_carriers += int(result.get("carrier_count", file_carriers) or 0)
                total_prefilter_scanned += file_prefilter_scanned
                total_prefilter_retained += file_prefilter_retained
                self._update_import_job(
                    job_id,
                    completed_files=index + 1,
                    processed_bytes=completed_bytes,
                    current_file_bytes=file_size,
                    records_processed=total_records,
                    pass_records=total_pass,
                    carrier_count=total_carriers,
                    prefilter_records_scanned=total_prefilter_scanned,
                    prefilter_records_retained=total_prefilter_retained,
                )
            result = {
                "files": results,
                "imported": sum(item["status"] == "imported" for item in results),
                "skipped": sum(item["status"] == "unchanged" for item in results),
                "failed": sum(item["status"] == "failed" for item in results),
                "stats": self.stats(),
            }
            self._update_import_job(
                job_id,
                status="succeeded",
                phase="complete",
                finished_at=utc_now(),
                current_path="",
                result=result,
            )
        except Exception as error:
            self._update_import_job(
                job_id,
                status="failed",
                phase="failed",
                finished_at=utc_now(),
                error=str(error),
            )
        finally:
            self._maintenance_lock.release()

    def _import_vcf_rowwise(
        self, path: Path, force: bool = False, allow_unknown_assembly: bool = False,
        progress: Callable[[dict], None] | None = None,
        import_profile: str = "full", analysis_scope: str = "exome",
    ) -> dict:
        """Legacy single-stream importer retained as an explicit fallback."""
        path = path.resolve()
        assembly = detect_vcf_assembly(path)
        if assembly["assembly"] == "conflict":
            raise ValueError("VCF header contains conflicting assembly evidence")
        if assembly["assembly"] == "GRCh37":
            raise ValueError(
                "cohort indexing is GRCh38-only; liftover and re-annotate this GRCh37 VCF first"
            )
        if assembly["assembly"] == "unknown" and not allow_unknown_assembly:
            raise ValueError(
                "VCF assembly is ambiguous; confirm it is GRCh38 to index it"
            )
        stat = path.stat()
        with self._session() as connection:
            existing = connection.execute(
                "SELECT * FROM cohort_files WHERE path = ?", (str(path),)
            ).fetchone()
            if (
                existing and not force
                and existing["size_bytes"] == stat.st_size
                and existing["mtime_ns"] == stat.st_mtime_ns
                and existing["import_profile"] == import_profile
                and existing["analysis_scope"] == analysis_scope
            ):
                result = dict(existing)
                try:
                    result["prefilter_options"] = json.loads(
                        result.get("prefilter_options") or "{}"
                    )
                except json.JSONDecodeError:
                    result["prefilter_options"] = {}
                result.update({"status": "unchanged"})
                if progress:
                    progress({"processed_bytes": stat.st_size})
                return result

        opener = gzip.open if path.name.lower().endswith(".gz") else open
        with self._session() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if existing:
                connection.execute("DELETE FROM cohort_files WHERE id = ?", (existing["id"],))
            file_id = connection.execute(
                """
                INSERT INTO cohort_files(
                  path, size_bytes, mtime_ns, imported_at, assembly,
                  lifted_from_assembly, import_profile, analysis_scope
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(path), stat.st_size, stat.st_mtime_ns, utc_now(),
                    "GRCh38",
                    "GRCh37" if assembly["lifted_from_grch37"] else None,
                    import_profile,
                    analysis_scope,
                ),
            ).lastrowid

            samples: list[str] = []
            sample_ids: list[int] = []
            csq_fields: list[str] = []
            saw_fileformat = False
            saw_columns = False
            pass_records = 0
            excluded_records = 0
            carrier_count = 0
            variant_cache: dict[str, int] = {}
            annotation_cache: set[tuple] = set()
            records_processed = 0

            try:
                with opener(path, "rt", encoding="utf-8", errors="replace") as handle:
                    for line in handle:
                        if line.startswith("##fileformat=VCF"):
                            saw_fileformat = True
                            continue
                        if line.startswith("##contig=<"):
                            payload = line.split("<", 1)[1].rsplit(">", 1)[0]
                            values = dict(
                                item.split("=", 1)
                                for item in payload.split(",")
                                if "=" in item
                            )
                            if (
                                normalize_chromosome(values.get("ID", "")) == "1"
                                and values.get("length")
                                and values["length"] != "248956422"
                            ):
                                raise ValueError(
                                    "VCF chromosome 1 length does not match GRCh38 "
                                    f"(expected 248956422, found {values['length']})"
                                )
                            continue
                        if line.startswith("##INFO=<ID=CSQ"):
                            match = re.search(r"Format:\s*([^\">]+)", line, re.IGNORECASE)
                            if match:
                                csq_fields = match.group(1).strip().split("|")
                            continue
                        if line.startswith("#CHROM"):
                            saw_columns = True
                            samples = line.rstrip("\n").split("\t")[9:]
                            if not samples:
                                raise ValueError("cohort VCF must contain at least one sample")
                            sample_ids = [
                                connection.execute(
                                    "INSERT INTO cohort_samples(file_id, name) VALUES (?, ?)",
                                    (file_id, sample),
                                ).lastrowid
                                for sample in samples
                            ]
                            continue
                        if not line.strip() or line.startswith("#"):
                            continue
                        if not saw_columns:
                            raise ValueError("VCF #CHROM header was not found before records")

                        records_processed += 1
                        columns = line.rstrip("\n").split("\t")
                        if len(columns) < 10:
                            continue
                        chrom_raw, pos_raw, rsid, ref, alt_raw, qual_raw, filter_value, raw_info, format_value = columns[:9]
                        if filter_value not in ("PASS", "."):
                            excluded_records += 1
                            continue
                        pass_records += 1
                        chrom = normalize_chromosome(chrom_raw)
                        pos = int(pos_raw)
                        info = info_map(raw_info)
                        consequences = parse_csq_entries(info.get("CSQ", ""), csq_fields)
                        sample_values = columns[9:]
                        qual = parse_number(qual_raw)

                        for alt_index, alt in enumerate(alt_raw.split(",")):
                            carrier_rows = []
                            for sample_index, sample_id in enumerate(sample_ids):
                                genotype = parse_genotype(
                                    format_value,
                                    sample_values[sample_index] if sample_index < len(sample_values) else "",
                                    alt_index,
                                )
                                if genotype["carrier"]:
                                    carrier_rows.append(
                                        (sample_id, samples[sample_index], genotype)
                                    )
                            if not carrier_rows:
                                continue

                            key = variant_key(chrom, pos, ref, alt)
                            original_alts = info.get("IEI_ORIGINAL_ALT", "").split(",")
                            original = {
                                "assembly": first(info, ("IEI_ORIGINAL_ASSEMBLY",)),
                                "chrom": first(info, ("IEI_ORIGINAL_CHROM",)),
                                "pos": int(info["IEI_ORIGINAL_POS"])
                                if info.get("IEI_ORIGINAL_POS", "").isdigit()
                                else None,
                                "ref": first(info, ("IEI_ORIGINAL_REF",)),
                                "alt": decode(
                                    original_alts[alt_index]
                                    if alt_index < len(original_alts)
                                    else (original_alts[0] if original_alts else "")
                                ),
                            }
                            variant_id = variant_cache.get(key)
                            if variant_id is None:
                                connection.execute(
                                    """
                                    INSERT INTO cohort_variants(
                                      variant_key, chrom, pos, ref, alt, rsid,
                                      original_assembly, original_chrom, original_pos,
                                      original_ref, original_alt,
                                      unscored_indel_reasons
                                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                                    ON CONFLICT(variant_key) DO UPDATE SET
                                      rsid = CASE
                                        WHEN excluded.rsid IS NOT NULL AND excluded.rsid != '.'
                                        THEN excluded.rsid ELSE cohort_variants.rsid END,
                                      original_assembly=COALESCE(
                                        cohort_variants.original_assembly,
                                        excluded.original_assembly
                                      ),
                                      original_chrom=COALESCE(
                                        cohort_variants.original_chrom,
                                        excluded.original_chrom
                                      ),
                                      original_pos=COALESCE(
                                        cohort_variants.original_pos,
                                        excluded.original_pos
                                      ),
                                      original_ref=COALESCE(
                                        cohort_variants.original_ref,
                                        excluded.original_ref
                                      ),
                                      original_alt=COALESCE(
                                        cohort_variants.original_alt,
                                        excluded.original_alt
                                      ),
                                      unscored_indel_reasons=COALESCE(
                                        NULLIF(excluded.unscored_indel_reasons, ''),
                                        cohort_variants.unscored_indel_reasons
                                      )
                                    """,
                                    (
                                        key, chrom, pos, ref.upper(), alt.upper(),
                                        None if rsid == "." else rsid,
                                        original["assembly"] or None,
                                        original["chrom"] or None,
                                        original["pos"],
                                        original["ref"] or None,
                                        original["alt"] or None,
                                        allele_info_value(
                                            info, "IEI_UNSCORED_INDEL", alt_index
                                        ) or None,
                                    ),
                                )
                                variant_id = connection.execute(
                                    "SELECT id FROM cohort_variants WHERE variant_key = ?", (key,)
                                ).fetchone()["id"]
                                if len(variant_cache) >= 100_000:
                                    variant_cache.clear()
                                variant_cache[key] = variant_id

                            matching = []
                            for consequence in consequences:
                                allele_number = consequence.get("ALLELE_NUM", "")
                                if allele_number.isdigit():
                                    if int(allele_number) == alt_index + 1:
                                        matching.append(consequence)
                                elif not consequence.get("Allele") or consequence.get("Allele") == alt:
                                    matching.append(consequence)
                            if not matching:
                                matching = consequences

                            annotations = [
                                annotation_from({**info, **consequence})
                                for consequence in matching
                            ]
                            if "PICK" not in csq_fields:
                                annotations_by_gene: dict[str, list[dict]] = {}
                                for annotation in annotations:
                                    annotations_by_gene.setdefault(
                                        annotation["gene"], []
                                    ).append(annotation)
                                for gene_annotations in annotations_by_gene.values():
                                    if (
                                        len(gene_annotations) == 1
                                        and not gene_annotations[0]["mane"]
                                    ):
                                        gene_annotations[0]["picked"] = 1

                            for annotation in annotations:
                                annotation_key = (
                                    variant_id, annotation["gene"], annotation["transcript"],
                                    annotation["hgvsc"], annotation["hgvsp"], annotation["consequence"],
                                )
                                if annotation_key in annotation_cache:
                                    continue
                                if len(annotation_cache) >= 100_000:
                                    annotation_cache.clear()
                                annotation_cache.add(annotation_key)
                                connection.execute(
                                    """
                                    INSERT INTO cohort_annotations(
                                      variant_id, gene, gene_id, transcript, hgvsc, hgvsp,
                                      consequence, impact, gnomad_popmax, cadd, alpha_missense,
                                      spliceai, promoterai, logofunc_prediction,
                                      logofunc_neutral, logofunc_gof, logofunc_lof,
                                      logofunc_allele_available, logofunc_source_transcript,
                                      logofunc_source_hgvsp, logofunc_match,
                                      clinvar, clinvar_conflicting,
                                      loftee, loftee_50bp,
                                      loftee_50bp_original, loftee_50bp_changed,
                                      ptc_distance, ptc_calc_status, mane, picked,
                                      repeat_masker, segdup
                                    ) VALUES (
                                      :variant_id, :gene, :gene_id, :transcript, :hgvsc, :hgvsp,
                                      :consequence, :impact, :gnomad_popmax, :cadd, :alpha_missense,
                                      :spliceai, :promoterai, :logofunc_prediction,
                                      :logofunc_neutral, :logofunc_gof, :logofunc_lof,
                                      :logofunc_allele_available, :logofunc_source_transcript,
                                      :logofunc_source_hgvsp, :logofunc_match,
                                      :clinvar, :clinvar_conflicting,
                                      :loftee, :loftee_50bp,
                                      :loftee_50bp_original, :loftee_50bp_changed,
                                      :ptc_distance, :ptc_calc_status, :mane, :picked,
                                      :repeat_masker, :segdup
                                    )
                                    ON CONFLICT(
                                      variant_id, gene, transcript, hgvsc, hgvsp, consequence
                                    ) DO UPDATE SET
                                      impact=excluded.impact,
                                      gnomad_popmax=COALESCE(excluded.gnomad_popmax, cohort_annotations.gnomad_popmax),
                                      cadd=COALESCE(excluded.cadd, cohort_annotations.cadd),
                                      alpha_missense=COALESCE(excluded.alpha_missense, cohort_annotations.alpha_missense),
                                      spliceai=COALESCE(excluded.spliceai, cohort_annotations.spliceai),
                                      promoterai=COALESCE(excluded.promoterai, cohort_annotations.promoterai),
                                      logofunc_prediction=COALESCE(NULLIF(excluded.logofunc_prediction, ''), cohort_annotations.logofunc_prediction),
                                      logofunc_neutral=COALESCE(excluded.logofunc_neutral, cohort_annotations.logofunc_neutral),
                                      logofunc_gof=COALESCE(excluded.logofunc_gof, cohort_annotations.logofunc_gof),
                                      logofunc_lof=COALESCE(excluded.logofunc_lof, cohort_annotations.logofunc_lof),
                                      logofunc_allele_available=MAX(excluded.logofunc_allele_available, cohort_annotations.logofunc_allele_available),
                                      logofunc_source_transcript=COALESCE(NULLIF(excluded.logofunc_source_transcript, ''), cohort_annotations.logofunc_source_transcript),
                                      logofunc_source_hgvsp=COALESCE(NULLIF(excluded.logofunc_source_hgvsp, ''), cohort_annotations.logofunc_source_hgvsp),
                                      logofunc_match=COALESCE(NULLIF(excluded.logofunc_match, ''), cohort_annotations.logofunc_match),
                                      clinvar=COALESCE(NULLIF(excluded.clinvar, ''), cohort_annotations.clinvar),
                                      clinvar_conflicting=COALESCE(NULLIF(excluded.clinvar_conflicting, ''), cohort_annotations.clinvar_conflicting),
                                      loftee=COALESCE(NULLIF(excluded.loftee, ''), cohort_annotations.loftee),
                                      loftee_50bp=COALESCE(NULLIF(excluded.loftee_50bp, ''), cohort_annotations.loftee_50bp),
                                      loftee_50bp_original=COALESCE(NULLIF(excluded.loftee_50bp_original, ''), cohort_annotations.loftee_50bp_original),
                                      loftee_50bp_changed=MAX(excluded.loftee_50bp_changed, cohort_annotations.loftee_50bp_changed),
                                      ptc_distance=COALESCE(excluded.ptc_distance, cohort_annotations.ptc_distance),
                                      ptc_calc_status=COALESCE(NULLIF(excluded.ptc_calc_status, ''), cohort_annotations.ptc_calc_status),
                                      -- latest import wins: MAX() was monotonic, so a
                                      -- reannotation could never clear a superseded
                                      -- MANE/PICK flag (two "preferred" transcripts per gene)
                                      mane=excluded.mane,
                                      picked=excluded.picked,
                                      repeat_masker=MAX(excluded.repeat_masker, cohort_annotations.repeat_masker),
                                      segdup=MAX(excluded.segdup, cohort_annotations.segdup)
                                    """,
                                    {"variant_id": variant_id, **annotation},
                                )

                            for sample_id, sample_name, genotype in carrier_rows:
                                haplotype = haplotype_frame_evidence(
                                    info.get("IEI_HAPLOTYPE_FRAME", ""),
                                    f"{chrom_raw}:{pos}:{ref}:{alt}",
                                    sample_name,
                                )
                                connection.execute(
                                    """
                                    INSERT INTO cohort_genotypes(
                                      variant_id, sample_id, genotype, zygosity, phased,
                                      dp, gq, allele_balance, qual,
                                      haplotype_frame_status,
                                      haplotype_frame_partners,
                                      haplotype_protein_change,
                                      haplotype_transcript
                                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                                    ON CONFLICT(variant_id, sample_id) DO UPDATE SET
                                      genotype=excluded.genotype,
                                      zygosity=excluded.zygosity,
                                      phased=excluded.phased,
                                      dp=excluded.dp,
                                      gq=excluded.gq,
                                      allele_balance=excluded.allele_balance,
                                      qual=excluded.qual,
                                      haplotype_frame_status=excluded.haplotype_frame_status,
                                      haplotype_frame_partners=excluded.haplotype_frame_partners,
                                      haplotype_protein_change=excluded.haplotype_protein_change,
                                      haplotype_transcript=excluded.haplotype_transcript
                                    """,
                                    (
                                        variant_id, sample_id, genotype["gt"],
                                        genotype["zygosity"], genotype["phased"],
                                        genotype["dp"], genotype["gq"],
                                        genotype["allele_balance"], qual,
                                        haplotype["status"], haplotype["partners"],
                                        haplotype["protein"], haplotype["transcript"],
                                    ),
                                )
                                carrier_count += 1

                        if progress and records_processed % 5000 == 0:
                            try:
                                if path.name.lower().endswith(".gz"):
                                    processed_bytes = handle.buffer.fileobj.tell()
                                else:
                                    processed_bytes = handle.buffer.tell()
                            except (AttributeError, OSError):
                                processed_bytes = 0
                            progress({
                                "processed_bytes": processed_bytes,
                                "records_processed": records_processed,
                                "pass_records": pass_records,
                                "carrier_count": carrier_count,
                            })

                if not saw_fileformat:
                    raise ValueError("file does not begin with a VCF fileformat header")
                if not csq_fields:
                    raise ValueError("VEP CSQ Format header was not found")

                variant_count = connection.execute(
                    """
                    SELECT COUNT(DISTINCT genotype.variant_id)
                    FROM cohort_genotypes AS genotype
                    JOIN cohort_samples AS sample ON sample.id = genotype.sample_id
                    WHERE sample.file_id = ?
                    """,
                    (file_id,),
                ).fetchone()[0]
                connection.execute(
                    """
                    UPDATE cohort_files SET
                      sample_count=?, pass_records=?, excluded_records=?,
                      variant_count=?, carrier_count=?
                    WHERE id=?
                    """,
                    (
                        len(samples), pass_records, excluded_records,
                        variant_count, carrier_count, file_id,
                    ),
                )
                if existing:
                    connection.execute(
                        """
                        DELETE FROM cohort_variants
                        WHERE id NOT IN (SELECT DISTINCT variant_id FROM cohort_genotypes)
                        """
                    )
                connection.commit()
                if progress:
                    progress({
                        "processed_bytes": stat.st_size,
                        "records_processed": records_processed,
                        "pass_records": pass_records,
                        "carrier_count": carrier_count,
                    })
            except Exception:
                connection.rollback()
                raise

        return {
            "id": file_id,
            "path": str(path),
            "status": "imported",
            "sample_count": len(samples),
            "pass_records": pass_records,
            "excluded_records": excluded_records,
            "variant_count": variant_count,
            "carrier_count": carrier_count,
            "import_profile": import_profile,
            "analysis_scope": analysis_scope,
        }

    def _prepare_indexed_vcf(
        self, path: Path, progress: Callable[[dict], None] | None = None
    ) -> PreparedVcf:
        if not self.enable_auto_index:
            return PreparedVcf(path, path, None, False, False)
        if self.hts_backend is None:
            return PreparedVcf(
                path,
                path,
                None,
                False,
                False,
                "bcftools/tabix and the configured HTS container are unavailable; "
                "using serial streaming without an index",
            )
        if progress:
            progress({"phase": "preparing_index", "processed_bytes": 0})

        if is_bgzf(path):
            source_index = self.hts_backend.validate_index(path)
            if source_index:
                return PreparedVcf(path, path, source_index, False, False)

        stat = path.stat()
        fingerprint = hashlib.sha256(
            f"{path}\0{stat.st_size}\0{stat.st_mtime_ns}".encode()
        ).hexdigest()[:20]
        cache_dir = self.prepared_dir / fingerprint
        cache_dir.mkdir(parents=True, exist_ok=True)
        source_name = path.name
        for suffix in (".vcf.gz", ".vcf", ".gz"):
            if source_name.lower().endswith(suffix):
                source_name = source_name[:-len(suffix)]
                break
        prepared = cache_dir / f"{source_name}.prepared.vcf.gz"
        cached_index = (
            self.hts_backend.validate_index(prepared)
            if prepared.is_file() else None
        )
        if cached_index:
            return PreparedVcf(path, prepared, cached_index, True, True)

        # Unique staging name: same-named VCFs from different directories map
        # to one cache entry, and a shared deterministic temp let a second
        # preparation delete the first's in-progress output mid-write.
        temporary = cache_dir / f".{source_name}.{uuid.uuid4().hex}.building.vcf.gz"
        for candidate in (
            temporary, Path(f"{temporary}.tbi"), Path(f"{temporary}.csi"),
            prepared, Path(f"{prepared}.tbi"), Path(f"{prepared}.csi"),
        ):
            if candidate.exists():
                candidate.unlink()
        try:
            if is_bgzf(path):
                shutil.copy2(path, prepared)
                try:
                    index_path = self.hts_backend.create_index(prepared)
                except RuntimeError:
                    self.hts_backend.sort_bgzip(path, temporary)
                    os.replace(temporary, prepared)
                    index_path = self.hts_backend.create_index(prepared)
            else:
                self.hts_backend.sort_bgzip(path, temporary)
                os.replace(temporary, prepared)
                index_path = self.hts_backend.create_index(prepared)
        except Exception as error:
            for candidate in (
                temporary, Path(f"{temporary}.tbi"), Path(f"{temporary}.csi"),
                prepared, Path(f"{prepared}.tbi"), Path(f"{prepared}.csi"),
            ):
                if candidate.exists():
                    candidate.unlink()
            return PreparedVcf(
                path,
                path,
                None,
                False,
                False,
                f"automatic BGZF preparation/indexing failed for {path.name}; "
                f"using serial staged import: {error}",
            )
        return PreparedVcf(path, prepared, index_path, True, False)

    def prepare_vcf(
        self, path: Path, progress: Callable[[dict], None] | None = None
    ) -> PreparedVcf:
        """Return an indexed BGZF source or cached working copy when possible."""
        return self._prepare_indexed_vcf(path.resolve(), progress)

    def prepare_managed_vcf(
        self, source: Path, destination_directory: Path, content_key: str
    ) -> tuple[Path, Path | None, str]:
        """Create an owned review VCF and index for persistent Sample Library.

        The cohort preparation directory is a cache and may be cleaned. This
        method therefore copies the prepared representation into a separate,
        content-addressed managed directory before returning it.
        """
        source = source.resolve()
        destination_directory.mkdir(parents=True, exist_ok=True)
        prepared = self._prepare_indexed_vcf(source)
        compressed = prepared.path.name.lower().endswith((".gz", ".bgz"))
        destination = destination_directory / (
            f"{content_key}.vcf.gz" if compressed else f"{content_key}.vcf"
        )
        if not destination.is_file():
            # Unique partial name: two concurrent imports of identical content
            # share content_key, and with one deterministic partial the second
            # os.replace raced the first (FileNotFoundError after the first
            # consumed the file). Each writer stages privately; os.replace is
            # atomic and both publish identical bytes.
            temporary = destination.with_name(
                f"{destination.name}.{uuid.uuid4().hex}.partial"
            )
            shutil.copy2(prepared.path, temporary)
            os.replace(temporary, destination)

        index: Path | None = None
        if prepared.index_path and prepared.index_path.is_file():
            suffix = ".csi" if prepared.index_path.name.endswith(".csi") else ".tbi"
            index = Path(f"{destination}{suffix}")
            if not index.is_file():
                temporary_index = Path(f"{index}.{uuid.uuid4().hex}.partial")
                shutil.copy2(prepared.index_path, temporary_index)
                os.replace(temporary_index, index)
        elif compressed and self.hts_backend is not None:
            index = self.hts_backend.validate_index(destination)
            if index is None:
                try:
                    index = self.hts_backend.create_index(destination)
                except RuntimeError:
                    index = None

        warning = prepared.warning
        if compressed and index is None:
            warning = "; ".join(filter(None, [
                warning,
                "managed review VCF retained without a tabix/CSI index",
            ]))
        return destination, index, warning

    @staticmethod
    def _compressed_position(handle, path: Path) -> int:
        try:
            if path.name.lower().endswith((".gz", ".bgz")):
                return int(handle.buffer.fileobj.tell())
            return int(handle.buffer.tell())
        except (AttributeError, OSError, ValueError):
            return 0

    def _serial_stage(
        self,
        path: Path,
        header: VcfHeader,
        stage_path: Path,
        progress: Callable[[dict], None] | None,
    ) -> dict:
        opener = gzip.open if path.name.lower().endswith((".gz", ".bgz")) else open
        with opener(path, "rt", encoding="utf-8", errors="replace") as handle:
            return _stage_vcf_records(
                handle,
                header,
                stage_path,
                progress=(
                    (lambda update: progress({**update, "phase": "indexing", "reader_count": 1}))
                    if progress else None
                ),
                processed_bytes=lambda: self._compressed_position(handle, path),
                batch_records=self.stage_batch_records,
            )

    def _parallel_stages(
        self,
        path: Path,
        header: VcfHeader,
        stage_root: Path,
        progress: Callable[[dict], None] | None,
    ) -> tuple[list[dict], int]:
        assert self.hts_backend is not None
        contigs = self.hts_backend.list_contigs(path)
        reader_count = min(self.index_readers, len(contigs))
        if reader_count < 2:
            stage = stage_root / "reader-0.sqlite3"
            return [self._serial_stage(path, header, stage, progress)], 1
        groups = [contigs[index::reader_count] for index in range(reader_count)]
        file_size = path.stat().st_size
        results: list[dict] = []

        def collect(executor) -> None:
            futures = [
                executor.submit(
                    _tabix_stage_worker,
                    self.hts_backend,
                    path,
                    tuple(groups[index]),
                    header,
                    stage_root / f"reader-{index}.sqlite3",
                    self.stage_batch_records,
                )
                for index in range(reader_count)
            ]
            for future in as_completed(futures):
                results.append(future.result())
                if progress:
                    progress({
                        "records_processed": sum(
                            result["records_processed"] for result in results
                        ),
                        "pass_records": sum(
                            result["pass_records"] for result in results
                        ),
                        "carrier_count": sum(
                            result["carrier_count"] for result in results
                        ),
                        "processed_bytes": int(
                            file_size * len(results) / reader_count
                        ),
                        "phase": "indexing",
                        "reader_count": reader_count,
                    })

        context = multiprocessing.get_context("spawn")
        try:
            executor = ProcessPoolExecutor(
                max_workers=reader_count, mp_context=context
            )
        except (PermissionError, NotImplementedError):
            executor = ThreadPoolExecutor(
                max_workers=reader_count, thread_name_prefix="cohort-reader"
            )
        with executor:
            collect(executor)
        return results, reader_count

    def _merge_stages(
        self,
        *,
        source_path: Path,
        prepared: PreparedVcf,
        assembly: dict,
        header: VcfHeader,
        stages: list[dict],
        existing: sqlite3.Row | None,
        import_mode: str,
        reader_count: int,
        import_profile: str,
        analysis_scope: str,
        prefilter_options_json: str,
        prefilter_metadata: dict,
    ) -> tuple[int, int]:
        stat = source_path.stat()
        aliases = [f"stage_{index}" for index in range(len(stages))]
        connection = self._connect()
        try:
            for alias, stage in zip(aliases, stages):
                connection.execute(
                    f"ATTACH DATABASE ? AS {alias}", (stage["stage_path"],)
                )
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("PRAGMA defer_foreign_keys=ON")
            other_file_count = connection.execute(
                "SELECT COUNT(*) FROM cohort_files WHERE path != ?",
                (str(source_path),),
            ).fetchone()[0]
            rebuild_secondary_indexes = other_file_count == 0
            if rebuild_secondary_indexes:
                for index_name in COHORT_SECONDARY_INDEXES:
                    connection.execute(f"DROP INDEX IF EXISTS {index_name}")
            if existing:
                connection.execute(
                    "DELETE FROM cohort_files WHERE id = ?", (existing["id"],)
                )
            file_id = connection.execute(
                """
                INSERT INTO cohort_files(
                  path, size_bytes, mtime_ns, imported_at, assembly,
                  lifted_from_assembly, prepared_path, index_path,
                  import_mode, reader_count, preparation_warning,
                  import_profile, analysis_scope, prefilter_options,
                  prefilter_records_scanned, prefilter_records_retained
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(source_path), stat.st_size, stat.st_mtime_ns, utc_now(),
                    "GRCh38",
                    "GRCh37" if assembly["lifted_from_grch37"] else None,
                    str(prepared.path),
                    str(prepared.index_path) if prepared.index_path else None,
                    import_mode, reader_count, prepared.warning,
                    import_profile, analysis_scope, prefilter_options_json,
                    int(prefilter_metadata.get("records_scanned", 0) or 0),
                    int(prefilter_metadata.get("records_retained", 0) or 0),
                ),
            ).lastrowid
            connection.executemany(
                "INSERT INTO cohort_samples(file_id, name) VALUES (?, ?)",
                ((file_id, sample) for sample in header.samples),
            )

            for alias in aliases:
                connection.execute(f"""
                    INSERT INTO cohort_variants(
                      variant_key, chrom, pos, ref, alt, rsid,
                      original_assembly, original_chrom, original_pos,
                      original_ref, original_alt, unscored_indel_reasons
                    )
                    SELECT variant_key, chrom, pos, ref, alt, rsid,
                           original_assembly, original_chrom, original_pos,
                           original_ref, original_alt, unscored_indel_reasons
                    FROM {alias}.stage_variants WHERE 1
                    ON CONFLICT(variant_key) DO UPDATE SET
                      rsid=CASE
                        WHEN excluded.rsid IS NOT NULL AND excluded.rsid != '.'
                        THEN excluded.rsid ELSE cohort_variants.rsid END,
                      original_assembly=COALESCE(
                        cohort_variants.original_assembly, excluded.original_assembly),
                      original_chrom=COALESCE(
                        cohort_variants.original_chrom, excluded.original_chrom),
                      original_pos=COALESCE(
                        cohort_variants.original_pos, excluded.original_pos),
                      original_ref=COALESCE(
                        cohort_variants.original_ref, excluded.original_ref),
                      original_alt=COALESCE(
                        cohort_variants.original_alt, excluded.original_alt),
                      unscored_indel_reasons=COALESCE(
                        NULLIF(excluded.unscored_indel_reasons, ''),
                        cohort_variants.unscored_indel_reasons)
                """)
                connection.execute(f"""
                    INSERT INTO cohort_annotations(
                      variant_id, gene, gene_id, transcript, hgvsc, hgvsp,
                      consequence, impact, gnomad_popmax, cadd, alpha_missense,
                      spliceai, promoterai, logofunc_prediction, logofunc_neutral,
                      logofunc_gof, logofunc_lof, logofunc_allele_available,
                      logofunc_source_transcript, logofunc_source_hgvsp,
                      logofunc_match, clinvar, clinvar_conflicting, loftee, loftee_50bp,
                      loftee_50bp_original, loftee_50bp_changed, ptc_distance,
                      ptc_calc_status, mane, picked, repeat_masker, segdup
                    )
                    SELECT variant.id, annotation.gene, annotation.gene_id,
                           annotation.transcript, annotation.hgvsc, annotation.hgvsp,
                           annotation.consequence, annotation.impact,
                           annotation.gnomad_popmax, annotation.cadd,
                           annotation.alpha_missense, annotation.spliceai,
                           annotation.promoterai, annotation.logofunc_prediction,
                           annotation.logofunc_neutral, annotation.logofunc_gof,
                           annotation.logofunc_lof, annotation.logofunc_allele_available,
                           annotation.logofunc_source_transcript,
                           annotation.logofunc_source_hgvsp, annotation.logofunc_match,
                           annotation.clinvar, annotation.clinvar_conflicting,
                           annotation.loftee, annotation.loftee_50bp,
                           annotation.loftee_50bp_original,
                           annotation.loftee_50bp_changed, annotation.ptc_distance,
                           annotation.ptc_calc_status, annotation.mane,
                           annotation.picked, annotation.repeat_masker,
                           annotation.segdup
                    FROM {alias}.stage_annotations AS annotation
                    JOIN cohort_variants AS variant
                      ON variant.variant_key = annotation.variant_key
                    WHERE 1
                    ON CONFLICT(
                      variant_id, gene, transcript, hgvsc, hgvsp, consequence
                    ) DO UPDATE SET
                      impact=excluded.impact,
                      gnomad_popmax=COALESCE(excluded.gnomad_popmax, cohort_annotations.gnomad_popmax),
                      cadd=COALESCE(excluded.cadd, cohort_annotations.cadd),
                      alpha_missense=COALESCE(excluded.alpha_missense, cohort_annotations.alpha_missense),
                      spliceai=COALESCE(excluded.spliceai, cohort_annotations.spliceai),
                      promoterai=COALESCE(excluded.promoterai, cohort_annotations.promoterai),
                      logofunc_prediction=COALESCE(NULLIF(excluded.logofunc_prediction, ''), cohort_annotations.logofunc_prediction),
                      logofunc_neutral=COALESCE(excluded.logofunc_neutral, cohort_annotations.logofunc_neutral),
                      logofunc_gof=COALESCE(excluded.logofunc_gof, cohort_annotations.logofunc_gof),
                      logofunc_lof=COALESCE(excluded.logofunc_lof, cohort_annotations.logofunc_lof),
                      logofunc_allele_available=MAX(excluded.logofunc_allele_available, cohort_annotations.logofunc_allele_available),
                      logofunc_source_transcript=COALESCE(NULLIF(excluded.logofunc_source_transcript, ''), cohort_annotations.logofunc_source_transcript),
                      logofunc_source_hgvsp=COALESCE(NULLIF(excluded.logofunc_source_hgvsp, ''), cohort_annotations.logofunc_source_hgvsp),
                      logofunc_match=COALESCE(NULLIF(excluded.logofunc_match, ''), cohort_annotations.logofunc_match),
                      clinvar=COALESCE(NULLIF(excluded.clinvar, ''), cohort_annotations.clinvar),
                      clinvar_conflicting=COALESCE(NULLIF(excluded.clinvar_conflicting, ''), cohort_annotations.clinvar_conflicting),
                      loftee=COALESCE(NULLIF(excluded.loftee, ''), cohort_annotations.loftee),
                      loftee_50bp=COALESCE(NULLIF(excluded.loftee_50bp, ''), cohort_annotations.loftee_50bp),
                      loftee_50bp_original=COALESCE(NULLIF(excluded.loftee_50bp_original, ''), cohort_annotations.loftee_50bp_original),
                      loftee_50bp_changed=MAX(excluded.loftee_50bp_changed, cohort_annotations.loftee_50bp_changed),
                      ptc_distance=COALESCE(excluded.ptc_distance, cohort_annotations.ptc_distance),
                      ptc_calc_status=COALESCE(NULLIF(excluded.ptc_calc_status, ''), cohort_annotations.ptc_calc_status),
                      -- latest import wins (see the reannotation note above)
                      mane=excluded.mane,
                      picked=excluded.picked,
                      repeat_masker=MAX(excluded.repeat_masker, cohort_annotations.repeat_masker),
                      segdup=MAX(excluded.segdup, cohort_annotations.segdup)
                """)
                connection.execute(f"""
                    INSERT INTO cohort_genotypes(
                      variant_id, sample_id, genotype, zygosity, phased,
                      dp, gq, allele_balance, qual, haplotype_frame_status,
                      haplotype_frame_partners, haplotype_protein_change,
                      haplotype_transcript
                    )
                    SELECT variant.id, sample.id, genotype.genotype,
                           genotype.zygosity, genotype.phased, genotype.dp,
                           genotype.gq, genotype.allele_balance, genotype.qual,
                           genotype.haplotype_frame_status,
                           genotype.haplotype_frame_partners,
                           genotype.haplotype_protein_change,
                           genotype.haplotype_transcript
                    FROM {alias}.stage_genotypes AS genotype
                    JOIN cohort_variants AS variant
                      ON variant.variant_key = genotype.variant_key
                    JOIN cohort_samples AS sample
                      ON sample.file_id = {int(file_id)}
                     AND sample.name = genotype.sample_name
                    WHERE 1
                    ON CONFLICT(variant_id, sample_id) DO UPDATE SET
                      genotype=excluded.genotype,
                      zygosity=excluded.zygosity,
                      phased=excluded.phased,
                      dp=excluded.dp,
                      gq=excluded.gq,
                      allele_balance=excluded.allele_balance,
                      qual=excluded.qual,
                      haplotype_frame_status=excluded.haplotype_frame_status,
                      haplotype_frame_partners=excluded.haplotype_frame_partners,
                      haplotype_protein_change=excluded.haplotype_protein_change,
                      haplotype_transcript=excluded.haplotype_transcript
                """)

            pass_records = sum(stage["pass_records"] for stage in stages)
            excluded_records = sum(stage["excluded_records"] for stage in stages)
            carrier_count = sum(stage["carrier_count"] for stage in stages)
            variant_count = connection.execute(
                """
                SELECT COUNT(DISTINCT genotype.variant_id)
                FROM cohort_genotypes AS genotype
                JOIN cohort_samples AS sample ON sample.id = genotype.sample_id
                WHERE sample.file_id = ?
                """,
                (file_id,),
            ).fetchone()[0]
            connection.execute(
                """
                UPDATE cohort_files SET
                  sample_count=?, pass_records=?, excluded_records=?,
                  variant_count=?, carrier_count=?
                WHERE id=?
                """,
                (
                    len(header.samples), pass_records, excluded_records,
                    variant_count, carrier_count, file_id,
                ),
            )
            if existing:
                connection.execute(
                    """
                    DELETE FROM cohort_variants
                    WHERE id NOT IN (SELECT DISTINCT variant_id FROM cohort_genotypes)
                    """
                )
            if rebuild_secondary_indexes:
                for statement in COHORT_SECONDARY_INDEXES.values():
                    connection.execute(
                        statement.replace(
                            "CREATE INDEX ", "CREATE INDEX IF NOT EXISTS ", 1
                        )
                    )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            for alias in aliases:
                try:
                    connection.execute(f"DETACH DATABASE {alias}")
                except sqlite3.Error:
                    pass
            connection.close()
        with self._session() as checkpoint_connection:
            checkpoint_connection.execute("PRAGMA wal_checkpoint(PASSIVE)")
        return file_id, variant_count

    def import_vcf(
        self, path: Path, force: bool = False, allow_unknown_assembly: bool = False,
        progress: Callable[[dict], None] | None = None,
        source_path: Path | None = None,
        import_profile: str = "full",
        analysis_scope: str = "exome",
        prefilter_options: dict | None = None,
        prefilter_metadata: dict | None = None,
    ) -> dict:
        if import_profile not in {"full", "prefiltered"}:
            raise ValueError("import_profile must be 'full' or 'prefiltered'")
        if analysis_scope not in {"exome", "whole_genome"}:
            raise ValueError("analysis_scope must be 'exome' or 'whole_genome'")
        if import_profile == "prefiltered":
            analysis_scope = "whole_genome"
        if os.environ.get("IEI_COHORT_LEGACY_IMPORT") == "1" and source_path is None:
            return self._import_vcf_rowwise(
                path, force=force,
                allow_unknown_assembly=allow_unknown_assembly,
                progress=progress,
                import_profile=import_profile,
                analysis_scope=analysis_scope,
            )
        path = path.resolve()
        source_path = (source_path or path).resolve()
        prefilter_metadata = prefilter_metadata or {}
        prefilter_options_json = json.dumps(
            prefilter_options or {}, sort_keys=True, separators=(",", ":")
        )
        assembly = detect_vcf_assembly(source_path)
        if assembly["assembly"] == "conflict":
            raise ValueError("VCF header contains conflicting assembly evidence")
        if assembly["assembly"] == "GRCh37":
            raise ValueError(
                "cohort indexing is GRCh38-only; liftover and re-annotate this GRCh37 VCF first"
            )
        if assembly["assembly"] == "unknown" and not allow_unknown_assembly:
            raise ValueError(
                "VCF assembly is ambiguous; confirm it is GRCh38 to index it"
            )
        stat = source_path.stat()
        with self._session() as connection:
            existing = connection.execute(
                "SELECT * FROM cohort_files WHERE path = ?", (str(source_path),)
            ).fetchone()
            if (
                existing and not force
                and existing["size_bytes"] == stat.st_size
                and existing["mtime_ns"] == stat.st_mtime_ns
                and existing["import_profile"] == import_profile
                and existing["analysis_scope"] == analysis_scope
                and existing["prefilter_options"] == prefilter_options_json
            ):
                result = dict(existing)
                try:
                    result["prefilter_options"] = json.loads(
                        result.get("prefilter_options") or "{}"
                    )
                except json.JSONDecodeError:
                    result["prefilter_options"] = {}
                result.update({"status": "unchanged"})
                if progress:
                    progress({
                        "phase": "complete",
                        "processed_bytes": stat.st_size,
                        "reader_count": result.get("reader_count", 1),
                    })
                return result

        prepared = self._prepare_indexed_vcf(path, progress)
        header = read_vcf_header(prepared.path)
        if progress:
            progress({
                "phase": "indexing",
                "prepared_path": str(prepared.path),
                "reader_count": 1,
            })
        with tempfile.TemporaryDirectory(
            prefix="cohort-import-", dir=self.staging_dir
        ) as stage_directory:
            stage_root = Path(stage_directory)
            if (
                prepared.index_path is not None
                and self.hts_backend is not None
                and self.index_readers > 1
            ):
                stages, reader_count = self._parallel_stages(
                    prepared.path, header, stage_root, progress
                )
            else:
                reader_count = 1
                stages = [self._serial_stage(
                    prepared.path, header, stage_root / "reader-0.sqlite3", progress
                )]
            import_mode = (
                "parallel_tabix_staged" if reader_count > 1 else "serial_staged"
            )
            if progress:
                progress({
                    "phase": "merging",
                    "processed_bytes": stat.st_size,
                    "records_processed": sum(
                        stage["records_processed"] for stage in stages
                    ),
                    "pass_records": sum(stage["pass_records"] for stage in stages),
                    "carrier_count": sum(stage["carrier_count"] for stage in stages),
                    "reader_count": reader_count,
                })
            file_id, variant_count = self._merge_stages(
                source_path=source_path,
                prepared=prepared,
                assembly=assembly,
                header=header,
                stages=stages,
                existing=existing,
                import_mode=import_mode,
                reader_count=reader_count,
                import_profile=import_profile,
                analysis_scope=analysis_scope,
                prefilter_options_json=prefilter_options_json,
                prefilter_metadata=prefilter_metadata,
            )

        pass_records = sum(stage["pass_records"] for stage in stages)
        excluded_records = sum(stage["excluded_records"] for stage in stages)
        carrier_count = sum(stage["carrier_count"] for stage in stages)
        records_processed = sum(stage["records_processed"] for stage in stages)
        result = {
            "id": file_id,
            "path": str(source_path),
            "status": "imported",
            "sample_count": len(header.samples),
            "pass_records": pass_records,
            "excluded_records": excluded_records,
            "variant_count": variant_count,
            "carrier_count": carrier_count,
            "records_processed": records_processed,
            "prepared_path": str(prepared.path),
            "index_path": str(prepared.index_path) if prepared.index_path else None,
            "import_mode": import_mode,
            "reader_count": reader_count,
            "preparation_warning": prepared.warning,
            "cache_hit": prepared.cache_hit,
            "import_profile": import_profile,
            "analysis_scope": analysis_scope,
            "prefilter_options": prefilter_options or {},
            "prefilter_records_scanned": int(
                prefilter_metadata.get("records_scanned", 0) or 0
            ),
            "prefilter_records_retained": int(
                prefilter_metadata.get("records_retained", 0) or 0
            ),
        }
        if progress:
            progress({
                **result,
                "phase": "complete",
                "processed_bytes": stat.st_size,
            })
        return result

    @staticmethod
    def _parse_variant_query(value: str) -> tuple[str, list] | None:
        cleaned = value.strip().replace(",", "")
        locus = re.fullmatch(
            r"(?:chr)?([A-Za-z0-9]+)[:\-](\d+)[:\-]([A-Za-z*]+)[:>\-]([A-Za-z*]+)",
            cleaned,
            re.IGNORECASE,
        )
        if locus:
            chrom, pos, ref, alt = locus.groups()
            return "v.variant_key = ?", [variant_key(chrom, int(pos), ref, alt)]
        position = re.fullmatch(
            r"(?:chr)?([A-Za-z0-9]+)[:\-](\d+)", cleaned, re.IGNORECASE
        )
        if position:
            chrom, pos = position.groups()
            return "v.chrom = ? AND v.pos = ?", [normalize_chromosome(chrom), int(pos)]
        return None

    def query(self, payload: dict) -> dict:
        mode = str(payload.get("mode") or "variant")
        if mode not in {"variant", "gene", "gene_list", "region"}:
            raise ValueError("mode must be 'variant', 'gene', 'gene_list', or 'region'")
        limit = max(1, min(int(payload.get("limit") or 500), 5000))
        variant_conditions: list[str] = []
        variant_parameters: list = []
        annotation_conditions: list[str] = []
        annotation_parameters: list = []
        genotype_conditions: list[str] = []
        genotype_parameters: list = []

        if mode == "variant":
            query = str(payload.get("query") or "").strip()
            if not query:
                raise ValueError("an exact variant, locus, or rsID is required")
            parsed = self._parse_variant_query(query)
            if parsed:
                clause, values = parsed
                variant_conditions.append(clause)
                variant_parameters.extend(values)
            else:
                variant_conditions.append(
                    "(v.rsid = ? COLLATE NOCASE OR v.variant_key = ? COLLATE NOCASE)"
                )
                variant_parameters.extend([query, query])
        elif mode == "region":
            raw = str(payload.get("region") or payload.get("query") or "").strip()
            match = re.fullmatch(
                r"(?:chr)?([0-9]{1,2}|[XYM]|MT)\s*:\s*([\d,]+)\s*[-–]\s*([\d,]+)",
                raw, re.IGNORECASE,
            )
            if not match:
                raise ValueError(
                    "region must look like chrom:start-end, e.g. 1:117000000-117500000"
                )
            chrom = normalize_chromosome(match.group(1))
            start = int(match.group(2).replace(",", ""))
            end = int(match.group(3).replace(",", ""))
            if end < start:
                start, end = end, start
            if end - start > 5_000_000:
                raise ValueError(
                    "the region spans more than 5 Mb — narrow the window "
                    "(regulatory context rarely needs more than ±500 kb)"
                )
            variant_conditions.append("(v.chrom = ? AND v.pos BETWEEN ? AND ?)")
            variant_parameters.extend([chrom, start, end])
        elif mode == "gene_list":
            raw_genes = payload.get("genes")
            if isinstance(raw_genes, str):
                raw_genes = re.split(r"[\s,;]+", raw_genes)
            if not isinstance(raw_genes, list):
                raise ValueError("genes must be a list or whitespace/comma-separated text")
            genes = sorted({
                str(value).strip().upper() for value in raw_genes if str(value).strip()
            })
            if not genes:
                raise ValueError("provide at least one gene symbol")
            if len(genes) > 2000:
                raise ValueError(
                    f"{len(genes)} genes exceeds the 2000-gene query limit — split the list"
                )
            annotation_conditions.append(
                "a.gene IN (" + ",".join("?" for _ in genes) + ")"
            )
            annotation_parameters.extend(genes)
        else:
            gene = str(payload.get("gene") or payload.get("query") or "").strip().upper()
            if not gene:
                raise ValueError("gene is required")
            annotation_conditions.append("a.gene = ?")
            annotation_parameters.append(gene)

        # Qualifying-variant filters apply to the gene and region modes.
        if mode in ("gene", "gene_list", "region"):
            impacts = [
                str(value).upper() for value in payload.get("impacts", ["HIGH", "MODERATE"])
                if str(value).upper() in ALLOWED_IMPACTS
            ]
            if impacts:
                annotation_conditions.append(
                    "a.impact IN (" + ",".join("?" for _ in impacts) + ")"
                )
                annotation_parameters.extend(impacts)
            if payload.get("max_popmax") is not None:
                annotation_conditions.append(
                    "(a.gnomad_popmax IS NULL OR a.gnomad_popmax <= ?)"
                )
                annotation_parameters.append(float(payload["max_popmax"]))
            for key, column in (
                ("min_cadd", "a.cadd"),
                ("min_alpha_missense", "a.alpha_missense"),
                ("min_spliceai", "a.spliceai"),
            ):
                if payload.get(key) is not None:
                    annotation_conditions.append(f"{column} >= ?")
                    annotation_parameters.append(float(payload[key]))
            logofunc_class = str(payload.get("logofunc_class") or "").strip()
            if logofunc_class:
                if logofunc_class not in {"GOF", "LOF", "Neutral"}:
                    raise ValueError("logofunc_class must be GOF, LOF, or Neutral")
                logofunc_score_column = {
                    "GOF": "logofunc_gof",
                    "LOF": "logofunc_lof",
                    "Neutral": "logofunc_neutral",
                }[logofunc_class]
                clause = (
                    "EXISTS (SELECT 1 FROM cohort_annotations lf_filter "
                    "WHERE lf_filter.variant_id = a.variant_id "
                    "AND lf_filter.gene = a.gene "
                    "AND lf_filter.logofunc_prediction = ? "
                    "AND lf_filter.logofunc_match = 'allele_transcript_protein'"
                )
                annotation_parameters.append(logofunc_class)
                if payload.get("min_logofunc_probability") is not None:
                    clause += f" AND lf_filter.{logofunc_score_column} >= ?"
                    annotation_parameters.append(
                        float(payload["min_logofunc_probability"])
                    )
                annotation_conditions.append(clause + ")")
            if payload.get("mane_only", True):
                annotation_conditions.append(
                    "(a.mane = 1 OR (a.picked = 1 AND NOT EXISTS ("
                    "SELECT 1 FROM cohort_annotations preferred_mane "
                    "WHERE preferred_mane.variant_id = a.variant_id "
                    "AND preferred_mane.gene = a.gene "
                    "AND preferred_mane.mane = 1)))"
                )
            if payload.get("exclude_repeat", True):
                annotation_conditions.append("a.repeat_masker = 0")
            if payload.get("exclude_segdup", True):
                annotation_conditions.append("a.segdup = 0")
            if payload.get("clinvar_pathogenic_only", False):
                annotation_conditions.append(
                    "LOWER(a.clinvar) LIKE '%pathogenic%' "
                    "AND LOWER(a.clinvar) NOT LIKE '%conflict%'"
                )
            if payload.get("clinvar_conflict_pathogenic_only", False):
                annotation_conditions.append(
                    "LOWER(a.clinvar) LIKE '%conflict%' "
                    "AND LOWER(COALESCE(a.clinvar_conflicting, '')) "
                    "LIKE '%pathogenic%'"
                )
            if payload.get("exclude_confirmed_frame_restored", False):
                genotype_conditions.append(
                    "COALESCE(g.haplotype_frame_status, '') "
                    "!= 'FRAME_RESTORED_CONFIRMED'"
                )

        zygosity = str(payload.get("zygosity") or "all")
        if zygosity != "all":
            if zygosity not in {"heterozygous", "homozygous", "hemizygous"}:
                raise ValueError("unsupported zygosity filter")
            genotype_conditions.append("g.zygosity = ?")
            genotype_parameters.append(zygosity)

        analysis_scopes = [
            str(value) for value in payload.get("analysis_scopes", [])
            if str(value) in {"exome", "whole_genome"}
        ]
        if analysis_scopes:
            genotype_conditions.append(
                "f.analysis_scope IN (" + ",".join("?" for _ in analysis_scopes) + ")"
            )
            genotype_parameters.extend(analysis_scopes)
        profile_hashes = [
            str(value).strip() for value in payload.get("profile_hashes", [])
            if str(value).strip()
        ]
        if profile_hashes:
            genotype_conditions.append(
                "COALESCE(NULLIF(f.profile_hash, ''), "
                "f.import_profile || ':' || f.analysis_scope) IN ("
                + ",".join("?" for _ in profile_hashes) + ")"
            )
            genotype_parameters.extend(profile_hashes)

        variant_where = " AND ".join(variant_conditions) if variant_conditions else "1"
        annotation_where = (
            " AND ".join(annotation_conditions) if annotation_conditions else "1"
        )
        genotype_where = " AND ".join(genotype_conditions) if genotype_conditions else "1"
        parameters = (
            variant_parameters + annotation_parameters + genotype_parameters
        )
        partition = "a.variant_id, a.gene" if mode == "gene" else "a.variant_id"
        ranking = f"""
          ROW_NUMBER() OVER (
            PARTITION BY {partition}
            ORDER BY a.mane DESC,
              a.picked DESC,
              CASE a.impact
                WHEN 'HIGH' THEN 1 WHEN 'MODERATE' THEN 2
                WHEN 'LOW' THEN 3 WHEN 'MODIFIER' THEN 4 ELSE 5 END,
              a.id
          ) AS annotation_rank
        """
        base = f"""
          WITH matched_variants AS (
            SELECT v.* FROM cohort_variants v WHERE {variant_where}
          ),
          ranked AS (
            SELECT a.*, {ranking}
            FROM cohort_annotations a
            JOIN matched_variants mv ON mv.id = a.variant_id
            WHERE {annotation_where}
          ),
          logofunc AS (
            SELECT source.*,
              ROW_NUMBER() OVER (
                PARTITION BY source.variant_id, source.gene
                ORDER BY source.id
              ) AS logofunc_rank
            FROM cohort_annotations source
            WHERE source.logofunc_match = 'allele_transcript_protein'
          )
          SELECT
            v.variant_key, v.chrom, v.pos, v.ref, v.alt, v.rsid,
            v.original_assembly, v.original_chrom, v.original_pos,
            v.original_ref, v.original_alt, v.unscored_indel_reasons,
            a.gene, a.gene_id, a.transcript, a.hgvsc, a.hgvsp,
            a.consequence, a.impact, a.gnomad_popmax, a.cadd,
            a.alpha_missense, a.spliceai, a.promoterai,
            COALESCE(NULLIF(a.logofunc_prediction, ''), lf.logofunc_prediction) AS logofunc_prediction,
            COALESCE(a.logofunc_neutral, lf.logofunc_neutral) AS logofunc_neutral,
            COALESCE(a.logofunc_gof, lf.logofunc_gof) AS logofunc_gof,
            COALESCE(a.logofunc_lof, lf.logofunc_lof) AS logofunc_lof,
            MAX(a.logofunc_allele_available, COALESCE(lf.logofunc_allele_available, 0)) AS logofunc_allele_available,
            COALESCE(lf.logofunc_source_transcript, a.logofunc_source_transcript) AS logofunc_source_transcript,
            COALESCE(lf.logofunc_source_hgvsp, a.logofunc_source_hgvsp) AS logofunc_source_hgvsp,
            CASE WHEN lf.id IS NOT NULL AND a.id != lf.id
              THEN 'source_transcript_match_elsewhere'
              ELSE COALESCE(a.logofunc_match, lf.logofunc_match) END AS logofunc_match,
            a.clinvar,
            a.clinvar_conflicting, a.loftee,
            a.loftee_50bp, a.loftee_50bp_original, a.loftee_50bp_changed,
            a.ptc_distance, a.ptc_calc_status,
            a.mane, a.picked, a.repeat_masker, a.segdup,
            s.id AS sample_entry_id, s.name AS sample,
            f.id AS source_file_id, f.path AS source_path,
            f.import_profile, f.analysis_scope,f.profile_label,f.profile_hash,
            g.genotype, g.zygosity, g.phased, g.dp, g.gq,
            g.allele_balance, g.qual, g.haplotype_frame_status,
            g.haplotype_frame_partners, g.haplotype_protein_change,
            g.haplotype_transcript,
            COUNT(*) OVER (PARTITION BY s.id) AS sample_qualifying_variant_count
          FROM cohort_genotypes g
          JOIN matched_variants v ON v.id = g.variant_id
          JOIN ranked a ON a.variant_id = v.id AND a.annotation_rank = 1
          LEFT JOIN logofunc lf ON lf.variant_id = a.variant_id
            AND lf.gene = a.gene AND lf.logofunc_rank = 1
          JOIN cohort_samples s ON s.id = g.sample_id
          JOIN cohort_files f ON f.id = s.file_id
          WHERE {genotype_where}
        """
        gene_order = (
            "sample_qualifying_variant_count DESC, s.name,"
            if mode == "gene" else ""
        )
        ordered = base + f"""
          ORDER BY
            {gene_order}
            CASE a.impact
              WHEN 'HIGH' THEN 1 WHEN 'MODERATE' THEN 2
              WHEN 'LOW' THEN 3 WHEN 'MODIFIER' THEN 4 ELSE 5 END,
            COALESCE(a.gnomad_popmax, -1), v.chrom, v.pos, s.name
          LIMIT ?
        """
        with self._session() as connection:
            rows = [
                self._serialize_query_row(row)
                for row in connection.execute(ordered, (*parameters, limit)).fetchall()
            ]
            totals = connection.execute(
                f"""
                SELECT COUNT(*) AS n,
                       COUNT(DISTINCT sample) AS individuals,
                       COUNT(DISTINCT variant_key) AS variants
                FROM ({base})
                """,
                parameters,
            ).fetchone()
        represented_profiles = sorted({
            row.get("profile_hash") or f"{row.get('import_profile')}:{row.get('analysis_scope')}"
            for row in rows
        })
        return {
            "mode": mode,
            "total": totals["n"],
            "limit": limit,
            "truncated": totals["n"] > limit,
            "individuals": totals["individuals"],
            "variants": totals["variants"],
            "rows": rows,
            "represented_profiles": represented_profiles,
            "comparability_warning": (
                "Carrier findings span multiple import profiles. Samples not represented "
                "under a comparable assay and retention profile are not interpreted as negative."
                if len(represented_profiles) > 1 else
                "Absence from this candidate index is not evidence that an individual lacks the variant."
            ),
        }

    def variant_detail(self, variant_key_value: str) -> dict:
        """Return all stored transcripts and carriers for one exact allele."""
        cleaned = str(variant_key_value or "").strip()
        parsed = self._parse_variant_query(cleaned)
        if not parsed or parsed[0] != "v.variant_key = ?":
            raise ValueError("variant_key must be CHROM:POS:REF:ALT")
        canonical_key = parsed[1][0]
        result = self.query({
            "mode": "variant", "query": canonical_key, "limit": 5_000,
        })
        if not result["rows"]:
            raise ValueError("variant is not present in the cohort index")
        with self._session() as connection:
            annotations = connection.execute(
                """
                SELECT a.gene, a.gene_id, a.transcript, a.hgvsc, a.hgvsp,
                       a.consequence, a.impact, a.gnomad_popmax, a.cadd,
                       a.alpha_missense, a.spliceai, a.promoterai,
                       a.logofunc_prediction, a.logofunc_neutral,
                       a.logofunc_gof, a.logofunc_lof,
                       a.logofunc_allele_available,
                       a.logofunc_source_transcript, a.logofunc_source_hgvsp,
                       a.logofunc_match, a.clinvar,
                       a.clinvar_conflicting, a.loftee, a.loftee_50bp,
                       a.loftee_50bp_original, a.loftee_50bp_changed,
                       a.ptc_distance, a.ptc_calc_status, a.mane, a.picked,
                       a.repeat_masker, a.segdup
                FROM cohort_annotations a
                JOIN cohort_variants v ON v.id = a.variant_id
                WHERE v.variant_key = ?
                ORDER BY a.mane DESC, a.picked DESC,
                  CASE a.impact
                    WHEN 'HIGH' THEN 1 WHEN 'MODERATE' THEN 2
                    WHEN 'LOW' THEN 3 WHEN 'MODIFIER' THEN 4 ELSE 5 END,
                  a.gene, a.transcript
                """,
                (canonical_key,),
            ).fetchall()
        result["annotations"] = [
            self._serialize_annotation_row(row) for row in annotations
        ]
        return result

    def review_records(self, selections: list[dict]) -> dict:
        """Fetch complete exact-allele records from indexed source VCFs.

        SQLite remains the cohort search index. Full INFO, CSQ, and FORMAT
        evidence is retrieved only when a user opens selected calls in REVIEW.
        The returned VCF snippets contain only requested sample columns and
        exact records, keeping the response independent of source VCF size.
        """
        if not isinstance(selections, list):
            raise ValueError("selections must be a list")
        if not selections:
            raise ValueError("select at least one cohort carrier for review")
        if len(selections) > 5_000:
            raise ValueError("a single cohort review is limited to 5,000 carrier calls")

        requested: dict[tuple[str, int], dict] = {}
        for selection in selections:
            if not isinstance(selection, dict):
                raise ValueError("each review selection must be an object")
            cleaned = str(selection.get("variant_key") or "").strip()
            parsed = self._parse_variant_query(cleaned)
            if not parsed or parsed[0] != "v.variant_key = ?":
                raise ValueError("variant_key must be CHROM:POS:REF:ALT")
            try:
                sample_entry_id = int(selection.get("sample_entry_id"))
            except (TypeError, ValueError) as error:
                raise ValueError("sample_entry_id must be an integer") from error
            if sample_entry_id < 1:
                raise ValueError("sample_entry_id must be a positive integer")
            canonical_key = parsed[1][0]
            requested[(canonical_key, sample_entry_id)] = {
                "variant_key": canonical_key,
                "sample_entry_id": sample_entry_id,
            }

        rows: list[sqlite3.Row] = []
        requested_pairs = sorted(requested)
        with self._session() as connection:
            for offset in range(0, len(requested_pairs), 300):
                batch = requested_pairs[offset:offset + 300]
                requested_values = ",".join("(?, ?)" for _ in batch)
                parameters = [value for pair in batch for value in pair]
                rows.extend(connection.execute(
                    f"""
                    WITH requested(variant_key, sample_entry_id) AS (
                        VALUES {requested_values}
                    )
                    SELECT v.variant_key, v.chrom, v.pos, v.ref, v.alt,
                           s.id AS sample_entry_id, s.name AS sample,
                           f.id AS source_file_id, f.path AS source_path,
                           f.prepared_path, f.index_path
                    FROM requested request
                    JOIN cohort_variants v
                      ON v.variant_key = request.variant_key
                    JOIN cohort_genotypes g
                      ON g.variant_id = v.id
                    JOIN cohort_samples s ON s.id = g.sample_id
                      AND s.id = request.sample_entry_id
                    JOIN cohort_files f ON f.id = s.file_id
                    """,
                    parameters,
                ).fetchall())

        matched = {
            (row["variant_key"], row["sample_entry_id"]): row
            for row in rows
            if (row["variant_key"], row["sample_entry_id"]) in requested
        }
        missing_index_entries = sorted(set(requested) - set(matched))
        warnings = [
            f"{variant}: sample entry {sample_id} is no longer present in the cohort index"
            for variant, sample_id in missing_index_entries
        ]
        grouped: dict[int, list[sqlite3.Row]] = {}
        for row in matched.values():
            grouped.setdefault(row["source_file_id"], []).append(row)

        files: list[dict] = []
        resolved: set[tuple[str, int]] = set()
        if self.hts_backend is None:
            warnings.append(
                "bcftools/tabix is unavailable; full source annotations could not be loaded"
            )
        else:
            for source_file_id, group in grouped.items():
                source_path = Path(group[0]["source_path"])
                prepared_value = group[0]["prepared_path"]
                prepared_path = Path(prepared_value) if prepared_value else source_path
                if not prepared_path.is_file():
                    warnings.append(
                        f"{source_path.name}: indexed review VCF is missing; refresh this cohort source"
                    )
                    continue
                saved_index = (
                    Path(group[0]["index_path"])
                    if group[0]["index_path"] else None
                )
                candidate_indexes = [
                    saved_index,
                    Path(f"{prepared_path}.tbi"),
                    Path(f"{prepared_path}.csi"),
                ]
                if not any(path and path.is_file() for path in candidate_indexes):
                    warnings.append(
                        f"{source_path.name}: tabix/CSI index is missing; refresh this cohort source"
                    )
                    continue

                try:
                    header = read_vcf_header(prepared_path)
                    header_lines = read_vcf_header_lines(prepared_path)
                    header_columns = header_lines[-1].split("\t")
                    sample_column = {
                        sample: index + 9 for index, sample in enumerate(header.samples)
                    }
                    selected_samples = []
                    for row in sorted(group, key=lambda value: value["sample_entry_id"]):
                        if row["sample"] not in sample_column:
                            warnings.append(
                                f"{source_path.name}: sample {row['sample']} is absent from the indexed VCF header"
                            )
                            continue
                        if row["sample"] not in selected_samples:
                            selected_samples.append(row["sample"])
                    if not selected_samples:
                        continue

                    available_contigs = list(header.contigs)
                    if not available_contigs:
                        available_contigs = self.hts_backend.list_contigs(prepared_path)
                    contig_by_normalized = {
                        normalize_chromosome(contig): contig
                        for contig in available_contigs
                    }
                    targets: dict[str, tuple[str, int, str, str]] = {}
                    for row in group:
                        targets[row["variant_key"]] = (
                            row["chrom"], row["pos"], row["ref"].upper(),
                            row["alt"].upper(),
                        )
                    regions = sorted({
                        f"{contig_by_normalized.get(chrom, chrom)}:{pos}-{pos}"
                        for chrom, pos, _, _ in targets.values()
                    })
                    records: list[str] = []
                    found_variants: set[str] = set()
                    for line in self.hts_backend.iter_records(prepared_path, regions):
                        columns = line.rstrip("\r\n").split("\t")
                        if len(columns) < 9 + len(header.samples):
                            continue
                        try:
                            record_pos = int(columns[1])
                        except ValueError:
                            continue
                        record_chrom = normalize_chromosome(columns[0])
                        record_ref = columns[3].upper()
                        record_alts = {alt.upper() for alt in columns[4].split(",")}
                        matching_keys = {
                            key for key, (chrom, pos, ref, alt) in targets.items()
                            if record_chrom == chrom and record_pos == pos
                            and record_ref == ref and alt in record_alts
                        }
                        if not matching_keys:
                            continue
                        found_variants.update(matching_keys)
                        projected = columns[:9] + [
                            columns[sample_column[sample]] for sample in selected_samples
                        ]
                        records.append("\t".join(projected))

                    projected_header = [*header_lines[:-1], "\t".join(
                        header_columns[:9] + selected_samples
                    )]
                    if records:
                        review_name = f"cohort-{source_file_id}-{source_path.name}"
                        for suffix in (".vcf.gz", ".vcf.bgz", ".vcf", ".gz", ".bgz"):
                            if review_name.lower().endswith(suffix):
                                review_name = review_name[:-len(suffix)]
                                break
                        review_name += ".vcf"
                        file_selections = []
                        for row in group:
                            pair = (row["variant_key"], row["sample_entry_id"])
                            if (
                                row["variant_key"] in found_variants
                                and row["sample"] in selected_samples
                            ):
                                resolved.add(pair)
                                file_selections.append({
                                    "variant_key": row["variant_key"],
                                    "sample_entry_id": row["sample_entry_id"],
                                    "sample": row["sample"],
                                })
                        files.append({
                            "source_file_id": source_file_id,
                            "source_path": str(source_path),
                            "prepared_path": str(prepared_path),
                            "name": review_name,
                            "selections": file_selections,
                            "vcf": "\n".join([*projected_header, *records, ""]),
                        })
                    for key in sorted(set(targets) - found_variants):
                        warnings.append(
                            f"{source_path.name}: exact allele {key} was not found by tabix"
                        )
                except (OSError, RuntimeError, ValueError) as error:
                    warnings.append(
                        f"{source_path.name}: source annotations could not be loaded ({error})"
                    )

        unresolved = [
            requested[pair] for pair in sorted(set(requested) - resolved)
        ]
        return {
            "requested": len(requested),
            "resolved": len(resolved),
            "files": files,
            "unresolved": unresolved,
            "warnings": warnings,
        }

    def sample_review_files(self, sample_ids: list[int]) -> dict:
        """Return complete stored review sets for selected cohort samples.

        This projects only the selected sample columns from each indexed source
        and retains PASS records where at least one selected sample carries an
        alternate allele. A hard carrier-count bound prevents a full WGS index
        from being materialized in browser memory.
        """
        if not isinstance(sample_ids, list):
            raise ValueError("sample_ids must be a list")
        try:
            selected = sorted({int(value) for value in sample_ids})
        except (TypeError, ValueError) as error:
            raise ValueError("sample_ids must contain integers") from error
        if not selected:
            raise ValueError("select at least one cohort individual")
        if len(selected) > 50:
            raise ValueError("a single browser review is limited to 50 sample entries")

        placeholders = ",".join("?" for _ in selected)
        with self._session() as connection:
            rows = connection.execute(
                f"""
                SELECT s.id AS sample_entry_id, s.name AS sample,
                       s.file_id AS source_file_id,
                       f.path AS source_path, f.prepared_path,
                       f.import_profile, f.analysis_scope,
                       f.prefilter_options, f.imported_at,
                       COUNT(g.id) AS carrier_observations
                FROM cohort_samples s
                JOIN cohort_files f ON f.id = s.file_id
                LEFT JOIN cohort_genotypes g ON g.sample_id = s.id
                WHERE s.id IN ({placeholders})
                GROUP BY s.id
                ORDER BY f.id, s.name
                """,
                selected,
            ).fetchall()
        if len(rows) != len(selected):
            found = {row["sample_entry_id"] for row in rows}
            missing = [value for value in selected if value not in found]
            raise ValueError(
                "sample entries were not found: " + ", ".join(map(str, missing))
            )

        total_carriers = sum(int(row["carrier_observations"] or 0) for row in rows)
        if total_carriers > MAX_BROWSER_SAMPLE_REVIEW_CARRIERS:
            raise ValueError(
                f"the selected stored review sets contain {total_carriers:,} carrier "
                f"observations; browser review is limited to "
                f"{MAX_BROWSER_SAMPLE_REVIEW_CARRIERS:,}. Review the matched findings "
                "instead, or index the WGS using the compact candidate profile"
            )

        grouped: dict[int, list[sqlite3.Row]] = {}
        for row in rows:
            grouped.setdefault(row["source_file_id"], []).append(row)

        files: list[dict] = []
        warnings: list[str] = []
        total_records = 0
        for source_file_id, group in grouped.items():
            source_path = Path(group[0]["source_path"])
            prepared_value = group[0]["prepared_path"]
            prepared_path = Path(prepared_value) if prepared_value else source_path
            if not prepared_path.is_file():
                raise ValueError(
                    f"{source_path.name}: the stored review VCF is missing; "
                    "refresh this cohort source before loading the individual"
                )
            header = read_vcf_header(prepared_path)
            header_lines = read_vcf_header_lines(prepared_path)
            header_columns = header_lines[-1].split("\t")
            sample_column = {
                sample: index + 9 for index, sample in enumerate(header.samples)
            }
            selected_samples = [
                row["sample"] for row in group if row["sample"] in sample_column
            ]
            missing_samples = [
                row["sample"] for row in group if row["sample"] not in sample_column
            ]
            if missing_samples:
                raise ValueError(
                    f"{source_path.name}: selected samples are absent from the stored "
                    f"VCF header: {', '.join(missing_samples)}"
                )

            records: list[str] = []
            opener = gzip.open if prepared_path.name.lower().endswith((".gz", ".bgz")) else open
            with opener(prepared_path, "rt", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    if line.startswith("#") or not line.strip():
                        continue
                    columns = line.rstrip("\r\n").split("\t")
                    if len(columns) < 9 + len(header.samples) or columns[6] not in ("PASS", "."):
                        continue
                    alternate_count = len(columns[4].split(","))
                    carries = False
                    for sample in selected_samples:
                        sample_value = columns[sample_column[sample]]
                        if any(
                            parse_genotype(columns[8], sample_value, alt_index)["carrier"]
                            for alt_index in range(alternate_count)
                        ):
                            carries = True
                            break
                    if not carries:
                        continue
                    records.append("\t".join(
                        columns[:9] + [columns[sample_column[sample]] for sample in selected_samples]
                    ))
                    if total_records + len(records) > MAX_BROWSER_SAMPLE_REVIEW_CARRIERS:
                        raise ValueError(
                            "the projected review exceeds the browser record limit; "
                            "review the matched findings instead"
                        )

            total_records += len(records)
            try:
                prefilter_options = json.loads(group[0]["prefilter_options"] or "{}")
            except json.JSONDecodeError:
                prefilter_options = {}
                warnings.append(
                    f"{source_path.name}: stored prefilter settings could not be decoded"
                )
            projected_header = [*header_lines[:-1], "\t".join(
                header_columns[:9] + selected_samples
            )]
            review_name = f"cohort-samples-{source_file_id}-{source_path.name}"
            for suffix in (".vcf.gz", ".vcf.bgz", ".vcf", ".gz", ".bgz"):
                if review_name.lower().endswith(suffix):
                    review_name = review_name[:-len(suffix)]
                    break
            files.append({
                "source_file_id": source_file_id,
                "source_path": str(source_path),
                "prepared_path": str(prepared_path),
                "name": review_name + ".vcf",
                "import_profile": group[0]["import_profile"],
                "analysis_scope": group[0]["analysis_scope"],
                "prefilter_options": prefilter_options,
                "imported_at": group[0]["imported_at"],
                "record_count": len(records),
                "samples": [
                    {
                        "sample_entry_id": row["sample_entry_id"],
                        "sample": row["sample"],
                        "carrier_observations": row["carrier_observations"],
                    }
                    for row in group
                ],
                "vcf": "\n".join([*projected_header, *records, ""]),
            })

        return {
            "sample_entries": len(rows),
            "carrier_observations": total_carriers,
            "records": total_records,
            "analysis_scope": (
                "whole_genome"
                if any(
                    row["analysis_scope"] == "whole_genome"
                    or row["import_profile"] == "prefiltered"
                    for row in rows
                )
                else "exome"
            ),
            "files": files,
            "warnings": warnings,
        }

    @staticmethod
    def _serialize_query_row(row: sqlite3.Row) -> dict:
        result = dict(row)
        for key in (
            "mane", "picked", "repeat_masker", "segdup", "phased",
            "loftee_50bp_changed", "logofunc_allele_available",
        ):
            result[key] = bool(result[key])
        return result

    @staticmethod
    def _serialize_annotation_row(row: sqlite3.Row) -> dict:
        result = dict(row)
        for key in (
            "mane", "picked", "repeat_masker", "segdup",
            "loftee_50bp_changed", "logofunc_allele_available",
        ):
            result[key] = bool(result[key])
        return result
