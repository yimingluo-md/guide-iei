#!/usr/bin/env python3
"""Build compact, auditable reference tables bundled with the local workbench.

Inputs are the official gnomAD v4.1.1 constraint TSV (BGZF) and the IUIS
October 2024 classification workbook. The output is deliberately small enough
to ship with the browser UI; users do not need to annotate their VCF with these
gene-level resources.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import re
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

GNOMAD_RELEASE = "4.1.1"
GNOMAD_URL = (
    "https://storage.googleapis.com/gcp-public-data--gnomad/release/4.1.1/"
    "constraint/gnomad.v4.1.1.constraint_metrics.tsv.bgz"
)
GNOMAD_SHA256 = "91b5cdfc9a9fb8d3b6c28c3b884b9f3e71dcc2da4805b428a9b763d997597ff9"
IUIS_RELEASE = "October 2024"
IUIS_URL = (
    "https://wp-iuis.s3.eu-west-1.amazonaws.com/app/uploads/2024/10/"
    "30094653/IUIS-IEI-list-for-web-site-July-2024V2.xlsx"
)
IUIS_SHA256 = "6e56ea25ef7601d72951289cda77293230f8f9add05d6a502ba126ffe35ad82b"

NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
NS_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS_PACKAGE_REL = "http://schemas.openxmlformats.org/package/2006/relationships"

# The IUIS workbook intentionally contains historical/alias labels. Normalize
# only known entries, retain source_genetic_defect beside the result, and leave
# cytogenetic/unknown entries without a gene symbol.
IUIS_GENE_NORMALIZATION: dict[str, list[str]] = {
    "11q23del": [],
    "14q32 deletion or mutation": [],
    "Del10p13-p14": [],
    "Large (3Mb) deletion of 22q11.2": [],
    "Unknown": [],
    "Unknown / environment": [],
    "ADAR1": ["ADAR"],
    "C4A+C4B": ["C4A", "C4B"],
    "CD20": ["MS4A1"],
    "CD21": ["CR2"],
    "CD40 (TNFRSF5)": ["CD40"],
    "CD40LG (TNFSF5)": ["CD40LG"],
    "CFHR1 CFHR2. CFHR3 CFHR4 CFHR5": [
        "CFHR1",
        "CFHR2",
        "CFHR3",
        "CFHR4",
        "CFHR5",
    ],
    "G6PT1": ["SLC37A4"],
    "KMT2D (MLL2)": ["KMT2D"],
    "MOGS (GCS1)": ["MOGS"],
    "NCKAPIL": ["NCKAP1L"],
    "NOLA2": ["NHP2"],
    "NOLA3": ["NOP10"],
    "PIK3CD GOF": ["PIK3CD"],
    "POLE1": ["POLE"],
    "PSEN": ["PSEN1"],
    "TAZ": ["TAFAZZIN"],
    "TNFRSF6": ["FAS"],
    "TNFSF6": ["FASLG"],
    "XRCC9": ["FANCG"],
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_source(path: Path, expected: str, label: str) -> None:
    observed = sha256(path)
    if observed != expected:
        raise SystemExit(
            f"{label} checksum mismatch: expected {expected}, observed {observed}"
        )


def clean_na(value: str | None) -> str:
    value = (value or "").strip()
    return "" if value in {"", "NA", "null", "None"} else value


def clean_flags(value: str | None) -> str:
    value = clean_na(value)
    if not value:
        return ""
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return value
    return "|".join(str(item) for item in parsed) if isinstance(parsed, list) else value


def numeric(value: str | None) -> float:
    try:
        return float(value or "")
    except ValueError:
        return -1.0


def transcript_priority(row: dict[str, str]) -> tuple[object, ...]:
    ensembl = row["gene_id"].startswith("ENSG")
    mane = row["mane_select"] == "true"
    canonical = row["canonical"] == "true"
    protein_coding = row["transcript_type"] == "protein_coding"
    return (
        int(ensembl and mane),
        int(ensembl and canonical),
        int(mane),
        int(canonical),
        int(ensembl),
        int(protein_coding),
        int(numeric(row["cds_length"])),
        row["transcript"],
    )


def build_gnomad(source: Path, destination: Path) -> dict[str, object]:
    best: dict[str, dict[str, str]] = {}
    source_rows = 0
    with gzip.open(source, "rt", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        required = {
            "gene",
            "gene_id",
            "transcript",
            "canonical",
            "mane_select",
            "mis.z_score",
            "lof.oe",
            "lof.oe_ci.upper",
            "lof.pLI",
        }
        missing = required.difference(reader.fieldnames or [])
        if missing:
            raise SystemExit(f"gnomAD source missing columns: {sorted(missing)}")
        for row in reader:
            source_rows += 1
            gene = row["gene"].strip().upper()
            if not gene:
                continue
            current = best.get(gene)
            if current is None or transcript_priority(row) > transcript_priority(current):
                best[gene] = row

    fields = [
        "gene_symbol",
        "ensembl_gene_id",
        "transcript_id",
        "mane_select",
        "canonical",
        "pLI",
        "loeuf",
        "missense_z",
        "lof_oe",
        "lof_observed",
        "lof_expected",
        "gene_flags",
        "constraint_flags",
        "exome_prop_bp_an90",
        "exome_prop_segdup",
        "exome_prop_lcr",
    ]
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=fields, delimiter="\t", lineterminator="\n"
        )
        writer.writeheader()
        for gene in sorted(best):
            row = best[gene]
            gene_id = clean_na(row["gene_id"])
            writer.writerow(
                {
                    "gene_symbol": gene,
                    "ensembl_gene_id": gene_id if gene_id.startswith("ENSG") else "",
                    "transcript_id": clean_na(row["transcript"]),
                    "mane_select": row["mane_select"],
                    "canonical": row["canonical"],
                    "pLI": clean_na(row["lof.pLI"]),
                    "loeuf": clean_na(row["lof.oe_ci.upper"]),
                    "missense_z": clean_na(row["mis.z_score"]),
                    "lof_oe": clean_na(row["lof.oe"]),
                    "lof_observed": clean_na(row["lof.obs"]),
                    "lof_expected": clean_na(row["lof.exp"]),
                    "gene_flags": clean_flags(row["gene_flags"]),
                    "constraint_flags": clean_flags(row["constraint_flags"]),
                    "exome_prop_bp_an90": clean_na(
                        row["gene_quality_metrics.exome_prop_bp_AN90"]
                    ),
                    "exome_prop_segdup": clean_na(
                        row["gene_quality_metrics.exome_prop_segdup"]
                    ),
                    "exome_prop_lcr": clean_na(
                        row["gene_quality_metrics.exome_prop_LCR"]
                    ),
                }
            )

    if len(best) != 19_638 or source_rows != 221_898:
        raise SystemExit(
            "unexpected gnomAD row counts "
            f"(source={source_rows}, selected_genes={len(best)})"
        )
    return {
        "source_rows": source_rows,
        "selected_genes": len(best),
        "selection": (
            "One row per gene: Ensembl MANE Select, then Ensembl canonical, "
            "then MANE/canonical, then Ensembl protein-coding/longest CDS."
        ),
    }


def column_index(cell_reference: str) -> int:
    letters = re.match(r"[A-Z]+", cell_reference)
    if not letters:
        raise ValueError(f"invalid cell reference: {cell_reference}")
    result = 0
    for character in letters.group(0):
        result = result * 26 + ord(character) - ord("A") + 1
    return result - 1


def shared_strings(archive: zipfile.ZipFile) -> list[str]:
    try:
        root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    values: list[str] = []
    for item in root.findall(f"{{{NS_MAIN}}}si"):
        values.append("".join(node.text or "" for node in item.iter(f"{{{NS_MAIN}}}t")))
    return values


def first_worksheet_path(archive: zipfile.ZipFile) -> str:
    workbook = ET.fromstring(archive.read("xl/workbook.xml"))
    sheet = workbook.find(f".//{{{NS_MAIN}}}sheet")
    if sheet is None:
        raise SystemExit("IUIS workbook contains no worksheets")
    relationship_id = sheet.attrib[f"{{{NS_REL}}}id"]
    relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    for relationship in relationships.findall(f"{{{NS_PACKAGE_REL}}}Relationship"):
        if relationship.attrib["Id"] == relationship_id:
            target = relationship.attrib["Target"].lstrip("/")
            return target if target.startswith("xl/") else f"xl/{target}"
    raise SystemExit("could not resolve IUIS worksheet relationship")


def xlsx_rows(path: Path) -> list[list[str]]:
    with zipfile.ZipFile(path) as archive:
        strings = shared_strings(archive)
        root = ET.fromstring(archive.read(first_worksheet_path(archive)))
        parsed: list[list[str]] = []
        for row in root.findall(f".//{{{NS_MAIN}}}sheetData/{{{NS_MAIN}}}row"):
            values: dict[int, str] = {}
            for cell in row.findall(f"{{{NS_MAIN}}}c"):
                index = column_index(cell.attrib["r"])
                kind = cell.attrib.get("t")
                raw = cell.findtext(f"{{{NS_MAIN}}}v", default="")
                if kind == "s" and raw:
                    value = strings[int(raw)]
                elif kind == "inlineStr":
                    value = "".join(
                        node.text or "" for node in cell.iter(f"{{{NS_MAIN}}}t")
                    )
                else:
                    value = raw
                values[index] = value
            if values:
                parsed.append([values.get(index, "") for index in range(max(values) + 1)])
        return parsed


def normalized_space(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").replace("\xa0", " ")).strip()


def build_iuis(
    source: Path,
    classification_destination: Path,
    genes_destination: Path,
    dominant_destination: Path,
) -> dict[str, object]:
    rows = xlsx_rows(source)
    if not rows:
        raise SystemExit("IUIS workbook is empty")
    header = [normalized_space(value) for value in rows[0]]
    index = {value: position for position, value in enumerate(header)}
    required = {
        "Disease",
        "Genetic defect",
        "Inheritance",
        "GOF/DN",
        "OMIM",
        "T cell count",
        "B cell count",
        "Immunoglobulin levels",
        "Neutrophil count",
        "Other affected cells",
        "Associated features",
        "Major category",
        "Subcategory",
    }
    missing = required.difference(index)
    if missing:
        raise SystemExit(f"IUIS source missing columns: {sorted(missing)}")

    output_rows: list[dict[str, str]] = []
    all_genes: set[str] = set()
    dominant_genes: set[str] = set()
    for source_row, row in enumerate(rows[1:], start=2):
        padded = row + [""] * (len(header) - len(row))
        raw_gene = normalized_space(padded[index["Genetic defect"]])
        genes = IUIS_GENE_NORMALIZATION.get(raw_gene, [raw_gene] if raw_gene else [])
        inheritance = normalized_space(padded[index["Inheritance"]])
        mechanism = normalized_space(padded[index["GOF/DN"]])
        is_dominant = bool(re.search(r"\bAD\b", inheritance))
        is_recessive = bool(re.search(r"\bAR\b", inheritance))
        is_x_linked = bool(re.search(r"\bXL\b", inheritance))
        for gene in genes or [""]:
            gene = gene.upper()
            if gene:
                all_genes.add(gene)
                if is_dominant:
                    dominant_genes.add(gene)
            output_rows.append(
                {
                    "source_row": str(source_row),
                    "gene_symbol": gene,
                    "source_genetic_defect": raw_gene,
                    "disease": normalized_space(padded[index["Disease"]]),
                    "inheritance": inheritance,
                    "mechanism": mechanism,
                    "is_dominant": str(is_dominant).lower(),
                    "is_recessive": str(is_recessive).lower(),
                    "is_x_linked": str(is_x_linked).lower(),
                    "omim": normalized_space(padded[index["OMIM"]]),
                    "t_cell_count": normalized_space(padded[index["T cell count"]]),
                    "b_cell_count": normalized_space(padded[index["B cell count"]]),
                    "immunoglobulin_levels": normalized_space(padded[index["Immunoglobulin levels"]]),
                    "neutrophil_count": normalized_space(padded[index["Neutrophil count"]]),
                    "other_affected_cells": normalized_space(padded[index["Other affected cells"]]),
                    "associated_features": normalized_space(padded[index["Associated features"]]),
                    "major_category": normalized_space(padded[index["Major category"]]),
                    "subcategory": normalized_space(padded[index["Subcategory"]]),
                }
            )

    # Keep the always-populated source row last so version-controlled TSV lines
    # never end in whitespace when optional final fields are blank.
    fields = [field for field in output_rows[0] if field != "source_row"] + ["source_row"]
    classification_destination.parent.mkdir(parents=True, exist_ok=True)
    with classification_destination.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=fields, delimiter="\t", lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(output_rows)
    genes_destination.write_text("\n".join(sorted(all_genes)) + "\n", encoding="utf-8")
    dominant_destination.write_text(
        "\n".join(sorted(dominant_genes)) + "\n", encoding="utf-8"
    )

    if len(rows) - 1 != 582 or len(all_genes) != 505 or len(dominant_genes) != 137:
        raise SystemExit(
            "unexpected IUIS row counts "
            f"(source={len(rows) - 1}, genes={len(all_genes)}, "
            f"dominant={len(dominant_genes)})"
        )
    return {
        "source_rows": len(rows) - 1,
        "normalized_rows": len(output_rows),
        "unique_genes": len(all_genes),
        "dominant_genes": len(dominant_genes),
        "normalization": (
            "Composite entries split; known legacy aliases converted to current "
            "VEP-compatible symbols; cytogenetic and unknown entries retained "
            "with a blank gene_symbol."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gnomad", required=True, type=Path)
    parser.add_argument("--iuis", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    verify_source(args.gnomad, GNOMAD_SHA256, "gnomAD")
    verify_source(args.iuis, IUIS_SHA256, "IUIS")
    args.output.mkdir(parents=True, exist_ok=True)

    gnomad_metadata = build_gnomad(
        args.gnomad, args.output / "gnomad_v4.1.1_gene_constraint.tsv"
    )
    iuis_metadata = build_iuis(
        args.iuis,
        args.output / "iuis_2024_classification.tsv",
        args.output / "iuis_2024_genes.txt",
        args.output / "iuis_2024_dominant_genes.txt",
    )
    manifest = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "gnomad": {
            "release": GNOMAD_RELEASE,
            "source_url": GNOMAD_URL,
            "source_sha256": GNOMAD_SHA256,
            **gnomad_metadata,
        },
        "iuis": {
            "release": IUIS_RELEASE,
            "source_url": IUIS_URL,
            "source_sha256": IUIS_SHA256,
            **iuis_metadata,
        },
    }
    (args.output / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(
        f"Wrote {gnomad_metadata['selected_genes']} gnomAD genes, "
        f"{iuis_metadata['unique_genes']} IUIS genes, and "
        f"{iuis_metadata['dominant_genes']} dominant IUIS genes to {args.output}"
    )


if __name__ == "__main__":
    main()
