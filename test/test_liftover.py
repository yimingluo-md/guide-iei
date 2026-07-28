#!/usr/bin/env python3
import gzip
import json
import pathlib
import subprocess
import tempfile


ROOT = pathlib.Path(__file__).resolve().parents[1]


def gzip_copy(source: pathlib.Path, target: pathlib.Path):
    with source.open("rb") as incoming, gzip.open(target, "wb") as outgoing:
        outgoing.write(incoming.read())


def test_prepare_and_qc_preserve_all_input_records(tmp_path):
    input_vcf = tmp_path / "legacy.vcf"
    input_vcf.write_text(
        "##fileformat=VCFv4.2\n"
        "##reference=GRCh37\n"
        "##contig=<ID=1,length=249250621>\n"
        '##INFO=<ID=MLEAC,Number=A,Type=Integer,Description="test">\n'
        '##INFO=<ID=DP,Number=1,Type=Integer,Description="test">\n'
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\n"
        "1\t100\trs1\tA\tG\t99\tPASS\tMLEAC=1,1;DP=20\tGT\t0/1\n"
        "1\t200\t.\tC\t<DEL>\t99\tPASS\t.\tGT\t0/1\n"
        "GL000207.1\t300\t.\tG\tA\t99\tPASS\t.\tGT\t0/1\n"
    )
    supported = tmp_path / "supported.vcf"
    unsupported = tmp_path / "unsupported.vcf"
    stats = tmp_path / "pre.json"
    subprocess.run(
        [
            "python3", str(ROOT / "pipeline" / "prepare_liftover_vcf.py"),
            "--input", str(input_vcf),
            "--supported", str(supported),
            "--unsupported", str(unsupported),
            "--stats", str(stats),
            "--max-allele-length", "50",
        ],
        check=True,
    )
    pre = json.loads(stats.read_text())
    assert pre["total_records"] == 3
    assert pre["supported_records"] == 1
    assert pre["unsupported_records"] == 2
    assert pre["unsupported_reasons"] == {
        "NON_PRIMARY_CONTIG": 1,
        "SYMBOLIC_OR_BREAKEND": 1,
    }
    assert pre["records_with_removed_malformed_info"] == 1
    assert pre["removed_malformed_info_fields"] == {"MLEAC": 1}
    supported_text = supported.read_text()
    assert "##iei_original_reference=GRCh37" in supported_text
    assert "IEI_ORIGINAL_POS=100" in supported_text
    assert "IEI_ORIGINAL_RECORD=1" in supported_text
    assert "MLEAC=" not in supported_text
    assert "DP=20" in supported_text

    lifted = tmp_path / "lifted.vcf.gz"
    lifted_text = supported_text.replace(
        "#CHROM\tPOS", "##reference=GRCh38\n##iei_target_assembly=GRCh38\n#CHROM\tPOS"
    ).replace("1\t100\trs1", "1\t120\trs1")
    with gzip.open(lifted, "wt") as handle:
        handle.write(lifted_text)
    unsupported_gz = tmp_path / "unsupported.vcf.gz"
    gzip_copy(unsupported, unsupported_gz)
    reject = tmp_path / "picard-reject.vcf.gz"
    with gzip.open(reject, "wt") as handle:
        handle.write(
            "##fileformat=VCFv4.2\n"
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
        )
    chain = tmp_path / "hg19ToHg38.over.chain.gz"
    chain.write_bytes(b"chain")
    dictionary = tmp_path / "GRCh38.dict"
    dictionary.write_text("@HD\tVN:1.6\n")
    qc = tmp_path / "qc.json"
    provenance = tmp_path / "provenance.json"
    subprocess.run(
        [
            "python3", str(ROOT / "pipeline" / "write_liftover_qc.py"),
            "--input", str(input_vcf),
            "--lifted", str(lifted),
            "--picard-reject", str(reject),
            "--unsupported", str(unsupported_gz),
            "--pre-stats", str(stats),
            "--chain", str(chain),
            "--target-dict", str(dictionary),
            "--qc-output", str(qc),
            "--provenance-output", str(provenance),
            "--picard-version", "TEST",
        ],
        check=True,
    )
    report = json.loads(qc.read_text())
    assert report["attempted_records"] == 3
    assert report["lifted_records"] == 1
    assert report["unsupported_records"] == 2
    assert report["all_records_accounted_for"]
    assert report["records_with_removed_malformed_info"] == 1
    assert report["removed_malformed_info_fields"] == {"MLEAC": 1}
    assert "Malformed allele-indexed INFO values" in report["warnings"][0]
    details = json.loads(provenance.read_text())
    assert details["chain"]["sha256"]
    assert details["policy"]["unlifted_records_are_not_interpreted_as_reference"]


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as directory:
        test_prepare_and_qc_preserve_all_input_records(pathlib.Path(directory))
    print("1 test passed")
