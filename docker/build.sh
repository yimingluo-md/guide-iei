#!/usr/bin/env bash
# Build the VEP+LOFTEE image. Reads VEP tag / LOFTEE branch from the pipeline
# config if present, else uses defaults. Works with docker or podman.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${HERE}/.." && pwd)"
CONFIG="${1:-${REPO_ROOT}/config/annotation.config.yaml}"

# --- defaults (overridable by env) -------------------------------------------
VEP_TAG="${VEP_TAG:-release_113.4}"
LOFTEE_BRANCH="${LOFTEE_BRANCH:-grch38}"
PICARD_VERSION="${PICARD_VERSION:-3.3.0}"
IMAGE_NAME="${IMAGE_NAME:-vep-annotate}"
IMAGE_TAG="${IMAGE_TAG:-latest}"

# --- pull container.* values out of the YAML config if available -------------
# Tiny grep-based reader (no yq dependency). Only reads the container: block.
if [[ -f "${CONFIG}" ]]; then
    v="$(awk '/^container:/{f=1;next} f&&/^[^ ]/{f=0} f&&/vep_image_tag:/{print $2;exit}' "${CONFIG}" | tr -d '"'"'"'')"
    [[ -n "${v:-}" ]] && VEP_TAG="${v}"
    v="$(awk '/^container:/{f=1;next} f&&/^[^ ]/{f=0} f&&/loftee_branch:/{print $2;exit}' "${CONFIG}" | tr -d '"'"'"'')"
    [[ -n "${v:-}" ]] && LOFTEE_BRANCH="${v}"
fi

RUNTIME="${RUNTIME:-docker}"
command -v "${RUNTIME}" >/dev/null 2>&1 || { echo "ERROR: '${RUNTIME}' not found on PATH." >&2; exit 1; }

echo "Building ${IMAGE_NAME}:${IMAGE_TAG}"
echo "  base VEP image : ensemblorg/ensembl-vep:${VEP_TAG}"
echo "  LOFTEE branch  : ${LOFTEE_BRANCH}"
echo "  Picard version : ${PICARD_VERSION}"
echo "  runtime        : ${RUNTIME}"

exec "${RUNTIME}" build \
    -t "${IMAGE_NAME}:${IMAGE_TAG}" \
    --build-arg "VEP_TAG=${VEP_TAG}" \
    --build-arg "LOFTEE_BRANCH=${LOFTEE_BRANCH}" \
    --build-arg "PICARD_VERSION=${PICARD_VERSION}" \
    -f "${HERE}/Dockerfile" \
    "${HERE}"
