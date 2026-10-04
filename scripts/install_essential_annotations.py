#!/usr/bin/env python3
"""One operation for environment, essential reference data and starter scores.

No advanced WGS/license-gated resources, no edits to installed configuration.
Run with --config and --annotation-root; the service supplies resolved paths.
"""
import argparse
import fcntl
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pipeline.starter_package import PLAN, install, load_plan, installed_paths
from local_service.essential_setup import missing_resources, reference_allowance
from local_service.container_startup import managed_colima_environment


def prepare(config_path, annotation_root, plan_path=PLAN, run=subprocess.run):
    import yaml
    from tempfile import TemporaryDirectory
    load_plan(plan_path)
    root = Path(annotation_root).resolve()
    if not root.is_dir():
        raise ValueError("Selected annotation storage is unavailable; reconnect it before setup")
    def resolve_paths(value):
        if isinstance(value, dict):
            return {k: resolve_paths(v) for k, v in value.items()}
        if isinstance(value, list):
            return [resolve_paths(v) for v in value]
        if isinstance(value, str) and value.startswith("references/"):
            return str(root / value[len("references/"):])
        return value
    config = resolve_paths(yaml.safe_load(Path(config_path).read_text()))
    with TemporaryDirectory(prefix=".essential-config-", dir=root) as temporary:
        path = Path(temporary) / "resolved.yaml"
        path.write_text(yaml.safe_dump(config))
        return _prepare(path, root, plan_path, run)


def _prepare(config_path, annotation_root, plan_path=PLAN, run=subprocess.run):
    import yaml
    plan = load_plan(plan_path)  # Fail before any tools or large references change.
    config = yaml.safe_load(Path(config_path).read_text())
    root = Path(annotation_root).resolve()
    if not root.is_dir():
        raise ValueError("Selected annotation storage is unavailable; reconnect it before setup")
    if config.get("reference", {}).get("assembly") != "GRCh38":
        raise ValueError("Essential setup supports GRCh38 only")
    resolve = lambda value: Path(value) if Path(value).is_absolute() else ROOT / value
    missing = missing_resources(config, resolve)
    # Conservative transient allowance: cache archive + extraction, LOFTEE,
    # reference/GTF/support tracks and latest clinical snapshots. UI calls it
    # an estimate, never an exact download size.
    existing = installed_paths(root, plan)
    needed = reference_allowance(missing) + sum(
        a["size_bytes"] for c in plan["components"] if c["id"] not in existing for a in c["files"])
    if shutil.disk_usage(root).free < needed:
        raise ValueError(f"Not enough space for essential setup: allow {needed:,} free bytes")
    with (root / ".essential-setup.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        print("=== Preparing the annotation environment ===", flush=True)
        env = dict(os.environ, IEI_PYTHON_BIN=sys.executable, PYTHONUNBUFFERED="1")
        run(["bash", str(ROOT / "scripts/setup_environment.sh"), "--install", "--yes",
             "--engine-only", "--config", str(config_path)], check=True, env=env)
        if sys.platform == "darwin":
            # setup_environment may have just created the managed Colima
            # profile. A child shell cannot export its connection into this
            # coordinator, so resolve it before launching dataset HTS tools.
            env.update(managed_colima_environment(env))
        # Reuse the established downloader and its LOFTEE/FASTA/index safeguards.
        # Gene models support LOFTEE/PTC and coding-region preparation.
        # Optional repeats, liftover, SCREEN, AVI and full SpliceAI stay in
        # the analysis workbench rather than extending first-time setup.
        print("=== Preparing essential reference files ===", flush=True)
        groups = {"VEP cache": "vep_cache", "Reference genome": "fasta",
                  "LOFTEE": "loftee", "Gene models": "gtf"}
        needed_groups = [group for name, group in groups.items() if name in missing]
        if needed_groups:
            run(["bash", str(ROOT / "scripts/download_references.sh"), str(config_path), "--only",
                 ",".join(needed_groups), "--skip-final-status"], check=True, env=env)
        install(root, plan, run=run)
        # Preserve a completed official snapshot on retry; do not auto-refresh
        # it at every launch. Explicit update actions remain in the workbench.
        for label, script in (("ClinVar", "fetch_clinvar.sh"), ("ClinGen", "update_clingen_erepo.sh")):
            if label in missing_resources(config, resolve):
                print(f"=== Preparing latest official {label} snapshot ===", flush=True)
                # First-time explicit setup must fetch even if per-run auto
                # refresh is disabled; do not alter the user's saved choice.
                step_config = dict(config)
                step_config["clinvar"] = dict(config.get("clinvar", {}), auto_fetch=True)
                from tempfile import TemporaryDirectory
                with TemporaryDirectory(prefix="essential-config-", dir=root) as temp:
                    path = Path(temp) / "config.yaml"
                    path.write_text(yaml.safe_dump(step_config))
                    run(["bash", str(ROOT / "scripts" / script), str(path)], check=True, env=env)
        missing = missing_resources(config, resolve)
        if missing:
            raise ValueError("Essential setup incomplete: " + ", ".join(missing))
        print("=== Essential annotations are ready ===", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--annotation-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, default=PLAN)
    args = parser.parse_args()
    prepare(args.config, args.annotation_root, args.manifest)


if __name__ == "__main__":
    main()
