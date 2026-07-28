#!/usr/bin/env python3
import gzip
import pathlib
import subprocess
import tempfile


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "pipeline" / "contig_map.py"


def test_chr_to_ensembl_map(tmp_path):
    vcf = tmp_path / "input.vcf.gz"
    with gzip.open(vcf, "wt") as handle:
        handle.write(
            "##fileformat=VCFv4.2\n"
            "##contig=<ID=chr1,length=248956422>\n"
            "##contig=<ID=chrM,length=16569>\n"
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
            "chr1\t100\t.\tA\tG\t.\tPASS\t.\n"
        )
    bed = tmp_path / "regions.bed.gz"
    with gzip.open(bed, "wt") as handle:
        handle.write("1\t90\t110\n")
    output = tmp_path / "map.tsv"
    subprocess.run(
        ["python3", str(SCRIPT), "--vcf", str(vcf), "--bed", str(bed), "--output", str(output)],
        check=True,
    )
    assert output.read_text().splitlines() == ["chr1\t1", "chrM\tMT"]


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as directory:
        test_chr_to_ensembl_map(pathlib.Path(directory))
    print("PASS  test_chr_to_ensembl_map")
