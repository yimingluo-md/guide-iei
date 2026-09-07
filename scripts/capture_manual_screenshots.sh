#!/usr/bin/env bash
# Capture synthetic examples against disposable UI/service/storage, never the
# operator's running workbench or Sample Library. No annotation/download jobs.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PATH="${IEI_TOOLS_DIR:-$HOME/.iei-variant-review/tools}/bin:$PATH"
SCRATCH="$(mktemp -d "${TMPDIR:-/tmp}/guide-iei-manual.XXXXXX")"
UI_PORT="${IEI_MANUAL_UI_PORT:-3017}"
SERVICE_PORT="${IEI_MANUAL_SERVICE_PORT:-43127}"
python3 - "$UI_PORT" "$SERVICE_PORT" <<'PY'
import socket
import sys
for port in map(int, sys.argv[1:]):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", port))
PY
UI_PID=""
SERVICE_PID=""
cleanup() {
    [ -z "$UI_PID" ] || kill "$UI_PID" 2>/dev/null || true
    [ -z "$SERVICE_PID" ] || kill "$SERVICE_PID" 2>/dev/null || true
    # Retain isolated logs for troubleshooting; never recursively delete a
    # variable path from this maintenance helper.
    echo "Isolated screenshot workspace and logs: $SCRATCH"
}
trap cleanup EXIT INT TERM
mkdir -p "$SCRATCH/project/webui" "$SCRATCH/state" "$SCRATCH/images"
cp -R "$ROOT/config" "$ROOT/pipeline" "$ROOT/local_service" "$ROOT/scripts" "$ROOT/docker" "$SCRATCH/project/"
cp "$ROOT/VERSION" "$SCRATCH/project/"
cp -R "$ROOT/webui/app" "$ROOT/webui/public" "$SCRATCH/project/webui/"
cp "$ROOT/webui/package.json" "$ROOT/webui/tsconfig.json" "$ROOT/webui/next.config.ts" "$ROOT/webui/next-env.d.ts" "$SCRATCH/project/webui/"
ln -s "$ROOT/webui/node_modules" "$SCRATCH/project/webui/node_modules"
python3 "$ROOT/scripts/make_demo_vcf.py" "$SCRATCH/demo_exome.vep.vcf.gz" \
    --qc-config "$SCRATCH/project/config/annotation.config.yaml"
export IEI_WORKBENCH_STATE_DIR="$SCRATCH/state"
export IEI_WORKBENCH_STORAGE_REGISTRY="$SCRATCH/storage.json"
export NEXT_PUBLIC_IEI_SERVICE_URL="http://127.0.0.1:$SERVICE_PORT"
python3 -m local_service.workbench_service \
    --pipeline-root "$SCRATCH/project" --port "$SERVICE_PORT" \
    > "$SCRATCH/service.log" 2>&1 &
SERVICE_PID=$!
node "$ROOT/webui/node_modules/next/dist/bin/next" dev "$SCRATCH/project/webui" \
    --webpack --hostname 127.0.0.1 --port "$UI_PORT" > "$SCRATCH/ui.log" 2>&1 &
UI_PID=$!
# Only poll our own disposable UI; fail rather than use an existing server.
for attempt in $(seq 1 60); do
    kill -0 "$UI_PID" 2>/dev/null || { echo "Isolated UI failed; see $SCRATCH/ui.log" >&2; exit 1; }
    kill -0 "$SERVICE_PID" 2>/dev/null || { echo "Isolated service failed; see $SCRATCH/service.log" >&2; exit 1; }
    if curl -fsS --max-time 30 "http://127.0.0.1:$UI_PORT" -o /dev/null 2>/dev/null; then break; fi
    sleep 1
done
IEI_MANUAL_ISOLATED=1 IEI_MANUAL_QC_HTML="$SCRATCH/demo_exome.vep.vcf.gz.annotation_qc.html" node "$ROOT/scripts/make_manual_screenshots.mjs" \
    "http://127.0.0.1:$UI_PORT" "$SCRATCH/demo_exome.vep.vcf.gz" "$SCRATCH/images"
echo "Review images before copying them to docs/assets/img."
