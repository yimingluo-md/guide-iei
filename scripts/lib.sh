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
        wget -c -O "$dest.part" "$url" || return 1
    else
        die "need curl or wget on PATH to download references"
    fi
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
#        (paths must be under $PWD, which is bind-mounted to /w)
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
    "$rt" run --rm -v "$PWD:/w" -w /w --entrypoint "$tool" "$img" "$@"
}
