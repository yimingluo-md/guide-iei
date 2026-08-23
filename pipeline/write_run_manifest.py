#!/usr/bin/env python3
"""Write a reproducibility manifest beside a completed annotation result."""
from __future__ import annotations

import argparse
from pathlib import Path
import hashlib
import json
import os
from datetime import datetime, timezone

from build_vep_command import load_config


def file_metadata(path: str) -> dict:
    data = {"path": path, "exists": os.path.exists(path)}
    if not data["exists"]:
        return data
    stat = os.stat(path)
    data.update({"size": stat.st_size, "mtime_ns": stat.st_mtime_ns})
    provenance_file = os.path.join(path, ".provenance.json") if os.path.isdir(path) else None
    if provenance_file and os.path.isfile(provenance_file):
        with open(provenance_file) as fh:
            data["provenance"] = json.load(fh)
    for suffix in (".sha256", ".sha256.local"):
        checksum_file = path + suffix
        if os.path.isfile(checksum_file):
            with open(checksum_file) as fh:
                data["sha256_record"] = fh.readline().strip()
            break
    return data


def sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def reference_paths(cfg: dict) -> list[str]:
    paths = []
    ref = cfg.get("reference", {}) or {}
    paths.append(ref.get("vep_cache_dir"))
    paths.append((ref.get("fasta", {}) or {}).get("path"))
    plugins = cfg.get("plugins", {}) or {}
    for name, keys in {
        "dbNSFP": ("path",),
        "LoF": ("human_ancestor_fa", "conservation_file", "gerp_bigwig"),
        "SpliceAI": ("snv", "indel"),
        "CADD_WGS": ("snv", "indels"),
        "PromoterAI": ("file", "transcript_map", "manifest"),
        "LoGoFunc": ("file", "manifest"),
    }.items():
        block = plugins.get(name, {}) or {}
        paths.extend(block.get(key) for key in keys)
    paths.extend(track.get("file") for track in (cfg.get("custom_tracks", {}) or {}).values())
    clingen = cfg.get("clingen_erepo", {}) or {}
    paths.extend(clingen.get(key) for key in ("database", "vcf", "manifest"))
    # Run-defining inputs previously absent from the manifest: the liftover
    # chain and source reference, the ClinVar amino-acid-match catalog, and
    # the GTF the frameshift 50-bp recalculation reads exon structure from.
    liftover = ((cfg.get("liftover", {}) or {}).get("grch37_to_grch38", {}) or {})
    paths.extend(liftover.get(key) for key in ("chain", "source_fasta"))
    post = cfg.get("post_processing", {}) or {}
    paths.append(((post.get("loftee_ptc_50bp", {}) or {}).get("gtf")))
    clinvar_dir = (cfg.get("clinvar", {}) or {}).get("dest_dir")
    if clinvar_dir:
        paths.append(str(Path(clinvar_dir) / "clinvar_aa_reference.tsv"))
    region = cfg.get("region", {}) or {}
    # The BED that decided which variants reached VEP is run-defining.
    paths.extend([region.get("custom_bed"), region.get("bed")])
    return sorted({path for path in paths if path and path != "auto"})


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--base-dir", required=True)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--plan-json", required=True)
    parser.add_argument("--runtime", required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--image-id", default="unknown")
    parser.add_argument("--clinvar-release", default="NA")
    parser.add_argument("--requested-assembly", default="")
    parser.add_argument("--resolved-assembly", default="")
    parser.add_argument("--filter-policy", default="")
    parser.add_argument("--region-bed", default="")
    args = parser.parse_args()

    cfg = load_config(args.config)
    plan = json.loads(args.plan_json)
    version_path = os.path.join(args.base_dir, "VERSION")
    if os.path.isfile(version_path):
        with open(version_path) as version_handle:
            pipeline_version = version_handle.read().strip()
    else:
        pipeline_version = "unknown"
    manifest = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "pipeline_version": pipeline_version,
        "config": {"path": os.path.abspath(args.config), "sha256": sha256(args.config)},
        "input": file_metadata(args.input),
        "output": file_metadata(args.output),
        "container": {"runtime": args.runtime, "image": args.image, "identity": args.image_id},
        "clinvar_release": args.clinvar_release,
        "requested_assembly": args.requested_assembly or None,
        "resolved_assembly": args.resolved_assembly or None,
        "input_filter_policy": args.filter_policy or None,
        "region_bed": args.region_bed or None,
        "vep_argv": plan.get("argv", []),
        "references": [
            file_metadata(
                path if os.path.isabs(path) else os.path.join(args.base_dir, path)
            )
            for path in sorted({*reference_paths(cfg),
                                *([args.region_bed] if args.region_bed else [])})
        ],
    }
    with open(args.output + ".run_manifest.json", "w") as out:
        json.dump(manifest, out, indent=2, sort_keys=True)
        out.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
