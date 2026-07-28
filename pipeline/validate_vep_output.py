#!/usr/bin/env python3
"""Validate that required annotation sources are present in VEP output."""

from __future__ import annotations

import argparse
import gzip
import re
from pathlib import Path

import yaml


def csq_fields(vcf_path: Path) -> list[str]:
    opener = gzip.open if vcf_path.name.endswith(".gz") else open
    with opener(vcf_path, "rt") as handle:
        for line in handle:
            if line.startswith("##INFO=<ID=CSQ"):
                match = re.search(r"Format:\s*([^\">]+)", line)
                if not match:
                    raise ValueError("CSQ header has no Format field list")
                return match.group(1).strip().split("|")
            if line.startswith("#CHROM"):
                break
    raise ValueError("VEP CSQ header was not found")


def validate(config_path: Path, vcf_path: Path) -> None:
    config = yaml.safe_load(config_path.read_text()) or {}
    fields = set(csq_fields(vcf_path))
    dbnsfp = ((config.get("plugins") or {}).get("dbNSFP") or {})
    if dbnsfp.get("enabled") and dbnsfp.get("required"):
        expected = set(dbnsfp.get("columns") or [])
        missing = sorted(expected - fields)
        if missing:
            raise ValueError(
                "required dbNSFP annotations are absent from CSQ: "
                + ", ".join(missing)
            )

    loftee = ((config.get("plugins") or {}).get("LoF") or {})
    if loftee.get("enabled") and loftee.get("required"):
        expected = {"LoF", "LoF_filter", "LoF_flags", "LoF_info"}
        missing = sorted(expected - fields)
        if missing:
            raise ValueError(
                "required LOFTEE annotations are absent from CSQ: "
                + ", ".join(missing)
            )

    spliceai = ((config.get("plugins") or {}).get("SpliceAI") or {})
    if spliceai.get("enabled") and spliceai.get("required"):
        expected = {
            "SpliceAI_pred_DS_AG",
            "SpliceAI_pred_DS_AL",
            "SpliceAI_pred_DS_DG",
            "SpliceAI_pred_DS_DL",
        }
        missing = sorted(expected - fields)
        if missing:
            raise ValueError(
                "required SpliceAI annotations are absent from CSQ: "
                + ", ".join(missing)
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--vcf", required=True, type=Path)
    args = parser.parse_args()
    validate(args.config, args.vcf)
    print("required VEP output annotations validated")


if __name__ == "__main__":
    main()
