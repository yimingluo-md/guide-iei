import os
from pathlib import Path
import subprocess

import pytest


BUILD = Path(__file__).resolve().parents[1] / "docker" / "build.sh"
CONFIG = """\
container: # build settings
  # vep_image_tag: disabled_tag
  nested:
    loftee_branch: nested_branch
# Comments may start at column one inside a mapping.
  vep_image_tag: "configured_tag" # active
  loftee_branch: 'configured_branch'
"""


@pytest.mark.parametrize(("config", "tag", "branch", "expected"), [
    (CONFIG, "", "", ("configured_tag", "configured_branch")),
    (CONFIG, "env_tag", "", ("env_tag", "configured_branch")),
    (CONFIG, "", "env_branch", ("configured_tag", "env_branch")),
    (CONFIG, "env_tag", "env_branch", ("env_tag", "env_branch")),
    ("container:\n  # vep_image_tag: disabled\n  loftee_branch: # empty\n"
     "other:\n  vep_image_tag: unrelated\n", "", "", ("release_113.4", "grch38")),
    (None, "", "", ("release_113.4", "grch38")),
])
def test_build_args(tmp_path, config, tag, branch, expected):
    config_path = tmp_path / "annotation.yaml"
    if config is not None:
        config_path.write_text(config, encoding="utf-8")
    runtime = tmp_path / "runtime"
    runtime.write_text("#!/bin/sh\nprintf '%s\\n' \"$@\"\n")
    runtime.chmod(0o755)
    result = subprocess.run(
        ["bash", str(BUILD), str(config_path)],
        env=dict(os.environ, RUNTIME=str(runtime), VEP_TAG=tag, LOFTEE_BRANCH=branch),
        capture_output=True, text=True, check=True,
    )
    args = result.stdout.splitlines()
    build_args = [args[i + 1] for i, arg in enumerate(args) if arg == "--build-arg"]
    assert f"VEP_TAG={expected[0]}" in build_args
    assert f"LOFTEE_BRANCH={expected[1]}" in build_args
