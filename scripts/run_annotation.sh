#!/usr/bin/env bash
# =============================================================================
# run_annotation.sh — annotate a single- or multi-sample VCF with VEP + LOFTEE
# container, driven entirely by config/annotation.config.yaml.
#
#   scripts/run_annotation.sh -i sample.vcf.gz -o results/sample.vep.vcf.gz \
#       [-c config/annotation.config.yaml] [--no-clinvar] [--all-variants]
#       [--include-filtered] [--input-assembly GRCh38|GRCh37|auto] [--dry-run]
#
# Steps:
#   0a. Resolve input assembly; liftover GRCh37/hg19 to canonical GRCh38.
#   0b. (default) retain FILTER=PASS or unfiltered (".") records and restrict
#      to the GRCh38 coding+splice BED. Explicit failure labels are excluded.
#      Region filtering is skipped with --all-variants or
#      region.coding_only:false. PASS filtering is skipped only with
#      --include-filtered or run.pass_only:false.
#   1. (optional) fetch latest ClinVar               -> fetch_clinvar.sh
#   2. build VEP argv + bind-mounts from config      -> build_vep_command.py
#   3. run vep inside the container                  -> docker/podman/singularity
#   4. frameshift PTC-based LOFTEE 50-bp correction  -> loftee_ptc_50bp.py
#   5. sample-specific haplotype consequences         -> Haplosaurus
#   6. Clinical-source protein residue/change matching -> clinical_protein_match.py
#   7. exact allele-level ClinGen expert assertions    -> clingen_erepo_annotate.py
#   8. annotation coverage report                     -> annotation_qc.py
#
# Output is an annotated VCF (INFO/CSQ), preserving sample GT/zygosity.
# =============================================================================
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${HERE}/lib.sh"
ROOT="$(cd "${HERE}/.." && pwd)"

CONFIG="${ROOT}/config/annotation.config.yaml"
INPUT=""; OUTPUT=""; DRY=0; DO_CLINVAR=1; ALL_VARIANTS=0; INCLUDE_FILTERED=0
REQUESTED_ASSEMBLY=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        -i|--input)  INPUT="$2"; shift 2 ;;
        -o|--output) OUTPUT="$2"; shift 2 ;;
        -c|--config) CONFIG="$2"; shift 2 ;;
        --no-clinvar) DO_CLINVAR=0; shift ;;
        --all-variants) ALL_VARIANTS=1; shift ;;   # bypass coding-only region restriction (WGS/non-coding)
        --include-filtered) INCLUDE_FILTERED=1; shift ;; # retain non-PASS records (review/debug only)
        --input-assembly) REQUESTED_ASSEMBLY="$2"; shift 2 ;;
        --dry-run)   DRY=1; shift ;;
        -h|--help)   grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) die "unknown arg: $1" ;;
    esac
done
[[ -n "$INPUT"  ]] || die "need -i/--input VCF"
[[ -n "$OUTPUT" ]] || die "need -o/--output VCF"
[[ -f "$INPUT"  ]] || die "input not found: $INPUT"
[[ -f "$CONFIG" ]] || die "config not found: $CONFIG"
# Post-processing (PTC 50-bp, haplotypes, aa-match, ClinGen, QC) is
# VCF-only; a tab run would produce output the rest of the pipeline cannot
# consume. Refuse up front instead of failing midway. The real key is
# output.format; run.format is also checked in case of older configs.
for FORMAT_KEY in output.format run.format; do
    RUN_FORMAT="$(yaml_get "$CONFIG" "$FORMAT_KEY")"
    if [[ -n "$RUN_FORMAT" && "$RUN_FORMAT" != "vcf" ]]; then
        die "${FORMAT_KEY}: ${RUN_FORMAT} is not supported by this runner — output is VCF-only"
    fi
done
INPUT="$(cd "$(dirname "$INPUT")" && pwd)/$(basename "$INPUT")"
# INPUT is reassigned through liftover/normalisation/pre-filter stages; the
# reproducibility manifest at the end records the file the operator supplied.
SOURCE_INPUT="$INPUT"
mkdir -p "$(dirname "$OUTPUT")"
OUTPUT="$(cd "$(dirname "$OUTPUT")" && pwd)/$(basename "$OUTPUT")"
# VEP is invoked with --force_overwrite: identical input and output paths
# would truncate the only source VCF. String equality catches the direct
# case; -ef additionally catches symlinks and hard links to the same file.
if [[ "$INPUT" == "$OUTPUT" || "$INPUT" -ef "$OUTPUT" ]]; then
    die "input and output resolve to the same file: $INPUT — the run would overwrite the source VCF"
fi
DELIVERABLE_SIDECAR="${OUTPUT}.deliverable"
AAMATCH_FINAL="${OUTPUT%.vcf.gz}.aamatch.vcf.gz"
[[ "$OUTPUT" == *.vcf.gz ]] || AAMATCH_FINAL="${OUTPUT%.vcf}.aamatch.vcf"
if [[ "$INPUT" == "$AAMATCH_FINAL" || "$INPUT" -ef "$AAMATCH_FINAL" ]]; then
    die "input collides with the run's .aamatch output: $AAMATCH_FINAL — choose a different output path"
fi
WORKDIR="$(dirname "$OUTPUT")"
INPUT_BASE="$(basename "$INPUT")"; INPUT_BASE="${INPUT_BASE%.gz}"; INPUT_BASE="${INPUT_BASE%.vcf}"

# Shared with UI preflight, but scan every row and the gzip trailer here.
# No compression, indexing, liftover, or filtering may run on unchecked input.
log "validating input VCF structure (complete file)"
python3 "${ROOT}/pipeline/validate_input_vcf.py" --vcf "$INPUT" \
    || die "input VCF validation failed; no annotation was started"

# --- runtime / image from config ---------------------------------------------
RUNTIME="$(yaml_get "$CONFIG" container.runtime)"; RUNTIME="${RUNTIME:-docker}"
IMAGE="$(yaml_get "$CONFIG" container.image)";     IMAGE="${IMAGE:-vep-annotate:latest}"
export RUNTIME IMAGE

# All run-scoped temp files carry the PID (so two runs sharing an input
# basename and output directory cannot interleave through a shared name) and
# are removed on exit (so a failed run cannot leave look-alike files beside
# the deliverable). Preprocessing intermediates (sorted/normalized/prefiltered
# working copies) get the same treatment as post-processing temps; only the
# liftover output keeps a stable name, because it is a provenance-validated
# cache — its creation is serialized by a lock below instead.
RUN_TOKEN="run$$"
POSTPROC_TMPS=()
RUN_SCRATCH=""
cleanup_postproc_tmps() {
    local tmp
    for tmp in ${POSTPROC_TMPS[@]+"${POSTPROC_TMPS[@]}"}; do
        rm -f "$tmp" "${tmp}.gz" "${tmp}.tbi" "${tmp}.csi"
    done
    # This private directory is created by mktemp below, never user supplied.
    [[ -z "$RUN_SCRATCH" ]] || rm -rf -- "$RUN_SCRATCH"
}
trap cleanup_postproc_tmps EXIT

# ============================================================================ #
# 0a. Input assembly. Annotation references and the cohort model stay GRCh38.
#     GRCh37/hg19 conversion occurs BEFORE the GRCh38 coding-region filter.
# ============================================================================ #
if [[ -z "$REQUESTED_ASSEMBLY" ]]; then
    REQUESTED_ASSEMBLY="$(yaml_get "$CONFIG" input.default_assembly)"
    REQUESTED_ASSEMBLY="${REQUESTED_ASSEMBLY:-GRCh38}"
fi
case "$REQUESTED_ASSEMBLY" in
    auto|GRCh37|GRCh38) ;;
    *) die "--input-assembly must be GRCh38, GRCh37, or auto" ;;
esac
RESOLVED_ASSEMBLY="$(python3 "${ROOT}/pipeline/vcf_assembly.py" \
    --vcf "$INPUT" --requested "$REQUESTED_ASSEMBLY")" \
    || die "input assembly validation failed"
log "input assembly: requested=${REQUESTED_ASSEMBLY}, resolved=${RESOLVED_ASSEMBLY}"

if [[ "$DRY" != "1" ]]; then
    log "checking container access to input, references, and work folders"
    ACCESS_ARGS=(--config "$CONFIG" --input "$INPUT" --output "$OUTPUT" --root "$ROOT" --assembly "$RESOLVED_ASSEMBLY")
    [[ "$ALL_VARIANTS" == "1" ]] && ACCESS_ARGS+=(--all-variants)
    python3 "${ROOT}/pipeline/container_access.py" "${ACCESS_ARGS[@]}" \
        || die "container file access check failed; no annotation was started"
    # Avoid macOS's unshared /var/folders scratch paths. Host-side temporary
    # files that later enter a container live beside the verified output dir.
    RUN_SCRATCH="$(mktemp -d "${WORKDIR}/.guide-iei-work.XXXXXX")" \
        || die "cannot create annotation temporary directory"
    export TMPDIR="$RUN_SCRATCH"
fi
# `bcftools sort` spills a whole-genome callset to its temp directory. TMPDIR
# does not reach a containerized bcftools (whose /tmp is a small overlay), so
# every sort names the scratch directory explicitly; hts() bind-mounts it
# (audit M14). The trailing slash puts the temp files inside the directory.
SORT_TMP_ARGS=()
[[ -z "$RUN_SCRATCH" ]] || SORT_TMP_ARGS=(-T "${RUN_SCRATCH}/")

if [[ "$RESOLVED_ASSEMBLY" == "GRCh37" ]]; then
    [[ "$(yaml_get "$CONFIG" liftover.enabled)" != "false" ]] \
        || die "GRCh37 input requires liftover.enabled:true"
    LIFTED_INPUT="${WORKDIR}/${INPUT_BASE}.lifted.GRCh38.vcf.gz"
    LIFTOVER_ARGS=(
        --input "$INPUT" --output "$LIFTED_INPUT" --config "$CONFIG"
    )
    [[ "$DRY" == "1" ]] && LIFTOVER_ARGS+=(--dry-run)
    # The guard is held by the supervisor and the liftover child. It remains
    # locked until the last process exits, including after a supervisor crash.
    python3 "${ROOT}/pipeline/run_liftover_locked.py" --lock "${LIFTED_INPUT}.lock" -- \
        bash "${HERE}/liftover_grch37_to_grch38.sh" "${LIFTOVER_ARGS[@]}" \
        || die "GRCh37->GRCh38 liftover failed"
    if [[ "$DRY" != "1" ]]; then
        INPUT="$LIFTED_INPUT"
        log "annotation input converted to canonical GRCh38: $INPUT"
    fi
fi

# ============================================================================ #
# 0b. Input pre-filtering (DEFAULT): retain explicit FILTER=PASS records and
#    restrict to coding exons + splice sites. Both filters are applied in one
#    bcftools pass so sample genotypes and headers remain intact.
# ============================================================================ #
CODING_ONLY="$(yaml_get "$CONFIG" region.coding_only)"
PASS_ONLY="$(yaml_get "$CONFIG" run.pass_only)"; PASS_ONLY="${PASS_ONLY:-true}"
REGION_BED=""
WGS_MODE=0
if [[ "$ALL_VARIANTS" == "1" ]]; then
    WGS_MODE=1
    log "--all-variants: region restriction OFF (annotating every variant)."
elif [[ "$CODING_ONLY" == "false" ]]; then
    WGS_MODE=1
    log "region.coding_only:false — region restriction OFF (annotating every variant)."
else
    # Which BED: user-supplied custom_bed wins; else the built coding+splice BED.
    CUSTOM_BED="$(yaml_get "$CONFIG" region.custom_bed)"
    if [[ -n "$CUSTOM_BED" ]]; then
        REGION_BED="$CUSTOM_BED"; [[ "$REGION_BED" = /* ]] || REGION_BED="${ROOT}/${REGION_BED}"
        [[ -s "$REGION_BED" ]] || die "region.custom_bed set but not found: $REGION_BED"
        # A user panel legitimately covers only some contigs; it must still be
        # a complete BGZF stream when compressed.
        if [[ "$REGION_BED" == *.gz ]] && ! bgzf_complete "$REGION_BED"; then
            die "region.custom_bed is an incomplete BGZF file (no EOF marker; variants after the truncation point would be silently dropped): $REGION_BED"
        fi
        log "region restriction ON (custom BED: $REGION_BED)"
    else
        REGION_BED="$(yaml_get "$CONFIG" region.bed)"; REGION_BED="${REGION_BED:-references/regions/coding_splice.padded.bed.gz}"
        [[ "$REGION_BED" = /* ]] || REGION_BED="${ROOT}/${REGION_BED}"
        if [[ ! -s "$REGION_BED" ]] || ! bgzf_complete "$REGION_BED"; then
            [[ -s "$REGION_BED" ]] && warn "coding BED is incomplete (missing BGZF EOF marker); rebuilding it: $REGION_BED"
            log "coding BED missing; building it (one-time)."
            [[ "$DRY" == "1" ]] || bash "${HERE}/build_coding_bed.sh" "$CONFIG" --force || die "coding BED build failed"
        fi
        if [[ "$DRY" != "1" ]]; then
            # Refuse to start on a region file that cannot cover the genome:
            # a truncated BGZF passes gzip -t and tabix, and bcftools -R then
            # returns success while omitting every later contig (audit M12).
            bgzf_complete "$REGION_BED" \
                || die "coding BED is incomplete (no BGZF EOF marker): $REGION_BED — rebuild with: bash scripts/build_coding_bed.sh --force"
            [[ -s "$REGION_BED.tbi" || -s "$REGION_BED.csi" ]] \
                || die "coding BED has no tabix index: $REGION_BED — rebuild with: bash scripts/build_coding_bed.sh --force"
            REQUIRED_REGION_CONTIGS="$(yaml_get "$CONFIG" region.required_contigs)"
            REQUIRED_REGION_CONTIGS="${REQUIRED_REGION_CONTIGS:-1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 X Y MT}"
            REGION_CONTIGS="$(hts tabix -l "$REGION_BED" 2>/dev/null || true)"
            MISSING_REGION_CONTIGS=""
            for contig in $REQUIRED_REGION_CONTIGS; do
                printf '%s\n' "$REGION_CONTIGS" | grep -qx -- "$contig" || MISSING_REGION_CONTIGS="${MISSING_REGION_CONTIGS} ${contig}"
            done
            [[ -z "$MISSING_REGION_CONTIGS" ]] \
                || die "coding BED lacks contig(s):${MISSING_REGION_CONTIGS} — annotation would silently skip them; rebuild with: bash scripts/build_coding_bed.sh --force"
        fi
        log "region restriction ON (coding+splice BED: $REGION_BED)"
    fi

fi

# Whole-genome intake always enters VEP through a validated coordinate-sorted
# BGZF + tabix/CSI source. Reuse a valid source index; otherwise create an
# indexed working copy without changing the submitted VCF.
if [[ "$WGS_MODE" == "1" ]]; then
    if [[ "$DRY" == "1" ]]; then
        log "--dry-run: would validate or create a sorted BGZF/tabix WGS working copy"
    elif hts bcftools index -s "$INPUT" >/dev/null 2>&1; then
        log "whole-genome input index validated and reused: $INPUT"
    else
        WGS_INDEXED="${WORKDIR}/${INPUT_BASE}.${RUN_TOKEN}.wgs-indexed.vcf.gz"
        POSTPROC_TMPS+=("$WGS_INDEXED")
        log "whole-genome input is not queryable; creating sorted BGZF working copy"
        hts bcftools sort ${SORT_TMP_ARGS[@]+"${SORT_TMP_ARGS[@]}"} -O z -o "$WGS_INDEXED" "$INPUT" \
            || die "whole-genome BGZF preparation failed"
        hts tabix -p vcf -f "$WGS_INDEXED" 2>/dev/null \
            || hts bcftools index -f -c "$WGS_INDEXED" \
            || die "whole-genome tabix/CSI indexing failed"
        INPUT="$WGS_INDEXED"
        log "whole-genome indexed working copy ready: $INPUT"
    fi
fi

VIEW_ARGS=()
FILTER_LABELS=()
PASS_FILTER_ON=0
FILTER_POLICY_NOTE=""
if [[ "$INCLUDE_FILTERED" == "1" ]]; then
    log "--include-filtered: PASS filter OFF (retaining non-PASS records)."
elif [[ "$PASS_ONLY" == "false" ]]; then
    log "run.pass_only:false — PASS filter OFF (retaining non-PASS records)."
else
    # Site FILTER policy: PASS and "." are both eligible. Per VCFv4.x, "."
    # means site filtering was not applied — treating it as a failure would
    # conflate missing evidence with failure (the popmax principle) and
    # silently empty never-hard-filtered cohort VCFs. Records with explicit
    # failure labels (LowQual, MONOALLELIC, ...) remain excluded. The review
    # workbench parser applies the same policy.
    VIEW_ARGS+=( -f PASS,. )
    FILTER_LABELS+=( "FILTER=PASS-or-unfiltered" )
    PASS_FILTER_ON=1
fi
if [[ -n "$REGION_BED" ]]; then
    VIEW_ARGS+=( -R "$REGION_BED" )
    FILTER_LABELS+=( "coding+splice region" )
fi

if [[ ${#VIEW_ARGS[@]} -gt 0 ]]; then
    INPUT_BASE="$(basename "$INPUT")"; INPUT_BASE="${INPUT_BASE%.gz}"; INPUT_BASE="${INPUT_BASE%.vcf}"
    FILT="${WORKDIR}/${INPUT_BASE}.${RUN_TOKEN}.prefiltered.vcf.gz"
    POSTPROC_TMPS+=("$FILT")
    if [[ "$DRY" != "1" ]]; then
        # Normalize only contig labels when a GRCh38 VCF uses UCSC chr1/chrM
        # names but the Ensembl region BED/cache uses 1/MT (or vice versa).
        # Coordinates, alleles, INFO and sample FORMAT fields are unchanged.
        if [[ -n "$REGION_BED" ]]; then
            CHROM_MAP="${WORKDIR}/${INPUT_BASE}.${RUN_TOKEN}.chr-map.tsv"
            POSTPROC_TMPS+=("$CHROM_MAP")
            python3 "${ROOT}/pipeline/contig_map.py" \
                --vcf "$INPUT" --bed "$REGION_BED" --output "$CHROM_MAP"
            if [[ -s "$CHROM_MAP" ]]; then
                NORMALIZED="${WORKDIR}/${INPUT_BASE}.${RUN_TOKEN}.contigs-normalized.vcf.gz"
                POSTPROC_TMPS+=("$NORMALIZED")
                hts bcftools annotate --rename-chrs "$CHROM_MAP" -O z -o "$NORMALIZED" "$INPUT" \
                    || die "contig-name normalization failed"
                hts tabix -p vcf -f "$NORMALIZED"
                INPUT="$NORMALIZED"
                log "contig labels normalized to match the coding-region BED"
            fi
        fi
        # bcftools view -R needs an indexed input. PASS-only filtering can read
        # a plain or unindexed VCF directly.
        if [[ -n "$REGION_BED" ]] && [[ ! -f "${INPUT}.tbi" && ! -f "${INPUT}.csi" ]]; then
            hts tabix -p vcf -f "$INPUT" 2>/dev/null || {
                # tabix fails on plain-text input AND on unsorted input, and
                # bgzip fixes neither ordering nor double-compression. Mirror
                # the whole-genome path: sort to BGZF, then index with a CSI
                # fallback for long contigs.
                COMPRESSED_INPUT="${WORKDIR}/${INPUT_BASE}.${RUN_TOKEN}.input.sorted.vcf.gz"
                POSTPROC_TMPS+=("$COMPRESSED_INPUT")
                hts bcftools sort ${SORT_TMP_ARGS[@]+"${SORT_TMP_ARGS[@]}"} -O z -o "$COMPRESSED_INPUT" "$INPUT" \
                    || die "region pre-filter input sort/BGZF preparation failed"
                INPUT="$COMPRESSED_INPUT"
                hts tabix -p vcf -f "$INPUT" 2>/dev/null \
                    || hts bcftools index -f -c "$INPUT" \
                    || die "region pre-filter input indexing failed"
            }
        fi
        # Record counts come from the index when one exists (bcftools index
        # -n reads the per-bin totals) and otherwise from a single pass; the
        # FILTER census below is that pass for PASS-filtered runs, so a
        # whole-genome input is no longer decompressed three times before
        # VEP starts (audit M18).
        count_vcf_records() {
            local n=""
            if [[ -f "$1.tbi" || -f "$1.csi" ]]; then
                n="$(hts bcftools index -n "$1" 2>/dev/null | tr -dc '0-9' || true)"
            fi
            [[ -n "$n" ]] || n="$(hts bcftools view -H "$1" | wc -l | tr -d ' ')"
            printf '%s' "$n"
        }
        # FILTER census: identifies a never-hard-filtered callset (no PASS
        # labels at all) so the run can say so, and gives the zero-retained
        # error measured facts instead of a differential to work through.
        NBEFORE=""; NPASS=""; NUNFILTERED=""
        if [[ "$PASS_FILTER_ON" == "1" ]]; then
            FILTER_CENSUS="$(hts bcftools query -f '%FILTER\n' "$INPUT" 2>/dev/null | awk '
                { total++ }
                $0 == "PASS" { pass++ }
                $0 == "." { unfiltered++ }
                END { printf "%d %d %d", total+0, pass+0, unfiltered+0 }')" || FILTER_CENSUS=""
            [[ -z "$FILTER_CENSUS" ]] || read -r NBEFORE NPASS NUNFILTERED <<<"$FILTER_CENSUS"
            if [[ "${NPASS:-0}" -eq 0 && "${NUNFILTERED:-0}" -gt 0 ]]; then
                NEXCLUDED=$(( NBEFORE - NUNFILTERED ))
                FILTER_POLICY_NOTE="No record in this VCF carries FILTER=PASS: upstream site filtering was not applied. ${NUNFILTERED} unfiltered ('.') records were retained; ${NEXCLUDED} records with explicit failure labels were excluded. Weigh per-variant call quality during review."
                warn "$FILTER_POLICY_NOTE"
            fi
        fi
        [[ -n "$NBEFORE" ]] || NBEFORE="$(count_vcf_records "$INPUT")"
        hts bcftools view "${VIEW_ARGS[@]}" -O z -o "$FILT" "$INPUT" \
            || die "input pre-filter failed"
        hts tabix -p vcf -f "$FILT" 2>/dev/null || true
        NAFTER="$(count_vcf_records "$FILT")"
        log "input pre-filter (${FILTER_LABELS[*]}): ${NBEFORE} -> ${NAFTER} variants"
        if [[ "$NAFTER" -eq 0 ]]; then
            if [[ "$PASS_FILTER_ON" == "1" && "${NPASS:-0}" -eq 0 && "${NUNFILTERED:-0}" -eq 0 ]]; then
                die "input pre-filter retained 0 of ${NBEFORE} variants: every record carries an explicit FILTER failure label (no PASS and no '.'). The callset failed upstream site filtering; rerun with --include-filtered to inspect it deliberately."
            fi
            VCF_CONTIGS="$( { hts bcftools view -H "$INPUT" 2>/dev/null | awk -F'\t' 'NR<=200{print $1} NR==200{exit}' | sort -u | awk 'NR<=3' | paste -sd ',' -; } || true)"
            BED_COUNT="$( { gunzip -c "$REGION_BED" 2>/dev/null | wc -l | tr -d ' '; } || echo unreadable)"
            BED_CONTIGS="$( { gunzip -c "$REGION_BED" 2>/dev/null | awk -F'\t' 'NR<=200{print $1} NR==200{exit}' | sort -u | awk 'NR<=3' | paste -sd ',' -; } || true)"
            die "input pre-filter retained 0 of ${NBEFORE} variants. Measured: ${NPASS:-n/a} PASS + ${NUNFILTERED:-n/a} unfiltered records satisfy the FILTER criterion, yet none overlap the region BED. VCF contigs begin: ${VCF_CONTIGS:-unreadable}; BED contigs begin: ${BED_CONTIGS:-unreadable}; BED intervals: ${BED_COUNT}. This isolates a contig-naming, assembly, or region-BED problem (including an unreadable/empty BED on a disconnected drive) — verify the input assembly and region.bed, or rerun with --all-variants to inspect."
        fi
        INPUT="$FILT"
    else
        log "--dry-run: would pre-filter $INPUT with bcftools view ${VIEW_ARGS[*]}"
    fi
else
    # A machine-readable count when it costs nothing (an index is present):
    # the workbench progress bar needs a denominator for these runs too.
    OFF_COUNT=""
    if [[ -f "${INPUT}.tbi" || -f "${INPUT}.csi" ]]; then
        OFF_COUNT="$(hts bcftools index -n "$INPUT" 2>/dev/null | tr -dc '0-9' || true)"
    fi
    if [[ -n "$OFF_COUNT" ]]; then
        log "input pre-filter OFF: ${OFF_COUNT} variants to annotate (every record in the input VCF)."
    else
        log "input pre-filter OFF: annotating every record in the input VCF."
    fi
fi

# ============================================================================ #
# 1. ClinVar auto-fetch
# ============================================================================ #
CLINVAR_RELEASE="NA"
CLINVAR_VCF=""
if [[ "$DO_CLINVAR" == "1" && "$DRY" == "1" ]]; then
    # A dry run must not mutate reference state: the fetch replaces the
    # installed ClinVar before the dry-run exit was ever reached.
    log "--dry-run: would fetch the latest ClinVar release"
    DO_CLINVAR=0
fi
if [[ "$DO_CLINVAR" == "1" ]] && [[ "$(yaml_get "$CONFIG" clinvar.auto_fetch)" != "false" ]]; then
    log "=== fetching latest ClinVar ==="
    CV_OUT="$(bash "${HERE}/fetch_clinvar.sh" "$CONFIG")" || die "ClinVar fetch failed"
    echo "$CV_OUT" | grep -E '^CLINVAR_(VCF|RELEASE)=' || true
    CLINVAR_RELEASE="$(echo "$CV_OUT" | sed -n 's/^CLINVAR_RELEASE=//p' | tail -1)"
    CLINVAR_RELEASE="${CLINVAR_RELEASE:-NA}"
    CLINVAR_VCF="$(echo "$CV_OUT" | sed -n 's/^CLINVAR_VCF=//p' | tail -1)"
else
    log "ClinVar auto-fetch disabled (--no-clinvar or config)."
fi

# --- build the pathogenic-missense residue reference (for aa-match) ----------
# Rebuild when the source content or VEP cache changes.  ClinVar keeps its
# historic variable/name for output compatibility; ClinGen and GenIA use the
# same provenance-preserving catalog contract below.
# Fingerprint of the configured P/LP label set (pathogenic_terms). Part of
# every catalog stamp so a policy edit rebuilds the catalogs; "unknown" when
# the configuration cannot be read, which always forces a rebuild.
clinical_protein_labels_fingerprint() {
    local line
    line="$(python3 "${ROOT}/pipeline/prepare_clinical_protein_catalog.py" labels \
        --config "$CONFIG" 2>/dev/null | cut -f1)"
    echo "${line:-unknown}"
}

AA_REF=""
CLINGEN_AA_REF=""
GENIA_AA_REF=""
PROTEIN_MATCH_ENABLED="$(yaml_get "$CONFIG" post_processing.clinical_protein_match.enabled)"
[[ -n "$PROTEIN_MATCH_ENABLED" ]] || \
    PROTEIN_MATCH_ENABLED="$(yaml_get "$CONFIG" post_processing.clinvar_aa_match.enabled)"
PROTEIN_MATCH_ENABLED="${PROTEIN_MATCH_ENABLED:-true}"
CLINVAR_MATCH_REQUESTED="true"
if [[ "$(yaml_get "$CONFIG" post_processing.clinical_protein_match.clinvar)" == "false" ]] \
   || [[ "$(yaml_get "$CONFIG" post_processing.clinvar_aa_match.enabled)" == "false" ]]; then
    CLINVAR_MATCH_REQUESTED="false"
fi
if [[ "$DRY" != "1" ]] \
   && [[ "$PROTEIN_MATCH_ENABLED" != "false" ]] \
   && [[ "$CLINVAR_MATCH_REQUESTED" == "true" ]] \
   && [[ -n "$CLINVAR_VCF" && -f "$CLINVAR_VCF" ]]; then
    DEST_DIR="$(yaml_get "$CONFIG" clinvar.dest_dir)"; DEST_DIR="${DEST_DIR:-references/clinvar}"
    [[ "$DEST_DIR" = /* ]] || DEST_DIR="${ROOT}/${DEST_DIR}"
    AA_REF="$(yaml_get "$CONFIG" clinvar.protein_match_catalog)"
    AA_REF="${AA_REF:-${DEST_DIR}/clinvar_aa_reference.tsv}"
    [[ "$AA_REF" = /* ]] || AA_REF="${ROOT}/${AA_REF}"
    STAMP="${DEST_DIR}/.aa_reference.release"
    NEED_BUILD=1
    # The stamp carries a format tag: catalogs built before the
    # reference-residue (aa4) or transcript (aa5) columns existed must
    # rebuild once even though the ClinVar release is unchanged — otherwise
    # the newer matching guards stay silently inactive until the next
    # release.
    AA_REF_FORMAT="aa9-provenance-v1-pick-allele-gene"
    # Bind the stamp to the ClinVar CONTENT, not just its release string: a
    # replaced or damaged ClinVar VCF under an unchanged release name must
    # trigger a rebuild (the same rule the ClinGen updater applies).
    CLINVAR_SHA="$(sha256_file "$CLINVAR_VCF")" || die "cannot hash the ClinVar VCF for the protein-match stamp: $CLINVAR_VCF"
    # ...and to the VEP cache the catalog's transcript column was picked
    # from: upgrading the cache without rebuilding the catalog would let
    # the transcript gate silently zero every match against retired
    # accessions. The versioned cache directory name identifies the cache.
    AA_VEP_CACHE_DIR="$(yaml_get "$CONFIG" reference.vep_cache_dir)"
    [[ "$AA_VEP_CACHE_DIR" = /* ]] || AA_VEP_CACHE_DIR="${ROOT}/${AA_VEP_CACHE_DIR}"
    VEP_CACHE_TAG="$(vep_cache_tag "$AA_VEP_CACHE_DIR")" \
        || die "cannot identify the VEP cache for the ClinVar protein-match catalog"
    # ...and to the configured pathogenic label set: editing pathogenic_terms
    # changes which source records the catalog admits, so it must rebuild.
    AA_LABELS_TAG="labels-$(clinical_protein_labels_fingerprint)"
    AA_STAMP_LINE="$CLINVAR_RELEASE $AA_REF_FORMAT $CLINVAR_SHA $VEP_CACHE_TAG $AA_LABELS_TAG"
    if [[ -s "$AA_REF" && -f "$STAMP" && "$(cat "$STAMP" 2>/dev/null)" == "$AA_STAMP_LINE" ]]; then
        NEED_BUILD=0
        log "aa-match reference up to date (release $CLINVAR_RELEASE), skip rebuild."
    fi
    AA_MATCH_RELEASE="$CLINVAR_RELEASE"
    if [[ "$NEED_BUILD" == "1" ]]; then
        log "=== building ClinVar clinical protein-match catalog ==="
        if bash "${HERE}/build_clinical_protein_catalog.sh" \
            "$CONFIG" clinvar "$CLINVAR_VCF" "$AA_REF"; then
            echo "$AA_STAMP_LINE" > "$STAMP"
        else
            # The matcher will run against the previous catalog; its output
            # must be labeled with THAT release, not the current one.
            # The stamp line is "release format sha cachetag"; only the
            # release belongs in the evidence label.
            AA_MATCH_RELEASE="$(cut -d' ' -f1 "$STAMP" 2>/dev/null || true)"
            AA_MATCH_RELEASE="${AA_MATCH_RELEASE:-unknown}"
            warn "aa-match reference rebuild failed; using the previous catalog (release ${AA_MATCH_RELEASE}) and labeling the evidence accordingly."
        fi
    fi
fi

# Build the same catalog shape for ClinGen and the optional GenIA variant
# component.  A content hash plus VEP-cache tag makes this lazy: ordinary jobs
# pay only two small hash checks, while a newly installed/refreshed source is
# annotated once.  Catalog publication is atomic inside the builder, so a
# failed refresh can safely retain the previous auditable snapshot.
ensure_clinical_protein_catalog() {
    local source_type="$1" source_path="$2" catalog_path="$3" label="$4"
    local stamp_path="${catalog_path}.stamp" source_sha cache_dir cache_tag expected
    CATALOG_SOURCE_SHA=""
    [[ -s "$source_path" ]] || return 1
    source_sha="$(sha256_file "$source_path")" || die "cannot hash the $label source for the catalog stamp: $source_path"
    cache_dir="$(yaml_get "$CONFIG" reference.vep_cache_dir)"
    [[ "$cache_dir" = /* ]] || cache_dir="${ROOT}/${cache_dir}"
    # The stamp binds the catalog to the versioned VEP cache directory. A
    # cache without one is not a state to stamp as "unknown" and continue
    # from: the annotation cannot run without it.
    cache_tag="$(vep_cache_tag "$cache_dir")" \
        || die "cannot identify the VEP cache for the $label protein-match catalog"
    expected="${source_sha} aa9-provenance-v1-pick-allele-gene ${cache_tag} labels-$(clinical_protein_labels_fingerprint)"
    if [[ -s "$catalog_path" && -f "$stamp_path" ]] \
       && [[ "$(cat "$stamp_path" 2>/dev/null)" == "$expected" ]]; then
        CATALOG_SOURCE_SHA="$source_sha"
        log "$label protein-match catalog is current; skip rebuild."
        return 0
    fi
    log "=== building $label clinical protein-match catalog ==="
    if bash "${HERE}/build_clinical_protein_catalog.sh" \
        "$CONFIG" "$source_type" "$source_path" "$catalog_path"; then
        echo "$expected" > "$stamp_path"
        CATALOG_SOURCE_SHA="$source_sha"
        return 0
    fi
    if [[ -s "$catalog_path" ]]; then
        CATALOG_SOURCE_SHA="$(cut -d' ' -f1 "$stamp_path" 2>/dev/null || true)"
        CATALOG_SOURCE_SHA="${CATALOG_SOURCE_SHA:-unknown}"
        warn "$label protein-match catalog refresh failed; using the previous catalog."
        return 0
    fi
    warn "$label protein-match catalog is unavailable; exact-allele annotation remains enabled."
    return 1
}

CLINGEN_AA_RELEASE="unknown"
GENIA_AA_RELEASE="unknown"
if [[ "$DRY" != "1" && "$PROTEIN_MATCH_ENABLED" != "false" ]]; then
    if [[ "$(yaml_get "$CONFIG" post_processing.clinical_protein_match.clingen)" != "false" ]] \
       && [[ "$(yaml_get "$CONFIG" clingen_erepo.enabled)" == "true" ]]; then
        CLINGEN_DB_FOR_AA="$(yaml_get "$CONFIG" clingen_erepo.database)"
        [[ -z "$CLINGEN_DB_FOR_AA" || "$CLINGEN_DB_FOR_AA" = /* ]] || \
            CLINGEN_DB_FOR_AA="${ROOT}/${CLINGEN_DB_FOR_AA}"
        CLINGEN_AA_REF="$(yaml_get "$CONFIG" clingen_erepo.protein_match_catalog)"
        CLINGEN_AA_REF="${CLINGEN_AA_REF:-references/clingen_erepo/clingen_aa_reference.tsv}"
        [[ "$CLINGEN_AA_REF" = /* ]] || CLINGEN_AA_REF="${ROOT}/${CLINGEN_AA_REF}"
        if ensure_clinical_protein_catalog clingen "$CLINGEN_DB_FOR_AA" \
            "$CLINGEN_AA_REF" ClinGen; then
            CLINGEN_AA_RELEASE="${CATALOG_SOURCE_SHA:0:12}"
        else
            CLINGEN_AA_REF=""
        fi
    fi

    if [[ "$(yaml_get "$CONFIG" post_processing.clinical_protein_match.genia)" != "false" ]] \
       && [[ "$(yaml_get "$CONFIG" genia.enabled)" == "true" ]]; then
        GENIA_DB_FOR_AA="$(yaml_get "$CONFIG" genia.database)"
        [[ -z "$GENIA_DB_FOR_AA" || "$GENIA_DB_FOR_AA" = /* ]] || \
            GENIA_DB_FOR_AA="${ROOT}/${GENIA_DB_FOR_AA}"
        GENIA_AA_REF="$(yaml_get "$CONFIG" genia.protein_match_catalog)"
        GENIA_AA_REF="${GENIA_AA_REF:-references/genia/genia_aa_reference.tsv}"
        [[ "$GENIA_AA_REF" = /* ]] || GENIA_AA_REF="${ROOT}/${GENIA_AA_REF}"
        # Gene/disease-only GenIA installations are valid but cannot support
        # protein matching. Do not emit all-zero fields that imply evaluation.
        if [[ -s "$GENIA_DB_FOR_AA" ]] && python3 - "$GENIA_DB_FOR_AA" <<'PY'
import sqlite3,sys
try:
    db=sqlite3.connect(f"file:{sys.argv[1]}?mode=ro&immutable=1", uri=True)
    row=db.execute("SELECT 1 FROM components WHERE id='variant_vcf'").fetchone()
    db.close()
except sqlite3.Error:
    raise SystemExit(1)
raise SystemExit(0 if row else 1)
PY
        then
            if ensure_clinical_protein_catalog genia "$GENIA_DB_FOR_AA" \
                "$GENIA_AA_REF" GenIA; then
                GENIA_AA_RELEASE="${CATALOG_SOURCE_SHA:0:12}"
            else
                GENIA_AA_REF=""
            fi
        else
            GENIA_AA_REF=""
            log "GenIA variant component not installed; protein matching not evaluated."
        fi
    fi
fi

# ============================================================================ #
# 2. Build VEP argv + mounts (JSON) from the config
# ============================================================================ #
log "=== building VEP command from config ==="
# VEP writes to a run-scoped temporary name beside the deliverable; the file
# is promoted to $OUTPUT only after validate_vep_output.py accepts it. A run
# that is killed or fails validation therefore never leaves a partial,
# look-alike VCF under the deliverable's name (audit M16). VEP derives its
# _summary.html/_warnings.txt sidecars from the output name, so they are
# renamed alongside.
case "$OUTPUT" in
    *.vcf.gz) VEP_TMP="${OUTPUT%.vcf.gz}.${RUN_TOKEN}.vep-tmp.vcf.gz" ;;
    *.vcf)    VEP_TMP="${OUTPUT%.vcf}.${RUN_TOKEN}.vep-tmp.vcf" ;;
    *)        VEP_TMP="${OUTPUT}.${RUN_TOKEN}.vep-tmp" ;;
esac
POSTPROC_TMPS+=("$VEP_TMP" "${VEP_TMP}_summary.html" "${VEP_TMP}_warnings.txt")
# --verify-integrity: hash every indexed-score dataset against its manifest
# now, before the multi-hour VEP run, rather than trusting the fast
# name/size/timestamp check the dataset screen polls with.
PLAN_JSON="$(python3 "${ROOT}/pipeline/build_vep_command.py" \
    --config "$CONFIG" --input "$INPUT" --output "$VEP_TMP" --json \
    --verify-integrity)" \
    || die "some REQUIRED reference file is missing (see WARN/ERROR above)"

# Parse the JSON with python into shell-friendly lines.
eval "$(python3 - "$PLAN_JSON" <<'PY'
import json, sys, shlex
plan = json.loads(sys.argv[1])
# argv as a bash array
print("VEP_ARGV=(" + " ".join(shlex.quote(a) for a in plan["argv"]) + ")")
# mounts as parallel arrays
hosts = [m["host"] for m in plan["mounts"]]
conts = [m["container"] for m in plan["mounts"]]
modes = [m["mode"] for m in plan["mounts"]]
print("M_HOST=(" + " ".join(shlex.quote(h) for h in hosts) + ")")
print("M_CONT=(" + " ".join(shlex.quote(c) for c in conts) + ")")
print("M_MODE=(" + " ".join(shlex.quote(m) for m in modes) + ")")
PY
)"

# ============================================================================ #
# 3. Assemble + run the container command
# ============================================================================ #
build_mount_flags() {
    # echoes runtime-appropriate mount flags for docker/podman vs singularity
    local i
    for i in "${!M_HOST[@]}"; do
        local h="${M_HOST[$i]}" c="${M_CONT[$i]}" m="${M_MODE[$i]}"
        case "$RUNTIME" in
            docker|podman)
                printf -- '-v\n%s:%s:%s\n' "$h" "$c" "$m" ;;
            singularity|apptainer)
                # singularity uses --bind host:container[:ro]; rw is default
                if [[ "$m" == "ro" ]]; then printf -- '--bind\n%s:%s:ro\n' "$h" "$c"
                else printf -- '--bind\n%s:%s\n' "$h" "$c"; fi ;;
        esac
    done
    # extra_mounts from config (host:container strings)
    python3 - "$CONFIG" "$RUNTIME" <<'PY'
import sys, yaml
cfg = yaml.safe_load(open(sys.argv[1])); rt = sys.argv[2]
for spec in (cfg.get("container", {}).get("extra_mounts") or []):
    if rt in ("docker", "podman"):
        print("-v"); print(spec)
    else:
        print("--bind"); print(spec)
PY
}

# Collect mount flags into an array (portable; avoids bash4 mapfile).
MOUNT_FLAGS=()
while IFS= read -r _line; do
    MOUNT_FLAGS+=("$_line")
done < <(build_mount_flags)

# The LoF plugin string contains a literal $LOFTEE_DIR that must expand INSIDE
# the container, so we run the argv through `sh -c` in the container.
# POSIX-safe single quoting: each embedded ' becomes '\'' — built via a
# variable to sidestep bash's replacement-escaping rules, which previously
# produced an unparseable string for any argument containing an apostrophe.
shquote_arg() {
    local q="'"
    printf "'%s'" "${1//$q/$q\\$q$q}"
}
VEP_CMD_STR=""
for a in "${VEP_ARGV[@]}"; do
    # single-quote-safe, but keep $LOFTEE_DIR unquoted so the container shell expands it
    if [[ "$a" == *'$LOFTEE_DIR'* ]]; then
        # split around $LOFTEE_DIR and quote the rest
        pre="${a%%\$LOFTEE_DIR*}"; post="${a#*\$LOFTEE_DIR}"
        VEP_CMD_STR+=" $(shquote_arg "$pre")\"\$LOFTEE_DIR\"$(shquote_arg "$post")"
    else
        VEP_CMD_STR+=" $(shquote_arg "$a")"
    fi
done

case "$RUNTIME" in
    docker|podman)
        FULL=( "$RUNTIME" run --rm --pull=never --network=none --ulimit core=0:0 "${MOUNT_FLAGS[@]}" --entrypoint sh "$IMAGE" -c "$VEP_CMD_STR" ) ;;
    singularity|apptainer)
        FULL=( "$RUNTIME" exec "${MOUNT_FLAGS[@]}" "$IMAGE" sh -c "$VEP_CMD_STR" ) ;;
    *) die "unsupported runtime: $RUNTIME" ;;
esac

log "=== VEP invocation ==="
{
    echo "  runtime : $RUNTIME"
    echo "  image   : $IMAGE"
    echo "  mounts  :"
    i=0
    while [[ $i -lt ${#M_HOST[@]} ]]; do
        printf '    %-3s %s -> %s\n' "${M_MODE[$i]}" "${M_HOST[$i]}" "${M_CONT[$i]}"
        i=$((i+1))
    done
    echo "  vep cmd (as run by in-container sh -c):"
    echo "    ${VEP_CMD_STR# }" | sed 's/ --/ \\\n      --/g'
} >&2

if [[ "$DRY" == "1" ]]; then
    log "--dry-run: not executing. Command shown above."
    exit 0
fi

# VEP only creates the warnings sidecar when warnings occur. Remove sidecars
# from a prior failed/forced run so they cannot be mistaken for this run's.
rm -f "${OUTPUT}_warnings.txt" "${VEP_TMP}_warnings.txt" "${VEP_TMP}_summary.html"
# The workbench progress tracker follows this line to count records as
# they are written (job_progress.py); keep its wording.
log "VEP writing to $VEP_TMP"
"${FULL[@]}" || die "VEP run failed"
[[ -s "$VEP_TMP" ]] || die "VEP reported success but wrote no output: $VEP_TMP"
log "VEP finished -> $VEP_TMP (validating before promotion)"
python3 "${ROOT}/pipeline/validate_vep_output.py" \
    --config "$CONFIG" --vcf "$VEP_TMP" \
    || die "required annotation validation failed; the unvalidated VEP output was not promoted to $OUTPUT"
# Point of no return: the deliverable is about to be (re)written, so a
# previous run's deliverable sidecar is now stale. Until this moment a killed
# run or a validation failure leaves the previous result and its pointer
# intact.
rm -f "$DELIVERABLE_SIDECAR"
mv -f "$VEP_TMP" "$OUTPUT" || die "could not promote validated VEP output to $OUTPUT"
[[ -e "${VEP_TMP}_summary.html" ]] && mv -f "${VEP_TMP}_summary.html" "${OUTPUT}_summary.html"
[[ -e "${VEP_TMP}_warnings.txt" ]] && mv -f "${VEP_TMP}_warnings.txt" "${OUTPUT}_warnings.txt"
log "validated VEP output -> $OUTPUT"

# ============================================================================ #
# 4. Recompute LOFTEE's frameshift 50-bp rule at the resulting PTC.
#    The corrected value replaces 50_BP_RULE inside CSQ/LoF_info; the original
#    verdict and calculation provenance remain in appended CSQ fields.
# ============================================================================ #
if [[ "$(yaml_get "$CONFIG" post_processing.loftee_ptc_50bp.enabled)" == "true" ]]; then
    log "=== LOFTEE frameshift PTC 50-bp recomputation ==="
    PTC_TMP="${OUTPUT%.gz}.ptc50.$$.tmp"
    POSTPROC_TMPS+=("$PTC_TMP")
    PTC_AUDIT="${OUTPUT}.loftee_ptc50.audit.json"
    if python3 "${ROOT}/pipeline/loftee_ptc_50bp.py" \
        --config "$CONFIG" --input "$OUTPUT" --output "$PTC_TMP" \
        --reported-output "$OUTPUT" --audit-json "$PTC_AUDIT"; then
        if [[ "$OUTPUT" == *.gz ]]; then
            publish_bgzf "$PTC_TMP" "$OUTPUT" vcf || die "PTC 50-bp indexed publication failed"
        else
            mv "$PTC_TMP" "$OUTPUT"
        fi
        log "PTC-based 50-bp rule applied in place -> $OUTPUT"
    elif [[ "$(yaml_get "$CONFIG" post_processing.loftee_ptc_50bp.required)" == "true" ]]; then
        die "required LOFTEE PTC 50-bp recomputation failed"
    else
        warn "optional LOFTEE PTC 50-bp recomputation failed; retaining original LOFTEE rule"
    fi
fi

# ============================================================================ #
# 5. Sample-specific Haplosaurus consequences and frame-restoration evidence.
#    Haplosaurus proposes multi-variant protein haplotypes; the Python
#    postprocessor independently checks GT/PS before calling restoration
#    confirmed. Unphased heterozygous combinations remain explicitly possible.
# ============================================================================ #
if [[ "$(yaml_get "$CONFIG" post_processing.haplotype_consequences.enabled)" == "true" ]]; then
    log "=== sample-specific Haplosaurus post-processing ==="
    HAPLO_STEM="${OUTPUT%.vcf.gz}"
    [[ "$HAPLO_STEM" == "$OUTPUT" ]] && HAPLO_STEM="${OUTPUT%.vcf}"
    HAPLO_SELECTED="${HAPLO_STEM}.haplo.selected.vcf.gz"
    HAPLO_CANDIDATES="${HAPLO_STEM}.haplo.candidates.vcf.gz"
    HAPLO_JSON="${HAPLO_STEM}.haplo.raw.json"
    HAPLO_TMP="${HAPLO_STEM}.haplo.$$.tmp"
    # The candidate/selection working files are removed explicitly on success
    # below; registering them too means a failed or optional-skipped
    # Haplosaurus pass cannot leave look-alike .haplo.*.vcf.gz files beside
    # the deliverable (audit M13).
    POSTPROC_TMPS+=("$HAPLO_TMP" "$HAPLO_SELECTED" "$HAPLO_CANDIDATES" "${HAPLO_CANDIDATES}.id.vcf.gz" "$HAPLO_JSON")
    HAPLO_AUDIT="${OUTPUT}.haplotype.audit.json"
    HAPLO_OK=1

    # Record-wide pre-selection: keeps every record with a frameshift
    # consequence on ANY allele, and the split below then hands each ALT —
    # including in-frame or substitution siblings — to Haplosaurus. The
    # postprocessor re-checks per allele (ALLELE_NUM) that at least two
    # contributors are themselves frameshifting on the haplotype's transcript
    # before any restoration status is written.
    hts bcftools view -i 'INFO/CSQ~"frameshift_variant"' -O z \
        -o "$HAPLO_SELECTED" "$OUTPUT" || HAPLO_OK=0
    if [[ "$HAPLO_OK" == "1" ]]; then
        hts bcftools norm -m -any -O z -o "$HAPLO_CANDIDATES" \
            "$HAPLO_SELECTED" || HAPLO_OK=0
    fi
    if [[ "$HAPLO_OK" == "1" ]]; then
        hts bcftools annotate -x INFO --set-id '%CHROM:%POS:%REF:%FIRST_ALT' \
            -O z -o "${HAPLO_CANDIDATES}.id.vcf.gz" "$HAPLO_CANDIDATES" \
            || HAPLO_OK=0
    fi
    if [[ "$HAPLO_OK" == "1" ]]; then
        mv "${HAPLO_CANDIDATES}.id.vcf.gz" "$HAPLO_CANDIDATES"
        hts tabix -p vcf -f "$HAPLO_CANDIDATES" || HAPLO_OK=0
    fi

    HAPLO_COUNT=0
    if [[ "$HAPLO_OK" == "1" ]]; then
        HAPLO_COUNT="$(hts bcftools view -H "$HAPLO_CANDIDATES" | wc -l | tr -d ' ')"
    fi
    if [[ "$HAPLO_OK" == "1" && "$HAPLO_COUNT" -gt 0 ]]; then
        CACHE_DIR="$(yaml_get "$CONFIG" reference.vep_cache_dir)"
        CACHE_DIR="${CACHE_DIR:-references/vep_cache}"
        [[ "$CACHE_DIR" = /* ]] || CACHE_DIR="${ROOT}/${CACHE_DIR}"
        FASTA_PATH="$(yaml_get "$CONFIG" reference.fasta.path)"
        [[ "$FASTA_PATH" = /* ]] || FASTA_PATH="${ROOT}/${FASTA_PATH}"
        FASTA_DIR="$(dirname "$FASTA_PATH")"
        FASTA_NAME="$(basename "$FASTA_PATH")"
        CANDIDATE_DIR="$(dirname "$HAPLO_CANDIDATES")"
        CANDIDATE_NAME="$(basename "$HAPLO_CANDIDATES")"
        HAPLO_JSON_NAME="$(basename "$HAPLO_JSON")"
        SPECIES="$(yaml_get "$CONFIG" reference.species)"
        SPECIES="${SPECIES:-homo_sapiens}"
        ASSEMBLY="$(yaml_get "$CONFIG" reference.assembly)"
        ASSEMBLY="${ASSEMBLY:-GRCh38}"
        case "$RUNTIME" in
            docker|podman)
                # PERLIO=:unix (unbuffered raw IO): with many-sample cohorts,
                # haplo segfaults inside Perl's buffered-IO layer, truncating
                # its JSON mid-write and losing containers silently. Unbuffered
                # IO avoids the crashing layer entirely; verified empirically
                # (exit 139 + truncated output -> exit 0 + complete output).
                "$RUNTIME" run --rm --pull=never --network=none --ulimit core=0:0 \
                    -e PERLIO=:unix \
                    -v "${CANDIDATE_DIR}:/work:rw" \
                    -v "${CACHE_DIR}:/cache:rw" \
                    -v "${FASTA_DIR}:/fasta:ro" \
                    --entrypoint haplo "$IMAGE" \
                    --input_file "/work/${CANDIDATE_NAME}" \
                    --output_file "/work/${HAPLO_JSON_NAME}" \
                    --offline --cache --dir_cache /cache \
                    --species "$SPECIES" --assembly "$ASSEMBLY" \
                    --fasta "/fasta/${FASTA_NAME}" --json --force_overwrite \
                    || HAPLO_OK=0
                ;;
            singularity|apptainer)
                "$RUNTIME" exec \
                    --env PERLIO=:unix \
                    --bind "${CANDIDATE_DIR}:/work" \
                    --bind "${CACHE_DIR}:/cache" \
                    --bind "${FASTA_DIR}:/fasta:ro" \
                    "$IMAGE" haplo \
                    --input_file "/work/${CANDIDATE_NAME}" \
                    --output_file "/work/${HAPLO_JSON_NAME}" \
                    --offline --cache --dir_cache /cache \
                    --species "$SPECIES" --assembly "$ASSEMBLY" \
                    --fasta "/fasta/${FASTA_NAME}" --json --force_overwrite \
                    || HAPLO_OK=0
                ;;
        esac
    elif [[ "$HAPLO_OK" == "1" ]]; then
        : > "$HAPLO_JSON"
        log "no frameshift candidates; recording an empty haplotype evidence set"
    fi

    if [[ "$HAPLO_OK" == "1" ]] && python3 "${ROOT}/pipeline/haplotype_consequences.py" \
        --input "$OUTPUT" --candidate-vcf "$HAPLO_CANDIDATES" \
        --haplosaurus-json "$HAPLO_JSON" --output "$HAPLO_TMP" \
        --audit-json "$HAPLO_AUDIT"; then
        if [[ "$OUTPUT" == *.gz ]]; then
            publish_bgzf "$HAPLO_TMP" "$OUTPUT" vcf || die "haplotype indexed publication failed"
        else
            mv "$HAPLO_TMP" "$OUTPUT"
        fi
        rm -f "$HAPLO_SELECTED" "${HAPLO_SELECTED}.tbi" \
            "$HAPLO_CANDIDATES" "${HAPLO_CANDIDATES}.tbi" "$HAPLO_JSON"
        log "sample-specific haplotype evidence applied -> $OUTPUT"
    elif [[ "$(yaml_get "$CONFIG" post_processing.haplotype_consequences.required)" == "true" ]]; then
        die "required sample-specific Haplosaurus post-processing failed"
    else
        warn "optional Haplosaurus post-processing failed; retaining per-variant consequences"
    fi
fi

# ============================================================================ #
# 6. Clinical-source amino-acid-match post-processing
# ============================================================================ #
if [[ "$PROTEIN_MATCH_ENABLED" != "false" ]]; then
    log "=== clinical protein residue/change post-processing ==="
    FINAL="${OUTPUT%.vcf.gz}.aamatch.vcf.gz"
    [[ "$OUTPUT" == *.vcf.gz ]] || FINAL="${OUTPUT%.vcf}.aamatch.vcf"
    MATCH_INPUT="$OUTPUT"
    MATCH_STEP=0

    apply_protein_match() {
        local source_label="$1" info_key="$2" reference="$3" release="$4" allow_missing="$5"
        local match_output="${OUTPUT%.gz}.protein-match-${MATCH_STEP}.$$.tmp"
        local match_args=(
            --input "$MATCH_INPUT" --output "$match_output"
            --reference "$reference" --info-key "$info_key"
            --source-label "$source_label" --clinvar-release "$release"
            --include-details
        )
        [[ "$allow_missing" == "1" ]] && match_args+=(--allow-missing-reference)
        python3 "${ROOT}/pipeline/clinical_protein_match.py" "${match_args[@]}" \
            || die "$source_label protein-match post-processing failed"
        [[ "$MATCH_INPUT" == "$OUTPUT" ]] || rm -f "$MATCH_INPUT"
        MATCH_INPUT="$match_output"
        POSTPROC_TMPS+=("$MATCH_INPUT")
        MATCH_STEP=$((MATCH_STEP + 1))
    }

    # A missing catalog means "not evaluated", represented by absent INFO
    # fields. All-zero flags would falsely look like a completed negative
    # evaluation. Existing catalogs remain usable with --no-clinvar.
    if [[ "$CLINVAR_MATCH_REQUESTED" == "true" ]]; then
        if [[ -z "$AA_REF" ]]; then
            AA_REF="$(yaml_get "$CONFIG" clinvar.protein_match_catalog)"
            AA_REF="${AA_REF:-references/clinvar/clinvar_aa_reference.tsv}"
            [[ "$AA_REF" = /* ]] || AA_REF="${ROOT}/${AA_REF}"
        fi
        if [[ -s "$AA_REF" ]]; then
            if [[ -z "${AA_MATCH_RELEASE:-}" || "${AA_MATCH_RELEASE:-}" == "NA" ]]; then
                AA_MATCH_RELEASE="$(cut -d' ' -f1 "$(dirname "$AA_REF")/.aa_reference.release" 2>/dev/null || true)"
                AA_MATCH_RELEASE="${AA_MATCH_RELEASE:-unknown}"
            fi
            apply_protein_match ClinVar ClinVar_path_aa_match "$AA_REF" \
                "${AA_MATCH_RELEASE:-$CLINVAR_RELEASE}" 0
        else
            warn "ClinVar protein-match catalog unavailable; ClinVar protein matching not evaluated."
        fi
    fi

    if [[ -n "$CLINGEN_AA_REF" && -s "$CLINGEN_AA_REF" ]]; then
        apply_protein_match ClinGen ClinGen_path_aa_match "$CLINGEN_AA_REF" \
            "$CLINGEN_AA_RELEASE" 1
    fi
    if [[ -n "$GENIA_AA_REF" && -s "$GENIA_AA_REF" ]]; then
        apply_protein_match GenIA GenIA_path_aa_match "$GENIA_AA_REF" \
            "$GENIA_AA_RELEASE" 1
    fi

    if [[ "$MATCH_STEP" == "0" ]]; then
        FINAL_OUTPUT="$OUTPUT"
    else
        if [[ "$FINAL" == *.gz ]]; then
            publish_bgzf "$MATCH_INPUT" "$FINAL" vcf || die "protein-match indexed publication failed"
        else
            mv "$MATCH_INPUT" "$FINAL"
        fi
        log "clinical protein-match post-processing done -> $FINAL"
        FINAL_OUTPUT="$FINAL"
    fi
else
    FINAL_OUTPUT="$OUTPUT"
fi

# ============================================================================ #
# 7. ClinGen Evidence Repository expert-panel assertions
# ============================================================================ #
if [[ "$(yaml_get "$CONFIG" clingen_erepo.enabled)" == "true" ]]; then
    log "=== ClinGen Evidence Repository exact allele annotation ==="
    CLINGEN_DB="$(yaml_get "$CONFIG" clingen_erepo.database)"
    [[ "$CLINGEN_DB" = /* ]] || CLINGEN_DB="${ROOT}/${CLINGEN_DB}"
    CLINGEN_REQUIRED="$(yaml_get "$CONFIG" clingen_erepo.required)"
    CLINGEN_TMP="${FINAL_OUTPUT%.gz}.clingen.$$.tmp"
    POSTPROC_TMPS+=("$CLINGEN_TMP")
    CLINGEN_FASTA="$(yaml_get "$CONFIG" reference.fasta.path)"
    [[ -z "$CLINGEN_FASTA" || "$CLINGEN_FASTA" = /* ]] || CLINGEN_FASTA="${ROOT}/${CLINGEN_FASTA}"
    CLINGEN_ARGS=(--input "$FINAL_OUTPUT" --output "$CLINGEN_TMP" --database "$CLINGEN_DB")
    # Left-align repeat indels against the same GRCh38 reference the catalog
    # was normalised to, so a caller's representation cannot hide a match.
    [[ -n "$CLINGEN_FASTA" && -s "$CLINGEN_FASTA" ]] && CLINGEN_ARGS+=(--reference "$CLINGEN_FASTA")
    if [[ -s "$CLINGEN_DB" ]] && python3 "${ROOT}/pipeline/clingen_erepo_annotate.py" "${CLINGEN_ARGS[@]}"; then
        if [[ "$FINAL_OUTPUT" == *.gz ]]; then
            publish_bgzf "$CLINGEN_TMP" "$FINAL_OUTPUT" vcf || die "ClinGen indexed publication failed"
        else
            mv "$CLINGEN_TMP" "$FINAL_OUTPUT"
        fi
        log "ClinGen expert-panel assertions applied -> $FINAL_OUTPUT"
    elif [[ "$CLINGEN_REQUIRED" == "true" ]]; then
        die "required ClinGen Evidence Repository annotation failed"
    else
        warn "optional ClinGen Evidence Repository annotation failed"
        rm -f "$CLINGEN_TMP"
    fi
fi

# ============================================================================ #
# 8. GenIA registered-user exact allele evidence
# ============================================================================ #
if [[ "$(yaml_get "$CONFIG" genia.enabled)" == "true" ]]; then
    GENIA_DB="$(yaml_get "$CONFIG" genia.database)"
    [[ -z "$GENIA_DB" || "$GENIA_DB" = /* ]] || GENIA_DB="${ROOT}/${GENIA_DB}"
    GENIA_REQUIRED="$(yaml_get "$CONFIG" genia.required)"
    log "=== GenIA exact allele annotation ==="
    GENIA_TMP="${FINAL_OUTPUT%.gz}.genia.$$.tmp"
    POSTPROC_TMPS+=("$GENIA_TMP")
    GENIA_FASTA="$(yaml_get "$CONFIG" reference.fasta.path)"
    [[ -z "$GENIA_FASTA" || "$GENIA_FASTA" = /* ]] || GENIA_FASTA="${ROOT}/${GENIA_FASTA}"
    GENIA_ARGS=(
        --input "$FINAL_OUTPUT" --output "$GENIA_TMP"
        --database "$GENIA_DB"
    )
    [[ -n "$GENIA_FASTA" ]] && GENIA_ARGS+=(--reference "$GENIA_FASTA")
    [[ "$GENIA_REQUIRED" == "true" ]] || GENIA_ARGS+=(--allow-unavailable)
    if python3 "${ROOT}/pipeline/genia_annotate.py" "${GENIA_ARGS[@]}"; then
        if [[ "$FINAL_OUTPUT" == *.gz ]]; then
            publish_bgzf "$GENIA_TMP" "$FINAL_OUTPUT" vcf || die "GenIA indexed publication failed"
        else
            mv "$GENIA_TMP" "$FINAL_OUTPUT"
        fi
        log "GenIA exact-allele postprocessing complete -> $FINAL_OUTPUT"
    elif [[ "$GENIA_REQUIRED" == "true" ]]; then
        die "required GenIA annotation failed"
    else
        warn "optional GenIA annotation failed"
        rm -f "$GENIA_TMP"
    fi
fi

# ============================================================================ #
# 9. Annotation coverage report
# ============================================================================ #
if [[ "$(yaml_get "$CONFIG" annotation_qc.enabled)" != "false" ]]; then
    log "=== annotation coverage report ==="
    QC_ARGS=( --config "$CONFIG" --vcf "$FINAL_OUTPUT" )
    [[ -n "$FILTER_POLICY_NOTE" ]] && QC_ARGS+=( --note "$FILTER_POLICY_NOTE" )
    python3 "${ROOT}/pipeline/annotation_qc.py" "${QC_ARGS[@]}" \
        || die "annotation coverage report generation failed"
fi

# ============================================================================ #
# 9b. Research-use notice inside the deliverable. The protein-match step
#    writes it on every default run; when that step is disabled the header is
#    added here with one bounded header rewrite so no annotated VCF leaves
#    the workstation without it (audit H10).
# ============================================================================ #
NOTICE_HEADER_LINE="$(python3 "${ROOT}/pipeline/research_use_notice.py" --vcf-header)"
if [[ "$FINAL_OUTPUT" == *.gz ]]; then
    NOTICE_PRESENT="$( { hts bcftools view -h "$FINAL_OUTPUT" 2>/dev/null || true; } | grep -c '^##GUIDE_IEI_notice=' || true)"
else
    NOTICE_PRESENT="$(grep -c '^##GUIDE_IEI_notice=' "$FINAL_OUTPUT" || true)"
fi
if [[ "${NOTICE_PRESENT:-0}" -eq 0 ]]; then
    NOTICE_HDR="${FINAL_OUTPUT%.gz}.notice.$$.hdr"
    NOTICE_TMP="${FINAL_OUTPUT%.gz}.notice.$$.tmp.vcf.gz"
    POSTPROC_TMPS+=("${FINAL_OUTPUT%.gz}.notice.$$.tmp")
    printf '%s' "$NOTICE_HEADER_LINE" > "$NOTICE_HDR"
    if [[ "$FINAL_OUTPUT" == *.gz ]]; then
        if hts bcftools annotate -h "$NOTICE_HDR" -O z -o "$NOTICE_TMP" "$FINAL_OUTPUT" \
            && bgzf_complete "$NOTICE_TMP" && hts tabix -p vcf -f "$NOTICE_TMP" \
            && mv "${NOTICE_TMP}.tbi" "${FINAL_OUTPUT}.tbi" && mv "$NOTICE_TMP" "$FINAL_OUTPUT"; then
            log "research-use notice added to the VCF header"
        else
            warn "could not add the research-use notice header (the annotated VCF itself is complete)"
        fi
    else
        NOTICE_PLAIN="${FINAL_OUTPUT}.notice.$$.tmp"
        if awk -v notice="$(printf '%s' "$NOTICE_HEADER_LINE" | tr -d '\n')" '
            BEGIN { done = 0 }
            /^#CHROM/ && !done { print notice; done = 1 }
            { print }' "$FINAL_OUTPUT" > "$NOTICE_PLAIN" && mv "$NOTICE_PLAIN" "$FINAL_OUTPUT"; then
            log "research-use notice added to the VCF header"
        else
            rm -f "$NOTICE_PLAIN"
            warn "could not add the research-use notice header (the annotated VCF itself is complete)"
        fi
    fi
    rm -f "$NOTICE_HDR"
fi

# ============================================================================ #
# 10. Reproducibility manifest (decision D6): a machine-readable record of the
#    config (with hash), container identity, exact VEP argv, and reference
#    file identities that produced this deliverable. Reference identity is
#    size+mtime plus any recorded checksum sidecars — multi-GB references are
#    never re-hashed, so this adds ~a second per run.
# ============================================================================ #
IMAGE_ID="unknown"
case "$RUNTIME" in
    docker|podman)
        IMAGE_ID="$("$RUNTIME" image inspect --format '{{.Id}}' "$IMAGE" 2>/dev/null || echo unknown)" ;;
esac
# The deliverable sidecar names the file this run actually produced, so the
# service never has to guess from mtimes (equal timestamps on coarse
# filesystems picked a stale .aamatch sibling).
printf '%s\n' "$(basename "$FINAL_OUTPUT")" > "$DELIVERABLE_SIDECAR" || true
if python3 "${ROOT}/pipeline/write_run_manifest.py" \
    --config "$CONFIG" --base-dir "$ROOT" \
    --input "$SOURCE_INPUT" --output "$FINAL_OUTPUT" \
    --plan-json "$PLAN_JSON" \
    --runtime "$RUNTIME" --image "$IMAGE" --image-id "$IMAGE_ID" \
    --clinvar-release "${AA_MATCH_RELEASE:-$CLINVAR_RELEASE}" \
    --requested-assembly "$REQUESTED_ASSEMBLY" \
    --resolved-assembly "$RESOLVED_ASSEMBLY" \
    --filter-policy "${FILTER_LABELS[*]:-none}" \
    --region-bed "${REGION_BED:-}"; then
    log "run manifest -> ${FINAL_OUTPUT}.run_manifest.json"
else
    warn "run manifest could not be written (the annotated VCF itself is complete)"
fi

log "DONE. Annotated VCF: $FINAL_OUTPUT"
