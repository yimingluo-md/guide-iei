#!/usr/bin/env bash
# Verify container fallback rewrites absolute paths and supports both CLI styles.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${HERE}/.." && pwd)"
source "${ROOT}/scripts/lib.sh"

WORK="$(mktemp -d)"
WORK="$(cd "$WORK" && pwd -P)"
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$WORK/bin" "$WORK/input dir" "$WORK/output dir" "$WORK/bed dir"
touch "$WORK/input dir/in.vcf.gz" "$WORK/bed dir/regions.bed.gz"

cat > "$WORK/bin/docker" <<'SH'
#!/usr/bin/env bash
if [[ "${1:-}" == "image" && "${2:-}" == "inspect" ]]; then
    [[ "${FAKE_IMAGE_MISSING:-0}" != "1" ]]
    exit
fi
printf '%s\n' "$@" > "$CAPTURE"
if [[ "${1:-}" == "run" && -n "${FAKE_RUN_RC:-}" ]]; then
    printf 'run\n' >> "$RUN_COUNT"
    exit "$FAKE_RUN_RC"
fi
SH
cp "$WORK/bin/docker" "$WORK/bin/singularity"
chmod +x "$WORK/bin/docker" "$WORK/bin/singularity"
export PATH="$WORK/bin:$PATH" HTS_VIA_CONTAINER=1 IMAGE=vep-test CAPTURE="$WORK/args"
export RUN_COUNT="$WORK/run-count"

RUNTIME=docker
hts bcftools view -R "$WORK/bed dir/regions.bed.gz" -o "$WORK/output dir/out.vcf.gz" \
    "$WORK/input dir/in.vcf.gz"
grep -Fx -- '--entrypoint' "$CAPTURE" >/dev/null
grep -Fx -- 'bcftools' "$CAPTURE" >/dev/null
grep -Fx -- '--pull=never' "$CAPTURE" >/dev/null
# Every tool the pipeline runs in the image works offline; the container
# must not be able to reach the network (audit M15).
grep -Fx -- '--network=none' "$CAPTURE" >/dev/null
grep -Fx -- 'core=0:0' "$CAPTURE" >/dev/null
[[ "$(ulimit -c)" == "0" ]]
grep -E '^/hts_[0-9]+/(regions.bed.gz|out.vcf.gz|in.vcf.gz)$' "$CAPTURE" >/dev/null
if grep -F "$WORK/input dir/in.vcf.gz" "$CAPTURE" >/dev/null; then
    echo "FAIL: host path leaked into container args" >&2
    exit 1
fi

RUNTIME=singularity
hts tabix -p vcf "$WORK/output dir/out.vcf.gz"
[[ "$(sed -n '1p' "$CAPTURE")" == "exec" ]]
grep -Fx -- 'tabix' "$CAPTURE" >/dev/null
grep -E '^/hts_[0-9]+/out.vcf.gz$' "$CAPTURE" >/dev/null

export FAKE_IMAGE_MISSING=1
if (RUNTIME=docker hts tabix -p vcf "$WORK/output dir/out.vcf.gz") \
    >"$WORK/missing-image.log" 2>&1; then
    echo "FAIL: missing local image was accepted" >&2
    exit 1
fi
unset FAKE_IMAGE_MISSING
grep -F 'is not available locally' "$WORK/missing-image.log" >/dev/null

export FAKE_RUN_RC=125
if (RUNTIME=docker hts tabix -p vcf "$WORK/output dir/out.vcf.gz") \
    >"$WORK/startup-failure.log" 2>&1; then
    echo "FAIL: container startup failure was accepted" >&2
    exit 1
fi
unset FAKE_RUN_RC
[[ "$(wc -l < "$RUN_COUNT" | tr -d ' ')" == "1" ]]

# A crashing executable stays failed with its original exit status and must
# not be retried into an apparent success. No actual crash/core is needed.
export FAKE_RUN_RC=139
crash_rc=0
(RUNTIME=docker hts tabix -p vcf "$WORK/output dir/out.vcf.gz") \
    >"$WORK/crash.log" 2>&1 || crash_rc=$?
unset FAKE_RUN_RC
[[ "$crash_rc" == "139" ]]
[[ "$(wc -l < "$RUN_COUNT" | tr -d ' ')" == "2" ]]
grep -F 'terminated by a signal (exit 139); not retrying' "$WORK/crash.log" >/dev/null

# Audit repro (H4): with a native bcftools on PATH, hts() dispatches to it —
# but a per-call HTS_VIA_CONTAINER=1 must still force the container, which
# is how the liftover script keeps the +liftover plugin pinned to the image.
cat > "$WORK/bin/bcftools" <<'SH'
#!/usr/bin/env bash
printf 'native\n' > "$NATIVE_MARK"
SH
chmod +x "$WORK/bin/bcftools"
export NATIVE_MARK="$WORK/native-mark"
rm -f "$NATIVE_MARK" "$CAPTURE"
(unset HTS_VIA_CONTAINER; RUNTIME=docker hts bcftools --version)
[[ -f "$NATIVE_MARK" ]] || { echo "FAIL: native bcftools should be preferred by default" >&2; exit 1; }
rm -f "$NATIVE_MARK" "$CAPTURE"
(unset HTS_VIA_CONTAINER; RUNTIME=docker HTS_VIA_CONTAINER=1 hts bcftools +liftover)
[[ ! -f "$NATIVE_MARK" ]] || { echo "FAIL: HTS_VIA_CONTAINER=1 must bypass the native bcftools" >&2; exit 1; }
grep -Fx -- '+liftover' "$CAPTURE" >/dev/null
grep -Fx -- 'bcftools' "$CAPTURE" >/dev/null
grep -F 'HTS_VIA_CONTAINER=1 hts bcftools +liftover' "${ROOT}/scripts/liftover_grch37_to_grch38.sh" >/dev/null \
    || { echo "FAIL: liftover script no longer forces the container for +liftover" >&2; exit 1; }

# A directory argument keeps its trailing slash (bcftools sort -T DIR/ puts
# its spill files inside DIR rather than using DIR as a name prefix — M14).
mkdir -p "$WORK/scratch dir"
RUNTIME=docker hts bcftools sort -T "$WORK/scratch dir/" -o "$WORK/output dir/sorted.vcf.gz" "$WORK/input dir/in.vcf.gz"
grep -E '^/hts_[0-9]+/$' "$CAPTURE" >/dev/null || { echo "FAIL: trailing slash on a directory argument was dropped" >&2; exit 1; }
grep -E "^$WORK/scratch dir:/hts_[0-9]+:rw$" "$CAPTURE" >/dev/null || { echo "FAIL: scratch directory was not bind-mounted" >&2; exit 1; }

# Every docker/podman `run` of an offline tool in the scripts and the service
# carries --network=none and --pull=never (M15: no network, and a missing
# local image fails at once instead of waiting on a registry); singularity
# has neither flag.
RUN_SITES=0
while IFS= read -r line; do
    RUN_SITES=$(( RUN_SITES + 1 ))
    case "$line" in
        *"--network=none"*) ;;
        *) echo "FAIL: container run without --network=none: $line" >&2; exit 1 ;;
    esac
    case "$line" in
        *"--pull=never"*) ;;
        *) echo "FAIL: container run without --pull=never: $line" >&2; exit 1 ;;
    esac
done < <(grep -hE '"\$(RUNTIME|rt)" run |FULL=\( "\$RUNTIME" run |"\$RUNTIME" run --rm' \
            "${ROOT}/scripts/run_annotation.sh" "${ROOT}/scripts/lib.sh" \
            "${ROOT}/scripts/build_clinical_protein_catalog.sh" "${ROOT}/scripts/build_clinvar_aa_reference.sh" \
            "${ROOT}/scripts/preflight.sh" "${ROOT}/scripts/verify_container_stack.sh" \
            | grep -v '^\s*#')
[[ "$RUN_SITES" -ge 11 ]] || { echo "FAIL: expected at least 11 container run sites, scanned $RUN_SITES" >&2; exit 1; }
grep -F '"run", "--rm", "--pull=never", "--network=none"' "${ROOT}/local_service/cohort_store.py" >/dev/null \
    || { echo "FAIL: cohort store container backend lacks --pull=never/--network=none" >&2; exit 1; }
grep -F 'run --rm --network=none' "${ROOT}/pipeline/screen_ccre_dataset.py" >/dev/null \
    || { echo "FAIL: bigBedToBed wrapper lacks --network=none" >&2; exit 1; }

echo "PASS  HTS container path mapping, no-pull/no-network guards (docker + singularity)"
