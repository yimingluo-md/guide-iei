#!/usr/bin/env bash
# Download and validate the registration-gated, pre-built dbNSFP GRCh38 table.
# The authorized URL is supplied in a mode-0600 file so it never appears in
# the job log or process arguments. The service unwraps Outlook Safe Links and
# validates the official host/filename before invoking this script.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${HERE}/.." && pwd)"
source "${HERE}/lib.sh"

URL_FILE="${1:?usage: download_dbnsfp.sh <private_url_file> [config.yaml]}"
CONFIG="${2:-${ROOT}/config/annotation.config.yaml}"
[[ -s "$URL_FILE" ]] || die "the private dbNSFP download link is unavailable; paste it again"
[[ -f "$CONFIG" ]] || die "config not found: $CONFIG"

TBI_URL_FILE="${URL_FILE}.tbi"
MD5_CURL_CONFIG="${URL_FILE}.md5.curl"
cleanup_private_links() {
    rm -f "$URL_FILE" "$TBI_URL_FILE" "$MD5_CURL_CONFIG"
}
trap cleanup_private_links EXIT
trap 'exit 130' INT
trap 'exit 143' HUP TERM

abs_path() {
    local path="$1"
    [[ "$path" = /* ]] || path="${ROOT}/${path}"
    printf '%s\n' "$path"
}

OUT="$(abs_path "$(yaml_get "$CONFIG" plugins.dbNSFP.path)")"
VERSION="$(yaml_get "$CONFIG" plugins.dbNSFP.version)"; VERSION="${VERSION:-5.4a}"
EXPECTED_NAME="$(basename "$OUT")"
mkdir -p "$(dirname "$OUT")"

# Derive both official sibling URLs without ever printing them. The curl
# config keeps the checksum URL out of argv; parallel_fetch's --url-file does
# the same for the large data file and index.
python3 - "$URL_FILE" "$TBI_URL_FILE" "$MD5_CURL_CONFIG" "$EXPECTED_NAME" <<'PY'
import os
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit, urlunsplit

source_path, tbi_path, md5_config_path, expected_name = sys.argv[1:]
url = Path(source_path).read_text(encoding="utf-8").strip()
parsed = urlsplit(url)
if parsed.scheme != "https" or parsed.hostname != "dist.genos.us":
    raise SystemExit("authorized dbNSFP URL must use https://dist.genos.us")
if unquote(Path(parsed.path).name) != expected_name:
    raise SystemExit(f"authorized URL must end with {expected_name}")

def companion(suffix: str) -> str:
    return urlunsplit(parsed._replace(path=parsed.path + suffix, fragment=""))

def curl_config(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'url = "{escaped}"\n'

Path(tbi_path).write_text(companion(".tbi") + "\n", encoding="utf-8")
Path(md5_config_path).write_text(curl_config(companion(".md5")), encoding="utf-8")
os.chmod(tbi_path, 0o600)
os.chmod(md5_config_path, 0o600)
PY

printf '=== dbNSFP %s published checksum ===\n' "$VERSION"
curl -fsSL --connect-timeout 30 --retry 3 --retry-delay 5 \
    --config "$MD5_CURL_CONFIG" --output "${OUT}.md5.part" \
    || die "the dbNSFP checksum could not be downloaded; request a fresh academic link if it expired"
WANT_MD5="$(awk 'NF { print tolower($1); exit }' "${OUT}.md5.part")"
[[ "$WANT_MD5" =~ ^[0-9a-f]{32}$ ]] \
    || die "the dbNSFP checksum response was not a valid MD5 sidecar; request a fresh academic link"
mv -f "${OUT}.md5.part" "${OUT}.md5"

printf '=== dbNSFP %s GRCh38 table (resumable, eight connections) ===\n' "$VERSION"
python3 "${HERE}/parallel_fetch.py" - "$OUT" \
    --url-file "$URL_FILE" \
    --state-url-key "dbnsfp:${EXPECTED_NAME}" \
    --connections 8 --chunk-mib 128 --md5 "$WANT_MD5" \
    --progress-start 0 --progress-scale 99.5

printf '=== dbNSFP %s tabix index ===\n' "$VERSION"
python3 "${HERE}/parallel_fetch.py" - "${OUT}.tbi" \
    --url-file "$TBI_URL_FILE" \
    --state-url-key "dbnsfp:${EXPECTED_NAME}.tbi" \
    --connections 2 --chunk-mib 16 \
    --progress-start 99.5 --progress-scale 0.49
touch "${OUT}.tbi"

printf '=== dbNSFP %s validation and installation ===\n' "$VERSION"
MAGIC="$(head -c 4 "$OUT" | od -An -tx1 | tr -d ' \n')"
[[ "$MAGIC" == "1f8b0804" ]] \
    || die "$EXPECTED_NAME is not BGZF; tabix/VEP cannot use it"

RUNTIME="$(yaml_get "$CONFIG" container.runtime)"; RUNTIME="${RUNTIME:-docker}"
IMAGE="$(yaml_get "$CONFIG" container.image)"; IMAGE="${IMAGE:-vep-annotate:latest}"
export RUNTIME IMAGE
FIRST_CONTIG="$( set +o pipefail; hts tabix -l "$OUT" 2>/dev/null | head -1 )"
[[ -n "$FIRST_CONTIG" ]] \
    || die "the published dbNSFP index cannot list contigs; retry the download"
hts tabix "$OUT" "${FIRST_CONTIG}:1-2000000" >/dev/null \
    || die "the published dbNSFP index failed a region query; retry the download"

printf '100.0%%  dbNSFP %s downloaded, MD5-verified, indexed, and installed\n' "$VERSION"
