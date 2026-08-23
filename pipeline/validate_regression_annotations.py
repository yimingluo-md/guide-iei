#!/usr/bin/env python3
"""Validate a VEP output VCF against the public annotation regression panel."""

from __future__ import annotations

import argparse
import gzip
import json
import re
from pathlib import Path

import yaml


MISSING = {"", ".", "-"}


def open_text(path: Path):
    return gzip.open(path, "rt") if path.name.endswith(".gz") else path.open()


def parse_info(raw: str) -> dict[str, str]:
    result = {}
    for item in raw.split(";"):
        if "=" in item:
            key, value = item.split("=", 1)
            result[key] = value
        elif item:
            result[item] = "1"
    return result


def load_vcf(path: Path) -> tuple[list[str], dict[str, dict]]:
    fields = None
    records = {}
    with open_text(path) as handle:
        for line in handle:
            if line.startswith("##INFO=<ID=CSQ,"):
                match = re.search(r"Format:\s*([^\">]+)", line)
                if not match:
                    raise ValueError("CSQ header has no Format field list")
                fields = match.group(1).strip().split("|")
                continue
            if line.startswith("#"):
                continue
            if fields is None:
                raise ValueError("VEP CSQ header was not found")
            columns = line.rstrip("\n").split("\t")
            info = parse_info(columns[7])
            entries = []
            for raw in info.get("CSQ", "").split(","):
                if not raw:
                    continue
                values = raw.split("|")
                values += [""] * (len(fields) - len(values))
                entries.append(dict(zip(fields, values)))
            # Index per ALT: a multi-allelic record keyed on the raw comma-
            # joined ALT column ("...-A,AGG") can never match a single-allele
            # expected.yaml key, producing a false "variant missing" FAIL.
            # Entries are attributed to their ALT via ALLELE_NUM — handing
            # every entry to every ALT let one allele's annotation make a
            # different allele pass. Entries without ALLELE_NUM (legacy
            # runs) still count toward every ALT.
            # On a duplicate key (e.g. a lifted record landing twice), merge
            # the CSQ entries instead of silently discarding the first.
            alts = columns[4].split(",")
            for alt_index, alt in enumerate(alts):
                allele_number = str(alt_index + 1)
                own_entries = [
                    entry for entry in entries
                    if not entry.get("ALLELE_NUM")
                    or entry["ALLELE_NUM"] == allele_number
                ]
                key = f"{columns[0]}-{columns[1]}-{columns[3]}-{alt}"
                if key in records:
                    records[key]["entries"].extend(own_entries)
                else:
                    records[key] = {"info": info, "entries": own_entries}
    if fields is None:
        raise ValueError("VEP CSQ header was not found")
    return fields, records


def consequences(entry: dict[str, str]) -> set[str]:
    return set(entry.get("Consequence", "").split("&"))


def nonempty(value: str | None) -> bool:
    return value is not None and value not in MISSING


def numeric_values(entry: dict[str, str], field: str) -> list[float]:
    values = []
    for raw in re.split(r"[&,]", entry.get(field, "")):
        try:
            values.append(float(raw))
        except ValueError:
            pass
    return values


def validate_contract(expected: dict, config: dict) -> list[dict]:
    contract = expected.get("resource_contract") or {}
    actual = {
        "assembly": (config.get("reference") or {}).get("assembly"),
        "dbNSFP": (((config.get("plugins") or {}).get("dbNSFP") or {}).get("version")),
        "LoGoFunc": (((config.get("plugins") or {}).get("LoGoFunc") or {}).get("version")),
        "vep_image_tag": (config.get("container") or {}).get("vep_image_tag"),
    }
    results = []
    for name, wanted in contract.items():
        got = actual.get(name)
        results.append(
            {
                "id": f"resource_contract.{name}",
                "status": "PASS" if str(got) == str(wanted) else "FAIL",
                "message": f"expected {wanted}; observed {got}",
            }
        )
    return results


def validate_variant(spec: dict, csq_fields: list[str], records: dict[str, dict]) -> dict:
    ident = spec["id"]
    key = spec["key"]
    record = records.get(key)
    if record is None:
        return {"id": ident, "status": "FAIL", "message": f"variant missing: {key}"}

    entries = record["entries"]
    gene = spec.get("gene")
    if gene:
        entries = [entry for entry in entries if entry.get("SYMBOL") == gene]
    if not entries:
        return {
            "id": ident,
            "status": "FAIL",
            "message": f"no CSQ entry for gene {gene or '(any)'}",
        }

    wanted_consequences = set(spec.get("consequence_any") or [])
    if wanted_consequences:
        entries = [
            entry for entry in entries if consequences(entry) & wanted_consequences
        ]
        if not entries:
            return {
                "id": ident,
                "status": "FAIL",
                "message": "expected consequence absent: "
                + ", ".join(sorted(wanted_consequences)),
            }

    problems = []
    skips = []
    for field in spec.get("fields_nonempty") or []:
        if field not in csq_fields:
            problems.append(f"CSQ field absent: {field}")
        elif not any(nonempty(entry.get(field)) for entry in entries):
            problems.append(f"field empty: {field}")

    for field, allowed in (spec.get("field_in") or {}).items():
        if field not in csq_fields:
            problems.append(f"CSQ field absent: {field}")
        elif not any(entry.get(field) in set(map(str, allowed)) for entry in entries):
            observed = sorted({entry.get(field, "") for entry in entries})
            problems.append(f"{field} expected one of {allowed}; observed {observed}")

    optional_field_in = spec.get("optional_field_in") or {}
    if optional_field_in:
        installed = [field for field in optional_field_in if field in csq_fields]
        if not installed:
            skips.append(
                "none installed: " + ", ".join(optional_field_in)
            )
        else:
            missing = [field for field in optional_field_in if field not in csq_fields]
            if missing:
                problems.append(
                    "partially installed optional fields; absent: " + ", ".join(missing)
                )
            for field in installed:
                allowed = set(map(str, optional_field_in[field]))
                if not any(entry.get(field) in allowed for entry in entries):
                    observed = sorted({entry.get(field, "") for entry in entries})
                    problems.append(
                        f"optional {field} expected one of "
                        f"{optional_field_in[field]}; observed {observed}"
                    )

    numeric = spec.get("any_numeric_field_at_least")
    if numeric:
        numeric_fields = numeric.get("fields") or []
        threshold = float(numeric["value"])
        if not all(field in csq_fields for field in numeric_fields):
            missing = [field for field in numeric_fields if field not in csq_fields]
            problems.append("CSQ fields absent: " + ", ".join(missing))
        else:
            observed = [
                value
                for entry in entries
                for field in numeric_fields
                for value in numeric_values(entry, field)
            ]
            if not observed or max(observed) < threshold:
                problems.append(
                    f"maximum {numeric_fields} below {threshold}; "
                    f"observed {max(observed) if observed else 'none'}"
                )

    clinvar_term = spec.get("clinvar_contains")
    if clinvar_term:
        values = [entry.get("ClinVar_CLNSIG", "") for entry in entries]
        if not any(clinvar_term.lower() in value.lower() for value in values):
            problems.append(
                f"ClinVar_CLNSIG lacks {clinvar_term}; observed {sorted(set(values))}"
            )

    for field, expected_value in (spec.get("info_equals") or {}).items():
        observed = record["info"].get(field)
        if str(observed) != str(expected_value):
            problems.append(f"INFO/{field} expected {expected_value}; observed {observed}")

    for field in spec.get("optional_fields_nonempty") or []:
        if field not in csq_fields:
            skips.append(f"{field} not installed")
        elif not any(nonempty(entry.get(field)) for entry in entries):
            problems.append(f"optional installed field is empty: {field}")

    optional_any = spec.get("optional_any_field_nonempty") or []
    if optional_any:
        installed = [field for field in optional_any if field in csq_fields]
        if not installed:
            skips.append("none installed: " + ", ".join(optional_any))
        elif not any(
            nonempty(entry.get(field)) for entry in entries for field in installed
        ):
            problems.append(
                "installed optional fields are empty: " + ", ".join(installed)
            )

    if problems:
        return {"id": ident, "status": "FAIL", "message": "; ".join(problems)}
    if skips:
        return {"id": ident, "status": "SKIP", "message": "; ".join(skips)}
    return {"id": ident, "status": "PASS", "message": key}


def run_validation(config_path: Path, expected_path: Path, vcf_path: Path) -> dict:
    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    expected = yaml.safe_load(expected_path.read_text(encoding="utf-8")) or {}
    csq_fields, records = load_vcf(vcf_path)
    checks = validate_contract(expected, config)
    checks.extend(
        validate_variant(spec, csq_fields, records)
        for spec in expected.get("variants") or []
    )
    failed = sum(check["status"] == "FAIL" for check in checks)
    return {
        "schema_version": 1,
        "status": "FAIL" if failed else "PASS",
        "vcf": str(vcf_path.resolve()),
        "checks": checks,
        "counts": {
            status: sum(check["status"] == status for check in checks)
            for status in ("PASS", "FAIL", "SKIP")
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--expected", required=True, type=Path)
    parser.add_argument("--vcf", required=True, type=Path)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()
    report = run_validation(args.config, args.expected, args.vcf)
    for check in report["checks"]:
        print(f"{check['status']:4}  {check['id']}: {check['message']}")
    print(
        "regression: "
        f"{report['status']} ({report['counts']['PASS']} pass, "
        f"{report['counts']['FAIL']} fail, {report['counts']['SKIP']} skip)"
    )
    if args.json:
        args.json.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    return 1 if report["status"] == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
