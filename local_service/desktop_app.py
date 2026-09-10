"""Supervise the bundled, offline-capable workbench without Terminal/Node."""
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
import urllib.request


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    support = Path(os.environ.get("IEI_APP_SUPPORT_DIR", str(Path.home() / "Library/Application Support/GUIDE-IEI")))
    support.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.umask(0o077)
    port = int(os.environ.get("IEI_UI_PORT", "3000"))
    url = f"http://127.0.0.1:{port}"
    # Never attach this app to an unrelated/older service on an occupied port.
    with socket.socket() as probe:
        try:
            probe.bind(("127.0.0.1", port))
        except OSError as exc:
            raise RuntimeError(f"Port {port} is occupied. Quit the other workbench before opening this app.") from exc
    env = dict(os.environ)
    env.update(IEI_DESKTOP_APP="1", PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1")
    env.setdefault("IEI_DEFAULT_ANNOTATION_ROOT", str(support / "references"))
    # Existing Storage selections and patient-library paths remain authoritative.
    tools = Path(env.get("IEI_TOOLS_DIR", str(Path.home() / ".iei-variant-review/tools")))
    env["PATH"] = f"{Path(sys.executable).parent}:{tools / 'bin'}:/usr/bin:/bin:/usr/sbin:/sbin"
    env["IEI_PYTHON_BIN"] = sys.executable
    child = None
    stopping = False

    def stop(_signum, _frame):
        nonlocal stopping
        stopping = True
        if child is not None and child.poll() is None:
            child.terminate()

    for name in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(name, stop)
    failures = 0
    opened = False
    try:
        while not stopping:
            started = time.monotonic()
            child = subprocess.Popen([
                sys.executable, "-s", "-B", "-m", "local_service.workbench_service",
                "--port", str(port), "--web-root", str(root / "webui/site"),
            ], cwd=root, env=env)
            if not opened:
                while child.poll() is None and time.monotonic() - started < 120 and not stopping:
                    try:
                        with urllib.request.urlopen(url + "/api/health", timeout=1) as response:
                            if json.load(response).get("ok"):
                                if env.get("IEI_DESKTOP_NO_BROWSER") != "1":
                                    subprocess.run(["/usr/bin/open", url], check=True)
                                opened = True
                                break
                    except (OSError, ValueError):
                        time.sleep(.25)
                if not opened and child.poll() is None:
                    child.terminate()
                    raise RuntimeError("The workbench did not become ready. See the startup log.")
            while child.poll() is None:
                if stopping:
                    try:
                        child.wait(timeout=30)
                    except subprocess.TimeoutExpired:
                        print("Service did not stop within 30 seconds; terminating the child.", flush=True)
                        child.kill()
                        child.wait()
                    break
                time.sleep(.25)
            result = child.returncode
            if stopping or result == 0:
                return 0
            if result == 75:
                continue
            if result == 4:
                return result
            failures = failures + 1 if time.monotonic() - started < 60 else 1
            if failures >= 3:
                return result or 1
            time.sleep(2)
    finally:
        if child is not None and child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=30)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
