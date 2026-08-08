#!/usr/bin/env bash
# =============================================================================
# prepare_dbnsfp.sh — rebuild a downloaded dbNSFP release into the single
# position-sorted, bgzipped, tabix-indexed GRCh38 file the VEP dbNSFP plugin
# expects.
#
# WHY this is a separate one-time step:
#   dbNSFP ships as a ZIP of per-chromosome files, coordinate-sorted on the
#   hg19 (GRCh37) position columns. For a GRCh38 pipeline the rows must be
#   re-sorted on the GRCh38 columns (hg38_chr, hg38_pos) and re-indexed, or
#   tabix lookups silently miss. This mirrors the recipe in the VEP dbNSFP
#   plugin's own documentation.
#
# INPUT : the unzipped dbNSFP release directory (contains dbNSFP<ver>_variant.chr* )
# OUTPUT: <config plugins.dbNSFP.path>  (+ .tbi)
#
# Usage:
#   scripts/prepare_dbnsfp.sh /path/to/dbNSFP_unzipped_dir [config.yaml]
#
# Requires bgzip + tabix (native, or via the container: HTS_VIA_CONTAINER=1).
# Big job: needs ~200 GB scratch and a good while for the genome-wide sort.
# =============================================================================
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${HERE}/lib.sh"
ROOT="$(cd "${HERE}/.." && pwd)"

SRC_DIR="${1:?usage: prepare_dbnsfp.sh <unzipped_dbNSFP_dir> [config.yaml]}"
CONFIG="${2:-${ROOT}/config/annotation.config.yaml}"
CONSUME_SOURCE="${3:-}"
[[ -d "$SRC_DIR" ]] || die "dbNSFP source dir not found: $SRC_DIR"
[[ -f "$CONFIG" ]] || die "config not found: $CONFIG"
[[ -z "$CONSUME_SOURCE" || "$CONSUME_SOURCE" == "--remove-source-after-success" ]] \
    || die "unknown preparation option: $CONSUME_SOURCE"

absdir() { local p="$1"; [[ "$p" = /* ]] || p="${ROOT}/${p}"; echo "$p"; }
OUT="$(absdir "$(yaml_get "$CONFIG" plugins.dbNSFP.path)")"
mkdir -p "$(dirname "$OUT")"
OUT_PLAIN="${OUT%.gz}"

# Locate per-chromosome variant files (naming differs slightly across versions).
shopt -s nullglob
CHR_FILES=( "$SRC_DIR"/dbNSFP*variant.chr* )
[[ ${#CHR_FILES[@]} -gt 0 ]] || die "no dbNSFP*variant.chr* files under $SRC_DIR"
log "found ${#CHR_FILES[@]} per-chromosome dbNSFP files"

# Header comes from chr1; strip the leading '#' handling to match plugin expectation.
H_FILE=""
for f in "${CHR_FILES[@]}"; do [[ "$f" == *chr1* ]] && H_FILE="$f" && break; done
[[ -n "$H_FILE" ]] || H_FILE="${CHR_FILES[0]}"

# Detect the 1-based column indices of the GRCh38 chrom/pos columns from the header.
HEADER="$(zcat "$H_FILE" | head -1)"
CHR_COL="$(awk -v FS='\t' '{for(i=1;i<=NF;i++) if($i=="hg38_chr"||$i=="chr"){print i; exit}}' <<<"$HEADER")"
POS_COL="$(awk -v FS='\t' '{for(i=1;i<=NF;i++) if($i=="hg38_pos(1-based)"||$i=="pos(1-based)"){print i; exit}}' <<<"$HEADER")"
[[ -n "$CHR_COL" && -n "$POS_COL" ]] || die "could not find hg38 chr/pos columns in dbNSFP header"
log "GRCh38 sort keys: chr=col$CHR_COL pos=col$POS_COL"

log "merging + sorting on GRCh38 coordinates (this takes a while)..."
{
    zcat "$H_FILE" | head -1                       # header first
    for f in "${CHR_FILES[@]}"; do
        zcat "$f" | tail -n +2                      # data, header dropped
    done | sort -t"$(printf '\t')" -k${CHR_COL},${CHR_COL} -k${POS_COL},${POS_COL}n
} > "$OUT_PLAIN"

log "bgzip + tabix (chr=col$CHR_COL pos=col$POS_COL, skip header line)"
( cd "$(dirname "$OUT")" && hts bgzip -f "$(basename "$OUT_PLAIN")" )
( cd "$(dirname "$OUT")" && hts tabix -f -s "$CHR_COL" -b "$POS_COL" -e "$POS_COL" -S 1 "$(basename "$OUT")" )

if [[ "$CONSUME_SOURCE" == "--remove-source-after-success" ]]; then
    [[ -s "$OUT" && -s "${OUT}.tbi" ]] || die "refusing to remove dbNSFP source before installed output and index are present"
    rm -f -- "${CHR_FILES[@]}"
    log "removed ${#CHR_FILES[@]} downloaded dbNSFP chromosome files after successful managed installation"
fi

log "dbNSFP ready: $OUT"
log "Set plugins.dbNSFP.path to this file (already the default) and enable it in the config."
