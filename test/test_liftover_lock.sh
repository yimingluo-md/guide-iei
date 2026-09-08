#!/usr/bin/env bash
# Audit M19: the process-held guard also respects legacy mkdir locks beside
# the lifted VCF. A lock whose holder is gone is reclaimed; a lock
# held by a live run is honoured; a pid reused by an unrelated process is not
# mistaken for a live run. Exercised through the runner's GRCh37 dry-run
# path, which takes and releases the lock without needing any reference.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${HERE}/.." && pwd)"
cd "$ROOT"

WORK="$(mktemp -d)"
HOLDER=""
cleanup() {
    [[ -z "$HOLDER" ]] || kill "$HOLDER" 2>/dev/null || true
    rm -rf "$WORK"
}
trap cleanup EXIT
fail() { echo "FAIL: $*" >&2; exit 1; }

mkdir -p test/fixtures/vep_cache/homo_sapiens/113_GRCh38 test/fixtures/fasta \
         test/fixtures/loftee test/fixtures/custom test/fixtures/clinvar
touch test/fixtures/fasta/genome.fa.gz test/fixtures/loftee/human_ancestor.fa.gz \
      test/fixtures/loftee/loftee.sql test/fixtures/loftee/gerp.bw \
      test/fixtures/custom/repeatmasker.bed.gz \
      test/fixtures/clinvar/clinvar_latest.GRCh38.vcf.gz

OUT="$WORK/out/sample.vep.vcf.gz"
LOCK="$WORK/out/sample.grch37.lifted.GRCh38.vcf.gz.lock"
mkdir -p "$WORK/out"
# Launch the runner directly so $! identifies it, rather than a wrapper
# subshell whose termination would leave the real runner alive.
RUNNER=(bash "${ROOT}/scripts/run_annotation.sh" -i test/sample.grch37.vcf -o "$OUT"
        -c test/test.config.yaml --no-clinvar --dry-run --input-assembly GRCh37)

# --- 1. holder pid no longer exists: reclaimed -------------------------------
dead_pid=99999
while kill -0 "$dead_pid" 2>/dev/null; do dead_pid=$(( dead_pid - 1 )); done
mkdir "$LOCK"; echo "$dead_pid" > "$LOCK/pid"
"${RUNNER[@]}" >"$WORK/dead.log" 2>&1 || fail "run blocked by a lock whose holder is dead (see $WORK/dead.log)"
grep -F "reclaiming abandoned liftover lock" "$WORK/dead.log" >/dev/null || fail "dead-holder lock was not reported as reclaimed"
[[ ! -e "$LOCK" ]] || fail "lock left behind after the run"
echo "  dead holder: lock reclaimed, run proceeded, lock released"

# --- 2. pid reused by an unrelated live process: reclaimed --------------------
sleep 60 & HOLDER=$!
mkdir "$LOCK"; echo "$HOLDER" > "$LOCK/pid"
"${RUNNER[@]}" >"$WORK/reuse.log" 2>&1 || fail "run blocked by a lock whose pid belongs to an unrelated process"
grep -F "reclaiming abandoned liftover lock" "$WORK/reuse.log" >/dev/null || fail "reused-pid lock was not reclaimed"
kill "$HOLDER" 2>/dev/null || true; wait "$HOLDER" 2>/dev/null || true; HOLDER=""
[[ ! -e "$LOCK" ]] || fail "lock left behind after the reused-pid run"
echo "  reused pid: lock reclaimed"

# --- 3. live runner holds the lock: honoured until it exits ------------------
bash -c 'exec -a run_annotation.sh sleep 120' & HOLDER=$!
sleep 0.5
mkdir "$LOCK"; echo "$HOLDER" > "$LOCK/pid"
"${RUNNER[@]}" >"$WORK/live.log" 2>&1 & waiter=$!
sleep 8
if ! kill -0 "$waiter" 2>/dev/null; then
    wait "$waiter" || true
    fail "run did not wait for a live holder (see $WORK/live.log)"
fi
grep -F "is converting this input; waiting for its liftover" "$WORK/live.log" >/dev/null \
    || fail "waiting run did not report the live holder"
[[ "$(cat "$LOCK/pid")" == "$HOLDER" ]] || fail "live holder's lock was replaced"
kill "$HOLDER"; wait "$HOLDER" 2>/dev/null || true; HOLDER=""
if ! wait "$waiter"; then fail "run failed after the holder exited (see $WORK/live.log)"; fi
grep -F "reclaiming abandoned liftover lock" "$WORK/live.log" >/dev/null || fail "lock of the exited holder was not reclaimed"
[[ ! -e "$LOCK" ]] || fail "lock left behind after the waited run"
echo "  live holder: honoured while alive, reclaimed once it exited"

# --- 4. legacy lock without a pid: honoured while fresh, reclaimed when old --
mkdir "$LOCK"
"${RUNNER[@]}" >"$WORK/fresh.log" 2>&1 & waiter=$!
sleep 8
if ! kill -0 "$waiter" 2>/dev/null; then
    wait "$waiter" || true
    fail "a fresh pid-less lock should be honoured (see $WORK/fresh.log)"
fi
kill "$waiter" 2>/dev/null || true; wait "$waiter" 2>/dev/null || true
[[ -d "$LOCK" ]] || fail "fresh pid-less lock was removed"
python3 - "$LOCK" <<'PY'
import os, sys, time
stamp = time.time() - 7 * 3600
os.utime(sys.argv[1], (stamp, stamp))
PY
"${RUNNER[@]}" >"$WORK/old.log" 2>&1 || fail "run blocked by a hours-old pid-less lock (see $WORK/old.log)"
grep -F "reclaiming abandoned liftover lock" "$WORK/old.log" >/dev/null || fail "old pid-less lock was not reclaimed"
[[ ! -e "$LOCK" ]] || fail "lock left behind after the old-lock run"
echo "  pid-less lock: honoured while fresh, reclaimed after hours"

# --- 5. two waiters find the same stale lock: exactly one reclaims it --------
# Without serialised reclamation both judged the lock stale, and the second
# removal deleted the first waiter's freshly taken lock (reproduced 5/6 runs).
for i in 1 2 3 4 5 6; do
    rm -rf "$LOCK" "$LOCK.reclaim"
    mkdir "$LOCK"; echo "$dead_pid" > "$LOCK/pid"
    "${RUNNER[@]}" >"$WORK/race-a.log" 2>&1 & pa=$!
    "${RUNNER[@]}" >"$WORK/race-b.log" 2>&1 & pb=$!
    wait "$pa" || fail "race run A failed (iteration $i; see $WORK/race-a.log)"
    wait "$pb" || fail "race run B failed (iteration $i; see $WORK/race-b.log)"
    reclaims=$(( $(grep -c "reclaiming abandoned liftover lock" "$WORK/race-a.log" || true) \
              + $(grep -c "reclaiming abandoned liftover lock" "$WORK/race-b.log" || true) ))
    [[ "$reclaims" == "1" ]] || fail "iteration $i: $reclaims reclaims of one stale lock (expected exactly 1)"
    [[ ! -e "$LOCK" && ! -e "$LOCK.reclaim" ]] || fail "iteration $i: lock or reclaim mutex left behind"
done
echo "  concurrent waiters: one reclaim per stale lock, the other waits"

echo "PASS  liftover cache lock records its holder and reclaims abandoned locks"
