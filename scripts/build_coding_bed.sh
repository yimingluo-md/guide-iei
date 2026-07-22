#!/usr/bin/env bash
# =============================================================================
# build_coding_bed.sh — build the coding-exon + splice-site BED used to
# restrict annotation to the clinically-interpretable fraction of the genome.
#
# Source: the release-matched Ensembl GTF (same Ensembl release as your VEP
# offline cache, so contig names and coordinates line up exactly — no `chr`
# stripping needed, unlike the UCSC tracks).
#
# Output: references/regions/coding_splice.padded.bed.gz (+ .tbi)
#   * CDS features (protein-coding), each expanded by `padding_bp` on both
#     sides to capture essential/consensus splice sites, then merged.
#
# Usage:
#   scripts/build_coding_bed.sh [config.yaml] [--force]
# =============================================================================
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${HERE}/lib.sh"
ROOT="$(cd "${HERE}/.." && pwd)"

CONFIG="${1:-${ROOT}/config/annotation.config.yaml}"
[[ "$CONFIG" == --* ]] && CONFIG="${ROOT}/config/annotation.config.yaml"
FORCE=0
for a in "$@"; do [[ "$a" == "--force" ]] && FORCE=1; done
[[ -f "$CONFIG" ]] || die "config not found: $CONFIG"

absdir() { local p="$1"; [[ "$p" = /* ]] && echo "$p" || echo "${ROOT}/${p}"; }

ASSEMBLY="$(yaml_get "$CONFIG" reference.assembly)"; ASSEMBLY="${ASSEMBLY:-GRCh38}"
VEP_TAG="$(yaml_get "$CONFIG" container.vep_image_tag)"
VEP_REL="$(echo "$VEP_TAG" | sed -E 's/[^0-9]*([0-9]+).*/\1/')"; VEP_REL="${VEP_REL:-113}"
PAD="$(yaml_get "$CONFIG" region.padding_bp)"; PAD="${PAD:-8}"
BED="$(absdir "$(yaml_get "$CONFIG" region.bed)")"
BED="${BED:-${ROOT}/references/regions/coding_splice.padded.bed.gz}"
CUSTOM="$(yaml_get "$CONFIG" region.custom_bed)"

# If the user supplied a custom BED, we never build from the GTF.
if [[ -n "$CUSTOM" ]]; then
    log "region.custom_bed set ($CUSTOM) — build_coding_bed.sh has nothing to do."
    exit 0
fi

if [[ -s "$BED" && "$FORCE" != "1" ]]; then
    log "coding BED already present: $BED  (use --force to rebuild)"
    exit 0
fi

mkdir -p "$(dirname "$BED")"
GTF_URL="https://ftp.ensembl.org/pub/release-${VEP_REL}/gtf/homo_sapiens/Homo_sapiens.${ASSEMBLY}.${VEP_REL}.gtf.gz"
RAW="$(dirname "$BED")/Homo_sapiens.${ASSEMBLY}.${VEP_REL}.gtf.gz"

log "=== coding+splice BED (Ensembl release $VEP_REL, $ASSEMBLY, pad ${PAD}bp) ==="
if [[ ! -s "$RAW" ]]; then
    log "fetching $GTF_URL"
    fetch "$GTF_URL" "$RAW" || die "Ensembl GTF download failed: $GTF_URL"
fi

# Need a contig-length file so padding never runs off the chromosome ends.
# Derive it from the FASTA .fai if present; else pad and let bedtools/awk clamp
# at >=0 (right end clamped by merge against real feature extents is not
# possible without lengths, so we clamp low end to 0 and leave high end — VEP
# tolerates a region end past the contig).
FAI="$(absdir "$(yaml_get "$CONFIG" reference.fasta.path)").fai"

TMP="$(mktemp)"
# Extract CDS + stop_codon (stop_codon is a separate GTF feature but is coding),
# convert GTF [1-based, inclusive] -> BED [0-based, half-open], pad by $PAD,
# clamp low end to 0.
zcat "$RAW" \
  | awk -v pad="$PAD" 'BEGIN{FS=OFS="\t"}
      $3=="CDS" || $3=="stop_codon" {
        s=$4-1-pad; if(s<0)s=0;
        e=$5+pad;
        print $1, s, e
      }' \
  | sort -k1,1 -k2,2n > "$TMP"

NFEAT=$(wc -l < "$TMP" | tr -d ' ')
[[ "$NFEAT" -gt 0 ]] || die "no CDS features parsed from $RAW"
log "parsed $NFEAT CDS/stop_codon intervals; merging"

# Merge overlapping/adjacent intervals (pure awk, no bedtools dependency).
MERGED="$(mktemp)"
awk 'BEGIN{FS=OFS="\t"; pc=""; ps=-1; pe=-1}
  {
    if($1==pc && $2<=pe){ if($3>pe) pe=$3 }
    else { if(pc!="") print pc, ps, pe; pc=$1; ps=$2; pe=$3 }
  }
  END{ if(pc!="") print pc, ps, pe }' "$TMP" > "$MERGED"

NMERGED=$(wc -l < "$MERGED" | tr -d ' ')
BPTOTAL=$(awk '{s+=$3-$2} END{printf "%.1f", s/1e6}' "$MERGED")
log "merged -> $NMERGED intervals covering ~${BPTOTAL} Mb"

# bgzip + tabix (via container passthrough if no host bgzip/tabix).
PLAIN="${BED%.gz}"
mv "$MERGED" "$PLAIN"
rm -f "$TMP"
( cd "$(dirname "$BED")" && hts bgzip -f "$(basename "$PLAIN")" )
( cd "$(dirname "$BED")" && hts tabix -f -p bed "$(basename "$BED")" )

log "coding+splice BED ready: $BED"
log "Restriction is ON by default (region.coding_only: true). To annotate ALL"
log "variants (WGS / non-coding), set region.coding_only: false or pass --all-variants."
