#!/usr/bin/env python3
"""Serialize a liftover command using an inherited, process-held file lock."""
from __future__ import annotations

import argparse
import fcntl
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time


def legacy_lock_stale(lock: Path) -> bool:
    """Respect live pre-upgrade mkdir locks; inspect them only under the guard."""
    try:
        pid = int((lock / "pid").read_text().strip())
    except (OSError, ValueError):
        return time.time() - lock.stat().st_mtime > 6 * 3600
    if pid <= 0:
        return time.time() - lock.stat().st_mtime > 6 * 3600
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    process = subprocess.run(
        ["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True,
    )
    command = process.stdout.strip()
    return bool(command and "run_annotation.sh" not in command)


def run_locked(lock: Path, command: list[str], *, timeout: float = 3600) -> int:
    deadline = time.monotonic() + timeout
    parent_pid = os.getppid()
    waiting_reported = False

    def check_parent() -> None:
        if os.getppid() != parent_pid:
            raise RuntimeError("annotation runner exited while waiting for its liftover lock")

    def wait() -> None:
        nonlocal waiting_reported
        check_parent()
        if not waiting_reported:
            print("another run is converting this input; waiting for its liftover...", file=sys.stderr, flush=True)
            waiting_reported = True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError(f"gave up waiting for {lock}; another liftover is still active")
        time.sleep(min(0.1, remaining))

    # Never unlink the guard: all contenders must lock the same inode. The
    # small file may persist; the OS releases the lock when its last holder
    # exits, so it needs neither a stale-owner check nor a reclaim mutex.
    with Path(str(lock) + ".guard").open("a+b") as guard:
        while True:
            try:
                fcntl.flock(guard.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                wait()

        while lock.exists():
            check_parent()
            try:
                stale = legacy_lock_stale(lock)
            except FileNotFoundError:
                continue  # A pre-upgrade holder released its directory.
            if stale:
                check_parent()
                print(f"reclaiming abandoned liftover lock: {lock}", file=sys.stderr, flush=True)
                shutil.rmtree(lock)
                break
            wait()

        # PID is informational only; flock determines ownership. Do not
        # recreate the legacy directory: a crash between mkdir and writing
        # its owner would leave ambiguous, PID-less metadata again.
        check_parent()
        guard.seek(0)
        guard.truncate()
        guard.write(f"{os.getpid()}\n".encode())
        guard.flush()
        # The child also holds the guard. Killing this supervisor cannot
        # unlock the cache while the liftover process is still writing it.
        result = subprocess.call(command, pass_fds=(guard.fileno(),))
        return result if result >= 0 else 128 - result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=3600)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command
    if command[:1] == ["--"]:
        command = command[1:]
    if not command:
        parser.error("a liftover command is required after --")
    try:
        return run_locked(args.lock, command, timeout=args.timeout)
    except (OSError, RuntimeError) as error:
        print(f"ERROR: liftover lock failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
