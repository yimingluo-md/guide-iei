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
        '##FORMAT=<ID=GT,Number=1,Type=String,Description="genotype">\n'
        '##FORMAT=<ID=GP,Number=G,Type=Float,Description="probabilities">\n'
        '##FORMAT=<ID=PL,Number=G,Type=Integer,Description="likelihoods">\n'
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\n"
        "1\t100\trs1\tA\tG\t99\tPASS\tMLEAC=1,1;DP=20\tGT:GP:PL\t"
        "0/1:0.1,0.9:30,0,40\n"
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
    assert pre["records_with_removed_malformed_format"] == 1
    assert pre["removed_malformed_format_fields"] == {"GP": 1}
    supported_text = supported.read_text()
    assert "##iei_original_reference=GRCh37" in supported_text
    assert "IEI_ORIGINAL_POS=100" in supported_text
    assert "IEI_ORIGINAL_RECORD=1" in supported_text
    assert "MLEAC=" not in supported_text
    assert "DP=20" in supported_text
    assert "GT:PL\t0/1:30,0,40" in supported_text
    assert "GT:GP:PL" not in supported_text

    lifted_all = tmp_path / "lifted-all.vcf.gz"
    lifted_text = supported_text.replace(
        "#CHROM\tPOS", "##reference=GRCh38\n##iei_target_assembly=GRCh38\n#CHROM\tPOS"
    ).replace("1\t100\trs1", "1\t120\trs1")
    with gzip.open(lifted_all, "wt") as handle:
        handle.write(lifted_text)
    retained_plain = tmp_path / "retained.vcf"
    correction_plain = tmp_path / "corrections.vcf"
    classification = tmp_path / "classification.json"
    subprocess.run(
        [
            "python3", str(ROOT / "pipeline" / "classify_liftover_records.py"),
            "--input", str(lifted_all),
            "--retained", str(retained_plain),
            "--reference-corrections", str(correction_plain),
            "--stats", str(classification),
        ],
        check=True,
    )
    lifted = tmp_path / "lifted.vcf.gz"
    corrections = tmp_path / "corrections.vcf.gz"
    gzip_copy(retained_plain, lifted)
    gzip_copy(correction_plain, corrections)
    unsupported_gz = tmp_path / "unsupported.vcf.gz"
    gzip_copy(unsupported, unsupported_gz)
    reject = tmp_path / "liftover-reject.vcf.gz"
    with gzip.open(reject, "wt") as handle:
        handle.write(
            "##fileformat=VCFv4.2\n"
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
        )
    chain = tmp_path / "hg19ToHg38.over.chain.gz"
    chain.write_bytes(b"chain")
    dictionary = tmp_path / "GRCh38.dict"
    dictionary.write_text("@HD\tVN:1.6\n")
    source_fasta = tmp_path / "hg19.fa.gz"
    source_fasta.write_bytes(b"source")
    target_fasta = tmp_path / "GRCh38.fa.gz"
    target_fasta.write_bytes(b"target")
    qc = tmp_path / "qc.json"
    provenance = tmp_path / "provenance.json"
    subprocess.run(
        [
            "python3", str(ROOT / "pipeline" / "write_liftover_qc.py"),
            "--input", str(input_vcf),
            "--lifted-all", str(lifted_all),
            "--lifted", str(lifted),
            "--reference-corrections", str(corrections),
            "--liftover-reject", str(reject),
            "--unsupported", str(unsupported_gz),
            "--pre-stats", str(stats),
            "--classification-stats", str(classification),
            "--chain", str(chain),
            "--source-fasta", str(source_fasta),
            "--target-fasta", str(target_fasta),
            "--target-dict", str(dictionary),
            "--qc-output", str(qc),
            "--provenance-output", str(provenance),
            "--bcftools-version", "TEST",
            "--plugin-commit", "TEST-COMMIT",
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
    assert report["records_with_removed_malformed_format"] == 1
    assert report["removed_malformed_format_fields"] == {"GP": 1}
    assert any(
        "Malformed allele-indexed INFO values" in warning
        for warning in report["warnings"]
    )
    assert any(
        "Malformed allele-indexed FORMAT values" in warning
        for warning in report["warnings"]
    )
    details = json.loads(provenance.read_text())
    assert details["chain"]["sha256"]
    assert details["policy"]["unlifted_records_are_not_interpreted_as_reference"]
    assert details["tool"]["name"] == "BCFtools/liftover"


def test_reference_alt_becoming_grch38_ref_is_audited_not_retained(tmp_path):
    lifted = tmp_path / "lifted.vcf"
    lifted.write_text(
        "##fileformat=VCFv4.2\n"
        '##INFO=<ID=IEI_LIFTOVER_SWAP,Number=1,Type=Integer,Description="swap">\n'
        '##INFO=<ID=SRC_CHROM,Number=1,Type=String,Description="source chrom">\n'
        '##INFO=<ID=SRC_POS,Number=1,Type=Integer,Description="source pos">\n'
        '##INFO=<ID=SRC_REF_ALT,Number=.,Type=String,Description="source alleles">\n'
        '##INFO=<ID=IEI_ORIGINAL_RECORD,Number=1,Type=Integer,Description="ordinal">\n'
        '##FORMAT=<ID=GT,Number=1,Type=String,Description="genotype">\n'
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\n"
        "11\t67997692\trs5792426\tAG\tA\t99\tPASS\t"
        "IEI_LIFTOVER_SWAP=1;SRC_CHROM=11;SRC_POS=67765163;"
        "SRC_REF_ALT=A,AG;IEI_ORIGINAL_RECORD=1\tGT\t0/0\n"
        "11\t68000000\ttrue-variant\tC\tT\t99\tPASS\t"
        "IEI_LIFTOVER_SWAP=1;SRC_CHROM=11;SRC_POS=67767471;"
        "SRC_REF_ALT=T,C;IEI_ORIGINAL_RECORD=2\tGT\t0/1\n"
        "11\t68001000\tordinary\tG\tA\t99\tPASS\t"
        "SRC_CHROM=11;SRC_POS=67768471;SRC_REF_ALT=G,A;"
        "IEI_ORIGINAL_RECORD=3\tGT\t0/1\n"
        "11\t68002000\tnew-ref-a\tC\tA\t99\tPASS\t"
        "IEI_LIFTOVER_SWAP=-1;SRC_CHROM=11;SRC_POS=67769471;"
        "SRC_REF_ALT=A,G;IEI_ORIGINAL_RECORD=4\tGT\t0/1\n"
        "11\t68002000\tnew-ref-g\tC\tG\t99\tPASS\t"
        "IEI_LIFTOVER_SWAP=-1;SRC_CHROM=11;SRC_POS=67769471;"
        "SRC_REF_ALT=A,G;IEI_ORIGINAL_RECORD=4\tGT\t0/1\n"
    )
    retained = tmp_path / "retained.vcf"
    corrections = tmp_path / "corrections.vcf"
    stats = tmp_path / "stats.json"
    subprocess.run(
        [
            "python3", str(ROOT / "pipeline" / "classify_liftover_records.py"),
            "--input", str(lifted),
            "--retained", str(retained),
            "--reference-corrections", str(corrections),
            "--stats", str(stats),
        ],
        check=True,
    )
    retained_text = retained.read_text()
    correction_text = corrections.read_text()
    assert "rs5792426" not in retained_text
    assert "rs5792426" in correction_text
    assert "IEI_REFERENCE_CORRECTION" in correction_text
    assert "true-variant" in retained_text
    assert "IEI_ASSEMBLY_ALLELE_SWAP" in retained_text
    assert "ordinary" in retained_text
    report = json.loads(stats.read_text())
    assert report["raw_lifted_allele_records"] == 5
    assert report["retained_allele_records"] == 4
    assert report["reference_correction_allele_records"] == 1
    assert report["raw_lifted_source_allele_records"] == 4
    assert report["new_reference_records"] == 2
    assert report["new_reference_source_allele_records"] == 1


def test_prepare_removes_only_number_g_fields_incompatible_with_liftover(tmp_path):
    input_vcf = tmp_path / "haploid.vcf"
    input_vcf.write_text(
        "##fileformat=VCFv4.2\n"
        "##reference=GRCh37\n"
        "##contig=<ID=X,length=155270560>\n"
        '##FORMAT=<ID=GT,Number=1,Type=String,Description="genotype">\n'
        '##FORMAT=<ID=AD,Number=R,Type=Integer,Description="depths">\n'
        '##FORMAT=<ID=DP,Number=1,Type=Integer,Description="depth">\n'
        '##FORMAT=<ID=GQ,Number=1,Type=Integer,Description="quality">\n'
        '##FORMAT=<ID=GP,Number=G,Type=Float,Description="probabilities">\n'
        '##FORMAT=<ID=PL,Number=G,Type=Integer,Description="likelihoods">\n'
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\n"
        "X\t100\t.\tA\tG\t99\tPASS\t.\tGT:AD:DP:GQ:GP:PL\t"
        "1:0,20:20:80:80,1e-8:100,0\n"
    )
    supported = tmp_path / "supported.vcf"
    unsupported = tmp_path / "unsupported.vcf"
    stats = tmp_path / "stats.json"
    subprocess.run(
        [
            "python3", str(ROOT / "pipeline" / "prepare_liftover_vcf.py"),
            "--input", str(input_vcf),
            "--supported", str(supported),
            "--unsupported", str(unsupported),
            "--stats", str(stats),
        ],
        check=True,
    )
    text = supported.read_text()
    assert "GT:AD:DP:GQ\t1:0,20:20:80" in text
    assert ":GP" not in text
    assert ":PL" not in text
    report = json.loads(stats.read_text())
    assert report["records_with_removed_malformed_format"] == 0
    assert report["records_with_removed_liftover_incompatible_format"] == 1
    assert report["removed_liftover_incompatible_format_fields"] == {
        "GP": 1,
        "PL": 1,
    }


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as directory:
        test_prepare_and_qc_preserve_all_input_records(pathlib.Path(directory))
    with tempfile.TemporaryDirectory() as directory:
        test_reference_alt_becoming_grch38_ref_is_audited_not_retained(
            pathlib.Path(directory)
        )
    with tempfile.TemporaryDirectory() as directory:
        test_prepare_removes_only_number_g_fields_incompatible_with_liftover(
            pathlib.Path(directory)
        )
    print("3 tests passed")
