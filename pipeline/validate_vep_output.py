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
            if line.startswith("##INFO=<ID=CSQ,"):
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
    enabled_gpn_fields = {
        f"GPN{model}_{field}" for key, model in (("gpn_star_m", "m447"), ("gpn_star_v", "v100"))
        if ((config.get("wgs_review") or {}).get(key) or {}).get("enabled")
        for field in ("LLR", "ABSLLR")
    }
    if enabled_gpn_fields:
        opener = gzip.open if vcf_path.suffix == ".gz" else open
        with opener(vcf_path, "rt") as handle:
            for line in handle:
                if line.startswith("#CHROM"):
                    break
                match = re.match(r"##INFO=<ID=([^,]+),Number=A,Type=Float,", line)
                if match:
                    enabled_gpn_fields.discard(match.group(1))
        if enabled_gpn_fields:
            raise ValueError("Missing GPN-Star allele INFO fields: " + ", ".join(sorted(enabled_gpn_fields)))

    dbnsfp = ((config.get("plugins") or {}).get("dbNSFP") or {})
    if dbnsfp.get("enabled") and dbnsfp.get("required"):
        columns = dbnsfp.get("columns") or []
        expected = {columns} if isinstance(columns, str) else set(columns)
        if "ALL" in expected:
            # VEP expands the wildcard into actual CSQ fields.
            if __package__:
                from .predictor_registry import load_registry
            else:
                from predictor_registry import load_registry
            registry_fields = {m.field for p in load_registry().predictors if p.resource_id == "dbnsfp"
                               for m in p.metrics}
            if not fields.intersection(registry_fields):
                raise ValueError("required dbNSFP annotations are absent from CSQ")
            expected.remove("ALL")
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
