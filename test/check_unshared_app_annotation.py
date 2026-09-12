#!/usr/bin/env python3
"""Opt-in real Docker Desktop test: application code in unshared /Applications.

Requires an existing matching image and local public regression references.
Creates/removes only its own temporary application-source folder. Stores logs
and public outputs in a new --output directory; does not change Docker settings,
install the app, download datasets, or open the patient's library.
"""
import argparse
import gzip
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import zipfile
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from build_macos_app import copy_source


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True, help="existing regression config with absolute reference paths")
    parser.add_argument("--output", type=Path, required=True, help="new directory for public outputs/logs")
    parser.add_argument("--baseline", type=Path, help="previous regression VCF to compare all CSQ fields")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    config = yaml.safe_load(args.config.read_text())
    runtime = config["container"]["runtime"]
    image = config["container"]["image"]
    assert runtime == "docker", "this regression specifically requires Docker Desktop"
    context = subprocess.check_output([runtime, "context", "show"], text=True).strip()
    assert context == "desktop-linux", "select Docker Desktop before this opt-in test"
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1",
           "IEI_PYTHON_BIN": sys.executable, "IEI_CONTAINER_WORK_DIR": str(output / "container-work"),
           "HTS_VIA_CONTAINER": "1", "IMAGE": image, "RUNTIME": runtime, "AVI_FORCE_CONTAINER": "1"}

    def run(name, command, *, cwd, expected=0):
        print(name, flush=True)
        with (output / (name + ".log")).open("w") as log:
            result = subprocess.run(command, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT)
        if (expected == 0 and result.returncode != 0) or (expected != 0 and result.returncode == 0):
            raise RuntimeError(f"{name} returned {result.returncode}; see {output / (name + '.log')}")
        return (output / (name + ".log")).read_text()

    with tempfile.TemporaryDirectory(prefix="GUIDE-IEI-sharing-check-", dir="/Applications") as folder:
        app = Path(folder)
        copy_source(app)
        (app / "desktop-build.json").write_text('{"preview": true}')
        # Negative control first AND last: success would not test the reported bug.
        blocked_probe = [runtime, "run", "--rm", "--pull=never", "--network=none", "--mount",
                         f"type=bind,source={app},target=/probe,readonly", "--entrypoint", "sh", image,
                         "-c", "test -r /probe/scripts/setup_environment.sh"]
        diagnostic = run("app-unshared-before", blocked_probe, cwd=output, expected=1)
        assert "bind source path does not exist" in diagnostic or "Mounts denied" in diagnostic, diagnostic
        run("setup", ["bash", str(app / "scripts/setup_environment.sh"), "--install", "--yes", "--engine-only",
                      "--config", str(args.config.resolve())], cwd=app)
        run("readiness", [sys.executable, str(app / "local_service/container_workspace.py"), "--image", image], cwd=app)
        run("plugins", ["bash", str(app / "scripts/verify_container_stack.sh"), str(args.config.resolve()), "--quick"], cwd=app)
        # Exercise the actual AVI script's container fallback without an 88GB
        # conversion: compile the staged helper, then reject an empty archive.
        archive = output / "invalid-avi.zip"
        with zipfile.ZipFile(archive, "w"):
            pass
        avi_config = output / "avi.yaml"
        avi_config.write_text(yaml.safe_dump({"container": config["container"], "custom_tracks": {
            "AlphaGenomeAVI": {"dest_dir": str(output / "avi")}}}))
        diagnostic = run("avi-staged-helpers", ["bash", str(app / "scripts/prepare_avi.sh"), str(archive), str(avi_config)], cwd=app, expected=1)
        assert "Unexpected AVI ZIP members" in diagnostic, diagnostic
        # Positive tiny conversion through the real Linux binary and Tabix.
        # Only the dataset identity/row-count pin is mocked for synthetic input;
        # byte conversion, image execution, indexing and hashes are real.
        sys.path.insert(0, str(ROOT / "test"))
        from test_avi_dataset import AviTest
        synthetic = AviTest()
        synthetic.root = output
        archive, identity = synthetic.source('chr1\t1\tT\tA\t0\t1\nchr1\t1\tT\tC\t0\t2\nchr1\t1\tT\tG\t0\t3\n')
        spec = importlib.util.spec_from_file_location("unshared_avi", app / "pipeline/avi_dataset.py")
        avi = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(avi)
        staged = output / "avi_convert.cpp"
        shutil.copy2(app / "pipeline/avi_convert.cpp", staged)
        binary = output / "avi_convert"
        run("avi-compile", ["bash", str(app / "scripts/avi_hts.sh"), "c++", "-O3", "-std=c++17", str(staged), "-lz", "-o", str(binary)], cwd=app)
        with patch.dict(os.environ, env), patch.object(avi, "inspect_archive", return_value=identity), patch.object(avi, "EXPECTED_ROWS", 3):
            prepared = avi.prepare(archive, output / "synthetic-avi", binary, 1, container_converter=True)
            assert avi.valid_bundle(prepared / "manifest.json", strict=True)
            with gzip.open(prepared / "avi.grch38.vcf.gz", "rt") as handle:
                rows = [line.strip() for line in handle if not line.startswith("#")]
            assert len(rows) == 1 and 'phred=1,2,3' in rows[0], rows
        fixture = output / "public-regression.vcf"
        shutil.copy2(ROOT / "test/regression/annotation_regression.GRCh38.vcf", fixture)
        annotated = output / "annotation_regression.vep.vcf.gz"
        run("annotation", ["bash", str(app / "scripts/run_annotation.sh"), "--input", str(fixture),
                           "--output", str(annotated), "--config", str(args.config.resolve()),
                           "--input-assembly", "GRCh38", "--all-variants", "--no-clinvar"], cwd=app)
        final = output / "annotation_regression.vep.aamatch.vcf.gz"
        if not final.exists():
            final = annotated
        run("validate", [sys.executable, str(app / "pipeline/validate_regression_annotations.py"), "--config", str(args.config.resolve()),
                         "--expected", str(ROOT / "test/regression/expected.yaml"), "--vcf", str(final),
                         "--json", str(output / "annotation_regression.report.json")], cwd=app)
        if args.baseline:
            def csq(path):
                records = {}
                with gzip.open(path, "rt") as handle:
                    for line in handle:
                        if line.startswith("#"):
                            continue
                        fields = line.rstrip().split("\t")
                        info = dict(item.split("=", 1) for item in fields[7].split(";") if "=" in item)
                        records[tuple(fields[i] for i in (0, 1, 3, 4))] = sorted(info.get("CSQ", "").split(","))
                return records
            assert csq(final) == csq(args.baseline), "CSQ differs from baseline (including LOFTEE)"
        with (output / "index-check.log").open("w") as log:
            def tabix(*arguments):
                return subprocess.check_output(["bash", str(app / "scripts/avi_hts.sh"), "tabix", *arguments],
                                               cwd=app, env=env, stderr=log, text=True).splitlines()
            indexed = [line for contig in tabix("-l", str(final)) for line in tabix(str(final), contig)]
            with gzip.open(final, "rt") as handle:
                sequential = [line.rstrip('\n') for line in handle if not line.startswith('#')]
            assert sorted(indexed) == sorted(sequential), "Tabix queries differ from sequential records"
        run("app-unshared-after", blocked_probe, cwd=output, expected=1)
    (output / "sharing-test.json").write_text(json.dumps({"status": "PASS", "context": context,
        "image": image, "app_unshared_before_and_after": True, "full_annotation": True,
        "all_csq_equal_to_baseline": bool(args.baseline), "index_complete": True,
        "synthetic_avi_conversion": True}, indent=2) + "\n")
    print(f"PASS: {output / 'sharing-test.json'}", flush=True)


if __name__ == "__main__":
    main()
