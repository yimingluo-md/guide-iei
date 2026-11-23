import json
import errno
import io
import os
import socket
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from local_service.software_update import SoftwareUpdater
from local_service.static_site import serve_static
from local_service.storage_locations import StorageLocationRegistry
from local_service.workbench_service import create_server
from local_service.desktop_app import port_available, OCCUPIED_PORT
from local_service import desktop_app, desktop_endpoint
from local_service.desktop_endpoint import read_endpoint, write_endpoint


class DesktopAppTests(unittest.TestCase):
    def test_lost_launcher_keeps_service_and_first_launch_alive_until_browser_quit(self):
        import yaml
        # No real preparation or downloads: simulate an installation waiting
        # for authorization while exercising the real supervisor and service.
        boot = '''
from functools import partial
from local_service import desktop_app
desktop_app.prepare_engine = partial(desktop_app.prepare_engine, call=lambda *_: {
    "available": False, "busy": False, "package": {"installable": True},
    "annotation_path": "/synthetic/references", "free_bytes": 1000,
    "estimated_required_bytes": 500})
raise SystemExit(desktop_app.main())
'''
        for setup in ("0", "1"):
            with self.subTest(setup=setup), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                env = dict(os.environ, HOME=tmp, IEI_APP_SUPPORT_DIR=tmp,
                    IEI_WORKBENCH_STATE_DIR=str(root / "data"),
                    IEI_WORKBENCH_STORAGE_REGISTRY=str(root / "storage.json"),
                    IEI_DEFAULT_ANNOTATION_ROOT=str(root / "refs"), IEI_UI_PORT="3000",
                    PYTHONPATH=str(Path(yaml.__file__).resolve().parents[1]),
                    IEI_DESKTOP_LAUNCHER_PID="0", IEI_DESKTOP_INSTANCE_ID="lost-window",
                    IEI_DESKTOP_SETUP=setup, IEI_AUTO_START_DOCKER="0", IEI_MAC_TOOL_DISCOVERY="0",
                    IEI_DESKTOP_STARTUP_STATUS=str(root / "status.json"),
                    IEI_DESKTOP_STARTUP_CONTROL=str(root / "control.json"),
                    IEI_DESKTOP_ENDPOINT_FILE=str(root / "endpoint.json"), IEI_DESKTOP_NO_BROWSER="1")
                args = ["-c", boot] if setup == "1" else ["-m", "local_service.desktop_app"]
                with subprocess.Popen([sys.executable, "-B", *args], cwd=Path(__file__).resolve().parents[1], env=env) as supervisor:
                    try:
                        deadline = time.monotonic() + 30
                        status = {}
                        while time.monotonic() < deadline:
                            self.assertIsNone(supervisor.poll())
                            try:
                                status = json.loads((root / "status.json").read_text())
                                saved = read_endpoint(root / "desktop-endpoint.json")
                                if saved and status.get("phase") == ("waiting" if setup == "1" else "ready"):
                                    break
                            except (OSError, ValueError):
                                pass
                            time.sleep(.1)
                        else:
                            self.fail(f"Service did not become ready: {status}")
                        time.sleep(2.5)  # Exceeds the old orphan Quit retry interval.
                        self.assertIsNone(supervisor.poll())
                        base = f"http://127.0.0.1:{saved['port']}"
                        self.assertEqual(desktop_app.api(base, "/api/service/status")["instance_id"], "lost-window")
                        duplicate_env = dict(env, IEI_DESKTOP_INSTANCE_ID="new-window",
                                             IEI_DESKTOP_ENDPOINT_FILE=str(root / "reopened.json"))
                        duplicate = subprocess.run([sys.executable, "-B", "-m", "local_service.desktop_app"],
                            cwd=Path(__file__).resolve().parents[1], env=duplicate_env, timeout=15)
                        # Source checkouts lack the packaged build identity and
                        # deliberately take the safe older-build recovery path.
                        self.assertEqual(duplicate.returncode, desktop_app.OTHER_BUILD)
                        self.assertEqual(read_endpoint(root / "reopened.json"), saved)
                        desktop_app.api(base, "/api/service/quit", {"confirm": True, "instance_id": "lost-window"})
                        self.assertEqual(supervisor.wait(timeout=15), 0)
                    finally:
                        if supervisor.poll() is None:
                            supervisor.terminate()
                            supervisor.wait(timeout=15)

    def test_late_setup_quit_and_offline_storage_restart_allow_reopen(self):
        import yaml
        # Use the real lifecycle endpoint and child processes. Stub only
        # dataset readiness so this never installs tools or downloads files.
        boot = """
from functools import partial
from local_service import desktop_app
desktop_app.prepare_engine = partial(desktop_app.prepare_engine, call=lambda *_: {"available": True})
raise SystemExit(desktop_app.main())
"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            status, control = root / "status.json", root / "action.json"
            blocker = root / "setup-active"
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                preferred_port = probe.getsockname()[1]
            env = dict(os.environ, HOME=tmp, IEI_APP_SUPPORT_DIR=tmp, IEI_WORKBENCH_STATE_DIR=str(root / "data"),
                       # The child uses -s; clean-clone CI installs PyYAML in the test user's site.
                       PYTHONPATH=str(Path(yaml.__file__).resolve().parents[1]),
                       IEI_WORKBENCH_STORAGE_REGISTRY=str(root / "storage.json"), IEI_DEFAULT_ANNOTATION_ROOT=str(root / "refs"),
                       IEI_UI_PORT=str(preferred_port), IEI_DESKTOP_LAUNCHER_PID=str(os.getpid()),
                       IEI_DESKTOP_INSTANCE_ID="test-owned", IEI_DESKTOP_SETUP_MARKER=str(blocker), IEI_AUTO_START_DOCKER="0",
                       IEI_MAC_TOOL_DISCOVERY="0", IEI_DESKTOP_STARTUP_STATUS=str(status),
                       IEI_DESKTOP_STARTUP_CONTROL=str(control), IEI_DESKTOP_ENDPOINT_FILE=str(root / "endpoint.json"),
                       IEI_DESKTOP_NO_BROWSER="1")
            def send_quit(request_id):
                pending = control.with_suffix(".tmp")
                pending.write_text(json.dumps({"action": "quit", "request_id": request_id}))
                pending.replace(control)

            def wait_for_status(supervisor, **expected):
                snapshot = {}
                for _ in range(100):
                    self.assertIsNone(supervisor.poll())
                    if status.exists():
                        snapshot = json.loads(status.read_text())
                        if all(snapshot.get(k) == v for k, v in expected.items()):
                            return snapshot
                    time.sleep(.1)
                self.fail(f"Expected {expected} in startup status, last saw {snapshot}")

            for setup in ("1", "0"):
                env["IEI_DESKTOP_SETUP"] = setup
                status.unlink(missing_ok=True)
                args = ["-c", boot] if setup == "1" else ["-m", "local_service.desktop_app"]
                with subprocess.Popen([sys.executable, "-B", *args], cwd=Path(__file__).resolve().parents[1],
                                      env=env) as supervisor:
                    try:
                        wait_for_status(supervisor, phase="ready")
                        port = read_endpoint(root / "desktop-endpoint.json")["port"]
                        blocker.touch()
                        for request_id in ("first", "second"):
                            send_quit(request_id)
                            reply = wait_for_status(supervisor, quit_request_id=request_id)
                            self.assertIn("annotation-environment", reply["quit_error"])
                        blocker.unlink()
                        if setup == "0":
                            base = f"http://127.0.0.1:{port}"
                            target = (root / "new-references").resolve()
                            change = desktop_app.api(base, "/api/storage/location", {"kind": "annotation", "path": str(target)})
                            self.assertTrue(change["restart_required"])
                            self.assertTrue(desktop_app.api(base, "/api/service/restart", {})["restarting"])
                            wait_for_status(supervisor, phase="ready", quit_error=None)
                            port = read_endpoint(root / "desktop-endpoint.json")["port"]
                            locations = desktop_app.api(f"http://127.0.0.1:{port}", "/api/storage/locations")["locations"]
                            annotation = next(location for location in locations if location["id"] == "annotation")
                            self.assertEqual(annotation["active_path"], str(target))
                        send_quit("final")
                        self.assertEqual(supervisor.wait(timeout=10), 0)
                        self.assertFalse(control.exists())
                        with self.assertRaises(ConnectionRefusedError):
                            socket.create_connection(("127.0.0.1", port), timeout=1)
                    finally:
                        if supervisor.poll() is None:
                            supervisor.terminate()
                            supervisor.wait(timeout=10)

    def test_auto_port_binds_fallback_without_touching_foreign_listener(self):
        with socket.socket() as foreign:
            foreign.bind(("127.0.0.1", 0))
            foreign.listen()
            occupied = foreign.getsockname()[1]
            server = create_server(object(), port=occupied, auto_port=True)
            try:
                self.assertEqual(server.server_address[0], "127.0.0.1")
                self.assertNotEqual(server.server_port, occupied)
                foreign.setblocking(False)
                with self.assertRaises(BlockingIOError):
                    foreign.accept()
                with self.assertRaises(OSError):
                    create_server(object(), port=occupied)  # CLI opt-in unchanged
            finally:
                server.server_close()

    def test_auto_port_retains_free_preferred_port_and_reopens_after_close(self):
        first = create_server(object(), port=0, auto_port=True)
        port = first.server_port
        first.server_close()
        second = create_server(object(), port=port, auto_port=True)
        try:
            self.assertEqual(second.server_port, port)
        finally:
            second.server_close()

    def test_auto_port_does_not_hide_non_collision_errors(self):
        with patch("local_service.workbench_service._bind_desktop_server", side_effect=PermissionError(errno.EACCES, "denied")) as constructor:
            with self.assertRaises(PermissionError):
                create_server(object(), port=3000, auto_port=True)
            self.assertEqual(constructor.call_count, 1)

    def test_macos_wildcard_collision_skips_preferred_bind_in_launcher_and_child(self):
        occupied, assigned = 43210, 51234

        def bind(address):
            if address == ("0.0.0.0", occupied):
                raise OSError(errno.EADDRINUSE, "occupied")

        with patch.object(desktop_endpoint.sys, "platform", "darwin"), \
                patch.object(desktop_endpoint.socket, "socket") as sockets, \
                patch("local_service.workbench_service.LoopbackHTTPServer") as constructor:
            probe = sockets.return_value.__enter__.return_value
            probe.bind.side_effect = bind
            constructor.return_value.server_port = assigned
            self.assertFalse(port_available(occupied))
            probe.bind.assert_called_once_with(("0.0.0.0", occupied))
            probe.bind.reset_mock()
            server = create_server(object(), port=occupied, auto_port=True)
            self.assertEqual(constructor.call_count, 1)
            self.assertEqual(constructor.call_args.args[0], ("127.0.0.1", 0))
            self.assertEqual(probe.bind.call_args_list[0].args[0], ("0.0.0.0", occupied))
            probe.bind.assert_any_call(("0.0.0.0", assigned))
            server.server_bind.assert_called_once_with()
            server.server_activate.assert_called_once_with()
            probe.listen.assert_not_called()
            probe.connect.assert_not_called()

    def test_kernel_selected_port_is_checked_before_listen(self):
        first, second = Mock(server_port=50001), Mock(server_port=50002)
        with patch("local_service.workbench_service.LoopbackHTTPServer", side_effect=[first, second]), \
                patch("local_service.workbench_service.guard_loopback_aliases") as guard:
            from contextlib import nullcontext
            guard.side_effect = [OSError(errno.EADDRINUSE, "occupied"), nullcontext()]
            self.assertIs(create_server(object(), port=0, auto_port=True), second)
            self.assertEqual([call.args[0] for call in guard.call_args_list], [50001, 50002])
            first.server_activate.assert_not_called()
            first.server_close.assert_called_once_with()
            second.server_activate.assert_called_once_with()

    @unittest.skipUnless(socket.has_ipv6, "IPv6 unavailable")
    def test_ipv6_loopback_listener_blocks_preferred_port(self):
        with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as listener:
            try:
                listener.bind(("::1", 0))
            except OSError as exc:
                if exc.errno == errno.EADDRNOTAVAIL:
                    self.skipTest("IPv6 loopback unavailable")
                raise
            listener.listen()
            occupied = listener.getsockname()[1]
            self.assertFalse(port_available(occupied))
            with create_server(object(), port=occupied, auto_port=True) as server:
                self.assertEqual(server.server_address[0], "127.0.0.1")
                self.assertNotEqual(server.server_port, occupied)

    def test_endpoint_is_private_atomic_and_never_accepts_a_remote_url(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "endpoint.json"
            self.assertIsNone(read_endpoint(path))
            write_endpoint(path, 43118, "owned")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(read_endpoint(path), {"service": "GUIDE-IEI", "port": 43118, "instance_id": "owned"})
            for bad in ([], {"service": "other", "port": 3000, "instance_id": "x"},
                        {"service": "GUIDE-IEI", "port": True, "instance_id": "x"},
                        {"service": "GUIDE-IEI", "port": 65536, "instance_id": "x"},
                        {"service": "GUIDE-IEI", "port": 3000, "instance_id": ""}):
                path.write_text(json.dumps(bad))
                self.assertIsNone(read_endpoint(path))
            path.write_text('{"service":"GUIDE-IEI","port":43118,"instance_id":"owned","url":"https://example.com"}')
            self.assertNotIn("url", read_endpoint(path))

    def test_reopen_uses_saved_verified_port_not_default(self):
        with tempfile.TemporaryDirectory() as temporary:
            endpoint = Path(temporary) / "native.json"
            saved = {"port": 43118, "instance_id": "running"}
            with patch.object(desktop_app, "existing_instance", return_value=desktop_app.EXISTING_BUILD) as identify, patch.object(desktop_app.subprocess, "run") as browser, patch.dict(os.environ, {"IEI_DESKTOP_NO_BROWSER": "0"}):
                self.assertEqual(desktop_app.reopen_endpoint(saved, Path(temporary), endpoint), desktop_app.EXISTING_BUILD)
                identify.assert_called_once_with("http://127.0.0.1:43118", Path(temporary), "running")
                browser.assert_called_once_with(["/usr/bin/open", "http://127.0.0.1:43118"], check=True)
                self.assertEqual(read_endpoint(endpoint)["port"], 43118)

    def test_new_session_prefers_default_and_never_reopens_an_unverified_listener(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            saved = root / "saved.json"
            write_endpoint(saved, 43210, "old-instance")
            for free, expected in ((True, 3000), (False, 43210)):
                with self.subTest(free=free), patch.dict(os.environ, {}, clear=True), \
                        patch.object(desktop_app, "existing_instance", return_value=OCCUPIED_PORT) as identify, \
                        patch.object(desktop_app, "port_available", return_value=free) as probe, \
                        patch.object(desktop_app.signal, "signal"), patch.object(desktop_app.subprocess, "run") as browser, \
                        patch.object(desktop_app.subprocess, "Popen", side_effect=OSError("stop at launch")) as child:
                    with self.assertRaisesRegex(OSError, "stop at launch"):
                        desktop_app.run_workbench(root, root, root / "session", saved, "new-instance")
                    identify.assert_called_once_with("http://127.0.0.1:43210", root, "old-instance")
                    probe.assert_called_once_with(3000)
                    command = child.call_args.args[0]
                    self.assertEqual(command[command.index("--port") + 1], str(expected))
                    browser.assert_not_called()

    def test_service_exit_during_preparation_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            endpoint = root / "session"
            child = Mock(returncode=None)
            child.poll.side_effect = lambda: child.returncode
            def launch(*args, **kwargs):
                write_endpoint(endpoint, 3000, "owned")
                return child
            def die(*args):
                child.returncode = 17
                raise OSError("lost connection")
            from functools import partial
            setup = partial(desktop_app.prepare_engine, call=die, pause=lambda _: None)
            opener = Mock()
            opener.open.return_value = io.BytesIO(b'{"ok":true}')
            env = {"IEI_DESKTOP_STARTUP_STATUS": str(root / "status"), "IEI_DESKTOP_STARTUP_CONTROL": str(root / "control")}
            with patch.dict(os.environ, env, clear=True), patch.object(desktop_app, "port_available", return_value=True), \
                    patch.object(desktop_app.signal, "signal"), patch.object(desktop_app, "prepare_engine", setup), \
                    patch.object(desktop_app.urllib.request, "build_opener", return_value=opener), \
                    patch.object(desktop_app.subprocess, "Popen", side_effect=launch):
                with self.assertRaisesRegex(RuntimeError, "during preparation .*17"):
                    desktop_app.run_workbench(root, root, endpoint, root / "saved", "owned")

    def test_running_supervisor_retries_control_io_and_honors_quit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            endpoint, control = root / "session", root / "control.json"
            claimed = control.with_suffix(".reading")
            control.write_text(json.dumps({"action": "quit", "request_id": "synthetic"}))
            child = Mock(returncode=None)
            child.poll.side_effect = lambda: child.returncode
            def launch(*args, **kwargs):
                write_endpoint(endpoint, 3000, "owned")
                return child
            opener = Mock()
            opener.open.return_value = io.BytesIO(b'{"ok":true}')
            failures = {"replace": 1, "read": 1, "unlink": 1}
            replace, read, unlink = Path.replace, Path.read_text, Path.unlink
            def flaky_replace(path, target):
                if path == control and failures["replace"]:
                    failures["replace"] -= 1
                    raise PermissionError("synthetic secret")
                return replace(path, target)
            def flaky_read(path, *args, **kwargs):
                if path == claimed and path.exists() and failures["read"]:
                    failures["read"] -= 1
                    raise OSError("synthetic secret")
                return read(path, *args, **kwargs)
            def flaky_unlink(path, *args, **kwargs):
                if path == claimed and failures["unlink"]:
                    failures["unlink"] -= 1
                    raise OSError("synthetic secret")
                return unlink(path, *args, **kwargs)
            def quit_service(*args):
                child.returncode = 0
            env = {"IEI_DESKTOP_STARTUP_CONTROL": str(control), "IEI_DESKTOP_NO_BROWSER": "1"}
            with patch.dict(os.environ, env, clear=True), patch.object(desktop_app, "port_available", return_value=True), \
                    patch.object(desktop_app.signal, "signal"), patch.object(desktop_app.time, "sleep"), \
                    patch.object(desktop_app.urllib.request, "build_opener", return_value=opener), \
                    patch.object(desktop_app.subprocess, "Popen", side_effect=launch), \
                    patch.object(desktop_app, "api", side_effect=quit_service) as api, \
                    patch.object(Path, "replace", flaky_replace), patch.object(Path, "read_text", flaky_read), \
                    patch.object(Path, "unlink", flaky_unlink), patch("builtins.print") as output:
                self.assertEqual(desktop_app.run_workbench(root, root, endpoint, root / "saved", "owned"), 0)
            self.assertEqual(failures, {"replace": 0, "read": 0, "unlink": 0})
            api.assert_called_once_with("http://127.0.0.1:3000", "/api/service/quit",
                                        {"confirm": True, "instance_id": "owned"})
            output.assert_not_called()

    def test_stale_endpoint_never_opens_foreign_service(self):
        with tempfile.TemporaryDirectory() as temporary:
            endpoint = Path(temporary) / "native.json"
            with patch.object(desktop_app, "existing_instance", return_value=OCCUPIED_PORT), patch.object(desktop_app.subprocess, "run") as browser:
                result = desktop_app.reopen_endpoint({"port": 43118, "instance_id": "dead"}, Path(temporary), endpoint)
                self.assertEqual(result, OCCUPIED_PORT)
                browser.assert_not_called()
                self.assertFalse(endpoint.exists())

    def test_concurrent_launch_reopens_verified_instance_without_second_child(self):
        import fcntl
        with tempfile.TemporaryDirectory() as temporary:
            support = Path(temporary)
            endpoint = support / "native-second.json"
            write_endpoint(support / "desktop-endpoint.json", 43118, "running")
            with (support / "desktop-launch.lock").open("a") as owner:
                fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with patch.dict(os.environ, {"IEI_APP_SUPPORT_DIR": temporary, "IEI_DESKTOP_ENDPOINT_FILE": str(endpoint), "IEI_DESKTOP_NO_BROWSER": "1"}), patch.object(desktop_app, "existing_instance", return_value=desktop_app.EXISTING_BUILD), patch.object(desktop_app.subprocess, "Popen") as launch:
                    self.assertEqual(desktop_app.main(), desktop_app.EXISTING_BUILD)
                    launch.assert_not_called()
                    self.assertEqual(read_endpoint(endpoint)["instance_id"], "running")

    def test_concurrent_launch_waits_without_taking_over_starting_instance(self):
        import fcntl
        with tempfile.TemporaryDirectory() as temporary:
            support = Path(temporary)
            with (support / "desktop-launch.lock").open("a") as owner:
                fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with patch.dict(os.environ, {"IEI_APP_SUPPORT_DIR": temporary}), patch.object(desktop_app.time, "sleep"), patch.object(desktop_app.subprocess, "Popen") as launch:
                    self.assertEqual(desktop_app.main(), OCCUPIED_PORT)
                    launch.assert_not_called()

    def test_supervisor_passes_bound_port_to_browser_and_native_controls(self):
        class Service:
            def worker_health(self):
                return {"alive": True, "failures": 0}

        class Child:
            returncode = None
            def poll(self):
                return self.returncode
            def terminate(self):
                self.returncode = 0
            def wait(self, **kwargs):
                return self.returncode

        with tempfile.TemporaryDirectory() as temporary, socket.socket() as foreign:
            root = Path(temporary)
            foreign.bind(("127.0.0.1", 0))
            foreign.listen()
            preferred = foreign.getsockname()[1]
            native, saved = root / "native.json", root / "saved.json"
            child = Child()
            servers = []
            browsers = []

            def launch(command, *, cwd, env):
                self.assertIn("--auto-port", command)
                port = int(command[command.index("--port") + 1])
                self.assertEqual(port, 0)
                server = create_server(Service(), port=port, auto_port=True)
                worker = threading.Thread(target=server.serve_forever, daemon=True)
                worker.start()
                servers.append((server, worker))
                write_endpoint(env["IEI_DESKTOP_ENDPOINT_FILE"], server.server_port, env["IEI_DESKTOP_INSTANCE_ID"])
                return child

            def browser(command, **kwargs):
                expected = f"http://127.0.0.1:{servers[0][0].server_port}"
                self.assertEqual(command, ["/usr/bin/open", expected])
                self.assertEqual(read_endpoint(native), read_endpoint(saved))
                browsers.append(expected)
                child.returncode = 0  # Simulate a clean Quit after readiness.

            try:
                with patch.dict(os.environ, {"IEI_UI_PORT": str(preferred), "IEI_DESKTOP_NO_BROWSER": "0", "IEI_DESKTOP_SETUP": "0"}), patch.object(desktop_app, "existing_instance", return_value=OCCUPIED_PORT), patch.object(desktop_app.subprocess, "Popen", side_effect=launch), patch.object(desktop_app.subprocess, "run", side_effect=browser), patch.object(desktop_app.signal, "signal"):
                    self.assertEqual(desktop_app.run_workbench(root, root, native, saved, "test-owned"), 0)
                self.assertEqual(len(browsers), 1)
                self.assertNotEqual(read_endpoint(native)["port"], preferred)
                self.assertEqual(foreign.getsockname()[1], preferred)
            finally:
                for server, worker in servers:
                    server.shutdown()
                    server.server_close()
                    worker.join(timeout=3)

    def test_recently_closed_connection_does_not_block_immediate_reopen(self):
        # Put the SERVER end in TIME_WAIT: it sends FIN first. This reproduces
        # the false 'another process is using port' failure after webpage Quit.
        with socket.socket() as listener, socket.socket() as client:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
            listener.listen()
            client.settimeout(3)
            client.connect(("127.0.0.1", port))
            connection, _ = listener.accept()
            with connection:
                connection.settimeout(3)
                connection.shutdown(socket.SHUT_WR)
                self.assertEqual(client.recv(1), b"")
                client.shutdown(socket.SHUT_WR)
                self.assertEqual(connection.recv(1), b"")
        with socket.socket() as old_probe:
            with self.assertRaises(OSError) as error:
                old_probe.bind(("127.0.0.1", port))
            self.assertEqual(error.exception.errno, errno.EADDRINUSE)
        with patch("local_service.desktop_app.existing_instance") as identify:
            self.assertTrue(port_available(port))
            identify.assert_not_called()
        with create_server(object(), port=port, auto_port=True) as server:
            self.assertEqual(server.server_port, port)

    def test_live_listener_still_blocks_reopen_without_stopping_it(self):
        with socket.socket() as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            port = listener.getsockname()[1]
            with patch("local_service.desktop_app.existing_instance", return_value=OCCUPIED_PORT) as identify:
                self.assertFalse(port_available(port))
                identify.assert_not_called()
            self.assertTrue(listener.fileno() >= 0)

    def test_static_disconnect_closes_connection_after_one_response(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "index.html").write_bytes(b"content")
            for phase in ("headers", "body"):
                with self.subTest(phase=phase):
                    handler = Mock(path="/", close_connection=False)
                    if phase == "headers":
                        handler.end_headers.side_effect = ConnectionResetError()
                    else:
                        handler.wfile.write.side_effect = BrokenPipeError()
                    serve_static(handler, root)
                    handler.send_response.assert_called_once_with(200)
                    handler.end_headers.assert_called_once_with()
                    handler.send_error.assert_not_called()
                    self.assertTrue(handler.close_connection)

    def test_static_export_stays_inside_web_root_and_api_is_preserved(self):
        class Service:
            def worker_health(self):
                return {"alive": True, "failures": 0}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            web = root / "site"
            web.mkdir()
            (web / "index.html").write_text("<h1>GUIDE-IEI</h1>")
            (root / "secret.txt").write_text("not public")
            (web / "escape.txt").symlink_to(root / "secret.txt")
            server = create_server(Service(), port=0, web_root=web)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_port}"
            try:
                with urllib.request.urlopen(base) as response:
                    self.assertIn(b"GUIDE-IEI", response.read())
                    self.assertEqual(response.headers["Cache-Control"], "no-store")
                with urllib.request.urlopen(base + "/api/health") as response:
                    self.assertTrue(json.load(response)["ok"])
                for path in ("/%2e%2e/secret.txt", "/escape.txt", "/unknown", "/api/unknown", "/%00"):
                    with self.assertRaises(urllib.error.HTTPError) as caught:
                        urllib.request.urlopen(base + path)
                    self.assertEqual(caught.exception.code, 404, path)
                request = urllib.request.Request(base, headers={"Host": "attacker.example"})
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    urllib.request.urlopen(request)
                self.assertEqual(caught.exception.code, 403)
            finally:
                server.shutdown()
                server.server_close()
                thread.join()

    def test_signed_app_cannot_be_patched_or_rolled_back_as_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "VERSION").write_text("0.6.1")
            (root / "desktop-build.json").write_text("{}")
            updater = SoftwareUpdater(root, root / "state", fetch=lambda *_: self.fail("must not download"))
            self.assertTrue(updater.status()["desktop_app"])
            self.assertTrue(updater.status()["release_page"].endswith("/releases"))
            for action in (updater.install, updater.rollback):
                with self.assertRaisesRegex(ValueError, "self-contained Mac app"):
                    action()
            self.assertFalse((root / "state").exists())

    def test_desktop_reference_default_is_outside_app_and_saved_selection_wins(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.dict("os.environ", {"IEI_DEFAULT_ANNOTATION_ROOT": str(root / "data/references")}):
                registry = StorageLocationRegistry(root / "App", root / "library", registry_path=root / "registry.json", persist=False)
                self.assertEqual(registry.root("annotation"), (root / "data/references").resolve())
                saved = registry._defaults()
                saved["annotation_root"] = str(root / "selected-drive")
                (root / "registry.json").write_text(json.dumps(saved))
                reopened = StorageLocationRegistry(root / "App2", root / "library", registry_path=root / "registry.json", persist=False)
                self.assertEqual(reopened.root("annotation"), (root / "selected-drive").resolve())


if __name__ == "__main__":
    unittest.main()
