#!/usr/bin/env bash
# Download, validate, and atomically install the ClinGen Evidence Repository.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${HERE}/lib.sh"
ROOT="$(repo_root)"
CONFIG="${1:-${ROOT}/config/annotation.config.yaml}"
URL="$(yaml_get "$CONFIG" clingen_erepo.url)"
URL="${URL:-https://erepo.clinicalgenome.org/evrepo/api/summary/classifications/download?type=csv}"
DEST="$(yaml_get "$CONFIG" clingen_erepo.dest_dir)"
DEST="${DEST:-references/clingen_erepo}"; [[ "$DEST" = /* ]] || DEST="${ROOT}/${DEST}"
CLINVAR="$(yaml_get "$CONFIG" custom_tracks.ClinVar.file)"
CLINVAR="${CLINVAR:-references/clinvar/clinvar_latest.GRCh38.vcf.gz}"; [[ "$CLINVAR" = /* ]] || CLINVAR="${ROOT}/${CLINVAR}"
FASTA="$(yaml_get "$CONFIG" reference.fasta.path)"; [[ "$FASTA" = /* ]] || FASTA="${ROOT}/${FASTA}"
[[ -s "$CLINVAR" ]] || die "ClinVar GRCh38 VCF is required to resolve ClinGen alleles; download ClinVar first"
[[ -s "$FASTA" && -s "${FASTA}.fai" ]] || die "indexed GRCh38 FASTA is required to prepare ClinGen indels"

mkdir -p "$DEST"
STAGE="$(mktemp -d "${DEST}/.update.XXXXXX")"
trap 'rm -rf "$STAGE"' EXIT
SOURCE="${STAGE}/classifications.tsv"
REGIONS="${STAGE}/regions.txt"
SEQUENCES="${STAGE}/regions.fa"
RAW_VCF="${STAGE}/clingen_erepo.GRCh38.vcf"
DB="${STAGE}/clingen_erepo.sqlite3"
MANIFEST="${STAGE}/manifest.json"

log "download ClinGen Evidence Repository classifications"
if command -v curl >/dev/null 2>&1; then
    curl -fL --retry 3 --retry-delay 3 -o "$SOURCE" "$URL"
else
    wget -O "$SOURCE" "$URL"
fi
if [[ -s "${DEST}/manifest.json" ]] && python3 - "$SOURCE" "${DEST}/manifest.json" <<'PY'
import hashlib,json,sys
source,manifest=sys.argv[1:]
digest=hashlib.sha256(open(source,'rb').read()).hexdigest()
try: installed=json.load(open(manifest)).get('source_sha256','')
except Exception: installed=''
raise SystemExit(0 if digest and digest == installed else 1)
PY
then
    log "ClinGen Evidence Repository is already current; installed snapshot unchanged"
    exit 0
fi
API_VERSION="$(python3 - <<'PY'
import json, urllib.request
try:
    with urllib.request.urlopen('https://erepo.clinicalgenome.org/evrepo/api/summary/srvc', timeout=20) as response:
        payload=json.load(response)
    def find(value):
        if isinstance(value, dict):
            for key in ('apiVersion', 'api_version', 'version'):
                if value.get(key): return str(value[key])
            for item in value.values():
                found=find(item)
                if found: return found
        elif isinstance(value, list):
            for item in value:
                found=find(item)
                if found: return found
        return ''
    print(find(payload) or 'unknown')
except Exception:
    print('unknown')
PY
)"
python3 "${ROOT}/pipeline/prepare_clingen_erepo.py" --source "$SOURCE" \
    --clinvar-vcf "$CLINVAR" --regions-out "$REGIONS"
if [[ -s "$REGIONS" ]]; then
    if command -v samtools >/dev/null 2>&1; then
        samtools faidx -r "$REGIONS" "$FASTA" > "$SEQUENCES"
    else
        log "samtools is unavailable; using the dependency-free sequential FASTA extractor"
        python3 "${ROOT}/pipeline/extract_fasta_regions.py" --fasta "$FASTA" \
            --regions "$REGIONS" --output "$SEQUENCES"
    fi
else
    : > "$SEQUENCES"
fi
python3 "${ROOT}/pipeline/prepare_clingen_erepo.py" --source "$SOURCE" \
    --clinvar-vcf "$CLINVAR" --reference-sequences "$SEQUENCES" \
    --output-vcf "$RAW_VCF" --output-sqlite "$DB" --manifest "$MANIFEST" \
    --api-version "$API_VERSION"
python3 - "$MANIFEST" "$RAW_VCF" "$DB" <<'PY'
import hashlib,json,sys
manifest_path,vcf,db=sys.argv[1:]
data=json.load(open(manifest_path))
def digest(path):
 h=hashlib.sha256()
 with open(path,'rb') as f:
  for chunk in iter(lambda:f.read(1024*1024),b''): h.update(chunk)
 return h.hexdigest()
data['vcf_sha256']=digest(vcf); data['sqlite_sha256']=digest(db)
open(manifest_path,'w').write(json.dumps(data,indent=2,sort_keys=True)+'\n')
PY

# Atomic file replacement: a failed download/preparation never damages the
# previous working snapshot. SQLite readers open a fresh connection per query.
mv "$RAW_VCF" "${DEST}/clingen_erepo.GRCh38.vcf.new"
mv "$DB" "${DEST}/clingen_erepo.sqlite3.new"
mv "$MANIFEST" "${DEST}/manifest.json.new"
mv "${DEST}/clingen_erepo.GRCh38.vcf.new" "${DEST}/clingen_erepo.GRCh38.vcf"
mv "${DEST}/clingen_erepo.sqlite3.new" "${DEST}/clingen_erepo.sqlite3"
mv "${DEST}/manifest.json.new" "${DEST}/manifest.json"
log "ClinGen Evidence Repository update installed: ${DEST}"
