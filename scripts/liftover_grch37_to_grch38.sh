#!/usr/bin/env bash
# Convert a GRCh37/hg19 small-variant VCF into a provenance-rich GRCh38 VCF.
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
TARGET_FASTA="$(yaml_get "$CONFIG" reference.fasta.path)"
[[ "$TARGET_FASTA" = /* ]] || TARGET_FASTA="${ROOT}/${TARGET_FASTA}"
MAX_LENGTH="$(yaml_get "$CONFIG" liftover.max_allele_length)"
MAX_LENGTH="${MAX_LENGTH:-50}"
PICARD_VERSION="$(yaml_get "$CONFIG" liftover.picard_version)"
PICARD_VERSION="${PICARD_VERSION:-3.3.0}"
PIPELINE_VERSION="$(cat "${ROOT}/VERSION" 2>/dev/null || echo unknown)"
TARGET_DICT="${TARGET_FASTA%.gz}"
TARGET_DICT="${TARGET_DICT%.fa}.dict"

WORKDIR="$(dirname "$OUTPUT")"
BASE="$(basename "$OUTPUT" .vcf.gz)"
SUPPORTED="${WORKDIR}/${BASE}.liftover-supported.vcf"
UNSUPPORTED="${WORKDIR}/${BASE}.liftover-unsupported.vcf"
UNSUPPORTED_GZ="${UNSUPPORTED}.gz"
PRE_STATS="${WORKDIR}/${BASE}.liftover-precheck.json"
SPLIT="${WORKDIR}/${BASE}.liftover-split.vcf.gz"
RAW_LIFTED="${WORKDIR}/${BASE}.liftover-picard.vcf.gz"
PICARD_REJECT="${WORKDIR}/${BASE}.liftover-rejected.vcf.gz"
NORMALIZED="${WORKDIR}/${BASE}.liftover-normalized.vcf.gz"
HEADER="${WORKDIR}/${BASE}.liftover-header.txt"
QC="${OUTPUT}.liftover.qc.json"
PROVENANCE="${OUTPUT}.liftover.provenance.json"

if [[ "$DRY" == "1" ]]; then
    log "--dry-run: would liftover GRCh37 -> GRCh38 before PASS/coding filtering"
    log "  chain: $CHAIN"
    log "  target reference: $TARGET_FASTA"
    log "  accepted output: $OUTPUT"
    log "  Picard rejects: $PICARD_REJECT"
    log "  unsupported records: $UNSUPPORTED_GZ"
    exit 0
fi

[[ -s "$CHAIN" ]] || die "GRCh37->GRCh38 chain missing: $CHAIN (run download_references.sh --only liftover)"
[[ -s "$TARGET_FASTA" ]] || die "GRCh38 target FASTA missing: $TARGET_FASTA"

if [[ ! -s "$TARGET_DICT" ]]; then
    log "creating GRCh38 sequence dictionary: $TARGET_DICT"
    hts samtools dict -o "$TARGET_DICT" "$TARGET_FASTA"
fi

# Reuse an identical prior conversion. This makes large-cohort ingestion a
# one-time preprocessing cost while invalidating safely when input, chain,
# target dictionary, Picard version, or the validated allele scope changes.
if [[ -s "$OUTPUT" && -s "$QC" && -s "$PROVENANCE" ]]; then
    if python3 - "$PROVENANCE" "$INPUT" "$CHAIN" "$TARGET_DICT" "$PICARD_VERSION" "$MAX_LENGTH" "$PIPELINE_VERSION" <<'PY'
import hashlib, json, os, sys
provenance_path, input_path, chain_path, dictionary_path, version, max_length, pipeline_version = sys.argv[1:]
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
def same(identity, path):
    stat = os.stat(path)
    return (
        identity.get("path") == os.path.realpath(path)
        and identity.get("size_bytes") == stat.st_size
        and identity.get("mtime_ns") == stat.st_mtime_ns
        and (not identity.get("sha256") or identity["sha256"] == sha256(path))
    )
valid = (
    same(value.get("input", {}), input_path)
    and same(value.get("chain", {}), chain_path)
    and same(value.get("target_sequence_dictionary", {}), dictionary_path)
    and value.get("tool", {}).get("version") == version
    and str(value.get("policy", {}).get("max_allele_length")) == max_length
    and value.get("pipeline_version") == pipeline_version
)
raise SystemExit(0 if valid else 1)
PY
    then
        log "reusing cached GRCh37->GRCh38 conversion: $OUTPUT"
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
hts tabix -p vcf -f "$SUPPORTED_GZ"

# Split multiallelic sites without changing source alleles. Target-reference
# normalization happens only after liftover.
hts bcftools norm -m -any -O z -o "$SPLIT" "$SUPPORTED_GZ"
hts tabix -p vcf -f "$SPLIT"

log "running Picard LiftoverVcf (GRCh37 -> GRCh38)"
hts picard LiftoverVcf \
    --INPUT "$SPLIT" \
    --OUTPUT "$RAW_LIFTED" \
    --CHAIN "$CHAIN" \
    --REJECT "$PICARD_REJECT" \
    --REFERENCE_SEQUENCE "$TARGET_FASTA" \
    --WARN_ON_MISSING_CONTIG true \
    --WRITE_ORIGINAL_POSITION true \
    --RECOVER_SWAPPED_REF_ALT true

hts bcftools norm -f "$TARGET_FASTA" -m -any --check-ref e \
    -O z -o "$NORMALIZED" "$RAW_LIFTED"

{
    echo "##reference=GRCh38"
    echo "##iei_target_assembly=GRCh38"
    echo "##iei_liftover=<SourceAssembly=GRCh37,TargetAssembly=GRCh38,Tool=Picard_LiftoverVcf,PicardVersion=${PICARD_VERSION},Chain=$(basename "$CHAIN"),QC=$(basename "$QC")>"
} > "$HEADER"
hts bcftools annotate -h "$HEADER" -O z -o "$OUTPUT" "$NORMALIZED"
hts tabix -p vcf -f "$OUTPUT"
hts tabix -p vcf -f "$PICARD_REJECT" 2>/dev/null || true

python3 "${ROOT}/pipeline/write_liftover_qc.py" \
    --input "$INPUT" --lifted "$OUTPUT" --picard-reject "$PICARD_REJECT" \
    --unsupported "$UNSUPPORTED_GZ" --pre-stats "$PRE_STATS" \
    --chain "$CHAIN" --target-dict "$TARGET_DICT" \
    --qc-output "$QC" --provenance-output "$PROVENANCE" \
    --picard-version "$PICARD_VERSION" --pipeline-version "$PIPELINE_VERSION" \
    || die "liftover QC accounting failed; inspect $QC"

LIFTED_COUNT="$(python3 - "$QC" <<'PY'
import json, sys
value = json.load(open(sys.argv[1]))
print(value["lifted_records"])
PY
)"
REJECTED_COUNT="$(python3 - "$QC" <<'PY'
import json, sys
value = json.load(open(sys.argv[1]))
print(value["picard_rejected_records"] + value["unsupported_records"])
PY
)"
log "liftover complete: ${LIFTED_COUNT} input records lifted; ${REJECTED_COUNT} rejected/unsupported"
log "liftover QC: $QC"
log "liftover provenance: $PROVENANCE"
