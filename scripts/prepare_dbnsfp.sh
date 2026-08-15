#!/usr/bin/env bash
# =============================================================================
# prepare_dbnsfp.sh — install a downloaded dbNSFP release as the single
# position-sorted, bgzipped, tabix-indexed GRCh38 file the VEP dbNSFP plugin
# expects.
#
# TWO SUPPORTED SOURCE LAYOUTS:
#   1. Pre-built single-file release (dbNSFP >= 5.1): the project distributes
#      the variant table as one tabix-indexed BGZF file per genome build
#      (dbNSFP<ver>_grch38.gz + .tbi + .md5), ready for VEP. This is validated
#      (MD5 when the sidecar is present, BGZF magic, tabix query) and
#      installed directly — fast, no scratch space needed.
#   2. Legacy per-chromosome ZIP layout (dbNSFP<ver>_variant.chr*.gz),
#      coordinate-sorted on hg19: rows are merged and re-sorted on the GRCh38
#      columns, then bgzipped and indexed (~200 GB scratch, hours).
#
# INPUT : the download folder (either layout), or the single .gz file itself
# OUTPUT: <config plugins.dbNSFP.path>  (+ .tbi)
#
# Usage:
#   scripts/prepare_dbnsfp.sh /path/to/dbNSFP_download [config.yaml]
#
# Requires bgzip + tabix (native, or via the container: HTS_VIA_CONTAINER=1).
# =============================================================================
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${HERE}/lib.sh"
ROOT="$(cd "${HERE}/.." && pwd)"

SRC_DIR="${1:?usage: prepare_dbnsfp.sh <dbNSFP_download_dir_or_file> [config.yaml]}"
CONFIG="${2:-${ROOT}/config/annotation.config.yaml}"
CONSUME_SOURCE="${3:-}"
[[ -e "$SRC_DIR" ]] || die "dbNSFP source not found: $SRC_DIR"
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

file_md5() {
    if command -v md5 >/dev/null 2>&1; then md5 -q "$1"
    else md5sum "$1" | awk '{print $1}'; fi
}

# ---------------------------------------------------------------------------
# Layout 1: pre-built single-file BGZF release (dbNSFP >= 5.1)
# ---------------------------------------------------------------------------
shopt -s nullglob
PREBUILT=""
if [[ -f "$SRC_DIR" ]]; then
    PREBUILT="$SRC_DIR"
else
    for candidate in "$SRC_DIR"/dbNSFP*grch38.gz; do PREBUILT="$candidate"; break; done
fi

if [[ -n "$PREBUILT" ]]; then
    log "pre-built GRCh38 BGZF release detected: $(basename "$PREBUILT")"
    if [[ "$(basename "$PREBUILT")" != "$(basename "$OUT")" ]]; then
        warn "source file $(basename "$PREBUILT") differs from configured $(basename "$OUT");"
        warn "installing under the source's own name — update plugins.dbNSFP.path and .version to match"
        OUT="$(dirname "$OUT")/$(basename "$PREBUILT")"
    fi
    # MD5 sidecar (published alongside the official download)
    if [[ -s "${PREBUILT}.md5" ]]; then
        WANT_MD5="$(awk '{print $1}' "${PREBUILT}.md5")"
        log "verifying MD5 against the published sidecar (reads the whole file once)..."
        GOT_MD5="$(file_md5 "$PREBUILT")"
        [[ "$GOT_MD5" == "$WANT_MD5" ]] \
            || die "MD5 mismatch for $(basename "$PREBUILT"): expected $WANT_MD5, got $GOT_MD5 — re-download the file"
        log "MD5 verified: $GOT_MD5"
    else
        warn "no .md5 sidecar next to $(basename "$PREBUILT"); continuing with structural checks only"
    fi
    # BGZF magic (1f 8b 08 04 — gzip with the BC extra field tabix requires)
    MAGIC="$(head -c 4 "$PREBUILT" | od -An -tx1 | tr -d ' \n')"
    [[ "$MAGIC" == "1f8b0804" ]] \
        || die "$(basename "$PREBUILT") is not BGZF (magic $MAGIC); tabix/VEP cannot use it"

    if [[ "$PREBUILT" != "$OUT" ]]; then
        log "installing to $OUT"
        if [[ "$CONSUME_SOURCE" == "--remove-source-after-success" ]]; then
            mv -f "$PREBUILT" "$OUT"
            [[ -s "${PREBUILT}.tbi" ]] && mv -f "${PREBUILT}.tbi" "${OUT}.tbi"
            rm -f "${PREBUILT}.md5"
        else
            cp "$PREBUILT" "$OUT"
            [[ -s "${PREBUILT}.tbi" ]] && cp "${PREBUILT}.tbi" "${OUT}.tbi"
        fi
    fi
    # Keep the index newer than the data file so htslib does not warn on
    # every open (the install move can reorder the two mtimes).
    [[ -s "${OUT}.tbi" ]] && touch "${OUT}.tbi" 2>/dev/null || true
    if [[ ! -s "${OUT}.tbi" ]]; then
        log "no .tbi shipped with the file; building the tabix index"
        HEADER="$( set +o pipefail; gzip -cd "$OUT" | head -1 )"
        CHR_COL="$(awk -v FS='\t' '{for(i=1;i<=NF;i++) if($i=="#chr"){print i; exit}}' <<<"$HEADER")"
        POS_COL="$(awk -v FS='\t' '{for(i=1;i<=NF;i++) if($i=="pos(1-based)"){print i; exit}}' <<<"$HEADER")"
        [[ "$CHR_COL" =~ ^[0-9]+$ && "$POS_COL" =~ ^[0-9]+$ ]] \
            || die "could not find #chr/pos(1-based) columns in the file header"
        ( cd "$(dirname "$OUT")" && hts tabix -f -s "$CHR_COL" -b "$POS_COL" -e "$POS_COL" -S 1 "$(basename "$OUT")" )
    fi
    # Prove the index answers a region query before declaring success.
    FIRST_CONTIG="$( set +o pipefail; hts tabix -l "$OUT" 2>/dev/null | head -1 )"
    [[ -n "$FIRST_CONTIG" ]] || die "tabix cannot list contigs from $OUT; the file or index is unusable"
    hts tabix "$OUT" "${FIRST_CONTIG}:1-2000000" >/dev/null \
        || die "tabix region query failed on $OUT"
    log "tabix query check passed (first contig: $FIRST_CONTIG)"
    log "dbNSFP ready: $OUT"
    log "Set plugins.dbNSFP.path to this file (already the default) and enable it in the config."
    exit 0
fi

# ---------------------------------------------------------------------------
# Layout 2: legacy per-chromosome release — merge and re-sort on GRCh38
# ---------------------------------------------------------------------------
[[ -d "$SRC_DIR" ]] || die "dbNSFP source dir not found: $SRC_DIR"
CHR_FILES=( "$SRC_DIR"/dbNSFP*variant.chr* )
[[ ${#CHR_FILES[@]} -gt 0 ]] \
    || die "no pre-built dbNSFP*grch38.gz and no dbNSFP*variant.chr* files under $SRC_DIR"
log "found ${#CHR_FILES[@]} per-chromosome dbNSFP files (legacy layout; genome-wide re-sort needed)"

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
