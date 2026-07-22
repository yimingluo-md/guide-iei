#!/usr/bin/env bash
# =============================================================================
# sync_to_onedrive.sh — copy this repo's working tree into a OneDrive (or any
# cloud-synced) folder, WITHOUT the .git directory.
#
# WHY: OneDrive's file provider blocks creation of `.git` directories
# ("Operation not permitted"), and even where it doesn't, syncing git's many
# small internal object files mid-write tends to corrupt the repo. So the
# recommended layout is:
#
#     ~/repos/<this-repo>        <- the real git clone (pull/push here)
#     <OneDrive>/.../vep-annotate <- a plain synced snapshot (edit/run here)
#
# Run this from the git clone after a `git pull` to refresh the OneDrive copy.
#
# Usage:
#   scripts/sync_to_onedrive.sh [DEST]
#
#   DEST defaults to the path below (edit ONEDRIVE_DEFAULT for your machine),
#   or set it via the ONEDRIVE_DEST environment variable, or pass it as $1.
#
# Safe by design: uses `rsync -a --exclude=.git`. Add --delete only if you want
# the destination pruned to match the source exactly (off by default so it
# never removes files you keep alongside the repo).
# =============================================================================
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$(cd "${HERE}/.." && pwd)"

# --- destination -------------------------------------------------------------
ONEDRIVE_DEFAULT="/Users/yl3232/Library/CloudStorage/OneDrive-ColumbiaUniversityIrvingMedicalCenter/diagnostic_pipeline/vep-annotate"
DEST="${1:-${ONEDRIVE_DEST:-$ONEDRIVE_DEFAULT}}"

PRUNE=0
for a in "$@"; do [[ "$a" == "--delete" ]] && PRUNE=1; done

command -v rsync >/dev/null 2>&1 || { echo "ERROR: rsync not found" >&2; exit 1; }
[[ -f "${SRC}/README.md" && -d "${SRC}/scripts" ]] || { echo "ERROR: ${SRC} does not look like this repo" >&2; exit 1; }

mkdir -p "$DEST" || { echo "ERROR: cannot create/access DEST: $DEST" >&2; exit 1; }

RSYNC_ARGS=(-a --exclude='.git' --exclude='.git/' --exclude='__pycache__/' \
            --exclude='.DS_Store' --exclude='references/' --exclude='results/' \
            --exclude='test/fixtures/' --exclude='test/out/')
[[ "$PRUNE" == "1" ]] && RSYNC_ARGS+=(--delete)

echo "syncing working tree -> $DEST"
echo "  source : $SRC"
[[ "$PRUNE" == "1" ]] && echo "  mode   : --delete (destination pruned to match source)"
rsync "${RSYNC_ARGS[@]}" "${SRC}/" "${DEST}/"

N=$(find "$DEST" -type f ! -path '*/.git/*' | wc -l | tr -d ' ')
echo "done. $N files in $DEST"
echo "NOTE: this is a plain snapshot (no .git). Do git pull/push in the clone at:"
echo "      $SRC"
