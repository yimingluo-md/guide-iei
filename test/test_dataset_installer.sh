#!/usr/bin/env bash
# Verify that bulk dataset setup prepares HTS tooling before downloads and
# runs exactly two independent reference groups concurrently.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_ROOT="$(cd "${HERE}/.." && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
TEST_ROOT="$WORK/repo"
export TEST_ROOT

mkdir -p "$TEST_ROOT/scripts" "$TEST_ROOT/docker" "$TEST_ROOT/pipeline" \
  "$TEST_ROOT/config" "$WORK/bin"
cp "$SOURCE_ROOT/scripts/install_recommended_datasets.sh" "$TEST_ROOT/scripts/"
cp "$SOURCE_ROOT/scripts/lib.sh" "$TEST_ROOT/scripts/"

cat > "$TEST_ROOT/config/test.yaml" <<'YAML'
container:
  runtime: docker
  image: "vep-test:local"
  vep_image_tag: "release_113.4"
reference:
  assembly: GRCh38
  vep_cache_dir: data/vep_cache
  fasta:
    path: data/fasta/reference.fa.gz
plugins:
  LoF:
    human_ancestor_fa: data/loftee/human_ancestor.fa.gz
    conservation_file: data/loftee/loftee.sql
    gerp_bigwig: data/loftee/gerp.bw
  SpliceAI:
    snv: data/spliceai/snv.vcf.gz
custom_tracks:
  RepeatMasker:
    file: data/tracks/repeatmasker.bed.gz
  SegDup:
    file: data/tracks/segdup.bed.gz
  ClinVar:
    file: data/clinvar/clinvar_latest.GRCh38.vcf.gz
post_processing:
  loftee_ptc_50bp:
    gtf: data/regions/genes.gtf.gz
liftover:
  grch37_to_grch38:
    source_fasta: data/liftover/hg19.fa.gz
    chain: data/liftover/hg19ToGRCh38.chain.gz
wgs_review:
  ccre:
    bed: data/regions/ccre.bed.gz
  gene_tss:
    path: data/regions/gene_tss.tsv
clingen_erepo:
  database: data/clingen/erepo.sqlite
  vcf: data/clingen/erepo.vcf.gz
  manifest: data/clingen/manifest.json
YAML

cat > "$WORK/bin/docker" <<'SH'
#!/usr/bin/env bash
case "${1:-} ${2:-}" in
  "info ") exit 0 ;;
  "image inspect") [[ -f "$TEST_ROOT/image.ready" ]] ;;
  *) echo "unexpected fake docker invocation: $*" >&2; exit 1 ;;
esac
SH

cat > "$TEST_ROOT/docker/build.sh" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
printf '%s %s:%s\n' "$RUNTIME" "$IMAGE_NAME" "$IMAGE_TAG" > "$TEST_ROOT/build.args"
touch "$TEST_ROOT/image.ready"
SH

cat > "$TEST_ROOT/scripts/download_references.sh" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
[[ -f "$TEST_ROOT/image.ready" ]] || { echo "download began before image build" >&2; exit 1; }
shift
only=""
skip=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --only) only="$2"; shift 2 ;;
    --skip-final-status) skip=1; shift ;;
    *) shift ;;
  esac
done
[[ -n "$only" ]]
if [[ "${TEST_EXPECT_SINGLE:-0}" == "1" ]]; then
  [[ "$only" == "repeatmasker,segdup,ccre,liftover" && "$skip" == "0" ]]
  printf '%s\n' "$only" > "$TEST_ROOT/single-lane.args"
  exit 0
fi
[[ "$skip" == "1" ]]
printf '%s\n' "$only" > "$TEST_ROOT/lane.${only}.args"
touch "$TEST_ROOT/lane.${only}.started"
for unused in $(seq 1 200); do
  set -- "$TEST_ROOT"/lane.*.started
  [[ "$#" -ge 2 ]] && exit 0
  sleep 0.025
done
echo "reference groups did not overlap" >&2
exit 1
SH

cat > "$TEST_ROOT/scripts/fetch_clinvar.sh" <<'SH'
#!/usr/bin/env bash
touch "$TEST_ROOT/clinvar.called"
SH
cat > "$TEST_ROOT/scripts/update_clingen_erepo.sh" <<'SH'
#!/usr/bin/env bash
touch "$TEST_ROOT/clingen.called"
SH
cat > "$TEST_ROOT/pipeline/check_dbnsfp_version.py" <<'PY'
#!/usr/bin/env python3
raise SystemExit(0)
PY
chmod +x "$WORK/bin/docker" "$TEST_ROOT/docker/build.sh" \
  "$TEST_ROOT/scripts/download_references.sh" "$TEST_ROOT/scripts/fetch_clinvar.sh" \
  "$TEST_ROOT/scripts/update_clingen_erepo.sh"

PATH="$WORK/bin:$PATH" HTS_VIA_CONTAINER=1 \
  bash "$TEST_ROOT/scripts/install_recommended_datasets.sh" \
    "$TEST_ROOT/config/test.yaml" exome > "$WORK/install.log" 2>&1

grep -Fx 'docker vep-test:local' "$TEST_ROOT/build.args" >/dev/null
test -f "$TEST_ROOT/lane.vep_cache,fasta,loftee.started"
test -f "$TEST_ROOT/lane.spliceai,repeatmasker,segdup,ccre,liftover.started"
test -f "$TEST_ROOT/clinvar.called"
test -f "$TEST_ROOT/clingen.called"
grep -F 'downloading two independent reference groups in parallel' "$WORK/install.log" >/dev/null

# A real repair commonly has the mirror-backed payloads and SpliceAI already
# installed, leaving only the small UCSC tracks. This is also the empty-array
# case that macOS Bash 3.2 must accept under `set -u`.
mkdir -p "$TEST_ROOT/data/vep_cache/homo_sapiens/113_GRCh38" \
  "$TEST_ROOT/data/fasta" "$TEST_ROOT/data/loftee" "$TEST_ROOT/data/spliceai"
touch "$TEST_ROOT/data/vep_cache/.homo_sapiens_vep_113_GRCh38.complete"
printf 'ready\n' > "$TEST_ROOT/data/fasta/reference.fa.gz"
printf 'ready\n' > "$TEST_ROOT/data/loftee/human_ancestor.fa.gz"
printf 'ready\n' > "$TEST_ROOT/data/loftee/loftee.sql"
printf 'ready\n' > "$TEST_ROOT/data/loftee/gerp.bw"
printf 'ready\n' > "$TEST_ROOT/data/spliceai/snv.vcf.gz"
printf 'ready\n' > "$TEST_ROOT/data/spliceai/snv.vcf.gz.tbi"
TEST_EXPECT_SINGLE=1 PATH="$WORK/bin:$PATH" HTS_VIA_CONTAINER=1 \
  bash "$TEST_ROOT/scripts/install_recommended_datasets.sh" \
    "$TEST_ROOT/config/test.yaml" exome > "$WORK/repair.log" 2>&1
grep -Fx 'repeatmasker,segdup,ccre,liftover' "$TEST_ROOT/single-lane.args" >/dev/null

echo "PASS  dataset installer preflight, parallel groups, and one-lane repair"
