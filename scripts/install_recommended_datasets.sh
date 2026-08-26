#!/usr/bin/env bash
# Install every automatically obtainable dataset in the recommended local
# annotation profile. Registration- and license-gated resources are excluded
# and remain visible as guided manual setup steps in the workbench.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${HERE}/.." && pwd)"
source "${HERE}/lib.sh"
CONFIG="${1:-${ROOT}/config/annotation.config.yaml}"
PROFILE="${2:-exome}"

case "${PROFILE}" in
  exome|whole_genome) ;;
  *) echo "ERROR: profile must be exome or whole_genome" >&2; exit 2 ;;
esac

echo "Installing recommended ${PROFILE} annotation datasets"
echo "Registration/license resources (dbNSFP and PromoterAI) are handled separately in the UI."
echo "LoGoFunc is optional and is not installed by this action."

abs_path() {
  local path="$1"
  [[ "$path" = /* ]] || path="${ROOT}/${path}"
  printf '%s\n' "$path"
}

abs_path_optional() {
  local path="$1"
  if [[ -z "$path" ]]; then
    printf '\n'
    return 0
  fi
  abs_path "$path"
}

indexed_ready() {
  local path="$1"
  [[ -s "$path" ]] && { [[ -s "${path}.tbi" ]] || [[ -s "${path}.csi" ]]; }
}

files_ready() {
  local path
  for path in "$@"; do
    [[ -s "$path" ]] || return 1
  done
}

# Build the smallest possible request. The individual downloader is resumable,
# but some large resources perform checksum verification when invoked; do not
# invoke them at all once the complete configured installation is present.
VEP_TAG="$(yaml_get "$CONFIG" container.vep_image_tag)"
VEP_RELEASE="$(printf '%s' "$VEP_TAG" | sed -E 's/[^0-9]*([0-9]+).*/\1/')"; VEP_RELEASE="${VEP_RELEASE:-113}"
ASSEMBLY="$(yaml_get "$CONFIG" reference.assembly)"; ASSEMBLY="${ASSEMBLY:-GRCh38}"
VEP_CACHE="$(abs_path "$(yaml_get "$CONFIG" reference.vep_cache_dir)")"
FASTA="$(abs_path "$(yaml_get "$CONFIG" reference.fasta.path)")"
ANCESTOR="$(abs_path "$(yaml_get "$CONFIG" plugins.LoF.human_ancestor_fa)")"
LOFTEE_SQL="$(abs_path "$(yaml_get "$CONFIG" plugins.LoF.conservation_file)")"
GERP="$(abs_path "$(yaml_get "$CONFIG" plugins.LoF.gerp_bigwig)")"
SPLICEAI="$(abs_path "$(yaml_get "$CONFIG" plugins.SpliceAI.snv)")"
REPEATMASKER="$(abs_path "$(yaml_get "$CONFIG" custom_tracks.RepeatMasker.file)")"
SEGDUP="$(abs_path "$(yaml_get "$CONFIG" custom_tracks.SegDup.file)")"
PTC_GTF="$(abs_path_optional "$(yaml_get "$CONFIG" post_processing.loftee_ptc_50bp.gtf)")"
CCRE="$(abs_path_optional "$(yaml_get "$CONFIG" wgs_review.ccre.bed)")"
GENE_TSS="$(abs_path_optional "$(yaml_get "$CONFIG" wgs_review.gene_tss.path)")"
LIFTOVER_FASTA="$(abs_path_optional "$(yaml_get "$CONFIG" liftover.grch37_to_grch38.source_fasta)")"
LIFTOVER_CHAIN="$(abs_path_optional "$(yaml_get "$CONFIG" liftover.grch37_to_grch38.chain)")"

missing_references=()
if [[ ! -f "${VEP_CACHE}/.homo_sapiens_vep_${VEP_RELEASE}_${ASSEMBLY}.complete" ]] \
  || [[ ! -d "${VEP_CACHE}/homo_sapiens/${VEP_RELEASE}_${ASSEMBLY}" ]]; then
  missing_references+=(vep_cache)
fi
[[ -s "$FASTA" ]] || missing_references+=(fasta)
files_ready "$ANCESTOR" "$LOFTEE_SQL" "$GERP" || missing_references+=(loftee)
indexed_ready "$SPLICEAI" || missing_references+=(spliceai)
indexed_ready "$REPEATMASKER" || missing_references+=(repeatmasker)
indexed_ready "$SEGDUP" || missing_references+=(segdup)
ccre_missing=0
if [[ -n "$PTC_GTF" ]] && [[ ! -s "$PTC_GTF" ]]; then
  ccre_missing=1
fi
if [[ -n "$GENE_TSS" ]] && [[ ! -s "$GENE_TSS" ]]; then
  ccre_missing=1
fi
if [[ -n "$CCRE" ]] && ! indexed_ready "$CCRE"; then
  ccre_missing=1
fi
((ccre_missing)) && missing_references+=(ccre)
if [[ -n "$LIFTOVER_FASTA" && -n "$LIFTOVER_CHAIN" ]]; then
  files_ready "$LIFTOVER_FASTA" "${LIFTOVER_FASTA}.fai" \
    "${LIFTOVER_FASTA}.gzi" "$LIFTOVER_CHAIN" || missing_references+=(liftover)
fi

CLINVAR="$(abs_path "$(yaml_get "$CONFIG" custom_tracks.ClinVar.file)")"
clinvar_missing=0
indexed_ready "$CLINVAR" || clinvar_missing=1

# Preparing reference files needs bgzip, tabix, and samtools. On clean machines
# these are normally supplied by the GUIDE-IEI annotation image. Validate that backend
# before transferring tens of gigabytes; if the configured local image is
# absent, build it now rather than allowing Docker to attempt an implicit pull
# only after all reference downloads have finished.
ensure_dataset_hts_backend() {
  if [[ "${HTS_VIA_CONTAINER:-0}" != "1" ]] \
    && command -v bgzip >/dev/null 2>&1 \
    && command -v tabix >/dev/null 2>&1 \
    && command -v samtools >/dev/null 2>&1; then
    echo "Dataset indexing backend ready (native bgzip, tabix, and samtools)."
    return 0
  fi

  local runtime image image_name image_tag image_tail managed_tools_bin docker_app_bin
  runtime="$(yaml_get "$CONFIG" container.runtime)"; runtime="${runtime:-docker}"
  image="$(yaml_get "$CONFIG" container.image)"; image="${image:-vep-annotate:latest}"
  managed_tools_bin="${IEI_TOOLS_DIR:-$HOME/.iei-variant-review/tools}/bin"
  docker_app_bin="/Applications/Docker.app/Contents/Resources/bin"
  if ! command -v "$runtime" >/dev/null 2>&1; then
    if [[ -x "${managed_tools_bin}/${runtime}" ]]; then
      export PATH="${managed_tools_bin}:${PATH}"
    elif [[ "$runtime" == "docker" && -x "${docker_app_bin}/docker" ]]; then
      export PATH="${docker_app_bin}:${PATH}"
    fi
  fi
  command -v "$runtime" >/dev/null 2>&1 \
    || die "dataset setup needs native bgzip/tabix/samtools or the configured '$runtime' runtime"

  case "$runtime" in
    docker|podman)
      "$runtime" info >/dev/null 2>&1 \
        || die "$runtime is installed but is not running; start it and retry dataset setup"
      if "$runtime" image inspect "$image" >/dev/null 2>&1; then
        echo "Dataset indexing backend ready (${runtime} image ${image})."
        return 0
      fi

      [[ "$image" != *@* ]] \
        || die "configured image $image is digest-pinned but is not available locally"
      image_name="$image"
      image_tag="latest"
      image_tail="${image##*/}"
      if [[ "$image_tail" == *:* ]]; then
        image_name="${image%:*}"
        image_tag="${image##*:}"
      fi

      echo "=== preparing the annotation tools (one-time setup) ==="
      echo "Building ${image} before large reference downloads begin."
      RUNTIME="$runtime" IMAGE_NAME="$image_name" IMAGE_TAG="$image_tag" \
        bash "${ROOT}/docker/build.sh" "$CONFIG"
      "$runtime" image inspect "$image" >/dev/null 2>&1 \
        || die "annotation tool build completed but image $image is unavailable"
      echo "Dataset indexing backend ready (${runtime} image ${image})."
      ;;
    singularity|apptainer)
      [[ -s "$image" ]] \
        || die "configured $runtime image is unavailable: $image"
      echo "Dataset indexing backend ready (${runtime} image ${image})."
      ;;
    *) die "unsupported dataset container runtime: $runtime" ;;
  esac
}

if ((${#missing_references[@]} || clinvar_missing)); then
  ensure_dataset_hts_backend
fi

if ((${#missing_references[@]})); then
  # Two lanes overlap independent network/disk work while keeping concurrency
  # deliberately bounded. SpliceAI's source, payload, and own connection count
  # are unchanged; it can simply proceed while the mirror-backed lane runs.
  mirror_lane=()
  canonical_lane=()
  for reference in "${missing_references[@]}"; do
    case "$reference" in
      vep_cache|fasta|loftee) mirror_lane+=("$reference") ;;
      spliceai|repeatmasker|segdup|ccre|liftover) canonical_lane+=("$reference") ;;
    esac
  done

  # macOS still ships Bash 3.2, where expanding an explicitly empty array
  # under `set -u` raises "unbound variable". Build each CSV only when that
  # lane has members so a resumed installation with just one lane remaining
  # is a normal, supported path.
  mirror_only=""
  canonical_only=""
  if ((${#mirror_lane[@]})); then
    mirror_only="$(IFS=,; printf '%s' "${mirror_lane[*]}")"
  fi
  if ((${#canonical_lane[@]})); then
    canonical_only="$(IFS=,; printf '%s' "${canonical_lane[*]}")"
  fi
  if ((${#mirror_lane[@]} && ${#canonical_lane[@]})); then
    echo "=== downloading two independent reference groups in parallel ==="
    echo "Mirror group: ${mirror_only}"
    echo "Canonical group: ${canonical_only}"
    bash "${HERE}/download_references.sh" "$CONFIG" \
      --only "$mirror_only" --skip-final-status &
    mirror_pid=$!
    bash "${HERE}/download_references.sh" "$CONFIG" \
      --only "$canonical_only" --skip-final-status &
    canonical_pid=$!
    reference_failure=0
    wait "$mirror_pid" || reference_failure=1
    wait "$canonical_pid" || reference_failure=1
    [[ "$reference_failure" == "0" ]] \
      || die "one or more reference download groups failed; completed downloads remain resumable"
    python3 "${ROOT}/pipeline/check_dbnsfp_version.py" --config "$CONFIG" \
      || warn "dbNSFP update check could not be completed"
  else
    only="${mirror_only}${canonical_only}"
    echo "Installing missing pinned resources: ${only}"
    bash "${HERE}/download_references.sh" "$CONFIG" --only "$only"
  fi
else
  echo "Pinned exome resources already installed; skipping downloads and checksum scans."
fi

if [[ "$clinvar_missing" == "0" ]]; then
  echo "ClinVar already installed; use 'Update installed datasets' to refresh it."
else
  bash "${HERE}/fetch_clinvar.sh" "${CONFIG}"
fi

CLINGEN_DB="$(abs_path "$(yaml_get "$CONFIG" clingen_erepo.database)")"
CLINGEN_VCF="$(abs_path "$(yaml_get "$CONFIG" clingen_erepo.vcf)")"
CLINGEN_MANIFEST="$(abs_path "$(yaml_get "$CONFIG" clingen_erepo.manifest)")"
if files_ready "$CLINGEN_DB" "$CLINGEN_VCF" "$CLINGEN_MANIFEST"; then
  echo "ClinGen already installed; use 'Update installed datasets' to refresh it."
else
  bash "${HERE}/update_clingen_erepo.sh" "${CONFIG}"
fi

if [[ "${PROFILE}" == "whole_genome" ]]; then
  # SCREEN tissue/immune contexts power the whole-genome Regulatory evidence
  # tab: ~1.5 GB verified prepared bundle from the public mirror. (CADD WGS is
  # an optional research annotation installed from its own dataset card.)
  REGION_BED="$(abs_path "$(yaml_get "$CONFIG" region.bed)")"
  SCREEN_ROOT="$(dirname "$(dirname "$REGION_BED")")/screen-context"
  SCREEN_MANIFEST="${SCREEN_ROOT}/prepared/screen.registry-v4.immune-contexts.json"
  if [[ -s "$SCREEN_MANIFEST" ]]; then
    echo "SCREEN tissue/immune context bundle already prepared; skipping."
  else
    bash "${HERE}/download_screen_context_bundle.sh" "${SCREEN_ROOT}"
  fi
fi

echo "Recommended ${PROFILE} automatic datasets are ready."
