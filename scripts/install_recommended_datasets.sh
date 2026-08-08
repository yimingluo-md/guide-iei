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
VEP_RELEASE="$(printf '%s' "$VEP_TAG" | sed -E 's/[^0-9]*([0-9]+).*/\1/')"
ASSEMBLY="$(yaml_get "$CONFIG" reference.assembly)"; ASSEMBLY="${ASSEMBLY:-GRCh38}"
VEP_CACHE="$(abs_path "$(yaml_get "$CONFIG" reference.vep_cache_dir)")"
FASTA="$(abs_path "$(yaml_get "$CONFIG" reference.fasta.path)")"
ANCESTOR="$(abs_path "$(yaml_get "$CONFIG" plugins.LoF.human_ancestor_fa)")"
LOFTEE_SQL="$(abs_path "$(yaml_get "$CONFIG" plugins.LoF.conservation_file)")"
GERP="$(abs_path "$(yaml_get "$CONFIG" plugins.LoF.gerp_bigwig)")"
SPLICEAI="$(abs_path "$(yaml_get "$CONFIG" plugins.SpliceAI.snv)")"
REPEATMASKER="$(abs_path "$(yaml_get "$CONFIG" custom_tracks.RepeatMasker.file)")"
SEGDUP="$(abs_path "$(yaml_get "$CONFIG" custom_tracks.SegDup.file)")"

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

if ((${#missing_references[@]})); then
  only="$(IFS=,; printf '%s' "${missing_references[*]}")"
  echo "Installing missing pinned resources: ${only}"
  bash "${HERE}/download_references.sh" "${CONFIG}" --only "$only"
else
  echo "Pinned exome resources already installed; skipping downloads and checksum scans."
fi

CLINVAR="$(abs_path "$(yaml_get "$CONFIG" custom_tracks.ClinVar.file)")"
if indexed_ready "$CLINVAR"; then
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
  CADD_SNV="$(abs_path "$(yaml_get "$CONFIG" plugins.CADD_WGS.snv)")"
  CADD_INDELS="$(abs_path "$(yaml_get "$CONFIG" plugins.CADD_WGS.indels)")"
  if indexed_ready "$CADD_SNV" && indexed_ready "$CADD_INDELS"; then
    echo "CADD whole-genome tables already installed; skipping the approximately 83 GiB checksum scan."
  else
    bash "${HERE}/download_cadd_wgs.sh" "${CONFIG}"
  fi
fi

echo "Recommended ${PROFILE} automatic datasets are ready."
