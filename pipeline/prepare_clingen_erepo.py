#!/usr/bin/env python3
"""Prepare the ClinGen Evidence Repository classification export for local use.

The official download contains one row per allele-disease-MOI assertion.  This
builder never collapses those rows to a single "strongest" classification.  It
resolves assertions to exact GRCh38 alleles using the current ClinVar VCF first,
then genomic HGVS expressions.  Full assertion text is stored in SQLite while a
small allele VCF carries stable identifiers and core assertion metadata.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote


EXPECTED_COLUMNS = {
    "ClinVar Variation Id", "Allele Registry Id", "HGVS Expressions",
    "HGNC Gene Symbol", "Disease", "Mondo Id", "Mode of Inheritance",
    "Assertion", "Applied Evidence Codes (Met)",
    "Applied Evidence Codes (Not Met)", "Summary of interpretation",
    "PubMed Articles", "Expert Panel", "Guideline", "Approval Date",
    "Published Date", "Retracted", "Evidence Repo Link", "Uuid",
}
GRCH38_ACCESSIONS = {
    "NC_000001.11": "1", "NC_000002.12": "2", "NC_000003.12": "3",
    "NC_000004.12": "4", "NC_000005.10": "5", "NC_000006.12": "6",
    "NC_000007.14": "7", "NC_000008.11": "8", "NC_000009.12": "9",
    "NC_000010.11": "10", "NC_000011.10": "11", "NC_000012.12": "12",
    "NC_000013.11": "13", "NC_000014.9": "14", "NC_000015.10": "15",
    "NC_000016.10": "16", "NC_000017.11": "17", "NC_000018.10": "18",
    "NC_000019.10": "19", "NC_000020.11": "20", "NC_000021.9": "21",
    "NC_000022.11": "22", "NC_000023.11": "X", "NC_000024.10": "Y",
    "NC_012920.1": "MT",
}
CHANGE_RE = re.compile(r"^(\d+)(?:_(\d+))?(delins|del|dup|ins)([ACGT]*)$")
SUB_RE = re.compile(r"^(\d+)([ACGT]+)>([ACGT]+)$")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_rows(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        fields = reader.fieldnames or []
        missing = EXPECTED_COLUMNS - set(fields)
        if missing:
            raise ValueError("ClinGen export is missing columns: " + ", ".join(sorted(missing)))
        rows = [{key: (value or "").strip() for key, value in row.items()} for row in reader]
    if not rows:
        raise ValueError("ClinGen export contains no classification rows")
    uuids = [row["Uuid"] for row in rows]
    if any(not value for value in uuids) or len(set(uuids)) != len(uuids):
        raise ValueError("ClinGen export UUIDs are blank or duplicated")
    return rows, fields


def grch38_hgvs(row: dict[str, str]) -> tuple[str, str] | None:
    for expression in row["HGVS Expressions"].split(","):
        if ":g." not in expression:
            continue
        accession, change = expression.split(":g.", 1)
        chrom = GRCH38_ACCESSIONS.get(accession)
        if chrom:
            return chrom, change
    return None


def scan_clinvar(path: Path, rows: list[dict[str, str]]) -> tuple[dict[str, tuple[str, int, str, str]], dict[str, tuple[str, int, str, str]]]:
    wanted_ids = {row["ClinVar Variation Id"] for row in rows if row["ClinVar Variation Id"]}
    wanted_caids = {row["Allele Registry Id"] for row in rows if row["Allele Registry Id"]}
    by_id: dict[str, tuple[str, int, str, str]] = {}
    by_caid: dict[str, tuple[str, int, str, str]] = {}
    opener = gzip.open if path.name.endswith(".gz") else open
    with opener(path, "rt") as handle:
        for line in handle:
            if line.startswith("#"):
                continue
            columns = line.rstrip("\n").split("\t")
            if len(columns) < 8 or "," in columns[4]:
                continue
            allele = (columns[0].removeprefix("chr"), int(columns[1]), columns[3], columns[4])
            if columns[2] in wanted_ids:
                by_id[columns[2]] = allele
            match = re.search(r"(?:^|;)CLNVI=([^;]+)", columns[7])
            if match:
                for item in match.group(1).split("|"):
                    if item.startswith("ClinGen:") and item[8:] in wanted_caids:
                        by_caid[item[8:]] = allele
    return by_id, by_caid


def parse_fasta_regions(path: Path | None) -> dict[tuple[str, int, int], str]:
    if path is None or not path.is_file() or path.stat().st_size == 0:
        return {}
    result: dict[tuple[str, int, int], str] = {}
    key = None
    sequence: list[str] = []
    with path.open(encoding="ascii") as handle:
        for line in handle:
            if line.startswith(">"):
                if key is not None:
                    result[key] = "".join(sequence).upper()
                label = line[1:].split()[0].removeprefix("chr")
                match = re.fullmatch(r"([^:]+):(\d+)-(\d+)", label)
                key = (match.group(1), int(match.group(2)), int(match.group(3))) if match else None
                sequence = []
            elif key is not None:
                sequence.append(line.strip())
    if key is not None:
        result[key] = "".join(sequence).upper()
    return result


def region_for(chrom: str, change: str) -> tuple[str, int, int] | None:
    match = CHANGE_RE.fullmatch(change)
    if not match:
        return None
    start, end = int(match.group(1)), int(match.group(2) or match.group(1))
    return chrom, max(1, start - 250), max(start, end)


def base_from_region(regions: dict[tuple[str, int, int], str], key: tuple[str, int, int], pos: int) -> str:
    sequence = regions.get(key, "")
    offset = pos - key[1]
    if offset < 0 or offset >= len(sequence):
        raise ValueError(f"reference base {key[0]}:{pos} is outside fetched region")
    return sequence[offset]


def sequence_from_region(regions: dict[tuple[str, int, int], str], key: tuple[str, int, int], start: int, end: int) -> str:
    sequence = regions.get(key, "")
    left, right = start - key[1], end - key[1] + 1
    if left < 0 or right > len(sequence):
        raise ValueError(f"reference interval {key[0]}:{start}-{end} is outside fetched region")
    return sequence[left:right]


def normalize_allele(chrom: str, pos: int, ref: str, alt: str, regions, region_key):
    # Minimal representation plus repeat-aware left alignment.  The 250-bp
    # upstream window is ample for ordinary short clinical indels; exhaustion
    # is recorded as an unmapped assertion rather than accepting a fuzzy match.
    while len(ref) > 1 and len(alt) > 1 and ref[-1] == alt[-1]:
        ref, alt = ref[:-1], alt[:-1]
    while len(ref) > 1 and len(alt) > 1 and ref[0] == alt[0]:
        ref, alt, pos = ref[1:], alt[1:], pos + 1
    while len(ref) != len(alt) and ref[-1] == alt[-1]:
        if pos <= region_key[1]:
            raise ValueError("left-normalization exceeded fetched reference window")
        previous = base_from_region(regions, region_key, pos - 1)
        ref, alt, pos = previous + ref[:-1], previous + alt[:-1], pos - 1
        while len(ref) > 1 and len(alt) > 1 and ref[-1] == alt[-1]:
            ref, alt = ref[:-1], alt[:-1]
    return chrom, pos, ref, alt


def allele_from_hgvs(row, regions) -> tuple[str, int, str, str] | None:
    parsed = grch38_hgvs(row)
    if not parsed:
        return None
    chrom, change = parsed
    substitution = SUB_RE.fullmatch(change)
    if substitution:
        return chrom, int(substitution.group(1)), substitution.group(2), substitution.group(3)
    match = CHANGE_RE.fullmatch(change)
    if not match:
        return None
    start, end = int(match.group(1)), int(match.group(2) or match.group(1))
    operation, inserted = match.group(3), match.group(4)
    key = region_for(chrom, change)
    if key is None or key not in regions or start <= 1:
        return None
    if operation == "del":
        anchor = base_from_region(regions, key, start - 1)
        ref = anchor + sequence_from_region(regions, key, start, end)
        return normalize_allele(chrom, start - 1, ref, anchor, regions, key)
    if operation == "delins" and inserted:
        anchor = base_from_region(regions, key, start - 1)
        ref = anchor + sequence_from_region(regions, key, start, end)
        return normalize_allele(chrom, start - 1, ref, anchor + inserted, regions, key)
    if operation == "ins" and inserted:
        anchor = base_from_region(regions, key, start)
        return normalize_allele(chrom, start, anchor, anchor + inserted, regions, key)
    if operation == "dup":
        duplicated = sequence_from_region(regions, key, start, end)
        anchor = base_from_region(regions, key, end)
        return normalize_allele(chrom, end, anchor, anchor + duplicated, regions, key)
    return None


def safe(value: str) -> str:
    return quote(value or "", safe="")


def write_sqlite(path: Path, rows, mappings, metadata) -> None:
    if path.exists():
        path.unlink()
    connection = sqlite3.connect(path)
    try:
        connection.executescript("""
            PRAGMA journal_mode=DELETE;
            CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE assertions (
              uuid TEXT PRIMARY KEY, chrom TEXT, pos INTEGER, ref TEXT, alt TEXT,
              variation TEXT, clinvar_variation_id TEXT, caid TEXT,
              hgvs_expressions TEXT, gene TEXT, disease TEXT, mondo_id TEXT,
              mode_of_inheritance TEXT, assertion TEXT, evidence_met TEXT,
              evidence_not_met TEXT, interpretation_summary TEXT, pubmed TEXT,
              expert_panel TEXT, guideline TEXT, approval_date TEXT,
              published_date TEXT, retracted TEXT, evidence_repo_link TEXT,
              active INTEGER NOT NULL, mapping_method TEXT, mapping_error TEXT
            );
            CREATE INDEX assertions_allele ON assertions(chrom,pos,ref,alt,active);
            CREATE INDEX assertions_caid ON assertions(caid);
        """)
        connection.executemany("INSERT INTO metadata(key,value) VALUES(?,?)", metadata.items())
        values = []
        for row in rows:
            mapped = mappings.get(row["Uuid"])
            allele = mapped[0] if mapped else (None, None, None, None)
            values.append((
                row["Uuid"], *allele, row.get("Variation", ""),
                row["ClinVar Variation Id"], row["Allele Registry Id"],
                row["HGVS Expressions"], row["HGNC Gene Symbol"], row["Disease"],
                row["Mondo Id"], row["Mode of Inheritance"], row["Assertion"],
                row["Applied Evidence Codes (Met)"], row["Applied Evidence Codes (Not Met)"],
                row["Summary of interpretation"], row["PubMed Articles"],
                row["Expert Panel"], row["Guideline"], row["Approval Date"],
                row["Published Date"], row["Retracted"], row["Evidence Repo Link"],
                int(row["Retracted"].lower() != "true"),
                mapped[1] if mapped else "", mapped[2] if mapped else "unresolved exact GRCh38 allele",
            ))
        connection.executemany(
            "INSERT INTO assertions VALUES(" + ",".join("?" for _ in range(27)) + ")", values,
        )
        connection.commit()
        result = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if result != "ok":
            raise ValueError(f"ClinGen SQLite integrity check failed: {result}")
    finally:
        connection.close()


def write_vcf(path: Path, rows, mappings, generated: str, source_hash: str) -> int:
    grouped = defaultdict(list)
    by_uuid = {row["Uuid"]: row for row in rows}
    for uuid, (allele, _method, _error) in mappings.items():
        row = by_uuid[uuid]
        if row["Retracted"].lower() == "true":
            continue
        grouped[allele].append(row)
    order = {str(i): i for i in range(1, 23)} | {"X": 23, "Y": 24, "MT": 25}
    with path.open("w", encoding="utf-8", newline="") as out:
        out.write("##fileformat=VCFv4.2\n")
        out.write(f"##fileDate={generated[:10].replace('-', '')}\n")
        out.write("##source=ClinGen_Evidence_Repository_local_snapshot\n")
        out.write(f"##ClinGenERepoSourceSHA256={source_hash}\n")
        out.write('##INFO=<ID=ClinGen_ERepo,Number=.,Type=String,Description="Active ClinGen expert-panel assertions encoded as ALT|UUID|CAID|assertion|disease|MONDO|MOI|panel|approval_date; URL-percent-encoded fields">\n')
        out.write('##INFO=<ID=ClinGen_ERepo_count,Number=1,Type=Integer,Description="Number of active ClinGen Evidence Repository assertions on this allele">\n')
        out.write("#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n")
        for allele in sorted(grouped, key=lambda item: (order.get(item[0], 99), item[1], item[2], item[3])):
            chrom, pos, ref, alt = allele
            assertions = sorted(grouped[allele], key=lambda row: (row["Disease"], row["Mode of Inheritance"], row["Uuid"]))
            tokens = []
            for row in assertions:
                tokens.append("|".join(safe(value) for value in (
                    alt, row["Uuid"], row["Allele Registry Id"], row["Assertion"],
                    row["Disease"], row["Mondo Id"], row["Mode of Inheritance"],
                    row["Expert Panel"], row["Approval Date"],
                )))
            out.write(f"{chrom}\t{pos}\t.\t{ref}\t{alt}\t.\t.\tClinGen_ERepo={','.join(tokens)};ClinGen_ERepo_count={len(tokens)}\n")
    return len(grouped)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--clinvar-vcf", type=Path, required=True)
    parser.add_argument("--regions-out", type=Path)
    parser.add_argument("--reference-sequences", type=Path)
    parser.add_argument("--output-vcf", type=Path)
    parser.add_argument("--output-sqlite", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--api-version", default="unknown")
    parser.add_argument("--minimum-mapping-rate", type=float, default=0.995)
    args = parser.parse_args()

    rows, fields = read_rows(args.source)
    by_id, by_caid = scan_clinvar(args.clinvar_vcf, rows)
    if args.regions_out:
        regions = set()
        for row in rows:
            if row["ClinVar Variation Id"] in by_id or row["Allele Registry Id"] in by_caid:
                continue
            parsed = grch38_hgvs(row)
            if parsed and not SUB_RE.fullmatch(parsed[1]):
                region = region_for(*parsed)
                if region:
                    regions.add(region)
        args.regions_out.parent.mkdir(parents=True, exist_ok=True)
        args.regions_out.write_text("".join(f"{chrom}:{start}-{end}\n" for chrom, start, end in sorted(regions)), encoding="ascii")
        print(f"ClinGen reference regions requested: {len(regions)}")
        return 0

    if not all((args.output_vcf, args.output_sqlite, args.manifest)):
        parser.error("final preparation requires --output-vcf, --output-sqlite, and --manifest")
    regions = parse_fasta_regions(args.reference_sequences)
    mappings = {}
    methods = Counter()
    for row in rows:
        allele = None
        method = ""
        variation_id, caid = row["ClinVar Variation Id"], row["Allele Registry Id"]
        if variation_id and variation_id in by_id:
            allele, method = by_id[variation_id], "ClinVar Variation ID"
        elif caid and caid in by_caid:
            allele, method = by_caid[caid], "ClinVar CA ID"
        else:
            try:
                allele = allele_from_hgvs(row, regions)
                method = "GRCh38 genomic HGVS" if allele else ""
            except ValueError:
                allele = None
        if allele and all(allele) and "," not in allele[3]:
            mappings[row["Uuid"]] = (allele, method, "")
            methods[method] += 1
        else:
            methods["unresolved"] += 1

    active_rows = [row for row in rows if row["Retracted"].lower() != "true"]
    mapped_active = sum(row["Uuid"] in mappings for row in active_rows)
    rate = mapped_active / len(active_rows)
    if rate < args.minimum_mapping_rate:
        raise ValueError(f"ClinGen exact allele mapping rate {rate:.3%} is below required {args.minimum_mapping_rate:.1%}")
    generated = datetime.now(timezone.utc).isoformat()
    source_hash = sha256(args.source)
    metadata = {
        "generated_utc": generated, "source_sha256": source_hash,
        "api_version": args.api_version, "source_rows": str(len(rows)),
        "active_rows": str(len(active_rows)), "mapped_active_rows": str(mapped_active),
        "mapping_rate": f"{rate:.8f}",
    }
    args.output_vcf.parent.mkdir(parents=True, exist_ok=True)
    allele_count = write_vcf(args.output_vcf, rows, mappings, generated, source_hash)
    write_sqlite(args.output_sqlite, rows, mappings, metadata)
    manifest = {
        **metadata, "source_url": "https://erepo.clinicalgenome.org/evrepo/api/summary/classifications/download?type=csv",
        "schema_columns": fields, "alleles": allele_count,
        "mapping_methods": dict(sorted(methods.items())),
        "assertions": dict(sorted(Counter(row["Assertion"] for row in active_rows).items())),
        "retracted_rows": len(rows) - len(active_rows),
        "unmapped_active_rows": len(active_rows) - mapped_active,
        "vcf": str(args.output_vcf), "sqlite": str(args.output_sqlite),
    }
    args.manifest.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"ClinGen Evidence Repository: {mapped_active}/{len(active_rows)} active assertions mapped ({rate:.2%}); {allele_count} alleles")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
