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
LIFTED_ALL="${WORKDIR}/${BASE}.liftover-all.vcf.gz"
RETAINED="${WORKDIR}/${BASE}.liftover-retained.vcf"
REFERENCE_CORRECTIONS_RAW="${WORKDIR}/${BASE}.liftover-reference-corrections.raw.vcf"
REFERENCE_CORRECTIONS="${WORKDIR}/${BASE}.liftover-reference-corrections.vcf.gz"
CLASSIFICATION_STATS="${WORKDIR}/${BASE}.liftover-classification.json"
HEADER="${WORKDIR}/${BASE}.liftover-header.txt"
QC="${OUTPUT}.liftover.qc.json"
PROVENANCE="${OUTPUT}.liftover.provenance.json"

if [[ "$DRY" == "1" ]]; then
    log "--dry-run: would run allele-aware hg19 -> GRCh38 liftover before PASS/coding filtering"
    log "  source reference: $SOURCE_FASTA"
    log "  chain: $CHAIN"
    log "  target reference: $TARGET_FASTA"
    log "  accepted output: $OUTPUT"
    log "  reference-correction audit: $REFERENCE_CORRECTIONS"
    log "  liftover rejects: $LIFTOVER_REJECT"
    log "  unsupported records: $UNSUPPORTED_GZ"
    exit 0
fi

[[ -s "$SOURCE_FASTA" ]] || die "hg19 source FASTA missing: $SOURCE_FASTA (run download_references.sh --only liftover)"
[[ -s "${SOURCE_FASTA}.fai" ]] || die "hg19 source FASTA index missing: ${SOURCE_FASTA}.fai"
[[ -s "${SOURCE_FASTA}.gzi" ]] || die "hg19 source FASTA BGZF index missing: ${SOURCE_FASTA}.gzi"
[[ -s "$CHAIN" ]] || die "hg19->GRCh38 chain missing: $CHAIN (run download_references.sh --only liftover)"
[[ -s "$TARGET_FASTA" ]] || die "GRCh38 target FASTA missing: $TARGET_FASTA"

if [[ ! -s "$TARGET_DICT" ]]; then
    log "creating GRCh38 sequence dictionary: $TARGET_DICT"
    hts samtools dict -o "$TARGET_DICT" "$TARGET_FASTA"
fi

# Reuse an identical prior conversion. New input, source/target reference,
# chain, tool pin, policy, or pipeline version invalidates the cache.
if [[ -s "$OUTPUT" && -s "$QC" && -s "$PROVENANCE" ]]; then
    if python3 - "$PROVENANCE" "$INPUT" "$CHAIN" "$SOURCE_FASTA" "$TARGET_DICT" \
        "$BCFTOOLS_VERSION" "$PLUGIN_COMMIT" "$MAX_LENGTH" "$PIPELINE_VERSION" <<'PY'
import hashlib, json, os, sys
(
    provenance_path, input_path, chain_path, source_path, dictionary_path,
    version, commit, max_length, pipeline_version,
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
valid = (
    same(value.get("input", {}), input_path)
    and same(value.get("chain", {}), chain_path)
    and same(value.get("source_reference", {}), source_path, verify_hash=False)
    and same(value.get("target_sequence_dictionary", {}), dictionary_path)
    and tool.get("bcftools_version") == version
    and tool.get("plugin_commit") == commit
    and str(value.get("policy", {}).get("max_allele_length")) == max_length
    and value.get("pipeline_version") == pipeline_version
)
raise SystemExit(0 if valid else 1)
PY
    then
        log "reusing cached hg19->GRCh38 conversion: $OUTPUT"
        exit 0
    fi
fi

python3 "${ROOT}/pipeline/prepare_liftover_vcf.py" \
    --input "$INPUT" --supported "$SUPPORTED" --unsupported "$UNSUPPORTED" \
    --stats "$PRE_STATS" --max-allele-length "$MAX_LENGTH"

hts bgzip -f "$UNSUPPORTED"
hts tabix -p vcf -f "$UNSUPPORTED_GZ" 2>/dev/null || true
hts bgzip -f "$SUPPORTED"
SUPPORTED_GZ="${SUPPORTED}.gz"

# Sorting here accepts otherwise valid legacy VCFs whose records are out of
# order and guarantees deterministic source and destination artifacts.
hts bcftools sort -O z -o "$SOURCE_SORTED" "$SUPPORTED_GZ"
hts bcftools norm -m -any -O z -o "$SPLIT" "$SOURCE_SORTED"

log "running BCFtools/liftover (hg19 -> GRCh38)"
hts bcftools +liftover "$SPLIT" --no-version -O z -o "$RAW_LIFTED" -- \
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
hts bcftools sort -O z -o "$LIFTED_ALL" "$NORMALIZED_UNSORTED"

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
    echo "##iei_liftover=<SourceAssembly=GRCh37/hg19,TargetAssembly=GRCh38,Tool=BCFtools_liftover,BCFtoolsVersion=${BCFTOOLS_VERSION},PluginCommit=${PLUGIN_COMMIT},Chain=$(basename "$CHAIN"),ReferenceCorrectionAudit=$(basename "$REFERENCE_CORRECTIONS"),QC=$(basename "$QC")>"
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
