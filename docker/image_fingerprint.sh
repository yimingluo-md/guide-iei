#!/usr/bin/env bash
# Print a deterministic fingerprint of the GUIDE-IEI-owned container inputs.
# The value is stored as an image label by build.sh and checked by the launcher,
# service, dataset installer, and command-line preflight. An older tagged image
# can therefore never be mistaken for the image that belongs to this checkout.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INPUTS=(
  ".dockerignore"
  "Dockerfile"
  "build.sh"
  "PromoterAI.pm"
  "LoGoFunc.pm"
  "IndexedScores.pm"
  "prune_kent.py"
  "UPSTREAM-MODIFICATIONS.txt"
)

sha256_file() {
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | awk '{print $1}'
  elif command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  else
    echo "ERROR: neither shasum nor sha256sum is available" >&2
    exit 2
  fi
}

sha256_stream() {
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 | awk '{print $1}'
  elif command -v sha256sum >/dev/null 2>&1; then
    sha256sum | awk '{print $1}'
  else
    echo "ERROR: neither shasum nor sha256sum is available" >&2
    exit 2
  fi
}

{
  printf 'GUIDE-IEI container inputs v1\n'
  for input in "${INPUTS[@]}"; do
    [[ -f "${HERE}/${input}" ]] || {
      echo "ERROR: container input is missing: ${HERE}/${input}" >&2
      exit 2
    }
    printf '%s  %s\n' "$(sha256_file "${HERE}/${input}")" "$input"
  done
} | sha256_stream
