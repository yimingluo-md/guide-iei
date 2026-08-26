#!/usr/bin/env bash
# Verify version extraction and zero-copy publication of a verified ClinVar VCF.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_ROOT="$(cd "${HERE}/.." && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
TEST_ROOT="$WORK/repo"
export TEST_ROOT CLINVAR_TEST_SOURCE="$WORK/source.vcf.gz"

mkdir -p "$TEST_ROOT/scripts" "$TEST_ROOT/config" "$WORK/bin"
cp "$SOURCE_ROOT/scripts/fetch_clinvar.sh" "$TEST_ROOT/scripts/"
cp "$SOURCE_ROOT/scripts/lib.sh" "$TEST_ROOT/scripts/"

printf '%s\n' \
  '##fileformat=VCFv4.2' \
  '##fileDate=2026-08-26' \
  '#CHROM POS ID REF ALT QUAL FILTER INFO' \
  | gzip -c > "$CLINVAR_TEST_SOURCE"
printf 'test-index\n' > "${CLINVAR_TEST_SOURCE}.tbi"

cat > "$TEST_ROOT/config/test.yaml" <<'YAML'
container:
  runtime: docker
  image: "vep-test:local"
reference:
  assembly: GRCh38
clinvar:
  auto_fetch: true
  dest_dir: data/clinvar
  url: "https://example.invalid/clinvar.vcf.gz"
  keep_dated_copy: true
YAML

# A valid-looking upstream checksum makes the production script use the fast
# verified-header path. The fake downloader keeps this test fully offline.
cat > "$WORK/bin/curl" <<'SH'
#!/usr/bin/env bash
printf '00000000000000000000000000000000  clinvar\n'
SH
cat > "$TEST_ROOT/scripts/parallel_fetch.py" <<'PY'
#!/usr/bin/env python3
import os
import shutil
import sys

source = os.environ["CLINVAR_TEST_SOURCE"]
url, destination = sys.argv[1:3]
if url.endswith(".tbi"):
    source += ".tbi"
shutil.copyfile(source, destination)
PY
chmod +x "$WORK/bin/curl" "$TEST_ROOT/scripts/parallel_fetch.py"

PATH="$WORK/bin:$PATH" bash "$TEST_ROOT/scripts/fetch_clinvar.sh" \
  "$TEST_ROOT/config/test.yaml" > "$WORK/fetch.log" 2>&1

DATED="$TEST_ROOT/data/clinvar/clinvar_20260826.GRCh38.vcf.gz"
LATEST="$TEST_ROOT/data/clinvar/clinvar_latest.GRCh38.vcf.gz"
test -s "$DATED"
test -s "$LATEST"
test -s "${LATEST}.tbi"
[[ "$DATED" -ef "$LATEST" ]]
[[ "${DATED}.tbi" -ef "${LATEST}.tbi" ]]
grep -F 'CLINVAR_RELEASE=20260826' "$WORK/fetch.log" >/dev/null

echo "PASS  ClinVar verified-header release and hard-link publication"
