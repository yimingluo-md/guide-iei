#!/usr/bin/env bash
# Assemble source/update and verified signed Mac assets. Never publish automatically.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${HERE}/.." && pwd)"
cd "$ROOT"
die() { echo "ERROR: $*" >&2; exit 1; }
MAC_ARTIFACTS=""
SOURCE_ONLY=0
DRAFT=0
OUTPUT=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --source-only) SOURCE_ONLY=1; shift ;;
        --macos-artifacts) [[ $# -ge 2 ]] || die "--macos-artifacts needs a directory"; MAC_ARTIFACTS="$2"; shift 2 ;;
        --output) [[ $# -ge 2 ]] || die "--output needs a directory"; OUTPUT="$2"; shift 2 ;;
        --draft) DRAFT=1; shift ;;
        *) die "usage: make_release.sh (--source-only | --macos-artifacts DIR) [--output DIR] [--draft]; automatic publication is disabled" ;;
    esac
done
[[ "$SOURCE_ONLY" == 1 && -z "$MAC_ARTIFACTS" || "$SOURCE_ONLY" == 0 && -n "$MAC_ARTIFACTS" ]] \
    || die "choose --source-only or --macos-artifacts DIR"
[[ "$DRAFT" == 0 || -n "$MAC_ARTIFACTS" ]] || die "--draft requires verified Mac artifacts"
[[ -z "$(git status --porcelain)" ]] || die "commit reviewed changes first; the working tree must be clean"
VERSION="$(tr -d '[:space:]' < VERSION)"
[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "invalid VERSION"
grep -Fq "## ${VERSION} — " CHANGELOG.md || die "missing CHANGELOG entry for $VERSION"
TAG="v${VERSION}"
OUTPUT="${OUTPUT:-dist/release-${VERSION}}"
[[ ! -e "$OUTPUT" ]] || die "output already exists; choose a new --output directory"
if [[ -n "$MAC_ARTIFACTS" ]]; then
    python3 scripts/verify_macos_distribution.py "$MAC_ARTIFACTS"
fi
mkdir -p "$OUTPUT"
ZIP="${OUTPUT}/guide-iei-${VERSION}.zip"
git archive --format=zip -o "$ZIP" HEAD
python3 - "$ZIP" <<'PY'
import subprocess, sys, zipfile
with zipfile.ZipFile(sys.argv[1], 'a') as archive:
    archive.writestr('release-manifest.txt', subprocess.check_output(['git', 'ls-files']))
PY
ASSETS=("$ZIP")
if [[ -n "$MAC_ARTIFACTS" ]]; then
    for suffix in zip dmg; do
        asset="GUIDE-IEI-macOS-${VERSION}-arm64.${suffix}"
        cp "${MAC_ARTIFACTS}/${asset}" "${OUTPUT}/${asset}"
        ASSETS+=("${OUTPUT}/${asset}")
    done
fi
for asset in "${ASSETS[@]}"; do
    shasum -a 256 "$asset" | awk -v name="$(basename "$asset")" '{print $1 "  " name}'
done > "${OUTPUT}/sha256sums.txt"
awk -v version="$VERSION" '
    index($0, "## " version " ") == 1 { active = 1; next }
    active && /^## / { exit }
    active { print }
' CHANGELOG.md > "${OUTPUT}/release-notes.md"
[[ -s "${OUTPUT}/release-notes.md" ]] || die "empty release notes"
if [[ "$DRAFT" == 1 ]]; then
    command -v gh >/dev/null || die "--draft needs authenticated gh"
    gh release view "$TAG" --repo yimingluo-md/guide-iei >/dev/null 2>&1 && die "release already exists; inspect it before attaching assets"
    gh release create "$TAG" "${ASSETS[@]}" "${OUTPUT}/sha256sums.txt" \
        --repo yimingluo-md/guide-iei --target "$(git rev-parse HEAD)" \
        --draft --prerelease --title "GUIDE-IEI ${VERSION} — Apple Silicon beta" \
        --notes-file "${OUTPUT}/release-notes.md"
fi
echo "Assembled $OUTPUT. Nothing published. Complete docs/RELEASE_CHECKLIST.md before publication."
