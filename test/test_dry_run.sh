#!/usr/bin/env bash
# Smoke test: exercise the full pipeline wiring without a container or VEP.
#  1. build_vep_command --json produces a valid plan from the test config
#  2. run_annotation.sh --dry-run assembles the container invocation
#  3. clinvar_aa_match.py runs for real on a simulated VEP VCF and preserves GT
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${HERE}/.." && pwd)"
CFG="test/test.config.yaml"
cd "$ROOT"

# Generate the throwaway fixture reference files + simulated VEP output that
# the dry-run needs (kept out of git; regenerated on every run).
mkdir -p test/fixtures/vep_cache/homo_sapiens/113_GRCh38 \
         test/fixtures/fasta test/fixtures/loftee test/fixtures/custom \
         test/fixtures/clinvar test/out
touch test/fixtures/fasta/genome.fa.gz \
      test/fixtures/loftee/human_ancestor.fa.gz test/fixtures/loftee/loftee.sql \
      test/fixtures/loftee/gerp.bw test/fixtures/custom/repeatmasker.bed.gz \
      test/fixtures/clinvar/clinvar_latest.GRCh38.vcf.gz

# a simulated VEP --vcf output (3 variants, single sample) + aa-match catalog
CSQFMT="Allele|Consequence|IMPACT|SYMBOL|Gene|Feature_type|Feature|BIOTYPE|Protein_position|Amino_acids|SIFT|PolyPhen|gnomADe_AF|CADD_PHRED|LoF|ClinVar_CLNSIG"
cat > test/out/sample.vep.vcf <<VEPEOF
##fileformat=VCFv4.2
##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">
##INFO=<ID=CSQ,Number=.,Type=String,Description="Consequence annotations from Ensembl VEP. Format: ${CSQFMT}">
#CHROM	POS	ID	REF	ALT	QUAL	FILTER	INFO	FORMAT	SAMPLE1
17	43093464	.	G	A	500	PASS	CSQ=A|missense_variant|MODERATE|BRCA1|ENSG00000012048|Transcript|ENST00000357654|protein_coding|1699|R/W|deleterious|probably_damaging|0.0001|28.5||Pathogenic	GT	0/1
13	32339132	.	C	T	500	PASS	CSQ=T|missense_variant|MODERATE|BRCA2|ENSG00000139618|Transcript|ENST00000380152|protein_coding|2508|D/N|tolerated|benign|0.02|12.1||	GT	1/1
17	43125270	.	C	G	500	PASS	CSQ=G|synonymous_variant|LOW|BRCA1|ENSG00000012048|Transcript|ENST00000357654|protein_coding|500|L/L|||0.5|3.2||	GT	0/1
VEPEOF
printf 'BRCA1\t1699\nBRCA2\t9999\n' > test/out/clinvar_aa_reference.tsv

echo "[1/3] builder --json"
python3 "${ROOT}/pipeline/build_vep_command.py" --config "$CFG" \
  --input test/sample.mini.vcf --output test/out/sample.vep.vcf.gz --json \
  | python3 -c "import json,sys; p=json.load(sys.stdin); assert p['argv'][0]=='vep'; assert not p['errors'], p['errors']; print('  argv tokens:', len(p['argv']), ' mounts:', len(p['mounts']))"

echo "[2/3] run_annotation.sh --dry-run"
DRY_LOG="$(bash "${ROOT}/scripts/run_annotation.sh" -i test/sample.mini.vcf \
  -o test/out/sample.vep.vcf.gz -c "$CFG" --no-clinvar --dry-run 2>&1)"
grep -q -- '-f PASS' <<<"$DRY_LOG"
DRY_ALL_LOG="$(bash "${ROOT}/scripts/run_annotation.sh" -i test/sample.mini.vcf \
  -o test/out/sample.vep.vcf.gz -c "$CFG" --no-clinvar --dry-run \
  --all-variants --include-filtered 2>&1)"
grep -q 'input pre-filter OFF' <<<"$DRY_ALL_LOG"
echo "  dry-run assembled OK"

echo "[3/3] clinvar_aa_match.py on simulated VEP output"
python3 "${ROOT}/pipeline/clinvar_aa_match.py" \
  --input test/out/sample.vep.vcf \
  --output test/out/sample.aamatch.vcf \
  --reference test/out/clinvar_aa_reference.tsv --clinvar-release TEST 2>&1 | sed 's/^/  /'
grep -q 'ClinVar_path_aa_match=1' test/out/sample.aamatch.vcf && echo "  match flag present"
echo "ALL DRY-RUN CHECKS PASSED"
