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
from predictor_registry import RegistryError, load_registry
from research_use_notice import RESEARCH_USE_NOTICE


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


def config_value(cfg: dict, dotted_path: str):
    value = cfg
    for part in dotted_path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def clinical_protein_catalog_paths(cfg: dict) -> list[str]:
    """Return the derived clinical-source protein-match catalogs.

    Catalog content is run-defining even when its source database is also
    recorded: transcript and protein coordinates depend on the VEP cache used
    to derive it, so the manifest hashes each small catalog itself.
    """
    paths: list[str] = []
    clinvar = cfg.get("clinvar", {}) or {}
    paths.append(
        clinvar.get("protein_match_catalog")
        or (
            str(Path(clinvar["dest_dir"]) / "clinvar_aa_reference.tsv")
            if clinvar.get("dest_dir")
            else None
        )
    )
    clingen = cfg.get("clingen_erepo", {}) or {}
    paths.append(
        clingen.get("protein_match_catalog")
        or (
            str(Path(clingen["dest_dir"]) / "clingen_aa_reference.tsv")
            if clingen.get("dest_dir")
            else None
        )
    )
    genia = cfg.get("genia", {}) or {}
    paths.append(
        genia.get("protein_match_catalog")
        or (
            str(Path(genia["database"]).parent / "genia_aa_reference.tsv")
            if genia.get("database")
            else None
        )
    )
    return [path for path in paths if path]


def reference_paths(cfg: dict, registry=None) -> list[str]:
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
        "FuncVEP": ("file", "manifest"),
    }.items():
        block = plugins.get(name, {}) or {}
        paths.extend(block.get(key) for key in keys)
    paths.extend(track.get("file") for track in (cfg.get("custom_tracks", {}) or {}).values())
    clingen = cfg.get("clingen_erepo", {}) or {}
    paths.extend(clingen.get(key) for key in ("database", "vcf", "manifest"))
    # These compact catalogs are derived from the installed clinical source
    # plus the release-matched VEP cache.  They directly determine candidate
    # same-change/same-residue evidence and therefore belong in run
    # provenance just as much as the exact-allele databases do.
    paths.extend(clinical_protein_catalog_paths(cfg))
    # Run-defining inputs previously absent from the manifest: the liftover
    # chain and source reference, the ClinVar amino-acid-match catalog, and
    # the GTF the frameshift 50-bp recalculation reads exon structure from.
    liftover = ((cfg.get("liftover", {}) or {}).get("grch37_to_grch38", {}) or {})
    paths.extend(liftover.get(key) for key in ("chain", "source_fasta"))
    post = cfg.get("post_processing", {}) or {}
    paths.append(((post.get("loftee_ptc_50bp", {}) or {}).get("gtf")))
    region = cfg.get("region", {}) or {}
    # The BED that decided which variants reached VEP is run-defining.
    paths.extend([region.get("custom_bed"), region.get("bed")])
    # Registry-declared assets make future predictors reproducible without
    # another hard-coded manifest edit. The legacy list above remains during
    # migration so older configurations keep identical behavior.
    if registry is not None:
        for resource in registry.resources:
            paths.extend(config_value(cfg, asset.config_path) for asset in resource.assets)
    return sorted({path for path in paths if path and path != "auto"})


def registry_metadata(path: Path, cfg: dict) -> tuple[dict, object | None]:
    result = file_metadata(str(path))
    if not result["exists"]:
        return result, None
    try:
        registry = load_registry(path)
    except RegistryError as exc:
        raise ValueError(f"invalid predictor registry {path}: {exc}") from exc
    result.update({
        "sha256": sha256(str(path)),
        "schema_version": registry.schema_version,
        "resource_count": len(registry.resources),
        "annotator_count": len(registry.annotators),
        "predictor_count": len(registry.predictors),
        "configured_resources": [
            {
                "id": resource.id,
                "enabled": (
                    bool(value.get("enabled", resource.default_enabled))
                    if isinstance((value := config_value(cfg, resource.config_path)), dict)
                    else resource.default_enabled
                ),
            }
            for resource in registry.resources
        ],
    })
    return result, registry


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
    registry_path = Path(args.base_dir) / "config" / "predictor-registry.json"
    predictor_registry, registry = registry_metadata(registry_path, cfg)
    input_metadata = file_metadata(args.input)
    if input_metadata["exists"]:
        # Bind the annotated result to the exact source VCF bytes. The Sample
        # Library also records an annotation-insensitive callset fingerprint,
        # while this full digest preserves strict run provenance.
        input_metadata["sha256"] = sha256(args.input)
    raw_reference_paths = sorted({
        *reference_paths(cfg, registry),
        *([args.region_bed] if args.region_bed else []),
    })
    catalog_paths = {
        path if os.path.isabs(path) else os.path.join(args.base_dir, path)
        for path in clinical_protein_catalog_paths(cfg)
    }
    reference_metadata = []
    for path in raw_reference_paths:
        resolved_path = path if os.path.isabs(path) else os.path.join(args.base_dir, path)
        entry = file_metadata(resolved_path)
        if (
            resolved_path in catalog_paths
            and entry["exists"]
            and os.path.isfile(resolved_path)
        ):
            entry["sha256"] = sha256(resolved_path)
        reference_metadata.append(entry)

    manifest = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "research_use_notice": RESEARCH_USE_NOTICE,
        "pipeline_version": pipeline_version,
        "config": {"path": os.path.abspath(args.config), "sha256": sha256(args.config)},
        "input": input_metadata,
        "output": file_metadata(args.output),
        "container": {"runtime": args.runtime, "image": args.image, "identity": args.image_id},
        "clinvar_release": args.clinvar_release,
        "requested_assembly": args.requested_assembly or None,
        "resolved_assembly": args.resolved_assembly or None,
        "input_filter_policy": args.filter_policy or None,
        "region_bed": args.region_bed or None,
        "vep_argv": plan.get("argv", []),
        "predictor_registry": predictor_registry,
        "references": reference_metadata,
    }
    with open(args.output + ".run_manifest.json", "w") as out:
        json.dump(manifest, out, indent=2, sort_keys=True)
        out.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
