"""Real HTTP interruption/resume and pinned-integrity regressions (no public downloads)."""
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
DOWNLOADER = ROOT / "scripts/parallel_fetch.py"
MIB = 1024 * 1024


class RangeDownloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.output = Path(self.temp.name) / "reference.bin"
        self.data = bytes(range(256)) * (3 * MIB // 256)
        self.digest = hashlib.sha256(self.data).hexdigest()
        self.requests = []
        self.block_second = False
        self.disconnect_once = False
        self.release = threading.Event()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                start, end = map(int, self.headers["Range"].removeprefix("bytes=").split("-"))
                owner.requests.append((start, end))
                if owner.block_second and start == MIB:
                    owner.release.wait(20)
                self.send_response(206)
                self.send_header("Content-Range", f"bytes {start}-{end}/{len(owner.data)}")
                self.send_header("Content-Length", str(end - start + 1))
                self.send_header("ETag", '"fixed-reference"')
                self.end_headers()
                content = owner.data[start:end + 1]
                if owner.disconnect_once and start == MIB:
                    owner.disconnect_once = False
                    content = content[:100]
                    self.close_connection = True
                try:
                    self.wfile.write(content)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.command = [sys.executable, str(DOWNLOADER),
                        f"http://127.0.0.1:{self.server.server_port}/reference", str(self.output),
                        "--connections", "1", "--chunk-mib", "1", "--sha256", self.digest]

    def tearDown(self):
        self.release.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def run_download(self):
        return subprocess.run(self.command, capture_output=True, text=True, timeout=45)

    def test_disconnect_retries_only_failed_range(self):
        self.disconnect_once = True
        result = self.run_download()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.output.read_bytes(), self.data)
        self.assertEqual(self.requests.count((0, MIB - 1)), 1)
        self.assertEqual(self.requests.count((MIB, 2 * MIB - 1)), 2)

    def test_process_interruption_preserves_completed_ranges_for_next_run(self):
        self.block_second = True
        child = subprocess.Popen(self.command, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, start_new_session=True)
        try:
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                try:
                    state = json.loads(Path(str(self.output) + ".ranges.json").read_text())
                    if state["completed"] == [0]:
                        break
                except (OSError, ValueError):
                    pass
                time.sleep(0.05)
            else:
                self.fail("first validated range was not persisted")
        finally:
            os.killpg(child.pid, signal.SIGTERM)
            child.wait(timeout=10)
        self.assertFalse(self.output.exists())
        self.assertTrue(Path(str(self.output) + ".parallel").exists())
        self.block_second = False
        self.release.set()
        result = self.run_download()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("1 already complete", result.stdout)
        self.assertEqual(self.requests.count((0, MIB - 1)), 1)
        self.assertEqual(self.output.read_bytes(), self.data)

    def test_bad_sha256_never_publishes_and_retry_is_not_wedged(self):
        self.command[-1] = "0" * 64
        result = self.run_download()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("failed SHA-256", result.stderr)
        self.assertFalse(self.output.exists())
        self.assertFalse(Path(str(self.output) + ".ranges.json").exists())
        self.command[-1] = self.digest
        self.assertEqual(self.run_download().returncode, 0)

    def test_same_size_corrupt_final_is_replaced(self):
        self.output.write_bytes(b"x" * len(self.data))
        result = self.run_download()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.output.read_bytes(), self.data)


class MirrorWiringTests(unittest.TestCase):
    def invoke(self, setup, assertions):
        script = (ROOT / "scripts/download_references.sh").read_text()
        function = script[script.index("mirror_fetch() {"):script.index("# --- destinations")]
        with tempfile.TemporaryDirectory() as folder:
            command = "set -euo pipefail\nREF_MIRROR=https://example.test\nHERE=/unused\n" + \
                "log() { :; }; warn() { :; }; _sha256_of() { echo good; };\n" + function + \
                '\ndest="$1/reference"\n' + setup + "\n" + assertions
            result = subprocess.run(["bash", "-c", command, "test", folder], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_new_download_uses_pinned_range_downloader_and_keeps_failed_state(self):
        self.invoke('python3() { [[ "$*" == *"--sha256 good"* ]]; touch "$dest.parallel" "$dest.ranges.json"; return 1; }',
                    'if mirror_fetch data good "$dest"; then exit 1; fi\n[[ -f "$dest.parallel" && -f "$dest.ranges.json" ]]')

    def test_legacy_prefix_survives_exhausted_transport_retries(self):
        self.invoke('printf prefix > "$dest.part"\ncalls=0\nfetch() { calls=$((calls+1)); return 1; }',
                    'if mirror_fetch data good "$dest"; then exit 1; fi\n[[ "$calls" = 3 && "$(< "$dest.part")" = prefix ]]')

    def test_legacy_prefix_resumes_after_disconnect(self):
        self.invoke('printf prefix > "$dest.part"\ncalls=0\nfetch() { calls=$((calls+1)); [[ "$calls" = 2 ]] || return 1; mv "$dest.part" "$dest"; }',
                    'mirror_fetch data good "$dest"\n[[ "$calls" = 2 && -s "$dest" ]]')


if __name__ == "__main__":
    unittest.main()
