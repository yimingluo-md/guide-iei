#!/usr/bin/env bash
# Build the standalone unsigned macOS app distributed to nontechnical users.
# A Developer ID is deliberately optional: on macOS the app receives a local
# ad-hoc signature and users approve it once in Privacy & Security.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${HERE}/.." && pwd)"
DIST="${1:-${ROOT}/dist}"
VERSION="$(tr -d '[:space:]' < "${ROOT}/VERSION")"
[[ -n "$VERSION" ]] || { echo "ERROR: VERSION is empty" >&2; exit 1; }

command -v git >/dev/null 2>&1 || {
    echo "ERROR: git is required to build the release payload" >&2
    exit 1
}
command -v shasum >/dev/null 2>&1 || {
    echo "ERROR: shasum is required to build the macOS release" >&2
    exit 1
}

mkdir -p "$DIST"
DIST="$(cd "$DIST" && pwd)"
STAGING="$(mktemp -d "${TMPDIR:-/tmp}/guide-iei-macos.XXXXXX")"
trap 'rm -rf "$STAGING"' EXIT

APP="${STAGING}/GUIDE-IEI.app"
TEMPLATE="${ROOT}/desktop/macos/GUIDE-IEI.app"
if [[ "$(uname -s)" == "Darwin" ]] && command -v ditto >/dev/null 2>&1; then
    # Never copy a developer machine's quarantine/provenance metadata into a
    # release. The downloaded ZIP receives fresh quarantine on the user's Mac.
    ditto --noextattr --noqtn "$TEMPLATE" "$APP"
else
    cp -R "$TEMPLATE" "$APP"
fi

PAYLOAD="${APP}/Contents/Resources/guide-iei-payload.tar.gz"
git -C "$ROOT" archive --format=tar.gz --prefix=guide-iei/ -o "$PAYLOAD" HEAD
shasum -a 256 "$PAYLOAD" | awk '{print $1 "  guide-iei-payload.tar.gz"}' \
    > "${APP}/Contents/Resources/guide-iei-payload.sha256"

# Keep Finder's displayed version aligned with VERSION without requiring
# PlistBuddy (the builder remains usable on Linux for a shell-shim fallback).
sed -i.bak \
    -e "s#<key>CFBundleVersion</key><string>[^<]*</string>#<key>CFBundleVersion</key><string>${VERSION}</string>#" \
    -e "s#<key>CFBundleShortVersionString</key><string>[^<]*</string>#<key>CFBundleShortVersionString</key><string>${VERSION}</string>#" \
    "${APP}/Contents/Info.plist"
rm -f "${APP}/Contents/Info.plist.bak"

NATIVE_LAUNCHER=0
if [[ "$(uname -s)" == "Darwin" ]] && command -v xcrun >/dev/null 2>&1 \
   && xcrun --find clang >/dev/null 2>&1; then
    xcrun clang -Wall -Wextra -Werror -Os \
        -arch arm64 -arch x86_64 -mmacosx-version-min=13.0 \
        "${ROOT}/desktop/macos/GUIDE-IEI-launcher.c" \
        -o "${APP}/Contents/MacOS/GUIDE-IEI"
    NATIVE_LAUNCHER=1
    # Free ad-hoc signing seals the bundle but does not claim an Apple-trusted
    # publisher identity. Gatekeeper still presents the documented one-time
    # manual approval, which is intentional until Developer ID is adopted.
    codesign --force --sign - --timestamp=none "$APP"
    codesign --verify --deep --strict "$APP"
elif [[ "${IEI_REQUIRE_NATIVE_MAC_LAUNCHER:-0}" == "1" ]]; then
    echo "ERROR: a macOS release requires Xcode clang for the universal launcher" >&2
    exit 1
else
    echo "WARNING: Xcode clang unavailable; packaging the architecture-declared shell shim" >&2
fi

ZIP="${DIST}/GUIDE-IEI-macOS-${VERSION}.zip"
rm -f "$ZIP"
if [[ "$(uname -s)" == "Darwin" ]] && command -v ditto >/dev/null 2>&1; then
    ditto -c -k --sequesterRsrc --keepParent "$APP" "$ZIP"
else
    command -v zip >/dev/null 2>&1 || {
        echo "ERROR: zip is required outside macOS" >&2
        exit 1
    }
    (cd "$STAGING" && zip -qry "$ZIP" GUIDE-IEI.app)
fi

echo "built $(basename "$ZIP") (native universal launcher: ${NATIVE_LAUNCHER})"
