#!/usr/bin/env python3
"""Opt-in anonymous download/install of the pinned public starter package.

Always uses a NEW scratch directory; never replaces installed user resources.
Curl's default config is disabled and no Hugging Face credential is passed.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

if not __debug__:
    raise RuntimeError("Release validation requires assertions; run Python without -O or PYTHONOPTIMIZE")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pipeline.starter_package import (component_path, component_valid, install,
                                      installed_paths, load_plan, sha256)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    plan = load_plan(args.manifest)
    scratch = args.output.resolve()
    scratch.mkdir(parents=True, exist_ok=False)
    commands = scratch / "anonymous-tools"
    commands.mkdir()
    curl = shutil.which("curl")
    if not curl:
        raise RuntimeError("curl is required for the anonymous download test")
    # Prevent a developer's ~/.curlrc from silently adding authentication.
    wrapper = commands / "curl"
    wrapper.write_text('#!/bin/sh\nexec "' + curl + '" -q "$@"\n')
    wrapper.chmod(0o700)
    env = {k: v for k, v in os.environ.items() if k not in {"HF_TOKEN", "HUGGING_FACE_HUB_TOKEN"}}
    env["PATH"] = str(commands) + os.pathsep + env.get("PATH", "")
    root = scratch / "selected data location"
    def anonymous_run(argv, **kwargs):
        return subprocess.run(argv, env=env, **kwargs)
    install(root, plan, run=anonymous_run)
    assert len(installed_paths(root, plan)) == 3
    assert all(component_valid(component_path(root, c), c, strict=True) for c in plan["components"])
    def no_download(*args, **kwargs):
        raise AssertionError("A completed installation must be reused on retry")
    install(root, plan, run=no_download)
    report = {"status": "PASS", "authentication": "none; curl defaults disabled",
              "manifest_sha256": sha256(args.manifest),
              "components": [{"id": c["id"], "version": c["version"],
                              "files": len(c["files"]), "bytes": sum(a["size_bytes"] for a in c["files"])}
                             for c in plan["components"]],
              "checks": ["anonymous fresh download", "SHA-256 of every asset", "atomic component installation",
                         "space-containing selected path", "strict installed verification", "retry reuse"]}
    (scratch / "validation.json").write_text(json.dumps(report, indent=2) + "\n")
    print(f"ANONYMOUS INSTALL PASSED: {scratch / 'validation.json'}")


if __name__ == "__main__":
    main()
