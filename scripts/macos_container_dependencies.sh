#!/usr/bin/env bash
# Sourced by setup_environment.sh; uses its ok/fix reporting functions.
# Keep --check read-only and preserve unrelated user executables.
start_managed_colima() {
    local colima="$1" root="$2" cpus="$3" memory="$4"
    # A stable parent survives replacing the app and avoids spaces in app names.
    case "$root" in /Applications/*) root=/Applications ;; esac
    # Explicit mounts replace the defaults. Keep home/temp and share only
    # the application code read-only (including apps outside the home folder).
    "$colima" start --cpu "$cpus" --memory "$memory" --disk 120 \
        --mount "$HOME:w" --mount /tmp/colima:w --mount "$root:r"
}

ensure_macos_lima_launchers() {
    local tools_dir="$1" version="$2" mode="$3" tool target failed=0
    for tool in limactl lima; do
        [[ -x "$tools_dir/bin/$tool" ]] && continue
        target="$tools_dir/lima-${version}/bin/$tool"
        if [[ "$mode" == "install" && -x "$target" ]] \
            && { [[ ! -e "$tools_dir/bin/$tool" ]] || [[ -L "$tools_dir/bin/$tool" ]]; }; then
            if ln -sfn "$target" "$tools_dir/bin/$tool"; then
                ok "repaired managed $tool launcher"
                continue
            fi
        fi
        fix "managed Colima dependency is missing: $tool" \
            "rerun setup_environment.sh --install; expected executable: $target (check file permissions if repair fails)"
        failed=1
    done
    return "$failed"
}
