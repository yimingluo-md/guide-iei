#!/usr/bin/env python3
"""Opt-in real Colima test. Creates/deletes only its unique temporary VM.

Usage: python3 test/check_colima_sharing_repair.py PATH_TO_ENGINE_TAR_GZ
Requires macOS, managed Colima/Lima tools, and internet for the VM base image.
Does not change the selected Docker context or mount patient/reference folders.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid
from unittest.mock import patch

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from local_service import colima_sharing
from local_service.container_startup import DockerStartup, managed_colima_environment


def main():
    archive = Path(sys.argv[1]).resolve()
    assert archive.is_file()
    manifest = json.loads((archive.parent / "manifest.json").read_text())
    profile = "guide-iei-sharing-test-" + uuid.uuid4().hex[:10]
    env = dict(os.environ)
    env.pop("DOCKER_CONTEXT", None)
    context_before = subprocess.check_output(["docker", "context", "show"], text=True).strip()
    with tempfile.TemporaryDirectory(prefix="guide-iei-colima-test-") as temp:
        stage = Path(temp).resolve()
        # Colima stores sockets under ~/.colima even with COLIMA_HOME set in
        # this pinned release. Use a unique profile there, removed in finally.
        colima_home = Path.home() / ".colima"
        folder = colima_home / profile
        assert not folder.exists(), "Refusing to reuse an existing profile"
        env["COLIMA_HOME"] = str(colima_home)
        env["DOCKER_CONFIG"] = str(stage / "docker-config")
        env["DOCKER_HOST"] = "unix://" + str(folder / "docker.sock")
        # Only the empty shared folder is visible initially. The synthetic app
        # sits beside it, intentionally outside Colima's declared mounts.
        shared = stage / "shared"
        shared.mkdir()
        app = stage / "synthetic-app"
        (app / "scripts").mkdir(parents=True)
        (app / "scripts/setup_environment.sh").write_text("# synthetic mount probe\n")
        try:
            print("Creating isolated test VM: " + profile, flush=True)
            subprocess.run(["colima", "start", "--profile", profile, "--activate=false",
                "--cpu", "2", "--memory", "2", "--disk", "10", "--mount", str(shared) + ":w"],
                env=env, check=True, timeout=600)
            subprocess.run(["docker", "image", "load", "--input", str(archive)], env=env, check=True, timeout=180)
            image = manifest["image_id"]
            # This host may run Desktop. Simulate only the otherwise-unused
            # default socket and Desktop absence; all Colima VM commands and
            # recovered-socket Docker probes are real, in isolated state.
            subprocess.run(["colima", "stop", "--profile", profile], env=env, check=True, timeout=120)
            unselected = dict(env)
            unselected.pop("DOCKER_HOST")
            original_run, original_resolve, original_glob = subprocess.run, Path.resolve, Path.glob
            def run(args, **kwargs):
                if Path(args[0]).name == "docker" and args[1:] == ["info"] and not kwargs.get("env", {}).get("DOCKER_HOST"):
                    return subprocess.CompletedProcess(args, 1, stdout=b"", stderr=b"")
                return original_run(args, **kwargs)
            def resolve(path, *args, **kwargs):
                if str(path) in ("/var/run/docker.sock", "/private/var/run/docker.sock"):
                    return Path("/private/var/run/docker.sock")
                return original_resolve(path, *args, **kwargs)
            def glob(path, pattern):
                if path == colima_home and pattern == "*/colima.yaml":
                    return iter([folder / "colima.yaml"])
                return original_glob(path, pattern)
            with patch("local_service.container_startup.docker_desktop_installed", return_value=False), \
                    patch("local_service.container_startup.subprocess.run", side_effect=run), \
                    patch("local_service.container_startup.Path.resolve", resolve), \
                    patch("local_service.container_startup.Path.glob", glob), \
                    patch.dict(os.environ, unselected, clear=True):
                recovered = managed_colima_environment(unselected)
                assert recovered["DOCKER_HOST"] == env["DOCKER_HOST"]
                startup = DockerStartup(stage / "recovered-startup.log")
                startup.run()
                assert startup.status["state"] == "ready", startup.status
            assert subprocess.check_output(["docker", "context", "show"], env=unselected, text=True).strip() == "default"
            print("REAL STARTUP RECOVERY PASSED: stopped VM restarted via recovered socket, default context unchanged", flush=True)
            assert not colima_sharing.probe("docker", image, app, env), "Test must reproduce the missing mount"
            print("REPRODUCED: existing VM cannot read app outside shared folders", flush=True)
            original = yaml.safe_load((folder / "colima.yaml").read_text())
            busy = subprocess.check_output(["docker", "run", "--detach", "--network=none", "--entrypoint", "sleep", image, "120"], env=env, text=True).strip()
            try:
                try:
                    colima_sharing.repair("docker", image, app, env)
                except colima_sharing.SharingError as exc:
                    assert "Other containers are running" in str(exc), str(exc)
                else:
                    raise AssertionError("Repair did not refuse active workload")
                assert subprocess.check_output(["docker", "inspect", "--format", "{{.State.Running}}", busy], env=env, text=True).strip() == "true"
                print("BUSY GUARD PASSED: unrelated test container still running", flush=True)
            finally:
                subprocess.run(["docker", "rm", "--force", busy], env=env, check=True)
            assert colima_sharing.repair("docker", image, app, env)
            updated = yaml.safe_load((folder / "colima.yaml").read_text())
            assert updated.pop("mounts") == original.pop("mounts") + [{"location": str(app), "writable": False}]
            assert updated == original
            assert not colima_sharing.repair("docker", image, app, env)
            print("REAL COLIMA REPAIR PASSED: mount fixed, settings preserved, second call reuses without restart", flush=True)
        finally:
            subprocess.run(["colima", "delete", "--force", "--profile", profile], env=env, check=True, timeout=120)
            assert subprocess.check_output(["docker", "context", "show"], text=True).strip() == context_before
            print("CLEANUP PASSED: only test VM deleted; original Docker context unchanged", flush=True)


if __name__ == "__main__":
    main()
