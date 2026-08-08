#!/usr/bin/env python3
"""Download, validate, or import the pinned LoGoFunc prediction bundle."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import shutil
import struct
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


RECORD_ID = "13835271"
RECORD_URL = f"https://zenodo.org/records/{RECORD_ID}"
DOI = "10.5281/zenodo.13835271"
PAPER_DOI = "10.1186/s13073-023-01261-9"
DATA_NAME = "LoGoFuncVotingEnsemble_metadata_preds_final.csv.gz"
INDEX_NAME = DATA_NAME + ".tbi"
README_NAME = "README.md"
DATA_MD5 = "d50a4601227140c295985b87a37cea7d"
INDEX_MD5 = "22fbabf44231e18209ec5b401d7de2ff"
README_MD5 = "e5b154114ea91cfda37049ff9f232690"
DATA_SIZE = 3_664_905_552
EXPECTED_HEADER = [
    "#CHROM", "POS", "REF", "ALT", "ID", "Consequence", "SYMBOL",
    "Gene", "Feature", "HGVSp", "AA_pos", "Ref_aa", "Alt_aa",
    "prediction", "LoGoFunc_neutral", "LoGoFunc_GOF", "LoGoFunc_LOF",
]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def md5(path: Path) -> str:
    digest = hashlib.md5()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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


def tabix_contigs(index_path: Path) -> list[str]:
    with gzip.open(index_path, "rb") as handle:
        content = handle.read()
    if content[:4] != b"TBI\x01" or len(content) < 36:
        raise ValueError("LoGoFunc index is not a readable TBI file")
    header = struct.unpack("<8i", content[4:36])
    names = content[36:36 + header[7]].rstrip(b"\0").decode().split("\0")
    if header[2:5] != (1, 2, 2):
        raise ValueError("LoGoFunc TBI does not index CHROM/POS/POS columns")
    return names


def validate(data_path: Path, index_path: Path, sample_rows: int = 100_000) -> dict:
    if not data_path.is_file() or data_path.stat().st_size == 0:
        raise ValueError(f"LoGoFunc data file is missing or empty: {data_path}")
    if not index_path.is_file() or index_path.stat().st_size == 0:
        raise ValueError(f"LoGoFunc tabix index is missing or empty: {index_path}")
    if data_path.stat().st_size != DATA_SIZE:
        raise ValueError(
            f"LoGoFunc data size mismatch: expected {DATA_SIZE}, "
            f"found {data_path.stat().st_size}"
        )
    if not is_bgzf(data_path):
        raise ValueError("LoGoFunc prediction table is not BGZF compressed")

    print("94.0% verifying LoGoFunc source checksums", flush=True)
    data_checksum = md5(data_path)
    index_checksum = md5(index_path)
    if data_checksum != DATA_MD5:
        raise ValueError(
            f"LoGoFunc data MD5 mismatch: expected {DATA_MD5}, found {data_checksum}"
        )
    if index_checksum != INDEX_MD5:
        raise ValueError(
            f"LoGoFunc index MD5 mismatch: expected {INDEX_MD5}, found {index_checksum}"
        )

    contigs = tabix_contigs(index_path)
    required_contigs = {str(value) for value in range(1, 23)} | {"X", "Y"}
    if not required_contigs.issubset(contigs):
        missing = sorted(required_contigs - set(contigs))
        raise ValueError("LoGoFunc index lacks primary contigs: " + ", ".join(missing))

    checked = 0
    previous: tuple[str, int] | None = None
    with gzip.open(data_path, "rt", encoding="utf-8", errors="strict") as handle:
        header = handle.readline().rstrip("\r\n").split("\t")
        if header != EXPECTED_HEADER:
            raise ValueError("LoGoFunc table header does not match Zenodo record 13835271")
        for line in handle:
            if checked >= sample_rows:
                break
            values = line.rstrip("\r\n").split("\t")
            if len(values) != len(EXPECTED_HEADER):
                raise ValueError(f"LoGoFunc row {checked + 2} does not have 17 columns")
            chrom, pos_raw, ref, alt = values[:4]
            pos = int(pos_raw)
            if len(ref) != 1 or len(alt) != 1 or ref not in "ACGT" or alt not in "ACGT":
                raise ValueError(f"LoGoFunc row {checked + 2} is not a canonical SNV")
            if not values[8].startswith("ENST") or not values[9].startswith("ENSP"):
                raise ValueError(f"LoGoFunc row {checked + 2} lacks Ensembl transcript/protein IDs")
            probabilities = [float(value) for value in values[14:17]]
            if any(value < 0 or value > 1 for value in probabilities):
                raise ValueError(f"LoGoFunc row {checked + 2} has an out-of-range probability")
            if abs(sum(probabilities) - 1.0) > 1e-6:
                raise ValueError(f"LoGoFunc row {checked + 2} probabilities do not sum to one")
            predicted = ("Neutral", "GOF", "LOF")[probabilities.index(max(probabilities))]
            if values[13] != predicted:
                raise ValueError(f"LoGoFunc row {checked + 2} prediction is not the argmax class")
            if previous and chrom == previous[0] and pos < previous[1]:
                raise ValueError(f"LoGoFunc table is not sorted at {chrom}:{pos}")
            previous = (chrom, pos)
            checked += 1

    return {
        "schema": EXPECTED_HEADER,
        "sample_rows_checked": checked,
        "contig_count": len(contigs),
        "primary_contigs": sorted(required_contigs),
        "data_md5": data_checksum,
        "index_md5": index_checksum,
    }


def download_file(url: str, destination: Path, start: float, end: float) -> None:
    part = Path(str(destination) + ".part")
    offset = part.stat().st_size if part.exists() else 0
    headers = {"User-Agent": "IEI-Variant-Review/LoGoFunc-setup"}
    if offset:
        headers["Range"] = f"bytes={offset}-"
    request = urllib.request.Request(url, headers=headers)
    response = urllib.request.urlopen(request, timeout=120)
    status = getattr(response, "status", 200)
    if offset and status != 206:
        offset = 0
        part.unlink(missing_ok=True)
    remaining = int(response.headers.get("Content-Length", "0") or 0)
    total = offset + remaining if remaining else 0
    mode = "ab" if offset else "wb"
    last_percent = -1
    with response, part.open(mode) as output:
        downloaded = offset
        while True:
            chunk = response.read(8 * 1024 * 1024)
            if not chunk:
                break
            output.write(chunk)
            downloaded += len(chunk)
            if total:
                percent = int(start + (end - start) * downloaded / total)
                if percent != last_percent:
                    print(f"{percent:.1f}% downloading {destination.name}", flush=True)
                    last_percent = percent
    os.replace(part, destination)


def write_manifest(
    path: Path,
    data_path: Path,
    validation: dict,
    mode: str,
    imported_from: Path | None = None,
) -> None:
    payload = {
        "resource": "LoGoFunc predictions",
        "assembly": "GRCh38",
        "scope": "canonical missense SNVs",
        "zenodo_record": RECORD_ID,
        "source_url": RECORD_URL,
        "source_doi": DOI,
        "paper_doi": PAPER_DOI,
        "publication_date": "2024-09-24",
        "usage_notice": "Zenodo description states academic use only; contact the corresponding author for commercial use.",
        "installed_at": utc_now(),
        "installation_mode": mode,
        "source_path": str(data_path.resolve()),
        "data_file": DATA_NAME,
        "data_size": data_path.stat().st_size,
        **validation,
    }
    if imported_from is not None:
        payload["imported_from"] = str(imported_from.resolve())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def install_move(source: Path, destination: Path) -> None:
    if source.resolve() == destination.resolve(strict=False):
        return
    source_size = source.stat().st_size
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.importing")
    if temporary.exists() or temporary.is_symlink():
        temporary.unlink()
    shutil.move(str(source), str(temporary))
    if temporary.stat().st_size != source_size:
        raise ValueError(f"incomplete LoGoFunc move: {destination}")
    temporary.replace(destination)


def source_file(value: Path) -> Path:
    candidate = value.expanduser().resolve()
    if candidate.is_dir():
        candidate = candidate / DATA_NAME
    return candidate


def command_install(args: argparse.Namespace) -> None:
    source = source_file(args.source)
    index = Path(str(source) + ".tbi")
    validation = validate(source, index)
    source_size = source.stat().st_size
    index_size = index.stat().st_size
    install_move(source, args.output)
    install_move(index, Path(str(args.output) + ".tbi"))
    if args.output.stat().st_size != source_size:
        raise ValueError("LoGoFunc data size changed during managed move")
    if Path(str(args.output) + ".tbi").stat().st_size != index_size:
        raise ValueError("LoGoFunc index size changed during managed move")
    write_manifest(
        args.manifest,
        args.output,
        validation,
        "moved_to_managed_annotation_storage",
        imported_from=source,
    )
    print("100.0% LoGoFunc moved to managed annotation storage; source removed", flush=True)


def command_download(args: argparse.Namespace) -> None:
    args.output.parent.mkdir(parents=True, exist_ok=True)
    base = f"https://zenodo.org/api/records/{RECORD_ID}/files"
    if not args.output.is_file() or args.output.stat().st_size != DATA_SIZE:
        download_file(f"{base}/{DATA_NAME}/content", args.output, 1, 91)
    index = Path(str(args.output) + ".tbi")
    download_file(f"{base}/{INDEX_NAME}/content", index, 91, 93)
    readme = args.output.parent / README_NAME
    download_file(f"{base}/{README_NAME}/content", readme, 93, 94)
    validation = validate(args.output, index)
    if md5(readme) != README_MD5:
        raise ValueError("LoGoFunc README checksum mismatch")
    # Avoid tabix's timestamp-only stale-index warning after sequential download.
    os.utime(index, None)
    write_manifest(args.manifest, args.output, validation, "downloaded_from_zenodo")
    print("100.0% LoGoFunc download and validation complete", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    install = subparsers.add_parser("install")
    install.add_argument("--source", required=True, type=Path)
    install.add_argument("--output", required=True, type=Path)
    install.add_argument("--manifest", required=True, type=Path)
    install.set_defaults(func=command_install)
    download = subparsers.add_parser("download")
    download.add_argument("--output", required=True, type=Path)
    download.add_argument("--manifest", required=True, type=Path)
    download.set_defaults(func=command_download)
    arguments = parser.parse_args()
    try:
        arguments.func(arguments)
    except (OSError, ValueError, urllib.error.URLError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(2)


if __name__ == "__main__":
    main()
