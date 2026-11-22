"""Supervise the bundled, offline-capable workbench without Terminal/Node."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import urllib.request
import uuid

from local_service.container_startup import mac_tool_path
from local_service.desktop_endpoint import port_available, read_endpoint, write_endpoint
from local_service.desktop_setup import api, prepare_engine, take_control_message, write_status


EXISTING_BUILD = 42
OTHER_BUILD = 43
OCCUPIED_PORT = 44


def existing_instance(url, root, expected_instance_id):
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
    if not expected_instance_id or status.get("instance_id") != expected_instance_id:
        return OCCUPIED_PORT
    if status.get("service") == "GUIDE-IEI":
        try:
            build = json.loads((root / "desktop-build.json").read_text())
        except (OSError, ValueError):
            build = {}
        if isinstance(build, dict) and build.get("build_id") and status.get("build_id") == build["build_id"]:
            return EXISTING_BUILD
        return OTHER_BUILD
    return OCCUPIED_PORT


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    support = Path(os.environ.get("IEI_APP_SUPPORT_DIR", str(Path.home() / "Library/Application Support/GUIDE-IEI")))
    support.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.umask(0o077)
    # Serialize native launches even when they choose different ports. The
    # service's separate state lock still protects patient storage across
    # older launchers and different support directories.
    import fcntl
    instance_id = os.environ.get("IEI_DESKTOP_INSTANCE_ID") or uuid.uuid4().hex
    endpoint = Path(os.environ.get("IEI_DESKTOP_ENDPOINT_FILE", str(support / f"endpoint-{instance_id}.json")))
    saved_path = support / "desktop-endpoint.json"
    with (support / "desktop-launch.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            # The first launch may still be opening its service. Never start
            # a second supervisor or stop a process just because a port is busy.
            for _ in range(20):
                saved = read_endpoint(saved_path)
                if saved:
                    result = reopen_endpoint(saved, root, endpoint)
                    if result != OCCUPIED_PORT:
                        return result
                time.sleep(.25)
            print("Another GUIDE-IEI launcher is still starting or stopping. No process was stopped.", flush=True)
            return OCCUPIED_PORT
        return run_workbench(root, support, endpoint, saved_path, instance_id)


def reopen_endpoint(saved, root, endpoint):
    url = f"http://127.0.0.1:{saved['port']}"
    result = existing_instance(url, root, saved["instance_id"])
    if result in (EXISTING_BUILD, OTHER_BUILD):
        write_endpoint(endpoint, saved["port"], saved["instance_id"])
        if result == EXISTING_BUILD and os.environ.get("IEI_DESKTOP_NO_BROWSER") != "1":
            subprocess.run(["/usr/bin/open", url], check=True)
    return result


def run_workbench(root, support, endpoint, saved_path, instance_id):
    saved = read_endpoint(saved_path)
    if saved:
        result = reopen_endpoint(saved, root, endpoint)
        if result in (EXISTING_BUILD, OTHER_BUILD):
            return result
    port = int(os.environ.get("IEI_UI_PORT", "3000"))
    if not 1 <= port <= 65535:
        raise ValueError("IEI_UI_PORT must be between 1 and 65535")
    url = f"http://127.0.0.1:{port}"
    if not port_available(port):
        # Only a verified private endpoint above can reopen a session. For
        # every other listener, let the child bind our fallback or a free port.
        fallback = saved["port"] if saved and saved["port"] != port else 0
        print(f"Preferred port {port} is busy; starting a separate workbench.", flush=True)
        port = fallback
    env = dict(os.environ)
    env.update(IEI_DESKTOP_APP="1", PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1")
    env["IEI_DESKTOP_ENDPOINT_FILE"] = str(endpoint)
    env["IEI_DESKTOP_INSTANCE_ID"] = instance_id
    env.setdefault("IEI_DEFAULT_ANNOTATION_ROOT", str(support / "references"))
    # Existing Storage selections and patient-library paths remain authoritative.
    tools = Path(env.get("IEI_TOOLS_DIR", str(Path.home() / ".iei-variant-review/tools")))
    env["PATH"] = mac_tool_path(f"{Path(sys.executable).parent}:{tools / 'bin'}:/usr/bin:/bin:/usr/sbin:/sbin")
    env["IEI_PYTHON_BIN"] = sys.executable
    child = None
    stopping = False
    launcher_pid = env.pop("IEI_DESKTOP_LAUNCHER_PID", None)
    control_file = env.get("IEI_DESKTOP_STARTUP_CONTROL")
    control_path = Path(control_file) if control_file else None
    status_file = env.get("IEI_DESKTOP_STARTUP_STATUS")

    def launcher_gone():
        return launcher_pid is not None and str(os.getppid()) != launcher_pid

    def stop(_signum, _frame):
        nonlocal stopping
        stopping = True
        if child is not None and child.poll() is None:
            child.terminate()

    for name in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(name, stop)
    failures = 0
    opened = False
    browser_opened = False
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        while not stopping and not launcher_gone():
            started = time.monotonic()
            endpoint.unlink(missing_ok=True)
            if status_file:
                write_status(Path(status_file), "checking", "Starting the workbench…")
            child = subprocess.Popen([
                sys.executable, "-s", "-B", "-m", "local_service.workbench_service",
                "--port", str(port), "--auto-port", "--web-root", str(root / "webui/site"),
            ], cwd=root, env=env)
            if not opened:
                while child.poll() is None and time.monotonic() - started < 120 and not stopping:
                    if launcher_gone():
                        stop(signal.SIGTERM, None)
                        break
                    try:
                        selected = read_endpoint(endpoint)
                        if not selected or selected["instance_id"] != instance_id:
                            time.sleep(.25)
                            continue
                        selected_url = f"http://127.0.0.1:{selected['port']}"
                        if selected_url != url:
                            browser_opened = False
                        port, url = selected["port"], selected_url
                        write_endpoint(saved_path, port, instance_id)
                        with opener.open(url + "/api/health", timeout=1) as response:
                            if json.load(response).get("ok"):
                                # The native window remains visible while the service
                                # prepares the engine. Browser launch is the final step.
                                if status_file and env.get("IEI_DESKTOP_SETUP", "1") != "0":
                                    prepared = prepare_engine(url, support, Path(status_file),
                                            control_path, lambda: not stopping and child.poll() is None,
                                            essential=True, launcher_gone=launcher_gone)
                                    if prepared is None and not stopping and not launcher_gone():
                                        if child.poll() == 75:
                                            break  # Repeat startup with the new storage roots.
                                        raise RuntimeError(f"Workbench service exited during preparation (exit {child.returncode}).")
                                    if not prepared:
                                        stop(signal.SIGTERM, None)
                                        break
                                elif status_file:
                                    write_status(Path(status_file), "ready", "Workbench running.")
                                opened = True
                                break
                    except (OSError, ValueError):
                        time.sleep(.25)
                if not opened and child.poll() is None and not stopping:
                    child.terminate()
                    raise RuntimeError("The workbench did not become ready. See the startup log.")
            quit_accepted = False
            next_orphan_retry = 0
            while child.poll() is None:
                if stopping:
                    try:
                        child.wait(timeout=30)
                    except subprocess.TimeoutExpired:
                        print("Service did not stop within 30 seconds; terminating the child.", flush=True)
                        child.kill()
                        child.wait()
                    break
                # The native window can still believe it is preparing after
                # prepare_engine returns. Keep its Quit channel alive here.
                message = take_control_message(control_path) if control_path else {}
                orphaned = launcher_gone()
                if not quit_accepted and (message.get("action") == "quit" or
                        (orphaned and time.monotonic() >= next_orphan_retry)):
                    try:
                        api(url, "/api/service/quit", {"confirm": True, "instance_id": instance_id})
                        quit_accepted = True
                    except (RuntimeError, OSError, ValueError) as exc:
                        # Honor service blockers such as storage migration.
                        # A surviving window can retry; without one, retry
                        # automatically once the service can safely stop.
                        next_orphan_retry = time.monotonic() + 2
                        if status_file and not orphaned:
                            write_status(Path(status_file), "ready", "Workbench running.",
                                         quit_request_id=message.get("request_id"),
                                         quit_error=str(exc) or "The workbench could not confirm shutdown.")
                elif not quit_accepted and not orphaned and not browser_opened and env.get("IEI_DESKTOP_NO_BROWSER") != "1":
                    try:
                        subprocess.run(["/usr/bin/open", url], check=True)
                    except (OSError, subprocess.CalledProcessError) as exc:
                        print(f"Could not open the browser automatically: {exc}. Use Open Workbench.", flush=True)
                    browser_opened = True
                time.sleep(.25)
            result = child.returncode
            if stopping or quit_accepted or result == 0:
                return 0
            if result == 75:
                opened = False
                continue
            if result == 4:
                return result
            failures = failures + 1 if time.monotonic() - started < 60 else 1
            if failures >= 3:
                return result or 1
            opened = False
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
