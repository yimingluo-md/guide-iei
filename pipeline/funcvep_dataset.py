#!/usr/bin/env python3
"""Prepare the official FuncVEP v2 score archive for VEP lookup.

After the user acknowledges the upstream terms, GUIDE-IEI can download the
archive directly from Zenodo or accept an existing local copy. This module
validates the published archive identity, streams its one TSV member, retains
only FuncVEP scores, sorts the result for tabix, and writes a structured
manifest consumed by the generic indexed-score VEP adapter.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import os
import re
import shutil
import struct
import subprocess
import uuid
import zipfile
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO

try:
    from .indexed_scores import MANIFEST_SCHEMA, validate_manifest
except ImportError:  # direct script execution
    from indexed_scores import MANIFEST_SCHEMA, validate_manifest


RECORD_ID = "20595206"
CONCEPT_RECORD_ID = "18432730"
RECORD_URL = f"https://zenodo.org/records/{RECORD_ID}"
DOI = f"10.5281/zenodo.{RECORD_ID}"
PAPER_DOI = "10.1038/s41588-026-02727-3"
ARCHIVE_NAME = "FuncVEP_and_ClinVEP_scores_all_possible_missense_variants.zip"
MEMBER_NAME = "FuncVEP_and_ClinVEP_scores_all_possible_missense_variants.tsv"
SOURCE_HEADER = [
    "ID",
    "ensg",
    "FuncVEP_CTI",
    "FuncVEP_CTE",
    "FuncVEP_SP",
    "ClinVEP_CTI",
    "ClinVEP_CTE",
    "ClinVEP_SP",
]
OUTPUT_COLUMNS = [
    "chrom",
    "position",
    "reference",
    "alternate",
    "ensembl_gene_id",
    "FuncVEP_CTI",
    "FuncVEP_CTE",
    "FuncVEP_SP",
]
OUTPUT_HEADER = "#" + "\t".join(OUTPUT_COLUMNS) + "\n"
MIN_WORKSPACE_BYTES = 20_000_000_000
ENSEMBL_GENE_RE = re.compile(rb"^ENSG[0-9]{11}(?:\.[0-9]+)?$")
CONTIG_RE = re.compile(rb"^(?:[1-9]|1[0-9]|2[0-2]|X|Y|MT)$")


@dataclass(frozen=True)
class ReleasePin:
    record_id: str = RECORD_ID
    archive_name: str = ARCHIVE_NAME
    archive_size: int = 4_236_523_156
    archive_md5: str = "0f6a2f261d14cc1683ccab096e076272"
    member_name: str = MEMBER_NAME
    member_size: int = 11_045_969_397
    member_crc32: int = 0xC433CCFA
    source_header: tuple[str, ...] = tuple(SOURCE_HEADER)

    @property
    def archive_url(self) -> str:
        return (
            f"https://zenodo.org/records/{self.record_id}/files/"
            f"{self.archive_name}?download=1"
        )


PINNED_RELEASE = ReleasePin()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def hash_file(path: Path, *, progress_start: float = 0, progress_end: float = 0) -> dict[str, str]:
    md5_digest = hashlib.md5()
    sha256_digest = hashlib.sha256()
    size = max(1, path.stat().st_size)
    consumed = 0
    last_report = -1
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            md5_digest.update(chunk)
            sha256_digest.update(chunk)
            consumed += len(chunk)
            if progress_end > progress_start:
                percent = int(progress_start + (progress_end - progress_start) * consumed / size)
                if percent != last_report:
                    print(f"{percent:.1f}% verifying official FuncVEP v2 archive", flush=True)
                    last_report = percent
    return {"md5": md5_digest.hexdigest(), "sha256": sha256_digest.hexdigest()}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_archive_identity(archive: Path, pin: ReleasePin) -> dict[str, str]:
    if not archive.is_file():
        raise ValueError(f"FuncVEP archive is missing: {archive}")
    actual_size = archive.stat().st_size
    if actual_size != pin.archive_size:
        raise ValueError(
            f"FuncVEP archive size mismatch: expected {pin.archive_size}, found {actual_size}"
        )
    hashes = hash_file(archive, progress_start=1, progress_end=20)
    if hashes["md5"] != pin.archive_md5:
        raise ValueError(
            f"FuncVEP archive MD5 mismatch: expected {pin.archive_md5}, "
            f"found {hashes['md5']}"
        )
    if archive.name != pin.archive_name:
        print(
            "NOTICE: selected FuncVEP ZIP was renamed by the browser; "
            "official size and checksum verification succeeded",
            flush=True,
        )
    return hashes


def _validate_zip_member(archive: zipfile.ZipFile, pin: ReleasePin) -> zipfile.ZipInfo:
    members = [item for item in archive.infolist() if not item.is_dir()]
    names = [item.filename for item in members]
    if names != [pin.member_name]:
        raise ValueError(
            "FuncVEP ZIP must contain exactly the official TSV member; found: "
            + (", ".join(names) if names else "no files")
        )
    member = members[0]
    if member.flag_bits & 0x1:
        raise ValueError("FuncVEP ZIP member must not be encrypted")
    if member.file_size != pin.member_size:
        raise ValueError(
            f"FuncVEP TSV size mismatch: expected {pin.member_size}, found {member.file_size}"
        )
    if member.CRC != pin.member_crc32:
        raise ValueError(
            f"FuncVEP TSV CRC mismatch: expected {pin.member_crc32:08x}, found {member.CRC:08x}"
        )
    return member


def _normalise_contig(raw: bytes, line_number: int) -> bytes:
    value = raw.strip()
    if value.lower().startswith(b"chr"):
        value = value[3:]
    if value.upper() == b"M":
        value = b"MT"
    value = value.upper() if value.upper() in {b"X", b"Y", b"MT"} else value
    if not CONTIG_RE.fullmatch(value):
        raise ValueError(f"unsupported FuncVEP contig on source line {line_number}: {raw!r}")
    return value


def _contig_sort_key(value: str) -> tuple[int, int | str]:
    if value.isdigit():
        return (0, int(value))
    return (1, {"X": 23, "Y": 24, "MT": 25}.get(value, value))


def _validated_score(value: bytes, label: str, line_number: int) -> bytes:
    value = value.strip()
    if value.lower() in {b"", b".", b"na", b"n/a", b"nan"}:
        return b"."
    try:
        number = float(value)
    except ValueError as exc:
        raise ValueError(f"invalid {label} on source line {line_number}") from exc
    if not math.isfinite(number) or not 0 <= number <= 1:
        raise ValueError(f"out-of-range {label} on source line {line_number}")
    return value


def _sort_partition(source: Path, destination: Path, temporary_directory: Path) -> None:
    sort_program = shutil.which("sort")
    if not sort_program:
        raise RuntimeError("the system sort command is required for FuncVEP preparation")
    environment = dict(os.environ)
    environment["LC_ALL"] = "C"
    with destination.open("wb") as output:
        subprocess.run(
            [
                sort_program,
                "-T",
                str(temporary_directory),
                "-t",
                "\t",
                "-k2,2n",
                "-k3,3",
                "-k4,4",
                "-k5,5",
                str(source),
            ],
            stdout=output,
            stderr=subprocess.PIPE,
            env=environment,
            check=True,
        )


def prepare_archive(
    archive_path: Path,
    output_path: Path,
    stats_path: Path,
    work_directory: Path,
    *,
    pin: ReleasePin = PINNED_RELEASE,
    check_space: bool = True,
) -> dict:
    """Validate and stream a pinned ZIP into a sorted plain FuncVEP TSV.

    ``pin`` and ``check_space`` are injectable only for tiny unit fixtures; the
    command-line interface always uses the immutable official release pin and
    enforces the production free-space guard.
    """

    archive_path = archive_path.expanduser().resolve()
    output_path = output_path.resolve()
    stats_path = stats_path.resolve()
    work_directory = work_directory.resolve()
    work_directory.mkdir(parents=True, exist_ok=True)
    if check_space:
        free = shutil.disk_usage(work_directory).free
        if free < MIN_WORKSPACE_BYTES:
            raise ValueError(
                "FuncVEP preparation needs at least 20 GB free on the managed-data "
                f"filesystem; only {free / 1_000_000_000:.1f} GB is available"
            )

    archive_hashes = _validate_archive_identity(archive_path, pin)
    partitions_dir = work_directory / "partitions"
    sorted_dir = work_directory / "sorted"
    partitions_dir.mkdir()
    sorted_dir.mkdir()
    handles: dict[str, BinaryIO] = {}
    counts: Counter[str] = Counter()
    last_report = -1

    try:
        with zipfile.ZipFile(archive_path) as zipped:
            member = _validate_zip_member(zipped, pin)
            with zipped.open(member, "r") as source:
                header = source.readline().decode("utf-8-sig").rstrip("\r\n").split("\t")
                if header != list(pin.source_header):
                    raise ValueError(
                        "FuncVEP TSV header mismatch; expected " + "\t".join(pin.source_header)
                    )
                for line_number, raw_line in enumerate(source, 2):
                    if not raw_line.strip():
                        raise ValueError(f"blank FuncVEP row on source line {line_number}")
                    fields = raw_line.rstrip(b"\r\n").split(b"\t")
                    if len(fields) != len(pin.source_header):
                        raise ValueError(
                            f"FuncVEP source line {line_number} has {len(fields)} columns; "
                            f"expected {len(pin.source_header)}"
                        )
                    identifier, gene, *scores = fields
                    variant = identifier.split(b"-")
                    if len(variant) != 4:
                        raise ValueError(f"invalid FuncVEP variant ID on source line {line_number}")
                    chrom, position_raw, reference, alternate = variant
                    chrom = _normalise_contig(chrom, line_number)
                    try:
                        position = int(position_raw)
                    except ValueError as exc:
                        raise ValueError(
                            f"invalid FuncVEP position on source line {line_number}"
                        ) from exc
                    reference = reference.upper()
                    alternate = alternate.upper()
                    if (
                        position < 1
                        or len(reference) != 1
                        or len(alternate) != 1
                        or reference not in b"ACGT"
                        or alternate not in b"ACGT"
                        or reference == alternate
                    ):
                        raise ValueError(f"invalid FuncVEP SNV on source line {line_number}")
                    if not ENSEMBL_GENE_RE.fullmatch(gene):
                        raise ValueError(
                            f"invalid Ensembl gene ID on source line {line_number}: "
                            f"{gene.decode('ascii', errors='replace')}"
                        )
                    gene = gene.split(b".", 1)[0]
                    # FuncVEP intentionally withholds a model's score for that
                    # model's own training variants. Preserve those missing
                    # values as '.'; validate only the three retained FuncVEP
                    # columns, never the discarded ClinVEP columns.
                    retained_scores = []
                    for index, score in enumerate(scores[:3]):
                        label = pin.source_header[index + 2]
                        validated = _validated_score(score, label, line_number)
                        retained_scores.append(validated)
                        if validated == b".":
                            counts[f"missing:{label}"] += 1

                    contig = chrom.decode("ascii")
                    if contig not in handles:
                        handles[contig] = (partitions_dir / f"{contig}.tsv").open("wb")
                    handles[contig].write(
                        b"\t".join(
                            [
                                chrom,
                                str(position).encode("ascii"),
                                reference,
                                alternate,
                                gene,
                                retained_scores[0],
                                retained_scores[1],
                                retained_scores[2],
                            ]
                        )
                        + b"\n"
                    )
                    counts["rows"] += 1
                    counts[f"contig:{contig}"] += 1
                    if counts["rows"] % 250_000 == 0:
                        percent = int(20 + 36 * source.tell() / max(1, member.file_size))
                        if percent != last_report:
                            print(
                                f"{percent:.1f}% reading FuncVEP scores ({counts['rows']:,} rows)",
                                flush=True,
                            )
                            last_report = percent
    except zipfile.BadZipFile as exc:
        raise ValueError(f"FuncVEP ZIP failed CRC/integrity validation: {exc}") from exc
    finally:
        for handle in handles.values():
            handle.close()

    if not counts["rows"]:
        raise ValueError("FuncVEP source table has no score rows")
    contigs = sorted(handles, key=_contig_sort_key)
    for index, contig in enumerate(contigs, 1):
        print(
            f"{56 + 22 * index / len(contigs):.1f}% sorting FuncVEP chromosome {contig}",
            flush=True,
        )
        _sort_partition(
            partitions_dir / f"{contig}.tsv",
            sorted_dir / f"{contig}.tsv",
            work_directory,
        )
        (partitions_dir / f"{contig}.tsv").unlink()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    previous_key: tuple[bytes, ...] | None = None
    written = 0
    with output_path.open("wb") as destination:
        destination.write(OUTPUT_HEADER.encode("ascii"))
        for index, contig in enumerate(contigs, 1):
            sorted_partition = sorted_dir / f"{contig}.tsv"
            with sorted_partition.open("rb") as source:
                for raw_line in source:
                    fields = raw_line.rstrip(b"\n").split(b"\t")
                    key = tuple(fields[:5])
                    if previous_key == key:
                        locus = b":".join((fields[0], fields[1])).decode("ascii")
                        raise ValueError(
                            "duplicate FuncVEP allele/gene record after normalization at "
                            f"{locus} {fields[2].decode()}>{fields[3].decode()} "
                            f"{fields[4].decode()}"
                        )
                    previous_key = key
                    destination.write(raw_line)
                    written += 1
            sorted_partition.unlink()
            print(
                f"{78 + 10 * index / len(contigs):.1f}% assembling indexed FuncVEP table",
                flush=True,
            )
    if written != counts["rows"]:
        raise RuntimeError("FuncVEP row count changed while sorting")

    stats = {
        "archive": {
            "name": pin.archive_name,
            "size": pin.archive_size,
            "md5": archive_hashes["md5"],
            "sha256": archive_hashes["sha256"],
            "preserved": True,
        },
        "source_member": {
            "name": pin.member_name,
            "size": pin.member_size,
            "crc32": f"{pin.member_crc32:08x}",
            "header": list(pin.source_header),
        },
        "output_columns": OUTPUT_COLUMNS,
        "row_count": written,
        # JSON object keys are sorted below for deterministic output, which
        # would otherwise turn 1,2,...,10 into 1,10,...,2 when the stats file
        # is read back. Preserve the biological/table order explicitly for
        # the final tabix-header validation.
        "contig_order": contigs,
        "contigs": {contig: counts[f"contig:{contig}"] for contig in contigs},
        "excluded_columns": ["ClinVEP_CTI", "ClinVEP_CTE", "ClinVEP_SP"],
        "missing_scores": {
            label: counts[f"missing:{label}"] for label in SOURCE_HEADER[2:5]
        },
        "prepared_at": utc_now(),
    }
    stats_path.parent.mkdir(parents=True, exist_ok=True)
    stats_path.write_text(json.dumps(stats, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("89.0% FuncVEP table validated and coordinate-sorted", flush=True)
    return stats


def is_bgzf(path: Path) -> bool:
    with path.open("rb") as handle:
        header = handle.read(12)
        if len(header) < 12 or header[:3] != b"\x1f\x8b\x08" or not header[3] & 4:
            return False
        extra = handle.read(int.from_bytes(header[10:12], "little"))
    cursor = 0
    while cursor + 4 <= len(extra):
        length = int.from_bytes(extra[cursor + 2:cursor + 4], "little")
        if extra[cursor:cursor + 2] == b"BC" and length == 2:
            return True
        cursor += 4 + length
    return False


def tabix_metadata(index_path: Path) -> dict:
    try:
        with gzip.open(index_path, "rb") as handle:
            content = handle.read()
    except (OSError, EOFError) as exc:
        raise ValueError(f"FuncVEP tabix index is unreadable: {exc}") from exc
    if content[:4] != b"TBI\x01" or len(content) < 36:
        raise ValueError("FuncVEP index is not a readable TBI file")
    header = struct.unpack("<8i", content[4:36])
    if header[2:5] != (1, 2, 2):
        raise ValueError("FuncVEP TBI must index columns chrom/position/position")
    names = content[36:36 + header[7]].rstrip(b"\0").decode("ascii").split("\0")
    return {"format": "tabix", "sequence_column": 1, "begin_column": 2, "end_column": 2, "contigs": names}


def build_manifest(
    archive_path: Path,
    score_path: Path,
    index_path: Path,
    stats_path: Path,
    *,
    installed_score_name: str,
    license_acknowledged: bool,
    source_kind: str = "user_supplied_official_archive",
) -> dict:
    if not license_acknowledged:
        raise ValueError("FuncVEP terms must be acknowledged before installation")
    stats = json.loads(stats_path.read_text(encoding="utf-8"))
    if not archive_path.is_file():
        raise ValueError("the original FuncVEP ZIP was not preserved")
    archive_stat = archive_path.stat()
    if archive_stat.st_size != stats["archive"]["size"]:
        raise ValueError("the FuncVEP source ZIP changed during preparation")
    if not score_path.is_file() or not is_bgzf(score_path):
        raise ValueError("prepared FuncVEP score table is not BGZF")
    if not index_path.is_file() or not index_path.stat().st_size:
        raise ValueError("prepared FuncVEP score table lacks a tabix index")
    with gzip.open(score_path, "rt", encoding="ascii") as handle:
        if handle.readline() != OUTPUT_HEADER:
            raise ValueError("prepared FuncVEP table header is invalid")
    index_metadata = tabix_metadata(index_path)
    expected_contigs = stats.get("contig_order")
    if not isinstance(expected_contigs, list) or not all(
        isinstance(contig, str) for contig in expected_contigs
    ):
        # Backward-compatible recovery for a partially completed preparation
        # written before contig_order became explicit.
        expected_contigs = sorted(stats["contigs"], key=_contig_sort_key)
    if index_metadata["contigs"] != expected_contigs:
        raise ValueError("FuncVEP tabix contigs do not match the prepared table")

    payload = {
        "manifest_schema": MANIFEST_SCHEMA,
        "resource": {
            "id": "funcvep",
            "name": "FuncVEP",
            "release": "v2",
            "record": RECORD_ID,
            "concept_record": CONCEPT_RECORD_ID,
            "doi": DOI,
            "paper_doi": PAPER_DOI,
            "publication_date": "2026-06-08",
        },
        "assembly": "GRCh38",
        "applicability": {"consequences": ["missense_variant"]},
        "table": {
            "format": "bgzf_tsv",
            "columns": OUTPUT_COLUMNS,
            "header_prefix": "#",
            "row_count": stats["row_count"],
            "index": index_metadata,
        },
        "match": {
            "required": ["allele", "ensembl_gene_id"],
            "dimensions": {
                "allele": {
                    "chrom": "chrom",
                    "position": "position",
                    "reference": "reference",
                    "alternate": "alternate",
                    "normalization": "vep_matched_variant_alleles",
                },
                "ensembl_gene_id": {
                    "column": "ensembl_gene_id",
                    "query": "ensembl_gene_id",
                    "normalization": "strip_version",
                },
            },
            "exact_status": "exact",
            "partial_status": "partial",
            "ambiguous_status": "ambiguous",
        },
        "outputs": [
            {
                "id": "FuncVEP_CTI",
                "column": "FuncVEP_CTI",
                "type": "number",
                "minimum": 0,
                "maximum": 1,
                "direction": "higher_is_more_functionally_damaging",
                "description": "FuncVEP score with clinically trained component predictors included",
            },
            {
                "id": "FuncVEP_CTE",
                "column": "FuncVEP_CTE",
                "type": "number",
                "minimum": 0,
                "maximum": 1,
                "direction": "higher_is_more_functionally_damaging",
                "description": "FuncVEP score with clinically trained component predictors excluded",
            },
            {
                "id": "FuncVEP_SP",
                "column": "FuncVEP_SP",
                "type": "number",
                "minimum": 0,
                "maximum": 1,
                "direction": "higher_is_more_functionally_damaging",
                "description": "FuncVEP score with other variant-effect predictors excluded",
            },
        ],
        "provenance": {
            "match": "FuncVEP_match",
            "match_status": "FuncVEP_match_status",
            "source_target": "FuncVEP_source_gene",
            "allele_available": "FuncVEP_allele_available",
        },
        "files": {
            "data": {
                "name": installed_score_name,
                "size": score_path.stat().st_size,
                "mtime_ns": score_path.stat().st_mtime_ns,
                "sha256": sha256_file(score_path),
            },
            "index": {
                "name": installed_score_name + ".tbi",
                "size": index_path.stat().st_size,
                "mtime_ns": index_path.stat().st_mtime_ns,
                "sha256": sha256_file(index_path),
            },
        },
        "source": {
            "kind": source_kind,
            "record_url": RECORD_URL,
            "archive": stats["archive"],
            "member": stats["source_member"],
            "excluded_columns": stats["excluded_columns"],
            "redistributed_by_guide_iei": False,
        },
        "license": {
            "name": "PolyForm Strict License 1.0.0",
            "url": "https://polyformproject.org/licenses/strict/1.0.0",
            "acknowledged_by_user": True,
            "acknowledged_at": utc_now(),
            "notice": "GUIDE-IEI does not redistribute FuncVEP code, models, or score data.",
        },
        "installed_at": utc_now(),
    }
    return validate_manifest(payload)


def publish_bundle(
    staged_score: Path,
    staged_index: Path,
    staged_manifest: Path,
    target_score: Path,
    target_manifest: Path,
) -> None:
    """Publish data, index, and manifest transactionally; manifest commits last."""

    payload = json.loads(staged_manifest.read_text(encoding="utf-8"))
    validate_manifest(payload)
    if sha256_file(staged_score) != payload["files"]["data"]["sha256"]:
        raise ValueError("staged FuncVEP data checksum does not match its manifest")
    if sha256_file(staged_index) != payload["files"]["index"]["sha256"]:
        raise ValueError("staged FuncVEP index checksum does not match its manifest")

    target_index = Path(str(target_score) + ".tbi")
    for target in (target_score, target_index, target_manifest):
        target.parent.mkdir(parents=True, exist_ok=True)
    pairs = [
        (staged_score, target_score),
        (staged_index, target_index),
        (staged_manifest, target_manifest),
    ]
    token = uuid.uuid4().hex
    backups: list[tuple[Path, Path]] = []
    published: list[tuple[Path, Path]] = []
    try:
        for _, target in pairs:
            if target.exists() or target.is_symlink():
                backup = target.with_name(f".{target.name}.previous.{token}")
                os.replace(target, backup)
                backups.append((target, backup))
        for staged, target in pairs:
            os.replace(staged, target)
            published.append((staged, target))
    except BaseException:
        for staged, target in reversed(published):
            if target.exists() or target.is_symlink():
                os.replace(target, staged)
        for target, backup in reversed(backups):
            if backup.exists() or backup.is_symlink():
                os.replace(backup, target)
        raise
    else:
        for _, backup in backups:
            backup.unlink(missing_ok=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    prepare = commands.add_parser("prepare", help="validate and sort the official v2 ZIP")
    prepare.add_argument("--archive", required=True, type=Path)
    prepare.add_argument("--output", required=True, type=Path)
    prepare.add_argument("--stats", required=True, type=Path)
    prepare.add_argument("--work-directory", required=True, type=Path)

    finalize = commands.add_parser("finalize", help="validate BGZF/TBI and write manifest")
    finalize.add_argument("--archive", required=True, type=Path)
    finalize.add_argument("--scores", required=True, type=Path)
    finalize.add_argument("--index", required=True, type=Path)
    finalize.add_argument("--stats", required=True, type=Path)
    finalize.add_argument("--output", required=True, type=Path)
    finalize.add_argument("--installed-score-name", required=True)
    finalize.add_argument("--license-acknowledged", action="store_true")
    finalize.add_argument(
        "--source-kind",
        default="user_supplied_official_archive",
        choices=[
            "user_supplied_official_archive",
            "downloaded_from_official_zenodo_record",
        ],
    )

    publish = commands.add_parser("publish", help="transactionally publish a prepared bundle")
    publish.add_argument("--scores", required=True, type=Path)
    publish.add_argument("--index", required=True, type=Path)
    publish.add_argument("--manifest", required=True, type=Path)
    publish.add_argument("--target-scores", required=True, type=Path)
    publish.add_argument("--target-manifest", required=True, type=Path)

    release_pin = commands.add_parser(
        "release-pin", help="print the authoritative automatic-download pin"
    )
    release_pin.add_argument(
        "--format", choices=["json", "tsv"], default="json"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "prepare":
        prepare_archive(args.archive, args.output, args.stats, args.work_directory)
    elif args.command == "finalize":
        payload = build_manifest(
            args.archive.expanduser().resolve(),
            args.scores.resolve(),
            args.index.resolve(),
            args.stats.resolve(),
            installed_score_name=args.installed_score_name,
            license_acknowledged=args.license_acknowledged,
            source_kind=args.source_kind,
        )
        args.output.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        print("98.0% FuncVEP provenance manifest validated", flush=True)
    elif args.command == "publish":
        publish_bundle(
            args.scores.resolve(),
            args.index.resolve(),
            args.manifest.resolve(),
            args.target_scores.expanduser().resolve(),
            args.target_manifest.expanduser().resolve(),
        )
        print("100.0% FuncVEP installation complete; original ZIP preserved", flush=True)
    elif args.command == "release-pin":
        payload = {
            "record_id": PINNED_RELEASE.record_id,
            "archive_name": PINNED_RELEASE.archive_name,
            "archive_url": PINNED_RELEASE.archive_url,
            "archive_size": PINNED_RELEASE.archive_size,
            "archive_md5": PINNED_RELEASE.archive_md5,
        }
        if args.format == "tsv":
            print(
                "\t".join((
                    payload["archive_name"], payload["archive_url"],
                    payload["archive_md5"], str(payload["archive_size"]),
                ))
            )
        else:
            print(json.dumps(payload, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
