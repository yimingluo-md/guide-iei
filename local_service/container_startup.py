"""Start an existing, selected local Mac Docker engine; never install or switch it."""
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import threading
import time


def mac_tool_path(path=None):
    """Finder has a minimal PATH; preserve its order and append known CLI locations."""
    if os.environ.get("IEI_MAC_TOOL_DISCOVERY") == "0":
        return path if path is not None else os.environ.get("PATH", "")
    home = Path.home()
    tools = Path(os.environ.get("IEI_TOOLS_DIR", str(home / ".iei-variant-review/tools")))
    entries = (path if path is not None else os.environ.get("PATH", "")).split(os.pathsep)
    entries += [str(tools / "bin"), str(home / ".docker/bin"), "/usr/local/bin",
                "/opt/homebrew/bin", "/Applications/Docker.app/Contents/Resources/bin"]
    return os.pathsep.join(dict.fromkeys(entry for entry in entries if entry))


def selected_start_command(docker, env):
    """Only recognized local socket endpoints qualify; remote/custom engines don't."""
    context = env.get("DOCKER_CONTEXT", "")
    endpoint = env.get("DOCKER_HOST", "") if not context else ""
    if not endpoint:
        if not context:
            result = subprocess.run([docker, "context", "show"], env=env,
                                    capture_output=True, text=True, timeout=5, check=True)
            context = result.stdout.strip()
        result = subprocess.run([docker, "context", "inspect", context], env=env,
                                capture_output=True, text=True, timeout=5, check=True)
        endpoint = json.loads(result.stdout)[0]["Endpoints"]["docker"]["Host"]
    if not endpoint.startswith("unix://"):
        return None
    socket_path = Path(endpoint[len("unix://"):]).expanduser()
    resolved = socket_path.resolve()
    home = Path.home()
    colima_home = Path(env.get("COLIMA_HOME", str(home / ".colima"))).expanduser().resolve()
    if resolved.parent.parent == colima_home and resolved.name == "docker.sock":
        profile = resolved.parent.name
        colima = shutil.which("colima", path=env["PATH"])
        # Never create a fresh VM or change its CPU/memory/mount settings here.
        if (colima and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", profile)
                and (colima_home / profile / "colima.yaml").is_file()):
            return "Colima", [colima, "start", "--profile", profile,
                              "--activate=false", "--save-config=false"]
        return None
    desktop_sockets = {(home / ".docker/run/docker.sock").resolve(),
                       (home / "Library/Containers/com.docker.docker/Data/docker.sock").resolve()}
    # /var/run/docker.sock is the Docker Desktop default only when it does
    # not resolve to an unrelated provider's socket.
    if resolved in desktop_sockets or resolved == Path("/var/run/docker.sock").absolute() or resolved == Path("/private/var/run/docker.sock"):
        for app in (home / "Applications/Docker.app", Path("/Applications/Docker.app")):
            if app.is_dir():
                return "Docker Desktop", ["/usr/bin/open", "-g", str(app)]
    return None


class DockerStartup:
    """One bounded background attempt per service launch; GET endpoints only read state."""
    def __init__(self, log_path):
        self.log_path = Path(log_path)
        self.status = {"state": "idle", "message": ""}
        self._cancel = threading.Event()
        self._thread = None

    def start(self, runtime="docker"):
        if (self._thread is not None or runtime != "docker" or platform.system() != "Darwin"
                or os.environ.get("IEI_AUTO_START_DOCKER", "1") == "0"):
            return
        self.status = {"state": "starting", "message": "Checking and starting the local Docker engine… Review remains available."}
        self._thread = threading.Thread(target=self.run, name="docker-startup", daemon=True)
        self._thread.start()

    def stop(self):
        self._cancel.set()
        if self._thread is not None:
            self._thread.join(timeout=7)

    def _set(self, state, message):
        self.status = {"state": state, "message": message}

    def _ready(self, docker, env):
        try:
            return subprocess.run([docker, "info"], env=env, stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL, timeout=5).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return False

    def _launch(self, command, env, log):
        child = subprocess.Popen(command, env=env, stdout=log, stderr=log)
        deadline = time.monotonic() + 120
        try:
            while child.poll() is None:
                if self._cancel.wait(.25) or time.monotonic() >= deadline:
                    return False
            return child.returncode == 0
        finally:
            if child.poll() is None:
                # Stop our startup client, not the user's Docker daemon/VM.
                child.terminate()
                try:
                    child.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()

    def run(self):
        env = dict(os.environ, PATH=mac_tool_path())
        docker = shutil.which("docker", path=env["PATH"])
        try:
            if not docker or self._cancel.is_set():
                self._set("idle", "")
                return
            if self._ready(docker, env):
                self._set("ready", "Docker is running.")
                return
            selected = selected_start_command(docker, env)
            if selected is None:
                self._set("unavailable", "Docker could not be started automatically. Start the engine selected in your Docker context, or use Set up annotation engine under Import & QC → Set up annotation datasets.")
                return
            label, command = selected
            self._set("starting", f"Starting {label}… Review remains available.")
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(self.log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(fd, "w") as log:
                log.write(f"Starting {label}\n")
                log.flush()
                if not self._launch(command, env, log):
                    raise RuntimeError("startup command did not complete successfully")
                deadline = time.monotonic() + 120
                while not self._cancel.is_set():
                    if self._ready(docker, env):
                        self._set("ready", f"{label} is running.")
                        return
                    if time.monotonic() >= deadline:
                        break
                    self._cancel.wait(2)
                raise RuntimeError("engine did not become ready")
        except (OSError, ValueError, KeyError, IndexError, subprocess.SubprocessError, RuntimeError):
            # Do not echo Docker endpoints or command output into the UI;
            # a private log contains the startup client's diagnostic output.
            self._set("failed", f"Docker could not be started automatically. Open Docker Desktop or start your selected Colima profile, then refresh. Startup log: {self.log_path}")


if __name__ == "__main__":
    startup = DockerStartup(Path.home() / ".iei-variant-review/logs/docker-startup.log")
    if platform.system() != "Darwin":
        raise SystemExit("Automatic engine startup is supported only on macOS.")
    startup.run()
    print(startup.status["message"])
    raise SystemExit(0 if startup.status["state"] == "ready" else 1)
