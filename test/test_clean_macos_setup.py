#!/usr/bin/env python3
"""Opt-in packaged one-click setup with empty storage and a separate Colima VM.

Downloads real public references. Does not delete existing installations, change
the host Docker context, or reuse an installed annotation directory. Retains
test data/logs for diagnosis, stops its own VM on completion. This is not a fresh
macOS account or Gatekeeper test. Run directly, never during the unit suite.
"""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("app", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    app = args.app.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    vm = Path(tempfile.mkdtemp(prefix="giei-clean-", dir="/private/tmp"))
    # Colima only honors COLIMA_HOME when that directory already exists.
    (vm / "colima").mkdir()
    support = output / "Application Support"
    tools = output / "tools"
    for folder in (output / "tmp", output / "docker", output / "cache"):
        folder.mkdir()
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    # Do not inherit developer PATH, Docker context, credentials or data roots.
    # HOME is not changed: the application has explicit storage overrides.
    env = {key: os.environ[key] for key in ("HOME", "USER", "LOGNAME", "LANG", "LC_CTYPE") if key in os.environ}
    env.update(PATH="/usr/bin:/bin:/usr/sbin:/sbin", TMPDIR=str(output / "tmp"),
        IEI_UI_PORT=str(port), IEI_APP_SUPPORT_DIR=str(support),
        IEI_WORKBENCH_STATE_DIR=str(output / "library"),
        IEI_WORKBENCH_STORAGE_REGISTRY=str(output / "storage.json"),
        IEI_DEFAULT_ANNOTATION_ROOT=str(output / "annotation data"),
        IEI_TOOLS_DIR=str(tools), IEI_MAC_TOOL_DISCOVERY="0",
        IEI_DESKTOP_NO_BROWSER="1", IEI_AUTO_START_DOCKER="0",
        IEI_DESKTOP_SETUP="1", PYTHONDONTWRITEBYTECODE="1",
        COLIMA_HOME=str(vm / "colima"), LIMA_HOME=str(vm / "lima"),
        DOCKER_CONFIG=str(output / "docker"),
        DOCKER_HOST="unix://" + str(vm / "colima/default/docker.sock"),
        XDG_CACHE_HOME=str(output / "cache"), HF_HOME=str(output / "cache/huggingface"))
    context = {"app": str(app), "output": str(output), "vm": str(vm), "port": port, "environment": env}
    # PATH alone is insufficient: the installer deliberately searches common
    # Mac runtime locations. Hide those existing installations from this test
    # process only; do not move or uninstall the user's working tools/data.
    hidden = [Path("/Applications/Docker.app"), Path("/opt/homebrew"), Path("/usr/local/bin"),
              Path.home() / ".docker",
              Path.home() / ".iei-variant-review", Path.home() / ".iei-variant-review-bootstrap.json",
              Path.home() / "Applications/Docker.app", Path(__file__).resolve().parents[1] / "references"]
    sandbox = output / "isolation.sb"
    sandbox.write_text("(version 1)\n(allow default)\n" + "\n".join(
        "(deny file-read* file-write* (subpath " + json.dumps(str(path)) + "))" for path in hidden) + "\n" +
        "\n".join("(deny file-read-data file-write* (subpath " + json.dumps(str(Path.home() / name)) + "))"
                  for name in (".colima", ".lima")) + "\n")
    context["sandbox"] = str(sandbox)
    (output / "test-context.json").write_text(json.dumps(context, indent=2))
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def api(route, body=None):
        request = urllib.request.Request(f"http://127.0.0.1:{port}" + route,
            data=None if body is None else json.dumps(body).encode(), headers={"Content-Type": "application/json"})
        try:
            with opener.open(request, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            raise RuntimeError(error.read().decode()) from error
    report = {"status": "RUNNING", "started": time.time(), "checks": [], "limitations": [
        "Existing macOS account, network and OS tools; not a Gatekeeper/notarization test",
        "Existing Docker Desktop retained but hidden from test processes; separate VM/socket",
        "Production Colima home mount remains; annotation paths use only fresh test storage"]}
    def record():
        (output / "validation.json").write_text(json.dumps(report, indent=2) + "\n")
    record()
    child = None
    log = (output / "launcher.log").open("w")
    try:
        subprocess.run(["codesign", "--verify", "--deep", "--strict", str(app)], check=True)
        child = subprocess.Popen(["/usr/bin/sandbox-exec", "-f", str(sandbox),
                                  str(app / "Contents/MacOS/GUIDE-IEI")], env=env, stdout=log, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + 180
        while True:
            assert child.poll() is None, "Native launcher exited"
            try:
                initial = api("/api/essential-setup/status")
                states = list((support / "logs").glob("startup-*.json"))
                startup = json.loads(states[0].read_text()) if states else {}
                if startup.get("phase") == "waiting" and startup.get("can_prepare"):
                    break
            except (OSError, ValueError):
                pass
            assert time.monotonic() < deadline, "Startup did not offer Prepare"
            time.sleep(1)
        assert not initial["available"] and not initial["legacy_ready"]
        assert len(initial["missing"]) == 6, initial
        assert len(initial["package"]["missing"]) == 3
        assert not tools.exists(), "Host tools leaked into empty setup"
        assert not api("/api/capabilities")["container_runtimes"]
        assert not api("/api/resource-downloads")["jobs"]
        report["initial"] = initial
        report["checks"].append("Empty resources, tools and VM; no automatic downloads before Prepare")
        record()
        # Same command channel used by the native Prepare button.
        control = states[0].with_name(states[0].stem + ".control.json")
        control.write_text(json.dumps({"action": "prepare"}))
        print("PREPARE: fresh tools + VM + engine + all essential datasets", flush=True)
        deadline = time.monotonic() + 6 * 60 * 60
        last = None
        while time.monotonic() < deadline:
            assert child.poll() is None, "Native launcher exited during setup"
            jobs = api("/api/resource-downloads")["jobs"]
            if jobs:
                job = jobs[0]
                report["job"] = {k: v for k, v in job.items() if k != "log"}
                record()
                # Keep console compact; full download progress lives in job logs.
                stage = (job["status"], str(job.get("message", "")).split(" — ")[0])
                if stage != last:
                    print(stage, flush=True)
                    last = stage
                if job["status"] in {"failed", "interrupted"}:
                    raise RuntimeError(job.get("error") or job.get("message"))
                if job["status"] == "succeeded":
                    break
            time.sleep(5)
        else:
            raise TimeoutError("Essential setup exceeded six hours")
        final = api("/api/essential-setup/status")
        assert final["available"] and not final["missing"] and final["package"]["available"], final
        report["final"] = final
        report["checks"].append("Complete packaged one-click setup with fresh downloads")
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
                for job in api("/api/resource-downloads")["jobs"]:
                    if job["status"] in {"running", "queued"}:
                        api("/api/annotation-engine/cancel", {"confirm": True, "job_id": job["id"]})
                for _ in range(120):
                    status = api("/api/service/status")
                    if not status["blockers"]:
                        api("/api/service/quit", {"confirm": True, "instance_id": status["instance_id"]})
                        break
                    time.sleep(1)
                child.wait(timeout=45)
            except (OSError, RuntimeError, subprocess.TimeoutExpired):
                child.terminate()
                child.wait(timeout=45)
        log.close()
        colima = tools / "bin/colima"
        if colima.exists():
            with (output / "vm-stop.log").open("w") as stop_log:
                subprocess.run([str(colima), "stop"], env={**env, "PATH": str(tools / "bin") + ":" + env["PATH"]},
                    stdout=stop_log, stderr=subprocess.STDOUT, timeout=120, check=False)
        print("Report: " + str(output / "validation.json"), flush=True)


if __name__ == "__main__":
    main()
