#!/usr/bin/env python3
"""Resumable validated HTTP range downloader for very large public references."""
from __future__ import annotations

import argparse
import concurrent.futures
import fcntl
import hashlib
import json
import os
import re
import subprocess
import threading
import time
from pathlib import Path


def remote_metadata(url: str) -> tuple[int, str]:
    # Some archives (notably ENCODE) redirect downloads to a signed object URL
    # whose signature is valid for GET but not HEAD.  A one-byte ranged GET
    # follows that redirect and reports both the object size and range support
    # without downloading the file.
    result = subprocess.run(
        [
            "curl", "-fsSL", "--max-time", "60", "--range", "0-0",
            "--dump-header", "-", "--output", "/dev/null", url,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    ranges = re.findall(
        r"^content-range:\s*bytes\s+0-0/(\d+)\s*$",
        result.stdout,
        flags=re.IGNORECASE | re.MULTILINE,
    )
    if not ranges:
        raise RuntimeError("server did not honor a one-byte range request")
    etags = re.findall(
        r'^etag:\s*(.+?)\s*$',
        result.stdout,
        flags=re.IGNORECASE | re.MULTILINE,
    )
    return int(ranges[-1]), etags[-1] if etags else ""


def bsd_sum(path: Path) -> tuple[int, int]:
    """Return the checksum and 1 KiB block count emitted by BSD/POSIX `sum`."""
    result = subprocess.run(["sum", str(path)], check=True, capture_output=True, text=True)
    fields = result.stdout.split()
    if len(fields) < 2:
        raise RuntimeError(f"could not parse checksum output for {path}")
    return int(fields[0]), int(fields[1])


def md5(path: Path) -> str:
    digest = hashlib.md5()
    with path.open("rb") as handle:
        while block := handle.read(8 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("url")
    parser.add_argument("output")
    parser.add_argument("--connections", type=int, default=8)
    parser.add_argument("--chunk-mib", type=int, default=128)
    parser.add_argument(
        "--sum-check",
        metavar="CHECKSUM:BLOCKS",
        help="verify the completed file against an Ensembl CHECKSUMS entry",
    )
    parser.add_argument(
        "--md5",
        help="verify the completed file against this hexadecimal MD5 digest",
    )
    parser.add_argument("--progress-start", type=float, default=0.0)
    parser.add_argument("--progress-scale", type=float, default=100.0)
    args = parser.parse_args()

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    expected_sum: tuple[int, int] | None = None
    if args.sum_check:
        try:
            checksum, blocks = args.sum_check.split(":", 1)
            expected_sum = int(checksum), int(blocks)
        except ValueError as error:
            raise SystemExit("--sum-check must be CHECKSUM:BLOCKS") from error
    expected_md5 = (args.md5 or "").strip().lower()
    if expected_md5 and not re.fullmatch(r"[0-9a-f]{32}", expected_md5):
        raise SystemExit("--md5 must be a 32-character hexadecimal digest")

    def display_progress(percent: float) -> float:
        return args.progress_start + (args.progress_scale * percent / 100.0)

    # A file lock prevents two invocations from sharing the .parallel file.
    # The lock file may remain on disk safely; flock itself is process-scoped.
    lock_path = Path(f"{output}.lock")
    lock_descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(lock_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as error:
        os.close(lock_descriptor)
        raise RuntimeError(f"another downloader is already writing {output}") from error
    os.ftruncate(lock_descriptor, 0)
    os.write(lock_descriptor, f"{os.getpid()}\n".encode())

    total, etag = remote_metadata(args.url)
    if output.is_file() and output.stat().st_size == total:
        if expected_sum and bsd_sum(output) != expected_sum:
            raise RuntimeError(
                f"existing file has the expected size but failed checksum: {output}"
            )
        if expected_md5 and md5(output) != expected_md5:
            raise RuntimeError(
                f"existing file has the expected size but failed MD5: {output}"
            )
        print(
            f"{display_progress(100):5.1f}%  verified {output} ({total} bytes)",
            flush=True,
        )
        os.close(lock_descriptor)
        lock_path.unlink(missing_ok=True)
        return 0

    partial = Path(f"{output}.parallel")
    state_path = Path(f"{output}.ranges.json")
    chunk_size = args.chunk_mib * 1024 * 1024
    chunks = [(start, min(total - 1, start + chunk_size - 1)) for start in range(0, total, chunk_size)]
    completed: set[int] = set()
    if state_path.exists():
        state = json.loads(state_path.read_text())
        expected = {"url": args.url, "size": total, "etag": etag, "chunk_size": chunk_size}
        if any(state.get(key) != value for key, value in expected.items()):
            raise RuntimeError(f"resume metadata differs; move aside {state_path} and {partial}")
        completed = set(state.get("completed", []))
    else:
        state = {"url": args.url, "size": total, "etag": etag, "chunk_size": chunk_size, "completed": []}

    descriptor = os.open(partial, os.O_CREAT | os.O_RDWR, 0o644)
    os.ftruncate(descriptor, total)
    lock = threading.Lock()
    started = time.monotonic()
    completed_bytes = sum(chunks[index][1] - chunks[index][0] + 1 for index in completed)

    def save_state() -> None:
        state["completed"] = sorted(completed)
        temporary = Path(f"{state_path}.tmp")
        temporary.write_text(json.dumps(state, sort_keys=True))
        temporary.replace(state_path)

    def download(index: int) -> int:
        start, end = chunks[index]
        expected_length = end - start + 1
        range_file = Path(f"{partial}.range-{index}")
        header_file = Path(f"{range_file}.headers")
        for attempt in range(1, 6):
            try:
                result = subprocess.run(
                    [
                        "curl", "-fsSL", "--connect-timeout", "30", "--max-time", "300",
                        "--speed-limit", "10240", "--speed-time", "20", "--retry", "2",
                        "--range", f"{start}-{end}", "--output", str(range_file),
                        "--dump-header", str(header_file),
                        "--write-out", "%{http_code}", args.url,
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                if result.stdout.strip() != "206":
                    raise RuntimeError(f"range {index}: expected HTTP 206, received {result.stdout.strip()}")
                headers = header_file.read_text(errors="replace")
                content_ranges = re.findall(
                    r"^content-range:\s*bytes\s+(\d+)-(\d+)/(\d+)\s*$",
                    headers,
                    flags=re.IGNORECASE | re.MULTILINE,
                )
                expected_range = (str(start), str(end), str(total))
                if not content_ranges or content_ranges[-1] != expected_range:
                    actual = content_ranges[-1] if content_ranges else "missing"
                    raise RuntimeError(
                        f"range {index}: expected Content-Range {expected_range}, received {actual}"
                    )
                received = range_file.stat().st_size
                if received != expected_length:
                    raise RuntimeError(f"range {index}: received {received} of {expected_length} bytes")
                offset = start
                with range_file.open("rb") as source:
                    while block := source.read(1024 * 1024):
                        os.pwrite(descriptor, block, offset)
                        offset += len(block)
                range_file.unlink()
                header_file.unlink(missing_ok=True)
                return index
            except Exception:
                range_file.unlink(missing_ok=True)
                header_file.unlink(missing_ok=True)
                if attempt == 5:
                    raise
                time.sleep(2**attempt)
        raise AssertionError("unreachable")

    pending = [index for index in range(len(chunks)) if index not in completed]
    print(
        f"downloading {total / 1024**3:.1f} GiB in {len(chunks)} validated ranges "
        f"with {args.connections} connections ({len(completed)} already complete)",
        flush=True,
    )
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.connections) as executor:
            futures = {executor.submit(download, index): index for index in pending}
            for future in concurrent.futures.as_completed(futures):
                index = future.result()
                with lock:
                    completed.add(index)
                    start, end = chunks[index]
                    completed_bytes += end - start + 1
                    save_state()
                    elapsed = max(time.monotonic() - started, 0.001)
                    percent = display_progress(100 * completed_bytes / total)
                    speed = (completed_bytes - sum(chunks[i][1] - chunks[i][0] + 1 for i in completed if i not in pending)) / elapsed
                    print(f"{percent:5.1f}%  {completed_bytes / 1024**3:5.1f} GiB  {speed / 1024**2:5.1f} MiB/s", flush=True)
    finally:
        os.close(descriptor)

    if len(completed) != len(chunks):
        raise RuntimeError("not all ranges completed")
    if expected_sum:
        actual_sum = bsd_sum(partial)
        if actual_sum != expected_sum:
            raise RuntimeError(
                f"completed download failed checksum: expected {expected_sum}, received {actual_sum}"
            )
    if expected_md5:
        actual_md5 = md5(partial)
        if actual_md5 != expected_md5:
            raise RuntimeError(
                "completed download failed MD5: "
                f"expected {expected_md5}, received {actual_md5}"
            )
    partial.replace(output)
    state_path.unlink(missing_ok=True)
    os.close(lock_descriptor)
    lock_path.unlink(missing_ok=True)
    print(f"complete: {output}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
