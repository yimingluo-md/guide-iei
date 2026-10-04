#!/usr/bin/env python3
"""Opt-in real-payload audit and isolated installation test (no public writes).

Uses local public sources read-only. Reports and an isolated installation are
retained in a new --output directory. This is not a clean-machine/VEP test.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import re
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pipeline.starter_annotations import (annotation_regions, file_identity,
                                         mane_select, PREPARER_SHA256)
from pipeline.indexed_scores import load_manifest, validate_manifest_files
from pipeline.starter_package import install, installed_paths, load_plan, component_path


def audit_component(kind, build, sources):
    import pysam
    directory = build / kind
    metadata = json.loads((directory / "preparation.json").read_text())
    assert metadata["resource"] == "starter_" + kind
    assert metadata["assembly"] == "GRCh38"
    # Components may have been built by different recorded preparer revisions.
    # Do not rewrite historical provenance merely to match today's auditor.
    assert re.fullmatch(r"[0-9a-f]{64}", metadata["preparer_sha256"])
    for expected in metadata["files"]:
        assert file_identity(directory / expected["name"]) == expected, expected["name"]
    notices = json.loads((directory / "LICENSE-NOTICES.json").read_text())
    assert notices["resource"]["id"] == "starter_" + kind
    assert notices["license"]["text"] and notices["resource"]["attribution"]
    suffix = ".vcf.gz" if kind == "spliceai" else ".tsv.gz"
    data = directory / (kind + suffix)
    if kind != "spliceai":
        validate_manifest_files(load_manifest(directory / "indexed.manifest.json"),
                                data, Path(str(data) + ".tbi"), strict=True)
    samples = []
    with pysam.TabixFile(str(data)) as index:
        counts = {chrom: sum(1 for _ in index.fetch(chrom)) for chrom in index.contigs}
        assert sum(counts.values()) == metadata["statistics"]["output_rows"]
        assert set(counts) == {str(i) for i in range(1, 23)} | {"X", "Y"}
        # Bounded, reproducible spatial sampling across all primary chromosomes.
        for chrom in index.contigs:
            for start in range(0, 250_000_000, 10_000_000):
                row = next(index.fetch(chrom, start, start + 10_000_000), None)
                if row:
                    samples.append(row.split("\t"))
    comparisons = 0
    if kind == "cadd":
        regions, _ = annotation_regions(ROOT / "references/regions/Homo_sapiens.GRCh38.113.gtf.gz")
        assert sum(counts.values()) == 3 * regions.bases()
        with pysam.TabixFile(str(sources / "whole_genome_SNVs.tsv.gz")) as source:
            for row in samples:
                chrom, pos, ref, alt, score = row
                assert regions.contains(chrom, int(pos))
                matches = [r.split("\t") for r in source.fetch(chrom, int(pos)-1, int(pos))
                           if r.split("\t")[:4] == row[:4]]
                assert len(matches) == 1 and matches[0][-1] == score
                comparisons += 1
    elif kind == "spliceai":
        assert metadata["all_source_chromosomes_supplied"]
        mane = mane_select(sources / "MANE.GRCh38.v1.5.summary.txt.gz")
        regions, sites = annotation_regions(sources / "MANE.GRCh38.v1.5.ensembl_genomic.gtf.gz", mane)
        assert sum(counts.values()) == 3 * regions.bases()
        # Check EVERY prepared SpliceAI site's gene context, not only samples.
        with pysam.TabixFile(str(data)) as index:
            for chrom in index.contigs:
                for text in index.fetch(chrom):
                    row = text.split("\t")
                    symbols = sites[(chrom, int(row[1]))]
                    assert len(row) == 8 and len(row[3]) == len(row[4]) == 1
                    for entry in row[7].removeprefix("SpliceAI=").split(","):
                        values = entry.split("|")
                        assert len(values) == 10 and values[0] == row[4] and values[1] in symbols
                        assert all(0 <= float(v) <= 1 for v in values[2:6])
                        assert all(abs(int(v)) <= 500 for v in values[6:])
        for chrom in counts:
            with pysam.TabixFile(str(sources / f"spliceai-mane-v1.5-d500-m1.snv.chr{chrom}.vcf.gz")) as source:
                source_chrom = chrom if chrom in source.contigs else "chr" + chrom
                for row in (r for r in samples if r[0] == chrom):
                    pos = int(row[1])
                    matches = [r.split("\t") for r in source.fetch(source_chrom, pos-1, pos)
                               if r.split("\t")[1:2] + r.split("\t")[3:5] == row[1:2] + row[3:5]]
                    assert len(matches) == 1
                    original = dict(v.split("=", 1) for v in matches[0][7].split(";") if "=" in v)["SpliceAI"]
                    assert set(row[7].removeprefix("SpliceAI=").split(",")) <= set(original.split(","))
                    comparisons += 1
    else:
        mane = mane_select(sources / "MANE.GRCh38.v1.5.summary.txt.gz")
        for row in samples:
            selected = mane[row[4].split(".")[0]]
            assert row[0] == selected["chrom"] and row[8] == selected["transcript"]
            assert len(row[2]) == len(row[3]) == 1 and 0 <= float(row[6]) <= 1
        with pysam.TabixFile(str(data)) as index:
            precedent = [r.split("\t") for r in index.fetch("1", 12746433, 12746434)
                         if r.split("\t")[2:6] == ["T", "A", "ENST00000614859.5", "F2I"]]
            assert len(precedent) == 1 and precedent[0][6] == "0.1201" and precedent[0][-1] == "canonical"
            for withheld in metadata.get("withheld_examples", []):
                key = withheld["key"]
                assert not any(r.split("\t")[:6] == key for r in index.fetch(key[0], int(key[1])-1, int(key[1])))
        assert metadata["statistics"]["output_rows"] > 60_000_000
    print(f"{kind}: integrity, index count, and biological checks PASS", flush=True)
    return {"status": "PASS", "records": sum(counts.values()), "contig_counts": counts,
            "sampled_rows": len(samples), "source_comparisons": comparisons,
            "payload_bytes": sum(a["size_bytes"] for a in metadata["files"]),
            "preparation": file_identity(directory / "preparation.json"),
            "preparer_sha256": metadata["preparer_sha256"],
            "auditor_preparer_sha256": PREPARER_SHA256}


def local_install_test(build, output):
    components = []
    source_by_url = {}
    for kind in ("alphamissense", "cadd", "spliceai"):
        directory = build / kind
        metadata = json.loads((directory / "preparation.json").read_text())
        files = metadata["files"] + [file_identity(directory / "preparation.json")]
        for asset in files:
            # An explicitly mocked transfer: these URLs are never contacted.
            asset["url"] = f"https://huggingface.co/datasets/local-test/starter/resolve/{'0'*40}/{kind}/{asset['name']}"
            source_by_url[asset["url"]] = directory / asset["name"]
        components.append({"id": kind, "version": "isolated-validation-v1", "files": files})
    plan = {"schema": "guide-iei.essential-annotations/v1", "release_status": "ready",
            "assembly": "GRCh38", "vep_release": 113, "components": components}
    path = output / "local-test-plan.json"
    path.write_text(json.dumps(plan, indent=2) + "\n")
    plan = load_plan(path)
    root = output / "selected data location"
    def copy_source(args, **kwargs):
        shutil.copyfile(source_by_url[args[2]], args[3])
    install(root, plan, run=copy_source)
    assert len(installed_paths(root, plan)) == 3
    def unexpected_download(*args, **kwargs):
        raise AssertionError("Retry must reuse completed components")
    install(root, plan, run=unexpected_download)
    # Corrupt only an isolated copied index, then verify repair and preservation.
    component = plan["components"][-1]
    directory = component_path(root, component)
    index = directory / "spliceai.vcf.gz.tbi"
    with index.open("r+b") as stream:
        stream.write(b"BROKEN")
    assert "spliceai" not in installed_paths(root, plan)
    install(root, plan, run=copy_source)
    assert len(installed_paths(root, plan)) == 3
    assert list(directory.parent.glob("*.replaced-*"))
    return {"status": "PASS", "transport": "local copy (network not tested)",
            "checks": ["real payload installation", "space-containing path", "retry reuse",
                       "corruption detection", "repair", "damaged-copy retention"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", type=Path, required=True)
    parser.add_argument("--sources", type=Path, default=ROOT / "references/starter-sources")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {k: pool.submit(audit_component, k, args.build, args.sources)
                   for k in ("alphamissense", "cadd", "spliceai")}
        results = {k: f.result() for k, f in futures.items()}
    results["installation"] = local_install_test(args.build, args.output)
    (args.output / "validation.json").write_text(json.dumps(results, indent=2) + "\n")
    print(f"ALL CHECKS PASSED: {args.output / 'validation.json'}", flush=True)


if __name__ == "__main__":
    main()
