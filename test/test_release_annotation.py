#!/usr/bin/env python3
"""Opt-in real annotation and isolated-library release test (not a clean machine).

python3 test/test_release_annotation.py --run [--app /path/to/GUIDE-IEI.app]
Uses existing references read-only; private GenIA is disabled. Public clinical
catalogs/stamps are copied to scratch because the pipeline may refresh them.
Results are retained under ignored test/out for release evidence.
"""
import argparse
import gzip
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--app", type=Path)
    args = parser.parse_args()
    if not args.run:
        print("SKIP: real release annotation requires --run and installed references")
        return
    import yaml
    code = args.app.resolve() / "Contents/Resources/application" if args.app else ROOT
    sys.path.insert(0, str(code))
    from local_service.cohort_store import CohortStore, read_vcf_header
    from local_service.sample_library import SampleLibrary
    scratch_root = ROOT / "test/out"
    scratch_root.mkdir(exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix="release annotation ", dir=scratch_root)).resolve()
    print(f"Release test evidence: {scratch}", flush=True)

    def paths(value):
        if isinstance(value, dict):
            return {key: paths(item) for key, item in value.items()}
        if isinstance(value, list):
            return [paths(item) for item in value]
        if isinstance(value, str) and value.startswith("references/"):
            return str(ROOT / value)
        return value

    config = paths(yaml.safe_load((code / "config/annotation.config.yaml").read_text()))
    config["genia"]["enabled"] = False
    for resource in ("clinvar", "clingen_erepo"):
        original = Path(config[resource]["dest_dir"])
        private = scratch / resource
        shutil.copytree(original, private)
        for key, value in config[resource].items():
            if isinstance(value, str) and value.startswith(str(original) + "/"):
                config[resource][key] = str(private / Path(value).relative_to(original))
        config[resource]["dest_dir"] = str(private)
    config_path = scratch / "annotation.yaml"
    config_path.write_text(yaml.safe_dump(config))
    source = ROOT / "test/regression/annotation_regression.GRCh38.vcf"
    output = scratch / "regression.vep.vcf.gz"
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", IEI_COHORT_INDEX_READERS="1",
               IEI_PYTHON_BIN=sys.executable,
               PATH=str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", ""))
    with (scratch / "annotation.log").open("w") as log:
        subprocess.run(["bash", str(code / "scripts/run_annotation.sh"), "--input", str(source),
                        "--output", str(output), "--config", str(config_path),
                        "--input-assembly", "GRCh38", "--all-variants", "--no-clinvar"],
                       env=env, stdout=log, stderr=subprocess.STDOUT, check=True)
    final = scratch / "regression.vep.aamatch.vcf.gz"
    if not final.exists():
        final = output
    subprocess.run([sys.executable, str(code / "pipeline/validate_regression_annotations.py"),
                    "--config", str(config_path), "--expected", str(ROOT / "test/regression/expected.yaml"),
                    "--vcf", str(final), "--json", str(scratch / "regression.report.json")], check=True, env=env)
    # Synthetic genotype quality for persistence checks (the public annotation
    # panel supplies GT only). Do not pretend these are measured call qualities.
    review = scratch / "synthetic-review.vcf"
    with gzip.open(final, "rt") as reader, review.open("w") as writer:
        for line in reader:
            if line.startswith("#CHROM"):
                writer.write('##FORMAT=<ID=DP,Number=1,Type=Integer,Description="Synthetic test depth">\n')
                writer.write('##FORMAT=<ID=GQ,Number=1,Type=Integer,Description="Synthetic test quality">\n')
                writer.write('##FORMAT=<ID=AD,Number=R,Type=Integer,Description="Synthetic test allele depths">\n')
            if not line.startswith("#"):
                fields = line.rstrip().split("\t")
                assert fields[8] == "GT", "Update synthetic quality helper for new fixture FORMAT"
                fields[8] = "GT:DP:GQ:AD"
                fields[9:] = [gt + ":40:99:20,20" for gt in fields[9:]]
                line = "\t".join(fields) + "\n"
            writer.write(line)
    state = scratch / "isolated-library"
    state.mkdir()
    database = state / "cohort.sqlite3"
    cohort = CohortStore(database, index_readers=1)
    library = SampleLibrary(state, cohort, workspace_dir=scratch)
    payload = {"analysis_scope": "whole_genome", "index_scope": "compact", "include_in_cohort": True,
               "qc_settings": {"minDp": 10, "minGq": 20}, "prefilter_settings": {}}
    result = library.import_vcf(review, payload)
    assert result["datasets"], result
    reopened = SampleLibrary(state, CohortStore(database, index_readers=1), workspace_dir=scratch)
    assert len(reopened.list()) == len(result["datasets"])
    assert reopened.cohort.query({"mode": "variant", "query": "17:42322474:C:T"})["total"] > 0
    for dataset in result["datasets"]:
        assert read_vcf_header(reopened.file(dataset["id"])).samples
    duplicate = reopened.import_vcf(review, payload)
    assert len(reopened.list()) == len(result["datasets"]), duplicate
    (scratch / "persistence.report.json").write_text(json.dumps({
        "status": "PASS", "datasets": len(result["datasets"]),
        "checks": ["library import", "cohort query", "reopen", "managed VCF", "duplicate import"],
        "scope": "Configured host; existing references; synthetic quality; not clean-machine installation",
    }, indent=2) + "\n")
    print(f"ANNOTATION + LIBRARY RELEASE TEST PASSED: {scratch}")


if __name__ == "__main__":
    main()
