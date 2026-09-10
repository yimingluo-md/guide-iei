"""Exercise an actual built app with isolated storage and no host Python/Node.

python3 test/test_self_contained_macos.py /path/to/preview.app
"""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request


def main():
    app = Path(sys.argv[1]).resolve()
    python = app / "Contents/Frameworks/Python.framework/Versions/Current/bin/python3"
    root = app / "Contents/Resources/application"
    subprocess.run(["codesign", "--verify", "--deep", "--strict", str(app)], check=True)
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
        }
        log = (state / "service.log").open("w+")
        child = subprocess.Popen([str(python), "-s", "-B", "-m", "local_service.desktop_app"], cwd=root, env=env, stdout=log, stderr=log)
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
            assert not capabilities["container_runtimes"]
            assert capabilities["defaults"]["output_directory"].startswith(str(state))
            get("/api/gene-knowledge/status")
            get("/api/gene-knowledge/filters")
            assert not (root / "references").exists()
            assert not (root / "results").exists()
            if os.environ.get("IEI_TEST_PACKAGED_BROWSER") == "1":
                browser_env = dict(os.environ, IEI_PACKAGED_TEST_URL=base)
                subprocess.run(["node", "tests/macos-app.browser.mjs"], cwd=Path(__file__).resolve().parents[1] / "webui", env=browser_env, check=True)
            request = urllib.request.Request(base + "/api/software-update/install", data=b"{}", headers={"Content-Type": "application/json"})
            try:
                urllib.request.urlopen(request, timeout=10)
                raise AssertionError("Packaged app accepted a source-code update")
            except urllib.error.HTTPError as error:
                assert error.code == 400
                assert b"self-contained Mac app" in error.read()
            print("PACKAGED APP SMOKE PASSED: isolated storage, bundled runtime, static UI, API, reference data, update guard")
        except BaseException:
            log.flush()
            print((state / "service.log").read_text(), file=sys.stderr)
            raise
        finally:
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
