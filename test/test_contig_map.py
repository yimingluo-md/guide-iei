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


if __name__ == "__main__":
    for test in (
        test_chr_to_ensembl_map,
        test_headerless_vcf_falls_back_to_record_contigs,
        test_ensembl_to_ucsc_does_not_double_prefix_mixed_headers,
        test_matching_styles_write_an_empty_map,
    ):
        with tempfile.TemporaryDirectory() as directory:
            test(pathlib.Path(directory))
        print(f"PASS  {test.__name__}")
