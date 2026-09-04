"""Live progress for running annotation jobs.

VEP prints nothing usable about its progress in offline VCF mode, but the
pipeline already knows both ends of the bar: the pre-filter step logs the
exact variant count VEP is about to process, and VEP's output is BGZF —
block-structured compression whose complete blocks can be decompressed
incrementally as the file grows. Counting records in newly appended
blocks gives an exact numerator for O(new bytes) per poll, with no
change to VEP itself.

Everything here is computed lazily, only when the UI polls a running
job; an unwatched run costs nothing.
"""
from __future__ import annotations

import re
import threading
import time
import zlib
from pathlib import Path

_BGZF_MAGIC = b"\x1f\x8b\x08\x04"


class BgzfRecordCounter:
    """Exact record count of a growing BGZF VCF, advanced incrementally.

    Only COMPLETE blocks are consumed; a partially written trailing block
    is left for the next poll. Header lines (leading '#') are excluded
    from the record count.
    """

    # Per-call decompression budget: a first poll against an
    # already-large output must not decompress gigabytes inside one HTTP
    # request — the bar ramps up over a few polls instead.
    BYTE_BUDGET = 32 * 1024 * 1024

    def __init__(self, path: Path):
        self.path = Path(path)
        self.offset = 0
        self.records = 0
        self._at_line_start = True
        self._in_header = True

    def _reset(self) -> None:
        self.offset = 0
        self.records = 0
        self._at_line_start = True
        self._in_header = True

    def advance(self) -> int:
        """Consume newly completed blocks; return the current record count."""
        try:
            size = self.path.stat().st_size
        except OSError:
            return self.records
        if size < self.offset:
            # The file shrank: VEP truncated a stale output from a
            # previous run to the same path. Recount from the start so
            # the bar self-corrects instead of freezing on stale numbers.
            self._reset()
        if size <= self.offset:
            return self.records
        consumed = 0
        with self.path.open("rb") as handle:
            while self.offset + 18 <= size and consumed < self.BYTE_BUDGET:
                handle.seek(self.offset)
                header = handle.read(18)
                if len(header) < 18 or header[:4] != _BGZF_MAGIC:
                    break  # not BGZF (or torn write): stop, retry next poll
                xlen = int.from_bytes(header[10:12], "little")
                extra = header[12:12 + max(0, min(xlen, 6))]
                if xlen != 6:
                    # Nonstandard extra layout: read the full extra field
                    # and locate the BC subfield.
                    handle.seek(self.offset + 12)
                    extra = handle.read(xlen)
                block_size = None
                cursor = 0
                while cursor + 4 <= len(extra):
                    subfield_length = int.from_bytes(
                        extra[cursor + 2:cursor + 4], "little"
                    )
                    if extra[cursor:cursor + 2] == b"BC" and subfield_length == 2:
                        block_size = int.from_bytes(
                            extra[cursor + 4:cursor + 6], "little"
                        ) + 1
                        break
                    cursor += 4 + subfield_length
                if block_size is None:
                    break
                if self.offset + block_size > size:
                    break  # trailing block still being written
                payload_start = self.offset + 12 + xlen
                payload_length = block_size - 12 - xlen - 8
                # A corrupted BSIZE/XLEN would make this negative — and a
                # read(-1) would slurp the rest of the file; both degrade
                # to "retry next poll" like any other torn block.
                if payload_length <= 0:
                    break
                handle.seek(payload_start)
                compressed = handle.read(payload_length)
                if len(compressed) != payload_length:
                    break  # shrank between stat and read
                try:
                    data = zlib.decompressobj(-15).decompress(compressed)
                except zlib.error:
                    break
                self._count(data)
                self.offset += block_size
                consumed += block_size
        return self.records

    def _count(self, data: bytes) -> None:
        if not data:
            return
        if self._in_header:
            # Slow path only while inside the VCF header (tens of KB).
            for line_start, byte in self._line_starts(data):
                if byte != 0x23:  # '#'
                    self._in_header = False
                    remaining = data[line_start:]
                    self.records += remaining.count(b"\n")
                    self._at_line_start = remaining.endswith(b"\n")
                    return
            self._at_line_start = data.endswith(b"\n")
            return
        self.records += data.count(b"\n")
        self._at_line_start = data.endswith(b"\n")

    def _line_starts(self, data: bytes):
        if self._at_line_start and data:
            yield 0, data[0]
        position = data.find(b"\n")
        while position != -1 and position + 1 < len(data):
            yield position + 1, data[position + 1]
            position = data.find(b"\n", position + 1)


# Ordered pipeline stages; each starts when its pattern first appears in
# the job log. These match the log lines run_annotation.sh already emits.
STAGES: tuple[tuple[str, str, str], ...] = (
    ("preflight", "Checking annotation setup", r"Job .* started"),
    ("input", "Detecting genome assembly", r"preflight container checks passed|input assembly:"),
    ("liftover", "GRCh37 → GRCh38 liftover", r"running BCFtools/liftover|reusing cached hg19"),
    (
        "prefilter",
        "Filtering input records",
        r"coding BED missing; building it|region restriction (?:ON|OFF)"
        r"|whole-genome input|PASS filter OFF|input pre-filter \(|input pre-filter OFF",
    ),
    ("clinvar", "Refreshing ClinVar", r"=== fetching latest ClinVar ===|=== building ClinVar aa-match reference ==="),
    (
        "protein_catalogs",
        "Preparing clinical protein evidence",
        r"=== building (?:ClinVar|ClinGen|GenIA) clinical protein-match catalog ===",
    ),
    ("vep", "VEP annotation", r"=== VEP invocation ==="),
    ("ptc50", "LOFTEE 50-bp recheck", r"=== LOFTEE frameshift PTC 50-bp recomputation ==="),
    ("haplotypes", "Haplotype evidence", r"=== sample-specific Haplosaurus post-processing ==="),
    (
        "aa_match",
        "Clinical protein matching",
        r"=== clinical protein residue/change post-processing ==="
        r"|=== ClinVar amino-acid-match post-processing ===",
    ),
    ("clingen", "ClinGen assertions", r"=== ClinGen Evidence Repository"),
    ("qc", "Coverage report", r"=== annotation coverage report ==="),
    ("done", "Complete", r"\] DONE\."),
)
_DENOMINATOR = re.compile(
    r"input pre-filter \([^)]*\): \d+ -> (\d+) variants"
    r"|input pre-filter OFF: (\d+) variants"
)
_VEP_FINISHED = re.compile(r"VEP finished")
_RESOLVED_ASSEMBLY = re.compile(
    r"input assembly:\s*requested=[^,\s]+,\s*resolved=(GRCh37|GRCh38)"
)


class _JobState:
    def __init__(self):
        self.log_offset = 0
        self.log_carry = b""
        self.stage_index = 0
        self.variants_total: int | None = None
        self.vep_finished = False
        self.counter: BgzfRecordCounter | None = None
        self.baseline: tuple[float, int] | None = None
        self.vep_seen_wall: float | None = None
        self.liftover_seen = False
        self.resolved_assembly: str | None = None
        self.last_access = 0.0
        self.lock = threading.Lock()


class JobProgressTracker:
    """Per-job progress snapshots, derived from the log and output file."""

    # More concurrent tracked jobs than this means something is wrong;
    # evicting the least-recently polled state bounds memory for jobs
    # that finish while nobody is watching.
    MAX_TRACKED = 32

    def __init__(self):
        self._states: dict[str, _JobState] = {}
        self._lock = threading.Lock()

    def snapshot(self, job: dict) -> dict | None:
        if job.get("status") != "running":
            with self._lock:
                self._states.pop(job.get("id", ""), None)
            return None
        # The registry lock only guards the dict; the per-job lock covers
        # the file IO, so one job's slow disk cannot stall every other
        # job's poll (outputs may live on external drives).
        with self._lock:
            state = self._states.setdefault(job["id"], _JobState())
            state.last_access = time.monotonic()
            if len(self._states) > self.MAX_TRACKED:
                oldest = min(
                    (key for key in self._states if key != job["id"]),
                    key=lambda key: self._states[key].last_access,
                    default=None,
                )
                if oldest is not None:
                    self._states.pop(oldest, None)
        with state.lock:
            self._consume_log(state, job.get("log_path"))
            stage_id = STAGES[state.stage_index][0]
            stage_label = STAGES[state.stage_index][1]
            if stage_id == "input" and state.resolved_assembly is not None:
                stage_label = (
                    "Preparing GRCh37 input for liftover"
                    if state.resolved_assembly == "GRCh37"
                    else "Preparing GRCh38 input"
                )
            if stage_id == "vep" and state.vep_seen_wall is None:
                state.vep_seen_wall = time.time()
            done = None
            percent = None
            eta = None
            if state.variants_total and stage_id == "vep" and not state.vep_finished:
                if state.counter is None and job.get("output_path"):
                    # A rerun to the same output path leaves the PREVIOUS
                    # run's completed file in place until VEP truncates it
                    # (seconds after the invocation marker). Counting that
                    # stale file flashed finished-looking numbers, so the
                    # counter only attaches once the output was written at
                    # or after the moment this run's VEP stage began.
                    output = Path(job["output_path"])
                    try:
                        fresh = output.stat().st_mtime >= (state.vep_seen_wall or 0) - 1
                    except OSError:
                        fresh = False
                    if fresh:
                        state.counter = BgzfRecordCounter(output)
                if state.counter is not None:
                    done = state.counter.advance()
                    percent = min(99.0, 100.0 * done / state.variants_total)
                    now = time.monotonic()
                    if state.baseline is None:
                        state.baseline = (now, done)
                    base_time, base_done = state.baseline
                    elapsed = now - base_time
                    if elapsed >= 10 and done > base_done:
                        rate = (done - base_done) / elapsed
                        if rate > 0:
                            eta = max(0.0, (state.variants_total - done) / rate)
            past_vep = state.stage_index > _stage_index("vep") or state.vep_finished
            return {
                "stage": stage_id,
                "stage_label": stage_label,
                "stages": [
                    {
                        "id": identifier,
                        "label": label,
                        "state": (
                            "done" if index < state.stage_index
                            or (identifier == "vep" and state.vep_finished)
                            else "active" if index == state.stage_index
                            else "pending"
                        ),
                    }
                    for index, (identifier, label, _pattern) in enumerate(STAGES)
                    # The liftover stage appears for declared GRCh37
                    # inputs, and for auto-detected ones once the log
                    # shows the liftover actually running.
                    if identifier != "liftover"
                    or job.get("input_assembly") == "GRCh37"
                    or state.resolved_assembly == "GRCh37"
                    or state.liftover_seen
                ],
                "resolved_assembly": state.resolved_assembly,
                "variants_total": state.variants_total,
                "variants_done": done,
                "vep_percent": percent,
                "eta_seconds": eta,
                "post_processing": bool(past_vep and stage_id != "done"),
            }

    def _consume_log(self, state: _JobState, log_path) -> None:
        if not log_path:
            return
        path = Path(log_path)
        try:
            size = path.stat().st_size
        except OSError:
            return
        if size <= state.log_offset:
            return
        with path.open("rb") as handle:
            handle.seek(state.log_offset)
            chunk = handle.read(size - state.log_offset)
        state.log_offset = size
        text = (state.log_carry + chunk)
        lines = text.split(b"\n")
        state.log_carry = lines.pop() if lines else b""
        for raw in lines:
            line = raw.decode("utf-8", "replace")
            if state.variants_total is None:
                match = _DENOMINATOR.search(line)
                if match:
                    state.variants_total = int(match.group(1) or match.group(2))
            if not state.vep_finished and _VEP_FINISHED.search(line):
                state.vep_finished = True
            if state.resolved_assembly is None:
                assembly_match = _RESOLVED_ASSEMBLY.search(line)
                if assembly_match:
                    state.resolved_assembly = assembly_match.group(1)
            if not state.liftover_seen and re.search(STAGES[_stage_index("liftover")][2], line):
                state.liftover_seen = True
            for index in range(state.stage_index + 1, len(STAGES)):
                if re.search(STAGES[index][2], line):
                    state.stage_index = index


def _stage_index(identifier: str) -> int:
    for index, (candidate, _label, _pattern) in enumerate(STAGES):
        if candidate == identifier:
            return index
    return -1
