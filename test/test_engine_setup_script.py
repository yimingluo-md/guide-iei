"""Run engine-only setup with a fake ready Docker; no downloads or installs."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class EngineScriptTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "darwin", "macOS connection recovery")
    def test_recovered_connection_is_inherited_by_all_following_docker_calls(self):
        self.run_scenario("recovered")

    def test_engine_only_reuses_runtime_and_skips_ui_and_native_package_managers(self):
        self.run_scenario("ready")

    def test_packaged_app_missing_bundle_never_builds_on_user_machine(self):
        self.run_scenario("missing_bundle")

    def test_packaged_app_corrupt_bundle_never_builds_or_pulls(self):
        self.run_scenario("corrupt_bundle")

    def test_packaged_app_root_is_not_mounted(self):
        self.run_scenario("packaged_ready")

    def run_scenario(self, scenario):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            (root / "scripts").mkdir()
            for name in ("setup_environment.sh", "macos_container_dependencies.sh", "bundled_engine.py"):
                shutil.copy2(ROOT / "scripts" / name, root / "scripts" / name)
            shutil.copytree(ROOT / "pipeline", root / "pipeline", ignore=shutil.ignore_patterns("__pycache__"))
            (root / "local_service").mkdir()
            for name in ("container_workspace.py", "colima_sharing.py", "container_startup.py"):
                shutil.copy2(ROOT / "local_service" / name, root / "local_service" / name)
            shutil.copytree(ROOT / "docker", root / "docker")
            config = root / "engine.yaml"
            config.write_text("container:\n  runtime: docker\n  image: test-engine:custom\n")
            if scenario not in ("ready", "recovered"):
                (root / "desktop-build.json").write_text('{}')
                if scenario == "corrupt_bundle":
                    (root / "bundled-engine").mkdir()
                    (root / "bundled-engine/manifest.json").write_text('not json')
            fingerprint = subprocess.check_output(["bash", str(root / "docker/image_fingerprint.sh")], text=True).strip()
            binaries = root / "bin"
            binaries.mkdir()
            docker = binaries / "docker"
            if scenario == "recovered":
                (root / "local_service/container_startup.py").write_text(
                    'import sys\nassert sys.argv[1:] == ["--resolve-managed-host"]\nprint("unix:///synthetic-colima/docker.sock")\n')
            probe = root / "fake_probe.py"
            probe.write_text('''import hashlib, os, pathlib, sys
args = sys.argv[1:]
mounts = {}
for i, arg in enumerate(args):
    if arg == '-v':
        host, target, mode = args[i + 1].split(':')
        assert host == os.environ['IEI_CONTAINER_WORK_DIR'], 'application root must not be mounted'
        mounts[target] = pathlib.Path(host)
checks = args[args.index('-e') + 2:]
for i in range(0, len(checks), 4):
    key, mode, target, digest = checks[i:i + 4]
    mount, name = target.rsplit('/', 1)
    path = mounts[mount] / name
    assert hashlib.sha256(path.read_bytes()[:4096]).hexdigest() == digest
    if mode == 'write':
        pathlib.Path(str(path) + '.container').write_bytes(b'container-write-ok\\n')
    print(key + ':OK')
''')
            docker.write_text('#!/bin/bash\n'
                + ('[ "${DOCKER_HOST:-}" = "unix:///synthetic-colima/docker.sock" ] || { echo "unexpected Docker connection" >&2; exit 93; }\n' if scenario == "recovered" else '')
                +
                'case "$*" in\n'
                f'  "image inspect --format "*) echo "{fingerprint if scenario in ("ready", "recovered", "packaged_ready") else "old"}";;\n'
                '  "info --format {{.Architecture}}") echo arm64;;\n'
                '  "info --format "*) echo 8;;\n'
                '  "run "*) exec "$TEST_PYTHON" "$TEST_PROBE" "$@";;\n'
                '  info*|"image inspect "*) exit 0;;\n'
                '  *) echo "unexpected Docker call: $*" >&2; exit 91;;\n'
                'esac\n')
            docker.chmod(0o755)
            for name in ("node", "npm", "brew", "conda", "sudo"):
                path = binaries / name
                path.write_text('#!/bin/sh\necho "unexpected package manager/tool" >&2\nexit 92\n')
                path.chmod(0o755)
            result = subprocess.run(["bash", str(root / "scripts/setup_environment.sh"), "--install", "--yes", "--engine-only", "--config", str(config)],
                env={**os.environ, "PATH": str(binaries) + ":/usr/bin:/bin:/usr/sbin:/sbin",
                     "IEI_PYTHON_BIN": sys.executable, "IEI_TOOLS_DIR": str(root / "tools"),
                     "IEI_CONTAINER_WORK_DIR": str(root / "workspace"),
                     "TEST_PYTHON": sys.executable, "TEST_PROBE": str(probe)},
                capture_output=True, text=True, timeout=30)
            if scenario in ("ready", "recovered", "packaged_ready"):
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("test-engine:custom matches", result.stdout)
                self.assertIn("managed working directory is accessible", result.stdout)
            else:
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("missing its bundled" if scenario == "missing_bundle" else "bundled engine could not be installed", result.stdout)
                self.assertNotIn("Downloading and building", result.stdout)
            self.assertIn("engine-only setup", result.stdout)
            self.assertNotIn("unexpected", result.stdout + result.stderr)
            self.assertFalse((root / "webui").exists())
            if scenario == "packaged_ready":
                before = {p: p.stat().st_mtime_ns for p in (root / "workspace").iterdir()}
                checked = subprocess.run(["bash", str(root / "scripts/setup_environment.sh"), "--check", "--engine-only", "--config", str(config)],
                    env={**os.environ, "PATH": str(binaries) + ":/usr/bin:/bin:/usr/sbin:/sbin",
                         "IEI_PYTHON_BIN": sys.executable, "IEI_TOOLS_DIR": str(root / "tools"),
                         "IEI_CONTAINER_WORK_DIR": str(root / "workspace"),
                         "TEST_PYTHON": sys.executable, "TEST_PROBE": str(probe)}, capture_output=True, text=True, timeout=30)
                self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
                self.assertEqual(before, {p: p.stat().st_mtime_ns for p in (root / "workspace").iterdir()})


if __name__ == "__main__":
    unittest.main()
