#!/usr/bin/env bash
# Audit repro (H11): `setup_environment.sh --install --yes` (what the
# launchers pass) used to run `curl -fsSL https://get.docker.com | sudo sh`
# without any prompt, because confirm() returned 0 under --yes. Privileged
# and remote-script actions must require an explicit interactive yes or the
# dedicated --yes-privileged flag.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${HERE}/.." && pwd)"
SCRIPT="${ROOT}/scripts/setup_environment.sh"

# The Docker/podman runtime install is gated by confirm_privileged, not confirm.
grep -q 'confirm_privileged "Install a container runtime now? Runs: $runtime_cmd"' "$SCRIPT" \
    || { echo "FAIL: the container runtime install is not gated by confirm_privileged" >&2; exit 1; }
if grep -q 'confirm "Install a container runtime now' "$SCRIPT"; then
    echo "FAIL: the container runtime install still uses the --yes-honouring confirm()" >&2; exit 1
fi
grep -q -- '--yes-privileged) ASSUME_YES_PRIVILEGED=1' "$SCRIPT" \
    || { echo "FAIL: --yes-privileged flag missing" >&2; exit 1; }

# Extract the two consent helpers and exercise them with stdin NOT a terminal
# (exactly the launcher/CI situation).
HELPERS="$(sed -n '/^note()/p; /^confirm()/,/^}/p; /^confirm_privileged()/,/^}/p' "$SCRIPT")"

run_helper() { # run_helper <ASSUME_YES> <ASSUME_YES_PRIVILEGED> <function>
    bash -c "
        ASSUME_YES=$1; ASSUME_YES_PRIVILEGED=$2
        $HELPERS
        $3 'Install a container runtime now? Runs: curl -fsSL https://get.docker.com | sudo sh'
    " </dev/null >/dev/null 2>&1
}

# --yes alone: the unprivileged confirm says yes ...
run_helper 1 0 confirm || { echo "FAIL: confirm should honour --yes" >&2; exit 1; }
# ... but the privileged one must NOT (no terminal, no --yes-privileged).
if run_helper 1 0 confirm_privileged; then
    echo "FAIL: confirm_privileged honoured --yes without a terminal" >&2; exit 1
fi
# Without any flag and no terminal: refused.
if run_helper 0 0 confirm_privileged; then
    echo "FAIL: confirm_privileged consented with no flag and no terminal" >&2; exit 1
fi
# The explicit flag is the only unattended path.
run_helper 0 1 confirm_privileged || { echo "FAIL: --yes-privileged must allow the step" >&2; exit 1; }
# The launcher itself never passes --yes-privileged.
if grep -q -- '--yes-privileged' "${ROOT}/scripts/start_workbench.sh" "${ROOT}/desktop/windows/GUIDE-IEI.ps1" "${ROOT}/desktop/macos/GUIDE-IEI.app/Contents/Resources/launcher.sh" 2>/dev/null; then
    echo "FAIL: a launcher passes --yes-privileged" >&2; exit 1
fi

echo "PASS  privileged installs require explicit consent even under --yes"
