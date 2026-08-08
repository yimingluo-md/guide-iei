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
# and a stable symlink/copy clinvar_latest.GRCh38.vcf.gz that the config's
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
# The completed temporary is moved to its dated filename below, so every new
# invocation still checks the current weekly release. Interrupted range files
# and their metadata remain resumable in place.
python3 "${HERE}/parallel_fetch.py" "$URL" "$TMP_VCF" \
    --connections 4 --chunk-mib 64 \
    || die "ClinVar download failed"
python3 "${HERE}/parallel_fetch.py" "${URL}.tbi" "${TMP_VCF}.tbi" \
    --connections 1 --chunk-mib 4 \
    || warn "ClinVar tabix index download failed; a local index will be generated if required"

# Extract the ClinVar release date from the VCF header
#   ##fileDate=2026-02-18  (or a source-stamped line). Fall back to today.
# macOS zcat expects legacy .Z files; gzip -cd is portable. Keep every stage
# consuming its input fully so `set -o pipefail` does not turn SIGPIPE into a
# false download failure.
RELEASE="$(gzip -cd "$TMP_VCF" 2>/dev/null | \
           sed -n 's/^##fileDate=\([0-9-]*\).*/\1/p' | sed -n '1p' | tr -d '-')"
[[ -n "$RELEASE" ]] || RELEASE="$(date +%Y%m%d)"
log "ClinVar release date: $RELEASE"

DATED="${DEST_DIR}/clinvar_${RELEASE}.${ASSEMBLY}.vcf.gz"
LATEST="${DEST_DIR}/clinvar_latest.${ASSEMBLY}.vcf.gz"

mv -f "$TMP_VCF" "$DATED"
[[ -f "${TMP_VCF}.tbi" ]] && mv -f "${TMP_VCF}.tbi" "${DATED}.tbi"

# (Re)build the tabix index if we didn't get one.
if [[ ! -f "${DATED}.tbi" ]]; then
    log "indexing ClinVar VCF (tabix)"
    hts tabix -p vcf "$DATED"
fi

# Point 'latest' at the dated file (copy for portability across FS without symlink support).
cp -f "$DATED" "$LATEST"
cp -f "${DATED}.tbi" "${LATEST}.tbi"

if [[ "${KEEP_DATED}" == "false" ]]; then
    log "keep_dated_copy=false — removing dated copy, keeping only latest"
    rm -f "$DATED" "${DATED}.tbi"
fi

# Emit the resolved path + release for the caller (run script captures this).
echo "CLINVAR_VCF=${LATEST}"
echo "CLINVAR_RELEASE=${RELEASE}"
log "ClinVar ready: $LATEST (release $RELEASE)"
