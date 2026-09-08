#!/usr/bin/env bash
# Shared helpers for the reference-download and run scripts.
# Sourced, not executed. Requires bash 4+.

# --- logging ------------------------------------------------------------------
log()  { printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*" >&2; }
warn() { printf '[%s] WARN: %s\n' "$(date +%H:%M:%S)" "$*" >&2; }
die()  { printf '[%s] ERROR: %s\n' "$(date +%H:%M:%S)" "$*" >&2; exit 1; }

# Native children inherit this limit. Docker/Podman also need an explicit
# daemon-side limit below. A crash still returns its original failure code.
ulimit -c 0 || die "cannot disable core dumps for this process"

# --- repo root ----------------------------------------------------------------
repo_root() { cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd; }

# --- BGZF integrity ------------------------------------------------------------
# A BGZF file ends with a fixed 28-byte empty block (the EOF marker). A stream
# cut exactly at a block boundary is otherwise a perfectly valid gzip: `gzip -t`
# passes, tabix indexes it, and htslib readers stop early with only a warning
# on stderr — `bcftools view -R` then silently drops every region after the cut
# and exits 0 (audit M12). Presence tests for published BGZF files must use
# this, not -s or gzip -t.
BGZF_EOF_MARKER_HEX="1f8b08040000000000ff0600424302001b0003000000000000000000"
bgzf_complete() { # bgzf_complete <file.gz>  -> 0 when the EOF marker is present
    [[ -s "$1" ]] || return 1
    local tail_hex
    tail_hex="$(tail -c 28 "$1" 2>/dev/null | od -An -tx1 | tr -d ' \n')"
    [[ "$tail_hex" == "$BGZF_EOF_MARKER_HEX" ]]
}
# Compress <plain> into <final.gz> atomically: move the plain file to a sibling
# temp name, bgzip it there, require the EOF marker, index, then rename so a
# partial file never carries the final name. The plain input is consumed
# (as `bgzip` would consume it); on failure nothing is left under either name.
#   publish_bgzf <plain> <final.gz> [tabix preset: vcf|bed|gff|sam]
publish_bgzf() {
    local plain="$1" final="$2" preset="${3:-}"
    local dir base tmp
    [[ -s "$plain" ]] || { warn "nothing to publish as $(basename "$final"): $plain is missing or empty"; return 1; }
    dir="$(dirname "$final")"; base="$(basename "$final")"
    tmp="${dir}/.${base}.publish.$$"
    rm -f "$tmp" "$tmp.gz" "$tmp.gz.tbi"
    mv -f "$plain" "$tmp" || return 1
    ( cd "$dir" && hts bgzip -f "$(basename "$tmp")" ) || { rm -f "$tmp" "$tmp.gz"; return 1; }
    if ! bgzf_complete "$tmp.gz"; then
        rm -f "$tmp" "$tmp.gz"
        warn "bgzip produced an incomplete BGZF stream for $base; not publishing it"
        return 1
    fi
    if [[ -n "$preset" ]]; then
        ( cd "$dir" && hts tabix -f -p "$preset" "$(basename "$tmp.gz")" ) \
            || { rm -f "$tmp.gz" "$tmp.gz.tbi"; return 1; }
        mv -f "$tmp.gz.tbi" "$final.tbi" || { rm -f "$tmp.gz" "$tmp.gz.tbi"; return 1; }
    fi
    mv -f "$tmp.gz" "$final" || { rm -f "$tmp.gz" "$final.tbi"; return 1; }
    return 0
}

# --- SHA-256 ------------------------------------------------------------------
# sha256_file <file>  -> hex digest on stdout. macOS ships shasum (Perl) but
# not sha256sum; minimal Linux images ship sha256sum but not Perl. A missing
# tool used to be swallowed by `shasum ... 2>/dev/null | cut`, which turned
# every content stamp into "unknown" and silently disabled rebuild detection
# (audit M11): this helper fails loudly instead, after trying python3.
sha256_file() {
    local file="$1"
    [[ -r "$file" ]] || { warn "sha256_file: cannot read $file"; return 1; }
    if command -v shasum >/dev/null 2>&1; then
        shasum -a 256 "$file" | awk '{print $1}'
    elif command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$file" | awk '{print $1}'
    elif command -v python3 >/dev/null 2>&1; then
        python3 - "$file" <<'PY'
import hashlib, sys
digest = hashlib.sha256()
with open(sys.argv[1], "rb") as handle:
    for chunk in iter(lambda: handle.read(1 << 20), b""):
        digest.update(chunk)
print(digest.hexdigest())
PY
    else
        die "no SHA-256 tool available (need shasum, sha256sum, or python3)"
    fi
}
# write_sha256_sidecar <file>  -> writes "<digest>  <basename>" to <file>.sha256.local
write_sha256_sidecar() {
    local file="$1" digest
    digest="$(sha256_file "$file")" || return 1
    printf '%s  %s\n' "$digest" "$(basename "$file")" > "${file}.sha256.local"
}

# Catalog stamps require a real cache version, including on the ClinVar path.
vep_cache_tag() {
    python3 - "$1" <<'PY'
from pathlib import Path
import sys

cache = Path(sys.argv[1])
try:
    versions = sorted(p.name for p in (cache / "homo_sapiens").iterdir() if p.is_dir())
except OSError:
    versions = []
if not versions:
    print(f"VEP cache has no homo_sapiens/<release> directory under {cache} "
          "(missing or unreadable); install the VEP cache before building a protein-match catalog",
          file=sys.stderr)
    raise SystemExit(1)
print(versions[-1])
PY
}

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
        # -#: compact progress bar whose lines carry an explicit percentage —
        # the workbench service turns these into readable job progress.
        curl -fL -# --retry 3 --retry-delay 5 -C - -o "$dest.part" "$url" || return 1
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
    case "$(head -c 15 "$dest.part" | LC_ALL=C tr '[:upper:]' '[:lower:]')" in
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
        # A trailing slash is meaningful to `bcftools sort -T DIR/` (temp
        # files inside DIR rather than DIR as a name prefix); keep it.
        [[ "$arg" == */ && -z "$base" ]] && mapped="${mapped}/"
        args[$i]="$mapped"
    done

    local rc=0
    case "$rt" in
        docker|podman)
            # IMAGE is a locally built GUIDE-IEI image, not a registry name.
            # Without this guard, `docker run` implicitly tries to pull a
            # missing image and can wait for network timeouts before failing.
            "$rt" image inspect "$img" >/dev/null 2>&1 \
                || die "container image $img is not available locally; run bash docker/build.sh before preparing indexed resources"
            mount_flags=(-v "$PWD:/w")
            for i in "${!hosts[@]}"; do mount_flags+=(-v "${hosts[$i]}:${conts[$i]}:rw"); done
            "$rt" run --pull=never --network=none --rm --ulimit core=0:0 "${mount_flags[@]}" -w /w --entrypoint "$tool" "$img" "${args[@]}" || rc=$?
            # A file written by the host or another container can be
            # incompletely visible to a container started moments later
            # (Docker Desktop VirtioFS bind caching; worse on FSKit-exFAT
            # drives), which fails tabix/bcftools/bgzip on perfectly valid
            # files. Settle and retry once; genuine failures (unsorted input,
            # non-BGZF, malformed records) fail identically on retry.
            if [[ "$rc" -gt 0 && "$rc" -lt 125 ]]; then
                log "WARN  containerized $tool failed (rc=$rc); retrying once after write settling"
                sleep 5
                rc=0
                "$rt" run --pull=never --network=none --rm --ulimit core=0:0 "${mount_flags[@]}" -w /w --entrypoint "$tool" "$img" "${args[@]}" || rc=$?
            fi
            if [[ "$rc" -ge 128 ]]; then
                log "ERROR: containerized $tool terminated by a signal (exit $rc); not retrying"
            fi
            return "$rc"
            ;;
        singularity|apptainer)
            mount_flags=(--bind "$PWD:/w")
            for i in "${!hosts[@]}"; do mount_flags+=(--bind "${hosts[$i]}:${conts[$i]}"); done
            "$rt" exec "${mount_flags[@]}" --pwd /w "$img" "$tool" "${args[@]}"
            ;;
        *) die "unsupported HTS container runtime: $rt" ;;
    esac
}
