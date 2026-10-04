#!/usr/bin/env python3
"""Opt-in cold reopen and exome/WGS jobs after test_clean_macos_setup.py.

Uses only that retained test VM/data and public synthetic alleles. Waits for
setup to finish when invoked concurrently. Does not use a patient library.
"""
import argparse
from collections import Counter
import gzip
import json
import os
from pathlib import Path
import subprocess
import time
import urllib.error
import urllib.request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("context", type=Path)
    parser.add_argument("--app", type=Path, help="test an updated preview against the isolated installation")
    parser.add_argument("--output", type=Path, help="retain a separate upgrade-test report")
    parser.add_argument("--prepare-engine", action="store_true")
    args = parser.parse_args()
    context = json.loads(args.context.read_text())
    setup_output = Path(context["output"])
    output = args.output.resolve() if args.output else setup_output
    if args.output:
        output.mkdir(parents=True, exist_ok=False)
    app = args.app.resolve() if args.app else Path(context["app"])
    root = app / "Contents/Resources/application"
    env = context["environment"]
    tools = Path(env["IEI_TOOLS_DIR"])
    cli_env = {**env, "PATH": str(tools / "bin") + ":" + env["PATH"]}
    command = ["/usr/bin/sandbox-exec", "-f", context["sandbox"]]
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def api(route, body=None):
        req = urllib.request.Request(f'http://127.0.0.1:{context["port"]}' + route,
            data=None if body is None else json.dumps(body).encode(), headers={"Content-Type": "application/json"})
        try:
            with opener.open(req, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            raise RuntimeError(error.read().decode()) from error
    deadline = time.monotonic() + 6 * 3600
    while time.monotonic() < deadline:
        try:
            setup = json.loads((setup_output / "validation.json").read_text())
            if setup["status"] != "RUNNING":
                assert setup["status"] == "PASS", "Fresh setup failed; downstream test must not bypass it"
                # Setup records the result before gracefully stopping its app/VM.
                stopped = subprocess.run([str(tools / "bin/colima"), "status"], env=cli_env,
                    capture_output=True, timeout=20)
                if stopped.returncode != 0:
                    break
        except (OSError, ValueError):
            pass
        time.sleep(10)
    else:
        raise TimeoutError("Fresh setup did not finish")
    report = {"status": "RUNNING", "started": time.time(), "checks": [], "jobs": []}
    def record():
        (output / "annotation-validation.json").write_text(json.dumps(report, indent=2) + "\n")
    record()
    child = None
    log = (output / "annotation-launcher.log").open("w")
    try:
        print("COLD REOPEN: start the isolated, newly prepared VM", flush=True)
        subprocess.run(command + [str(tools / "bin/colima"), "start", "--activate=false", "--save-config=false"],
            env=cli_env, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=600)
        child = subprocess.Popen(command + [str(app / "Contents/MacOS/GUIDE-IEI")], env=env, stdout=log, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + (900 if args.prepare_engine else 120)
        preparation_requested = False
        phases = set()
        while time.monotonic() < deadline:
            assert child.poll() is None, "Native app exited on cold reopen"
            try:
                status = api("/api/essential-setup/status")
                startup_files = sorted((Path(env["IEI_APP_SUPPORT_DIR"]) / "logs").glob("startup-*.json"), key=lambda p: p.stat().st_mtime)
                startup = json.loads(startup_files[-1].read_text()) if startup_files else {}
                phase = startup.get("phase")
                if phase in {"waiting", "preparing", "failed", "ready"}:
                    assert startup["annotation_path"] == env["IEI_DEFAULT_ANNOTATION_ROOT"], startup
                    phases.add(phase)
                if phase == "failed":
                    raise RuntimeError(startup["message"])
                if args.prepare_engine and phase == "waiting" and not preparation_requested:
                    control = startup_files[-1].with_name(startup_files[-1].stem + ".control.json")
                    control.write_text(json.dumps({"action": "prepare"}))
                    preparation_requested = True
                    print("UPGRADE: native Prepare installs the updated bundled engine", flush=True)
                if status["available"] and phase == "ready":
                    break
            except (OSError, ValueError):
                pass
            time.sleep(1)
        else:
            raise TimeoutError("Prepared app failed to become ready")
        resource_jobs = api("/api/resource-downloads")["jobs"]
        if args.prepare_engine:
            assert preparation_requested and "preparing" in phases and "ready" in phases
            assert all(j["status"] == "succeeded" for j in resource_jobs)
            report["checks"].append("Updated bundled engine installed; selected location survives waiting/preparing/ready")
        else:
            assert not resource_jobs, "Cold reopen unnecessarily repeated setup"
        profile = api("/api/capabilities")["annotation_profile"]
        assert profile["ready"], profile
        source_map = {s["id"]: s for s in profile["sources"]}
        assert not source_map["dbnsfp"]["required"] and not source_map["dbnsfp"]["installed"]
        report["checks"].append("Existing references reused; ready without dbNSFP")
        # Build a genuinely raw VCF from known public test alleles, not from a
        # patient's data or an already annotated clinical file.
        import pysam
        import yaml
        config = yaml.safe_load((root / "config/annotation.config.yaml").read_text())
        annotations = Path(env["IEI_DEFAULT_ANNOTATION_ROOT"])
        plan = json.loads((root / "config/essential-annotations.json").read_text())
        components = {c["id"]: annotations / "starter" / c["id"] / c["version"] for c in plan["components"]}
        alleles = set()
        for kind, filename in (("alphamissense", "alphamissense.tsv.gz"), ("spliceai", "spliceai.vcf.gz")):
            with pysam.TabixFile(str(components[kind] / filename)) as table:
                for chrom, start in (("1", 12740000), ("4", 6070054), ("6", 89500000), ("7", 150405904), ("17", 42000000), ("X", 10000000)):
                    for i, line in enumerate(table.fetch(chrom, start)):
                        row = line.split("\t")
                        ref, alt = row[3:5] if kind == "spliceai" else row[2:4]
                        alleles.add((chrom, row[1], ref, alt))
                        if i >= 5:
                            break
        rows = [[chrom, pos, ".", ref, alt, ".", "PASS", "."] for chrom, pos, ref, alt in alleles]
        rows.append(["1", "12746434", ".", "T", "TA", ".", "PASS", "."])
        fasta = annotations / config["reference"]["fasta"]["path"].removeprefix("references/")
        with pysam.FastaFile(str(fasta)) as fa:
            ref = fa.fetch("1", 999999, 1000000).upper()
            assert ref in "ACGT"
            rows.append(["1", "1000000", ".", ref, "A" if ref != "A" else "C", ".", "PASS", "."])
            for row in rows:
                assert fa.fetch(row[0], int(row[1])-1, int(row[1])-1+len(row[3])).upper() == row[3], row
        rows.sort(key=lambda row: (row[0], int(row[1]), row[3], row[4]))
        raw = output / "synthetic-raw.vcf"
        raw.write_text("##fileformat=VCFv4.2\n##reference=GRCh38\n" +
            "".join(f"##contig=<ID={chrom}>\n" for chrom in sorted({r[0] for r in rows})) +
            '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n' +
            '##FORMAT=<ID=DP,Number=1,Type=Integer,Description="Depth">\n' +
            '##FORMAT=<ID=GQ,Number=1,Type=Integer,Description="Genotype quality">\n' +
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tSYNTHETIC_CLEAN_TEST\n" +
            "".join("\t".join(row[:5] + ["60", "PASS", ".", "GT:DP:GQ", "0/1:40:99"]) + "\n" for row in rows))
        for scope in ("exome", "whole_genome"):
            print("ANNOTATE: " + scope, flush=True)
            job = api("/api/jobs", {"input_path": str(raw), "output_path": str(output / (scope + ".vep.vcf.gz")),
                "analysis_scope": scope, "input_assembly": "auto", "annotation_options": {"fork": 2}})
            deadline = time.monotonic() + 4 * 3600
            last = None
            while time.monotonic() < deadline:
                job = api("/api/jobs/" + job["id"])
                report["active_job"] = job
                record()
                state = (job["status"], job.get("stage"), job.get("message"))
                if state != last:
                    print(state, flush=True)
                    last = state
                if job["status"] in {"failed", "cancelled", "interrupted"}:
                    raise RuntimeError(str(job))
                if job["status"] == "succeeded":
                    break
                time.sleep(5)
            else:
                raise TimeoutError(scope + " annotation timed out")
            counts = Counter()
            final = Path(job.get("final_output_path") or job["output_path"])
            with gzip.open(final, "rt") as source:
                for line in source:
                    if line.startswith("##INFO=<ID=CSQ,"):
                        fields = line.split("Format: ", 1)[1].split('"', 1)[0].strip().split("|")
                    if line.startswith("#"):
                        continue
                    row = line.rstrip().split("\t")
                    assert row[9].split(":")[0] == "0/1", row
                    info = dict(item.split("=", 1) for item in row[7].split(";") if "=" in item)
                    for text in info.get("CSQ", "").split(","):
                        csq = dict(zip(fields, text.split("|")))
                        for field in ("StarterAM_score", "StarterCADD_phred", "SpliceAI_pred_DS_AG", "LoF"):
                            if csq.get(field):
                                counts[field] += 1
                    counts["records"] += 1
            assert all(counts[k] > 0 for k in ("StarterAM_score", "StarterCADD_phred", "SpliceAI_pred_DS_AG", "LoF")), counts
            qc = json.loads(Path(str(final) + ".annotation_qc.json").read_text())
            splice = qc["details"]["spliceai"]
            assert splice["annotated_records"] == splice["eligible_mane_snv_records"] == 36, splice
            report["jobs"].append({"job": job, "counts": dict(counts)})
            report["checks"].append(scope + ": real pipeline, starter scores, LOFTEE and genotypes present")
            record()
        assert report["jobs"][1]["counts"]["records"] > report["jobs"][0]["counts"]["records"], "Exome/WGS region filtering was not distinguished"
        report["status"] = "PASS"
    except BaseException as error:
        report["status"] = "FAIL"
        report["error"] = str(error)
        raise
    finally:
        report["finished"] = time.time()
        record()
        if child is not None and child.poll() is None:
            try:
                for job in api("/api/jobs")["jobs"]:
                    if job["status"] in {"queued", "running"}:
                        api("/api/jobs/" + job["id"] + "/cancel", {})
                status = api("/api/service/status")
                api("/api/service/quit", {"confirm": True, "instance_id": status["instance_id"]})
                child.wait(timeout=45)
            except (OSError, RuntimeError, subprocess.TimeoutExpired):
                child.terminate()
                child.wait(timeout=45)
        subprocess.run([str(tools / "bin/colima"), "stop"], env=cli_env, stdout=log, stderr=subprocess.STDOUT, timeout=120)
        log.close()
        print("Report: " + str(output / "annotation-validation.json"), flush=True)


if __name__ == "__main__":
    main()
