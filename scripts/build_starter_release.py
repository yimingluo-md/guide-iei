#!/usr/bin/env python3
"""Developer-only full subset build. Never installs into a user's active data.

--download explicitly permits ~120 GB of public source transfers. Outputs
remain development artifacts; this does not publish a mirror or enable a release.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pipeline.starter_annotations import prepare, file_identity, PREPARER_SHA256

SPLICE_REVISION = "90445b422a04dafd620c206093020ddbdc7817ba"
SPLICE_BASE = ("https://huggingface.co/datasets/luoyiming1991/"
               "spliceai-mane-v1.5-d500-m1-snv/resolve/" + SPLICE_REVISION)
CADD_BASE = "https://kircherlab.bihealth.org/download/CADD/v1.7/GRCh38"


def fetch(url, target, *, download, digest=None, size=None, md5=None):
    if target.is_file():
        if digest and (size is None or target.stat().st_size == size) and file_identity(target)["sha256"] == digest:
            return
        if md5 and not digest:
            hasher = hashlib.md5()
            with target.open("rb") as handle:
                for block in iter(lambda: handle.read(8 * 1024 * 1024), b""): hasher.update(block)
            if hasher.hexdigest() == md5: return
        # Never silently overwrite a mismatched source.
        raise ValueError(f"Existing source failed verification: {target}")
    if not download:
        raise ValueError(f"Missing source {target}; use --download to authorize source transfers")
    command = [sys.executable, str(ROOT / "scripts/parallel_fetch.py"), url, str(target), "--connections", "4"]
    if digest: command += ["--sha256", digest]
    if md5: command += ["--md5", md5]
    subprocess.run(command, check=True)
    if digest and (file_identity(target)["sha256"] != digest or (size is not None and target.stat().st_size != size)):
        raise ValueError(f"Downloaded source failed verification: {target}")


def build(kind, source, output, download):
    destination = output / kind
    if destination.exists():
        manifest = json.loads((destination / "preparation.json").read_text())
        if (manifest.get("resource") != "starter_" + kind
                or manifest.get("preparer_sha256") != PREPARER_SHA256
                or "LICENSE-NOTICES.json" not in {f["name"] for f in manifest.get("files", [])}
                or (kind == "spliceai" and not manifest.get("all_source_chromosomes_supplied"))):
            raise ValueError(f"Existing build is incomplete or from another preparer: {destination}; choose a new build folder")
        for expected in manifest["files"]:
            if file_identity(destination / expected["name"]) != expected:
                raise ValueError(f"Existing build failed verification: {destination}; choose a new build folder")
        print(f"=== {kind}: reusing verified completed build ===", flush=True)
        return manifest
    print(f"=== {kind}: preparing full starter subset ===", flush=True)
    common = dict(output=destination)
    if kind == "alphamissense":
        return prepare(kind, [source / "AlphaMissense_hg38.tsv.gz", source / "AlphaMissense_isoforms_hg38.tsv.gz"],
                       release="AlphaMissense-2023-MANE1.5", mane_summary=source / "MANE.GRCh38.v1.5.summary.txt.gz",
                       ambiguous_source_policy="withhold", **common)
    if kind == "cadd":
        pin = json.loads((ROOT / "config/cadd-starter-source.json").read_text())
        if pin["source"] != CADD_BASE:
            raise ValueError("CADD source URL differs from the reviewed pin")
        for name in ("whole_genome_SNVs.tsv.gz", "whole_genome_SNVs.tsv.gz.tbi"):
            asset = pin["files"][name]
            fetch(f"{CADD_BASE}/{name}", source / name, download=download,
                  digest=asset["sha256"], size=asset["size_bytes"])
        return prepare(kind, [source / "whole_genome_SNVs.tsv.gz"], release="CADD1.7-GRCh38-coding-SNV",
                       gtf=ROOT / "references/regions/Homo_sapiens.GRCh38.113.gtf.gz", **common)
    manifest_path = source / "spliceai-release-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    sources = []
    for entry in manifest["files"]:
        for role in ("vcf", "index"):
            path = source / Path(entry[role]).name
            fetch(f"{SPLICE_BASE}/{entry[role]}", path, download=download,
                  digest=entry[role + "_sha256"], size=entry[role + "_bytes"])
            if role == "vcf": sources.append(path)
    return prepare(kind, sources, release="SpliceAI-MANE1.5-D500-M1-essential-SNV",
                   mane_summary=source / "MANE.GRCh38.v1.5.summary.txt.gz",
                   gtf=source / "MANE.GRCh38.v1.5.ensembl_genomic.gtf.gz",
                   source_manifest=manifest_path, **common)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--kind", choices=["alphamissense", "cadd", "spliceai", "all"], default="all")
    parser.add_argument("--source", type=Path, default=ROOT / "references/starter-sources")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.source.mkdir(parents=True, exist_ok=True)
    args.output.mkdir(parents=True, exist_ok=True)
    kinds = ["alphamissense", "cadd", "spliceai"] if args.kind == "all" else [args.kind]
    # Bounded parallelism; one compute preparation can overlap one download.
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = {kind: pool.submit(build, kind, args.source, args.output, args.download) for kind in kinds}
        for kind, future in results.items():
            result = future.result()
            print(f"=== {kind}: complete, {result['statistics']['output_rows']:,} records ===", flush=True)


if __name__ == "__main__":
    main()
