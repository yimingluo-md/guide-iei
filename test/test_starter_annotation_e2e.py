#!/usr/bin/env python3
"""Opt-in Docker/VEP test of prepared starters using public, synthetic variants.

No clinical databases, downloads, or patient library are touched. The baked
IndexedScores plugin must match the current source before using the existing
engine. This does not validate a future app's startup or a clean machine.
"""
import argparse
from collections import Counter
import gzip
import json
from pathlib import Path
import subprocess
import sys

if not __debug__:
    raise RuntimeError("Release validation requires assertions; run Python without -O or PYTHONOPTIMIZE")
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def verify_baked_plugins(image, run=subprocess.check_output):
    from pipeline.starter_annotations import file_identity
    identities = {}
    for name in ("IndexedScores.pm", "SpliceAIStarter.pm", "SpliceAI-MANE1.5-gene-map.json"):
        baked = run(["docker", "run", "--rm", "--network", "none", "--entrypoint",
                     "sha256sum", image, "/plugins/" + name], text=True).split()[0]
        identity = file_identity(ROOT / "docker" / name)
        if baked != identity["sha256"]:
            raise ValueError(f"Rebuild an isolated test image; baked {name} is stale")
        identities[name] = identity
    return identities


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--image", default="vep-annotate:latest")
    args = parser.parse_args()
    import pysam
    import yaml
    from pipeline.build_vep_command import build_vep_command
    from pipeline.starter_annotations import file_identity
    from local_service.cohort_store import annotation_from
    scratch = args.output.resolve()
    scratch.mkdir(parents=True, exist_ok=False)
    build = args.build.resolve()
    alleles = set()
    regression_cases = json.loads((ROOT / "test/contracts/alphamissense_version_collisions.json").read_text())["cases"]
    regression_seen = set()
    for case in regression_cases:
        alleles.add((case["chrom"], case["pos"], case["ref"], case["alt"]))
    tables = {kind: pysam.TabixFile(str(build / kind / (kind + suffix))) for kind, suffix in
              (("alphamissense", ".tsv.gz"), ("cadd", ".tsv.gz"), ("spliceai", ".vcf.gz"))}
    for kind in ("alphamissense", "spliceai"):
        for chrom, start in (("1", 12740000), ("4", 6070054), ("6", 89500000), ("7", 150405904), ("17", 42000000), ("X", 10000000)):
            for i, text in enumerate(tables[kind].fetch(chrom, start)):
                row = text.split("\t")
                ref, alt = row[3:5] if kind == "spliceai" else row[2:4]
                alleles.add((chrom, int(row[1]), ref, alt))
                if i >= 5:
                    break
    # Outside starter scope: noncoding SNV and an indel at a scored position.
    alleles.add(("1", 10000, "N", "A"))
    example = sorted(alleles)[-1]
    alleles.add((example[0], example[1], example[2], example[2] + "A"))
    input_path = scratch / "public-starter-fixture.vcf"
    output_path = scratch / "starter.vep.vcf.gz"
    input_path.write_text("##fileformat=VCFv4.2\n##reference=GRCh38\n"
                          "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n" +
                          "".join(f"{c}\t{p}\t.\t{r}\t{a}\t.\tPASS\t.\n" for c,p,r,a in sorted(alleles)))
    config = yaml.safe_load((ROOT / "config/annotation.config.yaml").read_text())
    config["container"]["image"] = args.image
    for block in config["plugins"].values():
        if isinstance(block, dict):
            block["enabled"] = False
    for block in config.get("custom_tracks", {}).values():
        block["enabled"] = False
    for kind, name in (("alphamissense", "AlphaMissenseStarter"), ("cadd", "CADDStarter")):
        config["plugins"][name].update(enabled=True, required=True,
            file=str(build / kind / (kind + ".tsv.gz")), manifest=str(build / kind / "indexed.manifest.json"))
    config["plugins"]["SpliceAI"].update(enabled=True, required=True,
        snv=str(build / "spliceai/spliceai.vcf.gz"), coverage_scope="essential_splice_sites",
        source_manifest=str(build / "spliceai/preparation.json"))
    config["plugins"]["SpliceAI"].pop("format", None)
    config["run"]["fork"] = 2
    config["core"]["pick"] = False
    config["output"]["vep_stats"] = False
    plan = build_vep_command(config, str(input_path), str(output_path), base_dir=str(ROOT), verify_integrity=True)
    assert not plan.errors, plan.errors
    baked_plugins = verify_baked_plugins(config["container"]["image"])
    command = ["docker", "run", "--rm", "--network", "none"]
    for mount in plan.mounts:
        command += ["-v", f"{mount.host}:{mount.container}:{mount.mode}"]
    command += [config["container"]["image"], *plan.argv, "--dir_plugins", "/plugins"]
    (scratch / "command.json").write_text(json.dumps(command, indent=2) + "\n")
    with (scratch / "vep.log").open("w") as log:
        subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True)
    counters = Counter()
    gene_map = json.loads((ROOT / "docker/SpliceAI-MANE1.5-gene-map.json").read_text())["genes"]
    with gzip.open(output_path, "rt") as stream:
        for line in stream:
            if line.startswith("##INFO=<ID=CSQ,"):
                fields = line.split("Format: ", 1)[1].split('"', 1)[0].split("|")
            if line.startswith("#"):
                continue
            row = line.rstrip().split("\t")
            chrom, pos, ref, alt = row[0], int(row[1]), row[3], row[4]
            original = {}
            for kind, table in tables.items():
                original[kind] = [r.split("\t") for r in table.fetch(chrom, pos-1, pos)
                                  if (r.split("\t")[3:5] if kind == "spliceai" else r.split("\t")[2:4]) == [ref, alt]]
            annotations = dict(v.split("=", 1) for v in row[7].split(";") if "=" in v).get("CSQ", "")
            for text in annotations.split(","):
                csq = dict(zip(fields, (unquote(v) for v in text.split("|"))))
                stored = annotation_from(csq)
                for case in regression_cases:
                    if ((chrom, pos, ref, alt) == (case["chrom"], case["pos"], case["ref"], case["alt"])
                            and csq.get("Feature", "").split(".")[0] == case["transcript"]):
                        regression_seen.add(case["gene"])
                        if case["score"] is None:
                            assert not csq.get("StarterAM_score"), (case, csq)
                        else:
                            assert csq.get("StarterAM_match_status") == "exact", (case, csq)
                            assert float(csq["StarterAM_score"]) == case["score"], (case, csq)
                if csq.get("StarterCADD_phred"):
                    assert len(original["cadd"]) == 1
                    assert float(csq["StarterCADD_phred"]) == float(original["cadd"][0][4])
                    assert csq["StarterCADD_match_status"] == "exact"
                    assert stored["cadd"] == float(csq["StarterCADD_phred"])
                    counters["cadd_exact"] += 1
                if csq.get("StarterAM_score"):
                    matches = [r for r in original["alphamissense"] if r[4].split(".")[0] == csq["Feature"].split(".")[0]]
                    aa = csq["Amino_acids"].split("/")
                    protein = aa[0] + csq["Protein_position"] + aa[-1]
                    matches = [r for r in matches if r[5] == protein]
                    assert len(matches) == 1, (row[:5], csq, matches)
                    assert float(csq["StarterAM_score"]) == float(matches[0][6])
                    assert csq["StarterAM_prediction"] == matches[0][7]
                    assert csq["StarterAM_match_status"] == "exact"
                    assert stored["alpha_missense"] == float(csq["StarterAM_score"])
                    counters["alphamissense_exact"] += 1
                if csq.get("SpliceAI_pred_DS_AG"):
                    entries = [e.split("|") for r in original["spliceai"] for e in r[7].removeprefix("SpliceAI=").split(",")]
                    symbol = csq.get("SpliceAI_pred_SYMBOL", csq.get("SYMBOL"))
                    assert symbol in gene_map[csq["Gene"]]["symbols"], (row[:5], csq)
                    matching = [e for e in entries if e[1] == symbol]
                    assert matching, (row[:5], csq)
                    scores = [csq["SpliceAI_pred_" + label] for label in ("DS_AG", "DS_AL", "DS_DG", "DS_DL", "DP_AG", "DP_AL", "DP_DG", "DP_DL")]
                    assert any([float(v) for v in scores] == [float(v) for v in e[2:]] for e in matching)
                    counters["spliceai_exact"] += 1
                    if not csq.get("SYMBOL"):
                        counters["spliceai_missing_symbol_rescued"] += 1
                if len(ref) != 1 or len(alt) != 1:
                    assert not csq.get("StarterAM_score") and not csq.get("StarterCADD_phred") and not csq.get("SpliceAI_pred_DS_AG")
            counters["records"] += 1
    assert counters["records"] == len(alleles)
    assert all(counters[k] > 0 for k in ("cadd_exact", "alphamissense_exact", "spliceai_exact")), counters
    assert counters["spliceai_missing_symbol_rescued"] > 0, counters
    assert regression_seen == {c["gene"] for c in regression_cases}, regression_seen
    for table in tables.values():
        table.close()
    image_id = subprocess.check_output(["docker", "image", "inspect", config["container"]["image"],
                                        "--format", "{{.Id}}"], text=True).strip()
    report = {"status": "PASS", "counts": dict(counters), "engine_image_id": image_id,
              "alphamissense_version_collision_regressions": sorted(regression_seen),
              "indexed_plugin": file_identity(ROOT / "docker/IndexedScores.pm"),
              "baked_plugins": baked_plugins,
              "input": file_identity(input_path), "output": file_identity(output_path),
              "preparations": {k: file_identity(build / k / "preparation.json") for k in tables},
              "scope": "Existing VEP113 engine/cache; baked plugin matches current source; no dbNSFP or clinical sources; not a clean-machine test"}
    (scratch / "validation.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
