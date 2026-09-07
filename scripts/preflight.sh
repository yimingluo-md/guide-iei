#!/usr/bin/env bash
# Validate a run before expensive filtering/annotation begins.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${HERE}/lib.sh"
ROOT="$(cd "${HERE}/.." && pwd)"

CONFIG="${1:?usage: preflight.sh <config.yaml> <input.vcf[.gz]> <output> [--input-assembly GRCh38|GRCh37|auto] [--dry-run]}"
INPUT="${2:?missing input VCF}"
OUTPUT="${3:?missing output path}"
shift 3
MODE=""
INPUT_ASSEMBLY=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run) MODE="--dry-run"; shift ;;
        --input-assembly) INPUT_ASSEMBLY="$2"; shift 2 ;;
        *) die "unknown preflight argument: $1" ;;
    esac
done
if [[ -z "$INPUT_ASSEMBLY" ]]; then
    INPUT_ASSEMBLY="$(yaml_get "$CONFIG" input.default_assembly)"
    INPUT_ASSEMBLY="${INPUT_ASSEMBLY:-GRCh38}"
fi

python3 - "$CONFIG" "$INPUT" "$OUTPUT" "$ROOT" "$MODE" "$INPUT_ASSEMBLY" <<'PY'
import gzip
import os
import re
import sqlite3
import sys
import yaml
from pathlib import Path

config_path, input_path, output_path, root, mode, requested_assembly = sys.argv[1:]
cfg = yaml.safe_load(open(config_path))
errors, warnings = [], []
sys.path.insert(0, os.path.join(root, "pipeline"))
from indexed_scores import ManifestError, load_manifest, validate_manifest_files
from predictor_registry import Adapter, RegistryError, load_registry
from vcf_assembly import resolve_input_assembly
from validate_input_vcf import validate_input_vcf

# The runner performs the complete streaming scan before any HTS command.
# Keep the UI preflight fast without maintaining a separate validation rule.
try:
    validate_input_vcf(input_path, full=False)
except ValueError as exc:
    print(f"ERROR {exc}", file=sys.stderr)
    raise SystemExit(2)

def absolute(path):
    if not path or os.path.isabs(path):
        return path
    return os.path.normpath(os.path.join(root, path))

registry = None
try:
    registry = load_registry(os.path.join(root, "config", "predictor-registry.json"))
except RegistryError as exc:
    errors.append(f"Predictor registry is invalid: {exc}")

if os.path.realpath(input_path) == os.path.realpath(output_path):
    errors.append("input and output resolve to the same path")

out_cfg = cfg.get("output", {}) or {}
fmt = out_cfg.get("format", "vcf")
compression = out_cfg.get("compress", "none")
if fmt == "vcf" and compression == "bgzip" and not output_path.endswith(".vcf.gz"):
    errors.append("output.format=vcf + compress=bgzip requires an output ending in .vcf.gz")
post = cfg.get("post_processing", {}) or {}
legacy_protein_match = (post.get("clinvar_aa_match", {}) or {}).get("enabled", False)
clinical_protein_match = post.get("clinical_protein_match") or {}
protein_match_enabled = clinical_protein_match.get("enabled", legacy_protein_match)
if fmt != "vcf" and protein_match_enabled:
    errors.append("Clinical protein residue/change matching requires output.format=vcf")

opener = gzip.open if input_path.endswith(".gz") else open
sample_count = None
contigs = {}
first_chrom = None
try:
    with opener(input_path, "rt") as fh:
        for line in fh:
            if line.startswith("##contig=<"):
                payload = line.split("<", 1)[1].rsplit(">", 1)[0]
                values = dict(
                    item.split("=", 1) for item in payload.split(",") if "=" in item
                )
                if "ID" in values:
                    contigs[values["ID"]] = values.get("length")
            elif line.startswith("#CHROM"):
                sample_count = max(0, len(line.rstrip("\n").split("\t")) - 9)
            elif not line.startswith("#"):
                first_chrom = line.split("\t", 1)[0]
                break
except (OSError, EOFError) as exc:
    errors.append(f"cannot read input VCF: {exc}")

if sample_count is None:
    errors.append("VCF column header (#CHROM) was not found")
elif sample_count < 1:
    errors.append("pipeline requires at least one sample")

resolved_assembly = None
try:
    assembly_result = resolve_input_assembly(input_path, requested_assembly)
    resolved_assembly = assembly_result["resolved"]
    warnings.extend(assembly_result["warnings"])
except (OSError, EOFError, ValueError) as exc:
    errors.append(str(exc))

target_assembly = (cfg.get("reference", {}) or {}).get("assembly", "GRCh38")
if target_assembly != "GRCh38":
    errors.append(
        f"reference.assembly must remain GRCh38; native {target_assembly} annotation is not supported"
    )

region = cfg.get("region", {}) or {}
if region.get("coding_only", True) and resolved_assembly != "GRCh37":
    observed = next(iter(contigs), None) or first_chrom
    custom_bed = absolute(region.get("custom_bed"))
    if custom_bed:
        bed_open = gzip.open if custom_bed.endswith(".gz") else open
        try:
            with bed_open(custom_bed, "rt") as bed:
                bed_chrom = next(
                    (line.split("\t", 1)[0] for line in bed if line.strip() and not line.startswith("#")),
                    None,
                )
            if observed and bed_chrom and observed.startswith("chr") != bed_chrom.startswith("chr"):
                errors.append(f"VCF/BED contig naming differs ({observed!r} vs {bed_chrom!r})")
        except OSError as exc:
            errors.append(f"cannot read custom BED: {exc}")
    elif observed and observed.startswith("chr"):
        warnings.append(
            "VCF uses chr-prefixed contigs; the run will normalize labels to the Ensembl 1..22/X/Y/MT convention"
        )

if mode != "--dry-run":
    reference = cfg.get("reference", {}) or {}
    cache = absolute(reference.get("vep_cache_dir"))
    if not cache or not os.path.isdir(cache):
        errors.append(f"VEP cache directory missing: {cache}")

    fasta = reference.get("fasta", {}) or {}
    if fasta.get("enabled"):
        path = absolute(fasta.get("path"))
        if not path or not os.path.isfile(path):
            errors.append(f"reference FASTA missing: {path}")
        elif not os.path.isfile(path + ".fai"):
            errors.append(f"reference FASTA index missing: {path}.fai")

    ptc50 = (cfg.get("post_processing", {}) or {}).get("loftee_ptc_50bp", {}) or {}
    if ptc50.get("enabled", False):
        required = ptc50.get("required", False)
        gtf = absolute(ptc50.get("gtf"))
        message = None
        if not gtf or not os.path.isfile(gtf):
            message = f"LOFTEE PTC 50-bp GTF missing: {gtf}"
        if message:
            (errors if required else warnings).append(message)
        fasta_path = absolute(fasta.get("path"))
        if not fasta_path or not os.path.isfile(fasta_path):
            message = f"LOFTEE PTC 50-bp reference FASTA missing: {fasta_path}"
            (errors if required else warnings).append(message)
        else:
            for suffix in (".fai", ".gzi"):
                if not os.path.isfile(fasta_path + suffix):
                    message = (
                        "LOFTEE PTC 50-bp requires FASTA index: "
                        f"{fasta_path}{suffix}"
                    )
                    (errors if required else warnings).append(message)
        vep_tag = (cfg.get("container", {}) or {}).get("vep_image_tag", "")
        release_match = re.search(r"release_(\d+)", str(vep_tag))
        gtf_match = re.search(r"\.(\d+)\.gtf\.gz$", str(gtf or ""))
        if release_match and gtf_match and release_match.group(1) != gtf_match.group(1):
            message = (
                "LOFTEE PTC 50-bp GTF/VEP release mismatch: "
                f"GTF {gtf_match.group(1)} vs VEP {release_match.group(1)}"
            )
            (errors if required else warnings).append(message)

    if resolved_assembly == "GRCh37":
        liftover = cfg.get("liftover", {}) or {}
        if not liftover.get("enabled", True):
            errors.append("GRCh37 input requires liftover.enabled:true")
        conversion = liftover.get("grch37_to_grch38", {}) or {}
        source_reference = str(conversion.get("source_reference") or "")
        if source_reference.lower() != "hg19":
            errors.append(
                "The validated GRCh37 intake preset requires "
                "liftover.grch37_to_grch38.source_reference:hg19"
            )
        source_fasta = absolute(conversion.get("source_fasta"))
        if not source_fasta or not os.path.isfile(source_fasta):
            errors.append(
                f"hg19 source FASTA missing: {source_fasta} "
                "(run download_references.sh --only liftover)"
            )
        else:
            for suffix in (".fai", ".gzi"):
                if not os.path.isfile(source_fasta + suffix):
                    errors.append(
                        f"hg19 source FASTA index missing: {source_fasta}{suffix} "
                        "(run download_references.sh --only liftover)"
                    )
        chain = absolute(
            conversion.get("chain")
        )
        if not chain or not os.path.isfile(chain):
            errors.append(
                f"GRCh37->GRCh38 chain missing: {chain} "
                "(run download_references.sh --only liftover)"
            )

    indexed = []
    plugins = cfg.get("plugins", {}) or {}
    dbnsfp = plugins.get("dbNSFP", {}) or {}
    if dbnsfp.get("enabled"):
        indexed.append(("dbNSFP", absolute(dbnsfp.get("path")), dbnsfp.get("required", False)))
    loftee = plugins.get("LoF", {}) or {}
    if loftee.get("enabled"):
        required = loftee.get("required", False)
        for key in ("human_ancestor_fa", "conservation_file", "gerp_bigwig"):
            path = absolute(loftee.get(key))
            if not path or not os.path.isfile(path):
                if required:
                    errors.append(f"required LOFTEE reference missing ({key}): {path}")
                else:
                    warnings.append(f"optional LOFTEE reference absent ({key}): {path}")
        ancestor = absolute(loftee.get("human_ancestor_fa"))
        if ancestor and os.path.isfile(ancestor):
            for suffix in (".fai", ".gzi"):
                if not os.path.isfile(ancestor + suffix):
                    if required:
                        errors.append(
                            f"required LOFTEE ancestor index missing: {ancestor}{suffix}"
                        )
                    else:
                        warnings.append(
                            f"optional LOFTEE ancestor index absent: {ancestor}{suffix}"
                        )
    spliceai = plugins.get("SpliceAI", {}) or {}
    if spliceai.get("enabled"):
        for key in ("snv", "indel"):
            if spliceai.get(key):
                indexed.append((f"SpliceAI.{key}", absolute(spliceai.get(key)), spliceai.get("required", False)))
    cadd = plugins.get("CADD_WGS", {}) or {}
    if cadd.get("enabled"):
        for key in ("snv", "indels"):
            indexed.append((
                f"CADD_WGS.{key}", absolute(cadd.get(key)),
                cadd.get("required", False),
            ))
    promoterai = plugins.get("PromoterAI", {}) or {}
    if promoterai.get("enabled"):
        required = promoterai.get("required", False)
        indexed.append(("PromoterAI", absolute(promoterai.get("file")), required))
        for key in ("transcript_map", "manifest"):
            path = absolute(promoterai.get(key))
            if not path or not os.path.isfile(path):
                message = f"PromoterAI {key} missing: {path} (run scripts/prepare_promoterai.sh)"
                (errors if required else warnings).append(message)
    logofunc = plugins.get("LoGoFunc", {}) or {}
    if logofunc.get("enabled"):
        required = logofunc.get("required", False)
        path = absolute(logofunc.get("file"))
        indexed.append(("LoGoFunc", path, required))
        manifest = absolute(logofunc.get("manifest"))
        if path and os.path.isfile(path) and (
            not manifest or not os.path.isfile(manifest)
        ):
            warnings.append(
                "LoGoFunc provenance manifest missing: "
                f"{manifest} (run scripts/prepare_logofunc.sh)"
            )
    if registry is not None:
        for annotator in registry.annotators:
            if annotator.adapter is not Adapter.GENERIC_INDEXED_LOOKUP:
                continue
            _, block_name = annotator.config_path.split(".")
            block = plugins.get(block_name, {}) or {}
            if not block.get("enabled"):
                continue
            resource = registry.resource(annotator.resource_id)
            required = block.get("required", False)
            assets = {asset.id: asset for asset in resource.assets}
            score_asset = assets.get("scores")
            manifest_asset = assets.get("manifest")
            if score_asset is None or manifest_asset is None:
                errors.append(
                    f"predictor registry resource {resource.id} lacks scores/manifest assets"
                )
                continue
            score_key = score_asset.config_path.rsplit(".", 1)[-1]
            manifest_key = manifest_asset.config_path.rsplit(".", 1)[-1]
            score_path = absolute(block.get(score_key))
            manifest_path = absolute(block.get(manifest_key))
            indexed.append((resource.label, score_path, required))
            if not manifest_path or not os.path.isfile(manifest_path):
                message = (
                    f"{resource.label} manifest missing: {manifest_path} "
                    f"(prepare the {resource.label} dataset before enabling it)"
                )
                (errors if required else warnings).append(message)
                continue
            try:
                payload = load_manifest(Path(manifest_path))
                if payload["resource"]["id"] != resource.id:
                    raise ManifestError(
                        f"manifest resource.id is not {resource.id}"
                    )
                index_path = Path(str(score_path) + ".tbi") if score_path else None
                if score_path and os.path.isfile(score_path) and index_path.is_file():
                    validate_manifest_files(payload, Path(score_path), index_path)
            except (OSError, ManifestError) as exc:
                message = f"{resource.label} manifest is invalid: {exc}"
                (errors if required else warnings).append(message)
    clingen = cfg.get("clingen_erepo", {}) or {}
    if clingen.get("enabled", False):
        required = clingen.get("required", False)
        for key in ("database", "vcf", "manifest"):
            path = absolute(clingen.get(key))
            message = None
            if not path or not os.path.isfile(path):
                message = f"ClinGen Evidence Repository {key} missing: {path} (update it from Annotation datasets)"
            if message:
                (errors if required else warnings).append(message)
    genia = cfg.get("genia", {}) or {}
    if genia.get("enabled", False):
        required = genia.get("required", False)
        path = absolute(genia.get("database"))
        if not path or not os.path.isfile(path):
            if required:
                errors.append(f"required GenIA database missing: {path}")
        else:
            try:
                uri = Path(path).resolve().as_uri() + "?mode=ro&immutable=1"
                with sqlite3.connect(uri, uri=True) as connection:
                    if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                        raise ValueError("database quick check failed")
                    component = connection.execute(
                        "SELECT release_hint FROM components WHERE id='variant_vcf'"
                    ).fetchone()
                    if component is None:
                        raise ValueError("variant VCF component is not installed")
                    if "GRCh38" not in str(component[0] or ""):
                        raise ValueError("variant VCF component is not identified as GRCh38")
                    connection.execute(
                        """
                        SELECT record_id,short_name,class_code,relevant_subjects
                        FROM variants
                        WHERE chrom=? AND pos=? AND ref=? AND alt=? LIMIT 0
                        """,
                        ("1", 1, "A", "C"),
                    )
            except (sqlite3.Error, ValueError) as exc:
                message = f"GenIA variant component is unavailable: {exc}"
                (errors if required else warnings).append(message)
    for name, track in (cfg.get("custom_tracks", {}) or {}).items():
        if track.get("enabled") and str(track.get("file", "")).endswith(".gz"):
            indexed.append((name, absolute(track.get("file")), track.get("required", False)))

    for label, path, required in indexed:
        if not path or not os.path.isfile(path):
            if required:
                errors.append(f"required indexed reference missing ({label}): {path}")
            else:
                warnings.append(f"optional reference absent ({label}): {path}")
            continue
        if not (os.path.isfile(path + ".tbi") or os.path.isfile(path + ".csi")):
            errors.append(f"index missing for enabled reference ({label}): {path}.tbi/.csi")

for warning in warnings:
    print(f"WARN  {warning}", file=sys.stderr)
for error in errors:
    print(f"ERROR {error}", file=sys.stderr)
if errors:
    raise SystemExit(2)
print("preflight data/config checks passed", file=sys.stderr)
PY

if [[ "$MODE" == "--dry-run" ]]; then
    log "preflight: dry-run mode skipped image/reference execution checks"
    exit 0
fi

RUNTIME="$(yaml_get "$CONFIG" container.runtime)"; RUNTIME="${RUNTIME:-docker}"
IMAGE="$(yaml_get "$CONFIG" container.image)"; IMAGE="${IMAGE:-vep-annotate:latest}"
command -v "$RUNTIME" >/dev/null 2>&1 || die "container runtime not found: $RUNTIME"
GENERIC_INDEXED_ENABLED="$(python3 - "$CONFIG" "$ROOT" <<'PY'
import os
import sys
import yaml

config_path, root = sys.argv[1:]
sys.path.insert(0, os.path.join(root, "pipeline"))
from predictor_registry import Adapter, load_registry

config = yaml.safe_load(open(config_path)) or {}
plugins = config.get("plugins", {}) or {}
registry = load_registry(os.path.join(root, "config", "predictor-registry.json"))
enabled = any(
    annotator.adapter is Adapter.GENERIC_INDEXED_LOOKUP
    and bool((plugins.get(annotator.config_path.split(".")[1], {}) or {}).get("enabled"))
    for annotator in registry.annotators
)
print("true" if enabled else "false")
PY
)"
CHECK='for tool in vep haplo bgzip tabix bcftools samtools; do command -v "$tool" >/dev/null || { echo "missing tool: $tool" >&2; exit 2; }; done; bcftools plugin -l 2>&1 | grep -qw liftover || { echo "missing bcftools +liftover plugin" >&2; exit 2; }'
if [[ "$(yaml_get "$CONFIG" plugins.PromoterAI.enabled)" == "true" ]]; then
    CHECK+='; test -r /plugins/PromoterAI.pm || { echo "missing bundled PromoterAI VEP plugin; rebuild with bash docker/build.sh" >&2; exit 2; }'
fi
if [[ "$(yaml_get "$CONFIG" plugins.LoGoFunc.enabled)" == "true" ]]; then
    CHECK+='; test -r /plugins/LoGoFunc.pm || { echo "missing bundled LoGoFunc VEP plugin; rebuild with bash docker/build.sh" >&2; exit 2; }'
fi
if [[ "$GENERIC_INDEXED_ENABLED" == "true" ]]; then
    CHECK+='; test -r /plugins/IndexedScores.pm || { echo "missing bundled indexed-predictor VEP plugin; rebuild with bash docker/build.sh" >&2; exit 2; }'
fi
if [[ "$(yaml_get "$CONFIG" plugins.CADD_WGS.enabled)" == "true" ]]; then
    CHECK+='; test -r /plugins/CADD.pm || { echo "missing standard CADD VEP plugin; rebuild with bash docker/build.sh" >&2; exit 2; }'
fi
case "$RUNTIME" in
    docker|podman)
        # Docker Desktop can occasionally list and run a tagged image while
        # `docker inspect --type=image <tag>` incorrectly returns "No such
        # image". Resolve through the image listing first, then let the actual
        # container execution below be the authoritative readiness check.
        IMAGE_ID="$("$RUNTIME" image ls --quiet --no-trunc "$IMAGE" 2>/dev/null | head -n 1)"
        [[ -n "$IMAGE_ID" ]] || die \
            "container image not found: $IMAGE (build it with: bash docker/build.sh)"
        EXPECTED_IMAGE_FINGERPRINT="$(bash "$ROOT/docker/image_fingerprint.sh")"
        ACTUAL_IMAGE_FINGERPRINT="$(
            "$RUNTIME" image inspect --format \
                '{{ index .Config.Labels "org.guide-iei.source-fingerprint" }}' \
                "$IMAGE" 2>/dev/null || true
        )"
        [[ "$ACTUAL_IMAGE_FINGERPRINT" = "$EXPECTED_IMAGE_FINGERPRINT" ]] || die \
            "container image is from an older GUIDE-IEI version: $IMAGE (rebuild it with: bash docker/build.sh)"
        "$RUNTIME" run --rm --ulimit core=0:0 --entrypoint sh "$IMAGE" -c "$CHECK" \
            || die "container image cannot run or is missing a required executable: $IMAGE"
        ;;
    singularity|apptainer)
        "$RUNTIME" exec "$IMAGE" sh -c "$CHECK" \
            || die "container image is missing a required executable"
        ;;
    *) die "unsupported runtime: $RUNTIME" ;;
esac
log "preflight container checks passed"
RESOLVED_ASSEMBLY="$(python3 "${ROOT}/pipeline/vcf_assembly.py" --vcf "$INPUT" --requested "$INPUT_ASSEMBLY")"
python3 "${ROOT}/pipeline/container_access.py" \
    --config "$CONFIG" --input "$INPUT" --output "$OUTPUT" \
    --root "$ROOT" --assembly "$RESOLVED_ASSEMBLY" \
    || die "container file access check failed"
