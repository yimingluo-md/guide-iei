#!/usr/bin/env bash
# =============================================================================
# download_references.sh — fetch the freely-scriptable reference data.
#
# Fetches (tier: auto):
#   * VEP offline cache (indexed)         ~25 GB
#   * Reference genome FASTA (+bgzip)     ~1 GB
#   * LOFTEE GRCh38 data (Broad)          ~15 GB  (human_ancestor, loftee.sql, GERP bw)
#   * SpliceAI masked MANE SNVs (Ensembl) ~27 GB  (bgzipped VCF + tabix index)
#   * RepeatMasker (UCSC hg38, cleaned)   ~50 MB  (chr-stripped, bgzipped, tabix'd BED)
#   * SegDup / genomicSuperDups (UCSC)    ~2 MB   (chr-stripped, bgzipped, tabix'd BED)
#   * hg19 -> hg38 UCSC chain             ~1 MB   (GRCh37 VCF intake only)
#
# Does NOT fetch (you supply these):
#   * dbNSFP  (~50 GB; CADD/REVEL/AlphaMissense/SIFT/PolyPhen/... in one file)
#             NOT auto-downloadable: academic registration -> emailed access code ->
#             request links. See the printed instructions below + scripts/prepare_dbnsfp.sh.
#   * promoterAI, LoGoFunc  (Illumina license form / lab track)          -> bring-your-own-custom
#   * ClinVar               (fetched per-run by fetch_clinvar.sh)
#
# Usage:
#   scripts/download_references.sh [config.yaml] [--only vep_cache,fasta,loftee,spliceai,repeatmasker,segdup,liftover]
#
# Idempotent: existing non-empty files are skipped. Re-run to resume.
# =============================================================================
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${HERE}/lib.sh"
ROOT="$(cd "${HERE}/.." && pwd)"
cd "$ROOT"

CONFIG="${1:-${ROOT}/config/annotation.config.yaml}"
shift || true
ONLY=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --only) ONLY="$2"; shift 2 ;;
        *) shift ;;
    esac
done
[[ -f "$CONFIG" ]] || die "config not found: $CONFIG"

want() { [[ -z "$ONLY" ]] || [[ ",$ONLY," == *",$1,"* ]]; }

# --- versions / assembly ------------------------------------------------------
ASSEMBLY="$(yaml_get "$CONFIG" reference.assembly)"; ASSEMBLY="${ASSEMBLY:-GRCh38}"
# VEP cache release must match the base image's VEP major version.
VEP_TAG="$(yaml_get "$CONFIG" container.vep_image_tag)"       # e.g. release_113.4
VEP_REL="$(echo "$VEP_TAG" | sed -E 's/[^0-9]*([0-9]+).*/\1/')"; VEP_REL="${VEP_REL:-113}"
log "assembly=$ASSEMBLY  VEP cache release=$VEP_REL"

ENSEMBL_FTP="https://ftp.ensembl.org/pub/release-${VEP_REL}"
LOFTEE_BASE="https://personal.broadinstitute.org/konradk/loftee_data/${ASSEMBLY}"
UCSC_DB="https://hgdownload.soe.ucsc.edu/goldenPath/hg38/database"
SPLICEAI_BASE="https://ftp.ensembl.org/pub/data_files/homo_sapiens/GRCh38/variation_plugins"

# --- destinations (from config, resolved to repo root) ------------------------
absdir() { local p="$1"; [[ "$p" = /* ]] || p="${ROOT}/${p}"; echo "$p"; }
VEP_CACHE_DIR="$(absdir "$(yaml_get "$CONFIG" reference.vep_cache_dir)")"
FASTA_PATH="$(absdir "$(yaml_get "$CONFIG" reference.fasta.path)")"
HA_PATH="$(absdir "$(yaml_get "$CONFIG" plugins.LoF.human_ancestor_fa)")"
SQL_PATH="$(absdir "$(yaml_get "$CONFIG" plugins.LoF.conservation_file)")"
GERP_PATH="$(absdir "$(yaml_get "$CONFIG" plugins.LoF.gerp_bigwig)")"
SPLICEAI_PATH_RAW="$(yaml_get "$CONFIG" plugins.SpliceAI.snv)"
SPLICEAI_PATH=""
[[ -z "$SPLICEAI_PATH_RAW" ]] || SPLICEAI_PATH="$(absdir "$SPLICEAI_PATH_RAW")"
RM_PATH="$(absdir "$(yaml_get "$CONFIG" custom_tracks.RepeatMasker.file)")"
SEGDUP_PATH="$(absdir "$(yaml_get "$CONFIG" custom_tracks.SegDup.file)")"
LIFTOVER_CHAIN_RAW="$(yaml_get "$CONFIG" liftover.grch37_to_grch38.chain)"
LIFTOVER_CHAIN=""
[[ -z "$LIFTOVER_CHAIN_RAW" ]] || LIFTOVER_CHAIN="$(absdir "$LIFTOVER_CHAIN_RAW")"

# ============================================================================ #
# 1. VEP offline cache
# ============================================================================ #
if want vep_cache; then
    log "=== VEP cache (release $VEP_REL, $ASSEMBLY) ==="
    mkdir -p "$VEP_CACHE_DIR"
    CACHE_TARBALL="${VEP_CACHE_DIR}/homo_sapiens_vep_${VEP_REL}_${ASSEMBLY}.tar.gz"
    CACHE_URL="${ENSEMBL_FTP}/variation/indexed_vep_cache/homo_sapiens_vep_${VEP_REL}_${ASSEMBLY}.tar.gz"
    CACHE_READY="${VEP_CACHE_DIR}/.homo_sapiens_vep_${VEP_REL}_${ASSEMBLY}.complete"
    if [[ -f "$CACHE_READY" && -d "${VEP_CACHE_DIR}/homo_sapiens/${VEP_REL}_${ASSEMBLY}" ]]; then
        log "VEP cache already extracted, skip."
    else
        CHECKSUM_URL="${ENSEMBL_FTP}/variation/indexed_vep_cache/CHECKSUMS"
        # Do not exit awk early: under `set -o pipefail`, closing the pipe while
        # curl is still writing turns an otherwise valid match into curl(23).
        CHECKSUM_ENTRY="$(curl -fsSL --max-time 60 "$CHECKSUM_URL" | awk -v name="$(basename "$CACHE_TARBALL")" '$NF == name { print $1 ":" $2 }')"
        [[ -n "$CHECKSUM_ENTRY" ]] || die "could not find cache in Ensembl CHECKSUMS"
        python3 "${HERE}/parallel_fetch.py" "$CACHE_URL" "$CACHE_TARBALL" \
            --connections 8 --chunk-mib 128 --sum-check "$CHECKSUM_ENTRY" \
            || die "VEP cache download or checksum validation failed"
        log "checking VEP cache archive integrity..."
        tar -tzf "$CACHE_TARBALL" >/dev/null || die "VEP cache archive integrity check failed"
        EXTRACT_STAGE="$(mktemp -d "${VEP_CACHE_DIR}/.vep-extract.XXXXXX")"
        log "extracting VEP cache to staging directory..."
        tar -xzf "$CACHE_TARBALL" -C "$EXTRACT_STAGE"
        [[ -d "${EXTRACT_STAGE}/homo_sapiens/${VEP_REL}_${ASSEMBLY}" ]] \
            || die "VEP cache archive did not contain the expected directory"
        [[ ! -e "${VEP_CACHE_DIR}/homo_sapiens" ]] \
            || die "incomplete cache directory exists; move it aside before retrying"
        mv "${EXTRACT_STAGE}/homo_sapiens" "${VEP_CACHE_DIR}/homo_sapiens"
        rmdir "$EXTRACT_STAGE"
        touch "$CACHE_READY"
        rm -f "$CACHE_TARBALL"
    fi
fi

# ============================================================================ #
# 2. Reference FASTA  (Ensembl ships .fa.gz gzip; VEP wants bgzip -> re-compress)
# ============================================================================ #
if want fasta; then
    log "=== reference FASTA ==="
    mkdir -p "$(dirname "$FASTA_PATH")"
    if [[ -s "$FASTA_PATH" ]]; then
        log "FASTA present, skip."
    else
        RAW="${FASTA_PATH%.gz}.ensembl.gz"
        fetch "${ENSEMBL_FTP}/fasta/homo_sapiens/dna/Homo_sapiens.${ASSEMBLY}.dna.primary_assembly.fa.gz" "$RAW"
        log "re-compressing FASTA as bgzip (VEP/LOFTEE require bgzip, not gzip)"
        # decompress then bgzip; route bgzip through container if needed
        gunzip -c "$RAW" > "${FASTA_PATH%.gz}"
        ( cd "$(dirname "$FASTA_PATH")" && hts bgzip -f "$(basename "${FASTA_PATH%.gz}")" )
        rm -f "$RAW"
        ( cd "$(dirname "$FASTA_PATH")" && hts samtools faidx "$(basename "$FASTA_PATH")" 2>/dev/null || true )
    fi
fi

# ============================================================================ #
# 3. LOFTEE GRCh38 data (Broad public bucket)
# ============================================================================ #
if want loftee; then
    log "=== LOFTEE $ASSEMBLY data ==="
    if [[ "$ASSEMBLY" != "GRCh38" ]]; then
        warn "LOFTEE auto-download here targets GRCh38; assembly is $ASSEMBLY — skipping."
    else
        mkdir -p "$(dirname "$HA_PATH")"
        fetch "${LOFTEE_BASE}/human_ancestor.fa.gz"      "$HA_PATH" || warn "human_ancestor download failed"
        fetch "${LOFTEE_BASE}/human_ancestor.fa.gz.fai"  "${HA_PATH}.fai" || true
        fetch "${LOFTEE_BASE}/human_ancestor.fa.gz.gzi"  "${HA_PATH}.gzi" || true
        # conservation DB ships gzipped as loftee.sql.gz
        if [[ ! -s "$SQL_PATH" ]]; then
            fetch "${LOFTEE_BASE}/loftee.sql.gz" "${SQL_PATH}.gz" && gunzip -f "${SQL_PATH}.gz" || warn "loftee.sql download failed"
        fi
        fetch "${LOFTEE_BASE}/gerp_conservation_scores.homo_sapiens.GRCh38.bw" "$GERP_PATH" || warn "GERP bigwig download failed"
    fi
fi

# ============================================================================ #
# 4. SpliceAI masked SNV scores — Ensembl MANE v1.4, GRCh38
# ============================================================================ #
if want spliceai; then
    log "=== SpliceAI masked SNV scores (Ensembl MANE v1.4, GRCh38) ==="
    if [[ "$ASSEMBLY" != "GRCh38" ]]; then
        die "the configured automatic SpliceAI dataset is GRCh38-only; assembly is $ASSEMBLY"
    fi
    [[ -n "$SPLICEAI_PATH" ]] || die "plugins.SpliceAI.snv is not configured"
    SPLICEAI_NAME="spliceai_scores.masked.snv.ensembl_mane_v1.4.grch38.vcf.gz"
    python3 "${HERE}/parallel_fetch.py" \
        "${SPLICEAI_BASE}/${SPLICEAI_NAME}" "$SPLICEAI_PATH" \
        --connections 8 --chunk-mib 128 \
        || die "SpliceAI SNV dataset download failed"
    python3 "${HERE}/parallel_fetch.py" \
        "${SPLICEAI_BASE}/${SPLICEAI_NAME}.tbi" "${SPLICEAI_PATH}.tbi" \
        --connections 1 --chunk-mib 4 \
        || die "SpliceAI SNV index download failed"
fi

# ============================================================================ #
# 5. RepeatMasker + SegDup — UCSC hg38 tracks, cleaned to VEP-ready BED
# ----------------------------------------------------------------------------
# UCSC uses `chr1`/`chrM`; the Ensembl VEP cache uses `1`/`MT`. We strip the
# `chr` prefix, rename chrM->MT, keep primary contigs only (1-22, X, Y, MT),
# sort, bgzip, and tabix-index so VEP --custom can query the track.
# ============================================================================ #

# clean_ucsc_bed <chrom_col> <start_col> <end_col> <name_expr> <out.bed.gz>
# reads UCSC .txt.gz on stdin (tab-separated), emits a sorted bgzipped BED+tabix.
clean_ucsc_bed() {
    local cchr="$1" cstart="$2" cend="$3" name_expr="$4" out="$5"
    local tmp="${out%.gz}"
    # awk: normalise contig, drop non-primary; BED is 0-based start (UCSC txt
    # genoStart/chromStart are already 0-based half-open, so pass through).
    awk -v FS='\t' -v OFS='\t' \
        -v cc="$cchr" -v cs="$cstart" -v ce="$cend" \
        "{ chr=\$cc; sub(/^chr/,\"\",chr); if(chr==\"M\")chr=\"MT\";
           if(chr ~ /^([0-9]+|X|Y|MT)\$/) print chr, \$cs, \$ce, ($name_expr) }" \
        | sort -k1,1 -k2,2n > "$tmp"
    ( cd "$(dirname "$out")" && hts bgzip -f "$(basename "$tmp")" )
    ( cd "$(dirname "$out")" && hts tabix -f -p bed "$(basename "$out")" )
    log "wrote $(basename "$out")"
}

if want repeatmasker; then
    log "=== RepeatMasker (UCSC hg38 rmsk -> cleaned BED) ==="
    mkdir -p "$(dirname "$RM_PATH")"
    if [[ -s "$RM_PATH" ]]; then
        log "RepeatMasker present, skip."
    else
        RAW="${RM_PATH%.bed.gz}.rmsk.txt.gz"
        fetch "${UCSC_DB}/rmsk.txt.gz" "$RAW" || die "UCSC rmsk download failed"
        # rmsk.txt cols: 6 genoName, 7 genoStart, 8 genoEnd, 11 repName, 12 repClass, 13 repFamily
        log "cleaning rmsk (chr-strip, primary contigs, name=repClass|repFamily|repName)"
        gzip -cd "$RAW" | clean_ucsc_bed 6 7 8 '$12"|"$13"|"$11' "$RM_PATH"
        rm -f "$RAW"
    fi
fi

if want segdup; then
    log "=== SegDup (UCSC hg38 genomicSuperDups -> cleaned BED) ==="
    mkdir -p "$(dirname "$SEGDUP_PATH")"
    if [[ -s "$SEGDUP_PATH" ]]; then
        log "SegDup present, skip."
    else
        RAW="${SEGDUP_PATH%.bed.gz}.genomicSuperDups.txt.gz"
        fetch "${UCSC_DB}/genomicSuperDups.txt.gz" "$RAW" || die "UCSC genomicSuperDups download failed"
        # genomicSuperDups cols: 2 chrom, 3 chromStart, 4 chromEnd, 27 fracMatch
        log "cleaning genomicSuperDups (chr-strip, primary contigs, name=fracMatch)"
        gzip -cd "$RAW" | clean_ucsc_bed 2 3 4 '$27' "$SEGDUP_PATH"
        rm -f "$RAW"
    fi
fi

# ============================================================================ #
# 6. GRCh37/hg19 -> GRCh38 UCSC chain
# ============================================================================ #
if want liftover; then
    log "=== GRCh37/hg19 -> GRCh38 liftover chain ==="
    [[ "$ASSEMBLY" == "GRCh38" ]] || die "liftover target requires reference.assembly: GRCh38"
    [[ -n "$LIFTOVER_CHAIN" ]] || die "liftover.grch37_to_grch38.chain is not configured"
    RAW_CHAIN="${LIFTOVER_CHAIN%.gz}.ucsc.chain.gz"
    fetch \
      "https://hgdownload.soe.ucsc.edu/goldenPath/hg19/liftOver/hg19ToHg38.over.chain.gz" \
      "$RAW_CHAIN" \
      || die "UCSC hg19ToHg38 chain download failed"
    if [[ ! -s "$LIFTOVER_CHAIN" ]]; then
        log "normalizing UCSC chain contigs to Ensembl 1..22/X/Y/MT"
        gzip -cd "$RAW_CHAIN" \
          | awk 'BEGIN{OFS=" "}
              /^chain / {
                sub(/^chr/, "", $3); sub(/^chr/, "", $8);
                if ($3=="M") $3="MT"; if ($8=="M") $8="MT"
              }
              {print}' \
          | gzip -c > "$LIFTOVER_CHAIN"
    fi
    (
      cd "$(dirname "$LIFTOVER_CHAIN")"
      shasum -a 256 "$(basename "$LIFTOVER_CHAIN")" > "$(basename "$LIFTOVER_CHAIN").sha256.local"
    )
fi

# ============================================================================ #
# dbNSFP status reminder (cannot be auto-downloaded — academic registration).
# ============================================================================ #
DBNSFP_ENABLED="$(yaml_get "$CONFIG" plugins.dbNSFP.enabled)"
DBNSFP_PATH="$(absdir "$(yaml_get "$CONFIG" plugins.dbNSFP.path)")"
if [[ "$DBNSFP_ENABLED" == "true" && ! -s "$DBNSFP_PATH" ]]; then
    warn "-------------------------------------------------------------------"
    warn "dbNSFP is enabled but not present at: $DBNSFP_PATH"
    warn "dbNSFP (~50 GB) CANNOT be auto-downloaded. One-time manual steps:"
    warn "  1. Register (institutional email) at https://www.dbnsfp.org/download"
    warn "     -> you receive an academic access code after verification."
    warn "  2. Request the download links with that email + access code."
    warn "  3. Download + unzip the academic release (currently v5.3.1a)."
    warn "  4. Build the GRCh38 file for VEP:"
    warn "       scripts/prepare_dbnsfp.sh /path/to/dbNSFP5.3.1a_unzipped_dir"
    warn "dbNSFP is required by the diagnostic profile, so annotation will stop"
    warn "until the configured file and tabix index are present."
    warn "-------------------------------------------------------------------"
fi

python3 "${ROOT}/pipeline/check_dbnsfp_version.py" --config "$CONFIG" \
    || warn "dbNSFP update check could not be completed"

log "download_references.sh done. Review WARNs above for sources needing manual fetch."
log "Still to supply manually: dbNSFP (scripts/prepare_dbnsfp.sh), promoterAI, LoGoFunc."
log "Run scripts/fetch_clinvar.sh (or the main run script) to get ClinVar."
