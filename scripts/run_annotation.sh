#!/usr/bin/env bash
# =============================================================================
# run_annotation.sh — annotate a single-sample VCF with VEP + LOFTEE in the
# container, driven entirely by config/annotation.config.yaml.
#
#   scripts/run_annotation.sh -i sample.vcf.gz -o results/sample.vep.vcf.gz \
#       [-c config/annotation.config.yaml] [--no-clinvar] [--all-variants] [--dry-run]
#
# Steps:
#   0. (default) restrict input VCF to coding+splice BED  -> build_coding_bed.sh
#      Skipped with --all-variants or region.coding_only:false (WGS/non-coding).
#   1. (optional) fetch latest ClinVar               -> fetch_clinvar.sh
#   2. build VEP argv + bind-mounts from config      -> build_vep_command.py
#   3. run vep inside the container                  -> docker/podman/singularity
#   4. ClinVar amino-acid-match post-processing      -> clinvar_aa_match.py
#
# Output is an annotated VCF (INFO/CSQ), preserving sample GT/zygosity.
# =============================================================================
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${HERE}/lib.sh"
ROOT="$(cd "${HERE}/.." && pwd)"

CONFIG="${ROOT}/config/annotation.config.yaml"
INPUT=""; OUTPUT=""; DRY=0; DO_CLINVAR=1; ALL_VARIANTS=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        -i|--input)  INPUT="$2"; shift 2 ;;
        -o|--output) OUTPUT="$2"; shift 2 ;;
        -c|--config) CONFIG="$2"; shift 2 ;;
        --no-clinvar) DO_CLINVAR=0; shift ;;
        --all-variants) ALL_VARIANTS=1; shift ;;   # bypass coding-only region restriction (WGS/non-coding)
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

# --- runtime / image from config ---------------------------------------------
RUNTIME="$(yaml_get "$CONFIG" container.runtime)"; RUNTIME="${RUNTIME:-docker}"
IMAGE="$(yaml_get "$CONFIG" container.image)";     IMAGE="${IMAGE:-vep-annotate:latest}"
export RUNTIME IMAGE

# ============================================================================ #
# 0. Region restriction (DEFAULT): pre-filter input to coding exons + splice
#    sites. This is what makes a workstation run feasible — VEP then only sees
#    on-target variants. Bypass with --all-variants or region.coding_only:false.
# ============================================================================ #
CODING_ONLY="$(yaml_get "$CONFIG" region.coding_only)"
if [[ "$ALL_VARIANTS" == "1" ]]; then
    log "--all-variants: region restriction OFF (annotating every variant)."
elif [[ "$CODING_ONLY" == "false" ]]; then
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

    if [[ "$DRY" != "1" ]]; then
        # bcftools view -R needs an index on the input; ensure one, then subset.
        WORKDIR="$(dirname "$OUTPUT")"
        FILT="${WORKDIR}/$(basename "${INPUT%.vcf.gz}").coding.vcf.gz"
        FILT="${FILT%.vcf}.vcf.gz"   # normalize if input was plain .vcf
        [[ -f "${INPUT}.tbi" || -f "${INPUT}.csi" ]] || hts tabix -p vcf -f "$INPUT" 2>/dev/null || {
            # input not bgzipped/indexable as-is: bgzip a copy first
            hts bgzip -c "$INPUT" > "${WORKDIR}/$(basename "${INPUT%.gz}").gz"
            INPUT="${WORKDIR}/$(basename "${INPUT%.gz}").gz"
            hts tabix -p vcf -f "$INPUT"
        }
        NBEFORE="$(hts bcftools view -H "$INPUT" 2>/dev/null | wc -l | tr -d ' ')"
        hts bcftools view -R "$REGION_BED" -O z -o "$FILT" "$INPUT" || die "region pre-filter (bcftools view -R) failed"
        hts tabix -p vcf -f "$FILT" 2>/dev/null || true
        NAFTER="$(hts bcftools view -H "$FILT" 2>/dev/null | wc -l | tr -d ' ')"
        log "region pre-filter: ${NBEFORE} -> ${NAFTER} variants on-target"
        INPUT="$FILT"   # everything downstream annotates the filtered VCF
    else
        log "--dry-run: would pre-filter $INPUT with bcftools view -R $REGION_BED"
    fi
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

"${FULL[@]}" || die "VEP run failed"
log "VEP finished -> $OUTPUT"

# ============================================================================ #
# 4. ClinVar amino-acid-match post-processing
# ============================================================================ #
if [[ "$(yaml_get "$CONFIG" post_processing.clinvar_aa_match.enabled)" != "false" ]]; then
    log "=== ClinVar amino-acid-match post-processing ==="
    FINAL="${OUTPUT%.vcf.gz}.aamatch.vcf.gz"
    [[ "$OUTPUT" == *.vcf.gz ]] || FINAL="${OUTPUT%.vcf}.aamatch.vcf"
    AA_REF_ARG=()
    [[ -n "$AA_REF" && -s "$AA_REF" ]] && AA_REF_ARG=(--reference "$AA_REF")
    python3 "${ROOT}/pipeline/clinvar_aa_match.py" \
        --config "$CONFIG" --input "$OUTPUT" --output "$FINAL" \
        "${AA_REF_ARG[@]}" \
        --clinvar-release "$CLINVAR_RELEASE" || die "post-processing failed"
    log "post-processing done -> $FINAL"
    # index final if bgzipped
    [[ "$FINAL" == *.gz ]] && hts tabix -p vcf -f "$FINAL" 2>/dev/null || true
    log "DONE. Annotated VCF: $FINAL"
else
    log "DONE. Annotated VCF: $OUTPUT (post-processing disabled)"
fi
