#!/usr/bin/env bash
# =============================================================================
# fetch_clinvar.sh — download the latest NCBI ClinVar VCF for the configured
# assembly, version-stamp it, and point the ClinVar custom track at it.
#
# Called automatically by run_annotation.sh before each run when
# clinvar.auto_fetch: true. Can also be run standalone.
#
#   scripts/fetch_clinvar.sh [config.yaml]
#
# Result: references/clinvar/clinvar_<releasedate>.GRCh38.vcf.gz (+ .tbi),
# and a stable hard-link/copy clinvar_latest.GRCh38.vcf.gz that the config's
# ClinVar track points to.
# =============================================================================
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${HERE}/lib.sh"
ROOT="$(cd "${HERE}/.." && pwd)"

CONFIG="${1:-${ROOT}/config/annotation.config.yaml}"
[[ -f "$CONFIG" ]] || die "config not found: $CONFIG"

# hts() falls back to the container when host bgzip/tabix/samtools are absent;
# honour the configured runtime/image instead of the docker/vep-annotate:latest
# defaults (a podman-only host previously failed here despite correct config).
RUNTIME="$(yaml_get "$CONFIG" container.runtime)"; RUNTIME="${RUNTIME:-docker}"
IMAGE="$(yaml_get "$CONFIG" container.image)";     IMAGE="${IMAGE:-vep-annotate:latest}"
export RUNTIME IMAGE

ASSEMBLY="$(yaml_get "$CONFIG" reference.assembly)"; ASSEMBLY="${ASSEMBLY:-GRCh38}"
AUTO="$(yaml_get "$CONFIG" clinvar.auto_fetch)"
DEST_DIR="$(yaml_get "$CONFIG" clinvar.dest_dir)"; DEST_DIR="${DEST_DIR:-references/clinvar}"
URL_TMPL="$(yaml_get "$CONFIG" clinvar.url)"
KEEP_DATED="$(yaml_get "$CONFIG" clinvar.keep_dated_copy)"

# Resolve relative dest against repo root
[[ "$DEST_DIR" = /* ]] || DEST_DIR="${ROOT}/${DEST_DIR}"
mkdir -p "$DEST_DIR"

if [[ "${AUTO}" == "false" ]]; then
    log "clinvar.auto_fetch is false — skipping ClinVar download."
    exit 0
fi

# Fill {ASSEMBLY} in the URL template.
URL="${URL_TMPL//\{ASSEMBLY\}/$ASSEMBLY}"
[[ -n "$URL" ]] || URL="https://ftp.ncbi.nlm.nih.gov/pub/clinvar/vcf_${ASSEMBLY}/clinvar.vcf.gz"

log "ClinVar source: $URL"

TMP_VCF="${DEST_DIR}/clinvar.download.vcf.gz"
LATEST="${DEST_DIR}/clinvar_latest.${ASSEMBLY}.vcf.gz"
# Written after every successful publication; read before every download so
# an unchanged upstream release (or an unreachable NCBI) reuses the
# installed copy instead of re-downloading ~100 MB per run (audit M10).
PROVENANCE="${LATEST}.provenance.json"

release_of() { # release_of <vcf.gz> -> YYYYMMDD from ##fileDate, or empty
    python3 - "$1" <<'PY'
import gzip, re, sys
try:
    with gzip.open(sys.argv[1], "rb") as handle:
        for raw in handle:
            if raw.startswith(b"##fileDate="):
                match = re.search(rb"##fileDate=([0-9-]+)", raw)
                if match:
                    print(match.group(1).decode().replace("-", ""))
                break
            if not raw.startswith(b"#"):
                break
except (OSError, EOFError):
    pass
PY
}
recorded_provenance() { # recorded_provenance <key> -> value from the provenance sidecar, or empty
    python3 - "$PROVENANCE" "$1" <<'PY'
import json, sys
try:
    print(json.load(open(sys.argv[1])).get(sys.argv[2], ""))
except (OSError, ValueError):
    pass
PY
}
installed_release_usable() {
    [[ -s "$LATEST" && -s "${LATEST}.tbi" ]] && bgzf_complete "$LATEST"
}
reuse_installed() { # reuse_installed <reason>
    local release
    release="$(release_of "$LATEST")"
    release="${release:-$(recorded_provenance release)}"
    release="${release:-unknown}"
    log "$1; reusing the installed ClinVar release ${release}: $LATEST"
    echo "CLINVAR_VCF=${LATEST}"
    echo "CLINVAR_RELEASE=${release}"
    log "ClinVar ready: $LATEST (release $release, installed copy reused)"
    exit 0
}

# NCBI publishes MD5 sidecars beside the weekly files; verifying against
# them turns "same size" into "same content" — a run that crashed after a
# complete download but before the mv below would otherwise leave a stale
# temp file that a later same-size release could be mistaken for. When the
# sidecar is missing (a mirror without checksums), the download proceeds on
# the size check alone, stated as such. UPSTREAM_MD5_STATUS distinguishes a
# missing sidecar (HTTP error) from an unreachable source (no network).
UPSTREAM_MD5_STATUS="missing"
fetch_upstream_md5() { # fetch_upstream_md5 <url> <result-variable>
    local body rc=0 digest
    body="$(curl -fsSL --max-time 60 -H "Cache-Control: no-cache" "$1" 2>/dev/null)" || rc=$?
    case "$rc" in
        0)  UPSTREAM_MD5_STATUS="ok" ;;
        22) UPSTREAM_MD5_STATUS="missing" ;;      # HTTP error: no sidecar published
        *)  UPSTREAM_MD5_STATUS="unreachable" ;;  # DNS/connect/timeout
    esac
    digest="$(printf '%s\n' "$body" | awk '{print tolower($1); exit}')" || digest=""
    printf -v "$2" '%s' "$digest"
}
MD5_ARGS=()
VCF_MD5=""
fetch_upstream_md5 "${URL}.md5" VCF_MD5
if [[ "$VCF_MD5" =~ ^[0-9a-f]{32}$ ]]; then
    MD5_ARGS=(--md5 "$VCF_MD5")
    log "ClinVar VCF upstream MD5: $VCF_MD5"
    if installed_release_usable && [[ "$(recorded_provenance upstream_md5)" == "$VCF_MD5" ]] \
        && [[ "$(recorded_provenance size_bytes)" == "$(wc -c < "$LATEST" | tr -d ' ')" ]]; then
        reuse_installed "upstream ClinVar is unchanged (MD5 ${VCF_MD5})"
    fi
elif [[ "$UPSTREAM_MD5_STATUS" == "unreachable" ]]; then
    if installed_release_usable; then
        warn "cannot reach the ClinVar source ($URL); the installed release may be out of date"
        reuse_installed "ClinVar source unreachable"
    fi
    warn "cannot reach the ClinVar source and no ClinVar release is installed; attempting the download anyway"
else
    warn "no upstream MD5 available for the ClinVar VCF; only its size can be checked"
fi
# The completed temporary is moved to its dated filename below, so every new
# invocation still checks the current weekly release. Interrupted range files
# and their metadata remain resumable in place.
# ${arr[@]+...} keeps the empty-array expansion legal under `set -u` on the
# bash 3.2 that macOS ships.
if ! python3 "${HERE}/parallel_fetch.py" "$URL" "$TMP_VCF" \
    --connections 4 --chunk-mib 64 ${MD5_ARGS[@]+"${MD5_ARGS[@]}"}; then
    if installed_release_usable; then
        warn "ClinVar download failed; the installed release may be out of date"
        reuse_installed "ClinVar download failed"
    fi
    die "ClinVar download failed and no ClinVar release is installed"
fi
TBI_MD5_ARGS=()
TBI_MD5=""
fetch_upstream_md5 "${URL}.tbi.md5" TBI_MD5
[[ "$TBI_MD5" =~ ^[0-9a-f]{32}$ ]] && TBI_MD5_ARGS=(--md5 "$TBI_MD5")
python3 "${HERE}/parallel_fetch.py" "${URL}.tbi" "${TMP_VCF}.tbi" \
    --connections 1 --chunk-mib 4 ${TBI_MD5_ARGS[@]+"${TBI_MD5_ARGS[@]}"} \
    || warn "ClinVar tabix index download failed; a local index will be generated if required"

# Extract the ClinVar release date from the VCF header
#   ##fileDate=2026-02-18  (or a source-stamped line). Fall back to today.
# With an upstream MD5, reading the small header is sufficient: decompressing
# the entire VCF just to find fileDate needlessly scans it again. If the MD5
# was unavailable, retain a full gzip integrity check before the header read.
if [[ ${#MD5_ARGS[@]} -eq 0 ]]; then
    gzip -t "$TMP_VCF" 2>/dev/null || die "ClinVar VCF failed gzip integrity validation"
fi
RELEASE="$(release_of "$TMP_VCF")"
[[ -n "$RELEASE" ]] || RELEASE="$(date +%Y%m%d)"
log "ClinVar release date: $RELEASE"

DATED="${DEST_DIR}/clinvar_${RELEASE}.${ASSEMBLY}.vcf.gz"

mv -f "$TMP_VCF" "$DATED"
[[ -f "${TMP_VCF}.tbi" ]] && mv -f "${TMP_VCF}.tbi" "${DATED}.tbi"

# (Re)build the tabix index if we didn't get one.
if [[ ! -f "${DATED}.tbi" ]]; then
    log "indexing ClinVar VCF (tabix)"
    hts tabix -p vcf "$DATED"
fi

# Point 'latest' at the dated file. A hard link avoids reading and rewriting
# the complete VCF on APFS/ext4; filesystems without hard-link support fall
# back to a portable copy. Publish through a temporary sibling either way.
publish_alias() {
    local source="$1" destination="$2" temporary="${2}.new.$$"
    rm -f "$temporary"
    if ! ln "$source" "$temporary" 2>/dev/null; then
        cp -f "$source" "$temporary"
    fi
    mv -f "$temporary" "$destination"
}
publish_alias "$DATED" "$LATEST"
publish_alias "${DATED}.tbi" "${LATEST}.tbi"
python3 - "$PROVENANCE" "$URL" "${VCF_MD5:-}" "$RELEASE" "$LATEST" <<'PY'
import json, os, sys, time
provenance, url, md5, release, latest = sys.argv[1:]
record = {
    "source_url": url,
    "upstream_md5": md5 if len(md5) == 32 else None,
    "release": release,
    "size_bytes": os.stat(latest).st_size,
    "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
}
tmp = provenance + ".tmp"
with open(tmp, "w") as handle:
    json.dump(record, handle, indent=2, sort_keys=True)
    handle.write("\n")
os.replace(tmp, provenance)
PY

if [[ "${KEEP_DATED}" == "false" ]]; then
    log "keep_dated_copy=false — removing dated copy, keeping only latest"
    rm -f "$DATED" "${DATED}.tbi"
fi

# Emit the resolved path + release for the caller (run script captures this).
echo "CLINVAR_VCF=${LATEST}"
echo "CLINVAR_RELEASE=${RELEASE}"
log "ClinVar ready: $LATEST (release $RELEASE)"
