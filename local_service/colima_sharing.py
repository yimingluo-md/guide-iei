"""Installer-only repair of the selected local Colima profile's app sharing.

Never invoked by a status GET. Preserve settings and mounts, back up before
changing anything, and refuse to restart a VM with active Docker workloads.
"""
import argparse
import copy
import fcntl
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import uuid

import yaml

if __package__:
    from .container_startup import selected_start_command
else:
    from container_startup import selected_start_command


class SharingError(RuntimeError):
    pass


def probe(docker, image, root, env):
    try:
        return subprocess.run([docker, "run", "--rm", "--pull=never", "--network=none",
            "--mount", f"type=bind,source={root},target=/probe,readonly",
            "--entrypoint", "sh", image, "-c", "test -r /probe/scripts/setup_environment.sh"],
            env=env, capture_output=True, timeout=15, check=False).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def mount_plan(config, root, home, *, writable=False):
    """Add an identity-mapped mount without replacing custom ones."""
    result = copy.deepcopy(config)
    mounts = result.get("mounts") or []
    if not isinstance(mounts, list) or any(not isinstance(item, dict) or not isinstance(item.get("location"), str) for item in mounts):
        raise SharingError("The Colima sharing settings could not be read safely. Open Log for details; no settings were changed.")
    if not mounts:
        mounts = [{"location": str(home), "writable": True}, {"location": "/tmp/colima", "writable": True}]
    for mount in mounts:
        location = mount["location"]
        if location == "~" or location.startswith("~/"):
            location = str(home) + location[1:]
        source = Path(location)
        target = Path(mount.get("mountPoint") or location)
        if source.is_absolute() and target == source and (source == root or source in root.parents):
            if writable and not mount.get("writable", False):
                if source != root:
                    raise SharingError(f"The existing Colima mount {source} is read-only. Allow writes to {root} in your container manager, then Retry preparation; no broader permissions were changed.")
                mount["writable"] = True
            result["mounts"] = mounts
            return result
    # Colima/Lima may reject host mount paths containing spaces. Sharing the
    # stable Applications directory read-only also survives app replacement.
    shared = root
    if not writable and (root == Path("/Applications") or Path("/Applications") in root.parents):
        shared = Path("/Applications")
    elif any(char.isspace() for char in str(root)):
        raise SharingError(f"Colima cannot automatically share a folder containing spaces: {root}. Configure this folder in your container manager, then Retry preparation.")
    if "," in str(shared) or ":" in str(shared):
        raise SharingError(f"This folder name is not supported by the container runtime: {root}. Choose a folder without commas or colons, then retry preparation.")
    mounts.append({"location": str(shared), "writable": writable})
    result["mounts"] = mounts
    return result


def ensure_idle(docker, config, env):
    if (config.get("kubernetes") or {}).get("enabled"):
        raise SharingError("This Colima profile also runs Kubernetes. Disable it in your container manager before choosing Retry preparation; GUIDE-IEI has not stopped it.")
    try:
        result = subprocess.run([docker, "ps", "--quiet"], env=env, capture_output=True, text=True, timeout=10, check=True)
    except (OSError, subprocess.SubprocessError) as exc:
        raise SharingError("Cannot check whether other containers are running. Nothing was restarted; choose Retry preparation when the container runtime is available.") from exc
    if result.stdout.strip():
        raise SharingError("Other containers are running. Finish or stop those workloads in your container manager, then choose Retry preparation. GUIDE-IEI has not interrupted them.")


def atomic_write(path, content):
    fd, temporary = tempfile.mkstemp(prefix=".guide-iei-sharing-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def repair(docker, image, root, env=None, *, writable=False, probe_check=None):
    env = dict(os.environ if env is None else env)
    root = Path(root).resolve()
    check = probe_check or (lambda: probe(docker, image, root, env))
    if check():
        return False
    selected = selected_start_command(docker, env)
    if selected and selected[0] == "Docker Desktop":
        raise SharingError(f"Docker Desktop cannot access the required folder: {root}. Open Docker Desktop → Settings → Resources → File sharing, allow this folder, apply the change, then choose Retry preparation.")
    if not selected or selected[0] != "Colima":
        raise SharingError("The selected container runtime cannot access GUIDE-IEI. Automatic sharing repair supports local Colima profiles; use your container manager's file-sharing settings, then Retry preparation.")
    command = selected[1]
    colima, profile = command[0], command[command.index("--profile") + 1]
    home = Path.home()
    colima_home = Path(env.get("COLIMA_HOME", str(home / ".colima"))).expanduser().resolve()
    config_path = colima_home / profile / "colima.yaml"
    if config_path.is_symlink() or not config_path.is_file():
        raise SharingError("Colima uses an unsupported configuration location. No settings were changed; open Log for details.")
    # Independent installers must not edit the same profile concurrently.
    lock_path = config_path.parent / ".guide-iei-sharing.lock"
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "a+b") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise SharingError("Another setup is repairing this container profile. Wait for it to finish, then Retry preparation.") from exc
        original = config_path.read_bytes()
        config = yaml.safe_load(original)
        if not isinstance(config, dict) or not isinstance(config.get("kubernetes") or {}, dict):
            raise SharingError("Colima configuration is invalid; no settings were changed. Open Log for details.")
        planned = mount_plan(config, root, home, writable=writable)
        updated = yaml.safe_dump(planned, sort_keys=False).encode() if planned != config else original
        ensure_idle(docker, config, env)
        backup = config_path.with_name("colima.yaml.guide-iei-backup-" + uuid.uuid4().hex)
        with backup.open("xb") as handle:
            os.chmod(backup, 0o600)
            handle.write(original)
            handle.flush()
            os.fsync(handle.fileno())
        print("=== Repairing container file sharing ===", flush=True)
        print(f"Preserved Colima configuration in {backup}", flush=True)
        # Check again immediately before stop, and reject concurrent edits.
        ensure_idle(docker, config, env)
        if config_path.read_bytes() != original:
            raise SharingError("Colima settings changed during setup. Nothing was restarted; choose Retry preparation.")
        try:
            subprocess.run([colima, "stop", "--profile", profile], env=env, check=True, timeout=120)
            if config_path.read_bytes() != original:
                raise SharingError("Colima settings changed during restart. They were left intact; choose Retry preparation.")
            if updated != original:
                atomic_write(config_path, updated)
            print("=== Restarting container runtime with repaired sharing ===", flush=True)
            # Config was updated atomically above. Do not let CLI defaults
            # overwrite it, or activate/switch the user's Docker context.
            subprocess.run(command, env=env, check=True, timeout=180)
        except (OSError, subprocess.SubprocessError) as exc:
            if updated != original and config_path.read_bytes() == updated:
                atomic_write(config_path, original)
            raise SharingError("The container restart did not finish. Previous sharing settings were restored; choose Retry preparation. No image or dataset was deleted.") from exc
        print("=== Checking repaired container access ===", flush=True)
        for attempt in range(6):
            if check():
                return True
            if attempt < 5:
                time.sleep(1)
        raise SharingError(f"Colima restarted but access to {root} still failed. Check this folder's sharing permissions and open Log for details, then retry.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docker", required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    try:
        repair(args.docker, args.image, args.root)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, yaml.YAMLError) as exc:
        print("GUIDE_IEI_SETUP_ERROR: " + str(exc), flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
