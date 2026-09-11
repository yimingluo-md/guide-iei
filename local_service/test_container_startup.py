import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from local_service.container_startup import DockerStartup, mac_tool_path, selected_start_command, managed_colima_environment


class DockerStartupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name).resolve()
        home_patch = patch("local_service.container_startup.Path.home", return_value=self.home)
        home_patch.start()
        self.addCleanup(home_patch.stop)
        self.env = {"PATH": "/usr/bin:/bin"}
        self.startup = DockerStartup(self.home / "logs/startup.log")

    def desktop(self):
        (self.home / "Applications/Docker.app").mkdir(parents=True)
        self.env["DOCKER_HOST"] = "unix://" + str(self.home / ".docker/run/docker.sock")

    def test_desktop_endpoint_starts_existing_app(self):
        self.desktop()
        label, command = selected_start_command("docker", self.env)
        self.assertEqual(label, "Docker Desktop")
        self.assertEqual(command, ["/usr/bin/open", "-g", str(self.home / "Applications/Docker.app")])

    def test_remote_and_unrecognized_local_endpoints_are_not_started(self):
        self.desktop()
        for endpoint in ("ssh://someone@example.test", "tcp://127.0.0.1:2375", "unix:///tmp/other-provider.sock"):
            self.env["DOCKER_HOST"] = endpoint
            self.assertIsNone(selected_start_command("docker", self.env))

    def test_context_takes_precedence_over_host(self):
        self.desktop()
        self.env["DOCKER_CONTEXT"] = "remote"
        output = json.dumps([{"Endpoints": {"docker": {"Host": "ssh://example.test"}}}])
        with patch("local_service.container_startup.subprocess.run", return_value=Mock(stdout=output)) as run:
            self.assertIsNone(selected_start_command("docker", self.env))
        self.assertEqual(run.call_args.args[0], ["docker", "context", "inspect", "remote"])

    def test_colima_preserves_existing_profile_and_context(self):
        folder = self.home / ".colima/review"
        folder.mkdir(parents=True)
        (folder / "colima.yaml").write_text("cpu: 4\n")
        self.env["DOCKER_HOST"] = "unix://" + str(folder / "docker.sock")
        with patch("local_service.container_startup.shutil.which", return_value="/tools/colima"):
            label, command = selected_start_command("docker", self.env)
        self.assertEqual(label, "Colima")
        self.assertEqual(command, ["/tools/colima", "start", "--profile", "review", "--activate=false", "--save-config=false"])
        (folder / "colima.yaml").unlink()
        self.assertIsNone(selected_start_command("docker", self.env))

    def test_symlink_to_colima_does_not_start_desktop(self):
        self.desktop()
        folder = self.home / ".colima/default"
        folder.mkdir(parents=True)
        (folder / "colima.yaml").write_text("{}")
        alias = self.home / "alias.sock"
        alias.symlink_to(folder / "docker.sock")
        self.env["DOCKER_HOST"] = "unix://" + str(alias)
        with patch("local_service.container_startup.shutil.which", return_value="/tools/colima"):
            self.assertEqual(selected_start_command("docker", self.env)[0], "Colima")

    def test_running_engine_never_launches_another(self):
        with patch("local_service.container_startup.shutil.which", return_value="docker"), \
                patch.object(self.startup, "_ready", return_value=True), \
                patch("local_service.container_startup.selected_start_command") as select:
            self.startup.run()
        select.assert_not_called()
        self.assertEqual(self.startup.status["state"], "ready")

    def test_start_and_wait_for_readiness(self):
        with patch("local_service.container_startup.shutil.which", return_value="docker"), \
                patch.object(self.startup, "_ready", side_effect=[False, True]), \
                patch("local_service.container_startup.selected_start_command", return_value=("Docker Desktop", ["open"])), \
                patch.object(self.startup, "_launch", return_value=True) as launch:
            self.startup.run()
        launch.assert_called_once()
        self.assertEqual(self.startup.status["state"], "ready")
        self.assertEqual(self.startup.log_path.stat().st_mode & 0o777, 0o600)

    def test_command_failure_and_timeout_produce_actionable_status(self):
        for outcome in (False, subprocess.TimeoutExpired("open", 120)):
            with patch("local_service.container_startup.shutil.which", return_value="docker"), \
                    patch.object(self.startup, "_ready", return_value=False), \
                    patch("local_service.container_startup.selected_start_command", return_value=("Docker Desktop", ["open"])), \
                    patch.object(self.startup, "_launch", side_effect=[outcome]):
                self.startup.run()
            self.assertEqual(self.startup.status["state"], "failed")
            self.assertIn("Startup log:", self.startup.status["message"])

    def test_unready_engine_times_out_after_successful_launch(self):
        with patch("local_service.container_startup.shutil.which", return_value="docker"), \
                patch.object(self.startup, "_ready", return_value=False), \
                patch("local_service.container_startup.selected_start_command", return_value=("Docker Desktop", ["open"])), \
                patch.object(self.startup, "_launch", return_value=True), \
                patch("local_service.container_startup.time.monotonic", side_effect=[0, 121]):
            self.startup.run()
        self.assertEqual(self.startup.status["state"], "failed")

    def test_linux_podman_and_opt_out_do_not_start(self):
        with patch("local_service.container_startup.threading.Thread") as thread:
            with patch("local_service.container_startup.platform.system", return_value="Linux"):
                self.startup.start()
            with patch("local_service.container_startup.platform.system", return_value="Darwin"):
                self.startup.start("podman")
                with patch.dict(os.environ, {"IEI_AUTO_START_DOCKER": "0"}):
                    self.startup.start()
        thread.assert_not_called()

    def test_start_is_single_attempt(self):
        with patch("local_service.container_startup.platform.system", return_value="Darwin"), \
                patch.dict(os.environ, {"IEI_AUTO_START_DOCKER": "1"}), \
                patch("local_service.container_startup.threading.Thread") as thread:
            self.startup.start()
            self.startup.start()
        thread.assert_called_once()

    def test_path_preserves_precedence_and_supports_hermetic_smoke(self):
        with patch.dict(os.environ, {"IEI_MAC_TOOL_DISCOVERY": "1"}):
            path = mac_tool_path("/custom/bin:/usr/bin")
        self.assertTrue(path.startswith("/custom/bin:/usr/bin:"))
        self.assertIn("/Applications/Docker.app/Contents/Resources/bin", path)
        with patch.dict(os.environ, {"IEI_MAC_TOOL_DISCOVERY": "0"}):
            self.assertEqual(mac_tool_path("/usr/bin"), "/usr/bin")

    def managed_stack(self, profiles=("default",)):
        # Do not inherit this test host's Docker Desktop socket symlink.
        original_resolve = Path.resolve
        def resolve(path, *args, **kwargs):
            if str(path) in ("/var/run/docker.sock", "/private/var/run/docker.sock"):
                return Path("/private/var/run/docker.sock")
            return original_resolve(path, *args, **kwargs)
        resolver = patch("local_service.container_startup.Path.resolve", resolve)
        resolver.start()
        self.addCleanup(resolver.stop)
        tools = self.home / "tools"
        (tools / "bin").mkdir(parents=True)
        for name in ("docker", "colima"):
            path = tools / "bin" / name
            path.write_text("#!/bin/sh\n")
            path.chmod(0o700)
        for name in profiles:
            folder = self.home / ".colima" / name
            folder.mkdir(parents=True)
            (folder / "colima.yaml").write_text("cpu: 6\nmounts: []\n")
        return {"PATH": str(tools / "bin") + ":/usr/bin:/bin", "IEI_TOOLS_DIR": str(tools)}

    def selection_output(self, context="default", ready=False, endpoint="unix:///var/run/docker.sock"):
        def run(args, **kwargs):
            if args[1:] == ["context", "show"]:
                return Mock(stdout=context)
            if args[1:3] == ["context", "inspect"]:
                return Mock(stdout=json.dumps([{"Endpoints": {"docker": {"Host": endpoint}}}]))
            if args[1:] == ["info"]:
                return Mock(returncode=0 if ready else 1)
            raise AssertionError(args)
        return run

    def test_orphaned_default_recovers_process_local_host_without_writing(self):
        env = self.managed_stack()
        before = dict(env)
        config = self.home / ".colima/default/colima.yaml"
        contents = config.read_bytes()
        with patch("local_service.container_startup.docker_desktop_installed", return_value=False), \
                patch("local_service.container_startup.subprocess.run", side_effect=self.selection_output()) as run:
            recovered = managed_colima_environment(env)
        self.assertEqual(env, before)
        self.assertEqual(recovered["DOCKER_HOST"], "unix://" + str(config.parent / "docker.sock"))
        self.assertEqual(config.read_bytes(), contents)
        self.assertEqual([call.args[0][1:] for call in run.call_args_list], [["context", "show"], ["context", "inspect", "default"], ["info"]])
        self.assertEqual(selected_start_command("docker", recovered)[1][2:4], ["--profile", "default"])

    def test_recovery_never_overrides_explicit_or_nondefault_connections(self):
        env = self.managed_stack()
        with patch("local_service.container_startup.subprocess.run") as run:
            for override in ({"DOCKER_HOST": "ssh://test"}, {"DOCKER_CONTEXT": "default"}):
                selected = dict(env, **override)
                self.assertEqual(managed_colima_environment(selected), selected)
            run.assert_not_called()
        for context, endpoint, ready in (("custom", "unix:///var/run/docker.sock", False),
                                         ("default", "ssh://remote", False),
                                         ("default", "unix:///tmp/other.sock", False),
                                         ("default", "unix:///var/run/docker.sock", True)):
            with patch("local_service.container_startup.docker_desktop_installed", return_value=False), \
                    patch("local_service.container_startup.subprocess.run", side_effect=self.selection_output(context, ready, endpoint)):
                self.assertEqual(managed_colima_environment(env), env)

    def test_ambiguous_profiles_and_desktop_are_not_guessed(self):
        env = self.managed_stack(("default", "research"))
        with patch("local_service.container_startup.docker_desktop_installed", return_value=False), \
                patch("local_service.container_startup.subprocess.run", side_effect=self.selection_output()):
            self.assertEqual(managed_colima_environment(env), env)
        with patch("local_service.container_startup.docker_desktop_installed", return_value=True), \
                patch("local_service.container_startup.subprocess.run") as run:
            self.assertEqual(managed_colima_environment(env), env)
            run.assert_not_called()

    def test_missing_profile_and_unmanaged_client_are_not_guessed(self):
        env = self.managed_stack(())
        with patch("local_service.container_startup.docker_desktop_installed", return_value=False), \
                patch("local_service.container_startup.subprocess.run", side_effect=self.selection_output()):
            self.assertEqual(managed_colima_environment(env), env)
        with patch("local_service.container_startup.shutil.which", return_value="/unmanaged/docker"), \
                patch("local_service.container_startup.subprocess.run") as run:
            self.assertEqual(managed_colima_environment(env), env)
            run.assert_not_called()

    def test_recovery_errors_keep_original_environment(self):
        env = self.managed_stack()
        for problem in (subprocess.TimeoutExpired("docker", 5), ValueError("invalid JSON"), OSError("unavailable")):
            with patch("local_service.container_startup.docker_desktop_installed", return_value=False), \
                    patch("local_service.container_startup.subprocess.run", side_effect=problem):
                self.assertEqual(managed_colima_environment(env), env)

    def test_unrecognized_selection_writes_diagnostics(self):
        with patch("local_service.container_startup.shutil.which", return_value="docker"), \
                patch.object(self.startup, "_ready", return_value=False), \
                patch("local_service.container_startup.selected_start_command", return_value=None):
            self.startup.run()
        self.assertEqual(self.startup.status["state"], "unavailable")
        self.assertIn("No recognized selected local engine", self.startup.log_path.read_text())
        self.assertEqual(self.startup.log_path.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
