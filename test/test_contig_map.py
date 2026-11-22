#!/usr/bin/env python3
import gzip
import pathlib
import subprocess
import tempfile


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "pipeline" / "contig_map.py"


def run_map(tmp_path, vcf_text, bed_text):
    vcf = tmp_path / "input.vcf.gz"
    with gzip.open(vcf, "wt") as handle:
        handle.write(vcf_text)
    bed = tmp_path / "regions.bed.gz"
    with gzip.open(bed, "wt") as handle:
        handle.write(bed_text)
    output = tmp_path / "map.tsv"
    subprocess.run(
        ["python3", str(SCRIPT), "--vcf", str(vcf), "--bed", str(bed), "--output", str(output)],
        check=True,
    )
    return output.read_text().splitlines()


def test_chr_to_ensembl_map(tmp_path):
    lines = run_map(
        tmp_path,
        "##fileformat=VCFv4.2\n"
        "##contig=<ID=chr1,length=248956422>\n"
        "##contig=<ID=chrM,length=16569>\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
        "chr1\t100\t.\tA\tG\t.\tPASS\t.\n",
        "1\t90\t110\n",
    )
    assert lines == ["chr1\t1", "chrM\tMT"]


def test_headerless_vcf_falls_back_to_record_contigs(tmp_path):
    # Audit repro (AUX-C1): chr-style records with no ##contig headers used
    # to yield a zero-byte map, which the driver read as "no rename needed",
    # after which the Ensembl-style region filter matched nothing and the
    # run finished cleanly with an empty callset.
    lines = run_map(
        tmp_path,
        "##fileformat=VCFv4.2\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
        "chr1\t100\t.\tA\tG\t.\tPASS\t.\n"
        "chr2\t200\t.\tC\tT\t.\tPASS\t.\n"
        "chrM\t300\t.\tG\tA\t.\tPASS\t.\n",
        "1\t90\t110\n",
    )
    assert lines == ["chr1\t1", "chr2\t2", "chrM\tMT"]


def test_ensembl_to_ucsc_does_not_double_prefix_mixed_headers(tmp_path):
    # Audit repro (AUX-H1): a merged header carrying ID=1, ID=chr2, ID=MT
    # previously produced "chr2 -> chrchr2" plus identity mappings, silently
    # renaming chr2 onto a contig present in no reference or BED.
    lines = run_map(
        tmp_path,
        "##fileformat=VCFv4.2\n"
        "##contig=<ID=1,length=248956422>\n"
        "##contig=<ID=chr2,length=242193529>\n"
        "##contig=<ID=MT,length=16569>\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
        "1\t100\t.\tA\tG\t.\tPASS\t.\n",
        "chr1\t90\t110\n",
    )
    assert lines == ["1\tchr1", "MT\tchrM"]


def test_matching_styles_write_an_empty_map(tmp_path):
    lines = run_map(
        tmp_path,
        "##fileformat=VCFv4.2\n"
        "##contig=<ID=1,length=248956422>\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
        "1\t100\t.\tA\tG\t.\tPASS\t.\n",
        "1\t90\t110\n",
    )
    assert lines == []


def test_undeclared_mitochondrial_record_and_breakend_are_preserved(tmp_path):
    vcf = tmp_path / "fixture.vcf"
    vcf.write_text(
        "##fileformat=VCFv4.3\n##contig=<ID=1>\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS\n"
        "1\t5\t.\tA\tA[chrM:7[\t.\tPASS\tCHR2=chrM\tGT\t0/1\n"
        "chrM\t7\t.\tC\tG\t.\tPASS\t.\tGT\t1\n")
    base = ["python3", str(SCRIPT), "--vcf", str(vcf), "--output", str(tmp_path / "map"),
            "--extra-header", str(tmp_path / "headers")]
    bed = tmp_path / "regions.bed"
    bed.write_text("1\t0\t10\nMT\t0\t10\n")
    for target in ([], ["--bed", str(bed)]):
        subprocess.run(base + target + ["--observed-stdin"], input="1\nchrM\n", text=True, check=True)
        result = subprocess.check_output(base + ["--apply"], text=True)
        assert result.count("##contig=<ID=MT>") == 1
        assert "chrM" not in result
        assert "A[MT:7[\t.\tPASS\tCHR2=MT\tGT\t0/1" in result
        assert "MT\t7\t.\tC\tG\t.\tPASS\t.\tGT\t1" in result


def test_colliding_contigs_stop_before_annotation(tmp_path):
    path = tmp_path / "mixed.vcf"
    path.write_text("##contig=<ID=1>\n##contig=<ID=chr1>\n#CHROM\tPOS\nchr1\t100\n")
    output = tmp_path / "map"
    result = subprocess.run(["python3", str(SCRIPT), "--vcf", str(path), "--output", str(output)],
                            capture_output=True, text=True)
    assert result.returncode != 0 and "both map to 1" in result.stderr
    assert not output.exists()


if __name__ == "__main__":
    for test in (
        test_chr_to_ensembl_map,
        test_headerless_vcf_falls_back_to_record_contigs,
        test_ensembl_to_ucsc_does_not_double_prefix_mixed_headers,
        test_matching_styles_write_an_empty_map,
        test_undeclared_mitochondrial_record_and_breakend_are_preserved,
        test_colliding_contigs_stop_before_annotation,
    ):
        with tempfile.TemporaryDirectory() as directory:
            test(pathlib.Path(directory))
        print(f"PASS  {test.__name__}")
