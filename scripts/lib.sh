#!/usr/bin/env bash
# Shared helpers for the reference-download and run scripts.
# Sourced, not executed. Requires bash 4+.

# --- logging ------------------------------------------------------------------
log()  { printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*" >&2; }
warn() { printf '[%s] WARN: %s\n' "$(date +%H:%M:%S)" "$*" >&2; }
die()  { printf '[%s] ERROR: %s\n' "$(date +%H:%M:%S)" "$*" >&2; exit 1; }

# --- repo root ----------------------------------------------------------------
repo_root() { cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd; }

# --- downloader: prefer curl, fall back to wget -------------------------------
# fetch <url> <dest>   (resumable; skips if dest already present and non-empty)
fetch() {
    local url="$1" dest="$2"
    if [[ -s "$dest" ]]; then
        log "exists, skip: $(basename "$dest")"
        return 0
    fi
    mkdir -p "$(dirname "$dest")"
    log "download: $url"
    if command -v curl >/dev/null 2>&1; then
        curl -fL --retry 3 --retry-delay 5 -C - -o "$dest.part" "$url" || return 1
    elif command -v wget >/dev/null 2>&1; then
        wget --tries=3 -c -O "$dest.part" "$url" || return 1
    else
        die "need curl or wget on PATH to download references"
    fi
    # Never promote an empty file or an HTML error page to the canonical
    # reference path: fetch() skips existing non-empty destinations, so one
    # bad promote is sticky until someone deletes the file by hand.
    if [[ ! -s "$dest.part" ]]; then
        log "WARN  empty download discarded: $url"
        rm -f "$dest.part"
        return 1
    fi
    case "$(head -c 15 "$dest.part" | tr '[:upper:]' '[:lower:]')" in
        "<!doctype html"*|"<html"*)
            log "WARN  HTML error page discarded instead of saved as $(basename "$dest")"
            rm -f "$dest.part"
            return 1
            ;;
    esac
    mv "$dest.part" "$dest"
}

# --- tiny YAML reader (no yq dependency) --------------------------------------
# yaml_get <file> <dotted.key>   -- supports scalar values at nested keys.
# Handles the subset of YAML this project's config uses (2-space indent, no
# flow collections for the queried scalars). For lists/complex nodes use Python.
yaml_get() {
    local file="$1" key="$2"
    python3 - "$file" "$key" <<'PY'
import sys, yaml
f, key = sys.argv[1], sys.argv[2]
d = yaml.safe_load(open(f))
for k in key.split("."):
    if isinstance(d, dict) and k in d:
        d = d[k]
    else:
        sys.exit(0)   # missing -> empty output
if isinstance(d, bool):
    print("true" if d else "false")
elif d is not None:
    print(d)
PY
}

# --- htslib passthrough: run bgzip/tabix natively or via the container --------
# The container image ships bgzip+tabix. If they aren't on the host PATH, we
# shell into the image. Set HTS_VIA_CONTAINER=1 to force container use.
# Usage: hts bgzip -f file ;  hts tabix -p vcf file.gz
# Absolute input/output/reference paths are mapped into distinct container
# mounts, including paths outside the repository and paths containing spaces.
_hts_runtime() { echo "${RUNTIME:-docker}"; }
hts() {
    local tool="$1"; shift
    if [[ "${HTS_VIA_CONTAINER:-0}" != "1" ]] && command -v "$tool" >/dev/null 2>&1; then
        "$tool" "$@"
        return $?
    fi
    local img="${IMAGE:-vep-annotate:latest}"
    local rt; rt="$(_hts_runtime)"
    command -v "$rt" >/dev/null 2>&1 || die "no host $tool and no '$rt' to run it via container"
    log "($tool via $rt $img)"

    local args=("$@") hosts=() conts=() mount_flags=()
    local i j arg host_dir base mapped found
    # Rewrite every absolute path argument to a dedicated bind mount. Output
    # files do not need to exist yet; their existing parent directory is used.
    for i in "${!args[@]}"; do
        arg="${args[$i]}"
        [[ "$arg" == /* ]] || continue
        if [[ -d "$arg" ]]; then
            host_dir="$(cd "$arg" && pwd -P)"; base=""
        else
            host_dir="$(cd "$(dirname "$arg")" && pwd -P)"; base="$(basename "$arg")"
        fi
        found=-1
        for j in "${!hosts[@]}"; do
            [[ "${hosts[$j]}" == "$host_dir" ]] && found="$j" && break
        done
        if [[ "$found" == "-1" ]]; then
            found="${#hosts[@]}"
            hosts+=("$host_dir")
            conts+=("/hts_$((found + 1))")
        fi
        mapped="${conts[$found]}"
        [[ -n "$base" ]] && mapped="${mapped}/${base}"
        args[$i]="$mapped"
    done

    case "$rt" in
        docker|podman)
            mount_flags=(-v "$PWD:/w")
            for i in "${!hosts[@]}"; do mount_flags+=(-v "${hosts[$i]}:${conts[$i]}:rw"); done
            "$rt" run --rm "${mount_flags[@]}" -w /w --entrypoint "$tool" "$img" "${args[@]}"
            ;;
        singularity|apptainer)
            mount_flags=(--bind "$PWD:/w")
            for i in "${!hosts[@]}"; do mount_flags+=(--bind "${hosts[$i]}:${conts[$i]}"); done
            "$rt" exec "${mount_flags[@]}" --pwd /w "$img" "$tool" "${args[@]}"
            ;;
        *) die "unsupported HTS container runtime: $rt" ;;
    esac
}
