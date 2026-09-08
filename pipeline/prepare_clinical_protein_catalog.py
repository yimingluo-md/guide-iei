#!/usr/bin/env python3
"""Build provenance-preserving P/LP missense catalogs for protein matching.

The build has two explicit stages so VEP remains an external/containerized
concern:

``export`` writes one synthetic-ID VCF row per qualifying source record and a
metadata TSV keyed by that ID.  ``reduce`` joins VEP ``--tab`` output back to
the metadata and writes the catalog consumed by ``clinvar_aa_match.py``.

Supported sources are the public ClinVar VCF, the prepared ClinGen Evidence
Repository SQLite database, and the locally imported GenIA SQLite database.
No live service or patient data is involved.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, TextIO

import yaml


CATALOG_COLUMNS = (
    "gene", "protein_position", "ref_aa", "alt_aa", "transcript",
    "record_id", "source_allele", "classification", "disease",
)
METADATA_COLUMNS = (
    "synthetic_id", "record_id", "source_allele", "classification",
    "disease", "known_gene",
)
# Default classification labels that admit a source record into the P/LP
# catalog. Matching is on the WHOLE label after normalisation (case, and
# "_"/whitespace runs collapsed to one space), never on a substring: ClinVar
# writes compound values such as "Pathogenic|risk_factor" or
# "Likely_pathogenic,_low_penetrance", and whether those belong in a
# pathogenic catalog is a classification-policy decision, not a parsing
# accident. Add them explicitly through the configuration
# (post_processing.clinical_protein_match.pathogenic_terms, or the legacy
# post_processing.clinvar_aa_match.pathogenic_terms) if they should count.
DEFAULT_PATHOGENIC_TERMS = (
    "Pathogenic",
    "Likely_pathogenic",
    "Pathogenic/Likely_pathogenic",
)
PATHOGENIC_LABELS = {
    "pathogenic", "likely pathogenic", "pathogenic/likely pathogenic",
    "pathogenic likely pathogenic",
}
CONFIG_TERM_PATHS = (
    ("post_processing", "clinical_protein_match", "pathogenic_terms"),
    ("post_processing", "clinvar_aa_match", "pathogenic_terms"),
)


@dataclass(frozen=True)
class SourceRecord:
    chrom: str
    pos: int
    ref: str
    alt: str
    record_id: str
    classification: str
    disease: str = ""
    known_gene: str = ""

    @property
    def allele(self) -> str:
        chrom = normalize_chrom(self.chrom)
        return f"{chrom}:{self.pos}:{self.ref.upper()}:{self.alt.upper()}"


def normalize_chrom(value: str) -> str:
    chrom = str(value).strip().removeprefix("chr")
    return "MT" if chrom == "M" else chrom


def clean(value: object) -> str:
    return re.sub(r"\s+", " ", "" if value is None else str(value)).strip()


def classification_key(value: str) -> str:
    return re.sub(r"[_\s]+", " ", clean(value).casefold()).strip()


def is_pathogenic(value: str, labels: Iterable[str] | None = None) -> bool:
    """Exact (normalised) label membership; ``labels`` defaults to the
    built-in set. A compound ClinVar value never matches by substring."""
    active = PATHOGENIC_LABELS if labels is None else set(labels)
    return classification_key(value) in active


def normalize_labels(terms: Iterable[str]) -> frozenset[str]:
    labels = set()
    for term in terms:
        if not isinstance(term, str):
            raise ValueError(
                f"pathogenic_terms must be a list of strings, got {term!r}"
            )
        key = classification_key(term)
        if not key:
            continue
        if any(marker in key for marker in ("*", "?", "%")):
            raise ValueError(
                f"pathogenic_terms entries are whole labels, not patterns: {term!r}"
            )
        labels.add(key)
        # ClinVar spells the combined assertion "Pathogenic/Likely_pathogenic";
        # ClinGen and older exports write it without the slash. Treat the two
        # spellings of that ONE label as the same entry.
        if "/" in key:
            labels.add(key.replace("/", " "))
    if not labels:
        raise ValueError("pathogenic_terms must name at least one label")
    return frozenset(labels)


def load_pathogenic_labels(config_path: Path | None) -> tuple[frozenset[str], str]:
    """Return (normalised label set, where it came from).

    Reads ``post_processing.clinical_protein_match.pathogenic_terms`` first,
    then the legacy ``post_processing.clinvar_aa_match.pathogenic_terms``;
    without either the built-in default applies. Both ClinVar CLNSIG values
    and ClinGen assertion labels are judged against the same set.
    """
    if config_path is None:
        return normalize_labels(DEFAULT_PATHOGENIC_TERMS), "default"
    with Path(config_path).open("r", encoding="utf-8") as handle:
        document = yaml.safe_load(handle) or {}
    for path in CONFIG_TERM_PATHS:
        node = document
        for key in path:
            node = node.get(key) if isinstance(node, dict) else None
            if node is None:
                break
        if node is None:
            continue
        if not isinstance(node, list):
            raise ValueError(f"{'.'.join(path)} must be a list of labels")
        return normalize_labels(node), ".".join(path)
    return normalize_labels(DEFAULT_PATHOGENIC_TERMS), "default"


def labels_fingerprint(labels: Iterable[str]) -> str:
    """Short stable digest of a label set, for catalog stamps/manifests."""
    digest = hashlib.sha256("\n".join(sorted(labels)).encode("utf-8"))
    return digest.hexdigest()[:12]


def open_vcf(path: Path) -> TextIO:
    if path.name.endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8")
    return path.open("r", encoding="utf-8")


def parse_info(raw: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in raw.split(";"):
        key, marker, value = item.partition("=")
        if marker:
            result[key] = value
    return result


def iter_clinvar(
    path: Path, labels: Iterable[str] | None = None,
) -> Iterator[SourceRecord]:
    with open_vcf(path) as handle:
        for line in handle:
            if line.startswith("#"):
                continue
            columns = line.rstrip("\r\n").split("\t")
            if len(columns) < 8:
                raise ValueError("ClinVar VCF contains a malformed record")
            info = parse_info(columns[7])
            classification = info.get("CLNSIG", "")
            # MC is a cheap source-side reduction.  VEP remains authoritative
            # for the transcript/gene/protein match encoded in the catalog.
            if not is_pathogenic(classification, labels) or \
                    "missense_variant" not in info.get("MC", ""):
                continue
            try:
                pos = int(columns[1])
            except ValueError as exc:
                raise ValueError("ClinVar VCF contains a non-integer position") from exc
            for alt_index, alt in enumerate(columns[4].split(","), start=1):
                if not re.fullmatch(r"[ACGTN]+", columns[3], re.IGNORECASE) or \
                        not re.fullmatch(r"[ACGTN]+", alt, re.IGNORECASE):
                    continue
                record_id = columns[2] if columns[2] not in {"", "."} else \
                    f"{normalize_chrom(columns[0])}:{pos}:{columns[3]}:{alt}"
                if len(columns[4].split(",")) > 1:
                    record_id = f"{record_id}:ALT{alt_index}"
                yield SourceRecord(
                    columns[0], pos, columns[3], alt, record_id,
                    classification.replace("_", " "),
                    info.get("CLNDN", "").replace("_", " "),
                    "|".join(
                        token.partition(":")[0]
                        for token in info.get("GENEINFO", "").split("|")
                        if token.partition(":")[0]
                    ),
                )


def _open_sqlite(path: Path) -> sqlite3.Connection:
    if not path.is_file() or path.stat().st_size == 0:
        raise ValueError(f"source database is missing or empty: {path}")
    connection = sqlite3.connect(
        f"{path.resolve().as_uri()}?mode=ro&immutable=1", uri=True
    )
    check = connection.execute("PRAGMA quick_check").fetchone()
    if not check or check[0] != "ok":
        connection.close()
        raise ValueError(f"source database failed its quick check: {path}")
    return connection


def iter_clingen(
    path: Path, labels: Iterable[str] | None = None,
) -> Iterator[SourceRecord]:
    connection = _open_sqlite(path)
    try:
        query = """
          SELECT chrom,pos,ref,alt,uuid,assertion,disease,gene
          FROM assertions
          WHERE active=1 AND chrom IS NOT NULL AND pos IS NOT NULL
          ORDER BY chrom,pos,ref,alt,uuid
        """
        for chrom, pos, ref, alt, record_id, classification, disease, gene \
                in connection.execute(query):
            if is_pathogenic(str(classification or ""), labels):
                yield SourceRecord(
                    str(chrom), int(pos), str(ref), str(alt), str(record_id),
                    str(classification), str(disease or ""), str(gene or ""),
                )
    except sqlite3.Error as exc:
        raise ValueError(f"ClinGen database has an incompatible schema: {exc}") from exc
    finally:
        connection.close()


def iter_genia(path: Path) -> Iterator[SourceRecord]:
    connection = _open_sqlite(path)
    try:
        component = connection.execute(
            "SELECT 1 FROM components WHERE id='variant_vcf'"
        ).fetchone()
        if component is None:
            return
        query = """
          SELECT chrom,pos,ref,alt,record_id,class_code
          FROM variants WHERE upper(class_code) IN ('P','LP')
          ORDER BY chrom,pos,ref,alt,record_id
        """
        for chrom, pos, ref, alt, record_id, class_code in connection.execute(query):
            label = "Pathogenic" if str(class_code).upper() == "P" else "Likely pathogenic"
            yield SourceRecord(
                str(chrom), int(pos), str(ref), str(alt), str(record_id), label,
            )
    except sqlite3.Error as exc:
        raise ValueError(f"GenIA database has an incompatible schema: {exc}") from exc
    finally:
        connection.close()


def source_records(
    source_type: str, path: Path, labels: Iterable[str] | None = None,
) -> Iterator[SourceRecord]:
    if source_type == "clinvar":
        return iter_clinvar(path, labels)
    if source_type == "clingen":
        return iter_clingen(path, labels)
    if source_type == "genia":
        # GenIA stores a class code (P/LP), not a free-text label; the
        # configured label list does not apply.
        return iter_genia(path)
    raise ValueError(f"unsupported clinical protein source: {source_type}")


def export_source(source_type: str, source: Path, output_vcf: Path,
                  metadata: Path, labels: Iterable[str] | None = None) -> int:
    output_vcf.parent.mkdir(parents=True, exist_ok=True)
    metadata.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with output_vcf.open("w", encoding="utf-8", newline="") as vcf, \
            metadata.open("w", encoding="utf-8", newline="") as meta:
        vcf.write("##fileformat=VCFv4.2\n")
        vcf.write("##source=GUIDE_IEI_clinical_protein_catalog_builder\n")
        vcf.write("#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n")
        writer = csv.DictWriter(meta, fieldnames=METADATA_COLUMNS, delimiter="\t")
        writer.writeheader()
        for count, record in enumerate(
            source_records(source_type, source, labels), start=1
        ):
            synthetic_id = f"CPM{count:09d}"
            vcf.write(
                f"{normalize_chrom(record.chrom)}\t{record.pos}\t{synthetic_id}\t"
                f"{record.ref.upper()}\t{record.alt.upper()}\t.\t.\t.\n"
            )
            writer.writerow({
                "synthetic_id": synthetic_id,
                "record_id": clean(record.record_id),
                "source_allele": record.allele,
                "classification": clean(record.classification),
                "disease": clean(record.disease),
                "known_gene": clean(record.known_gene).upper(),
            })
    return count


def _read_metadata(path: Path) -> dict[str, dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if tuple(reader.fieldnames or ()) != METADATA_COLUMNS:
            raise ValueError("clinical protein metadata has an incompatible schema")
        return {
            row["synthetic_id"]: {key: clean(value) for key, value in row.items()}
            for row in reader
        }


def reduce_vep(vep_tab: Path, metadata: Path, output: Path) -> int:
    source = _read_metadata(metadata)
    header: list[str] | None = None
    catalog: set[tuple[str, ...]] = set()
    with vep_tab.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.startswith("##") or not line.strip():
                continue
            if line.startswith("#"):
                header = line.lstrip("#").rstrip("\r\n").split("\t")
                continue
            if header is None:
                raise ValueError("VEP output contains data before its tab header")
            values = line.rstrip("\r\n").split("\t")
            row = dict(zip(header, values))
            if "missense_variant" not in row.get("Consequence", ""):
                continue
            meta = source.get(row.get("Uploaded_variation", ""))
            if meta is None:
                continue
            gene = clean(row.get("SYMBOL", "")).upper()
            pos = clean(row.get("Protein_position", ""))
            transcript = clean(row.get("Feature", "")).split(".")[0]
            amino = clean(row.get("Amino_acids", ""))
            if not gene or not pos or pos == "-" or not transcript or "/" not in amino:
                continue
            known_gene = meta.get("known_gene", "")
            known_genes = {
                value for value in re.split(r"[|,;]", known_gene) if value
            }
            if known_genes and gene not in known_genes:
                continue
            ref_aa, _, alt_aa = amino.partition("/")
            if not ref_aa or not alt_aa:
                continue
            catalog.add((
                gene, pos, ref_aa, alt_aa, transcript,
                meta["record_id"], meta["source_allele"],
                meta["classification"], meta["disease"],
            ))
    if header is None:
        raise ValueError("VEP output has no tab header")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as handle:
        handle.write("#" + "\t".join(CATALOG_COLUMNS) + "\n")
        for record in sorted(catalog):
            handle.write("\t".join(clean(value) for value in record) + "\n")
    return len(catalog)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    export_parser = subparsers.add_parser("export")
    export_parser.add_argument("--source-type", choices=("clinvar", "clingen", "genia"), required=True)
    export_parser.add_argument("--source", type=Path, required=True)
    export_parser.add_argument("--output-vcf", type=Path, required=True)
    export_parser.add_argument("--metadata", type=Path, required=True)
    export_parser.add_argument(
        "--config", type=Path,
        help="annotation config whose pathogenic_terms define the P/LP labels",
    )
    labels_parser = subparsers.add_parser(
        "labels", help="print the active pathogenic label set and its fingerprint",
    )
    labels_parser.add_argument("--config", type=Path)
    reduce_parser = subparsers.add_parser("reduce")
    reduce_parser.add_argument("--vep-tab", type=Path, required=True)
    reduce_parser.add_argument("--metadata", type=Path, required=True)
    reduce_parser.add_argument("--output", type=Path, required=True)
    reduce_parser.add_argument("--manifest", type=Path)
    reduce_parser.add_argument("--source", type=Path)
    reduce_parser.add_argument("--source-type", choices=("clinvar", "clingen", "genia"))
    reduce_parser.add_argument("--config", type=Path)
    args = parser.parse_args(argv)

    if args.command == "labels":
        labels, origin = load_pathogenic_labels(args.config)
        print(f"{labels_fingerprint(labels)}\t{origin}\t{'|'.join(sorted(labels))}")
        return 0

    if args.command == "export":
        labels, origin = load_pathogenic_labels(args.config)
        count = export_source(
            args.source_type, args.source, args.output_vcf, args.metadata, labels
        )
        print(
            f"[clinical_protein_catalog] exported {count} {args.source_type} "
            f"P/LP source record(s) (labels from {origin}: "
            f"{', '.join(sorted(labels))})"
        )
        return 0

    count = reduce_vep(args.vep_tab, args.metadata, args.output)
    if args.manifest:
        labels, origin = load_pathogenic_labels(args.config)
        manifest = {
            "schema": "guide-iei-clinical-protein-catalog-v1",
            "source_type": args.source_type or "unknown",
            "source_sha256": sha256(args.source) if args.source else "",
            "catalog_rows": count,
            "catalog_sha256": sha256(args.output),
            "pathogenic_labels": sorted(labels),
            "pathogenic_labels_source": origin,
            "pathogenic_labels_fingerprint": labels_fingerprint(labels),
        }
        args.manifest.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    print(f"[clinical_protein_catalog] wrote {count} transcript-specific missense record(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
