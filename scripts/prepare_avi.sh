#!/usr/bin/env bash
# Prepare a local AVI ZIP. Never delete or rewrite the supplied archive.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${HERE}/.." && pwd)"
source "${HERE}/lib.sh"
ARCHIVE="${1:?usage: prepare_avi.sh archive.zip [config.yaml]}"
CONFIG="${2:-${ROOT}/config/annotation.config.yaml}"
DEST="$(yaml_get "$CONFIG" custom_tracks.AlphaGenomeAVI.dest_dir)"
[[ -n "$DEST" ]] || die "missing custom_tracks.AlphaGenomeAVI.dest_dir"
[[ "$DEST" = /* ]] || DEST="${ROOT}/${DEST}"
mkdir -p "$DEST"
ARCHIVE="$(cd "$(dirname "$ARCHIVE")" && pwd -P)/$(basename "$ARCHIVE")"
DEST="$(cd "$DEST" && pwd -P)"
RUNTIME="$(yaml_get "$CONFIG" container.runtime)"; RUNTIME="${RUNTIME:-docker}"
IMAGE="$(yaml_get "$CONFIG" container.image)"; IMAGE="${IMAGE:-vep-annotate:latest}"
export RUNTIME IMAGE
# Unique build directory prevents compiler races; failed conversion chunks
# are retained under the destination for restart, independent of this binary.
BUILD="$(mktemp -d "${DEST}/.avi-compiler.XXXXXX")"
trap 'rm -rf -- "$BUILD"' EXIT
HOST_COMPILER=1
# /usr/bin/c++ exists as an installer stub on clean Macs. Do not trigger an
# Xcode installation dialog: the already-required VEP image has a compiler.
if [[ "$(uname -s)" == Darwin ]] && ! xcode-select -p >/dev/null 2>&1; then
  HOST_COMPILER=0
fi
if [[ "$HOST_COMPILER" == 1 && "${AVI_FORCE_CONTAINER:-0}" != 1 ]] && command -v c++ >/dev/null 2>&1 && c++ -O3 -std=c++17 "${ROOT}/pipeline/avi_convert.cpp" -lz -o "${BUILD}/avi_convert"; then
  python3 "${ROOT}/pipeline/avi_dataset.py" prepare --archive "$ARCHIVE" --root "$DEST" --converter "${BUILD}/avi_convert" --workers "${AVI_WORKERS:-4}"
else
  export HTS_VIA_CONTAINER=1
  hts c++ -O3 -std=c++17 "${ROOT}/pipeline/avi_convert.cpp" -lz -o "${BUILD}/avi_convert"
  hts python3 "${ROOT}/pipeline/avi_dataset.py" prepare --archive "$ARCHIVE" --root "$DEST" --converter "${BUILD}/avi_convert" --workers "${AVI_WORKERS:-4}"
fi
