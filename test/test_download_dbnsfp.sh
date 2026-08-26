#!/usr/bin/env bash
# Exercise the private-link dbNSFP workflow without downloading the real data.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_ROOT="$(cd "${HERE}/.." && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
TEST_ROOT="$WORK/repo"
export TEST_ROOT

mkdir -p "$TEST_ROOT/scripts" "$TEST_ROOT/config" "$TEST_ROOT/data" "$WORK/bin"
cp "$SOURCE_ROOT/scripts/download_dbnsfp.sh" "$TEST_ROOT/scripts/"
cp "$SOURCE_ROOT/scripts/lib.sh" "$TEST_ROOT/scripts/"

cat > "$TEST_ROOT/config/test.yaml" <<YAML
container:
  runtime: docker
  image: vep-test:local
plugins:
  dbNSFP:
    version: "5.4a"
    path: "$TEST_ROOT/data/dbNSFP5.4a_grch38.gz"
YAML

cat > "$TEST_ROOT/scripts/parallel_fetch.py" <<'PY'
#!/usr/bin/env python3
import sys
from pathlib import Path

assert sys.argv[1] == "-"
output = Path(sys.argv[2])
url_file = Path(sys.argv[sys.argv.index("--url-file") + 1])
state_key = sys.argv[sys.argv.index("--state-url-key") + 1]
url = url_file.read_text().strip()
assert "authorized-code" in url
assert "authorized-code" not in state_key
output.parent.mkdir(parents=True, exist_ok=True)
if output.name.endswith(".tbi"):
    assert url.endswith("dbNSFP5.4a_grch38.gz.tbi")
    output.write_bytes(b"test-index")
else:
    assert url.endswith("dbNSFP5.4a_grch38.gz")
    output.write_bytes(bytes.fromhex("1f8b0804") + b"test-bgzf")
print(" 99.5%  fake verified download")
PY

cat > "$WORK/bin/curl" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
config=""
output=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --config) config="$2"; shift 2 ;;
    --output) output="$2"; shift 2 ;;
    *) shift ;;
  esac
done
grep -F 'dbNSFP5.4a_grch38.gz.md5' "$config" >/dev/null
printf 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa  dbNSFP5.4a_grch38.gz\n' > "$output"
SH

cat > "$WORK/bin/tabix" <<'SH'
#!/usr/bin/env bash
if [[ "${1:-}" == "-l" ]]; then
  printf '1\n'
fi
exit 0
SH
chmod +x "$TEST_ROOT/scripts/download_dbnsfp.sh" \
  "$TEST_ROOT/scripts/parallel_fetch.py" "$WORK/bin/curl" "$WORK/bin/tabix"

SECRET="$WORK/dbnsfp-url.secret"
printf '%s\n' \
  'https://dist.genos.us/academic/authorized-code/dbNSFP5.4a_grch38.gz' \
  > "$SECRET"
chmod 600 "$SECRET"

PATH="$WORK/bin:/usr/local/bin:/usr/bin:/bin" \
  bash "$TEST_ROOT/scripts/download_dbnsfp.sh" \
  "$SECRET" "$TEST_ROOT/config/test.yaml" > "$WORK/job.log" 2>&1

test -s "$TEST_ROOT/data/dbNSFP5.4a_grch38.gz"
test -s "$TEST_ROOT/data/dbNSFP5.4a_grch38.gz.tbi"
test -s "$TEST_ROOT/data/dbNSFP5.4a_grch38.gz.md5"
test ! -e "$SECRET"
test ! -e "${SECRET}.tbi"
test ! -e "${SECRET}.md5.curl"
grep -F 'downloaded, MD5-verified, indexed, and installed' "$WORK/job.log" >/dev/null
if grep -F 'authorized-code' "$WORK/job.log" >/dev/null; then
  echo "private dbNSFP access path leaked into the job log" >&2
  exit 1
fi

echo "PASS  dbNSFP private-link download, companion derivation, and log redaction"
