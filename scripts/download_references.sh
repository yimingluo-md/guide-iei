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
#   * ENCODE SCREEN cCRE Registry V4      ~30 MB  (cleaned GRCh38 BED + tabix)
#   * hg19 primary FASTA + hg19->hg38 chain ~1 GB (GRCh37 VCF intake only)
#
# Does NOT fetch (you supply these):
#   * dbNSFP  (~50 GB; CADD/REVEL/AlphaMissense/SIFT/PolyPhen/... in one file)
#             NOT auto-downloadable: academic registration -> emailed access code ->
#             request links. See the printed instructions below + scripts/prepare_dbnsfp.sh.
#   * PromoterAI (Illumina licensed files; prepare with scripts/prepare_promoterai.sh)
#   * LoGoFunc   (public optional 3.66 GB table; use scripts/download_logofunc.sh)
#   * CADD WGS   (public optional score-only tables; use scripts/download_cadd_wgs.sh)
#   * ClinVar               (fetched per-run by fetch_clinvar.sh)
#
# Usage:
#   scripts/download_references.sh [config.yaml] [--only vep_cache,fasta,loftee,spliceai,repeatmasker,segdup,ccre,liftover]
#       [--skip-final-status]  # internal: bulk install reports status once
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
SKIP_FINAL_STATUS=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --only) ONLY="$2"; shift 2 ;;
        --skip-final-status) SKIP_FINAL_STATUS=1; shift ;;
        *) shift ;;
    esac
done
[[ -f "$CONFIG" ]] || die "config not found: $CONFIG"

# hts() falls back to the container when host bgzip/tabix/samtools are absent;
# honour the configured runtime/image instead of the docker/vep-annotate:latest
# defaults (a podman-only host previously failed here despite correct config).
RUNTIME="$(yaml_get "$CONFIG" container.runtime)"; RUNTIME="${RUNTIME:-docker}"
IMAGE="$(yaml_get "$CONFIG" container.image)";     IMAGE="${IMAGE:-vep-annotate:latest}"
export RUNTIME IMAGE

want() { [[ -z "$ONLY" ]] || [[ ",$ONLY," == *",$1,"* ]]; }

# No upstream checksum exists for some mirrors; a BGZF file still proves its
# own integrity cheaply: gzip magic at byte 0 and the fixed 28-byte BGZF EOF
# block at the end. Zero-filled or truncated downloads fail both instantly.
verify_bgzf() {
    python3 - "$1" <<'BGZF'
import sys
eof = bytes.fromhex("1f8b08040000000000ff0600424302001b0003000000000000000000")
path = sys.argv[1]
try:
    with open(path, "rb") as handle:
        head = handle.read(2)
        handle.seek(-28, 2)
        tail = handle.read(28)
except OSError:
    sys.exit(1)
sys.exit(0 if head == b"\x1f\x8b" and tail == eof else 1)
BGZF
}

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

# ---------------------------------------------------------------------------
# Fast public mirror of the prepared VEP 113 / GRCh38 reference bundle
# (Ensembl data are unrestricted; LOFTEE is from an MIT-licensed project).
# Every mirror download is verified against the pinned SHA-256 recorded at
# publication; any failure quietly falls back to the canonical source.
# Set IEI_REFERENCE_MIRROR="" to disable the mirror entirely.
REF_MIRROR="${IEI_REFERENCE_MIRROR-https://huggingface.co/datasets/luoyiming1991/vep113-grch38-reference-bundle/resolve/main}"
MIRROR_SHA_VEP_CACHE="bd49c25265b5940330c5b556d5eb2edeff0ad04468e1b8cfc70a7fe518da5d67"
MIRROR_SHA_FASTA="6848fea59a70a3e8439849b9f5033281f63fe2641357ba492f0ca76b7045ccbd"
MIRROR_SHA_FASTA_FAI="0998f61682f4041b11f0d156e1db6dae3e4c743e26643a3f45ea7faea70cb604"
MIRROR_SHA_FASTA_GZI="47b1b878f0fe2903b3c04e043d22c1166f63527f1c721dada4465025a6b8e8ce"
MIRROR_SHA_HA="624b0e0f8e1c4e7c8639edf17df03f213621d1f56f6fa1c8025fad56082b157b"
MIRROR_SHA_HA_FAI="703e9a2011886f90679e99d0333ca91afc81df726db91c76d57b9e2d6f182ec5"
MIRROR_SHA_HA_GZI="a31e4ee63e519a0da4a8e2750dabbca177129b7f312b2a993c17a99981e6a3bf"
MIRROR_SHA_LOFTEE_SQL="22e214f1513d67682602b5915dfd220ea9d8360448f0de0b8db72c49bf03649a"
MIRROR_SHA_GERP="8801e57ce8effbef9248b122caee16da338bfa8319dd41392c9ce4e58ea6cfe8"

_sha256_of() { sha256_file "$1"; }

mirror_fetch() { # mirror_fetch <mirror-filename> <sha256> <dest>; 0 on verified success
    local name="$1" want="$2" dest="$3" got
    [[ -n "$REF_MIRROR" ]] || return 1
    if [[ -s "$dest" ]] && [[ "$(_sha256_of "$dest")" == "$want" ]]; then
        log "mirror: $name already present and verified"
        return 0
    fi
    log "mirror: fetching $name"
    if ! fetch "${REF_MIRROR}/${name}" "$dest"; then
        rm -f "$dest.part"
        return 1
    fi
    got="$(_sha256_of "$dest")"
    if [[ "$got" != "$want" ]]; then
        warn "mirror checksum mismatch for $name (expected $want, got $got); using the canonical source instead"
        rm -f "$dest"
        return 1
    fi
    log "mirror: verified $name"
}

# --- destinations (from config, resolved to repo root) ------------------------
absdir() { local p="$1"; [[ "$p" = /* ]] || p="${ROOT}/${p}"; echo "$p"; }
# A missing key must stay empty: absdir("") would return "${ROOT}/", turning
# "not configured" into a directory path that downstream writes clobber.
absdir_opt() {
    local raw="$1"
    [[ -n "$raw" ]] && absdir "$raw" || echo ""
}
VEP_CACHE_DIR="$(absdir_opt "$(yaml_get "$CONFIG" reference.vep_cache_dir)")"
FASTA_PATH="$(absdir_opt "$(yaml_get "$CONFIG" reference.fasta.path)")"
HA_PATH="$(absdir_opt "$(yaml_get "$CONFIG" plugins.LoF.human_ancestor_fa)")"
SQL_PATH="$(absdir_opt "$(yaml_get "$CONFIG" plugins.LoF.conservation_file)")"
GERP_PATH="$(absdir_opt "$(yaml_get "$CONFIG" plugins.LoF.gerp_bigwig)")"
SPLICEAI_PATH_RAW="$(yaml_get "$CONFIG" plugins.SpliceAI.snv)"
SPLICEAI_PATH=""
[[ -z "$SPLICEAI_PATH_RAW" ]] || SPLICEAI_PATH="$(absdir "$SPLICEAI_PATH_RAW")"
RM_PATH="$(absdir_opt "$(yaml_get "$CONFIG" custom_tracks.RepeatMasker.file)")"
SEGDUP_PATH="$(absdir_opt "$(yaml_get "$CONFIG" custom_tracks.SegDup.file)")"
LIFTOVER_CHAIN_RAW="$(yaml_get "$CONFIG" liftover.grch37_to_grch38.chain)"
LIFTOVER_CHAIN=""
[[ -z "$LIFTOVER_CHAIN_RAW" ]] || LIFTOVER_CHAIN="$(absdir "$LIFTOVER_CHAIN_RAW")"
LIFTOVER_SOURCE_FASTA_RAW="$(yaml_get "$CONFIG" liftover.grch37_to_grch38.source_fasta)"
LIFTOVER_SOURCE_FASTA=""
[[ -z "$LIFTOVER_SOURCE_FASTA_RAW" ]] || LIFTOVER_SOURCE_FASTA="$(absdir "$LIFTOVER_SOURCE_FASTA_RAW")"
CCRE_PATH_RAW="$(yaml_get "$CONFIG" wgs_review.ccre.bed)"
CCRE_PATH=""
[[ -z "$CCRE_PATH_RAW" ]] || CCRE_PATH="$(absdir "$CCRE_PATH_RAW")"
CCRE_SOURCE_URL="$(yaml_get "$CONFIG" wgs_review.ccre.source_url)"
GENE_TSS_PATH_RAW="$(yaml_get "$CONFIG" wgs_review.gene_tss.path)"
GENE_TSS_PATH=""
[[ -z "$GENE_TSS_PATH_RAW" ]] || GENE_TSS_PATH="$(absdir "$GENE_TSS_PATH_RAW")"
GENE_TSS_GTF_RAW="$(yaml_get "$CONFIG" wgs_review.gene_tss.gtf)"
GENE_TSS_GTF=""
[[ -z "$GENE_TSS_GTF_RAW" ]] || GENE_TSS_GTF="$(absdir "$GENE_TSS_GTF_RAW")"
GENE_TSS_RELEASE="$(yaml_get "$CONFIG" wgs_review.gene_tss.ensembl_release)"
GENE_TSS_RELEASE="${GENE_TSS_RELEASE:-$VEP_REL}"

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
        MIRROR_CACHE_NAME="homo_sapiens_vep_113_GRCh38.cache.tar.gz"
        if [[ "$VEP_REL" == "113" && "$ASSEMBLY" == "GRCh38" ]] \
           && mirror_fetch "$MIRROR_CACHE_NAME" "$MIRROR_SHA_VEP_CACHE" "${VEP_CACHE_DIR}/${MIRROR_CACHE_NAME}"; then
            CACHE_TARBALL="${VEP_CACHE_DIR}/${MIRROR_CACHE_NAME}"
        else
            CHECKSUM_URL="${ENSEMBL_FTP}/variation/indexed_vep_cache/CHECKSUMS"
            # Do not exit awk early: under `set -o pipefail`, closing the pipe while
            # curl is still writing turns an otherwise valid match into curl(23).
            CHECKSUM_ENTRY="$(curl -fsSL --max-time 60 "$CHECKSUM_URL" | awk -v name="$(basename "$CACHE_TARBALL")" '$NF == name { print $1 ":" $2 }')"
            [[ -n "$CHECKSUM_ENTRY" ]] || die "could not find cache in Ensembl CHECKSUMS"
            python3 "${HERE}/parallel_fetch.py" "$CACHE_URL" "$CACHE_TARBALL" \
                --connections 8 --chunk-mib 128 --sum-check "$CHECKSUM_ENTRY" \
                || die "VEP cache download or checksum validation failed"
        fi
        EXTRACT_STAGE="$(mktemp -d "${VEP_CACHE_DIR}/.vep-extract.XXXXXX")"
        # The archive already passed its pinned SHA-256/BSD checksum. tar's
        # extraction also validates the gzip stream and exits nonzero on
        # corruption, so a separate full `tar -t` pass only decompresses the
        # multi-GB archive twice. Staging + structure checks preserve atomicity.
        log "extracting verified VEP cache to staging directory..."
        tar -xzf "$CACHE_TARBALL" -C "$EXTRACT_STAGE" \
            || { rm -rf "$EXTRACT_STAGE"; die "VEP cache archive extraction failed"; }
        [[ -d "${EXTRACT_STAGE}/homo_sapiens/${VEP_REL}_${ASSEMBLY}" ]] \
            || die "VEP cache archive did not contain the expected directory"
        [[ ! -e "${VEP_CACHE_DIR}/homo_sapiens" ]] \
            || die "incomplete cache directory exists; move it aside before retrying"
        mv "${EXTRACT_STAGE}/homo_sapiens" "${VEP_CACHE_DIR}/homo_sapiens"
        rm -rf "$EXTRACT_STAGE"  # mirror tarball may carry a sentinel file
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
    if [[ -s "$FASTA_PATH" ]] && ! bgzf_complete "$FASTA_PATH"; then
        # A BGZF stream without its EOF marker was cut short by an interrupted
        # write; a bare -s test kept it forever (audit M12).
        warn "reference FASTA is an incomplete BGZF stream; removing it so it is re-installed: $FASTA_PATH"
        rm -f "$FASTA_PATH" "${FASTA_PATH}.fai" "${FASTA_PATH}.gzi"
    fi
    if [[ -s "$FASTA_PATH" ]]; then
        log "FASTA present, skip."
    elif [[ "$ASSEMBLY" == "GRCh38" \
            && "$(basename "$FASTA_PATH")" == "Homo_sapiens.GRCh38.dna.primary_assembly.fa.gz" ]] \
         && mirror_fetch "Homo_sapiens.GRCh38.dna.primary_assembly.fa.gz" "$MIRROR_SHA_FASTA" "$FASTA_PATH" \
         && mirror_fetch "Homo_sapiens.GRCh38.dna.primary_assembly.fa.gz.fai" "$MIRROR_SHA_FASTA_FAI" "${FASTA_PATH}.fai" \
         && mirror_fetch "Homo_sapiens.GRCh38.dna.primary_assembly.fa.gz.gzi" "$MIRROR_SHA_FASTA_GZI" "${FASTA_PATH}.gzi"; then
        log "FASTA installed from mirror (already BGZF with .fai/.gzi; no re-compression needed)"
    else
        RAW="${FASTA_PATH%.gz}.ensembl.gz"
        FASTA_URL="${ENSEMBL_FTP}/fasta/homo_sapiens/dna/Homo_sapiens.${ASSEMBLY}.dna.primary_assembly.fa.gz"
        fetch "$FASTA_URL" "$RAW"
        # An interrupted/resumed transfer can leave a truncated gzip stream.
        # Without this check the corrupt file persists (fetch skips existing
        # non-empty destinations) and every later run fails at gunzip.
        if ! gzip -t "$RAW" 2>/dev/null; then
            warn "downloaded FASTA failed gzip integrity check; refetching from scratch"
            rm -f "$RAW" "$RAW.part"
            fetch "$FASTA_URL" "$RAW"
            gzip -t "$RAW" 2>/dev/null || { rm -f "$RAW" "$RAW.part"; die "FASTA is corrupt after a clean refetch: $FASTA_URL"; }
        fi
        log "re-compressing FASTA as bgzip (VEP/LOFTEE require bgzip, not gzip)"
        # decompress then bgzip; route bgzip through container if needed. The
        # final name is only ever given to a complete BGZF stream.
        gunzip -c "$RAW" > "${FASTA_PATH%.gz}" || { rm -f "${FASTA_PATH%.gz}"; die "FASTA decompression failed: $RAW"; }
        publish_bgzf "${FASTA_PATH%.gz}" "$FASTA_PATH" || { rm -f "${FASTA_PATH%.gz}"; die "FASTA could not be published as a complete BGZF file: $FASTA_PATH"; }
        rm -f "$RAW"
        if ! ( cd "$(dirname "$FASTA_PATH")" && hts samtools faidx "$(basename "$FASTA_PATH")" 2>/dev/null ); then
            sleep 5  # same settle-and-retry as tabix above
            ( cd "$(dirname "$FASTA_PATH")" && hts samtools faidx "$(basename "$FASTA_PATH")" 2>/dev/null ) \
                || warn "FASTA .fai index could not be built; rerun this download or build it with samtools faidx"
        fi
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
        # LoF is required:true in the shipped config — a missing LOFTEE
        # resource fails the run at build_vep_command anyway, but only after
        # the remaining multi-GB downloads. Fail here, loudly and early, and
        # keep the fetch/gunzip failures distinguishable.
        mirror_fetch "human_ancestor.fa.gz" "$MIRROR_SHA_HA" "$HA_PATH" \
            || fetch "${LOFTEE_BASE}/human_ancestor.fa.gz" "$HA_PATH" || die "human_ancestor.fa.gz download failed"
        mirror_fetch "human_ancestor.fa.gz.fai" "$MIRROR_SHA_HA_FAI" "${HA_PATH}.fai" \
            || fetch "${LOFTEE_BASE}/human_ancestor.fa.gz.fai" "${HA_PATH}.fai" || die "human_ancestor .fai download failed"
        mirror_fetch "human_ancestor.fa.gz.gzi" "$MIRROR_SHA_HA_GZI" "${HA_PATH}.gzi" \
            || fetch "${LOFTEE_BASE}/human_ancestor.fa.gz.gzi" "${HA_PATH}.gzi" || die "human_ancestor .gzi download failed"
        # conservation DB ships gzipped as loftee.sql.gz upstream; the mirror
        # serves it ready to use
        if [[ ! -s "$SQL_PATH" ]]; then
            if ! mirror_fetch "loftee.sql" "$MIRROR_SHA_LOFTEE_SQL" "$SQL_PATH"; then
                fetch "${LOFTEE_BASE}/loftee.sql.gz" "${SQL_PATH}.gz" || die "loftee.sql.gz download failed"
                gunzip -f "${SQL_PATH}.gz" || die "loftee.sql.gz could not be decompressed (truncated or corrupt download)"
            fi
        fi
        mirror_fetch "gerp_conservation_scores.homo_sapiens.GRCh38.bw" "$MIRROR_SHA_GERP" "$GERP_PATH" \
            || fetch "${LOFTEE_BASE}/gerp_conservation_scores.homo_sapiens.GRCh38.bw" "$GERP_PATH" || die "GERP bigwig download failed"
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
    verify_bgzf "$SPLICEAI_PATH" || die "SpliceAI SNV dataset failed BGZF integrity check (delete it and re-run)"
    python3 "${HERE}/parallel_fetch.py" \
        "${SPLICEAI_BASE}/${SPLICEAI_NAME}.tbi" "${SPLICEAI_PATH}.tbi" \
        --connections 1 --chunk-mib 4 \
        || die "SpliceAI SNV index download failed"
    verify_bgzf "${SPLICEAI_PATH}.tbi" || die "SpliceAI SNV index failed BGZF integrity check (delete it and re-run)"
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
    # On external drives (VirtioFS bind + FSKit exFAT) a file written by one
    # container can be incompletely visible to a container started moments
    # later, failing tbx_index_build on a perfectly valid file. Settle and
    # retry once before treating the failure as real.
    if ! ( cd "$(dirname "$out")" && hts tabix -f -p bed "$(basename "$out")" ); then
        log "WARN  tabix failed on fresh $(basename "$out"); retrying after write settling"
        sleep 5
        ( cd "$(dirname "$out")" && hts tabix -f -p bed "$(basename "$out")" )
    fi
    log "wrote $(basename "$out")"
}

# A cleaned track is usable only when the BGZF stream is intact AND its tabix
# index exists — an interrupted write leaves a non-empty corrupt file that a
# bare -s test would skip forever.
bed_track_ready() {
    [[ -s "$1" ]] && gzip -t "$1" 2>/dev/null && { [[ -s "$1.tbi" ]] || [[ -s "$1.csi" ]]; }
}

# fetch + require an intact gzip stream, refetching once from scratch. fetch()
# skips existing non-empty destinations, so a raw download truncated by an
# interruption would otherwise poison every rebuild that consumes it (and a
# truncated gzip feeding a pipeline can yield a silently truncated track).
fetch_gzip_verified() {
    local url="$1" dest="$2"
    fetch "$url" "$dest" || return 1
    if ! gzip -t "$dest" 2>/dev/null; then
        log "WARN  $(basename "$dest") failed gzip integrity check; refetching from scratch"
        rm -f "$dest" "$dest.part"
        fetch "$url" "$dest" || return 1
        gzip -t "$dest" 2>/dev/null || { rm -f "$dest" "$dest.part"; return 1; }
    fi
}

if want repeatmasker; then
    log "=== RepeatMasker (UCSC hg38 rmsk -> cleaned BED) ==="
    mkdir -p "$(dirname "$RM_PATH")"
    if bed_track_ready "$RM_PATH"; then
        log "RepeatMasker present, skip."
    else
        rm -f "$RM_PATH" "$RM_PATH.tbi" "$RM_PATH.csi"
        RAW="${RM_PATH%.bed.gz}.rmsk.txt.gz"
        fetch_gzip_verified "${UCSC_DB}/rmsk.txt.gz" "$RAW" || die "UCSC rmsk download failed"
        # rmsk.txt cols: 6 genoName, 7 genoStart, 8 genoEnd, 11 repName, 12 repClass, 13 repFamily
        log "cleaning rmsk (chr-strip, primary contigs, name=repClass|repFamily|repName)"
        gzip -cd "$RAW" | clean_ucsc_bed 6 7 8 '$12"|"$13"|"$11' "$RM_PATH"
        rm -f "$RAW"
    fi
fi

if want segdup; then
    log "=== SegDup (UCSC hg38 genomicSuperDups -> cleaned BED) ==="
    mkdir -p "$(dirname "$SEGDUP_PATH")"
    if bed_track_ready "$SEGDUP_PATH"; then
        log "SegDup present, skip."
    else
        rm -f "$SEGDUP_PATH" "$SEGDUP_PATH.tbi" "$SEGDUP_PATH.csi"
        RAW="${SEGDUP_PATH%.bed.gz}.genomicSuperDups.txt.gz"
        fetch_gzip_verified "${UCSC_DB}/genomicSuperDups.txt.gz" "$RAW" || die "UCSC genomicSuperDups download failed"
        # genomicSuperDups cols: 2 chrom, 3 chromStart, 4 chromEnd, 27 fracMatch
        log "cleaning genomicSuperDups (chr-strip, primary contigs, name=fracMatch)"
        gzip -cd "$RAW" | clean_ucsc_bed 2 3 4 '$27' "$SEGDUP_PATH"
        rm -f "$RAW"
    fi
fi

# ============================================================================ #
# 6. ENCODE SCREEN Registry V4 cCREs — native WGS-review BED
# ----------------------------------------------------------------------------
# The official BED is GRCh38 with chr-prefixed contigs and six columns:
# chrom, start, end, SCREEN/DCC accession, cCRE accession, cCRE class. The
# review importer needs only the intervals, but retains accession and class so
# the prepared resource can support richer display without another download.
# ============================================================================ #
if want ccre; then
    log "=== ENCODE SCREEN cCREs (Registry V4, GRCh38) ==="
    [[ "$ASSEMBLY" == "GRCh38" ]] \
        || die "the bundled SCREEN cCRE resource is GRCh38-only; assembly is $ASSEMBLY"
    [[ -n "$CCRE_PATH" ]] || die "wgs_review.ccre.bed is not configured"
    [[ -n "$CCRE_SOURCE_URL" ]] || die "wgs_review.ccre.source_url is not configured"
    mkdir -p "$(dirname "$CCRE_PATH")"
    if [[ -s "$CCRE_PATH" && ( -s "${CCRE_PATH}.tbi" || -s "${CCRE_PATH}.csi" ) ]]; then
        log "SCREEN cCRE BED and index present, skip."
    else
        RAW="${CCRE_PATH%.bed.gz}.official.bed"
        CLEAN="${CCRE_PATH%.gz}"
        fetch "$CCRE_SOURCE_URL" "$RAW" || die "SCREEN Registry V4 download failed"
        log "normalizing SCREEN cCRE contigs and validating interval rows"
        awk -v FS='\t' -v OFS='\t' '
          NF >= 6 {
            chr=$1; sub(/^chr/, "", chr); if (chr=="M") chr="MT"
            if (chr !~ /^([1-9]|1[0-9]|2[0-2]|X|Y|MT)$/) next
            if ($2 !~ /^[0-9]+$/ || $3 !~ /^[0-9]+$/ || $3 <= $2) next
            if ($5 !~ /^EH38E[0-9]+$/ || $6 == "") next
            print chr, $2, $3, $5, $6
          }
        ' "$RAW" | sort -k1,1 -k2,2n -k3,3n > "$CLEAN"
        CCRE_ROWS="$(wc -l < "$CLEAN" | tr -d ' ')"
        [[ "$CCRE_ROWS" -ge 100000 ]] \
            || die "SCREEN cCRE cleaning retained only $CCRE_ROWS rows; refusing incomplete resource"
        ( cd "$(dirname "$CCRE_PATH")" && hts bgzip -f "$(basename "$CLEAN")" )
        ( cd "$(dirname "$CCRE_PATH")" && hts tabix -f -p bed "$(basename "$CCRE_PATH")" )
        rm -f "$RAW"
        log "wrote $CCRE_ROWS SCREEN cCRE intervals"
    fi
    write_sha256_sidecar "$CCRE_PATH" || die "cannot record the SCREEN cCRE checksum: $CCRE_PATH"
    [[ -n "$GENE_TSS_PATH" ]] || die "wgs_review.gene_tss.path is not configured"
    [[ -n "$GENE_TSS_GTF" ]] || die "wgs_review.gene_tss.gtf is not configured"
    if [[ ! -s "$GENE_TSS_GTF" ]]; then
        mkdir -p "$(dirname "$GENE_TSS_GTF")"
        GENE_TSS_GTF_URL="https://ftp.ensembl.org/pub/release-${GENE_TSS_RELEASE}/gtf/homo_sapiens/Homo_sapiens.${ASSEMBLY}.${GENE_TSS_RELEASE}.gtf.gz"
        fetch "$GENE_TSS_GTF_URL" "$GENE_TSS_GTF" \
            || die "release-matched Ensembl GTF download failed"
    fi
    if [[ ! -s "$GENE_TSS_PATH" || "$GENE_TSS_GTF" -nt "$GENE_TSS_PATH" ]]; then
        log "building release-matched Ensembl gene-level TSS context"
        python3 "${ROOT}/pipeline/build_gene_tss.py" \
          --gtf "$GENE_TSS_GTF" --output "$GENE_TSS_PATH" \
          --assembly "$ASSEMBLY" --release "$GENE_TSS_RELEASE" \
          || die "Ensembl gene TSS preparation failed"
    else
        log "Ensembl gene TSS context present, skip."
    fi
fi

# ============================================================================ #
# 7. GRCh37/hg19 source FASTA + GRCh38 UCSC chain
# ============================================================================ #
if want liftover; then
    log "=== GRCh37/hg19 -> GRCh38 liftover reference bundle ==="
    [[ "$ASSEMBLY" == "GRCh38" ]] || die "liftover target requires reference.assembly: GRCh38"
    [[ -n "$LIFTOVER_CHAIN" ]] || die "liftover.grch37_to_grch38.chain is not configured"
    [[ -n "$LIFTOVER_SOURCE_FASTA" ]] || die "liftover.grch37_to_grch38.source_fasta is not configured"

    if [[ -s "$LIFTOVER_SOURCE_FASTA" ]] && ! bgzf_complete "$LIFTOVER_SOURCE_FASTA"; then
        warn "hg19 source FASTA is an incomplete BGZF stream; removing it so it is rebuilt: $LIFTOVER_SOURCE_FASTA"
        rm -f "$LIFTOVER_SOURCE_FASTA" "${LIFTOVER_SOURCE_FASTA}.fai" "${LIFTOVER_SOURCE_FASTA}.gzi"
    fi
    if [[ ! -s "$LIFTOVER_SOURCE_FASTA" ]]; then
        mkdir -p "$(dirname "$LIFTOVER_SOURCE_FASTA")"
        RAW_HG19="${LIFTOVER_SOURCE_FASTA%.fa.gz}.ucsc.fa.gz"
        UNCOMPRESSED_HG19="${LIFTOVER_SOURCE_FASTA%.gz}"
        fetch \
          "https://hgdownload.soe.ucsc.edu/goldenPath/hg19/bigZips/hg19.fa.gz" \
          "$RAW_HG19" \
          || die "UCSC hg19 FASTA download failed"
        log "retaining hg19 primary contigs and normalizing names to 1..22/X/Y/MT"
        gzip -cd "$RAW_HG19" \
          | awk '
              /^>/ {
                header=$1; name=substr(header,2)
                keep=(name ~ /^chr([1-9]|1[0-9]|2[0-2]|X|Y|M)$/)
                if (keep) {
                  sub(/^chr/, "", name); if (name=="M") name="MT"
                  print ">" name substr($0,length(header)+1)
                }
                next
              }
              keep {print}
            ' > "$UNCOMPRESSED_HG19"
        publish_bgzf "$UNCOMPRESSED_HG19" "$LIFTOVER_SOURCE_FASTA" \
            || { rm -f "$UNCOMPRESSED_HG19"; die "hg19 FASTA could not be published as a complete BGZF file: $LIFTOVER_SOURCE_FASTA"; }
        rm -f "$RAW_HG19"
    else
        log "hg19 source FASTA present, skip."
    fi
    hts samtools faidx "$LIFTOVER_SOURCE_FASTA"

    if [[ -s "$LIFTOVER_CHAIN" ]] && ! gzip -t "$LIFTOVER_CHAIN" 2>/dev/null; then
        warn "hg19-to-GRCh38 chain is a corrupt gzip stream; removing it so it is rebuilt: $LIFTOVER_CHAIN"
        rm -f "$LIFTOVER_CHAIN"
    fi
    if [[ ! -s "$LIFTOVER_CHAIN" ]]; then
        RAW_CHAIN="${LIFTOVER_CHAIN%.gz}.ucsc.chain.gz"
        fetch \
          "https://hgdownload.soe.ucsc.edu/goldenPath/hg19/liftOver/hg19ToHg38.over.chain.gz" \
          "$RAW_CHAIN" \
          || die "UCSC hg19ToHg38 chain download failed"
        log "normalizing UCSC chain contigs to Ensembl 1..22/X/Y/MT"
        gzip -cd "$RAW_CHAIN" \
          | awk 'BEGIN{OFS=" "}
              /^chain / {
                sub(/^chr/, "", $3); sub(/^chr/, "", $8);
                if ($3=="M") $3="MT"; if ($8=="M") $8="MT"
              }
              {print}' \
          | gzip -c > "${LIFTOVER_CHAIN}.tmp" \
          || { rm -f "${LIFTOVER_CHAIN}.tmp"; die "chain normalization failed: $RAW_CHAIN"; }
        # Publish only a verified, non-empty chain: an interrupted pipe left a
        # truncated gzip that a -s test treated as installed forever.
        gzip -t "${LIFTOVER_CHAIN}.tmp" 2>/dev/null \
            || { rm -f "${LIFTOVER_CHAIN}.tmp"; die "normalized chain is not a valid gzip stream"; }
        # Consume the whole stream so gzip cannot fail with SIGPIPE after
        # the first record. Keep pipefail to catch read/decompression errors.
        gzip -cd "${LIFTOVER_CHAIN}.tmp" | awk '/^chain / { found=1 } END { exit !found }' \
            || { rm -f "${LIFTOVER_CHAIN}.tmp"; die "normalized chain contains no chain records"; }
        mv -f "${LIFTOVER_CHAIN}.tmp" "$LIFTOVER_CHAIN"
        rm -f "$RAW_CHAIN"
    else
        log "hg19-to-GRCh38 chain present, skip."
    fi
    # The conversion cache compares these sidecars with the provenance it
    # recorded, so they must be real digests, never an empty file.
    write_sha256_sidecar "$LIFTOVER_SOURCE_FASTA" || die "cannot record the hg19 FASTA checksum: $LIFTOVER_SOURCE_FASTA"
    write_sha256_sidecar "$LIFTOVER_CHAIN" || die "cannot record the chain checksum: $LIFTOVER_CHAIN"
fi

# ============================================================================ #
# dbNSFP status reminder (automatic after the user supplies an academic link).
# ============================================================================ #
if [[ "$SKIP_FINAL_STATUS" != "1" ]]; then
    DBNSFP_ENABLED="$(yaml_get "$CONFIG" plugins.dbNSFP.enabled)"
    DBNSFP_PATH="$(absdir "$(yaml_get "$CONFIG" plugins.dbNSFP.path)")"
    if [[ "$DBNSFP_ENABLED" == "true" && ! -s "$DBNSFP_PATH" ]]; then
        warn "-------------------------------------------------------------------"
        warn "dbNSFP is enabled but not present at: $DBNSFP_PATH"
        warn "dbNSFP (~52 GB) requires one-time academic registration:"
        warn "  1. Register (institutional email) at https://www.dbnsfp.org/download"
        warn "     -> you receive an academic access code after verification."
        warn "  2. Request the current single-file GRCh38 download link."
        warn "  3. Paste that link into the dbNSFP card on Annotation datasets."
        warn "GUIDE-IEI then downloads, resumes, verifies, and installs the"
        warn "published .gz plus its .tbi index and .md5 checksum automatically."
        warn "dbNSFP is required by the diagnostic profile, so annotation will stop"
        warn "until the configured file and tabix index are present."
        warn "-------------------------------------------------------------------"
    fi

    python3 "${ROOT}/pipeline/check_dbnsfp_version.py" --config "$CONFIG" \
        || warn "dbNSFP update check could not be completed"

    log "download_references.sh done. Review WARNs above for sources needing manual fetch."
    log "Still registration-gated: dbNSFP (paste its link in the UI) and PromoterAI."
    log "Optional LoGoFunc: scripts/download_logofunc.sh or use the dataset setup UI."
    log "Run scripts/fetch_clinvar.sh (or the main run script) to get ClinVar."
fi
