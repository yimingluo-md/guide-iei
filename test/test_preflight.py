#!/usr/bin/env python3
"""Dry-run preflight checks that require no references or container."""
import pathlib
import subprocess
import tempfile


ROOT = pathlib.Path(__file__).resolve().parents[1]
PREFLIGHT = ROOT / "scripts/preflight.sh"


def write_config(path):
    path.write_text(
        "reference:\n  assembly: GRCh38\n"
        "region:\n  coding_only: false\n"
        "output:\n  format: vcf\n  compress: bgzip\n"
        "post_processing:\n  clinvar_aa_match:\n    enabled: false\n"
    )


def write_vcf(path, samples=1, chr1_length="248956422"):
    names = [f"S{i+1}" for i in range(samples)]
    columns = "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT"
    if names:
        columns += "\t" + "\t".join(names)
    record = "1\t100\t.\tA\tG\t.\tPASS\t.\tGT"
    if names:
        record += "\t" + "\t".join("0/1" for _ in names)
    path.write_text(
        "##fileformat=VCFv4.2\n"
        f"##contig=<ID=1,length={chr1_length}>\n"
        + columns + "\n" + record + "\n"
    )


def run(config, input_path, output_path, input_assembly=None):
    command = [
        "bash", str(PREFLIGHT), str(config), str(input_path), str(output_path),
    ]
    if input_assembly:
        command.extend(["--input-assembly", input_assembly])
    command.append("--dry-run")
    return subprocess.run(
        command,
        text=True,
        capture_output=True,
    )


def run_reference_checks(config, input_path, output_path):
    return subprocess.run(
        ["bash", str(PREFLIGHT), str(config), str(input_path), str(output_path)],
        text=True,
        capture_output=True,
    )


def test_valid_single_and_multi_sample(tmp_path):
    config = tmp_path / "config.yaml"; write_config(config)
    vcf = tmp_path / "in.vcf"; write_vcf(vcf)
    result = run(config, vcf, tmp_path / "out.vcf.gz")
    assert result.returncode == 0, result.stderr
    multi_vcf = tmp_path / "multi.vcf"; write_vcf(multi_vcf, samples=3)
    result = run(config, multi_vcf, tmp_path / "multi.out.vcf.gz")
    assert result.returncode == 0, result.stderr


def test_same_path_rejected(tmp_path):
    config = tmp_path / "config.yaml"; write_config(config)
    vcf = tmp_path / "in.vcf"; write_vcf(vcf, samples=2)
    result = run(config, vcf, vcf)
    assert result.returncode != 0
    assert "same path" in result.stderr


def test_wrong_assembly_length_rejected(tmp_path):
    config = tmp_path / "config.yaml"; write_config(config)
    vcf = tmp_path / "in.vcf"; write_vcf(vcf, chr1_length="249250621")
    result = run(config, vcf, tmp_path / "out.vcf.gz")
    assert result.returncode != 0
    assert "header indicates GRCh37" in result.stderr


def test_explicit_grch37_is_accepted_for_liftover_dry_run(tmp_path):
    config = tmp_path / "config.yaml"; write_config(config)
    vcf = tmp_path / "in.vcf"; write_vcf(vcf, chr1_length="249250621")
    result = run(config, vcf, tmp_path / "out.vcf.gz", "GRCh37")
    assert result.returncode == 0, result.stderr


def test_required_spliceai_snv_does_not_require_unconfigured_indel(tmp_path):
    cache = tmp_path / "cache"; cache.mkdir()
    fasta = tmp_path / "genome.fa.gz"; fasta.touch()
    pathlib.Path(str(fasta) + ".fai").touch()
    snv = tmp_path / "spliceai.snv.vcf.gz"; snv.touch()
    pathlib.Path(str(snv) + ".tbi").touch()
    config = tmp_path / "config.yaml"
    config.write_text(
        "reference:\n"
        "  assembly: GRCh38\n"
        f"  vep_cache_dir: {cache}\n"
        f"  fasta:\n    enabled: true\n    path: {fasta}\n"
        "region:\n  coding_only: false\n"
        "output:\n  format: vcf\n  compress: bgzip\n"
        "container:\n  runtime: definitely-not-a-container-runtime\n"
        "plugins:\n"
        "  SpliceAI:\n"
        "    enabled: true\n"
        "    required: true\n"
        f"    snv: {snv}\n"
        "post_processing:\n  clinvar_aa_match:\n    enabled: false\n"
    )
    vcf = tmp_path / "in.vcf"; write_vcf(vcf)
    result = run_reference_checks(config, vcf, tmp_path / "out.vcf.gz")
    assert "preflight data/config checks passed" in result.stderr
    assert "SpliceAI.indel" not in result.stderr


def test_required_spliceai_snv_missing_fails(tmp_path):
    cache = tmp_path / "cache"; cache.mkdir()
    config = tmp_path / "config.yaml"
    config.write_text(
        "reference:\n"
        "  assembly: GRCh38\n"
        f"  vep_cache_dir: {cache}\n"
        "region:\n  coding_only: false\n"
        "output:\n  format: vcf\n  compress: bgzip\n"
        "plugins:\n"
        "  SpliceAI:\n"
        "    enabled: true\n"
        "    required: true\n"
        f"    snv: {tmp_path / 'missing.vcf.gz'}\n"
        "post_processing:\n  clinvar_aa_match:\n    enabled: false\n"
    )
    vcf = tmp_path / "in.vcf"; write_vcf(vcf)
    result = run_reference_checks(config, vcf, tmp_path / "out.vcf.gz")
    assert result.returncode != 0
    assert "required indexed reference missing (SpliceAI.snv)" in result.stderr
    assert "SpliceAI.indel" not in result.stderr


def test_required_loftee_reference_missing_fails(tmp_path):
    cache = tmp_path / "cache"; cache.mkdir()
    config = tmp_path / "config.yaml"
    config.write_text(
        "reference:\n"
        "  assembly: GRCh38\n"
        f"  vep_cache_dir: {cache}\n"
        "region:\n  coding_only: false\n"
        "output:\n  format: vcf\n  compress: bgzip\n"
        "plugins:\n"
        "  LoF:\n"
        "    enabled: true\n"
        "    required: true\n"
        f"    human_ancestor_fa: {tmp_path / 'missing.ancestor.fa.gz'}\n"
        f"    conservation_file: {tmp_path / 'missing.loftee.sql'}\n"
        f"    gerp_bigwig: {tmp_path / 'missing.gerp.bw'}\n"
        "post_processing:\n  clinvar_aa_match:\n    enabled: false\n"
    )
    vcf = tmp_path / "in.vcf"; write_vcf(vcf)
    result = run_reference_checks(config, vcf, tmp_path / "out.vcf.gz")
    assert result.returncode != 0
    assert "required LOFTEE reference missing (human_ancestor_fa)" in result.stderr
    assert "required LOFTEE reference missing (conservation_file)" in result.stderr
    assert "required LOFTEE reference missing (gerp_bigwig)" in result.stderr


def test_required_ptc_50bp_gtf_missing_fails(tmp_path):
    cache = tmp_path / "cache"; cache.mkdir()
    fasta = tmp_path / "genome.fa.gz"; fasta.touch()
    pathlib.Path(str(fasta) + ".fai").touch()
    pathlib.Path(str(fasta) + ".gzi").touch()
    config = tmp_path / "config.yaml"
    config.write_text(
        "reference:\n"
        "  assembly: GRCh38\n"
        f"  vep_cache_dir: {cache}\n"
        f"  fasta:\n    enabled: true\n    path: {fasta}\n"
        "region:\n  coding_only: false\n"
        "output:\n  format: vcf\n  compress: bgzip\n"
        "container:\n"
        "  runtime: definitely-not-a-container-runtime\n"
        "  vep_image_tag: release_113.4\n"
        "post_processing:\n"
        "  clinvar_aa_match:\n    enabled: false\n"
        "  loftee_ptc_50bp:\n"
        "    enabled: true\n"
        "    required: true\n"
        f"    gtf: {tmp_path / 'missing.113.gtf.gz'}\n"
    )
    vcf = tmp_path / "in.vcf"; write_vcf(vcf)
    result = run_reference_checks(config, vcf, tmp_path / "out.vcf.gz")
    assert result.returncode != 0
    assert "LOFTEE PTC 50-bp GTF missing" in result.stderr


def test_generic_indexed_predictor_manifest_is_validated_from_registry(tmp_path):
    cache = tmp_path / "cache"; cache.mkdir()
    scores = tmp_path / "scores.tsv.gz"; scores.write_bytes(b"scores")
    pathlib.Path(str(scores) + ".tbi").write_bytes(b"index")
    manifest = tmp_path / "manifest.json"; manifest.write_text("{}\n")
    config = tmp_path / "config.yaml"
    config.write_text(
        "reference:\n"
        "  assembly: GRCh38\n"
        f"  vep_cache_dir: {cache}\n"
        "region:\n  coding_only: false\n"
        "output:\n  format: vcf\n  compress: bgzip\n"
        "container:\n  runtime: definitely-not-a-container-runtime\n"
        "plugins:\n"
        "  FuncVEP:\n"
        "    enabled: true\n"
        "    required: true\n"
        f"    file: {scores}\n"
        f"    manifest: {manifest}\n"
        "post_processing:\n  clinvar_aa_match:\n    enabled: false\n"
    )
    vcf = tmp_path / "in.vcf"; write_vcf(vcf)
    result = run_reference_checks(config, vcf, tmp_path / "out.vcf.gz")
    assert result.returncode != 0
    assert "FuncVEP manifest is invalid" in result.stderr
    assert "manifest_schema" in result.stderr


def test_indexed_plugin_runtime_check_is_not_funcvep_specific(_tmp_path=None):
    source = PREFLIGHT.read_text()
    assert "annotator.adapter is Adapter.GENERIC_INDEXED_LOOKUP" in source
    assert "plugins.FuncVEP.enabled" not in source
    assert 'if [[ "$GENERIC_INDEXED_ENABLED" == "true" ]]' in source
    assert "test -r /plugins/IndexedScores.pm" in source


if __name__ == "__main__":
    tests = [
        test_valid_single_and_multi_sample,
        test_same_path_rejected,
        test_wrong_assembly_length_rejected,
        test_explicit_grch37_is_accepted_for_liftover_dry_run,
        test_required_spliceai_snv_does_not_require_unconfigured_indel,
        test_required_spliceai_snv_missing_fails,
        test_required_loftee_reference_missing_fails,
        test_required_ptc_50bp_gtf_missing_fails,
        test_generic_indexed_predictor_manifest_is_validated_from_registry,
        test_indexed_plugin_runtime_check_is_not_funcvep_specific,
    ]
    for test in tests:
        with tempfile.TemporaryDirectory() as td:
            test(pathlib.Path(td))
        print(f"PASS  {test.__name__}")
    print(f"\n{len(tests)} tests passed")
