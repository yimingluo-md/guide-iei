#!/usr/bin/env bash
# Build and inspect the actual standalone app on a macOS CI runner.
set -euo pipefail

if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "SKIP: native macOS release test requires macOS"
    exit 0
fi
if ! command -v xcrun >/dev/null 2>&1 || ! xcrun --find clang >/dev/null 2>&1; then
    echo "SKIP: native macOS release test requires Xcode clang on the build machine"
    exit 0
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="$(tr -d '[:space:]' < "${ROOT}/VERSION")"
TEMP="$(mktemp -d "${TMPDIR:-/tmp}/guide-iei-release-test.XXXXXX")"
trap 'rm -rf "$TEMP"' EXIT

IEI_REQUIRE_NATIVE_MAC_LAUNCHER=1 \
    bash "${ROOT}/scripts/build_macos_release.sh" "$TEMP"
ZIP="${TEMP}/GUIDE-IEI-macOS-${VERSION}.zip"
ditto -x -k "$ZIP" "${TEMP}/expanded"
APP="${TEMP}/expanded/GUIDE-IEI.app"
EXECUTABLE="${APP}/Contents/MacOS/GUIDE-IEI"

file "$EXECUTABLE" | grep -Fq 'universal binary'
lipo -archs "$EXECUTABLE" | grep -q 'arm64'
lipo -archs "$EXECUTABLE" | grep -q 'x86_64'
codesign --verify --deep --strict "$APP"
plutil -lint "${APP}/Contents/Info.plist" >/dev/null
test "$(plutil -extract LSMinimumSystemVersion raw -o - "${APP}/Contents/Info.plist")" = "13.0"
test "$(plutil -extract CFBundleShortVersionString raw -o - "${APP}/Contents/Info.plist")" = "$VERSION"

SUPPORT="${TEMP}/Library/Application Support/GUIDE-IEI"
launch_output="$(
    IEI_APP_SUPPORT_DIR="$SUPPORT" IEI_LAUNCHER_DRY_RUN=1 \
        "$EXECUTABLE"
)"
echo "$launch_output" | grep -Fq "ROOT=${SUPPORT}/application"
test -x "${SUPPORT}/application/scripts/start_workbench.sh"
echo "MACOS STANDALONE RELEASE TEST PASSED"
