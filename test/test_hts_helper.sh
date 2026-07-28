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
printf '%s\n' "$@" > "$CAPTURE"
SH
cp "$WORK/bin/docker" "$WORK/bin/singularity"
chmod +x "$WORK/bin/docker" "$WORK/bin/singularity"
export PATH="$WORK/bin:$PATH" HTS_VIA_CONTAINER=1 IMAGE=vep-test CAPTURE="$WORK/args"

RUNTIME=docker
hts bcftools view -R "$WORK/bed dir/regions.bed.gz" -o "$WORK/output dir/out.vcf.gz" \
    "$WORK/input dir/in.vcf.gz"
grep -Fx -- '--entrypoint' "$CAPTURE" >/dev/null
grep -Fx -- 'bcftools' "$CAPTURE" >/dev/null
grep -E '^/hts_[0-9]+/(regions.bed.gz|out.vcf.gz|in.vcf.gz)$' "$CAPTURE" >/dev/null
! grep -F "$WORK/input dir/in.vcf.gz" "$CAPTURE" >/dev/null

RUNTIME=singularity
hts tabix -p vcf "$WORK/output dir/out.vcf.gz"
[[ "$(sed -n '1p' "$CAPTURE")" == "exec" ]]
grep -Fx -- 'tabix' "$CAPTURE" >/dev/null
grep -E '^/hts_[0-9]+/out.vcf.gz$' "$CAPTURE" >/dev/null

echo "PASS  HTS container path mapping (docker + singularity)"
