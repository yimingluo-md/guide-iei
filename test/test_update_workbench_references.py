"""Check refresh destinations without downloading or building databases."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def test_refresh_destinations(tmp_path):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    for name in ("update_workbench_references.sh", "update_gene_knowledge.sh"):
        shutil.copy2(ROOT / "scripts" / name, scripts / name)

    binaries = tmp_path / "bin"
    binaries.mkdir()
    curl = binaries / "curl"
    curl.write_text("#!/bin/sh\nexit 0\n")
    curl.chmod(0o755)
    python = binaries / "python3"
    python.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "with open(os.environ['CALLS'], 'a') as f:\n"
        "    f.write(json.dumps(sys.argv[1:]) + '\\n')\n"
    )
    python.chmod(0o755)
    calls = tmp_path / "calls.jsonl"
    env = {**os.environ, "PATH": f"{binaries}:/usr/bin:/bin", "IEI_PYTHON_BIN": str(python), "CALLS": str(calls)}

    subprocess.run(["bash", str(scripts / "update_workbench_references.sh")], env=env, check=True, capture_output=True, text=True)
    recorded = [json.loads(line) for line in calls.read_text().splitlines()]
    assert len(recorded) == 2
    assert Path(recorded[0][0]).name == "build_workbench_reference_data.py"
    builder = recorded[1]
    assert Path(builder[0]).name == "build_gene_knowledge.py"
    bundled = tmp_path / "webui/public/bundled-data"
    assert builder[builder.index("--output") + 1] == str(bundled / "gene_knowledge_public.sqlite3")
    assert builder[builder.index("--manifest") + 1] == str(bundled / "gene_knowledge_manifest.json")

    calls.unlink()
    subprocess.run(["bash", str(scripts / "update_gene_knowledge.sh")], env=env, check=True, capture_output=True, text=True)
    workstation = json.loads(calls.read_text())
    assert Path(workstation[0]).name == "build_gene_knowledge.py"
    assert "--output" not in workstation
    assert "--manifest" not in workstation
