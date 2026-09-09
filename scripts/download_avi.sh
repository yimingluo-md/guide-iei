#!/usr/bin/env bash
# Download the verified preprocessed AVI mirror; no source ZIP or conversion.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${HERE}/.." && pwd)"
source "${HERE}/lib.sh"
CONFIG="${1:-${ROOT}/config/annotation.config.yaml}"
DEST="$(yaml_get "$CONFIG" custom_tracks.AlphaGenomeAVI.dest_dir)"
[[ -n "$DEST" ]] || die "missing AlphaGenome AVI destination"
[[ "$DEST" = /* ]] || DEST="${ROOT}/${DEST}"
exec python3 "${ROOT}/pipeline/avi_mirror.py" "$DEST"
