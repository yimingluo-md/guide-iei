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
#      columns, then bgzipped and indexed (~200 GB scratch on the destination
#      volume, hours). Rows without GRCh38 coordinates are skipped.
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
    if [[ ! "$PREBUILT" -ef "$OUT" && "$(basename "$PREBUILT")" != "$(basename "$OUT")" ]]; then
        warn "source file $(basename "$PREBUILT") differs from configured $(basename "$OUT");"
        warn "installing under the source's own name — update plugins.dbNSFP.path and .version to match"
        OUT="$(dirname "$OUT")/$(basename "$PREBUILT")"
    fi
    # MD5 sidecar (published alongside the official download)
    MD5_SIDECAR="${PREBUILT}.md5"
    if [[ "$PREBUILT" -ef "$OUT" && ! -e "$MD5_SIDECAR" ]]; then MD5_SIDECAR="${OUT}.md5"; fi
    if [[ -e "$MD5_SIDECAR" ]]; then
        WANT_MD5="$(read_md5_sidecar "$MD5_SIDECAR")" || die "invalid dbNSFP MD5 sidecar: $MD5_SIDECAR"
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

    transfer=cp
    if [[ "$PREBUILT" -ef "$OUT" ]]; then
        # Preserve the installed file and its companions even when the caller
        # supplies a different directory, filename, or hard link.
        if [[ ! -s "${OUT}.tbi" && -s "${PREBUILT}.tbi" ]]; then
            cp "${PREBUILT}.tbi" "${OUT}.tbi"
        fi
    else
        log "installing to $OUT"
        if [[ "$CONSUME_SOURCE" == "--remove-source-after-success" ]]; then
            transfer=mv
        fi
        "$transfer" -f "$PREBUILT" "$OUT"
        if [[ -s "${PREBUILT}.tbi" && ! "${PREBUILT}.tbi" -ef "${OUT}.tbi" ]]; then
            "$transfer" -f "${PREBUILT}.tbi" "${OUT}.tbi"
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
    if [[ "$transfer" == mv ]]; then rm -f "${PREBUILT}.md5"; fi
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

STAGE="$(mktemp -d "$(dirname "$OUT")/.dbnsfp.XXXXXX")"
trap 'rm -rf "$STAGE"' EXIT
trap 'exit 130' INT
trap 'exit 143' HUP TERM
PLAIN="$STAGE/table"
log "merging + sorting on GRCh38 coordinates (this takes a while)..."
{
    printf '%s\n' "$HEADER"
    for f in "${CHR_FILES[@]}"; do
        gzip -cd "$f" | LC_ALL=C awk -F '\t' -v chr="$CHR_COL" -v pos="$POS_COL" -v file="$f" '
            NR == 1 {next}
            $chr == "" || $chr == "." || $pos !~ /^[0-9]+$/ || $pos + 0 < 1 {skipped++; next}
            {print}
            END {if (skipped) print file ": skipped " skipped " rows without GRCh38 coordinates" > "/dev/stderr"}
        ' || exit 1
    done | LC_ALL=C sort -T "$STAGE" -t$'\t' -k"${CHR_COL},${CHR_COL}" -k"${POS_COL},${POS_COL}n"
} > "$PLAIN"

# Query a coordinate known to exist. A region query returning no rows can
# still exit successfully with a mismatched index.
FIRST_ROW="$(sed -n '2{p;q;}' "$PLAIN")"
[[ -n "$FIRST_ROW" ]] || die "no rows have a usable GRCh38 coordinate"
REGION="$(awk -F '\t' -v chr="$CHR_COL" -v pos="$POS_COL" '{print $chr ":" $pos "-" $pos}' <<<"$FIRST_ROW")"

log "bgzip + tabix (chr=col$CHR_COL pos=col$POS_COL, skip header line)"
( cd "$STAGE" && hts bgzip -f table )
bgzf_complete "$PLAIN.gz" || die "bgzip produced an incomplete dbNSFP file"
index_bgzf "$PLAIN.gz" "" "$STAGE/index.log" -s "$CHR_COL" -b "$POS_COL" -e "$POS_COL" -S 1 \
    || die "failed to index the staged dbNSFP file"
hts tabix "$PLAIN.gz" "$REGION" > "$STAGE/query" || die "tabix query failed at $REGION"
[[ "$(head -1 "$STAGE/query")" == "$FIRST_ROW" ]] || die "tabix did not return the expected dbNSFP row at $REGION"

# Serialize publishers and restore the previous pair on a failed rename.
# A rebuilt file supersedes its previous checksum and both types of index.
python3 - "$STAGE" "$OUT" <<'PY'
import fcntl
import os
import signal
import sys
from pathlib import Path

stage, output = map(Path, sys.argv[1:])
index = Path(str(output) + ".tbi")
targets = [index, Path(str(output) + ".csi"), Path(str(output) + ".md5"), output]
directory = os.open(output.parent, os.O_RDONLY)
try:
    fcntl.flock(directory, fcntl.LOCK_EX)
    # Let the shell act on cancellation after this short rename sequence.
    signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGHUP, signal.SIGINT, signal.SIGTERM})
    backups = {}
    installed = []
    try:
        for n, target in enumerate(targets):
            if target.is_file() or target.is_symlink():
                backup = stage / f"previous-{n}"
                os.replace(target, backup)
                backups[target] = backup
            elif target.exists():
                raise ValueError(f"Expected a file at {target}")
        for source, target in [(stage / "table.gz", output), (stage / "table.gz.tbi", index)]:
            os.replace(source, target)
            installed.append(target)
    except BaseException:
        for target in reversed(installed):
            target.unlink()
        for target, backup in reversed(list(backups.items())):
            os.replace(backup, target)
        raise
finally:
    os.close(directory)
PY

if [[ "$CONSUME_SOURCE" == "--remove-source-after-success" ]]; then
    [[ -s "$OUT" && -s "${OUT}.tbi" ]] || die "refusing to remove dbNSFP source before installed output and index are present"
    rm -f -- "${CHR_FILES[@]}"
    log "removed ${#CHR_FILES[@]} downloaded dbNSFP chromosome files after successful managed installation"
fi

log "dbNSFP ready: $OUT"
log "Set plugins.dbNSFP.path to this file (already the default) and enable it in the config."
