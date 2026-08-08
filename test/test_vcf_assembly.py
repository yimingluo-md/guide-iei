#!/usr/bin/env python3
import gzip
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from pipeline.vcf_assembly import detect_vcf_assembly, resolve_input_assembly


def write_vcf(path: pathlib.Path, header: str = ""):
    text = (
        "##fileformat=VCFv4.2\n"
        + header
        + "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\n"
        + "1\t100\t.\tA\tG\t99\tPASS\t.\tGT\t0/1\n"
    )
    if path.name.endswith(".gz"):
        with gzip.open(path, "wt") as handle:
            handle.write(text)
    else:
        path.write_text(text)


def test_detects_both_assemblies_and_pipeline_marker(tmp_path):
    grch37 = tmp_path / "37.vcf"
    write_vcf(grch37, "##contig=<ID=1,length=249250621>\n")
    assert detect_vcf_assembly(grch37)["assembly"] == "GRCh37"

    grch38 = tmp_path / "38.vcf.gz"
    write_vcf(grch38, "##reference=hg38\n")
    assert detect_vcf_assembly(grch38)["assembly"] == "GRCh38"

    lifted = tmp_path / "lifted.vcf"
    write_vcf(
        lifted,
        "##iei_target_assembly=GRCh38\n"
        "##iei_liftover=<SourceAssembly=GRCh37/hg19,TargetAssembly=GRCh38>\n"
        "##iei_original_reference=GRCh37\n",
    )
    result = detect_vcf_assembly(lifted)
    assert result["assembly"] == "GRCh38"
    assert result["lifted_from_grch37"]


def test_lifted_output_with_stale_contig_length_is_reannotatable(tmp_path):
    # Audit repro (SH-10 / AUX-M2): the pipeline's own lifted GRCh38 output
    # carries ##reference=GRCh38 + the liftover marker + the GRCh37 chr1
    # length= that bcftools +liftover does not rewrite. This used to resolve
    # as "conflict" and raise for auto AND for an explicit GRCh38 request,
    # making the pipeline's own deliverable un-reannotatable.
    lifted = tmp_path / "lifted-stale-length.vcf"
    write_vcf(
        lifted,
        "##reference=GRCh38\n"
        "##iei_target_assembly=GRCh38\n"
        "##iei_liftover=<SourceAssembly=GRCh37/hg19,TargetAssembly=GRCh38>\n"
        "##contig=<ID=1,length=249250621>\n",
    )
    detected = detect_vcf_assembly(lifted)
    assert detected["assembly"] == "GRCh38"
    assert detected["confidence"] == "high"
    assert any("stale length" in warning for warning in detected["warnings"])
    assert resolve_input_assembly(lifted, "auto")["resolved"] == "GRCh38"
    assert resolve_input_assembly(lifted, "GRCh38")["resolved"] == "GRCh38"


def test_genuine_declared_conflict_still_raises(tmp_path):
    # A reference header contradicting the marker is a real inconsistency and
    # must stay fatal — the stale-length exemption applies only when every
    # declared source agrees with the marker.
    conflicted = tmp_path / "declared-conflict.vcf"
    write_vcf(
        conflicted,
        "##reference=GRCh37\n"
        "##iei_target_assembly=GRCh38\n",
    )
    assert detect_vcf_assembly(conflicted)["assembly"] == "conflict"
    try:
        resolve_input_assembly(conflicted, "auto")
        raise AssertionError("declared conflict should fail")
    except ValueError as exc:
        assert "conflicting" in str(exc)

    lengths_only = tmp_path / "length-conflict.vcf"
    write_vcf(
        lengths_only,
        "##reference=GRCh38\n"
        "##contig=<ID=1,length=249250621>\n",
    )
    assert detect_vcf_assembly(lengths_only)["assembly"] == "conflict"


def test_auto_requires_evidence_and_explicit_choice_checks_conflicts(tmp_path):
    unknown = tmp_path / "unknown.vcf"
    write_vcf(unknown)
    try:
        resolve_input_assembly(unknown, "auto")
        raise AssertionError("ambiguous input should fail")
    except ValueError as exc:
        assert "ambiguous" in str(exc)
    assert resolve_input_assembly(unknown, "GRCh38")["resolved"] == "GRCh38"

    grch37 = tmp_path / "37.vcf"
    write_vcf(grch37, "##reference=GRCh37\n")
    try:
        resolve_input_assembly(grch37, "GRCh38")
        raise AssertionError("conflicting explicit choice should fail")
    except ValueError as exc:
        assert "indicates GRCh37" in str(exc)


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as directory:
        root = pathlib.Path(directory)
        test_detects_both_assemblies_and_pipeline_marker(root)
        test_lifted_output_with_stale_contig_length_is_reannotatable(root)
        test_genuine_declared_conflict_still_raises(root)
        test_auto_requires_evidence_and_explicit_choice_checks_conflicts(root)
    print("4 tests passed")
