#!/usr/bin/env bash
# Build a transcript-specific P/LP missense catalog for one clinical source.
#
# Usage:
#   build_clinical_protein_catalog.sh CONFIG SOURCE_TYPE SOURCE OUTPUT [MANIFEST]
# SOURCE_TYPE is clinvar, clingen, or genia. SOURCE is the ClinVar VCF or the
# prepared ClinGen/GenIA SQLite database.  The source data never enter the
# container; only a minimal synthetic-ID allele VCF is mounted for local VEP.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${HERE}/lib.sh"
ROOT="$(repo_root)"

CONFIG="${1:-${ROOT}/config/annotation.config.yaml}"
SOURCE_TYPE="${2:-}"
SOURCE_PATH="${3:-}"
OUTPUT="${4:-}"
MANIFEST="${5:-}"

[[ -f "$CONFIG" ]] || die "config not found: $CONFIG"
case "$SOURCE_TYPE" in clinvar|clingen|genia) ;; *) die "source type must be clinvar, clingen, or genia" ;; esac
[[ -s "$SOURCE_PATH" ]] || die "$SOURCE_TYPE source is missing or empty: $SOURCE_PATH"
[[ -n "$OUTPUT" ]] || die "catalog output path is required"

RUNTIME="$(yaml_get "$CONFIG" container.runtime)"; RUNTIME="${RUNTIME:-docker}"; export RUNTIME
IMAGE="$(yaml_get "$CONFIG" container.image)"; IMAGE="${IMAGE:-vep-annotate:latest}"; export IMAGE
ASSEMBLY="$(yaml_get "$CONFIG" reference.assembly)"; ASSEMBLY="${ASSEMBLY:-GRCh38}"
VEP_CACHE_DIR="$(yaml_get "$CONFIG" reference.vep_cache_dir)"
[[ "$VEP_CACHE_DIR" = /* ]] || VEP_CACHE_DIR="${ROOT}/${VEP_CACHE_DIR}"
[[ -d "$VEP_CACHE_DIR" ]] || die "VEP cache directory is missing: $VEP_CACHE_DIR"

mkdir -p "$(dirname "$OUTPUT")"
[[ -z "$MANIFEST" ]] || mkdir -p "$(dirname "$MANIFEST")"
# Use the catalog's own shared filesystem even when called outside a run.
WORK="$(mktemp -d "$(dirname "$OUTPUT")/.clinical_protein_catalog.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT
SOURCE_VCF="${WORK}/source.vcf"
METADATA="${WORK}/source-metadata.tsv"
VEP_TAB="${WORK}/source.vep.tsv"
CATALOG="${WORK}/catalog.tsv"

python3 "${ROOT}/pipeline/prepare_clinical_protein_catalog.py" export \
  --source-type "$SOURCE_TYPE" --source "$SOURCE_PATH" \
  --output-vcf "$SOURCE_VCF" --metadata "$METADATA"

VEP_INNER="vep -i /w/source.vcf -o /w/source.vep.tsv \
  --offline --cache --dir_cache /cache --species homo_sapiens --assembly ${ASSEMBLY} \
  --pick_allele_gene --pick_order mane_select,mane_plus_clinical,canonical,rank,biotype,ccds,length \
  --symbol --tab --force_overwrite --no_stats \
  --fields Uploaded_variation,SYMBOL,Protein_position,Consequence,Amino_acids,Feature"

case "$RUNTIME" in
  docker|podman)
    "$RUNTIME" run --rm --ulimit core=0:0 \
      -v "${WORK}:/w" -v "${VEP_CACHE_DIR}:/cache:ro" \
      --entrypoint sh "$IMAGE" -c "$VEP_INNER"
    ;;
  singularity|apptainer)
    "$RUNTIME" exec \
      --bind "${WORK}:/w" --bind "${VEP_CACHE_DIR}:/cache:ro" \
      "$IMAGE" sh -c "$VEP_INNER"
    ;;
  *) die "unsupported runtime: $RUNTIME" ;;
esac

REDUCE_ARGS=(
  reduce --vep-tab "$VEP_TAB" --metadata "$METADATA" --output "$CATALOG"
  --source "$SOURCE_PATH" --source-type "$SOURCE_TYPE"
)
[[ -z "$MANIFEST" ]] || REDUCE_ARGS+=(--manifest "${WORK}/manifest.json")
python3 "${ROOT}/pipeline/prepare_clinical_protein_catalog.py" "${REDUCE_ARGS[@]}"

OUTPUT_NEW="${OUTPUT}.new.$$"
mv "$CATALOG" "$OUTPUT_NEW"
mv "$OUTPUT_NEW" "$OUTPUT"
if [[ -n "$MANIFEST" ]]; then
  MANIFEST_NEW="${MANIFEST}.new.$$"
  mv "${WORK}/manifest.json" "$MANIFEST_NEW"
  mv "$MANIFEST_NEW" "$MANIFEST"
fi
log "${SOURCE_TYPE} clinical protein catalog installed: ${OUTPUT}"
