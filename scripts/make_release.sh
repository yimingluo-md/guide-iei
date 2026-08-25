#!/usr/bin/env bash
# =============================================================================
# make_release.sh — build the release artifacts the in-app updater consumes.
#
#   scripts/make_release.sh            build dist/ for the version in VERSION
#   scripts/make_release.sh --publish  also create the GitHub release (gh)
#
# Produces, in dist/:
#   guide-iei-<version>.zip          every git-tracked file at HEAD, plus
#                                    release-manifest.txt (updater file list)
#   GUIDE-IEI-macOS-<version>.zip    standalone unsigned Mac application
#   sha256sums.txt                   checksums for both archives
#   release-notes.md          this version's CHANGELOG section
#
# The zip is built from git's index (git archive), so nothing untracked —
# datasets, results, local state — can ever leak into a release.
# =============================================================================
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${HERE}/.." && pwd)"
cd "$ROOT"

die() { echo "ERROR: $*" >&2; exit 1; }

PUBLISH=0
[[ "${1:-}" == "--publish" ]] && PUBLISH=1

VERSION="$(tr -d '[:space:]' < VERSION)"
[[ -n "$VERSION" ]] || die "VERSION file is empty"
TAG="v${VERSION}"

git diff --quiet && git diff --cached --quiet \
    || die "the working tree has uncommitted changes; commit them first"
grep -q "^## ${VERSION} " CHANGELOG.md \
    || die "CHANGELOG.md has no '## ${VERSION} — <date>' entry"

mkdir -p dist
ZIP="dist/guide-iei-${VERSION}.zip"
MAC_ZIP="dist/GUIDE-IEI-macOS-${VERSION}.zip"
rm -f "$ZIP" "$MAC_ZIP" dist/sha256sums.txt dist/release-notes.md release-manifest.txt

# The manifest is the exact list of files this release owns: the updater
# replaces these, deletes what a previous release owned that this one no
# longer ships, and touches nothing else.
git ls-files > release-manifest.txt
trap 'rm -f release-manifest.txt' EXIT
git archive --format=zip -o "$ZIP" HEAD
# Append the manifest via python (git archive --add-file needs git >= 2.30,
# and python3 is already a hard dependency of the pipeline).
python3 - "$ZIP" release-manifest.txt <<'PY'
import sys, zipfile
zip_path, manifest = sys.argv[1:3]
with zipfile.ZipFile(zip_path, "a") as bundle:
    bundle.write(manifest, "release-manifest.txt")
PY

shasum -a 256 "$ZIP" | awk -v name="$(basename "$ZIP")" '{print $1 "  " name}' \
    > dist/sha256sums.txt

# The standalone app embeds the same git-tracked source as a verified payload,
# installs it into Application Support on first launch, and therefore works
# even when Gatekeeper translocates the unsigned .app.
bash scripts/build_macos_release.sh dist
shasum -a 256 "$MAC_ZIP" | awk -v name="$(basename "$MAC_ZIP")" '{print $1 "  " name}' \
    >> dist/sha256sums.txt

# This version's CHANGELOG section becomes the release notes.
awk -v version="$VERSION" '
    $0 ~ "^## " version " " { active = 1; next }
    active && /^## /        { exit }
    active                  { print }
' CHANGELOG.md > dist/release-notes.md
[[ -s dist/release-notes.md ]] || die "could not extract release notes for ${VERSION}"

echo "built:"
ls -la dist/
echo
if [[ "$PUBLISH" == "1" ]]; then
    command -v gh >/dev/null 2>&1 || die "--publish needs the gh CLI, authenticated"
    gh release view "$TAG" >/dev/null 2>&1 \
        && die "release ${TAG} already exists on GitHub"
    # gh release create makes the tag on the remote itself; pushing a bare
    # tag first would trigger the release workflow and the two publishers
    # would race to create the same release.
    gh release create "$TAG" "$ZIP" "$MAC_ZIP" dist/sha256sums.txt \
        --target "$(git rev-parse HEAD)" \
        --title "GUIDE-IEI ${VERSION}" --notes-file dist/release-notes.md
    echo "release ${TAG} published (the workflow skips tags that already have a release)"
else
    echo "next: git tag -a ${TAG} -m 'GUIDE-IEI ${VERSION}' && git push origin ${TAG}"
    echo "      (the release workflow publishes the assets automatically), or"
    echo "      re-run with --publish to publish from here."
fi
