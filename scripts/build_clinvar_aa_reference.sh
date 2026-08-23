#!/usr/bin/env bash
# =============================================================================
# build_clinvar_aa_reference.sh — build the pathogenic-missense residue catalog
# used by the ClinVar amino-acid-match post-processing step.
#
# Reproduces the reference side of the vep_hg38.sh awk step: the original keyed
# on SYMBOL_Protein_position from a VEP-annotated *pathogenic* ClinVar file.
# Here we:
#   1. filter the downloaded ClinVar VCF to Pathogenic/Likely_pathogenic
#      records whose molecular consequence (MC) is missense_variant,
#   2. VEP-annotate that small subset (in the container) to get SYMBOL +
#      Protein_position,
#   3. emit a 2-column TSV: SYMBOL<TAB>Protein_position
#      -> references/clinvar/clinvar_aa_reference.tsv
#
# This is cheap (ClinVar pathogenic-missense is ~10^5 variants) and is rebuilt
# whenever ClinVar updates. Called by run_annotation.sh when the reference is
# missing or older than the current ClinVar release.
#
#   scripts/build_clinvar_aa_reference.sh [config.yaml] <clinvar.vcf.gz>
# =============================================================================
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${HERE}/lib.sh"
ROOT="$(cd "${HERE}/.." && pwd)"

CONFIG="${1:-${ROOT}/config/annotation.config.yaml}"
CLINVAR_VCF="${2:-}"
[[ -f "$CONFIG" ]] || die "config not found: $CONFIG"
[[ -n "$CLINVAR_VCF" && -f "$CLINVAR_VCF" ]] || die "need ClinVar VCF as arg 2"

RUNTIME="$(yaml_get "$CONFIG" container.runtime)"; RUNTIME="${RUNTIME:-docker}"; export RUNTIME
IMAGE="$(yaml_get "$CONFIG" container.image)";     IMAGE="${IMAGE:-vep-annotate:latest}"; export IMAGE
ASSEMBLY="$(yaml_get "$CONFIG" reference.assembly)"; ASSEMBLY="${ASSEMBLY:-GRCh38}"
DEST_DIR="$(yaml_get "$CONFIG" clinvar.dest_dir)"; DEST_DIR="${DEST_DIR:-references/clinvar}"
[[ "$DEST_DIR" = /* ]] || DEST_DIR="${ROOT}/${DEST_DIR}"

VEP_CACHE_DIR="$(yaml_get "$CONFIG" reference.vep_cache_dir)"
[[ "$VEP_CACHE_DIR" = /* ]] || VEP_CACHE_DIR="${ROOT}/${VEP_CACHE_DIR}"

WORK="$(mktemp -d "${DEST_DIR}/aaref.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT

# --- 1. filter to pathogenic missense ----------------------------------------
# Keep records where CLNSIG is Pathogenic/Likely_pathogenic AND MC contains
# missense_variant. Pure gzip|awk so it needs no container.
PATH_TERMS="$(python3 - "$CONFIG" <<'PY'
import sys, yaml
cfg = yaml.safe_load(open(sys.argv[1]))
terms = (cfg.get("post_processing", {}).get("clinvar_aa_match", {})
         .get("pathogenic_terms", ["Pathogenic", "Likely_pathogenic",
                                    "Pathogenic/Likely_pathogenic"]))
print("|".join(terms))
PY
)"
log "pathogenic terms: $PATH_TERMS"

SUBSET="${WORK}/clinvar_path_missense.vcf"
# `gzip -cd` works on both GNU/Linux and macOS; macOS zcat expects .Z files.
gzip -cd "$CLINVAR_VCF" 2>/dev/null | awk -v terms="$PATH_TERMS" '
    /^#/ { print; next }
    {
        info = $8
        # require missense in MC
        if (info !~ /MC=[^;]*missense_variant/) next
        # require a pathogenic CLNSIG term
        n = split(terms, T, "|")
        ok = 0
        for (i = 1; i <= n; i++) {
            key = "CLNSIG=" T[i]
            if (index(info, key) > 0) { ok = 1; break }
        }
        if (ok) print
    }
' > "$SUBSET"
NVAR=$(grep -vc '^#' "$SUBSET" || true)
log "pathogenic-missense ClinVar records: $NVAR"
# A zero-record subset is a hard failure: an empty reference would be
# release-stamped by run_annotation.sh, never rebuilt for the life of that
# ClinVar release, and every variant would be silently flagged 0. The
# realistic trigger is the CLNSIG substring filter above no longer matching
# ClinVar's evolving formatting.

# --- 2. VEP-annotate the subset (VCF in, tab out, just SYMBOL+Protein_position)
REF_TSV="${DEST_DIR}/clinvar_aa_reference.tsv"
if [[ "$NVAR" -gt 0 ]]; then
    # bgzip the subset for VEP
    ( cd "$WORK" && hts bgzip -f "$(basename "$SUBSET")" )
    SUBSET_GZ="${SUBSET}.gz"

    VEPOUT="${WORK}/clinvar_path_missense.vep.tsv"
    # Minimal VEP: cache offline, pick, symbol; tab output with SYMBOL + Protein_position.
    # Feature records WHICH transcript --pick chose: protein position
    # numbering is transcript-specific, so the matcher requires the patient
    # CSQ entry to come from this same transcript before claiming a match.
    VEP_INNER="vep -i /w/$(basename "$SUBSET_GZ") -o /w/$(basename "$VEPOUT") \
        --offline --cache --dir_cache /cache --species homo_sapiens --assembly ${ASSEMBLY} \
        --pick --symbol --tab --force_overwrite --no_stats \
        --fields Uploaded_variation,SYMBOL,Protein_position,Consequence,Amino_acids,Feature"

    case "$RUNTIME" in
        docker|podman)
            "$RUNTIME" run --rm \
                -v "${WORK}:/w" -v "${VEP_CACHE_DIR}:/cache:ro" \
                --entrypoint sh "$IMAGE" -c "$VEP_INNER" ;;
        singularity|apptainer)
            "$RUNTIME" exec \
                --bind "${WORK}:/w" --bind "${VEP_CACHE_DIR}:/cache:ro" \
                "$IMAGE" sh -c "$VEP_INNER" ;;
        *) die "unsupported runtime: $RUNTIME" ;;
    esac

    # --- 3. reduce VEP tab output to SYMBOL<TAB>Protein_position (missense only)
    python3 "${ROOT}/pipeline/reduce_vep_to_aa_reference.py" \
        --input "$VEPOUT" --output "$REF_TSV"
else
    die "no pathogenic-missense records found in the ClinVar subset; refusing to write an empty aa-match reference (it would be stamped as current and never rebuilt for this release)"
fi

NREF=$(wc -l < "$REF_TSV" | tr -d ' ')
log "wrote aa-match reference: $REF_TSV ($NREF residues)"
echo "AA_REFERENCE=${REF_TSV}"
echo "AA_REFERENCE_N=${NREF}"
