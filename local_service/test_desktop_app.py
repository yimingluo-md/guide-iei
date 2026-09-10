import json
import errno
import socket
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from local_service.software_update import SoftwareUpdater
from local_service.storage_locations import StorageLocationRegistry
from local_service.workbench_service import create_server
from local_service.desktop_app import instance_on_port, OCCUPIED_PORT


class DesktopAppTests(unittest.TestCase):
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
            self.assertIsNone(instance_on_port(Path("/unused"), port))
            identify.assert_not_called()

    def test_live_listener_still_blocks_reopen_without_stopping_it(self):
        with socket.socket() as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            port = listener.getsockname()[1]
            with patch("local_service.desktop_app.existing_instance", return_value=OCCUPIED_PORT) as identify:
                self.assertEqual(instance_on_port(Path("/unused"), port), OCCUPIED_PORT)
                identify.assert_called_once()
            self.assertTrue(listener.fileno() >= 0)

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
