#!/usr/bin/env bash
# Start the loopback-only annotation service and browser UI on one workstation.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${HERE}/.." && pwd)"
SERVICE_PORT="${IEI_SERVICE_PORT:-43117}"
SERVICE_START_TIMEOUT="${IEI_SERVICE_START_TIMEOUT:-120}"
UI_PORT="${IEI_UI_PORT:-3000}"
UI_URL="http://127.0.0.1:${UI_PORT}"

# --bootstrap is the double-click entry path (GUIDE-IEI.app on macOS, the
# Windows launcher via WSL): prepare the environment on first use, then
# start the workbench and open the browser — no typed commands.
BOOTSTRAP=0
for arg in "$@"; do
    case "$arg" in
        --bootstrap) BOOTSTRAP=1 ;;
    esac
done

open_browser() {
    if command -v open >/dev/null 2>&1; then open "$1" 2>/dev/null || true
    elif grep -qi microsoft /proc/version 2>/dev/null; then
        # WSL: open the Windows-side default browser.
        powershell.exe -NoProfile -Command "Start-Process '$1'" >/dev/null 2>&1 || true
    elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$1" >/dev/null 2>&1 || true
    fi
}

ui_is_guide_iei() {
    curl -s -m 2 "$UI_URL" 2>/dev/null | grep -q "GUIDE-IEI"
}
service_is_guide_iei() {
    # The service answers long before the UI does on a first launch (npm ci
    # and the production build run in between), so a second double-click
    # must be recognised from the SERVICE port too — a second service on the
    # same state used to disturb the live one before failing to bind.
    curl -s -m 2 "http://127.0.0.1:${SERVICE_PORT}/api/health" 2>/dev/null | grep -q '"ok"'
}

# Single instance: if the workbench is already up, just open it.
if ui_is_guide_iei; then
    echo "GUIDE-IEI is already running at ${UI_URL}."
    if [[ "$BOOTSTRAP" == "1" ]]; then open_browser "$UI_URL"; fi
    exit 0
fi
if curl -s -m 2 -o /dev/null "$UI_URL" 2>/dev/null; then
    echo "ERROR: port ${UI_PORT} is in use by another application." >&2
    echo "Set IEI_UI_PORT to a free port and start again." >&2
    exit 1
fi
if service_is_guide_iei; then
    echo "GUIDE-IEI's annotation service is already running on 127.0.0.1:${SERVICE_PORT}"
    echo "(another launch is probably still preparing the interface). Wait for that"
    echo "window to finish, or stop it before starting again."
    exit 0
fi

if [[ "$BOOTSTRAP" == "1" ]]; then
    # First use: the FULL setup check (container included) decides whether to
    # install — checking with --skip-container skipped the very container
    # setup the guide promises whenever Python/Node happened to be ready.
    if ! bash "${HERE}/setup_environment.sh" --check; then
        echo
        echo "== First-time preparation: installing the environment."
        if grep -qi microsoft /proc/version 2>/dev/null; then
            echo "   Ubuntu may ask for the password created during its first-run setup."
        else
            echo "   This happens once and needs no administrator password on a Mac."
        fi
        # A container item that cannot be fixed unattended (Docker Desktop
        # installed but not running, say) must not brick the launch: the
        # review workbench works without the annotation runtime.
        if ! bash "${HERE}/setup_environment.sh" --install --yes; then
            echo
            echo "== Some items above still need attention (usually the container"
            echo "   runtime). The review workbench starts anyway; annotation"
            echo "   will ask for the runtime when it needs it."
        fi
    fi
fi

if [[ ! "$SERVICE_START_TIMEOUT" =~ ^[1-9][0-9]*$ ]]; then
    echo "ERROR: IEI_SERVICE_START_TIMEOUT must be a positive number of seconds." >&2
    exit 2
fi

# A factory-fresh Mac exposes /usr/bin/python3 as an Xcode installation stub,
# not a usable interpreter. Prefer any real host Python, then the managed
# native runtime installed by setup_environment.sh.
PYTHON_BIN="${IEI_PYTHON_BIN:-}"
python_is_usable() {
    "$1" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)' \
        >/dev/null 2>&1
}
if [[ -n "$PYTHON_BIN" ]] && ! python_is_usable "$PYTHON_BIN"; then
    PYTHON_BIN=""
fi
if [[ -z "$PYTHON_BIN" ]] && command -v python3 >/dev/null 2>&1 \
   && python_is_usable "$(command -v python3)"; then
    PYTHON_BIN="$(command -v python3)"
fi
if [[ -z "$PYTHON_BIN" ]]; then
    for candidate in \
        "${IEI_TOOLS_DIR:-$HOME/.iei-variant-review/tools}/bin/python3" \
        "${HOME}/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3"
    do
        if [[ -x "$candidate" ]] && python_is_usable "$candidate"; then
            PYTHON_BIN="$candidate"
            break
        fi
    done
fi
if [[ -z "$PYTHON_BIN" ]]; then
    cat >&2 <<'MESSAGE'
ERROR: A usable Python 3 runtime was not found.

The setup script installs the required runtime for this platform:
  bash scripts/setup_environment.sh --install
MESSAGE
    exit 127
fi
PYTHON_DIR="$(dirname "$PYTHON_BIN")"
export PATH="${PYTHON_DIR}:${PATH}"

# npm is not always on PATH in macOS GUI/managed shells. Find a compatible
# Node runtime first; the Codex desktop runtime is a useful last-resort fallback
# when this project is being developed from Codex.
NODE_BIN="${IEI_NODE_BIN:-}"
if [[ -z "$NODE_BIN" ]] && command -v node >/dev/null 2>&1; then
    NODE_BIN="$(command -v node)"
fi
if [[ -z "$NODE_BIN" ]]; then
    for candidate in \
        "${IEI_TOOLS_DIR:-$HOME/.iei-variant-review/tools}/bin/node" \
        /opt/homebrew/bin/node \
        /usr/local/bin/node \
        "${HOME}/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node"
    do
        if [[ -x "$candidate" ]]; then
            NODE_BIN="$candidate"
            break
        fi
    done
fi
if [[ -z "$NODE_BIN" ]]; then
    cat >&2 <<'MESSAGE'
ERROR: Node.js was not found.

The setup script installs it without admin rights:
  bash scripts/setup_environment.sh --install

Or install Node.js 22+ yourself (https://nodejs.org/, or brew install node),
then rerun:
  bash scripts/start_workbench.sh
MESSAGE
    exit 127
fi

NODE_MAJOR="$("$NODE_BIN" -p 'Number(process.versions.node.split(".")[0])')"
if [[ "$NODE_MAJOR" -lt 22 ]]; then
    echo "ERROR: Node.js 22 or newer is required; found $("$NODE_BIN" --version)." >&2
    exit 2
fi

cd "$ROOT"
# Supervised launch: exit code 75 is a user-requested in-app restart (Storage
# page "Restart workbench now"), used to activate pending storage-location
# changes without rerunning this script. Any other exit ends the supervisor.
(
    child=""
    trap '[[ -n "$child" ]] && kill "$child" 2>/dev/null' TERM INT
    rapid_failures=0
    while :; do
        launched_at=$SECONDS
        "$PYTHON_BIN" -m local_service.workbench_service --port "$SERVICE_PORT" &
        child=$!
        # Under set -e a nonzero `wait` would abort this subshell before the
        # status is ever inspected — killing both crash-restart and the
        # exit-75 in-app restart. Capture the status in the same command.
        rc=0
        wait "$child" || rc=$?
        if [[ "$rc" -eq 75 ]]; then
            echo "[workbench] service restart requested from the app; starting again"
            rapid_failures=0
            continue
        fi
        # 0 = clean stop; 130/143 = Ctrl-C / TERM (the trap above, or the user).
        if [[ "$rc" -eq 0 || "$rc" -eq 130 || "$rc" -eq 143 ]]; then
            exit "$rc"
        fi
        # 4 = another service already owns the state directory (or the port):
        # a deliberate refusal, never a crash to retry.
        if [[ "$rc" -eq 4 ]]; then
            echo "[workbench] another GUIDE-IEI service already owns this state; not retrying"
            exit "$rc"
        fi
        # Unexpected death (crash, out-of-memory kill): relaunch so the
        # workbench never sits headless, but give up on a rapid crash loop.
        if [[ $((SECONDS - launched_at)) -ge 60 ]]; then
            rapid_failures=0
        fi
        rapid_failures=$((rapid_failures + 1))
        if [[ "$rapid_failures" -ge 3 ]]; then
            echo "[workbench] service died $rapid_failures times in quick succession (last exit $rc); giving up — check the messages above"
            exit "$rc"
        fi
        echo "[workbench] service exited unexpectedly (code $rc); restarting in 3s"
        sleep 3
    done
) &
SERVICE_PID=$!

stop_service() {
    kill "$SERVICE_PID" 2>/dev/null || true
    wait "$SERVICE_PID" 2>/dev/null || true
}
trap stop_service EXIT INT TERM

SERVICE_READY=0
SERVICE_START_DEADLINE=$((SECONDS + SERVICE_START_TIMEOUT))
while (( SECONDS < SERVICE_START_DEADLINE )); do
    if ! kill -0 "$SERVICE_PID" 2>/dev/null; then
        wait "$SERVICE_PID" || true
        echo "ERROR: The annotation service could not start on 127.0.0.1:${SERVICE_PORT}." >&2
        echo "Set a different port with IEI_SERVICE_PORT if that port is already in use." >&2
        exit 1
    fi
    if "$PYTHON_BIN" -c 'import json,sys,urllib.request; data=json.load(urllib.request.urlopen("http://127.0.0.1:"+sys.argv[1]+"/api/health", timeout=.5)); raise SystemExit(0 if data.get("ok") else 1)' "$SERVICE_PORT" 2>/dev/null; then
        SERVICE_READY=1
        break
    fi
    sleep 0.25
done
if [[ "$SERVICE_READY" != "1" ]]; then
    echo "ERROR: The annotation service did not become ready on 127.0.0.1:${SERVICE_PORT} within ${SERVICE_START_TIMEOUT} seconds." >&2
    echo "Increase IEI_SERVICE_START_TIMEOUT if a one-time database migration is still running." >&2
    exit 1
fi

cd "${ROOT}/webui"
export NEXT_PUBLIC_IEI_SERVICE_URL="http://127.0.0.1:${SERVICE_PORT}"
NODE_DIR="$(dirname "$NODE_BIN")"
export PATH="${NODE_DIR}:${PATH}"

if [[ ! -d "${ROOT}/webui/node_modules" ]] && ! command -v npm >/dev/null 2>&1; then
    cat >&2 <<'MESSAGE'
ERROR: The web UI dependencies are not installed and npm was not found.
The setup script fixes both without admin rights:
  bash scripts/setup_environment.sh --install
MESSAGE
    exit 127
fi
if [[ ! -d "${ROOT}/webui/node_modules" ]]; then
    echo "webui dependencies are not installed yet; running npm ci (one-time)..."
    npm ci --no-fund --no-audit || {
        echo "ERROR: npm ci failed. Run 'bash scripts/setup_environment.sh' for diagnostics." >&2
        exit 1
    }
fi
WEB_BUILD_REQUIRED=0
if [[ -f "${ROOT}/webui/.dependencies-updated" ]]; then
    # A software update changed the web UI dependency manifest; the
    # installed node_modules would otherwise run silently stale.
    echo "a software update changed the web UI dependencies; running npm ci..."
    if npm ci --no-fund --no-audit; then
        rm -f "${ROOT}/webui/.dependencies-updated"
        WEB_BUILD_REQUIRED=1
    else
        echo "ERROR: npm ci failed after the software update. Run 'bash scripts/setup_environment.sh' for diagnostics." >&2
        exit 1
    fi
fi
# A git pull changes application code without leaving the in-app
# updater's sentinel, so the built interface is also compared against
# the commit it was built from. Installs without git (the standalone
# app) have no commit to compare and rely on the sentinel alone.
SOURCE_STAMP=""
if command -v git >/dev/null 2>&1; then
    SOURCE_STAMP="$(git -C "$ROOT" rev-parse HEAD 2>/dev/null || true)"
fi
if [[ ! -f "${ROOT}/webui/.next/BUILD_ID" || -f "${ROOT}/webui/.build-required" ]]; then
    WEB_BUILD_REQUIRED=1
elif [[ -n "$SOURCE_STAMP" && "$(cat "${ROOT}/webui/.next/.iei-source-stamp" 2>/dev/null)" != "$SOURCE_STAMP" ]]; then
    echo "the built interface predates the current source (e.g. after git pull); rebuilding..."
    WEB_BUILD_REQUIRED=1
fi
# NEXT_PUBLIC values are embedded in browser JavaScript at build time.
# A runtime-only port change otherwise leaves the UI calling the old service.
if [[ "$(cat "${ROOT}/webui/.next/.iei-service-url" 2>/dev/null)" != "$NEXT_PUBLIC_IEI_SERVICE_URL" ]]; then
    WEB_BUILD_REQUIRED=1
fi
if [[ "${IEI_WEB_MODE:-production}" != "dev" && "$WEB_BUILD_REQUIRED" == "1" ]]; then
    echo "building the production workbench interface (one-time after install/update)..."
    npm run build || {
        echo "ERROR: the web UI production build failed." >&2
        exit 1
    }
    rm -f "${ROOT}/webui/.build-required"
    printf '%s\n' "$NEXT_PUBLIC_IEI_SERVICE_URL" > "${ROOT}/webui/.next/.iei-service-url"
    if [[ -n "$SOURCE_STAMP" ]]; then
        printf '%s\n' "$SOURCE_STAMP" > "${ROOT}/webui/.next/.iei-source-stamp"
    fi
fi
# Open the browser once the UI answers; the poller waits in the background
# while Next.js occupies the foreground below.
if [[ "$BOOTSTRAP" == "1" ]]; then
    (
        for _ in $(seq 1 240); do
            sleep 0.5
            if curl -s -m 2 "$UI_URL" 2>/dev/null | grep -q "GUIDE-IEI"; then
                open_browser "$UI_URL"
                exit 0
            fi
        done
    ) &
fi

if [[ "${IEI_WEB_MODE:-production}" == "dev" ]]; then
    npm run dev -- -p "$UI_PORT"
elif command -v npm >/dev/null 2>&1; then
    npm run start -- -p "$UI_PORT"
else
    echo "npm is not on PATH; starting the production interface with ${NODE_BIN}."
    "$NODE_BIN" "${ROOT}/webui/node_modules/next/dist/bin/next" start --hostname 127.0.0.1 -p "$UI_PORT"
fi
