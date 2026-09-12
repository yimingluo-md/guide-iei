#!/usr/bin/env bash
# =============================================================================
# verify_container_stack.sh — the Docker-gated half of the release checks.
#
# The host-side suites (Python/shell/webui tests, tsc, stub-module perl -c)
# run without a container; this script covers everything that genuinely needs
# the built vep-annotate image and the installed reference stack:
#
#   1. plugin-syntax : perl -c on the image's /plugins/*.pm inside the container,
#                      against the real Bio::EnsEMBL modules (the host can only
#                      compile them against stubs)
#   2. plugin-list   : record the actual `bcftools plugin -l` output format and
#                      assert preflight's liftover detection matches it
#   3. image-drift   : the .pm files baked into the image must be byte-identical
#                      to the repo's — a stale image silently runs old plugin
#                      code no matter what the repo says
#   4. preflight     : the pipeline's own full container + reference validation
#   5. regression    : the eight-public-variant annotation panel end to end,
#                      then summarize the JSON report
#
# Usage:
#   scripts/verify_container_stack.sh [config.yaml] [--quick]
#
#   --quick   stages 1-3 only (no preflight, no VEP run) — seconds, not minutes
#
# Exit 0 only when every executed stage passes.
# =============================================================================
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${HERE}/lib.sh"
ROOT="$(cd "${HERE}/.." && pwd)"

CONFIG="${1:-${ROOT}/config/annotation.config.yaml}"
[[ "$CONFIG" == "--quick" ]] && { QUICK=1; CONFIG="${ROOT}/config/annotation.config.yaml"; }
QUICK="${QUICK:-0}"
[[ "${2:-}" == "--quick" ]] && QUICK=1
[[ -f "$CONFIG" ]] || die "config not found: $CONFIG"

RUNTIME="$(yaml_get "$CONFIG" container.runtime)"; RUNTIME="${RUNTIME:-docker}"
IMAGE="$(yaml_get "$CONFIG" container.image)";     IMAGE="${IMAGE:-vep-annotate:latest}"
export RUNTIME IMAGE
command -v "$RUNTIME" >/dev/null 2>&1 || die "'$RUNTIME' is not on PATH"
"$RUNTIME" image inspect "$IMAGE" >/dev/null 2>&1 \
    || die "image '$IMAGE' is not built — run docker/build.sh first"

FAILURES=0
fail() { log "FAIL  $*"; FAILURES=$((FAILURES + 1)); }
pass() { log "ok    $*"; }

# ---------------------------------------------------------------------------
log "=== [1/5] plugin syntax against the real Bio::EnsEMBL modules ==="
for plugin in PromoterAI LoGoFunc IndexedScores; do
    if "$RUNTIME" run --rm --pull=never --network=none \
        --entrypoint perl "$IMAGE" -I /plugins -c "/plugins/${plugin}.pm" >/dev/null 2>&1; then
        pass "perl -c ${plugin}.pm (in-container)"
    else
        "$RUNTIME" run --rm --pull=never --network=none \
            --entrypoint perl "$IMAGE" -I /plugins -c "/plugins/${plugin}.pm" 2>&1 | sed 's/^/      /' || true
        fail "perl -c ${plugin}.pm"
    fi
done

# ---------------------------------------------------------------------------
log "=== [2/5] bcftools plugin -l output format vs preflight detection ==="
PLUGIN_LIST="$("$RUNTIME" run --rm --pull=never --network=none --entrypoint bcftools "$IMAGE" plugin -l 2>&1 || true)"
log "plugin -l lines mentioning liftover:"
grep -i liftover <<<"$PLUGIN_LIST" | sed 's/^/      /' || log "      (none)"
if grep -qw liftover <<<"$PLUGIN_LIST"; then
    pass "preflight's 'grep -qw liftover' matches the real output"
else
    fail "preflight's liftover detection does NOT match 'bcftools plugin -l' output"
fi

# ---------------------------------------------------------------------------
log "=== [3/5] baked plugin files match the repo (image drift) ==="
for plugin in PromoterAI LoGoFunc IndexedScores; do
    baked="$("$RUNTIME" run --rm --pull=never --network=none --entrypoint sh "$IMAGE" \
        -c "sha256sum /plugins/${plugin}.pm" 2>/dev/null | awk '{print $1}')" || baked=""
    local_sum="$(sha256_file "${ROOT}/docker/${plugin}.pm")" || local_sum=""
    if [[ -n "$baked" && "$baked" == "$local_sum" ]]; then
        pass "image /plugins/${plugin}.pm is current"
    else
        fail "image /plugins/${plugin}.pm differs from the repo — rebuild with docker/build.sh"
    fi
done

if [[ "$QUICK" == "1" ]]; then
    log "--quick: skipping preflight and the regression panel"
    [[ "$FAILURES" -eq 0 ]] && { log "QUICK VERIFICATION PASSED"; exit 0; }
    die "$FAILURES quick-stage check(s) failed"
fi

# ---------------------------------------------------------------------------
log "=== [4/5] pipeline preflight (container + reference stack) ==="
PREFLIGHT_INPUT="${ROOT}/test/regression/annotation_regression.GRCh38.vcf"
PREFLIGHT_OUTPUT="${ROOT}/test/out/regression/preflight-probe.vep.vcf.gz"
mkdir -p "$(dirname "$PREFLIGHT_OUTPUT")"
if bash "${HERE}/preflight.sh" "$CONFIG" "$PREFLIGHT_INPUT" "$PREFLIGHT_OUTPUT" \
    --input-assembly GRCh38; then
    pass "preflight"
else
    fail "preflight"
fi

# ---------------------------------------------------------------------------
log "=== [5/5] eight-variant annotation regression panel ==="
REPORT="${ROOT}/test/out/regression/annotation_regression.report.json"
if bash "${HERE}/run_annotation_regression.sh" "$CONFIG"; then
    python3 - "$REPORT" <<'PYEOF'
import json, sys
report = json.load(open(sys.argv[1]))
print(f"      regression status : {report['status']}")
print(f"      check counts      : {report['counts']}")
for item in report.get("checks", []):
    if item.get("status") == "FAIL":
        print(f"      FAILED CHECK      : {item.get('id')}: {item.get('message')}")
PYEOF
    status="$(python3 -c "import json,sys;print(json.load(open('$REPORT'))['status'])")"
    if [[ "$status" == "PASS" ]]; then
        pass "regression panel ($REPORT)"
    else
        fail "regression panel reported $status ($REPORT)"
    fi
else
    fail "regression run did not complete"
fi

# ---------------------------------------------------------------------------
if [[ "$FAILURES" -eq 0 ]]; then
    log "CONTAINER-STACK VERIFICATION PASSED (all executed stages green)"
else
    die "$FAILURES verification stage(s) failed"
fi
