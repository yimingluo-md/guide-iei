#!/usr/bin/env bash
# Verify version extraction and zero-copy publication of a verified ClinVar VCF.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_ROOT="$(cd "${HERE}/.." && pwd)"
WORK="$(mktemp -d)"
trap '[[ -n "${KEEP_WORK:-}" ]] || rm -rf "$WORK"' EXIT
TEST_ROOT="$WORK/repo"
export TEST_ROOT CLINVAR_TEST_SOURCE="$WORK/source.vcf.gz"

mkdir -p "$TEST_ROOT/scripts" "$TEST_ROOT/config" "$WORK/bin"
cp "$SOURCE_ROOT/scripts/fetch_clinvar.sh" "$TEST_ROOT/scripts/"
cp "$SOURCE_ROOT/scripts/lib.sh" "$TEST_ROOT/scripts/"

# A BGZF-shaped source (gzip member + the BGZF EOF block), as NCBI publishes.
make_source() { # make_source <fileDate>
    printf '%s\n' \
      '##fileformat=VCFv4.2' \
      "##fileDate=$1" \
      '#CHROM POS ID REF ALT QUAL FILTER INFO' \
      | gzip -c > "$CLINVAR_TEST_SOURCE"
    printf '\x1f\x8b\x08\x04\x00\x00\x00\x00\x00\xff\x06\x00\x42\x43\x02\x00\x1b\x00\x03\x00\x00\x00\x00\x00\x00\x00\x00\x00' >> "$CLINVAR_TEST_SOURCE"
    printf 'test-index\n' > "${CLINVAR_TEST_SOURCE}.tbi"
}
make_source 2026-08-26

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
export FAKE_UPSTREAM_MD5="00000000000000000000000000000000" FAKE_CURL_RC=0
export FETCH_CALLS="$WORK/fetch-calls"
cat > "$WORK/bin/curl" <<'SH'
#!/usr/bin/env bash
[[ "${FAKE_CURL_RC:-0}" == "0" ]] || exit "$FAKE_CURL_RC"
printf '%s  clinvar\n' "$FAKE_UPSTREAM_MD5"
SH
cat > "$TEST_ROOT/scripts/parallel_fetch.py" <<'PY'
#!/usr/bin/env python3
import os
import shutil
import sys

source = os.environ["CLINVAR_TEST_SOURCE"]
url, destination = sys.argv[1:3]
with open(os.environ["FETCH_CALLS"], "a") as handle:
    handle.write(url + "\n")
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
PROVENANCE="${LATEST}.provenance.json"
test -s "$PROVENANCE"
grep -F '"upstream_md5": "00000000000000000000000000000000"' "$PROVENANCE" >/dev/null
[[ "$(grep -c . "$FETCH_CALLS")" == "2" ]]   # VCF + index

# Audit M10: an unchanged upstream release is not downloaded again.
fetch_clinvar() { PATH="$WORK/bin:$PATH" bash "$TEST_ROOT/scripts/fetch_clinvar.sh" "$TEST_ROOT/config/test.yaml"; }
fetch_clinvar > "$WORK/reuse.log" 2>&1
grep -F 'upstream ClinVar is unchanged' "$WORK/reuse.log" >/dev/null
grep -F 'CLINVAR_RELEASE=20260826' "$WORK/reuse.log" >/dev/null
grep -F "CLINVAR_VCF=${LATEST}" "$WORK/reuse.log" >/dev/null
[[ "$(grep -c . "$FETCH_CALLS")" == "2" ]] || { echo "FAIL: unchanged ClinVar was downloaded again" >&2; exit 1; }

# A new upstream release (different MD5) is downloaded and published.
make_source 2026-09-02
FAKE_UPSTREAM_MD5="11111111111111111111111111111111" fetch_clinvar > "$WORK/new.log" 2>&1
grep -F 'CLINVAR_RELEASE=20260902' "$WORK/new.log" >/dev/null
[[ "$(grep -c . "$FETCH_CALLS")" == "4" ]]
test -s "$TEST_ROOT/data/clinvar/clinvar_20260902.GRCh38.vcf.gz"
grep -F '"upstream_md5": "11111111111111111111111111111111"' "$PROVENANCE" >/dev/null

# Offline (curl cannot connect): the installed release is reused with a warning
# instead of failing the whole annotation run.
FAKE_CURL_RC=7 fetch_clinvar > "$WORK/offline.log" 2>&1 || { echo "FAIL: offline fetch failed despite an installed release" >&2; cat "$WORK/offline.log" >&2; exit 1; }
grep -F 'WARN: cannot reach the ClinVar source' "$WORK/offline.log" >/dev/null
grep -F 'CLINVAR_RELEASE=20260902' "$WORK/offline.log" >/dev/null
[[ "$(grep -c . "$FETCH_CALLS")" == "4" ]]

# A damaged installed copy (BGZF EOF block missing) is never reused.
python3 - "$LATEST" <<'PY'
import sys
path = sys.argv[1]
data = open(path, "rb").read()
open(path, "wb").write(data[:-28])
PY
FAKE_UPSTREAM_MD5="11111111111111111111111111111111" fetch_clinvar > "$WORK/damaged.log" 2>&1
[[ "$(grep -c . "$FETCH_CALLS")" == "6" ]] || { echo "FAIL: damaged installed ClinVar was reused" >&2; exit 1; }

# Offline with nothing installed: the download is attempted (and its failure
# is fatal — exercised through the fake fetcher refusing).
rm -f "$LATEST" "${LATEST}.tbi" "$PROVENANCE"
if FAKE_CURL_RC=7 CLINVAR_TEST_SOURCE="$WORK/missing.vcf.gz" fetch_clinvar > "$WORK/none.log" 2>&1; then
    echo "FAIL: download failure with no installed release did not fail" >&2; exit 1
fi
grep -F 'no ClinVar release is installed' "$WORK/none.log" >/dev/null

echo "PASS  ClinVar verified-header release, hard-link publication, and installed-release reuse"
