#!/usr/bin/env python3
"""Exercise the real launcher with disposable process/runtime stand-ins."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ProductionLauncherTests(unittest.TestCase):
    def test_clean_backend_quit_stops_its_paired_frontend(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "scripts").mkdir()
            (root / "webui/node_modules").mkdir(parents=True)
            binaries = root / "bin"
            binaries.mkdir()
            shutil.copy2(ROOT / "scripts/start_workbench.sh", root / "scripts/start_workbench.sh")
            scripts = {
                "curl": "#!/bin/sh\nexit 7\n",
                "git": "#!/bin/sh\necho fixed-commit\n",
                "npm": "#!/bin/sh\nmkdir -p .next\ntouch .next/BUILD_ID\n",
                "node": f"#!{sys.executable}\n" + (
                    "import pathlib, signal, sys, time\n"
                    "if sys.argv[1] == '-p': print(22); sys.exit(0)\n"
                    "def stop(*_):\n"
                    "    pathlib.Path('frontend-stopped').touch(); sys.exit(0)\n"
                    "signal.signal(signal.SIGTERM, stop)\n"
                    "pathlib.Path('frontend-ready').touch()\n"
                    "while True: time.sleep(.02)\n"
                ),
                "python3": f"#!{sys.executable}\n" + (
                    "import os, pathlib, signal, sys, time\n"
                    "if '-m' not in sys.argv: sys.exit(0)\n"
                    "signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))\n"
                    "root = pathlib.Path(os.environ['TEST_LAUNCH_ROOT'])\n"
                    "while not (root / 'webui/frontend-ready').exists(): time.sleep(.02)\n"
                    "sys.exit(0)\n"
                ),
            }
            for name, content in scripts.items():
                executable = binaries / name
                executable.write_text(content)
                executable.chmod(0o755)
            result = subprocess.run(["bash", str(root / "scripts/start_workbench.sh")],
                env={**os.environ, "PATH": str(binaries) + os.pathsep + os.environ["PATH"],
                     "IEI_PYTHON_BIN": str(binaries / "python3"), "IEI_NODE_BIN": str(binaries / "node"),
                     "IEI_WEB_MODE": "production", "IEI_SERVICE_START_TIMEOUT": "3", "TEST_LAUNCH_ROOT": str(root)},
                capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertTrue((root / "webui/frontend-stopped").exists())

    def test_service_port_change_rebuilds_embedded_browser_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "scripts").mkdir()
            (root / "webui/node_modules").mkdir(parents=True)
            binaries = root / "bin"
            binaries.mkdir()
            shutil.copy2(ROOT / "scripts/start_workbench.sh", root / "scripts/start_workbench.sh")
            scripts = {
                "curl": "#!/bin/sh\nexit 7\n",
                "git": "#!/bin/sh\necho fixed-commit\n",
                "node": "#!/bin/sh\necho 22\n",
                "python3": f"#!{sys.executable}\n" + (
                    "import signal, sys, time\n"
                    "if '-m' in sys.argv:\n"
                    "    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))\n"
                    "    while True: time.sleep(0.01)\n"
                ),
                "npm": f"#!{sys.executable}\n" + (
                    "import os, pathlib, sys\n"
                    "if sys.argv[1:] == ['run', 'build']:\n"
                    "    pathlib.Path('.next').mkdir(exist_ok=True)\n"
                    "    pathlib.Path('.next/BUILD_ID').write_text('test-build')\n"
                    "    with pathlib.Path('builds.log').open('a') as log:\n"
                    "        log.write(os.environ['NEXT_PUBLIC_IEI_SERVICE_URL'] + '\\n')\n"
                ),
            }
            for name, content in scripts.items():
                executable = binaries / name
                executable.write_text(content)
                executable.chmod(0o755)
            env = {
                **os.environ,
                "PATH": str(binaries) + os.pathsep + os.environ["PATH"],
                "IEI_PYTHON_BIN": str(binaries / "python3"),
                "IEI_NODE_BIN": str(binaries / "node"),
                "IEI_WEB_MODE": "production",
                "IEI_SERVICE_START_TIMEOUT": "3",
            }
            for port in (43117, 43117, 43129, 43129):
                result = subprocess.run(
                    ["bash", str(root / "scripts/start_workbench.sh")],
                    env={**env, "IEI_SERVICE_PORT": str(port)},
                    capture_output=True, text=True, timeout=10,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual((root / "webui/builds.log").read_text().splitlines(), [
                "http://127.0.0.1:43117", "http://127.0.0.1:43129",
            ])


if __name__ == "__main__":
    unittest.main()
