"""Verify a user-owned container workspace, never the signed application tree.

Preparation performs a real bidirectional write check. Readiness is read-only:
it checks the marker published by successful preparation through the container.
Dataset/input/output access is still checked separately by pipeline preflight.
"""
import argparse
import os
from pathlib import Path
import sys
import tempfile
import subprocess

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.container_access import AccessError, RuntimeAccessError, AccessPath, check_access

READY = "workspace-ready-v1.txt"
READY_BYTES = b"GUIDE-IEI container workspace: read/write verified v1\n"


def workspace_directory(state_dir=None):
    override = os.environ.get("IEI_CONTAINER_WORK_DIR")
    state = state_dir or os.environ.get("IEI_WORKBENCH_STATE_DIR") or Path.home() / ".iei-variant-review"
    return Path(override or Path(state) / "container-work").expanduser().resolve()


def check_ready(directory, runtime, image):
    directory = Path(directory).resolve()
    marker = directory / READY
    try:
        if marker.read_bytes() != READY_BYTES:
            raise ValueError("invalid workspace marker")
    except (OSError, ValueError) as exc:
        raise AccessError(f"Container workspace has not been prepared: {directory}. Choose Retry preparation.") from exc
    check_access([AccessPath(marker, "managed container workspace")], runtime, image, timeout=10)


def prepare(directory, runtime, image, *, repair_sharing=False):
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    marker = directory / READY
    marker.unlink(missing_ok=True)

    def verify():
        check_access([AccessPath(directory, "managed container workspace", True)], runtime, image, timeout=15)

    try:
        verify()
    except AccessError as original:
        if not repair_sharing or runtime != "docker" or isinstance(original, RuntimeAccessError):
            raise
        from local_service.colima_sharing import repair

        def probe_workspace():
            try:
                verify()
                return True
            except RuntimeAccessError:
                raise
            except AccessError:
                return False

        # The selected engine and image were already verified by setup. The
        # repair helper preserves custom mounts and refuses to stop workloads.
        try:
            repair(runtime, image, directory, writable=True, probe_check=probe_workspace)
        except (RuntimeError, subprocess.SubprocessError) as exc:
            raise AccessError(f"{exc}\n{original}") from exc
        verify()
    fd, temporary = tempfile.mkstemp(prefix=".workspace-ready-", dir=directory)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(READY_BYTES)
        os.chmod(temporary, 0o644)
        os.replace(temporary, marker)
    finally:
        Path(temporary).unlink(missing_ok=True)
    check_ready(directory, runtime, image)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=workspace_directory())
    parser.add_argument("--runtime", default="docker")
    parser.add_argument("--image", required=True)
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--repair-sharing", action="store_true")
    args = parser.parse_args()
    try:
        if args.prepare:
            prepare(args.directory, args.runtime, args.image, repair_sharing=args.repair_sharing)
        else:
            check_ready(args.directory, args.runtime, args.image)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print("GUIDE_IEI_SETUP_ERROR: " + str(exc), flush=True)
        return 1
    print(f"Container workspace verified: {args.directory}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
