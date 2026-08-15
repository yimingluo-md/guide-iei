#!/usr/bin/env bash
# Download the prepared SCREEN Registry V4 tissue/immune context bundle from
# the public Hugging Face mirror — the fast path (~1.5 GB) that replaces
# ~32 GB of source downloads and hours of processing.
#
# Every file is pinned to the SHA-256 recorded at publication; a mirror can
# never silently serve altered data. Reproducible from-source build:
#   scripts/prepare_screen_ccre_data.sh <data_root> [workers]
#
# Usage: scripts/download_screen_context_bundle.sh <data_root>
#   Files land in <data_root>/prepared/ (the layout the workbench expects).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${HERE}/lib.sh"

DATA_ROOT="${1:-}"
[[ -n "${DATA_ROOT}" ]] \
    || die "usage: scripts/download_screen_context_bundle.sh /path/to/screen-context"
mkdir -p "${DATA_ROOT}/prepared"
DATA_ROOT="$(cd "${DATA_ROOT}" && pwd -P)"
DEST="${DATA_ROOT}/prepared"

MIRROR="https://huggingface.co/datasets/luoyiming1991/screen-registry-v4-immune-contexts/resolve/main"

# name<space>sha256, pinned at publication (2026-08-15, Registry V4).
BUNDLE_FILES="\
screen.registry-v4.catalog.sqlite3 9217fddfbca7707069820cc4381250e4dba1b5c999ed3621cd1618617c62238c
screen.registry-v4.immune-context-members.tsv e07b2430312f2385c3a797b0e3d6d735d54ae3c93770561a20b3b7f254996088
screen.registry-v4.immune-contexts.json 3ef4365a4ca35365a9e073c2c428f1ed99a64315c0739b218bf5704b20ff64dd
screen.registry-v4.immune-contexts.sqlite3 fd4e312d99e10744651f2bac46af14d988fef7cdc34d32c7417834f94126b1f7
screen.registry-v4.immune.u8 9d7eb30061f305df68425cfab6ddd24c8ee0994464ecc30076eb30d6b4ad6add
screen.registry-v4.prepared.json 223847d8b76f9e77dfde2cac7846de5cd31bd3dbc3257ad5b4f7178a031ac4a9
screen.registry-v4.tissues.u8 a3b3848d0ac3594d36e0711ce765d59c1a657ac70a42a457c3a02c9bc896dba9"

file_sha256() {
    if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | awk '{print $1}'
    else shasum -a 256 "$1" | awk '{print $1}'; fi
}

TOTAL=$(printf '%s\n' "$BUNDLE_FILES" | wc -l | tr -d ' ')
INDEX=0
printf '%s\n' "$BUNDLE_FILES" | while read -r name want_sha; do
    INDEX=$((INDEX + 1))
    target="${DEST}/${name}"
    if [[ -s "$target" ]] && [[ "$(file_sha256 "$target")" == "$want_sha" ]]; then
        log "[$INDEX/$TOTAL] present and verified: $name"
        continue
    fi
    rm -f "$target" "$target.part"
    log "[$INDEX/$TOTAL] downloading $name"
    fetch "${MIRROR}/${name}" "$target" || die "download failed: $name"
    got_sha="$(file_sha256 "$target")"
    if [[ "$got_sha" != "$want_sha" ]]; then
        rm -f "$target"
        die "checksum mismatch for $name: expected $want_sha, got $got_sha — refusing to install"
    fi
    log "[$INDEX/$TOTAL] verified: $name"
done

log "prepared SCREEN context bundle ready under ${DEST}"
log "provenance and per-file checksums: ${DEST}/screen.registry-v4.immune-contexts.json"
