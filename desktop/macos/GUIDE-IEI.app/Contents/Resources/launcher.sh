#!/bin/bash
# GUIDE-IEI one-click launcher (macOS).
#
# A packaged release carries guide-iei-payload.tar.gz beside this script. On
# first launch it is verified and installed into the user's Application
# Support folder, which remains writable and has a stable path even while
# Gatekeeper runs this app from an App Translocation directory. A source-tree
# copy has no payload and falls back to the repository surrounding the app.
set -u

HERE="$(cd "$(dirname "$0")" && pwd)"
UI_PORT="${IEI_UI_PORT:-3000}"
PAYLOAD="${HERE}/guide-iei-payload.tar.gz"
PAYLOAD_SHA_FILE="${HERE}/guide-iei-payload.sha256"
APP_SUPPORT="${IEI_APP_SUPPORT_DIR:-${HOME}/Library/Application Support/GUIDE-IEI}"
INSTALL_ROOT="${APP_SUPPORT}/application"
DRY_RUN="${IEI_LAUNCHER_DRY_RUN:-0}"

show_error() {
    local message="$1"
    printf 'GUIDE-IEI: %s\n' "$message" >&2
    if [[ "$DRY_RUN" != "1" ]] && command -v osascript >/dev/null 2>&1; then
        /usr/bin/osascript - "$message" <<'APPLESCRIPT' >/dev/null 2>&1 || true
on run argv
    display alert "GUIDE-IEI could not start" message (item 1 of argv) as critical buttons {"OK"} default button "OK"
end run
APPLESCRIPT
    fi
}

sha256_file() {
    if command -v shasum >/dev/null 2>&1; then
        shasum -a 256 "$1" | awk '{print $1}'
    elif command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | awk '{print $1}'
    else
        return 1
    fi
}

install_packaged_payload() {
    local expected actual temporary extracted
    [[ -f "$PAYLOAD_SHA_FILE" ]] || {
        show_error "The application payload checksum is missing. Download GUIDE-IEI again."
        return 1
    }
    expected="$(awk 'NR == 1 {print $1}' "$PAYLOAD_SHA_FILE")"
    actual="$(sha256_file "$PAYLOAD" 2>/dev/null || true)"
    if [[ ! "$expected" =~ ^[0-9a-fA-F]{64}$ || "$actual" != "$expected" ]]; then
        show_error "The application payload is incomplete or damaged. Download GUIDE-IEI again."
        return 1
    fi

    mkdir -p "$APP_SUPPORT" || {
        show_error "The application support folder could not be created: ${APP_SUPPORT}"
        return 1
    }
    temporary="$(mktemp -d "${APP_SUPPORT}/.install.XXXXXX")" || {
        show_error "A temporary installation folder could not be created."
        return 1
    }
    if ! tar -xzf "$PAYLOAD" -C "$temporary"; then
        rm -rf "$temporary"
        show_error "The application payload could not be unpacked. Download GUIDE-IEI again."
        return 1
    fi
    extracted="${temporary}/guide-iei"
    if [[ ! -f "${extracted}/VERSION" || ! -x "${extracted}/scripts/start_workbench.sh" ]]; then
        rm -rf "$temporary"
        show_error "The application payload does not contain a complete GUIDE-IEI installation."
        return 1
    fi

    # Never overwrite an existing installation: it may contain downloaded
    # references, an edited config, or a newer version installed in-app.
    if [[ -e "$INSTALL_ROOT" ]]; then
        rm -rf "$temporary"
        show_error "The existing GUIDE-IEI installation is incomplete: ${INSTALL_ROOT}. Rename or remove that folder, then open GUIDE-IEI again."
        return 1
    fi
    if ! mv "$extracted" "$INSTALL_ROOT"; then
        rm -rf "$temporary"
        show_error "GUIDE-IEI could not be installed in ${INSTALL_ROOT}."
        return 1
    fi
    rmdir "$temporary" 2>/dev/null || true
    printf '%s\n' "$actual" > "${APP_SUPPORT}/installed-payload.sha256"
}

if curl -s -m 2 "http://127.0.0.1:${UI_PORT}" 2>/dev/null | grep -q "GUIDE-IEI"; then
    if [[ "$DRY_RUN" == "1" ]]; then
        printf 'URL=http://127.0.0.1:%s\n' "$UI_PORT"
        exit 0
    fi
    open "http://127.0.0.1:${UI_PORT}"
    exit 0
fi

if [[ -f "$PAYLOAD" ]]; then
    if [[ ! -x "${INSTALL_ROOT}/scripts/start_workbench.sh" ]]; then
        install_packaged_payload || exit 1
    fi
    ROOT="$INSTALL_ROOT"
else
    # Development/source-tree mode. This path intentionally is not used by
    # the standalone release because App Translocation breaks navigation to
    # siblings outside the .app bundle.
    ROOT="$(cd "${HERE}/../../../../.." && pwd)"
fi

RUNNER="${ROOT}/desktop/macos/GUIDE-IEI-Workbench.command"
if [[ ! -f "$RUNNER" || ! -f "${ROOT}/scripts/start_workbench.sh" ]]; then
    if [[ -f "$PAYLOAD" ]]; then
        show_error "The installed GUIDE-IEI application is incomplete: ${ROOT}. Rename or remove that folder, then open GUIDE-IEI again."
    else
        show_error "This source-tree app was separated from its repository by macOS. Use the standalone macOS release, or move the entire GUIDE-IEI folder to Documents and open GUIDE-IEI-Workbench.command."
    fi
    exit 1
fi
if [[ ! -x "$RUNNER" ]]; then chmod +x "$RUNNER" 2>/dev/null || true; fi

if [[ "$DRY_RUN" == "1" ]]; then
    printf 'ROOT=%s\nRUNNER=%s\n' "$ROOT" "$RUNNER"
    exit 0
fi
exec open -a Terminal "$RUNNER"
