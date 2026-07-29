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
        test_auto_requires_evidence_and_explicit_choice_checks_conflicts(root)
    print("2 tests passed")
