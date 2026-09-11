#!/usr/bin/env python3
"""Explicit real-Docker test: install packaged engine under an isolated tag.

Does not remove/replace vep-annotate:latest, mount patient folders, or change
runtime settings. Requires an already running Docker engine.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import uuid


def main():
    app = Path(sys.argv[1]).resolve()
    root = app / "Contents/Resources/application"
    python = app / "Contents/Frameworks/Python.framework/Versions/Current/bin/python3"
    subprocess.run(["codesign", "--verify", "--deep", "--strict", str(app)], check=True)
    image = "guide-iei-bundle-test:" + uuid.uuid4().hex
    subprocess.run(["docker", "info"], check=True, stdout=subprocess.DEVNULL)
    with tempfile.TemporaryDirectory(prefix="guide-iei-bundle-test-") as temporary:
        config = Path(temporary) / "config.yaml"
        config.write_text(f"container:\n  runtime: docker\n  image: {image}\n")
        command = ["bash", str(root / "scripts/setup_environment.sh"), "--install", "--yes", "--engine-only", "--config", str(config)]
        env = dict(os.environ, IEI_PYTHON_BIN=str(python), PYTHONUNBUFFERED="1")
        try:
            started = time.monotonic()
            first = subprocess.run(command, env=env, capture_output=True, text=True, timeout=240)
            print(first.stdout, first.stderr)
            assert first.returncode == 0, "Packaged engine install failed"
            assert "Installing bundled annotation engine" in first.stdout
            assert "Downloading and building" not in first.stdout
            manifest = json.loads((root / "bundled-engine/manifest.json").read_text())
            actual = subprocess.check_output(["docker", "image", "inspect", "--format", "{{.Id}}", image], text=True).strip()
            assert actual == manifest["image_id"]
            print(f"BUNDLED INSTALL PASSED ({time.monotonic() - started:.1f}s; existing Docker layers may be cached)")
            again = subprocess.run(command, env=env, capture_output=True, text=True, timeout=60)
            assert again.returncode == 0, again.stdout + again.stderr
            assert "matches this GUIDE-IEI version" in again.stdout
            assert "Installing bundled" not in again.stdout
            print("REUSE PASSED: second setup did not load/build/pull an image")
            subprocess.run(["codesign", "--verify", "--deep", "--strict", str(app)], check=True)
            print("APP SEAL PASSED: direct setup did not modify signed resources")
        finally:
            # Only the uniquely named tag owned by this test. The production
            # image and its existing tags remain present.
            subprocess.run(["docker", "image", "rm", image], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


if __name__ == "__main__":
    main()
