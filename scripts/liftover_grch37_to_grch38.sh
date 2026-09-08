#!/usr/bin/env bash
# Convert an hg19/GRCh37 small-variant VCF into a provenance-rich GRCh38 VCF.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${HERE}/lib.sh"
ROOT="$(cd "${HERE}/.." && pwd)"

CONFIG="${ROOT}/config/annotation.config.yaml"
INPUT=""; OUTPUT=""; DRY=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        -i|--input) INPUT="$2"; shift 2 ;;
        -o|--output) OUTPUT="$2"; shift 2 ;;
        -c|--config) CONFIG="$2"; shift 2 ;;
        --dry-run) DRY=1; shift ;;
        -h|--help)
            echo "usage: liftover_grch37_to_grch38.sh -i input.vcf[.gz] -o output.vcf.gz [-c config] [--dry-run]"
            exit 0 ;;
        *) die "unknown arg: $1" ;;
    esac
done
[[ -f "$INPUT" ]] || die "liftover input not found: $INPUT"
[[ -n "$OUTPUT" && "$OUTPUT" == *.vcf.gz ]] || die "liftover output must end in .vcf.gz"
[[ -f "$CONFIG" ]] || die "config not found: $CONFIG"
INPUT="$(cd "$(dirname "$INPUT")" && pwd)/$(basename "$INPUT")"
mkdir -p "$(dirname "$OUTPUT")"
OUTPUT="$(cd "$(dirname "$OUTPUT")" && pwd)/$(basename "$OUTPUT")"

RUNTIME="$(yaml_get "$CONFIG" container.runtime)"; RUNTIME="${RUNTIME:-docker}"
IMAGE="$(yaml_get "$CONFIG" container.image)"; IMAGE="${IMAGE:-vep-annotate:latest}"
export RUNTIME IMAGE

CHAIN="$(yaml_get "$CONFIG" liftover.grch37_to_grch38.chain)"
[[ "$CHAIN" = /* ]] || CHAIN="${ROOT}/${CHAIN}"
SOURCE_FASTA="$(yaml_get "$CONFIG" liftover.grch37_to_grch38.source_fasta)"
[[ "$SOURCE_FASTA" = /* ]] || SOURCE_FASTA="${ROOT}/${SOURCE_FASTA}"
TARGET_FASTA="$(yaml_get "$CONFIG" reference.fasta.path)"
[[ "$TARGET_FASTA" = /* ]] || TARGET_FASTA="${ROOT}/${TARGET_FASTA}"
MAX_LENGTH="$(yaml_get "$CONFIG" liftover.max_allele_length)"
MAX_LENGTH="${MAX_LENGTH:-50}"
# Source mitochondrial sequence: auto (header evidence), rcrs, or hg19.
MT_CONVENTION="$(yaml_get "$CONFIG" liftover.mt_convention)"
MT_CONVENTION="${MT_CONVENTION:-auto}"
case "$MT_CONVENTION" in auto|rcrs|hg19) ;; *) die "liftover.mt_convention must be auto, rcrs, or hg19 (got: $MT_CONVENTION)" ;; esac
BCFTOOLS_VERSION="$(yaml_get "$CONFIG" liftover.bcftools_version)"
BCFTOOLS_VERSION="${BCFTOOLS_VERSION:-1.20}"
PLUGIN_COMMIT="$(yaml_get "$CONFIG" liftover.plugin_commit)"
PLUGIN_COMMIT="${PLUGIN_COMMIT:-909d23019e19aeadf3bf6fe1407fd6afc094592a}"
PIPELINE_VERSION="$(cat "${ROOT}/VERSION" 2>/dev/null || echo unknown)"
TARGET_DICT="${TARGET_FASTA%.gz}"
TARGET_DICT="${TARGET_DICT%.fa}.dict"

WORKDIR="$(dirname "$OUTPUT")"
BASE="$(basename "$OUTPUT" .vcf.gz)"
SUPPORTED="${WORKDIR}/${BASE}.liftover-supported.vcf"
UNSUPPORTED="${WORKDIR}/${BASE}.liftover-unsupported.vcf"
UNSUPPORTED_GZ="${UNSUPPORTED}.gz"
PRE_STATS="${WORKDIR}/${BASE}.liftover-precheck.json"
SOURCE_SORTED="${WORKDIR}/${BASE}.liftover-source-sorted.vcf.gz"
SPLIT="${WORKDIR}/${BASE}.liftover-split.vcf.gz"
RAW_LIFTED="${WORKDIR}/${BASE}.liftover-plugin.vcf.gz"
LIFTOVER_REJECT="${WORKDIR}/${BASE}.liftover-rejected.vcf.gz"
NORMALIZED_UNSORTED="${WORKDIR}/${BASE}.liftover-normalized-unsorted.vcf.gz"
MT_PASSTHROUGH="${WORKDIR}/${BASE}.liftover-mt-passthrough.vcf"
MT_PASSTHROUGH_SORTED="${WORKDIR}/${BASE}.liftover-mt-passthrough-sorted.vcf.gz"
MT_PASSTHROUGH_SPLIT="${WORKDIR}/${BASE}.liftover-mt-passthrough-split.vcf.gz"
MT_PASSTHROUGH_VERIFIED="${WORKDIR}/${BASE}.liftover-mt-passthrough-verified.vcf.gz"
COMBINED_UNSORTED="${WORKDIR}/${BASE}.liftover-combined-unsorted.vcf.gz"
LIFTED_ALL="${WORKDIR}/${BASE}.liftover-all.vcf.gz"
RETAINED="${WORKDIR}/${BASE}.liftover-retained.vcf"
REFERENCE_CORRECTIONS_RAW="${WORKDIR}/${BASE}.liftover-reference-corrections.raw.vcf"
REFERENCE_CORRECTIONS="${WORKDIR}/${BASE}.liftover-reference-corrections.vcf.gz"
CLASSIFICATION_STATS="${WORKDIR}/${BASE}.liftover-classification.json"
HEADER="${WORKDIR}/${BASE}.liftover-header.txt"
QC="${OUTPUT}.liftover.qc.json"
PROVENANCE="${OUTPUT}.liftover.provenance.json"

# Every working copy of the callset above (several of them whole-callset
# BGZFs) is removed on success and on any failure. Kept beside the lifted
# VCF: the rejected/unsupported records, the reference-correction audit, the
# QC/provenance JSON, and the two small stage-statistics JSON files that the
# QC step reconciles (audit M13).
LIFTOVER_WORK_FILES=(
    "$SUPPORTED" "${SUPPORTED}.gz"
    "$SOURCE_SORTED" "$SPLIT" "$RAW_LIFTED" "$NORMALIZED_UNSORTED"
    "$MT_PASSTHROUGH" "${MT_PASSTHROUGH}.gz" "$MT_PASSTHROUGH_SORTED"
    "$MT_PASSTHROUGH_SPLIT" "$MT_PASSTHROUGH_VERIFIED" "$COMBINED_UNSORTED"
    "$LIFTED_ALL" "$RETAINED" "${RETAINED}.gz"
    "$REFERENCE_CORRECTIONS_RAW" "${REFERENCE_CORRECTIONS_RAW}.gz"
    "$HEADER"
)
# `bcftools sort` spill directory: beside the output (never the container's
# overlay /tmp or macOS's unshared /var/folders), created before the first
# sort and removed with the working copies (audit M14).
LIFTOVER_SCRATCH=""
cleanup_liftover_work_files() {
    local f
    for f in "${LIFTOVER_WORK_FILES[@]}"; do rm -f -- "$f"; done
    [[ -z "$LIFTOVER_SCRATCH" ]] || rm -rf -- "$LIFTOVER_SCRATCH"
}

if [[ "$DRY" == "1" ]]; then
    log "--dry-run: would run allele-aware hg19 -> GRCh38 liftover before PASS/coding filtering"
    log "  source reference: $SOURCE_FASTA"
    log "  chain: $CHAIN"
    log "  target reference: $TARGET_FASTA"
    log "  accepted output: $OUTPUT"
    log "  reference-correction audit: $REFERENCE_CORRECTIONS"
    log "  liftover rejects: $LIFTOVER_REJECT"
    log "  unsupported records: $UNSUPPORTED_GZ"
    log "  mitochondrial convention: $MT_CONVENTION (rCRS records bypass the chain)"
    exit 0
fi

[[ -s "$SOURCE_FASTA" ]] || die "hg19 source FASTA missing: $SOURCE_FASTA (run download_references.sh --only liftover)"
[[ -s "${SOURCE_FASTA}.fai" ]] || die "hg19 source FASTA index missing: ${SOURCE_FASTA}.fai"
[[ -s "${SOURCE_FASTA}.gzi" ]] || die "hg19 source FASTA BGZF index missing: ${SOURCE_FASTA}.gzi"
[[ -s "$CHAIN" ]] || die "hg19->GRCh38 chain missing: $CHAIN (run download_references.sh --only liftover)"
[[ -s "$TARGET_FASTA" ]] || die "GRCh38 target FASTA missing: $TARGET_FASTA"

if [[ ! -s "$TARGET_DICT" || "$TARGET_DICT" -ot "$TARGET_FASTA" ]]; then
    # A dictionary older than its FASTA is stale (the FASTA was replaced);
    # regenerating it also invalidates cached conversions whose provenance
    # recorded the old dictionary.
    log "creating GRCh38 sequence dictionary: $TARGET_DICT"
    hts samtools dict -o "$TARGET_DICT" "$TARGET_FASTA"
fi

# Reuse an identical prior conversion. New input, source/target reference,
# chain, tool pin, policy, or pipeline version invalidates the cache.
if [[ -s "$OUTPUT" && -s "${OUTPUT}.tbi" && -s "$QC" && -s "$PROVENANCE" ]]; then
    if python3 - "$PROVENANCE" "$INPUT" "$CHAIN" "$SOURCE_FASTA" "$TARGET_DICT" \
        "$BCFTOOLS_VERSION" "$PLUGIN_COMMIT" "$MAX_LENGTH" "$PIPELINE_VERSION" \
        "$OUTPUT" "$TARGET_FASTA" "$MT_CONVENTION" <<'PY'
import gzip, hashlib, json, os, sys, zlib
(
    provenance_path, input_path, chain_path, source_path, dictionary_path,
    version, commit, max_length, pipeline_version,
    output_path, target_fasta_path, mt_convention_setting,
) = sys.argv[1:]
try:
    value = json.load(open(provenance_path))
except (OSError, ValueError):
    raise SystemExit(1)
def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
def sidecar_agrees(identity, path):
    # The multi-GB FASTA is never re-hashed at reuse time, but its
    # download-time sha256 sidecar is one line of text: when the recorded
    # identity and a current sidecar both exist, they must agree — a
    # re-downloaded or replaced reference then invalidates the cache
    # without a full read.
    recorded = identity.get("sha256")
    if not recorded:
        return True
    try:
        fields = open(path + ".sha256.local").read().split()
    except OSError:
        return True
    return not fields or fields[0] == recorded
def tabix_index_ok(path):
    # Structural check for legacy provenances that never recorded the
    # index: gzip magic, TBI magic, and a parseable TBI header with a
    # plausible sequence count — magic bytes alone accepted a 4-byte
    # payload htslib would choke on.
    try:
        with open(path, "rb") as handle:
            if handle.read(2) != b"\x1f\x8b":
                return False
        import struct
        with gzip.open(path, "rb") as handle:
            head = handle.read(4 + 8 * 4)
        if len(head) < 4 + 8 * 4 or head[:4] != b"TBI\x01":
            return False
        n_ref, fmt, col_seq, col_beg, col_end, meta, skip, l_nm = (
            struct.unpack("<8i", head[4:])
        )
        return n_ref >= 1 and 0 <= l_nm <= (1 << 27) and col_seq >= 0
    except (OSError, EOFError, zlib.error):
        # Truncated gzip raises EOFError and corrupt deflate zlib.error —
        # neither is an OSError; an escape here printed a raw traceback
        # even though bash treated the nonzero exit as a safe cache miss.
        return False
def same(identity, path, verify_hash=True):
    stat = os.stat(path)
    return (
        identity.get("path") == os.path.realpath(path)
        and identity.get("size_bytes") == stat.st_size
        and identity.get("mtime_ns") == stat.st_mtime_ns
        and (
            not verify_hash
            or not identity.get("sha256")
            or identity["sha256"] == sha256(path)
        )
    )
tool = value.get("tool", {})
def same_optional(key, path, **kwargs):
    # Provenances written before the key existed stay valid on their old
    # checks; new provenances enforce the stronger identity.
    return key not in value or same(value[key], path, **kwargs)
valid = (
    same(value.get("input", {}), input_path)
    and same(value.get("chain", {}), chain_path)
    and same(value.get("source_reference", {}), source_path, verify_hash=False)
    and sidecar_agrees(value.get("source_reference", {}), source_path)
    and same(value.get("target_sequence_dictionary", {}), dictionary_path)
    # The cached OUTPUT itself must be intact: a truncated or in-place
    # damaged file previously passed on mere non-emptiness.
    and same(value.get("output", {}), output_path)
    # A provenance that recorded the index verifies it in full; one that
    # predates the key must still prove the current .tbi is structurally
    # a tabix index — "any nonempty file" reused literal garbage.
    and (
        same(value["output_index"], output_path + ".tbi")
        if "output_index" in value
        else tabix_index_ok(output_path + ".tbi")
    )
    and same_optional("target_reference", target_fasta_path, verify_hash=False)
    and tool.get("bcftools_version") == version
    and tool.get("plugin_commit") == commit
    and str(value.get("policy", {}).get("max_allele_length")) == max_length
    # Conversions made before the mitochondrial convention existed are
    # invalid: rCRS MT records went through the hg19 chain.
    and value.get("policy", {}).get("mt_convention_setting") == mt_convention_setting
    and value.get("pipeline_version") == pipeline_version
)
raise SystemExit(0 if valid else 1)
PY
    then
        log "reusing cached hg19->GRCh38 conversion: $OUTPUT"
        exit 0
    fi
fi

trap cleanup_liftover_work_files EXIT
LIFTOVER_SCRATCH="$(mktemp -d "${WORKDIR}/.liftover-work.XXXXXX")" \
    || die "cannot create liftover temporary directory in $WORKDIR"
SORT_TMP_ARGS=(-T "${LIFTOVER_SCRATCH}/")
python3 "${ROOT}/pipeline/prepare_liftover_vcf.py" \
    --input "$INPUT" --supported "$SUPPORTED" --unsupported "$UNSUPPORTED" \
    --passthrough "$MT_PASSTHROUGH" --mt-convention "$MT_CONVENTION" \
    --stats "$PRE_STATS" --max-allele-length "$MAX_LENGTH"
MT_PASSTHROUGH_COUNT="$(python3 - "$PRE_STATS" <<'PY'
import json, sys
value = json.load(open(sys.argv[1]))
print(value.get("mt_passthrough_records", 0))
PY
)"
log "mitochondrial convention: $(python3 - "$PRE_STATS" <<'PY'
import json, sys
value = json.load(open(sys.argv[1]))
print(f"{value.get('mt_convention')} (evidence: {value.get('mt_convention_source')}); {value.get('mt_passthrough_records', 0)} rCRS record(s) bypass the chain")
PY
)"

hts bgzip -f "$UNSUPPORTED"
hts tabix -p vcf -f "$UNSUPPORTED_GZ" 2>/dev/null || true
hts bgzip -f "$SUPPORTED"
SUPPORTED_GZ="${SUPPORTED}.gz"

# Sorting here accepts otherwise valid legacy VCFs whose records are out of
# order and guarantees deterministic source and destination artifacts.
hts bcftools sort "${SORT_TMP_ARGS[@]}" -O z -o "$SOURCE_SORTED" "$SUPPORTED_GZ"
hts bcftools norm -m -any -O z -o "$SPLIT" "$SOURCE_SORTED"

# The +liftover plugin exists only in the pinned annotation image. hts()
# prefers a native bcftools whenever one is on PATH (conda/brew/apt installs
# do not ship third-party plugins, and one that did would be an unpinned
# version), so this one call is forced into the container and the version
# that actually ran is measured for the provenance record.
MEASURED_BCFTOOLS_OUTPUT="$(HTS_VIA_CONTAINER=1 hts bcftools --version 2>/dev/null || true)"
MEASURED_BCFTOOLS_VERSION="$(printf '%s\n' "$MEASURED_BCFTOOLS_OUTPUT" | awk 'NR==1 && $1=="bcftools" {print $2}')"
if [[ -z "$MEASURED_BCFTOOLS_VERSION" ]]; then
    die "cannot run bcftools inside the annotation image (${IMAGE}); GRCh37 intake needs the pinned BCFtools/liftover plugin — run bash docker/build.sh"
fi
if [[ "$MEASURED_BCFTOOLS_VERSION" != "$BCFTOOLS_VERSION" ]]; then
    warn "container bcftools is ${MEASURED_BCFTOOLS_VERSION} but liftover.bcftools_version pins ${BCFTOOLS_VERSION}; the measured version is recorded in the provenance"
fi
log "running BCFtools/liftover (hg19 -> GRCh38) with container bcftools ${MEASURED_BCFTOOLS_VERSION}"
HTS_VIA_CONTAINER=1 hts bcftools +liftover "$SPLIT" --no-version -O z -o "$RAW_LIFTED" -- \
    --src-fasta-ref "$SOURCE_FASTA" \
    --fasta-ref "$TARGET_FASTA" \
    --chain "$CHAIN" \
    --reject "$LIFTOVER_REJECT" \
    --reject-type z \
    --write-src \
    --write-fail \
    --write-reject \
    --fix-tags \
    --swap-tag IEI_LIFTOVER_SWAP \
    --flip-tag IEI_LIFTOVER_FLIP

hts bcftools norm -f "$TARGET_FASTA" -m -any --check-ref e \
    -O z -o "$NORMALIZED_UNSORTED" "$RAW_LIFTED"

if [[ "$MT_PASSTHROUGH_COUNT" -gt 0 ]]; then
    # rCRS mitochondrial records are already GRCh38 MT coordinates. They
    # skip the chain but not the reference check: every REF base is verified
    # against GRCh38 MT, so an input that was really NC_001807 (hg19 chrM)
    # fails here instead of being silently mis-positioned.
    log "verifying ${MT_PASSTHROUGH_COUNT} rCRS mitochondrial record(s) against GRCh38 MT"
    hts bgzip -f "$MT_PASSTHROUGH"
    hts bcftools sort "${SORT_TMP_ARGS[@]}" -O z -o "$MT_PASSTHROUGH_SORTED" "${MT_PASSTHROUGH}.gz"
    hts bcftools norm -m -any -O z -o "$MT_PASSTHROUGH_SPLIT" "$MT_PASSTHROUGH_SORTED"
    hts bcftools norm -f "$TARGET_FASTA" --check-ref e \
        -O z -o "$MT_PASSTHROUGH_VERIFIED" "$MT_PASSTHROUGH_SPLIT" \
        || die "mitochondrial records do not match the rCRS/GRCh38 MT sequence; if this callset was aligned to UCSC hg19 chrM (NC_001807), set liftover.mt_convention: hg19"
    hts bcftools concat --no-version -O z -o "$COMBINED_UNSORTED" \
        "$NORMALIZED_UNSORTED" "$MT_PASSTHROUGH_VERIFIED"
    hts bcftools sort "${SORT_TMP_ARGS[@]}" -O z -o "$LIFTED_ALL" "$COMBINED_UNSORTED"
else
    hts bcftools sort "${SORT_TMP_ARGS[@]}" -O z -o "$LIFTED_ALL" "$NORMALIZED_UNSORTED"
fi

python3 "${ROOT}/pipeline/classify_liftover_records.py" \
    --input "$LIFTED_ALL" \
    --retained "$RETAINED" \
    --reference-corrections "$REFERENCE_CORRECTIONS_RAW" \
    --stats "$CLASSIFICATION_STATS"

hts bgzip -f "$RETAINED"
RETAINED_GZ="${RETAINED}.gz"
hts bgzip -f "$REFERENCE_CORRECTIONS_RAW"
REFERENCE_CORRECTIONS_RAW_GZ="${REFERENCE_CORRECTIONS_RAW}.gz"

{
    echo "##reference=GRCh38"
    echo "##iei_target_assembly=GRCh38"
    echo "##iei_liftover=<SourceAssembly=GRCh37/hg19,TargetAssembly=GRCh38,Tool=BCFtools_liftover,BCFtoolsVersion=${BCFTOOLS_VERSION},PluginCommit=${PLUGIN_COMMIT},Chain=$(basename "$CHAIN"),MTConvention=${MT_CONVENTION},MTPassthroughRecords=${MT_PASSTHROUGH_COUNT},ReferenceCorrectionAudit=$(basename "$REFERENCE_CORRECTIONS"),QC=$(basename "$QC")>"
} > "$HEADER"
hts bcftools annotate -h "$HEADER" -O z -o "$OUTPUT" "$RETAINED_GZ"
hts bcftools annotate -h "$HEADER" -O z -o "$REFERENCE_CORRECTIONS" "$REFERENCE_CORRECTIONS_RAW_GZ"
hts tabix -p vcf -f "$OUTPUT"
hts tabix -p vcf -f "$REFERENCE_CORRECTIONS" 2>/dev/null || true
hts tabix -p vcf -f "$LIFTOVER_REJECT" 2>/dev/null || true

python3 "${ROOT}/pipeline/write_liftover_qc.py" \
    --input "$INPUT" --lifted-all "$LIFTED_ALL" --lifted "$OUTPUT" \
    --reference-corrections "$REFERENCE_CORRECTIONS" \
    --liftover-reject "$LIFTOVER_REJECT" --unsupported "$UNSUPPORTED_GZ" \
    --pre-stats "$PRE_STATS" --classification-stats "$CLASSIFICATION_STATS" \
    --chain "$CHAIN" --source-fasta "$SOURCE_FASTA" \
    --target-fasta "$TARGET_FASTA" --target-dict "$TARGET_DICT" \
    --qc-output "$QC" --provenance-output "$PROVENANCE" \
    --bcftools-version "$BCFTOOLS_VERSION" --plugin-commit "$PLUGIN_COMMIT" \
    --pipeline-version "$PIPELINE_VERSION" \
    --mt-convention-setting "$MT_CONVENTION" \
    --bcftools-version-measured "$MEASURED_BCFTOOLS_VERSION" \
    || die "liftover QC accounting failed; inspect $QC"

LIFTED_COUNT="$(python3 - "$QC" <<'PY'
import json, sys
print(json.load(open(sys.argv[1]))["lifted_allele_records"])
PY
)"
CORRECTION_COUNT="$(python3 - "$QC" <<'PY'
import json, sys
print(json.load(open(sys.argv[1]))["reference_correction_records"])
PY
)"
REJECTED_COUNT="$(python3 - "$QC" <<'PY'
import json, sys
value = json.load(open(sys.argv[1]))
print(value["liftover_rejected_records"] + value["unsupported_records"])
PY
)"
log "liftover complete: ${LIFTED_COUNT} variant allele records retained; ${CORRECTION_COUNT} reference corrections audited; ${REJECTED_COUNT} rejected/unsupported"
log "reference-correction audit: $REFERENCE_CORRECTIONS"
log "liftover QC: $QC"
log "liftover provenance: $PROVENANCE"
