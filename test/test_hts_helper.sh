#!/usr/bin/env bash
# Verify container fallback rewrites absolute paths and supports both CLI styles.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${HERE}/.." && pwd)"
source "${ROOT}/scripts/lib.sh"

WORK="$(mktemp -d)"
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

echo "PASS  HTS container path mapping and no-pull guard (docker + singularity)"
