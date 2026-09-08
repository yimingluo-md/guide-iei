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


def _run_prepare(tmp_path, header_extra, records, convention="auto"):
    input_vcf = tmp_path / "mt.vcf"
    input_vcf.write_text(
        "##fileformat=VCFv4.2\n"
        + header_extra
        + "##contig=<ID=1,length=249250621>\n"
        '##FORMAT=<ID=GT,Number=1,Type=String,Description="genotype">\n'
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\n"
        + records
    )
    supported = tmp_path / "supported.vcf"
    unsupported = tmp_path / "unsupported.vcf"
    passthrough = tmp_path / "mt-passthrough.vcf"
    stats = tmp_path / "pre.json"
    subprocess.run(
        [
            "python3", str(ROOT / "pipeline" / "prepare_liftover_vcf.py"),
            "--input", str(input_vcf),
            "--supported", str(supported),
            "--unsupported", str(unsupported),
            "--passthrough", str(passthrough),
            "--mt-convention", convention,
            "--stats", str(stats),
        ],
        check=True,
    )
    return input_vcf, supported, passthrough, json.loads(stats.read_text())


def test_rcrs_mitochondrial_records_bypass_the_hg19_chain(tmp_path):
    """Audit repro (H3): b37/rCRS MT records were sent through the hg19
    (NC_001807) chain. With a declared 16,569 bp MT contig they must be
    carried over unchanged into the passthrough file, not the plugin input."""
    records = (
        "1\t100\trs1\tA\tG\t99\tPASS\t.\tGT\t0/1\n"
        "MT\t313\t.\tC\tA\t99\tPASS\t.\tGT\t1\n"
        "chrM\t3107\t.\tN\tA,C\t99\tPASS\t.\tGT\t1/2\n"
    )
    _, supported, passthrough, pre = _run_prepare(
        tmp_path, "##reference=b37\n##contig=<ID=MT,length=16569>\n", records
    )
    assert pre["mt_convention"] == "rcrs"
    assert pre["mt_convention_source"] == "contig_length_16569"
    assert pre["mt_contig_length"] == 16569
    assert pre["mt_passthrough_records"] == 2
    assert pre["mt_passthrough_allele_records"] == 3
    # Passthrough records are still SUPPORTED (accounted) records.
    assert pre["supported_records"] == 3
    assert pre["supported_allele_records"] == 4
    supported_text = supported.read_text()
    assert "\nMT\t" not in supported_text, "no rCRS MT record may reach the chain"
    passthrough_text = passthrough.read_text()
    assert "##contig=<ID=MT,length=16569>" in passthrough_text
    assert "##INFO=<ID=IEI_MT_PASSTHROUGH" in passthrough_text
    body = [line for line in passthrough_text.splitlines() if not line.startswith("#")]
    assert len(body) == 2
    assert body[0].startswith("MT\t313\t.\tC\tA\t")
    assert "IEI_MT_PASSTHROUGH" in body[0]
    assert "IEI_ORIGINAL_POS=313" in body[0]
    assert "IEI_ORIGINAL_RECORD=2" in body[0]
    # chrM is normalised to MT; the position is untouched.
    assert body[1].startswith("MT\t3107\t.\tN\tA,C\t")
    assert "IEI_ORIGINAL_CHROM=chrM" in body[1]


def test_hg19_mitochondrial_records_still_go_through_the_chain(tmp_path):
    records = "MT\t313\t.\tC\tA\t99\tPASS\t.\tGT\t1\n"
    _, supported, passthrough, pre = _run_prepare(
        tmp_path, "##contig=<ID=MT,length=16571>\n", records
    )
    assert pre["mt_convention"] == "hg19"
    assert pre["mt_convention_source"] == "contig_length_16571"
    assert pre["mt_passthrough_records"] == 0
    assert "\nMT\t313\t" in supported.read_text()
    assert not [l for l in passthrough.read_text().splitlines() if not l.startswith("#")]


def test_mt_convention_falls_back_to_reference_name_then_rcrs(tmp_path):
    records = "MT\t313\t.\tC\tA\t99\tPASS\t.\tGT\t1\n"
    _, supported, passthrough, pre = _run_prepare(
        tmp_path, "##reference=file:///refs/ucsc.hg19.fasta\n", records
    )
    assert (pre["mt_convention"], pre["mt_convention_source"]) == ("hg19", "reference_name")
    assert "\nMT\t313\t" in supported.read_text()

    _, supported, passthrough, pre = _run_prepare(
        tmp_path, "##reference=file:///refs/human_g1k_v37.fasta\n", records
    )
    assert (pre["mt_convention"], pre["mt_convention_source"]) == ("rcrs", "reference_name")
    assert pre["mt_passthrough_records"] == 1

    # Nothing to go on: rCRS is assumed and the QC reports it as a default.
    _, supported, passthrough, pre = _run_prepare(tmp_path, "", records)
    assert (pre["mt_convention"], pre["mt_convention_source"]) == ("rcrs", "default")
    assert pre["mt_passthrough_records"] == 1

    # An explicit setting always wins over header evidence.
    _, supported, passthrough, pre = _run_prepare(
        tmp_path, "##contig=<ID=MT,length=16569>\n", records, convention="hg19"
    )
    assert (pre["mt_convention"], pre["mt_convention_source"]) == ("hg19", "configured")
    assert pre["mt_passthrough_records"] == 0


def test_mt_passthrough_reconciles_in_the_liftover_qc(tmp_path):
    """The QC accounting must treat passthrough alleles as lifted alleles:
    attempted == lifted + rejected + unsupported still holds."""
    records = (
        "1\t100\trs1\tA\tG\t99\tPASS\t.\tGT\t0/1\n"
        "MT\t313\t.\tC\tA\t99\tPASS\t.\tGT\t1\n"
    )
    input_vcf, supported, passthrough, pre = _run_prepare(
        tmp_path, "##contig=<ID=MT,length=16569>\n", records
    )
    # Emulate the script: the plugin output (record 1, lifted to 120) is
    # concatenated with the verified passthrough records before classification.
    lifted_text = supported.read_text().replace(
        "#CHROM\tPOS", "##reference=GRCh38\n##iei_target_assembly=GRCh38\n#CHROM\tPOS"
    ).replace("1\t100\trs1", "1\t120\trs1")
    mt_body = [l for l in passthrough.read_text().splitlines() if not l.startswith("#")]
    lifted_all = tmp_path / "lifted-all.vcf.gz"
    with gzip.open(lifted_all, "wt") as handle:
        handle.write(lifted_text + "\n".join(mt_body) + "\n")
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
    retained_body = [l for l in retained_plain.read_text().splitlines() if not l.startswith("#")]
    assert any(l.startswith("MT\t313\t") and "IEI_MT_PASSTHROUGH" in l for l in retained_body)
    lifted = tmp_path / "lifted.vcf.gz"
    corrections = tmp_path / "corrections.vcf.gz"
    gzip_copy(retained_plain, lifted)
    gzip_copy(correction_plain, corrections)
    unsupported_gz = tmp_path / "unsupported.vcf.gz"
    gzip_copy(tmp_path / "unsupported.vcf", unsupported_gz)
    reject = tmp_path / "liftover-reject.vcf.gz"
    with gzip.open(reject, "wt") as handle:
        handle.write("##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n")
    for name in ("hg19ToHg38.over.chain.gz", "hg19.fa.gz", "GRCh38.fa.gz"):
        (tmp_path / name).write_bytes(b"x")
    (tmp_path / "GRCh38.dict").write_text("@HD\tVN:1.6\n")
    qc = tmp_path / "qc.json"
    provenance = tmp_path / "provenance.json"
    subprocess.run(
        [
            "python3", str(ROOT / "pipeline" / "write_liftover_qc.py"),
            "--input", str(input_vcf), "--lifted-all", str(lifted_all),
            "--lifted", str(lifted), "--reference-corrections", str(corrections),
            "--liftover-reject", str(reject), "--unsupported", str(unsupported_gz),
            "--pre-stats", str(tmp_path / "pre.json"),
            "--classification-stats", str(classification),
            "--chain", str(tmp_path / "hg19ToHg38.over.chain.gz"),
            "--source-fasta", str(tmp_path / "hg19.fa.gz"),
            "--target-fasta", str(tmp_path / "GRCh38.fa.gz"),
            "--target-dict", str(tmp_path / "GRCh38.dict"),
            "--qc-output", str(qc), "--provenance-output", str(provenance),
            "--bcftools-version", "TEST", "--plugin-commit", "TEST-COMMIT",
            "--mt-convention-setting", "auto",
        ],
        check=True,
    )
    report = json.loads(qc.read_text())
    assert report["all_records_accounted_for"], report
    assert report["attempted_records"] == 2
    assert report["lifted_records"] == 2
    assert report["mt_convention"] == "rcrs"
    assert report["mt_passthrough_records"] == 1
    assert any("carried over without the hg19 chain" in w for w in report["warnings"])
    details = json.loads(provenance.read_text())
    assert details["policy"]["mt_convention_setting"] == "auto"
    assert details["policy"]["mt_convention"] == "rcrs"
    assert details["policy"]["rcrs_mitochondrial_records_bypass_chain_after_grch38_verification"]


def test_mt_passthrough_survives_bcftools_split_verify_and_concat(tmp_path):
    """Real htslib check for the script's passthrough lane: the passthrough
    VCF must sort, split, REF-verify against a GRCh38 MT FASTA and concat
    with the plugin output without header conflicts; a REF that does not
    match GRCh38 MT (an NC_001807 callset mislabelled as rCRS) must abort."""
    import shutil
    if not (shutil.which("bcftools") and shutil.which("bgzip")):
        print("  (skipped: bcftools/bgzip not on PATH)")
        return
    records = (
        "1\t100\trs1\tA\tG\t99\tPASS\t.\tGT\t0/1\n"
        "MT\t4\t.\tC\tA,T\t99\tPASS\t.\tGT\t1/2\n"
    )
    _, supported, passthrough, pre = _run_prepare(
        tmp_path, "##contig=<ID=MT,length=16569>\n", records
    )
    assert pre["mt_passthrough_records"] == 1
    # A tiny "GRCh38" FASTA whose MT starts GATCACAG (rCRS): position 4 is C.
    mt_sequence = "GATCACAGGTCTATCACCCTATTAACCACTCACGGGAGCTCTCCATGCATTTGGTATTTT"
    fasta = tmp_path / "GRCh38.fa"
    fasta.write_text(">1\n" + "A" * 200 + "\n>MT\n" + mt_sequence + "\n")
    (tmp_path / "GRCh38.fa.fai").write_text(
        f"1\t200\t3\t200\t201\nMT\t{len(mt_sequence)}\t208\t{len(mt_sequence)}\t{len(mt_sequence) + 1}\n"
    )

    def run(*cmd):
        subprocess.run(list(cmd), check=True, cwd=tmp_path)

    passthrough_text = passthrough.read_text()
    run("bgzip", "-f", str(passthrough))
    run("bcftools", "sort", "-O", "z", "-o", "mt-sorted.vcf.gz", f"{passthrough}.gz")
    run("bcftools", "norm", "-m", "-any", "-O", "z", "-o", "mt-split.vcf.gz", "mt-sorted.vcf.gz")
    run("bcftools", "norm", "-f", str(fasta), "--check-ref", "e", "-O", "z",
        "-o", "mt-verified.vcf.gz", "mt-split.vcf.gz")
    # Plugin-like output carrying the GRCh38 MT contig line.
    lifted_text = supported.read_text().replace(
        "#CHROM\tPOS", "##contig=<ID=MT,length=16569>\n#CHROM\tPOS"
    ).replace("1\t100\trs1", "1\t120\trs1")
    with gzip.open(tmp_path / "lifted.vcf.gz", "wt") as handle:
        handle.write(lifted_text)
    run("bcftools", "concat", "--no-version", "-O", "z", "-o", "combined.vcf.gz",
        "lifted.vcf.gz", "mt-verified.vcf.gz")
    run("bcftools", "sort", "-O", "z", "-o", "all.vcf.gz", "combined.vcf.gz")
    with gzip.open(tmp_path / "all.vcf.gz", "rt") as handle:
        body = [line.rstrip("\n") for line in handle if not line.startswith("#")]
    assert len(body) == 3, body
    mt = [line for line in body if line.startswith("MT\t")]
    assert [line.split("\t")[1:5] for line in mt] == [["4", ".", "C", "A"], ["4", ".", "C", "T"]]
    assert all("IEI_MT_PASSTHROUGH" in line for line in mt)

    wrong = tmp_path / "wrong.vcf"
    wrong.write_text(passthrough_text.replace("MT\t4\t.\tC\tA,T", "MT\t4\t.\tG\tA,T"))
    run("bgzip", "-f", str(wrong))
    failed = subprocess.run(
        ["bcftools", "norm", "-f", str(fasta), "--check-ref", "e", "-O", "z",
         "-o", "wrong-verified.vcf.gz", f"{wrong}.gz"],
        cwd=tmp_path, capture_output=True,
    )
    assert failed.returncode != 0, "a non-rCRS REF must abort verification"


def test_liftover_working_copies_are_removed_on_every_exit(tmp_path):
    """Audit M13: the conversion writes several whole-callset working copies
    beside the lifted VCF. Each must be listed for removal by the EXIT trap
    that is installed before the first of them is written; the audit
    artefacts the documentation promises must not be."""
    script = (ROOT / "scripts" / "liftover_grch37_to_grch38.sh").read_text()
    listing = script.split("LIFTOVER_WORK_FILES=(", 1)[1].split(")", 1)[0]
    disposable = [
        "SUPPORTED", "SOURCE_SORTED", "SPLIT", "RAW_LIFTED", "NORMALIZED_UNSORTED",
        "MT_PASSTHROUGH", "MT_PASSTHROUGH_SORTED", "MT_PASSTHROUGH_SPLIT",
        "MT_PASSTHROUGH_VERIFIED", "COMBINED_UNSORTED", "LIFTED_ALL", "RETAINED",
        "REFERENCE_CORRECTIONS_RAW", "HEADER",
    ]
    for name in disposable:
        assert f'"${name}"' in listing, f"{name} is not removed by the cleanup trap"
    for kept in ("LIFTOVER_REJECT", "UNSUPPORTED_GZ", "REFERENCE_CORRECTIONS", "QC", "PROVENANCE", "OUTPUT"):
        assert f'"${kept}"' not in listing, f"{kept} is an audit artefact and must be kept"
    trap_at = script.index("trap cleanup_liftover_work_files EXIT")
    first_write = script.index('python3 "${ROOT}/pipeline/prepare_liftover_vcf.py"')
    assert trap_at < first_write, "the cleanup trap must be armed before the first working copy is written"
    # The trap is armed after the cache-reuse exit, which must not delete a
    # neighbouring run's files.
    assert script.index('log "reusing cached hg19->GRCh38 conversion') < trap_at
    # And the function really removes what it lists.
    prefixes = (
        "SUPPORTED=", "UNSUPPORTED=", "UNSUPPORTED_GZ=", "PRE_STATS=", "SOURCE_SORTED=",
        "SPLIT=", "RAW_LIFTED=", "LIFTOVER_REJECT=", "NORMALIZED_UNSORTED=", "MT_PASSTHROUGH",
        "COMBINED_UNSORTED=", "LIFTED_ALL=", "RETAINED=", "REFERENCE_CORRECTIONS",
        "CLASSIFICATION_STATS=", "HEADER=",
    )
    definition_block = script.split('WORKDIR="$(dirname "$OUTPUT")"', 1)[1].split("LIFTOVER_WORK_FILES=(", 1)[0]
    definitions = [line for line in definition_block.splitlines() if line.startswith(prefixes)]
    function_body = script.split("cleanup_liftover_work_files() {", 1)[1].split("\n}\n", 1)[0]
    probe = tmp_path / "probe.sh"
    probe.write_text(
        "set -euo pipefail\n"
        + f'WORKDIR="{tmp_path}"; BASE="x"\n'
        + "\n".join(definitions) + "\n"
        + "LIFTOVER_WORK_FILES=(" + listing + ")\n"
        + 'LIFTOVER_SCRATCH="$WORKDIR/.liftover-work.probe"; mkdir -p "$LIFTOVER_SCRATCH"\n'
        + "cleanup_liftover_work_files() {" + function_body + "\n}\n"
        + 'for f in "${LIFTOVER_WORK_FILES[@]}" "$LIFTOVER_REJECT" "$UNSUPPORTED_GZ" "$REFERENCE_CORRECTIONS"; do : > "$f"; done\n'
        + "cleanup_liftover_work_files\n"
    )
    subprocess.run(["bash", str(probe)], check=True)
    remaining = sorted(p.name for p in tmp_path.iterdir() if p.name != "probe.sh")
    assert remaining == [
        "x.liftover-reference-corrections.vcf.gz",
        "x.liftover-rejected.vcf.gz",
        "x.liftover-unsupported.vcf.gz",
    ], remaining


if __name__ == "__main__":
    # Discover every module-level test function so a new test can never be
    # silently left out of the CI run by a hand-maintained list.
    names = sorted(name for name in globals() if name.startswith("test_"))
    for name in names:
        with tempfile.TemporaryDirectory() as directory:
            globals()[name](pathlib.Path(directory))
    print(f"{len(names)} tests passed")
