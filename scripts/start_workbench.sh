#!/usr/bin/env bash
# Start the loopback-only annotation service and browser UI on one workstation.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${HERE}/.." && pwd)"
SERVICE_PORT="${IEI_SERVICE_PORT:-43117}"
SERVICE_START_TIMEOUT="${IEI_SERVICE_START_TIMEOUT:-120}"

if [[ ! "$SERVICE_START_TIMEOUT" =~ ^[1-9][0-9]*$ ]]; then
    echo "ERROR: IEI_SERVICE_START_TIMEOUT must be a positive number of seconds." >&2
    exit 2
fi

# npm is not always on PATH in macOS GUI/managed shells. Find a compatible
# Node runtime first; the Codex desktop runtime is a useful last-resort fallback
# when this project is being developed from Codex.
NODE_BIN="${IEI_NODE_BIN:-}"
if [[ -z "$NODE_BIN" ]] && command -v node >/dev/null 2>&1; then
    NODE_BIN="$(command -v node)"
fi
if [[ -z "$NODE_BIN" ]]; then
    for candidate in \
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

Install Node.js 22 or newer, then open a new Terminal and rerun:
  bash scripts/start_workbench.sh

macOS with Homebrew:
  brew install node

Or install the current LTS release from https://nodejs.org/
MESSAGE
    exit 127
fi

NODE_MAJOR="$("$NODE_BIN" -p 'Number(process.versions.node.split(".")[0])')"
if [[ "$NODE_MAJOR" -lt 22 ]]; then
    echo "ERROR: Node.js 22 or newer is required; found $("$NODE_BIN" --version)." >&2
    exit 2
fi

cd "$ROOT"
python3 -m local_service.workbench_service --port "$SERVICE_PORT" &
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
    if python3 -c 'import json,sys,urllib.request; data=json.load(urllib.request.urlopen("http://127.0.0.1:"+sys.argv[1]+"/api/health", timeout=.5)); raise SystemExit(0 if data.get("ok") else 1)' "$SERVICE_PORT" 2>/dev/null; then
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

if command -v npm >/dev/null 2>&1; then
    npm run dev
elif [[ -f "${ROOT}/webui/node_modules/next/dist/bin/next" ]]; then
    echo "npm is not on PATH; starting Next.js with ${NODE_BIN}."
    "$NODE_BIN" "${ROOT}/webui/node_modules/next/dist/bin/next" dev --hostname 127.0.0.1
else
    cat >&2 <<'MESSAGE'
ERROR: The web UI dependencies are not installed and npm was not found.
Install Node.js 22 or newer (which includes npm), then run:
  cd webui
  npm install
  cd ..
  bash scripts/start_workbench.sh
MESSAGE
    exit 127
fi
