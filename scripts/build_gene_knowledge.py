#!/usr/bin/env python3
"""Build the redistributable gene-knowledge database and its source manifest."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import subprocess
import tempfile
from contextlib import closing
from email.utils import parsedate_to_datetime
from pathlib import Path

from local_service.storage_locations import StorageLocationRegistry
from local_service.gencc import CachedSnapshotError, EXPORT_URL
from local_service.gene_knowledge import build_public_database, sha256

BUNDLED = Path(__file__).resolve().parents[1] / "webui/public/bundled-data/gene_knowledge_public.sqlite3"


def http_etag(value):
    if not isinstance(value, str):
        return ""
    if re.fullmatch(r"[0-9a-fA-F]{32}", value):
        return f'"{value}"'  # Earlier snapshots stored an unquoted digest.
    return value if re.fullmatch(r'(?:W/)?"[!#-~]*"', value) else ""


def gencc_release(source, headers, fallback_date):
    # curl may include several header blocks when following redirects.
    response = headers.read_text().strip().split("\n\n")[-1] if headers else ""
    fields = {}
    for line in response.splitlines():
        name, separator, value = line.partition(":")
        if separator:
            fields[name.lower()] = value.strip()
    modified = fields.get("last-modified")
    etag = http_etag(fields.get("etag", ""))
    digest = re.fullmatch(r'"([0-9a-fA-F]{32})"', etag)
    if digest and hashlib.md5(source.read_bytes()).hexdigest() != digest[1].lower():
        raise ValueError("GenCC download does not match its published checksum")
    return dict(release=parsedate_to_datetime(modified).date().isoformat() if modified else fallback_date,
                source_url=EXPORT_URL, etag=etag, source_sha256=sha256(source))


def _cached_gencc_release(previous):
    if previous is None or not previous.is_file():
        return None
    try:
        with closing(sqlite3.connect(previous.resolve().as_uri() + "?mode=ro", uri=True)) as db:
            row = db.execute(
                "SELECT r.release,r.source_sha256,m.value FROM resources r,metadata m "
                "WHERE r.id='gencc' AND m.key='gencc_etag' AND r.source_url=?", (EXPORT_URL,)
            ).fetchone()
    except sqlite3.DatabaseError:
        return None  # A broken or older snapshot can still be replaced by a fresh export.
    if row is None:
        return None
    release, digest, stored_etag = row
    etag = http_etag(stored_etag)
    if not etag or not isinstance(release, str):
        return None
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
        return None
    return dict(release=release, source_sha256=digest, etag=etag, source_url=EXPORT_URL)


def fetch_gencc(directory, previous, fallback_date):
    """Reuse the validated submissions behind a 304 even when other gene sources change."""
    cached = _cached_gencc_release(previous)
    conditional = ["-H", f"If-None-Match: {cached['etag']}"] if cached else []
    source, headers = directory / "gencc.csv", directory / "headers"
    result = subprocess.run([
        "curl", "-fsSL", "--retry", "2", "--connect-timeout", "30", "--max-time", "180",
        *conditional, "-D", str(headers), "-o", str(source), "-w", "%{http_code}", EXPORT_URL,
    ], text=True, stdout=subprocess.PIPE, check=True)
    if result.stdout == "304" and cached:
        return previous, cached, True
    if result.stdout != "200":
        raise ValueError(f"Unexpected GenCC download response {result.stdout}")
    return source, gencc_release(source, headers, fallback_date), False


def managed_public_database():
    registry = StorageLocationRegistry(Path(__file__).resolve().parents[1], Path.home() / ".iei-variant-review", persist=False)
    root = registry.root("annotation")
    if not registry.is_default("annotation") and not root.is_dir():
        raise ValueError(f"Configured annotation storage is unavailable: {root}")
    registry.validate_marker("annotation", root, required=bool(registry.storage_id("annotation")))
    return root / "gene-knowledge" / "gene_knowledge_public.sqlite3"


def main():
    parser = argparse.ArgumentParser()
    for key in ("hgnc", "iuis", "clingen-validity", "clingen-dosage"):
        parser.add_argument("--" + key, required=True, type=Path)
    parser.add_argument("--output", type=Path, help="Default: configured annotation storage")
    parser.add_argument("--manifest", type=Path, help="Default: manifest.json alongside the output database")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--gencc", type=Path, help="Official new-format CSV")
    source.add_argument("--fetch-gencc", action="store_true")
    parser.add_argument("--gencc-headers", type=Path, help="HTTP headers for the supplied CSV")
    parser.add_argument("--release-date", required=True)
    args = parser.parse_args()
    args.output = args.output or managed_public_database()
    args.manifest = args.manifest or args.output.with_name("manifest.json")
    releases = {
        "hgnc": {"release": args.release_date, "source_url": "https://storage.googleapis.com/public-download-files/hgnc/tsv/tsv/hgnc_complete_set.txt"},
        "iuis": {"release": "October 2024", "source_url": "https://iuis.org/committees/iei/"},
        "clingen_validity": {"release": args.release_date, "source_url": "https://search.clinicalgenome.org/kb/gene-validity/download"},
        "clingen_dosage": {"release": args.release_date, "source_url": "https://ftp.clinicalgenome.org/ClinGen_gene_curation_list_GRCh38.tsv"},
    }
    with tempfile.TemporaryDirectory() as stage:
        if args.fetch_gencc:
            prior = args.output if args.output.is_file() else BUNDLED
            path, releases["gencc"], reuse = fetch_gencc(Path(stage), prior, args.release_date)
        else:
            path, reuse = args.gencc, False
            releases["gencc"] = gencc_release(path, args.gencc_headers, args.release_date)
        while True:
            try:
                result = build_public_database(
                    hgnc=args.hgnc, iuis=args.iuis, clingen_validity=args.clingen_validity,
                    clingen_dosage=args.clingen_dosage, gencc_source=path, reuse_gencc=reuse,
                    destination=args.output, releases=releases,
                )
                break
            except CachedSnapshotError:
                if not reuse:
                    raise
                # The validator matched, but the local submissions could not be reused.
                path, releases["gencc"], reuse = fetch_gencc(Path(stage), None, args.release_date)
    with closing(sqlite3.connect(args.output)) as db:
        db.row_factory = sqlite3.Row
        resources = {}
        for row in db.execute("SELECT id,release,source_url,source_sha256,record_count FROM resources ORDER BY id"):
            entry = dict(row)
            resources[entry.pop("id")] = entry
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=args.manifest.parent, delete=False) as handle:
        json.dump(dict(result, resources=resources), handle, indent=2, sort_keys=True)
        handle.write("\n")
    temporary_manifest = Path(handle.name)
    temporary_manifest.chmod(0o644)
    temporary_manifest.replace(args.manifest)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
