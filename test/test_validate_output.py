#!/usr/bin/env python3
import pathlib
import sys
import tempfile

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from pipeline.validate_vep_output import validate


def write_config(path, required=True, spliceai=False, loftee=False):
    plugins = {
        "dbNSFP": {
            "enabled": True,
            "required": required,
            "columns": ["CADD_phred", "AlphaMissense_score"],
        }
    }
    if spliceai:
        plugins["SpliceAI"] = {"enabled": True, "required": required}
    if loftee:
        plugins["LoF"] = {"enabled": True, "required": required}
    path.write_text(yaml.safe_dump({
        "plugins": plugins
    }))


def write_vcf(path, fields):
    path.write_text(
        "##fileformat=VCFv4.2\n"
        '##INFO=<ID=CSQ,Number=.,Type=String,Description="Format: '
        + "|".join(fields)
        + '">\n'
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
    )


def test_required_dbnsfp_fields_present(tmp_path):
    config = tmp_path / "config.yaml"; write_config(config)
    vcf = tmp_path / "out.vcf"
    write_vcf(vcf, ["Allele", "CADD_phred", "AlphaMissense_score"])
    validate(config, vcf)


def test_required_dbnsfp_fields_missing_fails(tmp_path):
    config = tmp_path / "config.yaml"; write_config(config)
    vcf = tmp_path / "out.vcf"; write_vcf(vcf, ["Allele"])
    try:
        validate(config, vcf)
    except ValueError as error:
        assert "CADD_phred" in str(error) and "AlphaMissense_score" in str(error)
    else:
        raise AssertionError("missing required dbNSFP fields were accepted")


def test_required_spliceai_fields_present(tmp_path):
    config = tmp_path / "config.yaml"; write_config(config, spliceai=True)
    vcf = tmp_path / "out.vcf"
    write_vcf(vcf, [
        "Allele", "CADD_phred", "AlphaMissense_score",
        "SpliceAI_pred_DS_AG", "SpliceAI_pred_DS_AL",
        "SpliceAI_pred_DS_DG", "SpliceAI_pred_DS_DL",
    ])
    validate(config, vcf)


def test_required_spliceai_fields_missing_fails(tmp_path):
    config = tmp_path / "config.yaml"; write_config(config, spliceai=True)
    vcf = tmp_path / "out.vcf"
    write_vcf(vcf, ["Allele", "CADD_phred", "AlphaMissense_score"])
    try:
        validate(config, vcf)
    except ValueError as error:
        assert "required SpliceAI" in str(error)
        assert "SpliceAI_pred_DS_AG" in str(error)
    else:
        raise AssertionError("missing required SpliceAI fields were accepted")


def test_required_loftee_fields_present(tmp_path):
    config = tmp_path / "config.yaml"; write_config(config, loftee=True)
    vcf = tmp_path / "out.vcf"
    write_vcf(vcf, [
        "Allele", "CADD_phred", "AlphaMissense_score",
        "LoF", "LoF_filter", "LoF_flags", "LoF_info",
    ])
    validate(config, vcf)


def test_required_loftee_fields_missing_fails(tmp_path):
    config = tmp_path / "config.yaml"; write_config(config, loftee=True)
    vcf = tmp_path / "out.vcf"
    write_vcf(vcf, ["Allele", "CADD_phred", "AlphaMissense_score"])
    try:
        validate(config, vcf)
    except ValueError as error:
        assert "required LOFTEE" in str(error)
        assert "LoF_filter" in str(error)
    else:
        raise AssertionError("missing required LOFTEE fields were accepted")


if __name__ == "__main__":
    tests = [
        test_required_dbnsfp_fields_present,
        test_required_dbnsfp_fields_missing_fails,
        test_required_spliceai_fields_present,
        test_required_spliceai_fields_missing_fails,
        test_required_loftee_fields_present,
        test_required_loftee_fields_missing_fails,
    ]
    for test in tests:
        with tempfile.TemporaryDirectory() as directory:
            test(pathlib.Path(directory))
        print(f"PASS  {test.__name__}")
