#!/usr/bin/env python3
"""Persistent genotype-first cohort index for VEP-annotated VCF files.

The index is intentionally local and dependency-free. VCFs are parsed once
into SQLite, after which exact-variant and qualifying gene queries do not need
to reopen hundreds of source files.
"""

from __future__ import annotations

import gzip
import hashlib
import ipaddress
import json
import math
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
from urllib.parse import unquote, urlsplit

from pipeline.predictor_registry import (
    MatchDimension,
    MatchScope,
    MetricType,
    ScoreDirection,
    TranscriptVersionPolicy,
    load_registry,
)
from pipeline.vcf_assembly import detect_vcf_assembly


EMPTY = {"", ".", "-"}
IMPACT_ORDER = {"HIGH": 1, "MODERATE": 2, "LOW": 3, "MODIFIER": 4, "UNKNOWN": 5}
ALLOWED_IMPACTS = set(IMPACT_ORDER)
DEFAULT_INDEX_READERS = 4
DEFAULT_STAGE_BATCH_RECORDS = 2_000
MAX_STAGE_PREDICTION_OBSERVATIONS = 25_000
MAX_STAGE_PREDICTION_VALUES = 75_000
MAX_BROWSER_SAMPLE_REVIEW_CARRIERS = 200_000
# Stay below SQLite's historical 999-host-parameter default. Query APIs may
# return 10,000 observations, so hydrate typed values in portable chunks.
SQLITE_VARIABLE_CHUNK = 900
PREDICTOR_REGISTRY = load_registry()
EMBEDDED_VCF_PROVIDER = "GUIDE-IEI embedded VCF"
EMBEDDED_VCF_RELEASE = "embedded-vcf"


def _public_source_uri(source_uri: str) -> str:
    """Validate provenance URLs without ever accepting access credentials."""
    if not isinstance(source_uri, str):
        raise ValueError("source_uri must be a string")
    if not source_uri:
        return ""
    parsed_uri = urlsplit(source_uri)
    if (
        parsed_uri.scheme != "https"
        or not parsed_uri.hostname
        or parsed_uri.username
        or parsed_uri.password
        or parsed_uri.query
        or parsed_uri.fragment
    ):
        raise ValueError(
            "source_uri must be a public credential-free HTTPS URL"
        )
    host = parsed_uri.hostname.lower()
    if host == "localhost" or host.endswith(".local"):
        raise ValueError("source_uri must not point to a private host")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address and (
        address.is_private or address.is_loopback
        or address.is_link_local or address.is_reserved
    ):
        raise ValueError("source_uri must not point to a private host")
    decoded_path = unquote(parsed_uri.path)
    if re.search(
        r"(?i)(?:^|[/;])(?:token|api[_-]?key|secret|signature|credential|"
        r"private[_-]?account[_-]?key)"
        r"(?:[=/;_-]|$)",
        decoded_path,
    ):
        raise ValueError("source_uri path must not contain credentials")
    for opaque in re.findall(
        r"(?i)/(?:downloads?|access|auth)/([A-Za-z0-9_-]{20,})(?:/|$)",
        decoded_path,
    ):
        if (
            re.search(r"[a-z]", opaque)
            and re.search(r"[A-Z]", opaque)
            and re.search(r"[0-9]", opaque)
        ):
            raise ValueError("source_uri path must not contain credentials")
    return source_uri


def _embedded_vcf_release_identity(
    source_file_id: int, content_sha256: str = "", content_probe: str = "",
) -> tuple[str, str, str]:
    if content_sha256:
        checksum_algorithm = "sha256"
        checksum = content_sha256.lower()
    elif content_probe:
        checksum_algorithm = "iei-content-probe-sha256"
        checksum = content_probe.lower()
    else:
        checksum_algorithm = ""
        checksum = ""
    release_version = (
        f"{EMBEDDED_VCF_RELEASE}-{checksum[:16]}"
        if checksum
        else f"{EMBEDDED_VCF_RELEASE}-unverified-{source_file_id}"
    )
    return release_version, checksum_algorithm, checksum


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


def _content_probe(path: Path, block: int = 65536) -> str:
    """Cheap content fingerprint: head, tail, and interior stripes.

    Head+tail alone left a large blind window (a >128KiB file changed only
    in the middle probed identically); six evenly spaced interior stripes
    shrink that window for ~512KiB of I/O regardless of file size. Not a
    substitute for the full sha256 used where one is already computed — a
    defense for the paths that cannot afford one.
    """
    try:
        size = path.stat().st_size
        digest = hashlib.sha256()
        digest.update(str(size).encode())
        with path.open("rb") as handle:
            digest.update(handle.read(block))
            if size > 2 * block:
                interior = size - 2 * block
                stripes = 6
                for stripe in range(1, stripes + 1):
                    offset = block + (interior * stripe) // (stripes + 1)
                    handle.seek(offset)
                    digest.update(handle.read(min(block, max(0, size - offset))))
            if size > block:
                handle.seek(max(block, size - block))
                digest.update(handle.read(block))
        return digest.hexdigest()[:24]
    except OSError:
        return ""


_FULL_HASH_LIMIT = 8 << 30  # 8 GiB


def _copy_with_sha256(source: Path, destination: Path) -> str:
    """Copy source to destination, hashing the streamed bytes — the digest
    describes exactly the bytes the destination holds, at zero extra I/O
    over the copy itself, with no size limit."""
    digest = hashlib.sha256()
    with source.open("rb") as reader, destination.open("wb") as writer:
        while block := reader.read(4 * 1024 * 1024):
            digest.update(block)
            writer.write(block)
    # No copystat: the copy's own mtime is its creation time, which the
    # snapshot cleanup age-gate relies on to tell a crash leftover from a
    # snapshot serving a running import.
    return digest.hexdigest()


def _full_content_sha256(path: Path, limit: int = _FULL_HASH_LIMIT) -> str:
    """Complete sha256 for files small enough to hash in tolerable time.

    The stripe probe leaves interior blind windows by design; a full read
    closes them. Measured cost is ~0.5 s/GiB on a native interpreter
    (~3 s/GiB under a Rosetta/OpenSSL-1.1 Python), so the 8 GiB limit
    covers every exome-scale artifact — multi-sample annotated VCFs
    included — at single-digit seconds per explicit import. Files above
    the limit return "" and stay on the probe-only rule — the documented
    multi-GB whole-genome tradeoff. Note: changing this limit changes the
    hash for files in the affected band, so their prepare-cache
    fingerprints miss once and rebuild — a one-time upgrade cost.
    """
    try:
        if path.stat().st_size > limit:
            return ""
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while block := handle.read(4 * 1024 * 1024):
                digest.update(block)
        return digest.hexdigest()
    except OSError:
        return ""


def normalize_chromosome(value: str) -> str:
    chrom = value.strip()
    if chrom.lower().startswith("chr"):
        chrom = chrom[3:]
    return "MT" if chrom.upper() in {"M", "MT"} else chrom.upper()


def variant_key(chrom: str, pos: int, ref: str, alt: str) -> str:
    return f"{normalize_chromosome(chrom)}:{pos}:{ref.upper()}:{alt.upper()}"


def decode(value: str | None) -> str:
    # VEP percent-encodes CSQ special characters but a literal "+" is data —
    # splice HGVS like c.300+1G>C. Form-decoding "+" to a space corrupted
    # every intronic coordinate stored in the cohort database.
    return unquote(value or "")


def _prediction_dimension_missing(
    value: object, dimension: MatchDimension | None = None,
) -> bool:
    if value is None or not isinstance(value, str):
        return value is None
    cleaned = value.strip()
    return cleaned in {"", "."} or (
        cleaned == "-" and dimension is not MatchDimension.STRAND
    )


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


_CLINVAR_AA_ALLELE_FIELDS = (
    "ClinVar_path_aa_match",
    "ClinVar_path_aa_change_match",
)


def _clingen_assertions_for_alt(
    raw: str, *, alt: str, alt_index: int, alts: tuple[str, ...],
) -> list[str]:
    """Select this ALT's ClinGen assertions from old and new encodings.

    The legacy postprocessor writes one comma-separated record-level list
    whose tokens begin with ALT.  The planned Number=A producer writes one
    comma slot per ALT and joins multiple same-ALT assertions with ``&``.
    Supporting both here prevents record-level evidence and counts from being
    copied to every allele of a multi-ALT record.
    """
    if not raw or raw in EMPTY:
        return []
    comma_slots = raw.split(",")
    number_a_shape = len(alts) > 1 and len(comma_slots) == len(alts)
    all_tokens = [
        token
        for slot in comma_slots
        for token in slot.split("&")
    ]
    # Both the legacy record-level encoding and the current Number=A
    # encoding carry ALT as the first pipe-delimited token. Prefer that
    # explicit identity over an ambiguous comma count: a legacy two-ALT row
    # may legitimately contain exactly two assertions for the first ALT.
    has_explicit_alt = any(
        token.partition("|")[1]
        and decode(token.partition("|")[0]) in alts
        for token in all_tokens
    )
    candidates = (
        all_tokens
        if has_explicit_alt
        else comma_slots[alt_index].split("&")
        if number_a_shape and alt_index < len(comma_slots)
        else all_tokens
    )
    selected: list[str] = []
    for token in candidates:
        if not token or token in EMPTY:
            continue
        leading_alt, separator, _ = token.partition("|")
        decoded_leading = decode(leading_alt)
        if (
            not separator
            or decoded_leading == alt
            or len(alts) == 1
            or (
                not has_explicit_alt
                and number_a_shape
                and decoded_leading not in alts
            )
        ):
            selected.append(token)
    return selected


def _prediction_info_for_alt(
    info: dict[str, str], *, alt: str, alt_index: int, alts: tuple[str, ...],
) -> dict[str, str]:
    """Return INFO fields whose predictor evidence belongs to one ALT."""
    selected = dict(info)
    for key in _CLINVAR_AA_ALLELE_FIELDS:
        if key in info:
            selected[key] = allele_info_value(info, key, alt_index)
    if "ClinGen_ERepo" in info:
        assertions = _clingen_assertions_for_alt(
            info["ClinGen_ERepo"],
            alt=alt,
            alt_index=alt_index,
            alts=alts,
        )
        selected["ClinGen_ERepo"] = ",".join(assertions)
        selected["ClinGen_ERepo_count"] = (
            str(len(assertions)) if assertions else ""
        )
    return selected


def _prediction_annotation_records(
    prediction_info: dict[str, str], consequences: list[dict[str, str]],
) -> list[dict[str, str]]:
    if not consequences:
        return [prediction_info]
    gene_ids_by_symbol = {
        consequence.get("SYMBOL", "").upper(): consequence.get("Gene", "")
        for consequence in consequences
        if consequence.get("SYMBOL") and consequence.get("Gene")
    }
    records: list[dict[str, str]] = []
    for consequence in consequences:
        record = {**prediction_info, **consequence}
        source_gene = first(record, ("SpliceAI_pred_SYMBOL",))
        if source_gene:
            record["_SpliceAI_source_gene_id"] = gene_ids_by_symbol.get(
                source_gene.upper(), ""
            )
        records.append(record)
    return records


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
        "phase_set": first(fields, ("PS", "PID")),
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


def _vep_alt_allele(ref: str, alt: str) -> str:
    """Return VEP's minimized CSQ Allele representation for a small variant."""
    if not re.fullmatch(r"[ACGTN]+", ref, re.IGNORECASE) or not re.fullmatch(
        r"[ACGTN]+", alt, re.IGNORECASE
    ):
        return alt
    ref_sequence = ref.upper()
    alt_sequence = alt.upper()
    while ref_sequence and alt_sequence and ref_sequence[-1] == alt_sequence[-1]:
        ref_sequence = ref_sequence[:-1]
        alt_sequence = alt_sequence[:-1]
    while ref_sequence and alt_sequence and ref_sequence[0] == alt_sequence[0]:
        ref_sequence = ref_sequence[1:]
        alt_sequence = alt_sequence[1:]
    return alt_sequence or "-"


def _consequence_matches_alt(
    consequence: dict[str, str], *, ref: str, alts: tuple[str, ...], alt_index: int,
) -> bool:
    """Attribute a CSQ entry without borrowing an ambiguous minimized allele."""
    allele_number = consequence.get("ALLELE_NUM", "")
    if allele_number.isdigit():
        return int(allele_number) == alt_index + 1
    allele = consequence.get("Allele", "")
    if not allele:
        return True
    if allele.upper() == alts[alt_index].upper():
        return True
    minimized_alts = tuple(_vep_alt_allele(ref, alt) for alt in alts)
    minimized = minimized_alts[alt_index]
    return (
        allele.upper() == minimized.upper()
        and sum(candidate.upper() == minimized.upper() for candidate in minimized_alts)
        == 1
    )


_VARIANT_MATCH_DIMENSIONS = {
    MatchDimension.CHROMOSOME,
    MatchDimension.POSITION,
    MatchDimension.REFERENCE,
    MatchDimension.ALTERNATE,
}
_GENOMIC_MATCH_DIMENSIONS = {
    *_VARIANT_MATCH_DIMENSIONS,
    MatchDimension.START,
    MatchDimension.END,
}
_ANNOTATION_BOUND_SCOPES = {
    MatchScope.ALLELE_TRANSCRIPT,
    MatchScope.ALLELE_TRANSCRIPT_PROTEIN,
    MatchScope.ALLELE_TRANSCRIPT_TSS_STRAND,
    MatchScope.TRANSCRIPT_CONSEQUENCE,
    MatchScope.GENE_PROTEIN_RESIDUE,
}


def _promoterai_strand(record: dict[str, str]) -> str:
    """Normalize strand, using legacy VEP STRAND before returning invalid input."""
    encoding = {"+": "+", "1": "+", "-": "-", "-1": "-"}
    invalid = ""
    for field in ("PromoterAI_strand", "STRAND"):
        raw = decode(record.get(field, "")).strip()
        if not raw or raw == ".":
            continue
        strands = {
            encoding[token]
            for item in raw.replace(",", "&").split("&")
            if (token := item.strip()) in encoding
        }
        if len(strands) == 1:
            return strands.pop()
        invalid = invalid or raw
    return invalid


def _registry_metric_value(record: dict[str, str], metric) -> object | None:
    if metric.field == "PromoterAI_strand":
        return _promoterai_strand(record) or None
    raw = record.get(metric.field, "")
    if raw in EMPTY:
        return None
    if metric.value_type in {MetricType.FLOAT, MetricType.INTEGER}:
        values = [
            value
            for item in raw.replace("&", ",").split(",")
            if (value := parse_number(item)) is not None
        ]
        if not values:
            return None
        if metric.direction is ScoreDirection.LOWER:
            value = min(values)
        elif metric.direction is ScoreDirection.ABSOLUTE:
            value = max(values, key=abs)
        elif metric.direction is ScoreDirection.HIGHER:
            value = max(values)
        else:
            value = values[0]
        if metric.value_type is MetricType.INTEGER:
            return int(value) if value.is_integer() else None
        return value
    value = decode(raw)
    if metric.value_type is MetricType.BOOLEAN:
        tokens = [
            decode(token).strip().lower()
            for token in raw.replace("&", ",").split(",")
        ]
        true_tokens = {"1", "true", "yes", "y", "on"}
        false_tokens = {"0", "false", "no", "n", "off"}
        if tokens and all(token in true_tokens for token in tokens):
            return True
        if tokens and all(token in false_tokens for token in tokens):
            return False
        return None
    return value


def _stable_transcript(value: str, policy: TranscriptVersionPolicy) -> str:
    if policy is TranscriptVersionPolicy.EXACT:
        return value
    return value.split(".", 1)[0]


def _protein_position_from_change(value: str) -> str:
    if not value or (":" not in value and value.upper().startswith("ENSP")):
        return ""
    protein_change = value.rsplit(":", 1)[-1]
    match = re.search(r"(?:p\.)?[A-Za-z*?]*(\d+)", protein_change)
    return match.group(1) if match else ""


def _prediction_match_status(
    predictor_id: str,
    scope: MatchScope,
    values: dict[str, object],
    provenance: dict[str, object],
) -> str:
    if predictor_id == "funcvep":
        source_status = str(provenance.get("match_status") or "").lower()
        if source_status in {"exact", "partial", "ambiguous", "unmatched"}:
            return source_status
        if provenance.get("match") == scope.value:
            return "exact"
        if any(metric in values for metric in ("cti", "cte", "sp")):
            return "exact"
        return "partial" if (
            values.get("allele_available")
            or provenance.get("allele_available")
        ) else "unmatched"
    if predictor_id == "logofunc":
        if provenance.get("match") == scope.value:
            return "exact"
        if values.get("allele_available") or provenance.get("allele_available"):
            return "partial"
        return "partial" if values or provenance else "unmatched"
    if predictor_id == "promoterai":
        match = str(provenance.get("match") or "").lower()
        if "score" in values and match in {
            scope.value,
            "exact_version",
            "stable_id",
            "stable_transcript_id",
        }:
            return "exact"
        return "partial" if values or provenance else "unmatched"
    return "exact" if values or provenance else "unmatched"


def _prediction_target(
    record: dict[str, str], annotation: dict, annotator, values: dict[str, object],
) -> dict[str, object]:
    target: dict[str, object] = {}
    for dimension in annotator.match.dimensions:
        if dimension in _VARIANT_MATCH_DIMENSIONS:
            continue
        if dimension is MatchDimension.ENSEMBL_GENE:
            target[dimension.value] = annotation["gene_id"]
        elif dimension is MatchDimension.GENE_SYMBOL:
            target[dimension.value] = annotation["gene"]
        elif dimension is MatchDimension.ENSEMBL_TRANSCRIPT:
            target[dimension.value] = _stable_transcript(
                annotation["transcript"], annotator.match.transcript_version
            )
        elif dimension is MatchDimension.CONSEQUENCE:
            target[dimension.value] = annotation["consequence"]
        elif dimension is MatchDimension.PROTEIN_POSITION:
            protein_position = first(record, ("Protein_position",))
            if not protein_position:
                protein_position = _protein_position_from_change(
                    annotation["hgvsp"]
                )
            target[dimension.value] = protein_position
        elif dimension is MatchDimension.AMINO_ACID_CHANGE:
            target[dimension.value] = (
                first(record, ("Amino_acids",)) or annotation["hgvsp"]
            )
        elif dimension is MatchDimension.TSS:
            target[dimension.value] = first(record, ("PromoterAI_TSS",))
        elif dimension is MatchDimension.STRAND:
            target[dimension.value] = _promoterai_strand(record)
        elif dimension is MatchDimension.START:
            target[dimension.value] = first(record, ("START", "Start"))
        elif dimension is MatchDimension.END:
            target[dimension.value] = first(record, ("END", "End"))
    return target


def _canonical_prediction_target_key(
    annotator,
    target: dict[str, object],
    *,
    chrom: str,
    pos: int,
    ref: str,
    alt: str,
    sample: str = "",
) -> str:
    genomic: dict[MatchDimension, object] = {
        MatchDimension.CHROMOSOME: normalize_chromosome(chrom),
        MatchDimension.POSITION: int(pos),
        MatchDimension.REFERENCE: ref.upper(),
        MatchDimension.ALTERNATE: alt.upper(),
        MatchDimension.SAMPLE: sample or str(target.get("sample") or ""),
    }
    for dimension in (MatchDimension.START, MatchDimension.END):
        raw_value = target.get(dimension.value)
        genomic[dimension] = (
            int(raw_value)
            if not _prediction_dimension_missing(raw_value, dimension)
            else ""
        )
    canonical: dict[str, object] = {}
    for dimension in annotator.match.dimensions:
        if dimension in genomic:
            canonical[dimension.value] = genomic[dimension]
            continue
        value = target.get(dimension.value, "")
        canonical[dimension.value] = (
            ""
            if _prediction_dimension_missing(value, dimension)
            else str(value)
        )
    return json.dumps(canonical, sort_keys=True, separators=(",", ":"))


def _normalized_public_prediction_target(annotator, target: dict) -> dict:
    """Type-check caller-supplied match dimensions before exact certification."""
    normalized: dict[str, object] = {}
    coordinate_dimensions = {
        MatchDimension.POSITION,
        MatchDimension.START,
        MatchDimension.END,
        MatchDimension.TSS,
    }
    for dimension in annotator.match.dimensions:
        if dimension.value not in target:
            continue
        value = target[dimension.value]
        if _prediction_dimension_missing(value, dimension):
            normalized[dimension.value] = ""
        elif dimension in coordinate_dimensions:
            if isinstance(value, bool):
                raise ValueError(f"target {dimension.value} must be an integer")
            try:
                numeric = int(value)
            except (TypeError, ValueError) as error:
                raise ValueError(
                    f"target {dimension.value} must be an integer"
                ) from error
            if str(numeric) != str(value).strip() or numeric < 1:
                raise ValueError(
                    f"target {dimension.value} must be a positive integer"
                )
            normalized[dimension.value] = numeric
        elif dimension is MatchDimension.STRAND:
            if value not in {"+", "-"}:
                raise ValueError("target strand must be + or -")
            normalized[dimension.value] = value
        else:
            if (
                not isinstance(value, str)
                and not (
                    dimension is MatchDimension.PHASE_SET
                    and isinstance(value, int)
                    and not isinstance(value, bool)
                )
            ):
                raise ValueError(
                    f"target {dimension.value} must be a string"
                )
            normalized[dimension.value] = str(value).strip()
    return normalized


def _normalized_import_prediction_target(annotator, target: dict) -> tuple[dict, list[str]]:
    normalized: dict[str, object] = {}
    invalid: list[str] = []
    for dimension in annotator.match.dimensions:
        if dimension.value not in target:
            continue
        try:
            normalized.update(_normalized_public_prediction_target(
                annotator, {dimension.value: target[dimension.value]}
            ))
        except ValueError:
            normalized[dimension.value] = ""
            invalid.append(dimension.value)
    return normalized, invalid


def _active_predictors(fields: Iterable[str]) -> tuple:
    available = set(fields)
    return tuple(
        predictor for predictor in PREDICTOR_REGISTRY.predictors
        if any(metric.field in available for metric in predictor.metrics)
    )


def _normalized_predictions(
    record: dict[str, str], annotation: dict, predictors: Iterable | None = None,
) -> list[dict]:
    predictions: list[dict] = []
    selected_predictors = (
        _active_predictors(record) if predictors is None else predictors
    )
    for predictor in selected_predictors:
        annotator = PREDICTOR_REGISTRY.annotators_by_id[predictor.annotator_id]
        if annotator.match.scope is MatchScope.SAMPLE_HAPLOTYPE:
            # Expanded against the matching sample below, after genotypes are
            # parsed. A CSQ row alone cannot identify a phased observation.
            continue
        values: dict[str, object] = {}
        provenance: dict[str, object] = {}
        invalid_metrics: list[str] = []
        for metric in predictor.metrics:
            value = _registry_metric_value(record, metric)
            if value is None:
                continue
            try:
                value = _validated_prediction_metric(metric, value)
            except ValueError:
                # Imported VCF annotations are untrusted evidence. Discard a
                # malformed metric without rolling back every variant in the
                # cohort. Public upsert_prediction writes remain strict.
                invalid_metrics.append(metric.id)
                continue
            destination = (
                values if metric.filterable else provenance
            )
            destination[metric.id] = value
        if not values and not provenance:
            continue
        if invalid_metrics:
            provenance["invalid_metrics"] = invalid_metrics

        match_status = _prediction_match_status(
            predictor.id, annotator.match.scope, values, provenance
        )
        if predictor.id == "funcvep" and match_status != "exact":
            # A source allele without an exact Ensembl-gene match is useful
            # provenance, but its scores are not evidence for this gene.
            withheld: list[str] = []
            for metric in ("cti", "cte", "sp"):
                if values.pop(metric, None) is not None:
                    withheld.append(metric)
            if withheld:
                provenance["withheld_metrics"] = withheld
        elif predictor.id in {"logofunc", "promoterai"} and (
            match_status != "exact"
        ):
            withheld = sorted(values)
            values.clear()
            if withheld:
                provenance["withheld_metrics"] = withheld
        source_annotation = annotation
        target_record = record
        if predictor.id == "spliceai":
            source_gene_symbol = first(record, ("SpliceAI_pred_SYMBOL",))
            source_annotation = dict(annotation)
            source_annotation["gene"] = source_gene_symbol.upper()
            if source_gene_symbol:
                source_gene_id = first(
                    record, ("_SpliceAI_source_gene_id",)
                )
                if source_gene_id:
                    source_annotation["gene_id"] = source_gene_id
                elif annotation["gene"].upper() != source_gene_symbol.upper():
                    # The plugin matched a different gene from this CSQ row.
                    # Do not manufacture an Ensembl ID for that source gene.
                    source_annotation["gene_id"] = ""
                provenance.setdefault("source_gene_symbol", source_gene_symbol)
            else:
                source_annotation["gene_id"] = ""
        elif predictor.id == "funcvep":
            source_annotation = dict(annotation)
            source_annotation["gene_id"] = first(
                record, ("FuncVEP_source_gene",)
            )
        elif predictor.id == "promoterai":
            source_annotation = dict(annotation)
            source_annotation["transcript"] = first(
                record, ("PromoterAI_source_transcript",)
            )
        elif predictor.id == "logofunc":
            source_annotation = dict(annotation)
            source_annotation["transcript"] = first(
                record, ("LoGoFunc_source_transcript",)
            )
            source_annotation["hgvsp"] = first(
                record, ("LoGoFunc_source_HGVSp",)
            )
            target_record = dict(record)
            target_record["Protein_position"] = (
                _protein_position_from_change(source_annotation["hgvsp"])
            )
            target_record["Amino_acids"] = source_annotation["hgvsp"]
        target = _prediction_target(
            target_record, source_annotation, annotator, values
        )
        if (
            MatchDimension.ENSEMBL_TRANSCRIPT in annotator.match.dimensions
            and annotator.match.transcript_version
            is TranscriptVersionPolicy.EXACT_THEN_STABLE_ID
            and provenance.get("match") == "exact_version"
        ):
            target[MatchDimension.ENSEMBL_TRANSCRIPT.value] = (
                source_annotation["transcript"]
            )
        target, invalid_dimensions = _normalized_import_prediction_target(
            annotator, target
        )
        if invalid_dimensions:
            match_status = "partial"
            withheld = sorted(values)
            values.clear()
            if withheld:
                provenance["withheld_metrics"] = withheld
            provenance["invalid_dimensions"] = invalid_dimensions
        missing_dimensions = [
            dimension.value for dimension in annotator.match.dimensions
            if dimension not in _VARIANT_MATCH_DIMENSIONS
            and dimension is not MatchDimension.SAMPLE
            and _prediction_dimension_missing(
                target.get(dimension.value, ""), dimension
            )
        ]
        if match_status == "exact" and missing_dimensions:
            match_status = "partial"
            withheld = sorted(values)
            values.clear()
            if withheld:
                provenance["withheld_metrics"] = withheld
            provenance["missing_dimensions"] = missing_dimensions
        matched_dimensions = (
            []
            if match_status == "unmatched"
            else [
                dimension.value for dimension in annotator.match.dimensions
                if match_status == "exact"
                or dimension in _VARIANT_MATCH_DIMENSIONS
                or not _prediction_dimension_missing(
                    target.get(dimension.value, ""), dimension
                )
            ]
        )
        provenance["matched_dimensions"] = matched_dimensions
        target_dimensions = set(annotator.match.dimensions)
        bind_annotation = (
            match_status == "exact"
            and annotator.match.scope in _ANNOTATION_BOUND_SCOPES
        )
        if bind_annotation and predictor.id in {"logofunc", "promoterai"}:
            # VEP's default CSQ Feature omits transcript versions even though
            # the plugin can verify the version on the transcript object. The
            # plugin's exact_version provenance is therefore authoritative;
            # bind it to the CSQ row when their stable IDs agree while keeping
            # the versioned source transcript in the observation target.
            current_transcript = annotation["transcript"]
            source_transcript = str(target.get(
                MatchDimension.ENSEMBL_TRANSCRIPT.value, ""
            ))
            if (
                provenance.get("match") == "exact_version"
                and "." in current_transcript
            ):
                bind_annotation = current_transcript == source_transcript
            else:
                bind_annotation = _stable_transcript(
                    current_transcript, TranscriptVersionPolicy.STABLE_ID
                ) == _stable_transcript(
                    source_transcript, TranscriptVersionPolicy.STABLE_ID
                )
        if bind_annotation and predictor.id == "logofunc":
            # An exact source-scoped LoGoFunc value belongs only to the CSQ
            # annotation that produced the source transcript/protein match.
            # Other transcript rows may present it in the UI, but they must
            # not become storage owners of that exact observation.
            bind_annotation = (
                annotation["hgvsp"] == source_annotation["hgvsp"]
            )
        predictions.append({
            "resource_id": predictor.resource_id,
            "predictor_id": predictor.id,
            "target_scope": annotator.match.scope.value,
            "target": target,
            "bind_annotation": bind_annotation,
            "gene_id": (
                source_annotation["gene_id"]
                if MatchDimension.ENSEMBL_GENE in target_dimensions else ""
            ),
            "gene_symbol": (
                source_annotation["gene"]
                if MatchDimension.GENE_SYMBOL in target_dimensions else ""
            ),
            "transcript_id": (
                source_annotation["transcript"]
                if MatchDimension.ENSEMBL_TRANSCRIPT in target_dimensions else ""
            ),
            "protein_change": (
                source_annotation["hgvsp"]
                if target_dimensions & {
                    MatchDimension.PROTEIN_POSITION,
                    MatchDimension.AMINO_ACID_CHANGE,
                } else ""
            ),
            "match_status": match_status,
            "matcher": annotator.id,
            "provenance": provenance,
            "values": values,
        })
    return predictions


def _normalized_haplotype_prediction(
    haplotype: dict[str, str], *, sample: str, phase_set: str,
) -> dict | None:
    """Normalize sample-specific frame evidence after genotype parsing.

    The evidence is emitted in INFO, but its registry identity also requires
    the carrier sample and FORMAT phase set.  Keeping this separate from
    ``annotation_from`` prevents one sample's evidence from being attached to
    every consequence or carrier at the locus.
    """
    if not haplotype.get("status"):
        return None
    predictor = PREDICTOR_REGISTRY.predictors_by_id["haplotype_frame"]
    annotator = PREDICTOR_REGISTRY.annotators_by_id[predictor.annotator_id]
    transcript = _stable_transcript(
        haplotype.get("transcript", ""), annotator.match.transcript_version
    )
    target = {
        MatchDimension.SAMPLE.value: sample,
        MatchDimension.PHASE_SET.value: phase_set,
        MatchDimension.ENSEMBL_TRANSCRIPT.value: transcript,
    }
    missing_dimensions = [
        dimension.value for dimension in annotator.match.dimensions
        if dimension not in _GENOMIC_MATCH_DIMENSIONS
        and _prediction_dimension_missing(
            target.get(dimension.value, ""), dimension
        )
    ]
    match_status = "partial" if missing_dimensions else "exact"
    provenance: dict[str, object] = {
        "partners": haplotype.get("partners", ""),
        "source_protein_change": haplotype.get("protein", ""),
        "matched_dimensions": [
            dimension.value for dimension in annotator.match.dimensions
            if dimension in _GENOMIC_MATCH_DIMENSIONS
            or not _prediction_dimension_missing(
                target.get(dimension.value, ""), dimension
            )
        ],
    }
    values: dict[str, object] = {}
    if match_status == "exact":
        values["frame_evidence"] = haplotype["status"]
    else:
        provenance["frame_evidence"] = haplotype["status"]
        provenance["missing_dimensions"] = missing_dimensions
    return {
        "resource_id": predictor.resource_id,
        "predictor_id": predictor.id,
        "target_scope": annotator.match.scope.value,
        "target": target,
        "bind_annotation": False,
        "gene_id": "",
        "gene_symbol": "",
        "transcript_id": transcript,
        "protein_change": haplotype.get("protein", ""),
        "match_status": match_status,
        "matcher": annotator.id,
        "provenance": provenance,
        "values": values,
    }


def _validated_prediction_metric(metric, value: object) -> object:
    if metric.value_type is MetricType.BOOLEAN:
        if not isinstance(value, bool):
            raise ValueError(f"metric {metric.id} must be Boolean")
        return value
    if metric.value_type in {MetricType.FLOAT, MetricType.INTEGER}:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"metric {metric.id} must be numeric")
        numeric = float(value)
        if not math.isfinite(numeric):
            raise ValueError(f"metric {metric.id} must be finite")
        if metric.value_type is MetricType.INTEGER and not numeric.is_integer():
            raise ValueError(f"metric {metric.id} must be an integer")
        if metric.value_range and not (
            metric.value_range[0] <= numeric <= metric.value_range[1]
        ):
            raise ValueError(
                f"metric {metric.id} must be between "
                f"{metric.value_range[0]} and {metric.value_range[1]}"
            )
        return int(numeric) if metric.value_type is MetricType.INTEGER else numeric
    if not isinstance(value, str):
        raise ValueError(f"metric {metric.id} must be text")
    return value


def annotation_from(
    record: dict[str, str], *, predictors: Iterable | None = None,
) -> dict:
    impact = (first(record, ("IMPACT",)) or "UNKNOWN").upper()
    if impact not in ALLOWED_IMPACTS:
        impact = "UNKNOWN"
    annotation = {
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
    annotation["_predictions"] = _normalized_predictions(
        record, annotation, predictors
    )
    return annotation


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
STAGE_PREDICTION_OBSERVATION_COLUMNS = (
    "resource_id", "predictor_id", "variant_key", "target_scope",
    "target_key", "bind_annotation", "annotation_gene",
    "annotation_transcript", "annotation_hgvsc", "annotation_hgvsp",
    "annotation_consequence", "sample_name", "gene_id", "gene_symbol",
    "transcript_id", "protein_change", "match_status", "matcher",
    "provenance_json",
)
STAGE_PREDICTION_VALUE_COLUMNS = (
    "resource_id", "predictor_id", "variant_key", "target_scope",
    "target_key", "metric", "value_type", "numeric_value", "text_value",
    "boolean_value",
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
CREATE TABLE stage_prediction_observations (
  resource_id TEXT NOT NULL,
  predictor_id TEXT NOT NULL,
  variant_key TEXT NOT NULL,
  target_scope TEXT NOT NULL,
  target_key TEXT NOT NULL,
  bind_annotation INTEGER NOT NULL DEFAULT 0,
  annotation_gene TEXT NOT NULL DEFAULT '',
  annotation_transcript TEXT NOT NULL DEFAULT '',
  annotation_hgvsc TEXT NOT NULL DEFAULT '',
  annotation_hgvsp TEXT NOT NULL DEFAULT '',
  annotation_consequence TEXT NOT NULL DEFAULT '',
  sample_name TEXT NOT NULL DEFAULT '',
  gene_id TEXT NOT NULL DEFAULT '',
  gene_symbol TEXT NOT NULL DEFAULT '',
  transcript_id TEXT NOT NULL DEFAULT '',
  protein_change TEXT NOT NULL DEFAULT '',
  match_status TEXT NOT NULL,
  matcher TEXT NOT NULL,
  provenance_json TEXT NOT NULL DEFAULT '{}',
  PRIMARY KEY(
    resource_id, predictor_id, variant_key, target_scope, target_key
  )
) WITHOUT ROWID;
CREATE TABLE stage_prediction_values (
  resource_id TEXT NOT NULL,
  predictor_id TEXT NOT NULL,
  variant_key TEXT NOT NULL,
  target_scope TEXT NOT NULL,
  target_key TEXT NOT NULL,
  metric TEXT NOT NULL,
  value_type TEXT NOT NULL,
  numeric_value REAL,
  text_value TEXT,
  boolean_value INTEGER,
  PRIMARY KEY(
    resource_id, predictor_id, variant_key, target_scope, target_key, metric
  )
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
STAGE_PREDICTION_OBSERVATION_INSERT = f"""
INSERT INTO stage_prediction_observations (
  {','.join(STAGE_PREDICTION_OBSERVATION_COLUMNS)}
) VALUES ({_placeholders(STAGE_PREDICTION_OBSERVATION_COLUMNS)})
ON CONFLICT(
  resource_id, predictor_id, variant_key, target_scope, target_key
) DO UPDATE SET
  bind_annotation=excluded.bind_annotation,
  annotation_gene=excluded.annotation_gene,
  annotation_transcript=excluded.annotation_transcript,
  annotation_hgvsc=excluded.annotation_hgvsc,
  annotation_hgvsp=excluded.annotation_hgvsp,
  annotation_consequence=excluded.annotation_consequence,
  sample_name=excluded.sample_name,
  gene_id=excluded.gene_id,
  gene_symbol=excluded.gene_symbol,
  transcript_id=excluded.transcript_id,
  protein_change=excluded.protein_change,
  match_status=excluded.match_status,
  matcher=excluded.matcher,
  provenance_json=excluded.provenance_json
WHERE stage_prediction_observations.match_status != 'exact'
   OR excluded.match_status = 'exact'
"""
STAGE_PREDICTION_VALUE_INSERT = _stage_insert_sql(
    "stage_prediction_values", STAGE_PREDICTION_VALUE_COLUMNS
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
    # The exact-variant query compares variant_key COLLATE NOCASE; the
    # binary-collated primary key cannot serve that and degrades to a full
    # index scan at cohort scale.
    "cohort_variants_key_nocase_idx": (
        "CREATE INDEX cohort_variants_key_nocase_idx "
        "ON cohort_variants(variant_key COLLATE NOCASE)"
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
    "prediction_observations_variant_idx": (
        "CREATE INDEX prediction_observations_variant_idx "
        "ON prediction_observations(variant_id, predictor_id, release_id)"
    ),
    "prediction_observations_predictor_idx": (
        "CREATE INDEX prediction_observations_predictor_idx "
        "ON prediction_observations(predictor_id, variant_id, release_id)"
    ),
    "prediction_observations_source_idx": (
        "CREATE INDEX prediction_observations_source_idx "
        "ON prediction_observations(source_file_id, predictor_id) "
        "WHERE source_file_id IS NOT NULL"
    ),
    "prediction_observations_annotation_idx": (
        "CREATE INDEX prediction_observations_annotation_idx "
        "ON prediction_observations(annotation_id, predictor_id) "
        "WHERE annotation_id IS NOT NULL"
    ),
    "prediction_observations_sample_idx": (
        "CREATE INDEX prediction_observations_sample_idx "
        "ON prediction_observations(sample_id, predictor_id) "
        "WHERE sample_id IS NOT NULL"
    ),
    "prediction_observations_gene_idx": (
        "CREATE INDEX prediction_observations_gene_idx "
        "ON prediction_observations(gene_id, predictor_id) "
        "WHERE gene_id != ''"
    ),
    "prediction_observations_transcript_idx": (
        "CREATE INDEX prediction_observations_transcript_idx "
        "ON prediction_observations(transcript_id, predictor_id) "
        "WHERE transcript_id != ''"
    ),
    "prediction_observations_scope_idx": (
        "CREATE INDEX prediction_observations_scope_idx "
        "ON prediction_observations(target_scope, target_key)"
    ),
    "prediction_values_numeric_idx": (
        "CREATE INDEX prediction_values_numeric_idx "
        "ON prediction_values(metric, numeric_value, observation_id) "
        "WHERE value_type = 'number'"
    ),
    "prediction_values_text_idx": (
        "CREATE INDEX prediction_values_text_idx "
        "ON prediction_values(metric, text_value, observation_id) "
        "WHERE value_type = 'text'"
    ),
    "prediction_values_boolean_idx": (
        "CREATE INDEX prediction_values_boolean_idx "
        "ON prediction_values(metric, boolean_value, observation_id) "
        "WHERE value_type = 'boolean'"
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
    prediction_rows: dict[tuple, tuple] = {}
    prediction_value_rows: dict[tuple, tuple] = {}
    records_processed = 0
    pass_records = 0
    excluded_records = 0
    carrier_count = 0
    active_predictors = _active_predictors(
        (*header.csq_fields, *header.info_fields)
    )

    def flush() -> None:
        if variant_rows:
            connection.executemany(STAGE_VARIANT_INSERT, variant_rows.values())
        if annotation_rows:
            connection.executemany(
                STAGE_ANNOTATION_INSERT, annotation_rows.values()
            )
        if genotype_rows:
            connection.executemany(STAGE_GENOTYPE_INSERT, genotype_rows.values())
        if prediction_rows:
            connection.executemany(
                STAGE_PREDICTION_OBSERVATION_INSERT, prediction_rows.values()
            )
        if prediction_value_rows:
            connection.executemany(
                STAGE_PREDICTION_VALUE_INSERT, prediction_value_rows.values()
            )
        connection.commit()
        variant_rows.clear()
        annotation_rows.clear()
        genotype_rows.clear()
        prediction_rows.clear()
        prediction_value_rows.clear()

    def capture_prediction(
        prediction: dict,
        *,
        key: str,
        chrom: str,
        pos: int,
        ref: str,
        alt: str,
        annotation: dict | None = None,
        sample_name: str = "",
    ) -> None:
        annotator = PREDICTOR_REGISTRY.annotators_by_id[
            prediction["matcher"]
        ]
        target_key = _canonical_prediction_target_key(
            annotator,
            prediction["target"],
            chrom=chrom,
            pos=pos,
            ref=ref,
            alt=alt,
            sample=sample_name,
        )
        identity = (
            prediction["resource_id"],
            prediction["predictor_id"],
            key,
            prediction["target_scope"],
            target_key,
        )
        annotation = annotation or {}
        observation_row = (
            *identity,
            int(prediction["bind_annotation"]),
            annotation.get("gene", ""),
            annotation.get("transcript", ""),
            annotation.get("hgvsc", ""),
            annotation.get("hgvsp", ""),
            annotation.get("consequence", ""),
            sample_name,
            prediction["gene_id"],
            prediction["gene_symbol"],
            prediction["transcript_id"],
            prediction["protein_change"],
            prediction["match_status"],
            prediction["matcher"],
            json.dumps(
                prediction["provenance"],
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
        previous = prediction_rows.get(identity)
        if (
            previous is None
            or previous[-3] != "exact"
            or prediction["match_status"] == "exact"
        ):
            prediction_rows[identity] = observation_row
        for metric, value in prediction["values"].items():
            if isinstance(value, bool):
                typed = ("boolean", None, None, int(value))
            elif isinstance(value, (int, float)):
                typed = ("number", float(value), None, None)
            else:
                typed = ("text", None, str(value), None)
            value_identity = (*identity, metric)
            prediction_value_rows[value_identity] = (
                *value_identity, *typed
            )

    try:
        for line in lines:
            if not line.strip() or line.startswith("#"):
                continue
            records_processed += 1
            columns = line.rstrip("\r\n").split("\t")
            if len(columns) < 10:
                # A data row truncated at or before the FORMAT column in a
                # with-samples VCF is a corrupt file, not an ignorable line:
                # skipping it silently omitted the variant.
                position = ":".join(columns[:2]) if len(columns) >= 2 else "?"
                raise ValueError(
                    f"record {position} has {len(columns)} column(s) but the "
                    f"header declares {len(header.samples)} sample(s) — the "
                    "file looks truncated; re-export it and import again"
                )
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
            if len(sample_values) < len(header.samples):
                # A record with fewer sample columns than the header declares
                # is a truncated or corrupt file. Substituting empty
                # genotypes would import it "successfully" while silently
                # erasing the missing samples' carrier status.
                raise ValueError(
                    f"record {chrom_raw}:{pos_raw} has {len(sample_values)} "
                    f"sample column(s) but the header declares "
                    f"{len(header.samples)} samples — the file looks "
                    "truncated; re-export it and import again"
                )
            qual = parse_number(qual_raw)

            alts = tuple(alt_raw.split(","))
            for alt_index, alt in enumerate(alts):
                carriers: list[tuple[str, dict]] = []
                for sample_index, sample_name in enumerate(header.samples):
                    genotype = parse_genotype(
                        format_value,
                        sample_values[sample_index],
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

                matching = [
                    consequence
                    for consequence in consequences
                    if _consequence_matches_alt(
                        consequence, ref=ref, alts=alts, alt_index=alt_index
                    )
                ]
                if not matching and len(alts) == 1:
                    matching = consequences
                prediction_info = _prediction_info_for_alt(
                    info,
                    alt=alt,
                    alt_index=alt_index,
                    alts=alts,
                )
                annotation_records = _prediction_annotation_records(
                    prediction_info, matching
                )
                annotations = [
                    annotation_from(record, predictors=active_predictors)
                    for record in annotation_records
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
                    for prediction in annotation["_predictions"]:
                        capture_prediction(
                            prediction,
                            key=key,
                            chrom=chrom,
                            pos=pos,
                            ref=ref,
                            alt=alt,
                            annotation=annotation,
                        )

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
                    haplotype_prediction = _normalized_haplotype_prediction(
                        haplotype,
                        sample=sample_name,
                        phase_set=genotype["phase_set"],
                    )
                    if haplotype_prediction:
                        capture_prediction(
                            haplotype_prediction,
                            key=key,
                            chrom=chrom,
                            pos=pos,
                            ref=ref,
                            alt=alt,
                            sample_name=sample_name,
                        )
                    carrier_count += 1

            if (
                records_processed % batch_records == 0
                or len(prediction_rows) >= MAX_STAGE_PREDICTION_OBSERVATIONS
                or len(prediction_value_rows) >= MAX_STAGE_PREDICTION_VALUES
            ):
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
            # The shard readers are CPU-bound (per-record filtering), so
            # the default scales with the machine instead of pinning four
            # readers on a ten-core workstation: cores minus two for the
            # OS/browser, capped at eight, never below the old default.
            hardware_default = max(
                DEFAULT_INDEX_READERS, min(8, (os.cpu_count() or 4) - 2)
            )
            try:
                configured_readers = int(
                    os.environ.get("IEI_COHORT_INDEX_READERS", hardware_default)
                )
            except ValueError:
                configured_readers = hardware_default
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

                CREATE TABLE IF NOT EXISTS predictor_releases (
                    id INTEGER PRIMARY KEY,
                    provider TEXT NOT NULL,
                    resource_id TEXT NOT NULL,
                    release_version TEXT NOT NULL,
                    assembly TEXT NOT NULL,
                    source_uri TEXT NOT NULL DEFAULT '',
                    checksum_algorithm TEXT NOT NULL DEFAULT '',
                    checksum TEXT NOT NULL DEFAULT '',
                    priority INTEGER NOT NULL DEFAULT 0,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(
                        provider, resource_id, release_version, assembly,
                        checksum_algorithm, checksum
                    )
                );

                CREATE TABLE IF NOT EXISTS prediction_observations (
                    id INTEGER PRIMARY KEY,
                    release_id INTEGER NOT NULL
                        REFERENCES predictor_releases(id) ON DELETE CASCADE,
                    source_file_id INTEGER
                        REFERENCES cohort_files(id) ON DELETE CASCADE,
                    source_identity TEXT NOT NULL DEFAULT 'manual',
                    predictor_id TEXT NOT NULL,
                    variant_id INTEGER NOT NULL
                        REFERENCES cohort_variants(id) ON DELETE CASCADE,
                    annotation_id INTEGER
                        REFERENCES cohort_annotations(id) ON DELETE CASCADE,
                    sample_id INTEGER
                        REFERENCES cohort_samples(id) ON DELETE CASCADE,
                    target_scope TEXT NOT NULL,
                    target_key TEXT NOT NULL,
                    gene_id TEXT NOT NULL DEFAULT '',
                    gene_symbol TEXT NOT NULL DEFAULT '',
                    transcript_id TEXT NOT NULL DEFAULT '',
                    protein_change TEXT NOT NULL DEFAULT '',
                    match_status TEXT NOT NULL DEFAULT 'exact',
                    matcher TEXT NOT NULL DEFAULT '',
                    provenance_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(
                        release_id, predictor_id, variant_id,
                        target_scope, target_key, source_identity
                    )
                );

                CREATE TABLE IF NOT EXISTS prediction_values (
                    id INTEGER PRIMARY KEY,
                    observation_id INTEGER NOT NULL
                        REFERENCES prediction_observations(id) ON DELETE CASCADE,
                    metric TEXT NOT NULL,
                    value_type TEXT NOT NULL
                        CHECK(value_type IN ('number', 'text', 'boolean')),
                    numeric_value REAL,
                    text_value TEXT,
                    boolean_value INTEGER CHECK(boolean_value IN (0, 1)),
                    unit TEXT NOT NULL DEFAULT '',
                    UNIQUE(observation_id, metric),
                    CHECK(
                        (value_type = 'number'
                         AND numeric_value IS NOT NULL
                         AND text_value IS NULL
                         AND boolean_value IS NULL)
                        OR
                        (value_type = 'text'
                         AND numeric_value IS NULL
                         AND text_value IS NOT NULL
                         AND boolean_value IS NULL)
                        OR
                        (value_type = 'boolean'
                         AND numeric_value IS NULL
                         AND text_value IS NULL
                         AND boolean_value IS NOT NULL)
                    )
                );

                CREATE INDEX IF NOT EXISTS cohort_variants_locus_idx
                    ON cohort_variants(chrom, pos, ref, alt);
                CREATE INDEX IF NOT EXISTS cohort_variants_rsid_idx
                    ON cohort_variants(rsid);
                CREATE INDEX IF NOT EXISTS cohort_variants_rsid_nocase_idx
                    ON cohort_variants(rsid COLLATE NOCASE);
                CREATE INDEX IF NOT EXISTS cohort_variants_key_nocase_idx
                    ON cohort_variants(variant_key COLLATE NOCASE);
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
                CREATE INDEX IF NOT EXISTS predictor_releases_lookup_idx
                    ON predictor_releases(
                        resource_id, assembly, priority DESC, release_version
                    );
                CREATE INDEX IF NOT EXISTS prediction_observations_variant_idx
                    ON prediction_observations(
                        variant_id, predictor_id, release_id
                    );
                CREATE INDEX IF NOT EXISTS prediction_observations_predictor_idx
                    ON prediction_observations(
                        predictor_id, variant_id, release_id
                    );
                CREATE INDEX IF NOT EXISTS prediction_observations_source_idx
                    ON prediction_observations(source_file_id, predictor_id)
                    WHERE source_file_id IS NOT NULL;
                CREATE INDEX IF NOT EXISTS prediction_observations_annotation_idx
                    ON prediction_observations(annotation_id, predictor_id)
                    WHERE annotation_id IS NOT NULL;
                CREATE INDEX IF NOT EXISTS prediction_observations_sample_idx
                    ON prediction_observations(sample_id, predictor_id)
                    WHERE sample_id IS NOT NULL;
                CREATE INDEX IF NOT EXISTS prediction_observations_gene_idx
                    ON prediction_observations(gene_id, predictor_id)
                    WHERE gene_id != '';
                CREATE INDEX IF NOT EXISTS prediction_observations_transcript_idx
                    ON prediction_observations(transcript_id, predictor_id)
                    WHERE transcript_id != '';
                CREATE INDEX IF NOT EXISTS prediction_observations_scope_idx
                    ON prediction_observations(target_scope, target_key);
                CREATE INDEX IF NOT EXISTS prediction_values_numeric_idx
                    ON prediction_values(metric, numeric_value, observation_id)
                    WHERE value_type = 'number';
                CREATE INDEX IF NOT EXISTS prediction_values_text_idx
                    ON prediction_values(metric, text_value, observation_id)
                    WHERE value_type = 'text';
                CREATE INDEX IF NOT EXISTS prediction_values_boolean_idx
                    ON prediction_values(metric, boolean_value, observation_id)
                    WHERE value_type = 'boolean';
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
                ("content_probe", "TEXT NOT NULL DEFAULT ''"),
                ("content_sha256", "TEXT NOT NULL DEFAULT ''"),
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

    @staticmethod
    def _prediction_json(value: dict | None, label: str) -> str:
        if value is None:
            value = {}
        if not isinstance(value, dict):
            raise ValueError(f"{label} must be an object")
        try:
            return json.dumps(
                value,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        except (TypeError, ValueError) as error:
            raise ValueError(f"{label} must contain JSON-compatible values") from error

    @staticmethod
    def _required_prediction_text(value: object, label: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{label} must be a non-empty string")
        return value.strip()

    @staticmethod
    def _decode_prediction_json(value: str) -> dict:
        try:
            decoded = json.loads(value or "{}")
        except (TypeError, json.JSONDecodeError):
            return {}
        return decoded if isinstance(decoded, dict) else {}

    @classmethod
    def _serialize_predictor_release(cls, row: sqlite3.Row) -> dict:
        result = dict(row)
        result["metadata"] = cls._decode_prediction_json(
            result.pop("metadata_json", "{}")
        )
        return result

    def upsert_predictor_release(
        self,
        *,
        provider: str,
        resource_id: str,
        release_version: str,
        assembly: str,
        source_uri: str = "",
        checksum_algorithm: str = "",
        checksum: str = "",
        priority: int = 0,
        metadata: dict | None = None,
    ) -> dict:
        """Register one installed predictor resource release idempotently.

        A release describes the provenance shared by one or more predictors.
        It deliberately does not contain secret download credentials; callers
        should leave ``source_uri`` empty or store only a public canonical URI.
        """
        provider = self._required_prediction_text(provider, "provider")
        resource_id = self._required_prediction_text(resource_id, "resource_id")
        try:
            resource = PREDICTOR_REGISTRY.resources_by_id[resource_id]
        except KeyError as error:
            raise ValueError(f"unknown predictor resource: {resource_id}") from error
        release_version = self._required_prediction_text(
            release_version, "release_version"
        )
        assembly = self._required_prediction_text(assembly, "assembly")
        if resource.assembly and assembly != resource.assembly:
            raise ValueError(
                f"assembly for {resource_id} must be {resource.assembly}"
            )
        if not isinstance(priority, int) or isinstance(priority, bool):
            raise ValueError("priority must be an integer")
        for value, label in (
            (checksum_algorithm, "checksum_algorithm"),
            (checksum, "checksum"),
        ):
            if not isinstance(value, str):
                raise ValueError(f"{label} must be a string")
        if bool(checksum_algorithm) != bool(checksum):
            raise ValueError(
                "checksum_algorithm and checksum must be supplied together"
        )
        checksum_algorithm = checksum_algorithm.lower()
        checksum = checksum.lower()
        source_uri = _public_source_uri(source_uri)
        canonical_source_uri = _public_source_uri(resource.source_url or "")
        if source_uri and (
            not canonical_source_uri
            or source_uri.rstrip("/") != canonical_source_uri.rstrip("/")
        ):
            raise ValueError(
                "source_uri must be the registry's public canonical landing URL"
            )
        metadata_json = self._prediction_json(metadata, "metadata")
        now = utc_now()
        with self._session() as connection:
            connection.execute(
                """
                INSERT INTO predictor_releases(
                    provider, resource_id, release_version, assembly,
                    source_uri, checksum_algorithm, checksum, priority,
                    metadata_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(
                    provider, resource_id, release_version, assembly,
                    checksum_algorithm, checksum
                )
                DO UPDATE SET
                    source_uri=excluded.source_uri,
                    priority=excluded.priority,
                    metadata_json=excluded.metadata_json,
                    updated_at=excluded.updated_at
                """,
                (
                    provider, resource_id, release_version, assembly,
                    source_uri, checksum_algorithm, checksum, priority,
                    metadata_json, now, now,
                ),
            )
            row = connection.execute(
                """
                SELECT * FROM predictor_releases
                WHERE provider = ? AND resource_id = ?
                  AND release_version = ? AND assembly = ?
                  AND checksum_algorithm = ? AND checksum = ?
                """,
                (
                    provider, resource_id, release_version, assembly,
                    checksum_algorithm, checksum,
                ),
            ).fetchone()
        assert row is not None
        return self._serialize_predictor_release(row)

    @staticmethod
    def _ensure_embedded_predictor_releases(
        connection: sqlite3.Connection,
        resource_ids: Iterable[str],
        *,
        source_file_id: int,
        content_sha256: str = "",
        content_probe: str = "",
    ) -> dict[str, int]:
        resources = [
            PREDICTOR_REGISTRY.resources_by_id[resource_id]
            for resource_id in sorted(set(resource_ids))
        ]
        if not resources:
            return {}
        release_version, checksum_algorithm, checksum = (
            _embedded_vcf_release_identity(
                source_file_id, content_sha256, content_probe
            )
        )
        now = utc_now()
        connection.executemany(
            """
            INSERT INTO predictor_releases(
                provider, resource_id, release_version, assembly,
                source_uri, checksum_algorithm, checksum, priority,
                metadata_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
            ON CONFLICT(
                provider, resource_id, release_version, assembly,
                checksum_algorithm, checksum
            ) DO UPDATE SET updated_at=excluded.updated_at
            """,
            [
                (
                    EMBEDDED_VCF_PROVIDER,
                    resource.id,
                    release_version,
                    resource.assembly or "not_applicable",
                    _public_source_uri(resource.source_url or ""),
                    checksum_algorithm,
                    checksum,
                    json.dumps({
                        "distribution": resource.distribution.value,
                        "license_name": resource.license_name,
                        "origin": "embedded_vcf_annotation",
                        "registry_schema_version": PREDICTOR_REGISTRY.schema_version,
                    }, sort_keys=True, separators=(",", ":")),
                    now,
                    now,
                )
                for resource in resources
            ],
        )
        placeholders = ",".join("?" for _ in resources)
        return {
            row["resource_id"]: row["id"]
            for row in connection.execute(
                f"""
                SELECT id, resource_id FROM predictor_releases
                WHERE provider = ? AND release_version = ?
                  AND checksum_algorithm = ? AND checksum = ?
                  AND resource_id IN ({placeholders})
                """,
                (
                    EMBEDDED_VCF_PROVIDER,
                    release_version,
                    checksum_algorithm,
                    checksum,
                    *(resource.id for resource in resources),
                ),
            )
        }

    @classmethod
    def _serialize_prediction_observations(
        cls,
        connection: sqlite3.Connection,
        rows: list[sqlite3.Row],
    ) -> list[dict]:
        if not rows:
            return []
        observation_ids = [row["id"] for row in rows]
        value_rows: list[sqlite3.Row] = []
        for start in range(0, len(observation_ids), SQLITE_VARIABLE_CHUNK):
            chunk = observation_ids[start:start + SQLITE_VARIABLE_CHUNK]
            placeholders = ",".join("?" for _ in chunk)
            value_rows.extend(connection.execute(
                f"""
                SELECT observation_id, metric, value_type, numeric_value,
                       text_value, boolean_value, unit
                FROM prediction_values
                WHERE observation_id IN ({placeholders})
                ORDER BY observation_id, metric
                """,
                chunk,
            ).fetchall())
        values: dict[int, dict[str, object]] = {
            observation_id: {} for observation_id in observation_ids
        }
        value_types: dict[int, dict[str, str]] = {
            observation_id: {} for observation_id in observation_ids
        }
        value_units: dict[int, dict[str, str]] = {
            observation_id: {} for observation_id in observation_ids
        }
        for value_row in value_rows:
            value_type = value_row["value_type"]
            if value_type == "number":
                value: object = value_row["numeric_value"]
            elif value_type == "boolean":
                value = bool(value_row["boolean_value"])
            else:
                value = value_row["text_value"]
            observation_id = value_row["observation_id"]
            metric = value_row["metric"]
            values[observation_id][metric] = value
            value_types[observation_id][metric] = value_type
            if value_row["unit"]:
                value_units[observation_id][metric] = value_row["unit"]

        serialized: list[dict] = []
        for row in rows:
            result = dict(row)
            result["provenance"] = cls._decode_prediction_json(
                result.pop("provenance_json", "{}")
            )
            result["release_metadata"] = cls._decode_prediction_json(
                result.pop("release_metadata_json", "{}")
            )
            result["values"] = values[result["id"]]
            result["value_types"] = value_types[result["id"]]
            result["value_units"] = value_units[result["id"]]
            serialized.append(result)
        return serialized

    @staticmethod
    def _prediction_select() -> str:
        return """
            SELECT observation.*, predictor_release.provider,
                   predictor_release.resource_id,
                   predictor_release.release_version,
                   predictor_release.assembly,
                   predictor_release.source_uri,
                   predictor_release.checksum_algorithm,
                   predictor_release.checksum, predictor_release.priority,
                   predictor_release.metadata_json AS release_metadata_json
            FROM prediction_observations AS observation
            JOIN predictor_releases AS predictor_release
              ON predictor_release.id = observation.release_id
        """

    def upsert_prediction(
        self,
        *,
        release_id: int,
        predictor_id: str,
        variant_id: int,
        target_scope: str,
        values: dict[str, float | int | str | bool | None],
        annotation_id: int | None = None,
        sample_id: int | None = None,
        gene_id: str = "",
        gene_symbol: str = "",
        transcript_id: str = "",
        protein_change: str = "",
        consequence: str = "",
        target: dict | None = None,
        target_key: str | None = None,
        match_status: str = "exact",
        matcher: str = "",
        provenance: dict | None = None,
        units: dict[str, str] | None = None,
        replace_values: bool = False,
    ) -> dict:
        """Upsert one scope-aware prediction and its typed metric values.

        ``None`` removes a named metric. Other values are stored in separate
        numeric, text, and Boolean columns so filtering remains indexed.
        Unless ``replace_values`` is true, metrics omitted from ``values`` are
        preserved, allowing a predictor to be populated incrementally.
        """
        predictor_id = self._required_prediction_text(predictor_id, "predictor_id")
        try:
            predictor = PREDICTOR_REGISTRY.predictors_by_id[predictor_id]
        except KeyError as error:
            raise ValueError(f"unknown predictor: {predictor_id}") from error
        annotator = PREDICTOR_REGISTRY.annotators_by_id[predictor.annotator_id]
        target_scope = self._required_prediction_text(target_scope, "target_scope")
        if target_scope != annotator.match.scope.value:
            raise ValueError(
                f"target_scope for {predictor_id} must be "
                f"{annotator.match.scope.value}"
            )
        match_status = self._required_prediction_text(match_status, "match_status")
        if match_status not in {"exact", "partial", "ambiguous", "unmatched"}:
            raise ValueError("unsupported prediction match_status")
        if not isinstance(release_id, int) or isinstance(release_id, bool):
            raise ValueError("release_id must be an integer")
        if not isinstance(variant_id, int) or isinstance(variant_id, bool):
            raise ValueError("variant_id must be an integer")
        if annotation_id is not None and (
            not isinstance(annotation_id, int) or isinstance(annotation_id, bool)
        ):
            raise ValueError("annotation_id must be an integer or null")
        if sample_id is not None and (
            not isinstance(sample_id, int) or isinstance(sample_id, bool)
        ):
            raise ValueError("sample_id must be an integer or null")
        if not isinstance(values, dict):
            raise ValueError("values must be an object")
        if not isinstance(replace_values, bool):
            raise ValueError("replace_values must be a Boolean")
        units = units or {}
        if not isinstance(units, dict) or any(
            not isinstance(metric, str) or not isinstance(unit, str)
            for metric, unit in units.items()
        ):
            raise ValueError("units must map metric names to strings")
        identifiers = {
            "gene_id": gene_id,
            "gene_symbol": gene_symbol,
            "transcript_id": transcript_id,
            "protein_change": protein_change,
            "consequence": consequence,
        }
        for label, value in identifiers.items():
            if not isinstance(value, str):
                raise ValueError(f"{label} must be a string")
            identifiers[label] = value.strip()
        if matcher and matcher != annotator.id:
            raise ValueError(f"matcher for {predictor_id} must be {annotator.id}")
        matcher = annotator.id
        if target_key is not None:
            raise ValueError("target_key is derived from the registry match scope")
        if target is None:
            target = {}
        if not isinstance(target, dict):
            raise ValueError("target must be an object")
        allowed_target_fields = {
            dimension.value for dimension in annotator.match.dimensions
        }
        unknown_target_fields = set(target) - allowed_target_fields
        if unknown_target_fields:
            raise ValueError(
                "target has fields outside the registry match scope: "
                + ", ".join(sorted(unknown_target_fields))
            )
        target = _normalized_public_prediction_target(annotator, target)
        provenance_object = dict(provenance or {})
        self._prediction_json(provenance_object, "provenance")

        typed_values: list[
            tuple[str, str, float | None, str | None, int | None, str]
        ] = []
        removed_metrics: list[str] = []
        removed_provenance_metrics: list[str] = []
        withheld_metrics: list[str] = []
        metrics_by_id = {metric.id: metric for metric in predictor.metrics}
        withhold_filterable = (
            predictor_id in {"funcvep", "logofunc", "promoterai"}
            and match_status != "exact"
        )
        if withhold_filterable:
            removed_metrics.extend(
                metric.id for metric in predictor.metrics if metric.filterable
            )
        unknown_unit_metrics = set(units) - set(metrics_by_id)
        if unknown_unit_metrics:
            raise ValueError(
                f"unknown metric for {predictor_id}: "
                + ", ".join(sorted(unknown_unit_metrics))
            )
        for metric_id, value in values.items():
            metric_id = self._required_prediction_text(metric_id, "metric")
            try:
                metric = metrics_by_id[metric_id]
            except KeyError as error:
                raise ValueError(
                    f"unknown metric for {predictor_id}: {metric_id}"
                ) from error
            unit = units.get(metric_id, "")
            if value is None:
                if metric.filterable:
                    removed_metrics.append(metric_id)
                else:
                    removed_provenance_metrics.append(metric_id)
                continue
            value = _validated_prediction_metric(metric, value)
            if withhold_filterable and metric.filterable:
                withheld_metrics.append(metric_id)
                continue
            if not metric.filterable:
                provenance_object[metric_id] = value
            elif metric.value_type is MetricType.BOOLEAN:
                typed_values.append(
                    (metric_id, "boolean", None, None, int(value), unit)
                )
            elif metric.value_type in {MetricType.FLOAT, MetricType.INTEGER}:
                typed_values.append(
                    (metric_id, "number", float(value), None, None, unit)
                )
            else:
                typed_values.append(
                    (metric_id, "text", None, str(value), None, unit)
                )
        now = utc_now()
        with self._session() as connection:
            release = connection.execute(
                "SELECT resource_id FROM predictor_releases WHERE id = ?",
                (release_id,),
            ).fetchone()
            if release is None:
                raise ValueError("release_id does not exist")
            if release["resource_id"] != predictor.resource_id:
                raise ValueError(
                    f"release_id is not a {predictor.resource_id} release"
                )
            variant = connection.execute(
                """
                SELECT chrom, pos, ref, alt FROM cohort_variants WHERE id = ?
                """,
                (variant_id,),
            ).fetchone()
            if variant is None:
                raise ValueError("variant_id does not exist")
            if annotation_id is not None:
                annotation = connection.execute(
                    "SELECT variant_id FROM cohort_annotations WHERE id = ?",
                    (annotation_id,),
                ).fetchone()
                if annotation is None:
                    raise ValueError("annotation_id does not exist")
                if annotation["variant_id"] != variant_id:
                    raise ValueError("annotation_id belongs to another variant")
            if (
                annotator.match.scope not in _ANNOTATION_BOUND_SCOPES
                or match_status != "exact"
            ):
                annotation_id = None
            elif annotation_id is None:
                raise ValueError(f"{target_scope} predictions require annotation_id")
            sample_name = ""
            if sample_id is not None:
                sample = connection.execute(
                    "SELECT name FROM cohort_samples WHERE id = ?", (sample_id,)
                ).fetchone()
                if sample is None:
                    raise ValueError("sample_id does not exist")
                sample_name = sample["name"]
            if annotator.match.scope is not MatchScope.SAMPLE_HAPLOTYPE:
                sample_id = None
                sample_name = ""
            elif sample_id is None:
                raise ValueError("sample_haplotype predictions require sample_id")
            supplied_variant_dimensions = {
                "chromosome": normalize_chromosome(str(target.get("chromosome", ""))),
                "position": target.get("position", ""),
                "reference": str(target.get("reference", "")).upper(),
                "alternate": str(target.get("alternate", "")).upper(),
            }
            actual_variant_dimensions = {
                "chromosome": normalize_chromosome(variant["chrom"]),
                "position": int(variant["pos"]),
                "reference": variant["ref"].upper(),
                "alternate": variant["alt"].upper(),
            }
            for dimension, supplied in supplied_variant_dimensions.items():
                if (
                    dimension in target
                    and not _prediction_dimension_missing(
                        target[dimension], MatchDimension(dimension)
                    )
                    and supplied != actual_variant_dimensions[dimension]
                ):
                    raise ValueError(
                        f"target {dimension} contradicts variant_id"
                    )
            if (
                "sample" in target
                and target["sample"] != sample_name
            ):
                raise ValueError("target sample contradicts sample_id")
            canonical_target = dict(target)
            canonical_target.setdefault("ensembl_gene", identifiers["gene_id"])
            canonical_target.setdefault("gene_symbol", identifiers["gene_symbol"])
            transcript_policy = (
                TranscriptVersionPolicy.EXACT
                if (
                    annotator.match.transcript_version
                    is TranscriptVersionPolicy.EXACT_THEN_STABLE_ID
                    and provenance_object.get("match") == "exact_version"
                )
                else annotator.match.transcript_version
            )
            canonical_target["ensembl_transcript"] = _stable_transcript(
                str(
                    canonical_target.get("ensembl_transcript")
                    or identifiers["transcript_id"]
                ),
                transcript_policy,
            )
            canonical_target.setdefault("consequence", identifiers["consequence"])
            canonical_target.setdefault(
                "amino_acid_change", identifiers["protein_change"]
            )
            canonical_target.setdefault(
                "protein_position",
                _protein_position_from_change(identifiers["protein_change"]),
            )
            if match_status == "exact":
                missing_dimensions = [
                    dimension.value for dimension in annotator.match.dimensions
                    if dimension not in _VARIANT_MATCH_DIMENSIONS
                    and dimension is not MatchDimension.SAMPLE
                    and _prediction_dimension_missing(
                        canonical_target.get(dimension.value, ""), dimension
                    )
                ]
                if missing_dimensions:
                    raise ValueError(
                        "exact prediction is missing match dimensions: "
                        + ", ".join(missing_dimensions)
                    )
            target_key = _canonical_prediction_target_key(
                annotator,
                canonical_target,
                chrom=variant["chrom"],
                pos=variant["pos"],
                ref=variant["ref"],
                alt=variant["alt"],
                sample=sample_name,
            )
            target_dimensions = set(annotator.match.dimensions)
            stored_identifiers = {
                "gene_id": (
                    str(canonical_target.get("ensembl_gene") or "")
                    if MatchDimension.ENSEMBL_GENE in target_dimensions else ""
                ),
                "gene_symbol": (
                    str(canonical_target.get("gene_symbol") or "")
                    if MatchDimension.GENE_SYMBOL in target_dimensions else ""
                ),
                "transcript_id": (
                    str(canonical_target.get("ensembl_transcript") or "")
                    if MatchDimension.ENSEMBL_TRANSCRIPT in target_dimensions
                    else ""
                ),
                "protein_change": (
                    str(
                        canonical_target.get("amino_acid_change")
                        or identifiers["protein_change"]
                    )
                    if target_dimensions & {
                        MatchDimension.PROTEIN_POSITION,
                        MatchDimension.AMINO_ACID_CHANGE,
                    } else ""
                ),
            }
            provenance_object.setdefault(
                "matched_dimensions",
                [] if match_status == "unmatched" else [
                    dimension.value for dimension in annotator.match.dimensions
                    if match_status == "exact"
                    or dimension in _VARIANT_MATCH_DIMENSIONS
                    or not _prediction_dimension_missing(
                        canonical_target.get(dimension.value, ""), dimension
                    )
                ],
            )
            existing_observation = connection.execute(
                """
                SELECT id, provenance_json FROM prediction_observations
                WHERE release_id = ? AND predictor_id = ? AND variant_id = ?
                  AND target_scope = ? AND target_key = ?
                  AND source_identity = 'manual'
                """,
                (
                    release_id, predictor_id, variant_id,
                    target_scope, target_key,
                ),
            ).fetchone()
            if existing_observation is not None and withhold_filterable:
                withheld_metrics.extend(
                    row["metric"] for row in connection.execute(
                        """
                        SELECT metric FROM prediction_values
                        WHERE observation_id = ?
                        """,
                        (existing_observation["id"],),
                    )
                )
            if existing_observation is not None and not replace_values:
                merged_provenance = self._decode_prediction_json(
                    existing_observation["provenance_json"]
                )
                merged_provenance.update(provenance_object)
                provenance_object = merged_provenance
            for metric_id in removed_provenance_metrics:
                provenance_object.pop(metric_id, None)
            inherited_withheld = provenance_object.get(
                "withheld_metrics", []
            )
            withheld_set = set(
                inherited_withheld
                if isinstance(inherited_withheld, list) else []
            )
            if withhold_filterable:
                withheld_set.update(withheld_metrics)
            else:
                withheld_set.difference_update(
                    row[0] for row in typed_values
                )
            if withheld_set:
                provenance_object["withheld_metrics"] = sorted(withheld_set)
            else:
                provenance_object.pop("withheld_metrics", None)
            provenance_json = self._prediction_json(
                provenance_object, "provenance"
            )
            connection.execute(
                """
                INSERT INTO prediction_observations(
                    release_id, source_file_id, source_identity, predictor_id,
                    variant_id, annotation_id, sample_id, target_scope,
                    target_key, gene_id, gene_symbol,
                    transcript_id, protein_change, match_status, matcher,
                    provenance_json, created_at, updated_at
                ) VALUES (?, NULL, 'manual', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(
                    release_id, predictor_id, variant_id,
                    target_scope, target_key, source_identity
                ) DO UPDATE SET
                    annotation_id=excluded.annotation_id,
                    sample_id=excluded.sample_id,
                    gene_id=excluded.gene_id,
                    gene_symbol=excluded.gene_symbol,
                    transcript_id=excluded.transcript_id,
                    protein_change=excluded.protein_change,
                    match_status=excluded.match_status,
                    matcher=excluded.matcher,
                    provenance_json=excluded.provenance_json,
                    updated_at=excluded.updated_at
                """,
                (
                    release_id, predictor_id, variant_id, annotation_id,
                    sample_id, target_scope, target_key,
                    stored_identifiers["gene_id"],
                    stored_identifiers["gene_symbol"],
                    stored_identifiers["transcript_id"],
                    stored_identifiers["protein_change"],
                    match_status, matcher.strip(), provenance_json, now, now,
                ),
            )
            observation_id = connection.execute(
                """
                SELECT id FROM prediction_observations
                WHERE release_id = ? AND predictor_id = ? AND variant_id = ?
                  AND target_scope = ? AND target_key = ?
                  AND source_identity = 'manual'
                """,
                (
                    release_id, predictor_id, variant_id,
                    target_scope, target_key,
                ),
            ).fetchone()[0]
            if replace_values:
                connection.execute(
                    "DELETE FROM prediction_values WHERE observation_id = ?",
                    (observation_id,),
                )
            elif removed_metrics:
                placeholders = ",".join("?" for _ in removed_metrics)
                connection.execute(
                    f"""
                    DELETE FROM prediction_values
                    WHERE observation_id = ? AND metric IN ({placeholders})
                    """,
                    (observation_id, *removed_metrics),
                )
            connection.executemany(
                """
                INSERT INTO prediction_values(
                    observation_id, metric, value_type, numeric_value,
                    text_value, boolean_value, unit
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(observation_id, metric) DO UPDATE SET
                    value_type=excluded.value_type,
                    numeric_value=excluded.numeric_value,
                    text_value=excluded.text_value,
                    boolean_value=excluded.boolean_value,
                    unit=excluded.unit
                """,
                [
                    (observation_id, metric, value_type, numeric, text, boolean, unit)
                    for metric, value_type, numeric, text, boolean, unit
                    in typed_values
                ],
            )
            row = connection.execute(
                self._prediction_select() + " WHERE observation.id = ?",
                (observation_id,),
            ).fetchone()
            assert row is not None
            result = self._serialize_prediction_observations(connection, [row])[0]
        return result

    def query_predictions(
        self,
        *,
        observation_id: int | None = None,
        release_id: int | None = None,
        predictor_id: str | None = None,
        variant_id: int | None = None,
        annotation_id: int | None = None,
        sample_id: int | None = None,
        target_scope: str | None = None,
        limit: int = 1000,
    ) -> list[dict]:
        """Return generic predictions without changing legacy query results."""
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 10_000:
            raise ValueError("limit must be between 1 and 10000")
        if predictor_id is not None:
            try:
                predictor = PREDICTOR_REGISTRY.predictors_by_id[predictor_id]
            except KeyError as error:
                raise ValueError(f"unknown predictor: {predictor_id}") from error
            expected_scope = PREDICTOR_REGISTRY.annotators_by_id[
                predictor.annotator_id
            ].match.scope.value
            if target_scope is not None and target_scope != expected_scope:
                raise ValueError(
                    f"target_scope for {predictor_id} must be {expected_scope}"
                )
        conditions: list[str] = []
        parameters: list[object] = []
        for column, value in (
            ("observation.id", observation_id),
            ("observation.release_id", release_id),
            ("observation.predictor_id", predictor_id),
            ("observation.variant_id", variant_id),
            ("observation.annotation_id", annotation_id),
            ("observation.sample_id", sample_id),
            ("observation.target_scope", target_scope),
        ):
            if value is not None:
                conditions.append(f"{column} = ?")
                parameters.append(value)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        with self._session() as connection:
            rows = connection.execute(
                self._prediction_select()
                + where
                + " ORDER BY predictor_release.priority DESC, observation.id LIMIT ?",
                (*parameters, limit),
            ).fetchall()
            return self._serialize_prediction_observations(connection, rows)

    def filter_predictions(
        self,
        *,
        metric: str,
        operator: str,
        value: float | int | str | bool,
        release_id: int | None = None,
        predictor_id: str | None = None,
        target_scope: str | None = None,
        limit: int = 1000,
    ) -> list[dict]:
        """Filter one typed predictor metric using indexed value columns."""
        metric = self._required_prediction_text(metric, "metric")
        predictor_id = self._required_prediction_text(
            predictor_id, "predictor_id"
        )
        try:
            predictor = PREDICTOR_REGISTRY.predictors_by_id[predictor_id]
        except KeyError as error:
            raise ValueError(f"unknown predictor: {predictor_id}") from error
        metrics_by_id = {
            definition.id: definition for definition in predictor.metrics
        }
        try:
            metric_definition = metrics_by_id[metric]
        except KeyError as error:
            raise ValueError(
                f"unknown metric for {predictor_id}: {metric}"
            ) from error
        if not metric_definition.filterable:
            raise ValueError(f"metric {predictor_id}.{metric} is not filterable")
        expected_scope = PREDICTOR_REGISTRY.annotators_by_id[
            predictor.annotator_id
        ].match.scope.value
        if target_scope is not None and target_scope != expected_scope:
            raise ValueError(
                f"target_scope for {predictor_id} must be {expected_scope}"
            )
        operators = {
            "eq": "=", "=": "=", "ne": "!=", "!=": "!=",
            "lt": "<", "<": "<", "lte": "<=", "<=": "<=",
            "gt": ">", ">": ">", "gte": ">=", ">=": ">=",
        }
        sql_operator = operators.get(operator)
        if sql_operator is None:
            raise ValueError("unsupported prediction filter operator")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 10_000:
            raise ValueError("limit must be between 1 and 10000")
        if isinstance(value, bool):
            value_type = "boolean"
            value_column = "typed_value.boolean_value"
            sql_value: object = int(value)
        elif isinstance(value, (int, float)):
            value_type = "number"
            value_column = "typed_value.numeric_value"
            sql_value = float(value)
            if not math.isfinite(sql_value):
                raise ValueError("prediction filter value must be finite")
        elif isinstance(value, str):
            value_type = "text"
            value_column = "typed_value.text_value"
            sql_value = value
        else:
            raise ValueError("prediction filter value has an unsupported type")
        _validated_prediction_metric(metric_definition, value)
        if value_type != "number" and sql_operator not in {"=", "!="}:
            raise ValueError("text and Boolean prediction filters support only = and !=")

        conditions = [
            "typed_value.metric = ?",
            f"typed_value.value_type = '{value_type}'",
            f"{value_column} {sql_operator} ?",
        ]
        parameters: list[object] = [metric, sql_value]
        for column, filter_value in (
            ("observation.release_id", release_id),
            ("observation.predictor_id", predictor_id),
            ("observation.target_scope", target_scope),
        ):
            if filter_value is not None:
                conditions.append(f"{column} = ?")
                parameters.append(filter_value)
        select = self._prediction_select() + """
            JOIN prediction_values AS typed_value
              ON typed_value.observation_id = observation.id
        """
        with self._session() as connection:
            rows = connection.execute(
                select
                + " WHERE " + " AND ".join(conditions)
                + " ORDER BY predictor_release.priority DESC, observation.id LIMIT ?",
                (*parameters, limit),
            ).fetchall()
            return self._serialize_prediction_observations(connection, rows)

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
                DROP TABLE prediction_values;
                DROP TABLE prediction_observations;
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
        content_probe = _content_probe(path)
        content_sha = _full_content_sha256(path)
        with self._session() as connection:
            existing = connection.execute(
                "SELECT * FROM cohort_files WHERE path = ?", (str(path),)
            ).fetchone()
            existing_probe = (
                (existing["content_probe"] if "content_probe" in existing.keys() else "")
                if existing else ""
            )
            existing_sha = (
                (existing["content_sha256"] if "content_sha256" in existing.keys() else "")
                if existing else ""
            )
            if (
                existing and not force
                # Identity-less rows reindex once (same rationale as the
                # main gate: size+mtime alone cannot honestly say
                # "unchanged").
                and (existing_probe or existing_sha)
                and existing["size_bytes"] == stat.st_size
                and existing["mtime_ns"] == stat.st_mtime_ns
                and (not existing_probe or existing_probe == content_probe)
                and (not existing_sha or not content_sha or existing_sha == content_sha)
                and ((existing_probe and content_probe)
                     or (existing_sha and content_sha))
                and existing["import_profile"] == import_profile
                and existing["analysis_scope"] == analysis_scope
            ):
                # Backfill the full hash onto probe-era rows (same
                # rationale as the main gate; identity-less rows reindex
                # instead of reaching this branch).
                if (content_sha and not existing_sha) or (
                    content_probe and not existing_probe
                ):
                    # Monotone: a freshly computed empty value (read
                    # failure) must never erase stored identity.
                    connection.execute(
                        "UPDATE cohort_files SET "
                        "content_probe=CASE WHEN ?!='' THEN ? "
                        "ELSE content_probe END, "
                        "content_sha256=CASE WHEN ?!='' THEN ? "
                        "ELSE content_sha256 END WHERE id=?",
                        (content_probe, content_probe,
                         content_sha, content_sha, existing["id"]),
                    )
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
                  lifted_from_assembly, import_profile, analysis_scope,
                  content_probe, content_sha256
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(path), stat.st_size, stat.st_mtime_ns, utc_now(),
                    "GRCh38",
                    "GRCh37" if assembly["lifted_from_grch37"] else None,
                    import_profile,
                    analysis_scope,
                    content_probe, content_sha,
                ),
            ).lastrowid

            samples: list[str] = []
            sample_ids: list[int] = []
            csq_fields: list[str] = []
            info_fields: set[str] = set()
            active_predictors: tuple = ()
            saw_fileformat = False
            saw_columns = False
            pass_records = 0
            excluded_records = 0
            carrier_count = 0
            variant_cache: dict[str, int] = {}
            annotation_cache: set[tuple] = set()
            embedded_release_ids: dict[str, int] = {}
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
                        if line.startswith("##INFO=<ID="):
                            id_match = re.match(r"##INFO=<ID=([^,>]+)", line)
                            if id_match:
                                info_fields.add(id_match.group(1))
                            if id_match and id_match.group(1) == "CSQ":
                                match = re.search(
                                    r"Format:\s*([^\">]+)",
                                    line,
                                    re.IGNORECASE,
                                )
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
                            active_predictors = _active_predictors(
                                (*csq_fields, *info_fields)
                            )
                            continue
                        if not line.strip() or line.startswith("#"):
                            continue
                        if not saw_columns:
                            raise ValueError("VCF #CHROM header was not found before records")

                        records_processed += 1
                        columns = line.rstrip("\n").split("\t")
                        if len(columns) < 10:
                            position = ":".join(columns[:2]) if len(columns) >= 2 else "?"
                            raise ValueError(
                                f"record {position} has {len(columns)} "
                                f"column(s) but the header declares "
                                f"{len(sample_ids)} sample(s) — the file "
                                "looks truncated; re-export it and import "
                                "again"
                            )
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
                        if len(sample_values) < len(sample_ids):
                            raise ValueError(
                                f"record {chrom_raw}:{pos_raw} has "
                                f"{len(sample_values)} sample column(s) but "
                                f"the header declares {len(sample_ids)} "
                                "samples — the file looks truncated; "
                                "re-export it and import again"
                            )
                        qual = parse_number(qual_raw)

                        alts = tuple(alt_raw.split(","))
                        for alt_index, alt in enumerate(alts):
                            carrier_rows = []
                            for sample_index, sample_id in enumerate(sample_ids):
                                genotype = parse_genotype(
                                    format_value,
                                    sample_values[sample_index],
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

                            matching = [
                                consequence
                                for consequence in consequences
                                if _consequence_matches_alt(
                                    consequence,
                                    ref=ref,
                                    alts=alts,
                                    alt_index=alt_index,
                                )
                            ]
                            if not matching and len(alts) == 1:
                                matching = consequences

                            prediction_info = _prediction_info_for_alt(
                                info,
                                alt=alt,
                                alt_index=alt_index,
                                alts=alts,
                            )

                            annotation_records = _prediction_annotation_records(
                                prediction_info, matching
                            )
                            annotations = [
                                annotation_from(
                                    record, predictors=active_predictors
                                )
                                for record in annotation_records
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
                                annotation_id = connection.execute(
                                    """
                                    SELECT id FROM cohort_annotations
                                    WHERE variant_id = ? AND gene = ?
                                      AND COALESCE(transcript, '') = ?
                                      AND COALESCE(hgvsc, '') = ?
                                      AND COALESCE(hgvsp, '') = ?
                                      AND consequence = ?
                                    """,
                                    annotation_key,
                                ).fetchone()[0]
                                self._write_embedded_predictions(
                                    connection,
                                    variant_id=variant_id,
                                    annotation_id=annotation_id,
                                    annotation=annotation,
                                    chrom=chrom,
                                    pos=pos,
                                    ref=ref,
                                    alt=alt,
                                    release_ids=embedded_release_ids,
                                    source_file_id=file_id,
                                    content_sha256=content_sha,
                                    content_probe=content_probe,
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
                                haplotype_prediction = (
                                    _normalized_haplotype_prediction(
                                        haplotype,
                                        sample=sample_name,
                                        phase_set=genotype["phase_set"],
                                    )
                                )
                                if haplotype_prediction:
                                    self._write_embedded_predictions(
                                        connection,
                                        variant_id=variant_id,
                                        annotation_id=None,
                                        annotation=None,
                                        chrom=chrom,
                                        pos=pos,
                                        ref=ref,
                                        alt=alt,
                                        release_ids=embedded_release_ids,
                                        source_file_id=file_id,
                                        content_sha256=content_sha,
                                        content_probe=content_probe,
                                        predictions=(haplotype_prediction,),
                                        sample_id=sample_id,
                                        sample_name=sample_name,
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
    , content_hint: str | None = None, content_addressed: bool = False,
    full_sha: str | None = None, display_name: str | None = None) -> PreparedVcf:
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
        # The working-copy cache must observe the same content probe as the
        # import-identity gate: keying on size+mtime alone let a same-size,
        # timestamp-restored content swap serve a STALE prepared VCF to a
        # re-import the identity gate had correctly triggered — and the
        # managed-copy step then filed those stale bytes under the new
        # source checksum.
        # The full digest (for files small enough to hash) binds the cache
        # entry to the bytes themselves: with the probe alone, a probe-blind
        # edit re-imported after the identity gate correctly fired still
        # read THIS stale working copy — and the import then stamped its row
        # with the new file's hash, laundering stale data as verified.
        if content_addressed and content_hint:
            # The caller verified content_hint IS the full sha256 of these
            # exact bytes (snapshot flow): the cache entry is addressed by
            # the content alone, so uniquely named snapshots of identical
            # imports share one entry and a swapped path can never alias.
            fingerprint = hashlib.sha256(
                f"content\0{content_hint}".encode()
            ).hexdigest()[:20]
        else:
            # Callers that already hold the file's full digest pass it down
            # (full_sha) so the fingerprint never pays a second full read.
            fingerprint = hashlib.sha256(
                f"{path}\0{stat.st_size}\0{stat.st_mtime_ns}\0"
                f"{_content_probe(path)}\0"
                f"{full_sha if full_sha is not None else _full_content_sha256(path)}\0"
                f"{content_hint or ''}".encode()
            ).hexdigest()[:20]
        cache_dir = self.prepared_dir / fingerprint
        cache_dir.mkdir(parents=True, exist_ok=True)
        source_name = path.name
        for suffix in (".vcf.gz", ".vcf", ".gz"):
            if source_name.lower().endswith(suffix):
                source_name = source_name[:-len(suffix)]
                break
        if content_addressed and content_hint:
            # Snapshot inputs carry a unique name per import; the cache
            # entry must not inherit it or identical content never hits.
            source_name = "content"
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
        preclean = [temporary, Path(f"{temporary}.tbi"), Path(f"{temporary}.csi")]
        if not (content_addressed and content_hint):
            # Path-keyed cache dirs are private to one source path; a
            # stale final entry there is safe to sweep. Content-addressed
            # dirs are SHARED by every concurrent import of identical
            # content — deleting the final files here destroyed a sibling
            # importer's just-published entry mid-copy. Publishes are
            # atomic os.replace, so leaving a stale final is safe: this
            # build simply replaces it.
            preclean += [prepared, Path(f"{prepared}.tbi"), Path(f"{prepared}.csi")]
        for candidate in preclean:
            if candidate.exists():
                candidate.unlink()
        try:
            if is_bgzf(path):
                # Stage, then publish atomically: copying the final path
                # directly let two same-fingerprint builders interleave.
                shutil.copy2(path, temporary)
                os.replace(temporary, prepared)
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
                f"automatic BGZF preparation/indexing failed for "
                f"{display_name or path.name}; "
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

        The destination is filed UNDER content_key, so every byte published
        here must provably descend from content that hashes to that key.
        Re-reading the live source path cannot prove it: an A->B->A swap
        timed around the reads passed every re-check while the prepared
        bytes were B's — and REPLACED a healthy managed file. The source is
        therefore snapshotted ONCE, hashed as it streams (any size — the
        caller already paid full hashes of these bytes), and every later
        step (re-encode, copy, publish) reads only the frozen snapshot.
        """
        source = source.resolve()
        destination_directory.mkdir(parents=True, exist_ok=True)
        snapshot_suffix = (
            ".vcf.gz" if source.name.lower().endswith((".gz", ".bgz")) else ".vcf"
        )
        snapshot = destination_directory / (
            f".{content_key}.{uuid.uuid4().hex}.snapshot{snapshot_suffix}"
        )
        try:
            snapshot_sha = _copy_with_sha256(source, snapshot)
            if snapshot_sha != content_key:
                raise ValueError(
                    f"{source.name} changed while it was being imported — "
                    "wait for the file to finish copying, then import it "
                    "again"
                )
            return self._prepare_managed_from_snapshot(
                source, snapshot, destination_directory, content_key
            )
        finally:
            snapshot.unlink(missing_ok=True)

    def _prepare_managed_from_snapshot(
        self, source: Path, snapshot: Path,
        destination_directory: Path, content_key: str,
    ) -> tuple[Path, Path | None, str]:
        # A BGZF snapshot needs no re-encoding: it is published straight
        # from the verified bytes and indexed at the destination, so an
        # already-indexed pipeline output never materializes a THIRD full
        # copy in the prepare cache (an 8 GiB source would otherwise cost
        # +8 GiB steady-state). Sources needing a sort/re-encode go
        # through the content-addressed prepare cache; an unsorted BGZF
        # falls back to it too when destination indexing fails.
        if is_bgzf(snapshot) and self.hts_backend is not None:
            prepared = PreparedVcf(snapshot, snapshot, None, False, False)
        else:
            # The prepare cache is keyed by the verified content itself:
            # the snapshot path is unique per import, and content identity
            # is exactly what the destination filename promises.
            prepared = self._prepare_indexed_vcf(
                snapshot, content_hint=content_key, content_addressed=True,
                display_name=source.name,
            )
        compressed = prepared.path.name.lower().endswith((".gz", ".bgz"))
        destination = destination_directory / (
            f"{content_key}.vcf.gz" if compressed else f"{content_key}.vcf"
        )
        rebuild_destination = False
        if destination.is_file() and prepared.path.is_file():
            # A destination poisoned by the pre-fix cache (stale bytes filed
            # under this key) must be repaired, not trusted forever. The
            # prepared representation may legitimately differ from the raw
            # source (re-encoding), so compare against the prepared bytes.
            try:
                if (
                    destination.stat().st_size != prepared.path.stat().st_size
                    or _content_probe(destination) != _content_probe(prepared.path)
                ):
                    rebuild_destination = True
            except OSError:
                rebuild_destination = True
        if rebuild_destination:
            for candidate in (
                destination,
                Path(f"{destination}.tbi"),
                Path(f"{destination}.csi"),
            ):
                candidate.unlink(missing_ok=True)
        if not destination.is_file():
            # Unique partial name: two concurrent imports of identical content
            # share content_key, and with one deterministic partial the second
            # os.replace raced the first (FileNotFoundError after the first
            # consumed the file). Each writer stages privately; os.replace is
            # atomic and both publish identical bytes.
            temporary = destination.with_name(
                f"{destination.name}.{uuid.uuid4().hex}.partial"
            )
            try:
                shutil.copy2(prepared.path, temporary)
            except FileNotFoundError:
                # The shared content-addressed cache entry can vanish under
                # us (another importer of identical content rebuilding it).
                # The snapshot holds the same verified bytes whenever no
                # re-encoding happened — fall back to it.
                if snapshot.is_file() and (
                    snapshot.name.lower().endswith((".gz", ".bgz")) == compressed
                ):
                    shutil.copy2(snapshot, temporary)
                else:
                    raise
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
            if index is None and prepared.path == snapshot and snapshot.is_file():
                # The direct-publish shortcut met an unsorted BGZF: route
                # it through the prepare cache after all (whose internal
                # fallback re-sorts), and rebuild the destination from the
                # sorted result so it does not stay unindexed.
                sorted_prepared = self._prepare_indexed_vcf(
                    snapshot, content_hint=content_key,
                    content_addressed=True, display_name=source.name,
                )
                if (
                    sorted_prepared.path != snapshot
                    and sorted_prepared.path.is_file()
                ):
                    temporary = destination.with_name(
                        f"{destination.name}.{uuid.uuid4().hex}.partial"
                    )
                    shutil.copy2(sorted_prepared.path, temporary)
                    os.replace(temporary, destination)
                    prepared = sorted_prepared
                    if prepared.index_path and prepared.index_path.is_file():
                        suffix = (
                            ".csi" if prepared.index_path.name.endswith(".csi")
                            else ".tbi"
                        )
                        index = Path(f"{destination}{suffix}")
                        temporary_index = Path(
                            f"{index}.{uuid.uuid4().hex}.partial"
                        )
                        shutil.copy2(prepared.index_path, temporary_index)
                        os.replace(temporary_index, index)

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

    def _merge_stage_predictions(
        self,
        connection: sqlite3.Connection,
        alias: str,
        file_id: int,
        *,
        content_sha256: str = "",
        content_probe: str = "",
    ) -> None:
        resource_ids = [
            row[0] for row in connection.execute(
                f"SELECT DISTINCT resource_id FROM {alias}.stage_prediction_observations"
            )
        ]
        if not resource_ids:
            return
        self._ensure_embedded_predictor_releases(
            connection,
            resource_ids,
            source_file_id=file_id,
            content_sha256=content_sha256,
            content_probe=content_probe,
        )
        release_version, checksum_algorithm, checksum = (
            _embedded_vcf_release_identity(
                file_id, content_sha256, content_probe
            )
        )
        source_identity = f"cohort-file:{file_id}"
        now = utc_now()
        connection.execute(
            f"""
            INSERT INTO prediction_observations(
                release_id, source_file_id, source_identity, predictor_id,
                variant_id, annotation_id, sample_id, target_scope,
                target_key, gene_id, gene_symbol,
                transcript_id, protein_change, match_status, matcher,
                provenance_json, created_at, updated_at
            )
            SELECT predictor_release.id, ?, ?, staged.predictor_id, variant.id,
                   CASE WHEN staged.bind_annotation = 1
                        THEN annotation.id ELSE NULL END,
                   CASE WHEN staged.sample_name != ''
                        THEN sample.id ELSE NULL END,
                   staged.target_scope, staged.target_key, staged.gene_id,
                   staged.gene_symbol, staged.transcript_id,
                   staged.protein_change, staged.match_status, staged.matcher,
                   staged.provenance_json, ?, ?
            FROM {alias}.stage_prediction_observations AS staged
            JOIN cohort_variants AS variant
              ON variant.variant_key = staged.variant_key
            JOIN predictor_releases AS predictor_release
             ON predictor_release.provider = ?
             AND predictor_release.resource_id = staged.resource_id
             AND predictor_release.release_version = ?
             AND predictor_release.checksum_algorithm = ?
             AND predictor_release.checksum = ?
            LEFT JOIN cohort_annotations AS annotation
              ON staged.bind_annotation = 1
             AND annotation.variant_id = variant.id
             AND annotation.gene = staged.annotation_gene
             AND COALESCE(annotation.transcript, '') = staged.annotation_transcript
             AND COALESCE(annotation.hgvsc, '') = staged.annotation_hgvsc
             AND COALESCE(annotation.hgvsp, '') = staged.annotation_hgvsp
             AND annotation.consequence = staged.annotation_consequence
            LEFT JOIN cohort_samples AS sample
              ON sample.file_id = ? AND sample.name = staged.sample_name
            WHERE (staged.bind_annotation = 0 OR annotation.id IS NOT NULL)
              AND (staged.sample_name = '' OR sample.id IS NOT NULL)
            ON CONFLICT(
                release_id, predictor_id, variant_id,
                target_scope, target_key, source_identity
            ) DO UPDATE SET
                annotation_id=CASE
                    WHEN prediction_observations.match_status = 'exact'
                     AND excluded.match_status != 'exact'
                    THEN prediction_observations.annotation_id
                    ELSE excluded.annotation_id END,
                sample_id=COALESCE(
                    excluded.sample_id, prediction_observations.sample_id
                ),
                gene_id=excluded.gene_id,
                gene_symbol=excluded.gene_symbol,
                transcript_id=excluded.transcript_id,
                protein_change=excluded.protein_change,
                match_status=CASE
                    WHEN prediction_observations.match_status = 'exact'
                     AND excluded.match_status != 'exact'
                    THEN prediction_observations.match_status
                    ELSE excluded.match_status END,
                matcher=excluded.matcher,
                provenance_json=CASE
                    WHEN prediction_observations.match_status = 'exact'
                     AND excluded.match_status != 'exact'
                    THEN prediction_observations.provenance_json
                    ELSE excluded.provenance_json END,
                updated_at=excluded.updated_at
            """,
            (
                int(file_id), source_identity, now, now,
                EMBEDDED_VCF_PROVIDER, release_version,
                checksum_algorithm, checksum, int(file_id),
            ),
        )
        connection.execute(
            f"""
            INSERT INTO prediction_values(
                observation_id, metric, value_type, numeric_value,
                text_value, boolean_value, unit
            )
            SELECT observation.id, staged.metric, staged.value_type,
                   staged.numeric_value, staged.text_value,
                   staged.boolean_value, ''
            FROM {alias}.stage_prediction_values AS staged
            JOIN cohort_variants AS variant
              ON variant.variant_key = staged.variant_key
            JOIN predictor_releases AS predictor_release
             ON predictor_release.provider = ?
             AND predictor_release.resource_id = staged.resource_id
             AND predictor_release.release_version = ?
             AND predictor_release.checksum_algorithm = ?
             AND predictor_release.checksum = ?
            JOIN prediction_observations AS observation
              ON observation.release_id = predictor_release.id
             AND observation.predictor_id = staged.predictor_id
             AND observation.variant_id = variant.id
             AND observation.target_scope = staged.target_scope
             AND observation.target_key = staged.target_key
             AND observation.source_identity = ?
            WHERE 1
            ON CONFLICT(observation_id, metric) DO UPDATE SET
                value_type=excluded.value_type,
                numeric_value=excluded.numeric_value,
                text_value=excluded.text_value,
                boolean_value=excluded.boolean_value,
                unit=excluded.unit
            """,
            (
                EMBEDDED_VCF_PROVIDER, release_version,
                checksum_algorithm, checksum, source_identity,
            ),
        )

    def _write_embedded_predictions(
        self,
        connection: sqlite3.Connection,
        *,
        variant_id: int,
        annotation_id: int | None,
        annotation: dict | None,
        chrom: str,
        pos: int,
        ref: str,
        alt: str,
        release_ids: dict[str, int],
        source_file_id: int,
        content_sha256: str = "",
        content_probe: str = "",
        predictions: Iterable[dict] | None = None,
        sample_id: int | None = None,
        sample_name: str = "",
    ) -> None:
        if predictions is None:
            predictions = annotation["_predictions"] if annotation else ()
        predictions = tuple(predictions)
        missing_resources = {
            prediction["resource_id"] for prediction in predictions
            if prediction["resource_id"] not in release_ids
        }
        if missing_resources:
            release_ids.update(
                self._ensure_embedded_predictor_releases(
                    connection,
                    missing_resources,
                    source_file_id=source_file_id,
                    content_sha256=content_sha256,
                    content_probe=content_probe,
                )
            )
        source_identity = f"cohort-file:{source_file_id}"
        now = utc_now()
        for prediction in predictions:
            annotator = PREDICTOR_REGISTRY.annotators_by_id[
                prediction["matcher"]
            ]
            target_key = _canonical_prediction_target_key(
                annotator,
                prediction["target"],
                chrom=chrom,
                pos=pos,
                ref=ref,
                alt=alt,
                sample=sample_name,
            )
            connection.execute(
                """
                INSERT INTO prediction_observations(
                    release_id, source_file_id, source_identity, predictor_id,
                    variant_id, annotation_id, sample_id, target_scope,
                    target_key, gene_id, gene_symbol,
                    transcript_id, protein_change, match_status, matcher,
                    provenance_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(
                    release_id, predictor_id, variant_id,
                    target_scope, target_key, source_identity
                ) DO UPDATE SET
                    annotation_id=CASE
                        WHEN prediction_observations.match_status = 'exact'
                         AND excluded.match_status != 'exact'
                        THEN prediction_observations.annotation_id
                        ELSE excluded.annotation_id END,
                    sample_id=COALESCE(
                        excluded.sample_id, prediction_observations.sample_id
                    ),
                    gene_id=excluded.gene_id,
                    gene_symbol=excluded.gene_symbol,
                    transcript_id=excluded.transcript_id,
                    protein_change=excluded.protein_change,
                    match_status=CASE
                        WHEN prediction_observations.match_status = 'exact'
                         AND excluded.match_status != 'exact'
                        THEN prediction_observations.match_status
                        ELSE excluded.match_status END,
                    matcher=excluded.matcher,
                    provenance_json=CASE
                        WHEN prediction_observations.match_status = 'exact'
                         AND excluded.match_status != 'exact'
                        THEN prediction_observations.provenance_json
                        ELSE excluded.provenance_json END,
                    updated_at=excluded.updated_at
                """,
                (
                    release_ids[prediction["resource_id"]],
                    source_file_id,
                    source_identity,
                    prediction["predictor_id"],
                    variant_id,
                    annotation_id if prediction["bind_annotation"] else None,
                    sample_id,
                    prediction["target_scope"],
                    target_key,
                    prediction["gene_id"],
                    prediction["gene_symbol"],
                    prediction["transcript_id"],
                    prediction["protein_change"],
                    prediction["match_status"],
                    prediction["matcher"],
                    json.dumps(
                        prediction["provenance"],
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    now,
                    now,
                ),
            )
            observation_id = connection.execute(
                """
                SELECT id FROM prediction_observations
                WHERE release_id = ? AND predictor_id = ? AND variant_id = ?
                  AND target_scope = ? AND target_key = ?
                  AND source_identity = ?
                """,
                (
                    release_ids[prediction["resource_id"]],
                    prediction["predictor_id"],
                    variant_id,
                    prediction["target_scope"],
                    target_key,
                    source_identity,
                ),
            ).fetchone()[0]
            typed_rows = []
            for metric, value in prediction["values"].items():
                if isinstance(value, bool):
                    typed = ("boolean", None, None, int(value))
                elif isinstance(value, (int, float)):
                    typed = ("number", float(value), None, None)
                else:
                    typed = ("text", None, str(value), None)
                typed_rows.append((observation_id, metric, *typed, ""))
            connection.executemany(
                """
                INSERT INTO prediction_values(
                    observation_id, metric, value_type, numeric_value,
                    text_value, boolean_value, unit
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(observation_id, metric) DO UPDATE SET
                    value_type=excluded.value_type,
                    numeric_value=excluded.numeric_value,
                    text_value=excluded.text_value,
                    boolean_value=excluded.boolean_value,
                    unit=excluded.unit
                """,
                typed_rows,
            )

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
        content_probe: str = "",
        content_sha256: str = "",
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
                  prefilter_records_scanned, prefilter_records_retained,
                  content_probe, content_sha256
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    content_probe, content_sha256,
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
                self._merge_stage_predictions(
                    connection,
                    alias,
                    file_id,
                    content_sha256=content_sha256,
                    content_probe=content_probe,
                )
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
        content_probe = _content_probe(source_path)
        content_sha = _full_content_sha256(source_path)
        with self._session() as connection:
            existing = connection.execute(
                "SELECT * FROM cohort_files WHERE path = ?", (str(source_path),)
            ).fetchone()
            existing_probe = (
                (existing["content_probe"] if "content_probe" in existing.keys() else "")
                if existing else ""
            )
            existing_sha = (
                (existing["content_sha256"] if "content_sha256" in existing.keys() else "")
                if existing else ""
            )
            if (
                existing and not force
                # A row with NO content identity at all cannot honestly be
                # called "unchanged": size+mtime alone is forgeable, and
                # certifying current bytes onto rows indexed from unknown
                # content would present a trust-on-first-use hash as a
                # verified one. Such rows reindex once and gain identity
                # from the reindex itself — self-extinguishing.
                and (existing_probe or existing_sha)
                and existing["size_bytes"] == stat.st_size
                and existing["mtime_ns"] == stat.st_mtime_ns
                and (not existing_probe or existing_probe == content_probe)
                # Files small enough to hash completely must ALSO match on
                # the full digest — the probe's interior blind windows are
                # a documented tradeoff for multi-GB genomes, not for exome
                # VCFs where a full read costs milliseconds.
                and (not existing_sha or not content_sha or existing_sha == content_sha)
                # At least one comparison must have actually RUN: a row
                # whose only identity field cannot be computed right now
                # (transient read failure) would otherwise pass on
                # size+mtime alone.
                and ((existing_probe and content_probe)
                     or (existing_sha and content_sha))
                and existing["import_profile"] == import_profile
                and existing["analysis_scope"] == analysis_scope
                and existing["prefilter_options"] == prefilter_options_json
            ):
                # Backfill the full hash onto probe-era rows: the probe
                # just matched, so the bytes are the ones the row indexed
                # (to probe resolution), and the hash closes the remaining
                # blind window from here on. Rows with NO identity never
                # reach this branch — they reindex instead, so a
                # trust-on-first-use hash is never presented as verified.
                if (content_sha and not existing_sha) or (
                    content_probe and not existing_probe
                ):
                    # Monotone: a freshly computed empty value (read
                    # failure) must never erase stored identity.
                    connection.execute(
                        "UPDATE cohort_files SET "
                        "content_probe=CASE WHEN ?!='' THEN ? "
                        "ELSE content_probe END, "
                        "content_sha256=CASE WHEN ?!='' THEN ? "
                        "ELSE content_sha256 END WHERE id=?",
                        (content_probe, content_probe,
                         content_sha, content_sha, existing["id"]),
                    )
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

        prepared = self._prepare_indexed_vcf(path, progress, full_sha=content_sha)
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
                content_probe=content_probe,
                content_sha256=content_sha,
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
                            if len(columns) >= 10:
                                warnings.append(
                                    f"{source_path.name}: source record "
                                    f"at {':'.join(columns[:2])} is truncated "
                                    "(missing sample columns) — its evidence "
                                    "falls back to the compact index"
                                )
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
                    if 10 <= len(columns) < 9 + len(header.samples):
                        # The stored review VCF is corrupt (interrupted copy,
                        # or an artifact of the pre-fix silent-skip import):
                        # omitting the row would hide a carrier variant from
                        # the clinician's review set.
                        raise ValueError(
                            f"stored review VCF looks truncated at "
                            f"{':'.join(columns[:2])} — re-import this dataset"
                        )
                    if len(columns) < 10 or columns[6] not in ("PASS", "."):
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
