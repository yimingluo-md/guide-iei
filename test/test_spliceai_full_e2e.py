#!/usr/bin/env python3
"""Opt-in real-data test: unchanged 24 shards, combined fixture, fork 1/2.

Reuses downloaded public source files by hard link in a separate test directory.
Runs the actual installer (hash verification, original license/notice download).
Never modifies a source score file, patient library or installed engine tag.
"""
import argparse
import gzip
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    import pysam
    import yaml
    from pipeline import spliceai_dataset as dataset
    from pipeline.build_vep_command import build_vep_command
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--reuse", action="store_true", help="rerun this generated test directory")
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=args.reuse)
    release = json.loads(dataset.PIN.read_text())
    stage = out / "full.preparing/data"
    manifest = out / "full/manifest.json"
    if not manifest.exists():
        stage.mkdir(parents=True)
        for row in release["files"]:
            for kind in ("vcf", "index"):
                os.link(args.sources / Path(row[kind]).name, stage / Path(row[kind]).name)
    dataset.install(manifest)
    # Query source rows from all chromosomes and the known missing-symbol sites.
    source_rows = {}
    for entry in release["files"]:
        with pysam.TabixFile(str(manifest.parent / entry["vcf"])) as table:
            assert len(table.contigs) == 1
            for i, line in enumerate(table.fetch(table.contigs[0])):
                fields = line.split("\t")
                source_rows[tuple(fields[:2] + fields[3:5])] = line
                if i == 5:
                    break
            if entry["contig"] in {"4", "7"}:
                pos = 6070054 if entry["contig"] == "4" else 150405904
                for line in table.fetch(table.contigs[0], pos, pos + 2):
                    fields = line.split("\t")
                    source_rows[tuple(fields[:2] + fields[3:5])] = line
    ordered = sorted(source_rows, key=lambda k: (k[0], int(k[1]), k[2:]))
    raw = out / "public-raw.vcf"
    raw.write_text("##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n" +
                   "".join(f"{c.removeprefix('chr')}\t{p}\t.\t{r}\t{a}\t.\tPASS\t.\n" for c,p,r,a in ordered))
    combined = out / "combined.vcf"
    combined.write_text("##fileformat=VCFv4.2\n##INFO=<ID=SpliceAI,Number=.,Type=String,Description=\"SpliceAI\">\n" +
                        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n" +
                        "".join(source_rows[k] + "\n" for k in ordered))
    pysam.tabix_compress(str(combined), str(combined) + ".gz", force=True)
    pysam.tabix_index(str(combined) + ".gz", preset="vcf", force=True)
    mapping = json.loads((ROOT / "docker/SpliceAI-MANE1.5-gene-map.json").read_text())
    provenance = out / "preparation.json"
    provenance.write_text(json.dumps({"resource": "starter_spliceai", "assembly": "GRCh38",
        "sources": [{"role": k, "sha256": v} for k,v in mapping["source_sha256"].items()]}))
    results, timings = {}, {}
    for name, fork, sharded in (("shards-fork1", 1, True), ("shards-fork2", 2, True), ("combined-fork2", 2, False)):
        config = yaml.safe_load((ROOT / "config/annotation.config.yaml").read_text())
        for block in config["plugins"].values():
            block["enabled"] = False
        for block in config.get("custom_tracks", {}).values():
            block["enabled"] = False
        config["plugins"]["SpliceAI"] = ({"format": dataset.FORMAT, "snv": str(manifest)} if sharded else
            {"snv": str(combined) + ".gz", "coverage_scope": "essential_splice_sites", "source_manifest": str(provenance)})
        config["plugins"]["SpliceAI"].update(enabled=True, required=True)
        config["run"]["fork"] = fork
        config["core"]["pick"] = False
        config["output"]["vep_stats"] = False
        result = out / (name + ".vcf.gz")
        plan = build_vep_command(config, str(raw), str(result), base_dir=str(ROOT))
        assert not plan.errors, plan.errors
        command = ["docker", "run", "--rm", "--network", "none"]
        for mount in plan.mounts:
            command += ["-v", f"{mount.host}:{mount.container}:{mount.mode}"]
        command += [args.image, *plan.argv, "--dir_plugins", "/plugins"]
        start = time.monotonic()
        with (out / (name + ".log")).open("w") as log:
            subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True)
        timings[name] = round(time.monotonic() - start, 2)
        if sharded:
            config_path = out / (name + ".yaml")
            config_path.write_text(yaml.safe_dump(config))
            subprocess.run([sys.executable, str(ROOT / "pipeline/write_run_manifest.py"),
                "--config", str(config_path), "--base-dir", str(ROOT), "--input", str(raw),
                "--output", str(result), "--plan-json", json.dumps({"argv": plan.argv}),
                "--runtime", "docker", "--image", args.image], check=True)
            run_manifest = json.loads(Path(str(result) + ".run_manifest.json").read_text())
            assert run_manifest["spliceai_release"]["chromosome_files"] == release["files"]
        rows = {}
        with gzip.open(result, "rt") as handle:
            for line in handle:
                if line.startswith("##INFO=<ID=CSQ,"):
                    fields = line.split("Format: ",1)[1].split('"',1)[0].split("|")
                if line.startswith("#"):
                    continue
                data = line.rstrip().split("\t")
                csq = next(v[4:] for v in data[7].split(";") if v.startswith("CSQ="))
                for text in csq.split(","):
                    record = dict(zip(fields, text.split("|")))
                    key = tuple(data[:2] + data[3:5] + [record.get("Gene", ""), record.get("Feature", "")])
                    rows[key] = {k:v for k,v in record.items() if k.startswith("SpliceAI_pred_")}
        results[name] = rows
    assert results["shards-fork1"] == results["shards-fork2"] == results["combined-fork2"]
    scored = {k:v for k,v in results["shards-fork1"].items() if v.get("SpliceAI_pred_SYMBOL")}
    assert scored and any(k[1] == "6070055" and k[-2] == "ENSG00000284684" for k in scored)
    assert any(k[0] == "X" for k in scored) and any(k[0] == "Y" for k in scored)
    report = {"status": "PASS", "source_records": len(ordered), "source_chromosomes": 24,
              "scored_transcripts": len(scored), "scored_chromosomes": sorted({k[0] for k in scored}),
              "identical_sharded_combined_and_fork_results": True, "seconds": timings,
              "note": "Small synthetic fixture timings, not a whole-genome throughput benchmark"}
    (out / "validation.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report), flush=True)


if __name__ == "__main__":
    main()
