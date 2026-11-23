"""Run real AppKit Quit and supervisor scenarios using isolated temporary data."""
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile

import yaml

root = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix="guide-native-quit-") as temporary:
    temporary = Path(temporary)
    binary = temporary / "native-quit-smoke"
    subprocess.run(["xcrun", "clang", "-fobjc-arc", "-Wall", "-Wextra", "-Werror",
                    "-framework", "Cocoa", str(root / "test/native_quit_smoke.m"), "-o", str(binary)], check=True)
    for mode in (sys.argv[1:] or ("normal", "gap", "race", "restart", "restart-gap")):
        work = temporary / mode
        work.mkdir()
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        env = dict(os.environ, QUIT_TEST_DIR=str(work), QUIT_TEST_ROOT=str(root),
                   QUIT_TEST_PYTHON=sys.executable, QUIT_TEST_PORT=str(port),
                   PYTHONPATH=str(Path(yaml.__file__).resolve().parents[1]))
        process = subprocess.Popen([str(binary), mode], env=env, start_new_session=True)
        try:
            code = process.wait(timeout=40)
            if code:
                raise RuntimeError(f"{mode} native Quit failed: {code}")
        finally:
            # NSTask creates a separate process group; ask only this test's
            # recorded supervisor to clean up its own child if still alive.
            pid_file = work / "worker.pid"
            if pid_file.exists():
                try:
                    pid = int(pid_file.read_text())
                    os.kill(pid, signal.SIGCONT)
                    os.kill(pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            # Clean up only this harness's process group, including on timeout.
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            if process.poll() is None:
                process.wait(timeout=10)
        print(f"{mode}: passed", flush=True)
