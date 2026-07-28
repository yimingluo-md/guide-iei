#!/usr/bin/env python3
"""Tests for frameshift PTC-based replacement of LOFTEE's 50-bp rule."""

from __future__ import annotations

import pathlib
import re
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from pipeline.loftee_ptc_50bp import (
    Transcript,
    calculate,
    collect_transcripts,
    process_vcf,
)


def model() -> Transcript:
    return Transcript(
        transcript_id="ENST00000000001",
        chrom="1",
        version=1,
        strand=1,
        blocks=[(100, 117, 0)],
        cds="ATGAAACCCGGGTTTTAA",
        utr3="ATGATAATAA",
        last_coding_exon_cds=1,
        last_exon_junction_cds=100,
    )


def test_frameshift_is_scored_at_downstream_ptc():
    result = calculate(
        model(),
        102,
        "GA",
        "G",
        "frameshift_variant",
        "ENST00000000001.1:c.4del",
        50,
    )
    assert result["status"] == "ok"
    assert result["ptc_cds"] == 19
    assert result["ptc_aa"] == 7
    assert result["dist"] == 80
    assert result["rule"] == "PASS"


def test_successful_recomputation_replaces_lof_info_and_preserves_original(tmp_path):
    fields = [
        "Allele",
        "ALLELE_NUM",
        "Consequence",
        "Feature",
        "HGVSc",
        "HGVSp",
        "LoF",
        "LoF_info",
    ]
    csq = "|".join(
        [
            "G",
            "1",
            "frameshift_variant",
            "ENST00000000001",
            "ENST00000000001.1:c.4del",
            "ENSP1:p.Ala2GlyfsTer6",
            "HC",
            "DIST_FROM_LAST_EXON:10&50_BP_RULE:FAIL",
        ]
    )
    source = tmp_path / "input.vcf"
    output = tmp_path / "output.vcf"
    source.write_text(
        "##fileformat=VCFv4.2\n"
        '##INFO=<ID=CSQ,Number=.,Type=String,Description="Format: '
        + "|".join(fields)
        + '">\n'
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
        f"1\t102\t.\tGA\tG\t.\tPASS\tCSQ={csq}\n"
    )
    report = process_vcf(
        source,
        output,
        {"ENST00000000001": model()},
        fields,
        50,
        True,
        {"assembly": "GRCh38", "ensembl_release": "test"},
    )
    text = output.read_text()
    header = next(line for line in text.splitlines() if line.startswith("##INFO=<ID=CSQ"))
    output_fields = re.search(r"Format: ([^\">]+)", header).group(1).split("|")
    record = next(line for line in text.splitlines() if not line.startswith("#"))
    raw_csq = dict(item.split("=", 1) for item in record.split("\t")[7].split(";"))["CSQ"]
    annotation = dict(zip(output_fields, raw_csq.split("|")))

    assert "50_BP_RULE:PASS" in annotation["LoF_info"]
    assert annotation["LoF_50_BP_RULE_original"] == "FAIL"
    assert annotation["LoF_50_BP_RULE_PTC"] == "PASS"
    assert annotation["LoF_50_BP_RULE_changed"] == "1"
    assert annotation["PTC_dist_from_last_exon"] == "80"
    assert annotation["PTC_calc_status"] == "ok"
    assert report["changed"] == 1
    assert report["hgvsp_fsTer_validation"] == {"checked": 1, "matched": 1}

    second = tmp_path / "second.vcf"
    _, second_fields = collect_transcripts(output)
    process_vcf(
        output,
        second,
        {"ENST00000000001": model()},
        second_fields,
        50,
        True,
        {"assembly": "GRCh38", "ensembl_release": "test"},
    )
    second_header = next(
        line for line in second.read_text().splitlines()
        if line.startswith("##INFO=<ID=CSQ")
    )
    fields_again = re.search(r"Format: ([^\">]+)", second_header).group(1).split("|")
    second_record = next(
        line for line in second.read_text().splitlines() if not line.startswith("#")
    )
    second_csq = dict(
        item.split("=", 1) for item in second_record.split("\t")[7].split(";")
    )["CSQ"]
    annotation_again = dict(zip(fields_again, second_csq.split("|")))
    assert annotation_again["LoF_50_BP_RULE_original"] == "FAIL"
    assert annotation_again["LoF_50_BP_RULE_PTC"] == "PASS"


def test_version_mismatch_is_refused():
    result = calculate(
        model(),
        102,
        "GA",
        "G",
        "frameshift_variant",
        "ENST00000000001.2:c.4del",
        50,
    )
    assert result["status"] == "transcript_version_mismatch"
    assert result["rule"] == ""


if __name__ == "__main__":
    tests = [
        test_frameshift_is_scored_at_downstream_ptc,
        test_successful_recomputation_replaces_lof_info_and_preserves_original,
        test_version_mismatch_is_refused,
    ]
    for test in tests:
        with tempfile.TemporaryDirectory() as directory:
            if "tmp_path" in test.__code__.co_varnames:
                test(pathlib.Path(directory))
            else:
                test()
        print(f"PASS  {test.__name__}")
