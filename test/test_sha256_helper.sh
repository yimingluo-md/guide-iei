#!/usr/bin/env bash
# Audit M11: content stamps (ClinVar/protein-catalog rebuild detection, the
# liftover cache sidecars) must come from a real digest. `shasum ... 2>/dev/null
# | cut` silently produced an empty string — and therefore the stamp
# "unknown" — on hosts without Perl's shasum. sha256_file() tries shasum,
# sha256sum, then python3, and fails loudly when none exists; the scripts
# that stamp content use it and die on failure.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${HERE}/.." && pwd)"
source "${ROOT}/scripts/lib.sh"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
fail() { echo "FAIL: $*" >&2; exit 1; }

printf 'guide-iei\n' > "$WORK/f.txt"
EXPECTED="$(python3 -c 'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())' "$WORK/f.txt")"

# Each backend on its own, selected by a PATH that exposes only that tool.
mkdir -p "$WORK/only-shasum" "$WORK/only-sha256sum" "$WORK/only-python" "$WORK/none"
for tool in shasum sha256sum python3 awk cat basename dirname; do
    real="$(command -v "$tool" 2>/dev/null || true)"
    [[ -n "$real" ]] || continue
    case "$tool" in
        shasum)    ln -s "$real" "$WORK/only-shasum/$tool" ;;
        sha256sum) ln -s "$real" "$WORK/only-sha256sum/$tool" ;;
        python3)   ln -s "$real" "$WORK/only-python/$tool" ;;
        *) for d in only-shasum only-sha256sum only-python none; do ln -s "$real" "$WORK/$d/$tool"; done ;;
    esac
done
for d in only-shasum only-sha256sum only-python; do
    tool="${d#only-}"; [[ "$tool" == "python" ]] && tool=python3
    if ! command -v "$tool" >/dev/null 2>&1; then echo "  ($d: $tool not installed here, skipped)"; continue; fi
    got="$(PATH="$WORK/$d" sha256_file "$WORK/f.txt")" || fail "$d: sha256_file failed"
    [[ "$got" == "$EXPECTED" ]] || fail "$d: digest $got != $EXPECTED"
    echo "  $d: correct digest"
done

# No tool at all: a loud failure, never an empty digest.
rc=0
got="$(PATH="$WORK/none" sha256_file "$WORK/f.txt" 2>"$WORK/none.log")" || rc=$?
[[ "$rc" != "0" ]] || fail "sha256_file succeeded with no hashing tool on PATH"
[[ -z "$got" ]] || fail "unexpected output without a hashing tool: $got"
grep -F "no SHA-256 tool available" "$WORK/none.log" >/dev/null || fail "missing-tool diagnostic absent"
echo "  no tool: fails loudly"

# Unreadable/missing input fails too.
if sha256_file "$WORK/absent" 2>/dev/null; then fail "absent file hashed"; fi

# Sidecar format is what the liftover cache reads: "<digest>  <basename>".
write_sha256_sidecar "$WORK/f.txt" || fail "write_sha256_sidecar failed"
[[ "$(cat "$WORK/f.txt.sha256.local")" == "$EXPECTED  f.txt" ]] || fail "sidecar content: $(cat "$WORK/f.txt.sha256.local")"
echo "  sidecar: digest and basename"

# The stamping sites no longer swallow a missing tool.
for pattern in 'shasum -a 256 "$CLINVAR_VCF" 2>/dev/null' 'shasum -a 256 "$source_path" 2>/dev/null' 'CLINVAR_SHA:-unknown' 'source_sha:-unknown'; do
    ! grep -F -- "$pattern" "${ROOT}/scripts/run_annotation.sh" >/dev/null || fail "run_annotation.sh still uses the silent pattern: $pattern"
done
grep -F 'CLINVAR_SHA="$(sha256_file "$CLINVAR_VCF")" || die' "${ROOT}/scripts/run_annotation.sh" >/dev/null || fail "ClinVar stamp does not use sha256_file"
grep -F 'source_sha="$(sha256_file "$source_path")" || die' "${ROOT}/scripts/run_annotation.sh" >/dev/null || fail "catalog stamp does not use sha256_file"
grep -F 'write_sha256_sidecar "$LIFTOVER_SOURCE_FASTA" || die' "${ROOT}/scripts/download_references.sh" >/dev/null || fail "hg19 sidecar is not written through the helper"
grep -F 'write_sha256_sidecar "$LIFTOVER_CHAIN" || die' "${ROOT}/scripts/download_references.sh" >/dev/null || fail "chain sidecar is not written through the helper"
! grep -E 'shasum -a 256' "${ROOT}/scripts/download_references.sh" "${ROOT}/scripts/run_annotation.sh" "${ROOT}/scripts/verify_container_stack.sh" >/dev/null \
    || fail "a direct shasum call remains in a stamping script"
# The VEP-cache tag in the same stamp must not degrade to "unknown" either:
# a cache without a versioned homo_sapiens directory is a loud failure.
! grep -F 'cache_tag="${cache_tag:-unknown}"' "${ROOT}/scripts/run_annotation.sh" >/dev/null \
    || fail "run_annotation.sh still stamps an absent VEP cache as unknown"
grep -F 'VEP_CACHE_TAG="$(vep_cache_tag "$AA_VEP_CACHE_DIR")"' "${ROOT}/scripts/run_annotation.sh" >/dev/null \
    || fail "ClinVar stamp does not validate its cache version"
grep -F 'cache_tag="$(vep_cache_tag "$cache_dir")"' "${ROOT}/scripts/run_annotation.sh" >/dev/null \
    || fail "other clinical catalogs do not validate their cache version"
for shape in missing empty files-only; do
    cache="$WORK/cache-$shape"
    [[ "$shape" == missing ]] || mkdir -p "$cache/homo_sapiens"
    [[ "$shape" != files-only ]] || touch "$cache/homo_sapiens/info.txt"
    if vep_cache_tag "$cache" > "$WORK/tag" 2> "$WORK/tag-error"; then
        fail "$shape cache was accepted for a catalog stamp"
    fi
    [[ ! -s "$WORK/tag" ]] || fail "invalid cache emitted a tag"
    grep -F 'VEP cache has no homo_sapiens/<release> directory' "$WORK/tag-error" >/dev/null \
        || fail "$shape cache did not produce a diagnostic"
done
mkdir -p "$WORK/cache-valid/homo_sapiens/113_GRCh38"
[[ "$(vep_cache_tag "$WORK/cache-valid")" == 113_GRCh38 ]] || fail "valid cache version not returned"
echo "  stamping sites use the helper"

echo "PASS  sha256_file fallback chain and loud failure"
