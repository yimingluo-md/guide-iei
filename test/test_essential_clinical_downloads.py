#!/usr/bin/env python3
"""Opt-in fresh official ClinVar/ClinGen setup in a NEW scratch directory."""
import argparse
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    import yaml
    from local_service.essential_setup import required_files
    scratch = args.output.resolve()
    scratch.mkdir(parents=True, exist_ok=False)
    def resolve(value):
        if isinstance(value, dict):
            return {k: resolve(v) for k, v in value.items()}
        if isinstance(value, list):
            return [resolve(v) for v in value]
        if isinstance(value, str) and value.startswith("references/"):
            parts = Path(value).parts
            return str((scratch if parts[1] in {"clinvar", "clingen_erepo"} else ROOT / "references").joinpath(*parts[1:]))
        return value
    config = resolve(yaml.safe_load((ROOT / "config/annotation.config.yaml").read_text()))
    config["clinvar"]["auto_fetch"] = True
    config_path = scratch / "annotation.yaml"
    config_path.write_text(yaml.safe_dump(config))
    env = dict(os.environ, IEI_PYTHON_BIN=sys.executable,
               PATH=str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", ""))
    for script in ("fetch_clinvar.sh", "update_clingen_erepo.sh"):
        print(f"Fresh official source: {script}", flush=True)
        with (scratch / (script + ".log")).open("w") as log:
            subprocess.run(["bash", str(ROOT / "scripts" / script), str(config_path)],
                           stdout=log, stderr=subprocess.STDOUT, env=env, check=True)
    for label, paths in required_files(config, Path).items():
        if label in {"ClinVar", "ClinGen"}:
            assert all(p.is_file() and p.stat().st_size for p in paths), (label, paths)
    with sqlite3.connect(f"file:{scratch / 'clingen_erepo/clingen_erepo.sqlite3'}?mode=ro", uri=True) as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    report = {"status": "PASS", "scope": "Fresh official downloads; existing read-only FASTA and local engine",
              "clinvar": json.loads((scratch / "clinvar/clinvar_latest.GRCh38.vcf.gz.provenance.json").read_text()),
              "clingen": json.loads((scratch / "clingen_erepo/manifest.json").read_text())}
    (scratch / "validation.json").write_text(json.dumps(report, indent=2) + "\n")
    print(f"PASS: {scratch / 'validation.json'}")


if __name__ == "__main__":
    main()
