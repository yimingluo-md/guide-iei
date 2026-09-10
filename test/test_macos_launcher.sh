#!/usr/bin/env bash
# Regression coverage for the unsigned macOS launcher. Runs on Linux too: the
# resource script is plain Bash and dry-run mode never opens Finder/Terminal.
set -euo pipefail
# Dry-run path tests must not attach to a real workbench or another test's
# listener on port 3000. No TCP server can listen on destination port zero.
export IEI_UI_PORT=0

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LAUNCHER="${ROOT}/desktop/macos/GUIDE-IEI.app/Contents/Resources/launcher.sh"
INFO="${ROOT}/desktop/macos/GUIDE-IEI.app/Contents/Info.plist"
TMP_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/guide-iei-launcher-test.XXXXXX")"
trap 'rm -rf "$TMP_ROOT"' EXIT

echo "[1/5] source-tree launcher resolves the real repository"
source_output="$(IEI_LAUNCHER_DRY_RUN=1 bash "$LAUNCHER")"
echo "$source_output" | grep -Fq "ROOT=${ROOT}"
echo "$source_output" | grep -Fq "RUNNER=${ROOT}/desktop/macos/GUIDE-IEI-Workbench.command"

echo "[2/5] packaged launcher installs from an arbitrary translocated path"
RESOURCES="${TMP_ROOT}/AppTranslocation/random/d/GUIDE-IEI.app/Contents/Resources"
PAYLOAD_TREE="${TMP_ROOT}/payload/guide-iei"
SUPPORT="${TMP_ROOT}/Library/Application Support/GUIDE-IEI"
mkdir -p "$RESOURCES" "$PAYLOAD_TREE/scripts" "$PAYLOAD_TREE/desktop/macos"
cp "$LAUNCHER" "${RESOURCES}/launcher.sh"
printf '9.9.9\n' > "${PAYLOAD_TREE}/VERSION"
printf '#!/bin/bash\nexit 0\n' > "${PAYLOAD_TREE}/scripts/start_workbench.sh"
printf '#!/bin/bash\nexit 0\n' > "${PAYLOAD_TREE}/desktop/macos/GUIDE-IEI-Workbench.command"
chmod +x "${PAYLOAD_TREE}/scripts/start_workbench.sh" \
    "${PAYLOAD_TREE}/desktop/macos/GUIDE-IEI-Workbench.command"
tar -czf "${RESOURCES}/guide-iei-payload.tar.gz" -C "${TMP_ROOT}/payload" guide-iei
shasum -a 256 "${RESOURCES}/guide-iei-payload.tar.gz" \
    | awk '{print $1 "  guide-iei-payload.tar.gz"}' \
    > "${RESOURCES}/guide-iei-payload.sha256"
packaged_output="$(
    IEI_APP_SUPPORT_DIR="$SUPPORT" IEI_LAUNCHER_DRY_RUN=1 \
        bash "${RESOURCES}/launcher.sh"
)"
echo "$packaged_output" | grep -Fq "ROOT=${SUPPORT}/application"
test -x "${SUPPORT}/application/scripts/start_workbench.sh"

echo "[3/5] an existing writable installation is reused"
printf 'locally updated\n' > "${SUPPORT}/application/local-state.txt"
IEI_APP_SUPPORT_DIR="$SUPPORT" IEI_LAUNCHER_DRY_RUN=1 \
    bash "${RESOURCES}/launcher.sh" >/dev/null
grep -Fq 'locally updated' "${SUPPORT}/application/local-state.txt"

echo "[4/5] an incomplete existing installation fails with recovery guidance"
INCOMPLETE_SUPPORT="${TMP_ROOT}/incomplete-support"
mkdir -p "${INCOMPLETE_SUPPORT}/application"
if IEI_APP_SUPPORT_DIR="$INCOMPLETE_SUPPORT" IEI_LAUNCHER_DRY_RUN=1 \
    bash "${RESOURCES}/launcher.sh" >"${TMP_ROOT}/incomplete.out" 2>"${TMP_ROOT}/incomplete.err"; then
    echo "ERROR: incomplete existing installation unexpectedly succeeded" >&2
    exit 1
fi
grep -Fq 'existing GUIDE-IEI installation is incomplete' "${TMP_ROOT}/incomplete.err"

echo "[5/5] damaged payloads fail visibly before extraction"
BAD_SUPPORT="${TMP_ROOT}/bad-support"
printf '%064d  guide-iei-payload.tar.gz\n' 0 \
    > "${RESOURCES}/guide-iei-payload.sha256"
if IEI_APP_SUPPORT_DIR="$BAD_SUPPORT" IEI_LAUNCHER_DRY_RUN=1 \
    bash "${RESOURCES}/launcher.sh" >"${TMP_ROOT}/bad.out" 2>"${TMP_ROOT}/bad.err"; then
    echo "ERROR: corrupt payload unexpectedly succeeded" >&2
    exit 1
fi
grep -Fq 'payload is incomplete or damaged' "${TMP_ROOT}/bad.err"

grep -A3 -F '<key>LSArchitecturePriority</key>' "$INFO" | grep -Fq '<string>arm64</string>'
grep -Fq '<key>LSMinimumSystemVersion</key><string>13.0</string>' "$INFO"
if [[ "$(uname -s)" == "Darwin" ]]; then
    plutil -lint "$INFO" >/dev/null
fi
echo "ALL MACOS LAUNCHER TESTS PASSED"
