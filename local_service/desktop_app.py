"""Supervise the bundled, offline-capable workbench without Terminal/Node."""
import json
import errno
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
import urllib.request

from local_service.container_startup import mac_tool_path


EXISTING_BUILD = 42
OTHER_BUILD = 43
OCCUPIED_PORT = 44


def existing_instance(url, root):
    """Identify without modifying or signalling the process on this port."""
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    def read(route):
        try:
            with opener.open(url + route, timeout=3) as response:
                # A local HTTP redirect is not an instance-identification reply.
                if response.geturl() != url + route:
                    return {}
                data = json.loads(response.read(256 * 1024))
                return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}
    status = read("/api/service/status")
    if status.get("service") == "GUIDE-IEI":
        try:
            build = json.loads((root / "desktop-build.json").read_text())
        except (OSError, ValueError):
            build = {}
        if isinstance(build, dict) and build.get("build_id") and status.get("build_id") == build["build_id"]:
            return EXISTING_BUILD
        return OTHER_BUILD
    # Older standalone builds predate the lifecycle endpoint. Recognize their
    # API identity, but never treat their matching version number as proof
    # that they contain the same code.
    if read("/api/capabilities").get("service") == "IEI Variant Review local service":
        return OTHER_BUILD
    return OCCUPIED_PORT


def instance_on_port(root, port):
    """Match HTTPServer's bind policy, including recently closed connections.

    Without SO_REUSEADDR, a clean shutdown's TIME_WAIT sockets can make this
    probe fail even though HTTPServer could restart and no process is listening.
    This does not permit binding over a live listener (no SO_REUSEPORT).
    """
    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", port))
        except OSError as exc:
            if exc.errno != errno.EADDRINUSE:
                raise
            return existing_instance(f"http://127.0.0.1:{port}", root)
    return None


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    support = Path(os.environ.get("IEI_APP_SUPPORT_DIR", str(Path.home() / "Library/Application Support/GUIDE-IEI")))
    support.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.umask(0o077)
    port = int(os.environ.get("IEI_UI_PORT", "3000"))
    url = f"http://127.0.0.1:{port}"
    # Reopen only an identified instance of this exact packaged build.
    result = instance_on_port(root, port)
    if result is not None:
        if result == EXISTING_BUILD and os.environ.get("IEI_DESKTOP_NO_BROWSER") != "1":
            subprocess.run(["/usr/bin/open", url], check=True)
        print(f"Another process is using port {port}; startup result {result}. No process was stopped.", flush=True)
        return result
    env = dict(os.environ)
    env.update(IEI_DESKTOP_APP="1", PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1")
    env.setdefault("IEI_DEFAULT_ANNOTATION_ROOT", str(support / "references"))
    # Existing Storage selections and patient-library paths remain authoritative.
    tools = Path(env.get("IEI_TOOLS_DIR", str(Path.home() / ".iei-variant-review/tools")))
    env["PATH"] = mac_tool_path(f"{Path(sys.executable).parent}:{tools / 'bin'}:/usr/bin:/bin:/usr/sbin:/sbin")
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
