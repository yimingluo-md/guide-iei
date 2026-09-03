"""Private, component-based GenIA imports and local lookups.

GenIA exports require a registered user and are never bundled by GUIDE-IEI.
Each supported export is an independent component: installing a subset updates
only those components and preserves the remaining working local index.
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import json
import re
import sqlite3
import threading
import uuid
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, TextIO

from pipeline.genia_alleles import normalize_allele, normalize_chrom


SCHEMA_VERSION = 1

COMPONENT_LABELS = {
    "gei_disease": "GEI gene–disease list",
    "disease_catalog": "GenIA disease catalog",
    "disease_phenotypes": "Disease–phenotype associations",
    "phenotype_vocabulary": "GenIA phenotype vocabulary",
    "variant_vcf": "GenIA GRCh38 variants",
}

COMPONENT_TABLES = {
    "gei_disease": ("gei_relationships",),
    "disease_catalog": ("diseases",),
    "disease_phenotypes": ("disease_phenotypes",),
    "phenotype_vocabulary": ("phenotype_terms",),
    "variant_vcf": ("variants", "rejected_variants"),
}

CLASS_LABELS = {
    "P": "Pathogenic",
    "LP": "Likely pathogenic",
    "VUS": "Uncertain significance",
    "LB": "Likely benign",
    "B": "Benign",
    "NC": "Not classified",
    "RF": "Risk factor",
}

SCHEMA = """
PRAGMA foreign_keys=OFF;
CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE components(
  id TEXT PRIMARY KEY, label TEXT NOT NULL, source_name TEXT NOT NULL,
  source_sha256 TEXT NOT NULL, schema_fingerprint TEXT NOT NULL,
  record_count INTEGER NOT NULL, installed_at TEXT NOT NULL,
  release_hint TEXT NOT NULL DEFAULT ''
);
CREATE TABLE gei_relationships(
  source_id TEXT NOT NULL, disease_name TEXT NOT NULL, synonyms TEXT NOT NULL,
  gene_symbol TEXT NOT NULL, moi TEXT NOT NULL, moa TEXT NOT NULL,
  publication_year TEXT NOT NULL, iuis_classification TEXT NOT NULL,
  curation_status TEXT NOT NULL, curation_status_raw TEXT NOT NULL,
  case_count TEXT NOT NULL, family_count TEXT NOT NULL,
  omim_id TEXT NOT NULL, mondo_id TEXT NOT NULL,
  clingen_class TEXT NOT NULL, last_updated TEXT NOT NULL,
  PRIMARY KEY(source_id,gene_symbol,disease_name)
);
CREATE INDEX gei_gene_lookup ON gei_relationships(gene_symbol COLLATE NOCASE);
CREATE TABLE diseases(
  disease_id TEXT PRIMARY KEY, term TEXT NOT NULL, acronym TEXT NOT NULL,
  alternate_terms TEXT NOT NULL, gene_id TEXT NOT NULL,
  gene_symbol TEXT NOT NULL, moi TEXT NOT NULL, moa TEXT NOT NULL,
  publication_year TEXT NOT NULL, iei TEXT NOT NULL,
  iuis_table TEXT NOT NULL, omim_id TEXT NOT NULL, mondo_id TEXT NOT NULL,
  clingen_class TEXT NOT NULL, clingen_review_date TEXT NOT NULL,
  last_updated TEXT NOT NULL, case_count TEXT NOT NULL,
  family_count TEXT NOT NULL
);
CREATE INDEX diseases_gene_lookup ON diseases(gene_symbol COLLATE NOCASE);
CREATE INDEX diseases_join_lookup ON diseases(acronym COLLATE NOCASE,gene_symbol COLLATE NOCASE);
CREATE TABLE disease_phenotypes(
  id INTEGER PRIMARY KEY, disease_id TEXT NOT NULL, disease_name TEXT NOT NULL,
  omim_id TEXT NOT NULL, gene_id TEXT NOT NULL, gene_symbol TEXT NOT NULL,
  moi TEXT NOT NULL, clinical_term_id TEXT NOT NULL,
  clinical_term TEXT NOT NULL, hpo_term TEXT NOT NULL, hpo_id TEXT NOT NULL,
  rank INTEGER, count_yes INTEGER, percent_yes REAL,
  count_no INTEGER, percent_no REAL, count_unreported INTEGER,
  percent_unreported REAL
);
CREATE INDEX disease_phenotypes_gene_lookup ON disease_phenotypes(gene_symbol COLLATE NOCASE);
CREATE INDEX disease_phenotypes_disease_lookup ON disease_phenotypes(disease_id,rank);
CREATE TABLE phenotype_terms(
  clinical_term_id TEXT PRIMARY KEY, term TEXT NOT NULL,
  alternate_terms TEXT NOT NULL, description TEXT NOT NULL,
  hpo_term TEXT NOT NULL, hpo_id TEXT NOT NULL, ncit TEXT NOT NULL,
  mesh TEXT NOT NULL, icd10 TEXT NOT NULL, efo TEXT NOT NULL,
  oae TEXT NOT NULL, last_updated TEXT NOT NULL, parent_terms TEXT NOT NULL
);
CREATE TABLE variants(
  chrom TEXT NOT NULL, pos INTEGER NOT NULL, ref TEXT NOT NULL,
  alt TEXT NOT NULL, record_id TEXT NOT NULL, short_name TEXT NOT NULL,
  class_code TEXT NOT NULL, relevant_subjects INTEGER,
  relevant_subjects_raw TEXT NOT NULL,
  source_chrom TEXT NOT NULL, source_pos INTEGER NOT NULL,
  source_ref TEXT NOT NULL, source_alt TEXT NOT NULL,
  PRIMARY KEY(chrom,pos,ref,alt,record_id)
);
CREATE INDEX variants_allele_lookup ON variants(chrom,pos,ref,alt);
CREATE TABLE rejected_variants(
  source_record_id TEXT NOT NULL, source_locus TEXT NOT NULL,
  reason TEXT NOT NULL
);
"""

# A SQLite quick check verifies page-level integrity, not that a database has
# the GenIA schema this reader expects.  Keep the read contract explicit so a
# truncated, hand-edited, or future-schema database is reported unavailable
# before a component capability is advertised to the rest of the workbench.
EXPECTED_TABLE_COLUMNS = {
    "metadata": ("key", "value"),
    "components": (
        "id", "label", "source_name", "source_sha256", "schema_fingerprint",
        "record_count", "installed_at", "release_hint",
    ),
    "gei_relationships": (
        "source_id", "disease_name", "synonyms", "gene_symbol", "moi", "moa",
        "publication_year", "iuis_classification", "curation_status",
        "curation_status_raw", "case_count", "family_count", "omim_id",
        "mondo_id", "clingen_class", "last_updated",
    ),
    "diseases": (
        "disease_id", "term", "acronym", "alternate_terms", "gene_id",
        "gene_symbol", "moi", "moa", "publication_year", "iei", "iuis_table",
        "omim_id", "mondo_id", "clingen_class", "clingen_review_date",
        "last_updated", "case_count", "family_count",
    ),
    "disease_phenotypes": (
        "id", "disease_id", "disease_name", "omim_id", "gene_id",
        "gene_symbol", "moi", "clinical_term_id", "clinical_term", "hpo_term",
        "hpo_id", "rank", "count_yes", "percent_yes", "count_no", "percent_no",
        "count_unreported", "percent_unreported",
    ),
    "phenotype_terms": (
        "clinical_term_id", "term", "alternate_terms", "description", "hpo_term",
        "hpo_id", "ncit", "mesh", "icd10", "efo", "oae", "last_updated",
        "parent_terms",
    ),
    "variants": (
        "chrom", "pos", "ref", "alt", "record_id", "short_name", "class_code",
        "relevant_subjects", "relevant_subjects_raw", "source_chrom", "source_pos",
        "source_ref", "source_alt",
    ),
    "rejected_variants": ("source_record_id", "source_locus", "reason"),
}

COMPONENT_PRIMARY_TABLE = {
    role: tables[0] for role, tables in COMPONENT_TABLES.items()
}


_INSTALL_LOCKS_GUARD = threading.Lock()
_INSTALL_LOCKS: dict[str, threading.Lock] = {}


def _install_lock(database: Path) -> threading.Lock:
    """Return the process-wide install lock for one derived GenIA database."""
    key = str(database.expanduser().resolve())
    with _INSTALL_LOCKS_GUARD:
        return _INSTALL_LOCKS.setdefault(key, threading.Lock())


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _clean(value: object) -> str:
    text = re.sub(
        r"\s+", " ", ("" if value is None else str(value)).replace("\xa0", " ")
    ).strip()
    # GenIA tabular exports use these exact tokens for missing cells.  Store
    # absence as absence so the review UI cannot accidentally turn "NA" into
    # an identifier, inheritance mode, or disease assertion.
    return "" if text.casefold() in {"na", "n/a", "null", "none", ".", "-"} else text


def _header_key(value: object) -> str:
    text = str(value or "").lstrip("\ufeff#").strip()
    text = re.sub(r"\[[^]]*\]", "", text)
    text = text.replace("#", "number_").replace("'", "")
    return re.sub(r"[^a-z0-9]+", "_", text.casefold()).strip("_")


def canonical_mondo(value: object) -> str:
    text = _clean(value).upper().removeprefix("MONDO:")
    if not text:
        return ""
    return f"MONDO:{text.zfill(7)}" if text.isdigit() else _clean(value)


def canonical_omim(value: object) -> str:
    return _clean(value).upper().removeprefix("OMIM:")


def normalize_curation(value: object) -> str:
    raw = _clean(value).casefold()
    return {
        "yes": "curated",
        "ongoing": "ongoing",
        "no": "not_curated",
    }.get(raw, "unknown")


def _is_gzip(path: Path) -> bool:
    with path.open("rb") as handle:
        return handle.read(2) == b"\x1f\x8b"


@contextmanager
def open_text(path: Path) -> Iterator[TextIO]:
    if _is_gzip(path):
        with gzip.open(path, "rt", encoding="utf-8-sig", newline="") as handle:
            yield handle
    else:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            yield handle


def _reject_bad_source(path: Path) -> None:
    if not path.is_file() or path.stat().st_size == 0:
        raise ValueError(f"{path.name} is missing or empty")
    with path.open("rb") as handle:
        prefix = handle.read(4096)
    if prefix.startswith(b"\x1f\x8b"):
        try:
            with gzip.open(path, "rb") as handle:
                prefix = handle.read(4096)
        except (OSError, EOFError) as exc:
            raise ValueError(f"{path.name} is not a readable gzip file") from exc
    lowered = prefix.lstrip().lower()
    if lowered.startswith(b"<!doctype html") or lowered.startswith(b"<html"):
        raise ValueError(f"{path.name} contains a web page instead of GenIA data")


def _table_header(path: Path) -> tuple[list[str], str]:
    with open_text(path) as handle:
        for line in handle:
            if not line.strip() or line.startswith("##"):
                continue
            delimiter = "\t" if line.count("\t") >= line.count(",") else ","
            header = next(csv.reader([line.lstrip("#")], delimiter=delimiter))
            return [_header_key(value) for value in header], delimiter
    raise ValueError(f"{path.name} does not contain a table header")


_GENIA_VCF_INFO_CONTRACT = {
    "Variant": ("1", "String"),
    "Class": ("1", "String"),
    "Relevant_in": ("1", "String"),
}


def _vcf_header_attribute(line: str, name: str) -> str:
    match = re.search(
        rf"(?:<|,)\s*{re.escape(name)}\s*=\s*(?:\"([^\"]*)\"|([^,>]*))",
        line,
        re.IGNORECASE,
    )
    return (match.group(1) if match and match.group(1) is not None else
            match.group(2) if match else "").strip()


def _assembly_family(value: str) -> str:
    value = value.strip().strip('"').casefold()
    if re.search(r"(?<![a-z0-9])(?:grch38|hg38)(?![a-z0-9])", value):
        return "GRCh38"
    if re.search(r"(?<![a-z0-9])(?:grch37|hg19)(?![a-z0-9])", value):
        return "GRCh37"
    return ""


def _inspect_genia_vcf_header(path: Path) -> dict[str, object]:
    """Validate the identifying schema and structured assembly declarations."""
    info_contract: dict[str, tuple[str, str]] = {}
    contig_assemblies: list[str] = []
    reference_values: list[str] = []
    saw_fileformat = False
    saw_columns = False
    with open_text(path) as handle:
        for line_number, line in enumerate(handle, start=1):
            line = line.rstrip("\r\n")
            if line_number == 1:
                saw_fileformat = line.startswith("##fileformat=VCF")
                if not saw_fileformat:
                    raise ValueError(f"{path.name} is not a VCF export")
            if line.startswith("##INFO=<"):
                info_id = _vcf_header_attribute(line, "ID")
                if info_id:
                    info_contract[info_id] = (
                        _vcf_header_attribute(line, "Number"),
                        _vcf_header_attribute(line, "Type"),
                    )
            elif line.lower().startswith("##contig=<"):
                assembly = _vcf_header_attribute(line, "assembly")
                if assembly:
                    contig_assemblies.append(assembly)
            elif line.lower().startswith("##reference="):
                reference_values.append(line.partition("=")[2].strip())
            elif line.lower().startswith("##assembly="):
                reference_values.append(line.partition("=")[2].strip())
            elif line.startswith("#CHROM"):
                columns = line.split("\t")
                if columns[:8] != [
                    "#CHROM", "POS", "ID", "REF", "ALT", "QUAL", "FILTER", "INFO"
                ]:
                    raise ValueError(f"{path.name} does not have the required VCF columns")
                saw_columns = True
                break
            elif line and not line.startswith("#"):
                raise ValueError(f"{path.name} has a VCF record before its #CHROM header")
    if not saw_fileformat or not saw_columns:
        raise ValueError(f"{path.name} does not contain a complete VCF header")

    missing = sorted(set(_GENIA_VCF_INFO_CONTRACT) - set(info_contract))
    if missing:
        raise ValueError(
            f"{path.name} is not a recognized GenIA variant export; missing INFO definitions: "
            + ", ".join(missing)
        )
    incompatible = sorted(
        info_id
        for info_id, expected in _GENIA_VCF_INFO_CONTRACT.items()
        if tuple(value.casefold() for value in info_contract[info_id])
        != tuple(value.casefold() for value in expected)
    )
    if incompatible:
        raise ValueError(
            f"{path.name} has incompatible GenIA INFO definitions: "
            + ", ".join(incompatible)
        )

    # GenIA's current export supplies assembly=hg38 on its ##contig records.
    # A structured ##reference= declaration is accepted as an alternative,
    # while incidental mentions inside descriptions or provenance are ignored.
    contig_families = {_assembly_family(value) for value in contig_assemblies}
    if "" in contig_families:
        raise ValueError(f"{path.name} contains an unrecognized contig assembly declaration")
    reference_families = {
        family for value in reference_values if (family := _assembly_family(value))
    }
    families = contig_families | reference_families
    if "GRCh37" in families or "GRCh38" not in families:
        raise ValueError(
            f"{path.name} must consistently declare GRCh38/hg38 in ##contig assembly or ##reference metadata"
        )
    return {
        "info_contract": info_contract,
        "assembly": "GRCh38",
        "assembly_declarations": sorted(set(contig_assemblies + reference_values)),
    }


def detect_component(path: Path) -> dict[str, str]:
    path = path.expanduser().resolve()
    try:
        _reject_bad_source(path)
        with open_text(path) as handle:
            first = handle.readline().lstrip("\ufeff")
        if first.startswith("##fileformat=VCF"):
            header = _inspect_genia_vcf_header(path)
            role = "variant_vcf"
            fingerprint = "vcf:" + ",".join(
                f"{info_id}:{number}:{type_name}"
                for info_id, (number, type_name) in sorted(
                    header["info_contract"].items()
                )
                if info_id in _GENIA_VCF_INFO_CONTRACT
            ) + f":{header['assembly']}"
        else:
            header, _ = _table_header(path)
            fields = set(header)
            signatures = (
                ("gei_disease", {"id", "conditions_name", "gene", "curated"}),
                ("disease_catalog", {"id", "term", "acronym", "gene_id", "gene", "moi", "moa", "iei"}),
                ("disease_phenotypes", {"disease_id", "disease_name", "gene", "clinterm_id", "hpo_id", "rank", "count_yes"}),
                ("phenotype_vocabulary", {"id", "term", "description", "hpo_id", "parent_terms"}),
            )
            matches = [name for name, required in signatures if required <= fields]
            if len(matches) != 1:
                raise ValueError(f"{path.name} does not have a recognized GenIA export schema")
            role = matches[0]
            fingerprint = "table:" + ",".join(header)
    except (OSError, EOFError, UnicodeError, csv.Error) as exc:
        raise ValueError(
            f"{path.name} is not a readable GenIA export; do not select the VCF .csi index: {exc}"
        ) from exc
    return {
        "role": role,
        "label": COMPONENT_LABELS[role],
        "name": path.name,
        "path": str(path),
        "schema_fingerprint": hashlib.sha256(fingerprint.encode()).hexdigest(),
    }


def inspect_genia_sources(paths: list[Path]) -> dict[str, object]:
    if not paths:
        raise ValueError("select at least one GenIA export")
    detected = [detect_component(path) for path in paths]
    roles = [item["role"] for item in detected]
    duplicates = sorted({role for role in roles if roles.count(role) > 1})
    if duplicates:
        labels = ", ".join(COMPONENT_LABELS[role] for role in duplicates)
        raise ValueError(f"select only one file for each GenIA component; duplicates: {labels}")
    return {
        "files": detected,
        "selected_components": roles,
        "missing_components": [role for role in COMPONENT_LABELS if role not in roles],
    }


def _iter_table(path: Path) -> Iterator[dict[str, str]]:
    header, delimiter = _table_header(path)
    with open_text(path) as handle:
        reader = csv.reader(handle, delimiter=delimiter)
        found_header = False
        for values in reader:
            if not values or not any(value.strip() for value in values):
                continue
            if not found_header:
                candidate = [_header_key(value) for value in values]
                if candidate == header:
                    found_header = True
                continue
            if values[0].startswith("#"):
                continue
            yield {
                header[index]: _clean(values[index]) if index < len(values) else ""
                for index in range(len(header))
            }
    if not found_header:
        raise ValueError(f"{path.name} does not contain its detected header")


def _integer(value: object) -> int | None:
    text = _clean(value)
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def _number(value: object) -> float | None:
    text = _clean(value).removesuffix("%")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _import_gei(connection: sqlite3.Connection, path: Path) -> int:
    count = 0
    for row in _iter_table(path):
        gene, disease = row.get("gene", "").upper(), row.get("conditions_name", "")
        if not gene or not disease:
            continue
        connection.execute(
            "INSERT OR REPLACE INTO gei_relationships VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                row.get("id", ""), disease, row.get("synonyms", ""), gene,
                row.get("moi", ""), row.get("moa", ""), row.get("pub_year", ""),
                row.get("iuis_classification", ""), normalize_curation(row.get("curated", "")),
                row.get("curated", ""), row.get("number_cases", ""),
                row.get("number_fams", ""), canonical_omim(row.get("omim", "")),
                canonical_mondo(row.get("mondo", "")), row.get("clingen_class", ""),
                row.get("lastupdated", ""),
            ),
        )
        count += 1
    return count


def _import_diseases(connection: sqlite3.Connection, path: Path) -> int:
    count = 0
    for row in _iter_table(path):
        disease_id = row.get("id", "")
        if not disease_id:
            continue
        connection.execute(
            "INSERT OR REPLACE INTO diseases VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                disease_id, row.get("term", ""), row.get("acronym", ""),
                row.get("alt_term", ""), row.get("gene_id", ""), row.get("gene", "").upper(),
                row.get("moi", ""), row.get("moa", ""), row.get("pub_year", ""),
                row.get("iei", ""), row.get("iuis_tbl", ""), canonical_omim(row.get("omim_id", "")),
                canonical_mondo(row.get("mondo_id", "")), row.get("clingen_class", ""),
                row.get("clingen_rev_date", ""), row.get("last_updated", ""),
                row.get("num_cases", ""), row.get("num_fams", ""),
            ),
        )
        count += 1
    return count


def _import_disease_phenotypes(connection: sqlite3.Connection, path: Path) -> int:
    count = 0
    for row in _iter_table(path):
        if not row.get("disease_id") or not row.get("gene"):
            continue
        connection.execute(
            """INSERT INTO disease_phenotypes(
              disease_id,disease_name,omim_id,gene_id,gene_symbol,moi,
              clinical_term_id,clinical_term,hpo_term,hpo_id,rank,
              count_yes,percent_yes,count_no,percent_no,count_unreported,percent_unreported
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                row.get("disease_id", ""), row.get("disease_name", ""),
                canonical_omim(row.get("omim_id", "")), row.get("gene_id", ""),
                row.get("gene", "").upper(), row.get("moi", ""),
                row.get("clinterm_id", ""), row.get("clinterm", ""),
                row.get("hpo_term", ""), row.get("hpo_id", ""), _integer(row.get("rank")),
                _integer(row.get("count_yes")), _number(row.get("percent_yes")),
                _integer(row.get("count_no")), _number(row.get("percent_no")),
                _integer(row.get("count_unrep")), _number(row.get("percent_unrep")),
            ),
        )
        count += 1
    return count


def _import_phenotypes(connection: sqlite3.Connection, path: Path) -> int:
    count = 0
    for row in _iter_table(path):
        term_id = row.get("id", "")
        if not term_id:
            continue
        connection.execute(
            "INSERT OR REPLACE INTO phenotype_terms VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                term_id, row.get("term", ""), row.get("alt_term", ""),
                row.get("description", ""), row.get("hpo_term", ""), row.get("hpo_id", ""),
                row.get("ncit", ""), row.get("mesh", ""), row.get("icd10", ""),
                row.get("efo", ""), row.get("oae", ""), row.get("last_updated", ""),
                row.get("parent_terms", ""),
            ),
        )
        count += 1
    return count


def _vcf_info(raw: str) -> dict[str, str]:
    return {
        key: value
        for item in raw.split(";")
        if "=" in item
        for key, value in [item.split("=", 1)]
    }


def _import_variants(
    connection: sqlite3.Connection,
    path: Path,
    reference_fasta: Path | None = None,
) -> tuple[int, int]:
    count = rejected = 0
    _inspect_genia_vcf_header(path)
    reference = None
    if reference_fasta is not None:
        try:
            from pipeline.loftee_ptc_50bp import IndexedFasta
            reference = IndexedFasta(reference_fasta)
        except (OSError, ValueError) as exc:
            raise ValueError(f"the GRCh38 reference could not be opened for GenIA normalization: {exc}") from exc
    try:
        with open_text(path) as handle:
            for line in handle:
                if line.startswith("##"):
                    continue
                if line.startswith("#"):
                    continue
                columns = line.rstrip("\r\n").split("\t")
                if len(columns) < 8:
                    raise ValueError(f"{path.name} contains a malformed VCF record")
                chrom, raw_pos, record_id, raw_ref, raw_alts = columns[:5]
                for raw_alt in raw_alts.split(","):
                    source_locus = f"{chrom}:{raw_pos}:{raw_ref}:{raw_alt}"
                    try:
                        pos = int(raw_pos)
                        if pos < 1 or not record_id or record_id == ".":
                            raise ValueError("record ID and positive position are required")
                        if "N" in raw_ref.upper() or "N" in raw_alt.upper():
                            raise ValueError("ambiguous N allele is not eligible for exact matching")
                        normalized_chrom = normalize_chrom(chrom)
                        fetch_base = (
                            (lambda coordinate, contig=normalized_chrom: reference.fetch(contig, coordinate, coordinate))
                            if reference is not None else None
                        )
                        pos, ref, alt = normalize_allele(pos, raw_ref, raw_alt, fetch_base)
                        if reference is not None:
                            observed = reference.fetch(normalized_chrom, pos, pos + len(ref) - 1)
                            if observed != ref:
                                raise ValueError("REF does not match the configured GRCh38 reference")
                        info = _vcf_info(columns[7])
                        relevant_raw = info.get("Relevant_in", "")
                        relevant_match = re.fullmatch(r"(\d+)_subjects?", relevant_raw)
                        relevant = int(relevant_match.group(1)) if relevant_match else None
                        if relevant_raw and relevant is None:
                            raise ValueError("Relevant_in is not formatted as N_subjects")
                        connection.execute(
                            "INSERT INTO variants VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (
                                normalized_chrom, pos, ref, alt, record_id,
                                info.get("Variant", ""), info.get("Class", "").upper(),
                                relevant, relevant_raw, chrom, int(raw_pos), raw_ref, raw_alt,
                            ),
                        )
                        count += 1
                    except (KeyError, ValueError, sqlite3.IntegrityError) as exc:
                        connection.execute(
                            "INSERT INTO rejected_variants VALUES(?,?,?)",
                            (record_id, source_locus, str(exc)),
                        )
                        rejected += 1
    finally:
        if reference is not None:
            reference.close()
    return count, rejected


def _copy_unselected_components(
    connection: sqlite3.Connection, destination: Path, selected: set[str],
) -> None:
    if not destination.is_file():
        return
    # A complete reinstall is also the recovery route for a corrupt or
    # obsolete local index; nothing from the old database needs preserving.
    if selected == set(COMPONENT_LABELS):
        return
    try:
        connection.execute("ATTACH DATABASE ? AS previous", (str(destination),))
        previous_version = connection.execute(
            "SELECT value FROM previous.metadata WHERE key='schema_version'"
        ).fetchone()
        if not previous_version or int(previous_version[0]) != SCHEMA_VERSION:
            raise ValueError("the existing GenIA index has an unsupported schema; reinstall all components")
        for role, tables in COMPONENT_TABLES.items():
            if role in selected:
                continue
            component = connection.execute(
                "SELECT * FROM previous.components WHERE id=?", (role,)
            ).fetchone()
            if component is None:
                continue
            connection.execute(
                "INSERT INTO components SELECT * FROM previous.components WHERE id=?", (role,)
            )
            for table in tables:
                connection.execute(f"INSERT INTO {table} SELECT * FROM previous.{table}")
        connection.commit()
        connection.execute("DETACH DATABASE previous")
    except (sqlite3.Error, ValueError) as exc:
        try:
            connection.execute("DETACH DATABASE previous")
        except sqlite3.Error:
            pass
        raise ValueError(f"the existing GenIA index could not be preserved: {exc}") from exc


def _build_genia_database_unlocked(
    source_paths: list[Path],
    destination: Path,
    reference_fasta: Path | None = None,
    *,
    replace_unreadable: bool = False,
) -> dict[str, object]:
    """Install selected components atomically, preserving omitted components.

    ``replace_unreadable`` is deliberately opt-in.  It permits a valid subset
    to replace a derived index that fails validation, but never causes omitted
    components in a healthy index to be discarded.
    """
    inspection = inspect_genia_sources(source_paths)
    detected = inspection["files"]
    assert isinstance(detected, list)
    selected = {str(item["role"]) for item in detected}
    destination = destination.expanduser().resolve()
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ValueError(f"the GenIA index folder is unavailable: {exc}") from exc

    discard_unreadable = False
    if destination.is_file() and selected != set(COMPONENT_LABELS):
        current_status = GeniaStore(destination).status()
        if current_status.get("error"):
            if not replace_unreadable:
                raise ValueError(
                    "the existing GenIA index is unreadable; select all five components "
                    "or explicitly replace it with the selected subset "
                    "(replace_unreadable=True)"
                )
            discard_unreadable = True

    temporary = destination.with_suffix(f"{destination.suffix}.{uuid.uuid4().hex}.new")
    try:
        connection = sqlite3.connect(temporary)
    except (OSError, sqlite3.Error) as exc:
        raise ValueError(f"the temporary GenIA index could not be created: {exc}") from exc
    built = False
    warnings: list[str] = []
    try:
        connection.executescript(SCHEMA)
        if not discard_unreadable:
            _copy_unselected_components(connection, destination, selected)
        for item in detected:
            role = str(item["role"])
            path = Path(str(item["path"]))
            rejected = 0
            if role == "gei_disease":
                count = _import_gei(connection, path)
            elif role == "disease_catalog":
                count = _import_diseases(connection, path)
            elif role == "disease_phenotypes":
                count = _import_disease_phenotypes(connection, path)
            elif role == "phenotype_vocabulary":
                count = _import_phenotypes(connection, path)
            elif role == "variant_vcf":
                count, rejected = _import_variants(connection, path, reference_fasta)
                if rejected:
                    reasons = dict(connection.execute(
                        "SELECT reason,count(*) FROM rejected_variants GROUP BY reason"
                    ))
                    details: list[str] = []
                    ambiguous = int(reasons.pop(
                        "ambiguous N allele is not eligible for exact matching", 0
                    ))
                    if ambiguous:
                        details.append(
                            f"{ambiguous} contain ambiguous N bases"
                            if ambiguous != 1 else "1 contains ambiguous N bases"
                        )
                    ref_mismatch = int(reasons.pop(
                        "REF does not match the configured GRCh38 reference", 0
                    ))
                    if ref_mismatch:
                        details.append(
                            f"{ref_mismatch} have REF alleles that do not match the configured GRCh38 reference"
                            if ref_mismatch != 1 else
                            "1 has a REF allele that does not match the configured GRCh38 reference"
                        )
                    for reason, reason_count in sorted(reasons.items()):
                        details.append(f"{reason_count} failed validation ({reason})")
                    warnings.append(
                        f"{rejected} GenIA source allele{'s were' if rejected != 1 else ' was'} "
                        "not indexed for exact matching: " + "; ".join(details) + "."
                    )
            else:  # pragma: no cover - guarded by detection
                raise ValueError(f"unsupported GenIA component: {role}")
            if count < 1:
                raise ValueError(f"{path.name} produced no usable {COMPONENT_LABELS[role]} records")
            connection.execute(
                "INSERT INTO components VALUES(?,?,?,?,?,?,?,?)",
                (
                    role, COMPONENT_LABELS[role], path.name, sha256(path),
                    item["schema_fingerprint"], count, utc_now(),
                    (
                        "GRCh38;reference_left_aligned"
                        if role == "variant_vcf" and reference_fasta is not None
                        else "GRCh38;minimal"
                        if role == "variant_vcf"
                        else ""
                    ),
                ),
            )
        metadata = {
            "schema_version": str(SCHEMA_VERSION),
            "updated_at": utc_now(),
            "license": "User-provided GenIA exports; not redistributed by GUIDE-IEI",
        }
        connection.executemany("INSERT INTO metadata VALUES(?,?)", metadata.items())
        connection.commit()
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise ValueError(f"the prepared GenIA index failed its integrity check: {integrity}")
        GeniaStore._validate_schema(connection)
        built = True
    except (OSError, UnicodeError, csv.Error, sqlite3.Error) as exc:
        raise ValueError(f"the selected GenIA exports could not be imported: {exc}") from exc
    finally:
        connection.close()
        if not built:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
    try:
        temporary.replace(destination)
        try:
            destination.chmod(0o600)
        except OSError:
            pass
    except OSError as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise ValueError(f"the prepared GenIA index could not be activated: {exc}") from exc
    result = GeniaStore(destination).status()
    result.update({
        "updated_components": sorted(selected),
        "warnings": warnings,
        "replaced_unreadable": discard_unreadable,
    })
    return result


def build_genia_database(
    source_paths: list[Path],
    destination: Path,
    reference_fasta: Path | None = None,
    *,
    replace_unreadable: bool = False,
) -> dict[str, object]:
    """Serialize updates to one derived index so partial installs cannot race."""
    resolved_destination = destination.expanduser().resolve()
    with _install_lock(resolved_destination):
        return _build_genia_database_unlocked(
            source_paths,
            resolved_destination,
            reference_fasta=reference_fasta,
            replace_unreadable=replace_unreadable,
        )


def _open_ro(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro&immutable=1", uri=True)


class GeniaStore:
    def __init__(self, database: Path, reference_fasta: Path | None = None):
        self.database = database
        self.reference_fasta = reference_fasta

    @staticmethod
    def _rows(connection: sqlite3.Connection, query: str, parameters: tuple = ()) -> list[dict]:
        connection.row_factory = sqlite3.Row
        return [dict(row) for row in connection.execute(query, parameters).fetchall()]

    @staticmethod
    def _validate_schema(connection: sqlite3.Connection) -> tuple[dict[str, str], list[dict]]:
        for table, expected_columns in EXPECTED_TABLE_COLUMNS.items():
            actual_columns = tuple(
                row[1] for row in connection.execute(f"PRAGMA table_info({table})")
            )
            if actual_columns != expected_columns:
                detail = "missing" if not actual_columns else "has incompatible columns"
                raise ValueError(f"required table {table} is {detail}")

        metadata = dict(connection.execute("SELECT key,value FROM metadata"))
        try:
            schema_version = int(metadata.get("schema_version", ""))
        except (TypeError, ValueError) as exc:
            raise ValueError("metadata has no valid schema_version") from exc
        if schema_version != SCHEMA_VERSION:
            raise ValueError(
                f"unsupported GenIA schema version {schema_version}; expected {SCHEMA_VERSION}"
            )

        rows = GeniaStore._rows(connection, "SELECT * FROM components ORDER BY id")
        if not rows:
            raise ValueError("no installed component metadata")
        unknown = sorted({str(row["id"]) for row in rows} - set(COMPONENT_LABELS))
        if unknown:
            raise ValueError("unknown GenIA components: " + ", ".join(unknown))
        for row in rows:
            role = str(row["id"])
            try:
                declared_count = int(row["record_count"])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"component {role} has an invalid record count") from exc
            if declared_count < 1:
                raise ValueError(f"component {role} has no usable records")
            table = COMPONENT_PRIMARY_TABLE[role]
            observed_count = int(
                connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            )
            if observed_count != declared_count:
                raise ValueError(
                    f"component {role} record count mismatch: "
                    f"metadata={declared_count}, table={observed_count}"
                )
        installed_roles = {str(row["id"]) for row in rows}
        for role, tables in COMPONENT_TABLES.items():
            if role in installed_roles:
                continue
            for table in tables:
                observed_count = int(
                    connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                )
                if observed_count:
                    raise ValueError(
                        f"table {table} contains data without component metadata"
                    )
        return metadata, rows

    def status(self) -> dict[str, object]:
        empty = {
            "installed": False,
            "components": {},
            "capabilities": {
                "gene_disease": False,
                "phenotype_evidence": False,
                "phenotype_details": False,
                "variant_evidence": False,
            },
            "license": "Registration and user-provided GenIA exports required",
        }
        if not self.database.is_file():
            return empty
        try:
            with closing(_open_ro(self.database)) as connection:
                integrity = connection.execute("PRAGMA quick_check").fetchone()[0]
                if integrity != "ok":
                    raise ValueError(f"database quick check: {integrity}")
                metadata, rows = self._validate_schema(connection)
                components = {row["id"]: row for row in rows}
                if "variant_vcf" in components:
                    components["variant_vcf"]["rejected_record_count"] = int(
                        connection.execute("SELECT count(*) FROM rejected_variants").fetchone()[0]
                    )
            return {
                "installed": bool(components),
                "components": components,
                "capabilities": {
                    "gene_disease": bool({"gei_disease", "disease_catalog"} & components.keys()),
                    "phenotype_evidence": "disease_phenotypes" in components,
                    "phenotype_details": "phenotype_vocabulary" in components,
                    "variant_evidence": "variant_vcf" in components,
                },
                "updated_at": metadata.get("updated_at", ""),
                "license": metadata.get("license", empty["license"]),
            }
        except (OSError, UnicodeError, ValueError, sqlite3.Error) as exc:
            return {**empty, "error": f"GenIA index could not be validated: {exc}"}

    def install(
        self, source_paths: list[Path], *, replace_unreadable: bool = False,
    ) -> dict[str, object]:
        reference = (
            self.reference_fasta
            if self.reference_fasta is not None
            and self.reference_fasta.is_file()
            and Path(str(self.reference_fasta) + ".fai").is_file()
            and Path(str(self.reference_fasta) + ".gzi").is_file()
            else None
        )
        return build_genia_database(
            source_paths, self.database, reference_fasta=reference,
            replace_unreadable=replace_unreadable,
        )

    def inspect(self, source_paths: list[Path]) -> dict[str, object]:
        return inspect_genia_sources(source_paths)

    def gene(self, gene_symbol: str) -> dict[str, object]:
        status = self.status()
        result: dict[str, object] = {**status, "relationships": []}
        if not status.get("installed"):
            return result
        components = status.get("components") or {}
        symbol = gene_symbol.upper()
        with closing(_open_ro(self.database)) as connection:
            relationships: dict[str, dict] = {}
            if "gei_disease" in components:
                for row in self._rows(
                    connection,
                    "SELECT * FROM gei_relationships WHERE gene_symbol=? COLLATE NOCASE ORDER BY disease_name",
                    (symbol,),
                ):
                    catalog = None
                    if "disease_catalog" in components:
                        found = self._rows(
                            connection,
                            "SELECT * FROM diseases WHERE acronym=? COLLATE NOCASE AND gene_symbol=? COLLATE NOCASE LIMIT 1",
                            (row["source_id"], symbol),
                        )
                        catalog = found[0] if found else None
                    disease_id = (catalog or {}).get("disease_id", "")
                    key = disease_id or f"gei:{row['source_id']}:{row['disease_name'].casefold()}"
                    relationships[key] = {
                        **row,
                        "disease_id": disease_id,
                        "relationship_source": "gei",
                        "phenotypes": [],
                    }
            if "disease_catalog" in components:
                for row in self._rows(
                    connection,
                    "SELECT * FROM diseases WHERE gene_symbol=? COLLATE NOCASE AND upper(iei)='Y' ORDER BY term",
                    (symbol,),
                ):
                    key = row["disease_id"]
                    if key not in relationships:
                        relationships[key] = {
                            "source_id": row["acronym"], "disease_id": row["disease_id"],
                            "disease_name": row["term"], "synonyms": row["alternate_terms"],
                            "gene_symbol": row["gene_symbol"], "moi": row["moi"], "moa": row["moa"],
                            "publication_year": row["publication_year"],
                            "iuis_classification": row["iuis_table"],
                            "curation_status": "unknown", "curation_status_raw": "",
                            "case_count": row["case_count"], "family_count": row["family_count"],
                            "omim_id": row["omim_id"], "mondo_id": row["mondo_id"],
                            "clingen_class": row["clingen_class"],
                            "clingen_review_date": row["clingen_review_date"],
                            "last_updated": row["last_updated"],
                            "relationship_source": "disease_catalog", "phenotypes": [],
                        }
            if "disease_phenotypes" in components:
                phenotype_rows = self._rows(
                    connection,
                    """SELECT dp.*,pt.description AS phenotype_description,
                              pt.alternate_terms AS phenotype_alternate_terms,
                              pt.parent_terms AS phenotype_parent_terms
                       FROM disease_phenotypes dp
                       LEFT JOIN phenotype_terms pt ON pt.clinical_term_id=dp.clinical_term_id
                       WHERE dp.gene_symbol=? COLLATE NOCASE
                       ORDER BY dp.disease_name,dp.rank,dp.clinical_term""",
                    (symbol,),
                )
                for phenotype in phenotype_rows:
                    key = phenotype["disease_id"]
                    # The phenotype export and GEI list share disease names,
                    # while their stable numeric link is supplied only by the
                    # optional disease catalog.  Preserve the useful join when
                    # a user installs GEI + phenotypes without that catalog.
                    if key not in relationships:
                        matching_keys = [
                            relationship_key
                            for relationship_key, relationship in relationships.items()
                            if str(relationship.get("disease_name") or "").casefold()
                            == str(phenotype["disease_name"] or "").casefold()
                        ]
                        if len(matching_keys) == 1:
                            key = matching_keys[0]
                    if key not in relationships:
                        relationships[key] = {
                            "source_id": "", "disease_id": key,
                            "disease_name": phenotype["disease_name"], "synonyms": "",
                            "gene_symbol": phenotype["gene_symbol"], "moi": phenotype["moi"],
                            "moa": "", "publication_year": "", "iuis_classification": "",
                            "curation_status": "unknown", "curation_status_raw": "",
                            "case_count": "", "family_count": "", "omim_id": phenotype["omim_id"],
                            "mondo_id": "", "clingen_class": "", "last_updated": "",
                            "relationship_source": "phenotype_export", "phenotypes": [],
                        }
                    relationships[key]["phenotypes"].append(phenotype)
            result["relationships"] = list(relationships.values())
        return result

    def gei_genes(self) -> list[str]:
        status = self.status()
        components = status.get("components") or {}
        if "gei_disease" not in components:
            return []
        with closing(_open_ro(self.database)) as connection:
            return [
                row[0]
                for row in connection.execute(
                    "SELECT DISTINCT gene_symbol FROM gei_relationships "
                    "WHERE gene_symbol<>'' ORDER BY gene_symbol COLLATE NOCASE"
                )
            ]

    def variant(self, chrom: str, pos: int, ref: str, alt: str) -> dict[str, object]:
        status = self.status()
        available = bool((status.get("capabilities") or {}).get("variant_evidence"))
        if not available:
            return {**status, "available": False, "records": []}
        reference = None
        fetch_base = None
        variant_component = (status.get("components") or {}).get("variant_vcf") or {}
        needs_reference = "reference_left_aligned" in str(variant_component.get("release_hint", ""))
        if needs_reference:
            if self.reference_fasta is None or not self.reference_fasta.is_file():
                return {
                    **status,
                    "available": False,
                    "records": [],
                    "error": "The GRCh38 reference used to normalize this GenIA index is unavailable.",
                }
            try:
                from pipeline.loftee_ptc_50bp import IndexedFasta
                reference = IndexedFasta(self.reference_fasta)
                normalized_chrom = normalize_chrom(chrom)
                fetch_base = lambda coordinate: reference.fetch(normalized_chrom, coordinate, coordinate)
            except (KeyError, OSError, ValueError) as exc:
                return {
                    **status,
                    "available": False,
                    "records": [],
                    "error": f"The GRCh38 reference used to normalize this GenIA index could not be opened: {exc}",
                }
        try:
            pos, ref, alt = normalize_allele(int(pos), ref, alt, fetch_base)
        except (KeyError, ValueError) as exc:
            return {
                **status,
                "available": False,
                "records": [],
                "error": (
                    "This allele could not be normalized against the configured "
                    f"GRCh38 reference: {exc}"
                ),
            }
        finally:
            if reference is not None:
                reference.close()
        with closing(_open_ro(self.database)) as connection:
            records = self._rows(
                connection,
                """SELECT record_id,short_name,class_code,relevant_subjects,
                          relevant_subjects_raw,source_chrom,source_pos,source_ref,source_alt
                   FROM variants WHERE chrom=? AND pos=? AND ref=? AND alt=?
                   ORDER BY record_id""",
                (normalize_chrom(chrom), pos, ref, alt),
            )
        for record in records:
            record["class_label"] = CLASS_LABELS.get(record["class_code"], f"Unrecognized classification ({record['class_code'] or 'blank'})")
        return {**status, "available": True, "records": records}
