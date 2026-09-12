"""Exercise an actual built app with isolated storage and no host Python/Node.

python3 test/test_self_contained_macos.py /path/to/preview.app
"""
import json
import os
import plistlib
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile


def main():
    app = Path(sys.argv[1]).resolve()
    python = app / "Contents/Frameworks/Python.framework/Versions/Current/bin/python3"
    root = app / "Contents/Resources/application"
    existing_engine = os.environ.get("IEI_TEST_EXISTING_ENGINE") == "1"
    if existing_engine:
        # Refuse to run the automatic path unless the existing image matches;
        # this test must not install tools or rebuild a user's container.
        expected = subprocess.check_output(["bash", str(root / "docker/image_fingerprint.sh")], text=True).strip()
        actual = subprocess.check_output(["docker", "image", "inspect", "--format",
            '{{ index .Config.Labels "org.guide-iei.source-fingerprint" }}', "vep-annotate:latest"], text=True).strip()
        assert actual == expected, "Existing engine test needs a matching running Docker image"
    with (app / "Contents/Info.plist").open("rb") as handle:
        assert plistlib.load(handle)["CFBundleIdentifier"] == "org.guide-iei.desktop"
    subprocess.run(["codesign", "--verify", "--deep", "--strict", str(app)], check=True)
    archive = app.with_suffix(".zip")
    if archive.is_file():
        with zipfile.ZipFile(archive) as bundle:
            assert "GUIDE-IEI.app/Contents/Info.plist" in bundle.namelist(), "ZIP must install GUIDE-IEI.app, not an architecture/version-named app"
    with tempfile.TemporaryDirectory(prefix="guide-iei-app-smoke-") as temporary:
        state = Path(temporary).resolve()
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        env = {
            "HOME": str(state), "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
            "TMPDIR": str(state), "IEI_UI_PORT": str(port),
            "IEI_APP_SUPPORT_DIR": str(state / "Application Support"),
            "IEI_WORKBENCH_STATE_DIR": str(state / "patient-library"),
            "IEI_WORKBENCH_STORAGE_REGISTRY": str(state / "storage.json"),
            "IEI_TOOLS_DIR": str(state / "empty-tools"),
            "IEI_DESKTOP_NO_BROWSER": "1", "PYTHONDONTWRITEBYTECODE": "1",
            "IEI_AUTO_START_DOCKER": "0", "IEI_MAC_TOOL_DISCOVERY": "0",
            "IEI_DESKTOP_SETUP": "0",  # Offline review smoke; setup has separate tests.
        }
        if existing_engine:
            env.update(IEI_DESKTOP_SETUP="1", IEI_MAC_TOOL_DISCOVERY="1", PATH=os.environ["PATH"])
            env["DOCKER_CONFIG"] = os.environ.get("DOCKER_CONFIG", str(Path.home() / ".docker"))
            for name in ("DOCKER_HOST", "DOCKER_CONTEXT"):
                if name in os.environ:
                    env[name] = os.environ[name]
        log = (state / "service.log").open("w+")
        command = ([str(app / "Contents/MacOS/GUIDE-IEI")]
                   if os.environ.get("IEI_TEST_NATIVE_APP") == "1"
                   else [str(python), "-s", "-B", "-m", "local_service.desktop_app"])
        child = subprocess.Popen(command, cwd=root, env=env, stdout=log, stderr=log)
        base = f"http://127.0.0.1:{port}"

        def get(path):
            with urllib.request.urlopen(base + path, timeout=10) as response:
                return json.load(response)

        try:
            for _ in range(120):
                if child.poll() is not None:
                    raise AssertionError("Bundled service exited during launch")
                try:
                    if get("/api/health")["ok"]:
                        break
                except OSError:
                    time.sleep(.25)
            else:
                raise AssertionError("Bundled service failed to start")
            with urllib.request.urlopen(base, timeout=10) as response:
                assert b"GUIDE-IEI" in response.read()
            manifest = get("/bundled-data/manifest.json")
            assert manifest["haploinsufficiency"]["genes"] == 48
            assert get("/api/software-update/status")["desktop_app"]
            capabilities = get("/api/capabilities")
            if existing_engine:
                assert capabilities["container_runtimes"]
                for _ in range(300):
                    states = list((state / "Application Support/logs").glob("startup-*.json"))
                    if states and json.loads(states[0].read_text()).get("phase") == "ready":
                        break
                    time.sleep(.1)
                else:
                    raise AssertionError("Native automatic startup did not reuse the ready engine: " +
                                         repr([path.read_text() for path in states]))
                jobs = get("/api/resource-downloads")["jobs"]
                assert len(jobs) == 1 and jobs[0]["resource_id"] == "annotation_engine" and jobs[0]["status"] == "succeeded", jobs
                assert "matches this GUIDE-IEI version" in jobs[0]["log"], jobs
                assert "Container workspace verified" in jobs[0]["log"], jobs
                for forbidden in ("=== Installing bundled annotation engine ===", "Downloading and building"):
                    assert forbidden not in jobs[0]["log"], jobs
                print("AUTOMATIC STARTUP PASSED: matching engine reused; new workspace verified without image installation")
            else:
                assert not capabilities["container_runtimes"]
            assert capabilities["defaults"]["output_directory"].startswith(str(state))
            get("/api/gene-knowledge/status")
            get("/api/gene-knowledge/filters")
            assert not (root / "references").exists()
            assert not (root / "results").exists()
            status = get("/api/service/status")
            assert status["build_id"]
            duplicate = subprocess.run([str(python), "-s", "-B", "-m", "local_service.desktop_app"],
                cwd=root, env=env, capture_output=True, text=True, timeout=15)
            assert duplicate.returncode == 42, duplicate.stdout + duplicate.stderr
            assert child.poll() is None
            assert get("/api/service/status")["instance_id"] == status["instance_id"]
            if os.environ.get("IEI_TEST_PACKAGED_BROWSER") == "1":
                demo = state / "synthetic-demo.vcf"
                subprocess.run([str(python), "-B", str(root / "scripts/make_demo_vcf.py"), str(demo)], check=True)
                browser_env = dict(os.environ, IEI_PACKAGED_TEST_URL=base, IEI_PACKAGED_TEST_VCF=str(demo))
                subprocess.run(["node", "tests/macos-app.browser.mjs"], cwd=Path(__file__).resolve().parents[1] / "webui", env=browser_env, check=True)
            request = urllib.request.Request(base + "/api/software-update/install", data=b"{}", headers={"Content-Type": "application/json"})
            try:
                urllib.request.urlopen(request, timeout=10)
                raise AssertionError("Packaged app accepted a source-code update")
            except urllib.error.HTTPError as error:
                assert error.code == 400
                assert b"self-contained Mac app" in error.read()
            request = urllib.request.Request(base + "/api/service/quit",
                data=json.dumps({"confirm": True, "instance_id": status["instance_id"]}).encode(),
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(request, timeout=10) as response:
                assert response.status == 202
                assert json.load(response)["quitting"]
            assert child.wait(timeout=40) == 0
            try:
                get("/api/health")
                raise AssertionError("Quit left the service listening")
            except OSError:
                pass
            # Reopen immediately on the SAME port/state, while the server's
            # recent connections can still be in TIME_WAIT. A fresh free-port
            # smoke alone cannot catch this user-visible lifecycle regression.
            child = subprocess.Popen(command,
                                     cwd=root, env=env, stdout=log, stderr=log)
            for _ in range(120):
                if child.poll() is not None:
                    raise AssertionError("Immediate reopen exited instead of restarting")
                try:
                    reopened = get("/api/service/status")
                    break
                except OSError:
                    time.sleep(.25)
            else:
                raise AssertionError("Immediate reopen did not become ready")
            assert reopened["instance_id"] != status["instance_id"]
            assert reopened["build_id"] == status["build_id"]
            if existing_engine:
                for _ in range(200):
                    if get("/api/annotation-engine/status").get("available"):
                        break
                    time.sleep(.1)
                else:
                    raise AssertionError("Reopened engine did not reuse the prepared workspace")
                # Resource jobs aren't persisted between service instances.
                assert not get("/api/resource-downloads")["jobs"], "Reopen unnecessarily ran setup again"
            request = urllib.request.Request(base + "/api/service/quit",
                data=json.dumps({"confirm": True, "instance_id": reopened["instance_id"]}).encode(),
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(request, timeout=10) as response:
                assert response.status == 202
            assert child.wait(timeout=40) == 0
            print("IMMEDIATE REOPEN PASSED: same port and state, new service instance, second clean Quit")
            print("PACKAGED APP SMOKE PASSED: isolated storage, bundled runtime, static UI, API, reference data, update guard, duplicate launch, graceful Quit")
        except BaseException:
            log.flush()
            print((state / "service.log").read_text(), file=sys.stderr)
            for path in (state / "Application Support/logs").glob("desktop-*.log"):
                print(path.read_text()[-8000:], file=sys.stderr)
            raise
        finally:
            # In native mode, terminating only the Cocoa parent would orphan
            # its supervisor. Always ask the isolated service to quit first,
            # including when a browser assertion fails.
            try:
                remaining = get("/api/service/status")
                request = urllib.request.Request(base + "/api/service/quit",
                    data=json.dumps({"confirm": True, "instance_id": remaining["instance_id"]}).encode(),
                    headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(request, timeout=5):
                    pass
                child.wait(timeout=40)
            except (OSError, KeyError, subprocess.TimeoutExpired):
                pass
            if child.poll() is None:
                child.terminate()
            try:
                child.wait(timeout=40)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
                raise AssertionError("Packaged supervisor did not shut down")
            log.close()
        subprocess.run(["codesign", "--verify", "--deep", "--strict", str(app)], check=True)
        print("Signature remains valid after launch and shutdown")


if __name__ == "__main__":
    if len(sys.argv) == 1:
        print("SKIP: packaged-app integration test requires the path to a built .app")
    else:
        main()
