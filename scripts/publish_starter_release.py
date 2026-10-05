#!/usr/bin/env python3
"""Stage audited public starter data and optionally publish a pinned HF release.

Never reads patient/state folders. Upload requires --upload and two passing
validation reports. App configuration is not edited by this developer tool.
"""
import argparse
import json
from pathlib import Path
import re
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pipeline.starter_package import load_plan, sha256


def identity(path):
    return {"name": path.name, "size_bytes": path.stat().st_size, "sha256": sha256(path)}


def reuse_unchanged_components(components, previous):
    """Keep installed component receipts valid when only another table changed.

    New audit timestamps in preparation.json alone do not require redownloading
    unchanged scores. Data, indexes, manifests AND license notices must agree.
    Reused components retain their original provenance and immutable URLs.
    """
    def assets(component):
        return {a["name"]: (a["size_bytes"], a["sha256"]) for a in component["files"]
                if a["name"] != "preparation.json"}
    old = {c["id"]: c for c in previous["components"]}
    return [old[c["id"]] if c["id"] in old and assets(c) == assets(old[c["id"]]) else c
            for c in components]


def stage(build, audit, vep, destination, version,
          repo="luoyiming1991/guide-iei-essential-annotations-grch38"):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,100}", version):
        raise ValueError("Invalid release version")
    report = json.loads(audit.read_text())
    vep_report = json.loads(vep.read_text())
    if vep_report.get("status") != "PASS" or report.get("installation", {}).get("status") != "PASS":
        raise ValueError("Passing real-payload installation and VEP reports are required")
    expected_plugins = {name: identity(ROOT / "docker" / name) for name in
                        ("IndexedScores.pm", "SpliceAIStarter.pm", "SpliceAI-MANE1.5-gene-map.json")}
    if vep_report.get("baked_plugins") != expected_plugins:
        raise ValueError("VEP validation must verify both baked starter plugins and the gene map")
    if report.get("alphamissense", {}).get("runtime_key_uniqueness") != "PASS":
        raise ValueError("Full AlphaMissense runtime-key uniqueness validation is required")
    if report.get("alphamissense", {}).get("version_resolution_audit", {}).get("status") != "PASS":
        raise ValueError("Full AlphaMissense version-resolution comparison is required")
    required_regressions = {case["gene"] for case in json.loads(
        (ROOT / "test/contracts/alphamissense_version_collisions.json").read_text())["cases"]}
    if set(vep_report.get("alphamissense_version_collision_regressions", [])) != required_regressions:
        raise ValueError("VEP validation must cover the AlphaMissense version-collision regressions")
    kinds = ("alphamissense", "cadd", "spliceai")
    metadata = {}
    for kind in kinds:
        source = build / kind
        result = report.get(kind, {})
        if result.get("status") != "PASS" or result.get("preparation") != identity(source / "preparation.json"):
            raise ValueError(f"Audit does not cover these {kind} artifacts")
        metadata[kind] = json.loads((source / "preparation.json").read_text())
        for asset in metadata[kind]["files"]:
            if identity(source / asset["name"]) != asset:
                raise ValueError(f"Changed artifact: {kind}/{asset['name']}")
    if vep_report.get("preparations") != {k: identity(build / k / "preparation.json") for k in kinds}:
        raise ValueError("VEP validation must identify exactly these prepared datasets")
    destination.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(audit, destination / "validation.json")
    shutil.copyfile(vep, destination / "vep-validation.json")
    components = []
    for kind in kinds:
        folder = destination / kind
        folder.mkdir()
        for asset in metadata[kind]["files"]:
            shutil.copyfile(build / kind / asset["name"], folder / asset["name"])
        promoted = dict(metadata[kind], release_status="validated",
                        original_preparation_sha256=report[kind]["preparation"]["sha256"],
                        validation={"audit": identity(audit)["sha256"], "vep": identity(vep)["sha256"]})
        (folder / "preparation.json").write_text(json.dumps(promoted, indent=2) + "\n")
        components.append({"id": kind, "version": version,
                           "files": [identity(folder / asset["name"]) for asset in metadata[kind]["files"]]
                                    + [identity(folder / "preparation.json")]})
    rows = "\n".join(f"| {k} | {report[k]['records']:,} | {report[k]['payload_bytes']/1e6:.1f} MB |" for k in kinds)
    readme = f"""---
license: other
license_name: component-specific-annotation-data-terms
license_link: https://huggingface.co/datasets/{repo}/blob/main/README.md#licenses
pretty_name: GUIDE-IEI compact essential annotations (GRCh38)
tags:
- genomics
- GRCh38
- variant-annotation
- alphamissense
- cadd
- spliceai
---
# GUIDE-IEI compact essential annotations

Release **{version}**. Prepared public score subsets for one-click [GUIDE-IEI](https://github.com/yimingluo-md/guide-iei)
setup, downloaded alongside (not inside) the VEP reference bundle. Application
installers do not embed these data. No patient data or model weights are included.

**AlphaMissense correction:** the initial `2026-10-04-v1` table had 518,100
duplicate runtime keys across 97 MANE transcripts, which caused the matcher to
withhold scores. This release resolves keys using the runtime's stable transcript
ID rule. Within the preferred source, an unambiguous exact MANE v1.5 transcript
version takes precedence over older versions, recovering 95,924 additional keys
across 30 transcripts compared with `2026-10-05-v2`. Unresolved conflicts remain
withheld; scores are never averaged or chosen by magnitude. Update GUIDE-IEI's
pinned essential package and rerun essential setup, then reannotate affected VCFs;
existing annotation outputs do not change automatically. CADD and SpliceAI scores
are unchanged by this correction. Historical immutable revisions remain available
for reproducibility, but the initial AlphaMissense table should not be used for
new annotations.

| Component | Rows | Data, index and notices |
|---|---:|---:|
{rows}

## Coverage and matching

- **AlphaMissense:** GRCh38 MANE Select v1.5 transcript selection from the
  official canonical and isoform tables. Exact allele + stable transcript ID +
  amino-acid change, with source and MANE versions retained. Canonical predictions
  take precedence for overlapping keys; ambiguous source keys are withheld.
  Deduplication uses the same version-stripped transcript key as runtime lookup.
  Within that source, the exact pinned MANE version wins over other versions;
  conflicts remaining in the winning tier are withheld, not chosen by score.
  Predictions are not recomputed. Not every MANE transcript has source scores.
- **CADD 1.7:** SNVs in the Ensembl 113 protein-coding CDS + stop-codon union.
  Original Phred score only. No indels, raw score or intronic padding. Allele-scoped.
- **SpliceAI:** MANE Select v1.5 first/last two intronic bases, SNVs only, D=500,
  masked M=1. Original gene names and all DS/DP scores are retained. Summary and
  GTF gene names are accepted only when anchored to the exact MANE transcript.

The source-pinned preparation manifests describe modifications, counts and hashes.
`validation.json` records full index counts, biological checks and isolated
installation/repair tests; `vep-validation.json` records real VEP113 score checks.
AlphaMissense validation includes a complete normalized-key uniqueness scan and
version-collision regressions for CIITA, TAPBP, CFH, DMD, ATRX, HNF1A, COL1A1,
MAPT, WT1 and EIF4G3. The last three recover the exact pinned MANE-version score.
Every output row is also compared with the original versioned table under this
selection rule; all recovered transcript versions are checked against the
Ensembl 113 GTF used by the annotation engine.
These are not a claim of complete clean-machine or clinical validation.

## Licenses

**These datasets are not MIT-licensed.** GUIDE-IEI's MIT software license does
not replace component data terms. Each component includes `LICENSE-NOTICES.json`
with attribution, source notices, modifications, links and license text.

- AlphaMissense: upstream project currently states CC BY 4.0 for predictions;
  historical notices from the downloaded tables are retained in the component.
  [Official project](https://github.com/google-deepmind/alphamissense).
- CADD: upstream noncommercial-use terms apply; commercial use requires separate
  arrangements. [Official terms/downloads](https://cadd.bihealth.org/download).
- SpliceAI source dataset: CC BY-NC 4.0 with retained upstream and third-party
  notices. [Pinned source](https://huggingface.co/datasets/luoyiming1991/spliceai-mane-v1.5-d500-m1-snv/tree/90445b422a04dafd620c206093020ddbdc7817ba).

No additional rights or endorsement by the upstream authors are implied.
Review the applicable component terms before use.

## Use

GUIDE-IEI downloads separately pinned components and verifies their SHA-256 hashes.
BGZF tables/VCF and tabix indexes are provided separately for resumable transfers.
ClinVar/ClinGen remain separate official-source downloads; VEP references remain
in their [existing reference mirror](https://huggingface.co/datasets/luoyiming1991/vep113-grch38-reference-bundle).
"""
    (destination / "README.md").write_text(readme)
    (destination / "components.json").write_text(json.dumps(components, indent=2) + "\n")
    return components


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--vep-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--repo", default="luoyiming1991/guide-iei-essential-annotations-grch38")
    parser.add_argument("--upload", action="store_true")
    parser.add_argument("--reuse-unchanged-from", type=Path,
                        help="Previous pinned app plan; keep identical components at their installed versions")
    args = parser.parse_args()
    previous = load_plan(args.reuse_unchanged_from) if args.reuse_unchanged_from else None
    components = stage(args.build, args.audit, args.vep_report, args.output, args.version, args.repo)
    if not args.upload:
        print(f"Staged only: {args.output}")
        return
    from huggingface_hub import HfApi
    api = HfApi()
    api.create_repo(args.repo, repo_type="dataset", private=False, exist_ok=True)
    # Upload only the explicitly staged public artifacts, never a broad workspace.
    result = api.upload_folder(repo_id=args.repo, repo_type="dataset", folder_path=str(args.output),
                               commit_message=f"Publish validated compact starter annotations {args.version}")
    revision = result.oid
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("Mirror did not return an immutable commit revision")
    for component in components:
        for asset in component["files"]:
            asset["url"] = f"https://huggingface.co/datasets/{args.repo}/resolve/{revision}/{component['id']}/{asset['name']}"
    if previous:
        components = reuse_unchanged_components(components, previous)
    plan = {"schema": "guide-iei.essential-annotations/v1", "release_status": "ready",
            "assembly": "GRCh38", "vep_release": 113, "components": components}
    target = args.output.parent / (args.output.name + ".pinned.json")
    target.write_text(json.dumps(plan, indent=2) + "\n")
    load_plan(target)
    print(f"Published {args.repo}@{revision}; candidate app manifest: {target}")


if __name__ == "__main__":
    main()
