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
#   0b. (default) retain FILTER=PASS and restrict to GRCh38 coding+splice BED.
#      Region filtering is skipped with --all-variants or
#      region.coding_only:false. PASS filtering is skipped only with
#      --include-filtered or run.pass_only:false.
#   1. (optional) fetch latest ClinVar               -> fetch_clinvar.sh
#   2. build VEP argv + bind-mounts from config      -> build_vep_command.py
#   3. run vep inside the container                  -> docker/podman/singularity
#   4. frameshift PTC-based LOFTEE 50-bp correction  -> loftee_ptc_50bp.py
#   5. sample-specific haplotype consequences         -> Haplosaurus
#   6. ClinVar amino-acid-match post-processing       -> clinvar_aa_match.py
#   7. exact allele-level ClinGen expert assertions    -> clingen_erepo_annotate.py
#   8. annotation completeness certificate            -> annotation_qc.py
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
INPUT="$(cd "$(dirname "$INPUT")" && pwd)/$(basename "$INPUT")"
mkdir -p "$(dirname "$OUTPUT")"
OUTPUT="$(cd "$(dirname "$OUTPUT")" && pwd)/$(basename "$OUTPUT")"
ORIGINAL_INPUT="$INPUT"
WORKDIR="$(dirname "$OUTPUT")"
INPUT_BASE="$(basename "$INPUT")"; INPUT_BASE="${INPUT_BASE%.gz}"; INPUT_BASE="${INPUT_BASE%.vcf}"

# --- runtime / image from config ---------------------------------------------
RUNTIME="$(yaml_get "$CONFIG" container.runtime)"; RUNTIME="${RUNTIME:-docker}"
IMAGE="$(yaml_get "$CONFIG" container.image)";     IMAGE="${IMAGE:-vep-annotate:latest}"
export RUNTIME IMAGE

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

if [[ "$RESOLVED_ASSEMBLY" == "GRCh37" ]]; then
    [[ "$(yaml_get "$CONFIG" liftover.enabled)" != "false" ]] \
        || die "GRCh37 input requires liftover.enabled:true"
    LIFTED_INPUT="${WORKDIR}/${INPUT_BASE}.lifted.GRCh38.vcf.gz"
    LIFTOVER_ARGS=(
        --input "$INPUT" --output "$LIFTED_INPUT" --config "$CONFIG"
    )
    [[ "$DRY" == "1" ]] && LIFTOVER_ARGS+=(--dry-run)
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
        log "region restriction ON (custom BED: $REGION_BED)"
    else
        REGION_BED="$(yaml_get "$CONFIG" region.bed)"; REGION_BED="${REGION_BED:-references/regions/coding_splice.padded.bed.gz}"
        [[ "$REGION_BED" = /* ]] || REGION_BED="${ROOT}/${REGION_BED}"
        if [[ ! -s "$REGION_BED" ]]; then
            log "coding BED missing; building it (one-time)."
            [[ "$DRY" == "1" ]] || bash "${HERE}/build_coding_bed.sh" "$CONFIG" || die "coding BED build failed"
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
        WGS_INDEXED="${WORKDIR}/${INPUT_BASE}.wgs-indexed.vcf.gz"
        log "whole-genome input is not queryable; creating sorted BGZF working copy"
        hts bcftools sort -O z -o "$WGS_INDEXED" "$INPUT" \
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
if [[ "$INCLUDE_FILTERED" == "1" ]]; then
    log "--include-filtered: PASS filter OFF (retaining non-PASS records)."
elif [[ "$PASS_ONLY" == "false" ]]; then
    log "run.pass_only:false — PASS filter OFF (retaining non-PASS records)."
else
    VIEW_ARGS+=( -f PASS )
    FILTER_LABELS+=( "FILTER=PASS" )
fi
if [[ -n "$REGION_BED" ]]; then
    VIEW_ARGS+=( -R "$REGION_BED" )
    FILTER_LABELS+=( "coding+splice region" )
fi

if [[ ${#VIEW_ARGS[@]} -gt 0 ]]; then
    INPUT_BASE="$(basename "$INPUT")"; INPUT_BASE="${INPUT_BASE%.gz}"; INPUT_BASE="${INPUT_BASE%.vcf}"
    FILT="${WORKDIR}/${INPUT_BASE}.prefiltered.vcf.gz"
    if [[ "$DRY" != "1" ]]; then
        # Normalize only contig labels when a GRCh38 VCF uses UCSC chr1/chrM
        # names but the Ensembl region BED/cache uses 1/MT (or vice versa).
        # Coordinates, alleles, INFO and sample FORMAT fields are unchanged.
        if [[ -n "$REGION_BED" ]]; then
            CHROM_MAP="${WORKDIR}/${INPUT_BASE}.chr-map.tsv"
            python3 "${ROOT}/pipeline/contig_map.py" \
                --vcf "$INPUT" --bed "$REGION_BED" --output "$CHROM_MAP"
            if [[ -s "$CHROM_MAP" ]]; then
                NORMALIZED="${WORKDIR}/${INPUT_BASE}.contigs-normalized.vcf.gz"
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
                COMPRESSED_INPUT="${WORKDIR}/${INPUT_BASE}.input.vcf.gz"
                hts bgzip -c "$INPUT" > "$COMPRESSED_INPUT"
                INPUT="$COMPRESSED_INPUT"
                hts tabix -p vcf -f "$INPUT"
            }
        fi
        NBEFORE="$(hts bcftools view -H "$INPUT" 2>/dev/null | wc -l | tr -d ' ')"
        hts bcftools view "${VIEW_ARGS[@]}" -O z -o "$FILT" "$INPUT" \
            || die "input pre-filter failed"
        hts tabix -p vcf -f "$FILT" 2>/dev/null || true
        NAFTER="$(hts bcftools view -H "$FILT" 2>/dev/null | wc -l | tr -d ' ')"
        log "input pre-filter (${FILTER_LABELS[*]}): ${NBEFORE} -> ${NAFTER} variants"
        INPUT="$FILT"
    else
        log "--dry-run: would pre-filter $INPUT with bcftools view ${VIEW_ARGS[*]}"
    fi
else
    log "input pre-filter OFF: annotating every record in the input VCF."
fi

# ============================================================================ #
# 1. ClinVar auto-fetch
# ============================================================================ #
CLINVAR_RELEASE="NA"
CLINVAR_VCF=""
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
# Rebuild when missing or older than the current ClinVar release. Requires the
# container (VEP). Skipped on --dry-run and when aa-match is disabled.
AA_REF=""
if [[ "$DRY" != "1" ]] \
   && [[ "$(yaml_get "$CONFIG" post_processing.clinvar_aa_match.enabled)" != "false" ]] \
   && [[ -n "$CLINVAR_VCF" && -f "$CLINVAR_VCF" ]]; then
    DEST_DIR="$(yaml_get "$CONFIG" clinvar.dest_dir)"; DEST_DIR="${DEST_DIR:-references/clinvar}"
    [[ "$DEST_DIR" = /* ]] || DEST_DIR="${ROOT}/${DEST_DIR}"
    AA_REF="${DEST_DIR}/clinvar_aa_reference.tsv"
    STAMP="${DEST_DIR}/.aa_reference.release"
    NEED_BUILD=1
    if [[ -s "$AA_REF" && -f "$STAMP" && "$(cat "$STAMP" 2>/dev/null)" == "$CLINVAR_RELEASE" ]]; then
        NEED_BUILD=0
        log "aa-match reference up to date (release $CLINVAR_RELEASE), skip rebuild."
    fi
    if [[ "$NEED_BUILD" == "1" ]]; then
        log "=== building ClinVar aa-match reference ==="
        if bash "${HERE}/build_clinvar_aa_reference.sh" "$CONFIG" "$CLINVAR_VCF"; then
            echo "$CLINVAR_RELEASE" > "$STAMP"
        else
            warn "aa-match reference build failed; matcher will flag 0 for all records."
        fi
    fi
fi

# ============================================================================ #
# 2. Build VEP argv + mounts (JSON) from the config
# ============================================================================ #
log "=== building VEP command from config ==="
PLAN_JSON="$(python3 "${ROOT}/pipeline/build_vep_command.py" \
    --config "$CONFIG" --input "$INPUT" --output "$OUTPUT" --json)" \
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
VEP_CMD_STR=""
for a in "${VEP_ARGV[@]}"; do
    # single-quote-safe, but keep $LOFTEE_DIR unquoted so the container shell expands it
    if [[ "$a" == *'$LOFTEE_DIR'* ]]; then
        # split around $LOFTEE_DIR and quote the rest
        pre="${a%%\$LOFTEE_DIR*}"; post="${a#*\$LOFTEE_DIR}"
        VEP_CMD_STR+=" '${pre}'\"\$LOFTEE_DIR\"'${post}'"
    else
        VEP_CMD_STR+=" '${a//\'/\'\\\'\'}'"
    fi
done

case "$RUNTIME" in
    docker|podman)
        FULL=( "$RUNTIME" run --rm "${MOUNT_FLAGS[@]}" --entrypoint sh "$IMAGE" -c "$VEP_CMD_STR" ) ;;
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

# VEP only creates this sidecar when warnings occur. Remove a sidecar from a
# prior failed/forced run so it cannot be mistaken for the current run's state.
rm -f "${OUTPUT}_warnings.txt"
"${FULL[@]}" || die "VEP run failed"
log "VEP finished -> $OUTPUT"
python3 "${ROOT}/pipeline/validate_vep_output.py" \
    --config "$CONFIG" --vcf "$OUTPUT" \
    || die "required annotation validation failed"

# ============================================================================ #
# 4. Recompute LOFTEE's frameshift 50-bp rule at the resulting PTC.
#    The corrected value replaces 50_BP_RULE inside CSQ/LoF_info; the original
#    verdict and calculation provenance remain in appended CSQ fields.
# ============================================================================ #
if [[ "$(yaml_get "$CONFIG" post_processing.loftee_ptc_50bp.enabled)" == "true" ]]; then
    log "=== LOFTEE frameshift PTC 50-bp recomputation ==="
    PTC_TMP="${OUTPUT%.gz}.ptc50.tmp"
    PTC_AUDIT="${OUTPUT}.loftee_ptc50.audit.json"
    if python3 "${ROOT}/pipeline/loftee_ptc_50bp.py" \
        --config "$CONFIG" --input "$OUTPUT" --output "$PTC_TMP" \
        --reported-output "$OUTPUT" --audit-json "$PTC_AUDIT"; then
        if [[ "$OUTPUT" == *.gz ]]; then
            hts bgzip -f "$PTC_TMP" || die "PTC 50-bp bgzip failed"
            mv "${PTC_TMP}.gz" "$OUTPUT"
            hts tabix -p vcf -f "$OUTPUT" || die "PTC 50-bp tabix index failed"
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
    HAPLO_TMP="${HAPLO_STEM}.haplo.tmp"
    HAPLO_AUDIT="${OUTPUT}.haplotype.audit.json"
    HAPLO_OK=1

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
                "$RUNTIME" run --rm \
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
            hts bgzip -f "$HAPLO_TMP" || die "haplotype post-processing bgzip failed"
            mv "${HAPLO_TMP}.gz" "$OUTPUT"
            hts tabix -p vcf -f "$OUTPUT" || die "haplotype post-processing tabix failed"
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
# 6. ClinVar amino-acid-match post-processing
# ============================================================================ #
if [[ "$(yaml_get "$CONFIG" post_processing.clinvar_aa_match.enabled)" != "false" ]]; then
    log "=== ClinVar amino-acid-match post-processing ==="
    FINAL="${OUTPUT%.vcf.gz}.aamatch.vcf.gz"
    [[ "$OUTPUT" == *.vcf.gz ]] || FINAL="${OUTPUT%.vcf}.aamatch.vcf"
    MATCH_OUTPUT="$FINAL"
    [[ "$FINAL" == *.gz ]] && MATCH_OUTPUT="${FINAL%.gz}.tmp"
    if [[ -n "$AA_REF" && -s "$AA_REF" ]]; then
        python3 "${ROOT}/pipeline/clinvar_aa_match.py" \
            --config "$CONFIG" --input "$OUTPUT" --output "$MATCH_OUTPUT" \
            --reference "$AA_REF" \
            --clinvar-release "$CLINVAR_RELEASE" || die "post-processing failed"
    else
        python3 "${ROOT}/pipeline/clinvar_aa_match.py" \
            --config "$CONFIG" --input "$OUTPUT" --output "$MATCH_OUTPUT" \
            --clinvar-release "$CLINVAR_RELEASE" || die "post-processing failed"
    fi
    if [[ "$FINAL" == *.gz ]]; then
        hts bgzip -f "$MATCH_OUTPUT" || die "post-processing bgzip failed"
        mv "${MATCH_OUTPUT}.gz" "$FINAL"
    fi
    log "post-processing done -> $FINAL"
    # index final if bgzipped
    if [[ "$FINAL" == *.gz ]]; then
        hts tabix -p vcf -f "$FINAL" || die "post-processing tabix index failed"
    fi
    FINAL_OUTPUT="$FINAL"
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
    CLINGEN_TMP="${FINAL_OUTPUT%.gz}.clingen.tmp"
    if [[ -s "$CLINGEN_DB" ]] && python3 "${ROOT}/pipeline/clingen_erepo_annotate.py" \
        --input "$FINAL_OUTPUT" --output "$CLINGEN_TMP" --database "$CLINGEN_DB"; then
        if [[ "$FINAL_OUTPUT" == *.gz ]]; then
            hts bgzip -f "$CLINGEN_TMP" || die "ClinGen annotation bgzip failed"
            mv "${CLINGEN_TMP}.gz" "$FINAL_OUTPUT"
            hts tabix -p vcf -f "$FINAL_OUTPUT" || die "ClinGen annotation tabix failed"
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
# 8. Annotation completeness certificate
# ============================================================================ #
if [[ "$(yaml_get "$CONFIG" annotation_qc.enabled)" != "false" ]]; then
    log "=== annotation completeness certificate ==="
    python3 "${ROOT}/pipeline/annotation_qc.py" \
        --config "$CONFIG" --vcf "$FINAL_OUTPUT" \
        || die "annotation completeness certificate generation failed"
fi

log "DONE. Annotated VCF: $FINAL_OUTPUT"
