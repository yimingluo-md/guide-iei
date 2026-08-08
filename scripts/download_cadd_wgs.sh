#!/usr/bin/env bash
# Download exactly the CADD v1.7 GRCh38 score tables consumed by VEP CADD.pm.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${HERE}/.." && pwd)"
source "${HERE}/lib.sh"

CONFIG="${1:-${ROOT}/config/annotation.config.yaml}"
[[ -f "$CONFIG" ]] || die "config not found: $CONFIG"

ASSEMBLY="$(yaml_get "$CONFIG" reference.assembly)"
VERSION="$(yaml_get "$CONFIG" plugins.CADD_WGS.version)"
[[ "$ASSEMBLY" == "GRCh38" ]] || die "CADD WGS download is GRCh38-only (configured: $ASSEMBLY)"
[[ "$VERSION" == "1.7" ]] || die "this downloader is pinned to CADD v1.7 (configured: $VERSION)"

abs_path() {
    local path="$1"
    [[ "$path" = /* ]] || path="${ROOT}/${path}"
    printf '%s\n' "$path"
}

SNV="$(abs_path "$(yaml_get "$CONFIG" plugins.CADD_WGS.snv)")"
INDELS="$(abs_path "$(yaml_get "$CONFIG" plugins.CADD_WGS.indels)")"
[[ -n "$SNV" && -n "$INDELS" ]] || die "plugins.CADD_WGS.snv and .indels must be configured"

BASE="https://kircherlab.bihealth.org/download/CADD/v1.7/GRCh38"
SNV_NAME="whole_genome_SNVs.tsv.gz"
INDEL_NAME="gnomad.genomes.r4.0.indel.tsv.gz"
[[ "$(basename "$SNV")" == "$SNV_NAME" ]] || die "configured CADD SNV filename must be $SNV_NAME"
[[ "$(basename "$INDELS")" == "$INDEL_NAME" ]] || die "configured CADD indel filename must be $INDEL_NAME"

mkdir -p "$(dirname "$SNV")" "$(dirname "$INDELS")"
printf '  0.0%%  preparing official CADD v1.7 checksums\n'
log "CADD is licensed for non-commercial use; see ${BASE}/COPYRIGHT"
log "downloading only score tables, tabix indexes, and MD5 files (no inclAnno or release bundle)"

for target in "$SNV" "$INDELS"; do
    name="$(basename "$target")"
    fetch "${BASE}/${name}.md5" "${target}.md5" \
        || die "could not download CADD checksum: ${name}.md5"
    fetch "${BASE}/${name}.tbi.md5" "${target}.tbi.md5" \
        || die "could not download CADD checksum: ${name}.tbi.md5"
done

checksum() {
    local sidecar="$1" value
    value="$(awk 'NF { print tolower($1); exit }' "$sidecar")"
    [[ "$value" =~ ^[0-9a-f]{32}$ ]] || die "invalid CADD MD5 sidecar: $sidecar"
    printf '%s\n' "$value"
}

# Resolve every checksum via plain assignments FIRST: `die` inside a command
# substitution exits only that subshell, and an inline "$(checksum ...)" that
# failed previously expanded to "" — which parallel_fetch.py treats as "no
# verification", silently downloading 80+ GB unchecked. A failing assignment
# aborts the script under set -e with checksum()'s message on stderr.
SNV_MD5="$(checksum "${SNV}.md5")"
INDELS_MD5="$(checksum "${INDELS}.md5")"
SNV_TBI_MD5="$(checksum "${SNV}.tbi.md5")"
INDELS_TBI_MD5="$(checksum "${INDELS}.tbi.md5")"

python3 "${HERE}/parallel_fetch.py" "${BASE}/${SNV_NAME}" "$SNV" \
    --connections 8 --chunk-mib 128 --md5 "$SNV_MD5" \
    --progress-start 0 --progress-scale 98.59

python3 "${HERE}/parallel_fetch.py" "${BASE}/${INDEL_NAME}" "$INDELS" \
    --connections 4 --chunk-mib 64 --md5 "$INDELS_MD5" \
    --progress-start 98.59 --progress-scale 1.40

python3 "${HERE}/parallel_fetch.py" "${BASE}/${SNV_NAME}.tbi" "${SNV}.tbi" \
    --connections 1 --chunk-mib 4 --md5 "$SNV_TBI_MD5" \
    --progress-start 99.99 --progress-scale 0.006

python3 "${HERE}/parallel_fetch.py" "${BASE}/${INDEL_NAME}.tbi" "${INDELS}.tbi" \
    --connections 1 --chunk-mib 4 --md5 "$INDELS_TBI_MD5" \
    --progress-start 99.996 --progress-scale 0.004

# Keep the verified official index newer than its data file to avoid htslib's
# harmless stale-index timestamp warning after a resumed/finalized download.
touch "${SNV}.tbi" "${INDELS}.tbi"

RUNTIME="$(yaml_get "$CONFIG" container.runtime)"; RUNTIME="${RUNTIME:-docker}"
IMAGE="$(yaml_get "$CONFIG" container.image)"; IMAGE="${IMAGE:-vep-annotate:latest}"
SNV_HEADER="$(hts tabix -H "$SNV")"
INDEL_HEADER="$(hts tabix -H "$INDELS")"
[[ "$SNV_HEADER" == *"CADD GRCh38-v1.7"* && "$SNV_HEADER" == *$'#Chrom\tPos\tRef\tAlt\tRawScore\tPHRED'* ]] \
    || die "CADD SNV header is not the expected GRCh38-v1.7 score schema"
[[ "$INDEL_HEADER" == *"CADD GRCh38-v1.7"* && "$INDEL_HEADER" == *$'#Chrom\tPos\tRef\tAlt\tRawScore\tPHRED'* ]] \
    || die "CADD indel header is not the expected GRCh38-v1.7 score schema"

printf '100.0%%  CADD v1.7 GRCh38 score tables verified and indexed\n'
