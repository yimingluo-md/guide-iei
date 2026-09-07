#!/usr/bin/env bash
# Sourced by setup_environment.sh; uses its ok/fix reporting functions.
# Keep --check read-only and preserve unrelated user executables.
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
