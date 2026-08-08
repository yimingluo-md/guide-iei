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

# hts() falls back to the container when host bgzip/tabix/samtools are absent;
# honour the configured runtime/image instead of the docker/vep-annotate:latest
# defaults (a podman-only host previously failed here despite correct config).
RUNTIME="$(yaml_get "$CONFIG" container.runtime)"; RUNTIME="${RUNTIME:-docker}"
IMAGE="$(yaml_get "$CONFIG" container.image)";     IMAGE="${IMAGE:-vep-annotate:latest}"
export RUNTIME IMAGE
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

# Detect the 1-based column indices of the GRCh38 chrom/pos columns from the
# header. GRCh38-native releases (4.x/5.x, e.g. 5.3.1a) name them "#chr" and
# "pos(1-based)"; legacy GRCh37-sorted releases carried explicit "hg38_chr" /
# "hg38_pos(1-based)" columns, which take priority when present.
# NOTE: macOS /usr/bin/zcat is the legacy .Z reader and fails on plain .gz, so
# use `gzip -cd` (matching every other script here). The header read disables
# pipefail locally: `head -1` closes the pipe early and gzip's SIGPIPE (141)
# would otherwise abort the script.
HEADER="$( set +o pipefail; gzip -cd "$H_FILE" | head -1 )"
find_col() {
    awk -v FS='\t' -v name="$1" '{for(i=1;i<=NF;i++) if($i==name){print i; exit}}' <<<"$HEADER"
}
# Explicit hg38_* columns win: on a legacy hg19-sorted release "#chr" is the
# hg19 chromosome and only hg38_chr/hg38_pos are GRCh38.
CHR_COL="$(find_col "hg38_chr")"
[[ -n "$CHR_COL" ]] || CHR_COL="$(find_col "#chr")"
POS_COL="$(find_col "hg38_pos(1-based)")"
[[ -n "$POS_COL" ]] || POS_COL="$(find_col "pos(1-based)")"
[[ "$CHR_COL" =~ ^[0-9]+$ && "$POS_COL" =~ ^[0-9]+$ ]] \
    || die "could not find hg38 chr/pos columns in dbNSFP header (saw: $(cut -f1-4 <<<"$HEADER") ...)"
log "GRCh38 sort keys: chr=col$CHR_COL pos=col$POS_COL"

log "merging + sorting on GRCh38 coordinates (this takes a while)..."
{
    ( set +o pipefail; gzip -cd "$H_FILE" | head -1 )   # header first
    for f in "${CHR_FILES[@]}"; do
        gzip -cd "$f" | tail -n +2                      # data, header dropped
    done | sort -t"$(printf '\t')" -k"${CHR_COL},${CHR_COL}" -k"${POS_COL},${POS_COL}n"
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
