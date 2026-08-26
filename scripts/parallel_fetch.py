#!/usr/bin/env python3
"""Resumable validated HTTP range downloader for very large references."""
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


def curl_url_config(url: str) -> str:
    """Pass a possibly private URL to curl without exposing it in argv."""
    if not url or any(character in url for character in ("\r", "\n", "\0")):
        raise ValueError("download URL must be one non-empty line")
    escaped = url.replace("\\", "\\\\").replace('"', '\\"')
    return f'url = "{escaped}"\n'


def remote_metadata(url: str) -> tuple[int, str, str]:
    # Some archives (notably ENCODE) redirect downloads to a signed object URL
    # whose signature is valid for GET but not HEAD.  A one-byte ranged GET
    # follows that redirect and reports both the object size and range support
    # without downloading the file.
    result = subprocess.run(
        [
            "curl", "-fsSL", "--max-time", "60", "--range", "0-0",
            "--dump-header", "-", "--output", "/dev/null", "--config", "-",
        ],
        check=True,
        capture_output=True,
        text=True,
        input=curl_url_config(url),
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
    modified = re.findall(
        r'^last-modified:\s*(.+?)\s*$',
        result.stdout,
        flags=re.IGNORECASE | re.MULTILINE,
    )
    return (
        int(ranges[-1]),
        etags[-1] if etags else "",
        modified[-1] if modified else "",
    )


class RemoteChangedError(RuntimeError):
    """The remote file changed during the download — retrying the same
    range cannot succeed; the whole download must restart."""


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


def load_resume_state(state_path: Path, partial: Path, url: str,
                      total: int, etag: str | None, chunk_size: int,
                      last_modified: str | None = None) -> set[int]:
    """Ranges safe to skip on resume.

    The completed set is only trustworthy while the partial file it
    describes still exists at full size: trusting the state after the
    partial was deleted recreated a zero-filled sparse file and published
    it as a finished download. Metadata mismatches remain hard errors (the
    remote changed); a missing or wrong-size partial just restarts.

    Last-Modified is part of the identity: without it, a remote that
    changed while keeping the same size (and serving no ETag) let a
    resume COMBINE ranges of two different versions into one published
    file — stamped afterwards with the new version's metadata, so the
    mix was never detectable again. A state written before the field
    existed cannot prove which version its ranges came from, so when the
    remote NOW reports a Last-Modified the state is discarded and the
    download restarts — one-time, self-extinguishing. A bare presence
    flap (one side reports a date, the other does not) also restarts
    rather than hard-erroring: absence is unknown, not evidence of
    change.
    """
    if not state_path.exists():
        return set()
    state = json.loads(state_path.read_text())
    expected = {"url": url, "size": total, "etag": etag, "chunk_size": chunk_size}
    if any(state.get(key) != value for key, value in expected.items()):
        raise RuntimeError(f"resume metadata differs; move aside {state_path} and {partial}")
    stored_modified = state.get("last_modified") if "last_modified" in state else None
    if stored_modified is None and last_modified:
        print("resume state predates remote-version tracking and the remote "
              "reports one; restarting the download from scratch", flush=True)
        state_path.unlink(missing_ok=True)
        partial.unlink(missing_ok=True)
        return set()
    if stored_modified is not None and bool(stored_modified) != bool(last_modified):
        print("the remote's version metadata changed shape; restarting the "
              "download from scratch", flush=True)
        state_path.unlink(missing_ok=True)
        partial.unlink(missing_ok=True)
        return set()
    if stored_modified and last_modified and stored_modified != last_modified:
        raise RuntimeError(f"resume metadata differs; move aside {state_path} and {partial}")
    if not partial.exists() or partial.stat().st_size != total:
        print("resume state found but the partial file is missing or the wrong "
              "size; restarting the download from scratch", flush=True)
        state_path.unlink(missing_ok=True)
        partial.unlink(missing_ok=True)
        return set()
    return set(state.get("completed", []))


def migrate_resume_url_key(state_path: Path, source_url: str,
                           state_url_key: str) -> None:
    """Replace a persisted access URL with a caller-provided stable key.

    Registration links can expire while a large download is incomplete. When
    the caller explicitly supplies a non-secret identity, retain the completed
    ranges and let the normal size/ETag/Last-Modified checks below decide
    whether they still belong to the current remote object.
    """
    if source_url == state_url_key or not state_path.is_file():
        return
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    old_url = state.get("url")
    if not isinstance(old_url, str) or not old_url.startswith(("http://", "https://")):
        return
    state["url"] = state_url_key
    temporary = Path(f"{state_path}.migrate.tmp")
    temporary.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
    temporary.replace(state_path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("url")
    parser.add_argument("output")
    parser.add_argument(
        "--url-file",
        help=(
            "read the source URL from a private file; pass '-' as the positional "
            "URL so credentials never appear in process arguments"
        ),
    )
    parser.add_argument(
        "--state-url-key",
        help=(
            "non-secret stable URL identity stored in resume metadata; useful "
            "for registration links whose access token may be renewed"
        ),
    )
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

    if args.url_file:
        if args.url != "-":
            raise SystemExit("pass '-' as URL when --url-file is used")
        source_url = Path(args.url_file).read_text(encoding="utf-8").strip()
    else:
        source_url = args.url.strip()
    try:
        curl_url_config(source_url)
    except ValueError as error:
        raise SystemExit(str(error)) from error
    state_url_key = (args.state_url_key or source_url).strip()
    if not state_url_key:
        raise SystemExit("--state-url-key cannot be empty")

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

    total, etag, remote_modified = remote_metadata(source_url)
    version_sidecar = Path(f"{output}.etag")

    def stored_version() -> tuple[str, str]:
        if not version_sidecar.is_file():
            return "", ""
        text = version_sidecar.read_text().strip()
        try:
            data = json.loads(text)
        except ValueError:
            return text, ""  # first-generation sidecar: bare ETag line
        return str(data.get("etag") or ""), str(data.get("last_modified") or "")

    def strip_weak(value: str) -> str:
        return value[2:] if value.startswith("W/") else value

    if output.is_file() and output.stat().st_size == total:
        refresh_reason = ""
        if (expected_sum and bsd_sum(output) != expected_sum) or (
            expected_md5 and md5(output) != expected_md5
        ):
            # A size-matching existing file that fails the caller's checksum
            # is a stale same-size file (crashed earlier run, later release
            # of identical size): re-download it. Hard-erroring here left
            # the state permanently wedged; the strict failure remains for
            # freshly downloaded bytes below.
            refresh_reason = (
                f"{output} matches the remote size but fails the expected "
                "checksum — a stale same-size file; fetching the current one"
            )
        elif not (expected_sum or expected_md5):
            stored_etag, stored_modified = stored_version()
            modified_known = bool(stored_modified and remote_modified)
            modified_changed = modified_known and stored_modified != remote_modified
            etag_changed = bool(
                stored_etag and etag
                and strip_weak(stored_etag) != strip_weak(etag)
            )
            # Load-balanced mirrors rotate ETags per node for one unchanged
            # file, so an AGREEING Last-Modified vetoes an ETag-only
            # refresh (no redownload-forever loop); a changed Last-Modified
            # refreshes even when the server sends no ETag at all.
            if modified_changed:
                refresh_reason = (
                    f"{output} matches the remote size but the remote "
                    "changed since it was downloaded (Last-Modified moved); "
                    "fetching the current file"
                )
            elif etag_changed and not modified_known:
                refresh_reason = (
                    f"{output} matches the remote size but was downloaded "
                    "under a different remote ETag; fetching the current file"
                )
        if not refresh_reason:
            if expected_sum or expected_md5:
                print(
                    f"{display_progress(100):5.1f}%  verified {output} "
                    f"({total} bytes)",
                    flush=True,
                )
            else:
                # "Verified" is an integrity claim; without an upstream
                # checksum only the size was compared.
                print(
                    f"{display_progress(100):5.1f}%  {output} matches the "
                    f"remote size ({total} bytes); no upstream checksum "
                    "available to verify content",
                    flush=True,
                )
            os.close(lock_descriptor)
            lock_path.unlink(missing_ok=True)
            return 0
        print(refresh_reason, flush=True)
        # The completed output is being replaced: resume state left by an
        # interrupted earlier refresh of a DIFFERENT remote version is
        # dead, and load_resume_state would otherwise hard-error on it
        # forever. State matching the current metadata stays resumable.
        state_file = Path(f"{output}.ranges.json")
        if state_file.exists():
            try:
                prior = json.loads(state_file.read_text())
            except ValueError:
                prior = {}
            if (
                prior.get("url") != state_url_key
                or prior.get("size") != total
                or prior.get("etag") != etag
                or ("last_modified" in prior
                    and prior.get("last_modified") != remote_modified)
            ):
                state_file.unlink(missing_ok=True)
                Path(f"{output}.parallel").unlink(missing_ok=True)

    partial = Path(f"{output}.parallel")
    state_path = Path(f"{output}.ranges.json")
    chunk_size = args.chunk_mib * 1024 * 1024
    chunks = [(start, min(total - 1, start + chunk_size - 1)) for start in range(0, total, chunk_size)]
    if args.state_url_key:
        migrate_resume_url_key(state_path, source_url, state_url_key)
    completed = load_resume_state(state_path, partial, state_url_key, total, etag,
                                  chunk_size, remote_modified)
    state = {"url": state_url_key, "size": total, "etag": etag,
             "last_modified": remote_modified, "chunk_size": chunk_size,
             "completed": sorted(completed)}

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
                # No --max-time: a healthy-but-slow transfer of a large range
                # must be allowed to finish. Ensembl/EBI often serve well under
                # 0.5 MiB/s per connection, so a wall-clock cap kills chunks
                # that are making real progress, discards their bytes, and
                # retries forever. Stalls and unacceptably slow connections are
                # already policed by --speed-limit/--speed-time, dead servers
                # by --connect-timeout.
                result = subprocess.run(
                    [
                        "curl", "-fsSL", "--connect-timeout", "30",
                        "--speed-limit", "10240", "--speed-time", "20", "--retry", "2",
                        "--range", f"{start}-{end}", "--output", str(range_file),
                        "--dump-header", str(header_file),
                        "--write-out", "%{http_code}", "--config", "-",
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                    input=curl_url_config(source_url),
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
                # Every range must come from the SAME remote version the
                # initial probe saw, or a remote update mid-download
                # assembles a mixed-version file that passes every size
                # check. Same rule as the acceptance branch: an agreeing
                # Last-Modified vetoes an ETag-only difference so
                # rotating per-node ETags on load-balanced mirrors do not
                # fail healthy transfers.
                range_etags = re.findall(
                    r'^etag:\s*(.+?)\s*$', headers,
                    flags=re.IGNORECASE | re.MULTILINE,
                )
                range_modified = re.findall(
                    r'^last-modified:\s*(.+?)\s*$', headers,
                    flags=re.IGNORECASE | re.MULTILINE,
                )
                range_etag = range_etags[-1] if range_etags else ""
                range_lm = range_modified[-1] if range_modified else ""
                lm_known = bool(range_lm and remote_modified)
                lm_moved = lm_known and range_lm != remote_modified
                etag_moved = bool(
                    range_etag and etag
                    and strip_weak(range_etag) != strip_weak(etag)
                )
                # An ETag-only difference with no Last-Modified to
                # arbitrate is ambiguous: rotating per-node ETags on
                # load-balanced mirrors look exactly like a changed
                # remote. When the caller supplied an end-to-end checksum
                # the final verification arbitrates, so ambiguity must
                # not fail a healthy transfer; without one, restarting is
                # the only safe reading.
                if lm_moved or (
                    etag_moved and not lm_known
                    and not (expected_sum or expected_md5)
                ):
                    raise RemoteChangedError(
                        f"range {index}: the remote file changed during the "
                        "download (its ETag/Last-Modified no longer match "
                        "the initial probe); run the download again to "
                        "fetch the current version"
                    )
                received = range_file.stat().st_size
                if received != expected_length:
                    raise RuntimeError(f"range {index}: received {received} of {expected_length} bytes")
                offset = start
                with range_file.open("rb") as source:
                    while block := source.read(1024 * 1024):
                        # os.pwrite may write fewer bytes than requested —
                        # routine on network/FUSE filesystems like OneDrive.
                        # Advancing by the requested size would leave a hole
                        # and shift every later block of this range, and the
                        # size checks above validate the temp file, not the
                        # assembled output.
                        view = memoryview(block)
                        while view:
                            written = os.pwrite(descriptor, view, offset)
                            if written <= 0:
                                raise RuntimeError(
                                    f"range {index}: pwrite returned {written} at offset {offset}"
                                )
                            offset += written
                            view = view[written:]
                range_file.unlink()
                header_file.unlink(missing_ok=True)
                return index
            except RemoteChangedError:
                # A changed remote cannot be retried into agreement —
                # burning five attempts per chunk just delays the message.
                range_file.unlink(missing_ok=True)
                header_file.unlink(missing_ok=True)
                raise
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
                try:
                    index = future.result()
                except RemoteChangedError:
                    # Nothing queued behind this can succeed either.
                    executor.shutdown(wait=False, cancel_futures=True)
                    raise
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
    def _discard_proven_bad(reason: str):
        # The assembled bytes failed verification: keeping them (and the
        # state that vouches for their ranges) wedged every retry into
        # refetching nothing and failing the same check forever.
        partial.unlink(missing_ok=True)
        state_path.unlink(missing_ok=True)
        raise RuntimeError(
            f"{reason}; the partial download was discarded — run again to "
            "fetch the current file"
        )

    if expected_sum:
        actual_sum = bsd_sum(partial)
        if actual_sum != expected_sum:
            _discard_proven_bad(
                f"completed download failed checksum: expected "
                f"{expected_sum}, received {actual_sum}"
            )
    if expected_md5:
        actual_md5 = md5(partial)
        if actual_md5 != expected_md5:
            _discard_proven_bad(
                f"completed download failed MD5: expected {expected_md5}, "
                f"received {actual_md5}"
            )
    partial.replace(output)
    # Record which remote version these bytes are: checksum-less callers can
    # then detect a changed remote on later runs instead of accepting any
    # same-size file.
    if etag or remote_modified:
        version_sidecar.write_text(
            json.dumps({"etag": etag, "last_modified": remote_modified}) + "\n"
        )
    else:
        version_sidecar.unlink(missing_ok=True)
    state_path.unlink(missing_ok=True)
    os.close(lock_descriptor)
    lock_path.unlink(missing_ok=True)
    print(f"complete: {output}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
