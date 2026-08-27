"""Versioned local gene-level knowledge resources.

Public HGNC, IUIS, and ClinGen assertions are kept separate from optional,
locally licensed OMIM data.  Nothing in this module writes gene annotations
back into a VCF: resource updates therefore do not require VEP reannotation.
"""

from __future__ import annotations

import csv
import hashlib
import html
import json
import re
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse



def _open_ro(path):
    """Read-only sqlite connection with a percent-encoded file: URI.

    URI mode parses '?' as the query string, '#' as a fragment, and decodes
    '%', so interpolating a raw filesystem path truncates or redirects the
    open for paths containing those characters. Path.as_uri() encodes them.
    """
    from pathlib import Path as _Path
    return sqlite3.connect(f"{_Path(path).resolve().as_uri()}?mode=ro&immutable=1", uri=True)


SCHEMA_VERSION = 2
OMIM_FILES = ("mim2gene.txt", "mimTitles.txt", "genemap2.txt", "morbidmap.txt")
OMIM_SCHEMA_VERSION = 4
OMIM_DOWNLOAD_HOSTS = frozenset({"omim.org", "www.omim.org", "data.omim.org"})
_OMIM_URL = re.compile(r"https://[^\s<>{}\[\]()\"']+", re.I)


def _unwrap_outlook_safe_link(value: str) -> str:
    """Return the target of an Outlook Safe Link without requesting it."""
    candidate = html.unescape(value).replace("\\&", "&").strip()
    for _ in range(2):
        parsed = urlparse(candidate)
        host = (parsed.hostname or "").lower()
        if not host.endswith(".safelinks.protection.outlook.com"):
            return candidate
        target = (parse_qs(parsed.query).get("url") or [""])[0].strip()
        if not target:
            raise ValueError("an Outlook Safe Link does not contain its destination")
        candidate = target
    return candidate


def validate_omim_download_url(value: str, expected_filename: str | None = None) -> tuple[str, str]:
    """Validate a credential-bearing OMIM URL without returning it in errors."""
    candidate = _unwrap_outlook_safe_link(value)
    parsed = urlparse(candidate)
    host = (parsed.hostname or "").lower()
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("an OMIM download link has an invalid port") from exc
    filename = unquote(Path(parsed.path).name)
    if (
        parsed.scheme.lower() != "https"
        or host not in OMIM_DOWNLOAD_HOSTS
        or port not in {None, 443}
        or parsed.username is not None
        or parsed.password is not None
        or filename not in OMIM_FILES
    ):
        raise ValueError("a recognized OMIM file must use its official HTTPS download link")
    if expected_filename and filename != expected_filename:
        raise ValueError(f"the OMIM response did not match {expected_filename}")
    return parsed._replace(fragment="").geturl(), filename


def extract_omim_download_urls(value: str) -> dict[str, str]:
    """Extract the four official files from a pasted email or link block.

    The returned URLs are intentionally for immediate, in-memory use only.
    Errors name files but never echo credential-bearing input.
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError("paste the OMIM download email or the four download links")
    if len(value) > 250_000:
        raise ValueError("the pasted OMIM link block is unexpectedly large")
    normalized = html.unescape(value).replace("\\&", "&")
    recognized: dict[str, str] = {}
    for raw in _OMIM_URL.findall(normalized):
        candidate = raw.rstrip(".,;:)]}")
        try:
            download_url, filename = validate_omim_download_url(candidate)
        except ValueError:
            # A copied email normally contains help, contact, and account-page
            # links in addition to the four data-file links. Ignore those.
            continue
        previous = recognized.get(filename)
        if previous and previous != download_url:
            raise ValueError(f"the pasted text contains conflicting links for {filename}")
        recognized[filename] = download_url
    missing = [filename for filename in OMIM_FILES if filename not in recognized]
    if missing:
        raise ValueError(
            "the pasted OMIM text is missing: " + ", ".join(missing)
        )
    return {filename: recognized[filename] for filename in OMIM_FILES}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _clean(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\xa0", " ")).strip()


def _pipe_values(value: str) -> list[str]:
    return [item.strip().strip('"') for item in value.split("|") if item.strip().strip('"')]


IUIS_MEASURE_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("Not assessed", re.compile(r"\b(?:not assessed|no data|unknown|not available)\b", re.I)),
    ("Absent / very low", re.compile(r"\b(?:absent|undetectable|very low|profound(?:ly)?|severe(?:ly)? low|agammaglobulin\w*|depletion)\b", re.I)),
    ("Reduced", re.compile(r"\b(?:low|decreas\w*|reduc\w*|lymphopen\w*|neutropen\w*|hypogammaglobulin\w*|pancytopen\w*|declin\w*)\b", re.I)),
    ("Increased", re.compile(r"\b(?:high|increas\w*|elevat\w*|expan\w*)\b", re.I)),
    ("Normal", re.compile(r"\b(?:normal|nl|near-normal)\b", re.I)),
    ("Variable / mixed", re.compile(r"\b(?:variab\w*|occasion\w*|sometimes|may be|normal to|normal or|low to normal|to high|to low)\b|/", re.I)),
    ("Functional / subset abnormality", re.compile(r"\b(?:poor|impair\w*|dysfunction\w*|defect\w*|response\w*|proliferat\w*|activat\w*|memory|transitional|naive|naïve|class switch\w*|treg|cytotoxic\w*)\b", re.I)),
)


def summarize_iuis_measure(value: str) -> list[str]:
    """Conservatively tag heterogeneous IUIS laboratory wording.

    Tags are deliberately non-exclusive: "normal count, low memory B cells"
    retains both Normal and Functional / subset abnormality, while the exact
    source wording remains available for interpretation.
    """
    value = _clean(value)
    if not value:
        return []
    labels = [label for label, pattern in IUIS_MEASURE_RULES if pattern.search(value)]
    if "Not assessed" in labels:
        return ["Not assessed"]
    return labels or ["Descriptive finding"]


IUIS_CELL_GROUP_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("NK / innate lymphoid", re.compile(r"\b(?:NK|NKT|iNKT|MAIT)\b", re.I)),
    ("Myeloid / phagocyte", re.compile(r"\b(?:myeloid|monocyte|macrophage|phagocyte|granulocyte|PMN|neutrophil)\w*\b", re.I)),
    ("Dendritic cell", re.compile(r"\b(?:dendritic|DC|pDC|MDC)\w*\b", re.I)),
    ("Stem / progenitor", re.compile(r"\b(?:hematopoietic stem|bone marrow|progenitor)\w*\b", re.I)),
    ("Platelet / erythroid", re.compile(r"\b(?:platelet|erythroid|red cell|anaemia|anemia|hemoglobin|Hb)\w*\b", re.I)),
    ("Epithelial / skin", re.compile(r"\b(?:epithelial|epidermis|keratinocyte|skin)\w*\b", re.I)),
    ("CNS / microglia", re.compile(r"\b(?:CNS|central nervous|microglia|brain)\w*\b", re.I)),
    ("Stromal / fibroblast", re.compile(r"\b(?:stromal|fibroblast)\w*\b", re.I)),
    ("Osteoclast", re.compile(r"\bosteoclast\w*\b", re.I)),
    ("Other lymphocyte", re.compile(r"\b(?:lymphocyte|T cell|B cell|Treg|gamma.?delta|g/d)\w*\b", re.I)),
    ("Eosinophil / mast cell", re.compile(r"\b(?:eosinophil|mast cell)\w*\b", re.I)),
)


def summarize_iuis_other_cells(value: str) -> list[str]:
    value = _clean(value)
    if not value:
        return []
    labels = [label for label, pattern in IUIS_CELL_GROUP_RULES if pattern.search(value)]
    return labels or ["Other cell or tissue"]


def _source_rows(path: Path, delimiter: str = "\t") -> tuple[list[str], list[list[str]]]:
    """Read comment-prefixed tabular releases while retaining the real header."""
    with path.open(encoding="utf-8-sig", newline="") as handle:
        lines = handle.readlines()
    header_index = next(
        (
            index for index, line in enumerate(lines)
            if delimiter in line and line.lstrip("#").strip()
        ),
        None,
    )
    if header_index is None:
        raise ValueError(f"empty resource: {path.name}")
    # ClinGen validity has four descriptive CSV rows before its column names.
    if delimiter == ",":
        header_index = next(
            (index for index, line in enumerate(lines) if "GENE SYMBOL" in line.upper()),
            header_index,
        )
    header_line = lines[header_index].lstrip("#").strip("\r\n ")
    header = next(csv.reader([header_line], delimiter=delimiter))
    rows: list[list[str]] = []
    for line in lines[header_index + 1 :]:
        if not line.strip() or line.startswith("#"):
            continue
        rows.append(next(csv.reader([line], delimiter=delimiter)))
    return [_clean(item) for item in header], rows


def _dict_rows(path: Path, delimiter: str = "\t") -> list[dict[str, str]]:
    header, rows = _source_rows(path, delimiter)
    return [
        {header[index]: _clean(row[index]) if index < len(row) else "" for index in range(len(header))}
        for row in rows
    ]


def _omim_rows(path: Path, required: tuple[tuple[str, ...], ...]) -> list[dict[str, str]]:
    """Read one official OMIM flat file and reject common bad downloads."""
    if not path.is_file() or path.stat().st_size == 0:
        raise ValueError(f"{path.name} is missing or empty")
    with path.open("rb") as handle:
        prefix = handle.read(4096).lstrip().lower()
    if prefix.startswith(b"<!doctype html") or prefix.startswith(b"<html"):
        raise ValueError(
            f"{path.name} contains a web page instead of OMIM data; "
            "the account link may be expired or unauthorized"
        )
    header, source_rows = _source_rows(path)
    missing: list[str] = []
    for alternatives in required:
        if not any(
            any(column == name or column.startswith(name) for column in header)
            for name in alternatives
        ):
            missing.append(" or ".join(alternatives))
    if missing:
        raise ValueError(
            f"{path.name} has an unrecognized OMIM schema; missing "
            + ", ".join(missing)
        )
    if not source_rows:
        raise ValueError(f"{path.name} contains no OMIM data rows")
    return [
        {
            header[index]: _clean(row[index]) if index < len(row) else ""
            for index in range(len(header))
        }
        for row in source_rows
    ]


PUBLIC_SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE resources(
  id TEXT PRIMARY KEY, release TEXT NOT NULL, source_url TEXT NOT NULL,
  source_sha256 TEXT NOT NULL, record_count INTEGER NOT NULL, imported_at TEXT NOT NULL
);
CREATE TABLE genes(
  hgnc_id TEXT PRIMARY KEY, symbol TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
  ensembl_gene_id TEXT, entrez_id TEXT, locus_type TEXT, status TEXT
);
CREATE TABLE gene_aliases(alias TEXT NOT NULL, hgnc_id TEXT NOT NULL,
  kind TEXT NOT NULL, PRIMARY KEY(alias,hgnc_id));
CREATE INDEX gene_alias_lookup ON gene_aliases(alias COLLATE NOCASE);
CREATE TABLE iuis_assertions(
  id INTEGER PRIMARY KEY, hgnc_id TEXT, gene_symbol TEXT, source_gene TEXT,
  disease TEXT, inheritance TEXT, mechanism TEXT, omim TEXT,
  t_cell_count TEXT, t_cell_summary TEXT, b_cell_count TEXT, b_cell_summary TEXT,
  immunoglobulin_levels TEXT, immunoglobulin_summary TEXT,
  neutrophil_count TEXT, neutrophil_summary TEXT,
  other_affected_cells TEXT, other_affected_cell_groups TEXT,
  associated_features TEXT, major_category TEXT, subcategory TEXT, source_row INTEGER
);
CREATE INDEX iuis_gene_lookup ON iuis_assertions(gene_symbol COLLATE NOCASE);
CREATE TABLE clingen_validity(
  id INTEGER PRIMARY KEY, hgnc_id TEXT, gene_symbol TEXT, disease TEXT,
  mondo_id TEXT, moi TEXT, sop TEXT, classification TEXT, report_url TEXT,
  classification_date TEXT, expert_panel TEXT
);
CREATE INDEX clingen_validity_gene_lookup ON clingen_validity(gene_symbol COLLATE NOCASE);
CREATE TABLE clingen_dosage(
  hgnc_id TEXT, gene_symbol TEXT PRIMARY KEY, entrez_id TEXT, cytoband TEXT,
  genomic_location TEXT, hi_score TEXT, hi_description TEXT, hi_disease_id TEXT,
  ts_score TEXT, ts_description TEXT, ts_disease_id TEXT,
  date_last_evaluated TEXT, hi_pmids TEXT, ts_pmids TEXT
);
"""


def build_public_database(
    *, hgnc: Path, iuis: Path, clingen_validity: Path, clingen_dosage: Path,
    destination: Path, releases: dict[str, dict[str, str]],
) -> dict[str, object]:
    """Build a deterministic public SQLite bundle from official/clean sources."""
    for path in (hgnc, iuis, clingen_validity, clingen_dosage):
        if not path.is_file():
            raise ValueError(f"gene resource not found: {path}")
    # Unique temp name: with a shared deterministic ".new" path, a concurrent
    # build's unlink removed the file this build still had open, and the last
    # rename could publish a partially populated database.
    temporary = destination.with_suffix(f"{destination.suffix}.{uuid.uuid4().hex}.new")
    temporary.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(temporary)
    counts: dict[str, int] = {}
    try:
        connection.executescript(PUBLIC_SCHEMA)
        connection.execute("INSERT INTO metadata VALUES('schema_version',?)", (str(SCHEMA_VERSION),))
        connection.execute("INSERT INTO metadata VALUES('generated_at',?)", (utc_now(),))

        hgnc_rows = _dict_rows(hgnc)
        required_hgnc = {"hgnc_id", "symbol", "name", "ensembl_gene_id", "entrez_id"}
        if not hgnc_rows or not required_hgnc.issubset(hgnc_rows[0]):
            raise ValueError("HGNC complete set has an unexpected schema")
        symbol_to_hgnc: dict[str, str] = {}
        aliases: list[tuple[str, str, str]] = []
        for row in hgnc_rows:
            hgnc_id, symbol = row["hgnc_id"], row["symbol"].upper()
            if not hgnc_id or not symbol:
                continue
            symbol_to_hgnc[symbol] = hgnc_id
            connection.execute(
                "INSERT INTO genes VALUES(?,?,?,?,?,?,?)",
                (hgnc_id, symbol, row["name"], row["ensembl_gene_id"], row["entrez_id"],
                 row.get("locus_type", ""), row.get("status", "")),
            )
            aliases.append((symbol, hgnc_id, "approved"))
            for column, kind in (("alias_symbol", "alias"), ("prev_symbol", "previous")):
                aliases.extend((value.upper(), hgnc_id, kind) for value in _pipe_values(row.get(column, "")))
        connection.executemany("INSERT OR IGNORE INTO gene_aliases VALUES(?,?,?)", aliases)
        # Resolve source symbols using approved names first and unambiguous aliases second.
        alias_map: dict[str, set[str]] = {}
        for alias, hgnc_id, _ in aliases:
            alias_map.setdefault(alias, set()).add(hgnc_id)
        def resolve(symbol: str) -> tuple[str, str]:
            normalized = symbol.upper()
            ids = alias_map.get(normalized, set())
            hgnc_id = next(iter(ids)) if len(ids) == 1 else symbol_to_hgnc.get(normalized, "")
            if not hgnc_id:
                return "", normalized
            approved = connection.execute("SELECT symbol FROM genes WHERE hgnc_id=?", (hgnc_id,)).fetchone()[0]
            return hgnc_id, approved

        iuis_rows = _dict_rows(iuis)
        for row in iuis_rows:
            hgnc_id, symbol = resolve(row.get("gene_symbol", "")) if row.get("gene_symbol") else ("", "")
            connection.execute(
                "INSERT INTO iuis_assertions(hgnc_id,gene_symbol,source_gene,disease,inheritance,mechanism,omim,t_cell_count,t_cell_summary,b_cell_count,b_cell_summary,immunoglobulin_levels,immunoglobulin_summary,neutrophil_count,neutrophil_summary,other_affected_cells,other_affected_cell_groups,associated_features,major_category,subcategory,source_row) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (hgnc_id, symbol, row.get("source_genetic_defect", ""), row.get("disease", ""),
                 row.get("inheritance", ""), row.get("mechanism", ""), row.get("omim", ""),
                 row.get("t_cell_count", ""), "|".join(summarize_iuis_measure(row.get("t_cell_count", ""))),
                 row.get("b_cell_count", ""), "|".join(summarize_iuis_measure(row.get("b_cell_count", ""))),
                 row.get("immunoglobulin_levels", ""), "|".join(summarize_iuis_measure(row.get("immunoglobulin_levels", ""))),
                 row.get("neutrophil_count", ""), "|".join(summarize_iuis_measure(row.get("neutrophil_count", ""))),
                 row.get("other_affected_cells", ""), "|".join(summarize_iuis_other_cells(row.get("other_affected_cells", ""))),
                 row.get("associated_features", ""),
                 row.get("major_category", ""), row.get("subcategory", ""), int(row.get("source_row") or 0)),
            )

        validity_rows = _dict_rows(clingen_validity, ",")
        for row in validity_rows:
            source_symbol = row.get("GENE SYMBOL", "")
            source_hgnc = row.get("GENE ID (HGNC)", "")
            source_hgnc = source_hgnc if source_hgnc.startswith("HGNC:") else f"HGNC:{source_hgnc}" if source_hgnc else ""
            hgnc_id, symbol = resolve(source_symbol)
            if source_hgnc in {item[0] for item in connection.execute("SELECT hgnc_id FROM genes WHERE hgnc_id=?", (source_hgnc,))}:
                hgnc_id = source_hgnc
                symbol = connection.execute("SELECT symbol FROM genes WHERE hgnc_id=?", (hgnc_id,)).fetchone()[0]
            connection.execute(
                "INSERT INTO clingen_validity(hgnc_id,gene_symbol,disease,mondo_id,moi,sop,classification,report_url,classification_date,expert_panel) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (hgnc_id, symbol or source_symbol.upper(), row.get("DISEASE LABEL", ""),
                 row.get("DISEASE ID (MONDO)", ""), row.get("MOI", ""), row.get("SOP", ""),
                 row.get("CLASSIFICATION", ""), row.get("ONLINE REPORT", ""),
                 row.get("CLASSIFICATION DATE", ""), row.get("GCEP", "")),
            )

        dosage_rows = _dict_rows(clingen_dosage)
        for row in dosage_rows:
            source_symbol = row.get("Gene Symbol", "")
            hgnc_id, symbol = resolve(source_symbol)
            # filter() drops blank middle slots ("123||456" -> "123|456")
            hi_pmids = "|".join(filter(None, (row.get(f"Haploinsufficiency PMID{i}", "") for i in range(1, 7))))
            ts_pmids = "|".join(filter(None, (row.get(f"Triplosensitivity PMID{i}", "") for i in range(1, 7))))
            connection.execute(
                "INSERT OR REPLACE INTO clingen_dosage VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (hgnc_id, symbol or source_symbol.upper(), row.get("Gene ID", ""), row.get("cytoBand", ""),
                 row.get("Genomic Location", ""), row.get("Haploinsufficiency Score", ""),
                 row.get("Haploinsufficiency Description", ""), row.get("Haploinsufficiency Disease ID", ""),
                 row.get("Triplosensitivity Score", ""), row.get("Triplosensitivity Description", ""),
                 row.get("Triplosensitivity Disease ID", ""), row.get("Date Last Evaluated", ""),
                 hi_pmids, ts_pmids),
            )

        counts = {
            "hgnc": connection.execute("SELECT count(*) FROM genes").fetchone()[0],
            "iuis": connection.execute("SELECT count(*) FROM iuis_assertions").fetchone()[0],
            "clingen_validity": connection.execute("SELECT count(*) FROM clingen_validity").fetchone()[0],
            "clingen_dosage": connection.execute("SELECT count(*) FROM clingen_dosage").fetchone()[0],
        }
        paths = {"hgnc": hgnc, "iuis": iuis, "clingen_validity": clingen_validity, "clingen_dosage": clingen_dosage}
        for resource_id, path in paths.items():
            release = releases.get(resource_id, {})
            connection.execute(
                "INSERT INTO resources VALUES(?,?,?,?,?,?)",
                (resource_id, release.get("release", ""), release.get("source_url", ""),
                 sha256(path), counts[resource_id], utc_now()),
            )
        connection.commit()
        connection.execute("PRAGMA optimize")
    finally:
        connection.close()
    temporary.replace(destination)
    return {"schema_version": SCHEMA_VERSION, "counts": counts, "path": str(destination)}


OMIM_SCHEMA = """
CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE genes(gene_mim TEXT, symbol TEXT, entrez_id TEXT, ensembl_gene_id TEXT,
  title TEXT, PRIMARY KEY(gene_mim,symbol));
CREATE INDEX omim_symbol_lookup ON genes(symbol COLLATE NOCASE);
CREATE TABLE phenotypes(id INTEGER PRIMARY KEY, gene_mim TEXT, symbol TEXT,
  phenotype_mim TEXT, phenotype TEXT, mapping_key TEXT, inheritance TEXT,
  cytoband TEXT, raw_phenotype TEXT, source_file TEXT);
CREATE INDEX omim_phenotype_symbol_lookup ON phenotypes(symbol COLLATE NOCASE);
"""


PHENOTYPE_RE = re.compile(r"^[{?\[]?\s*(.*?),\s*(\d{6})\s*\((\d)\)(?:,\s*(.*?))?\s*[}\]]?$", re.I)


def _parse_omim_phenotype(value: str) -> tuple[str, str, str, str]:
    match = PHENOTYPE_RE.match(_clean(value))
    if not match:
        return _clean(value).strip("{}[]? "), "", "", ""
    return tuple(_clean(item) for item in match.groups())  # type: ignore[return-value]


def _omim_field(row: dict[str, str], *names: str) -> str:
    """Read stable OMIM concepts across documented header-label changes."""
    for name in names:
        if name in row:
            return row[name]
    for name in names:
        for key, value in row.items():
            if key.startswith(name):
                return value
    return ""


def build_omim_database(source_dir: Path, destination: Path) -> dict[str, object]:
    """Import user-licensed OMIM flat files without copying the sources."""
    files = {name: source_dir / name for name in OMIM_FILES}
    missing = [name for name, path in files.items() if not path.is_file()]
    if missing:
        raise ValueError("OMIM folder is missing required files: " + ", ".join(missing))
    temporary = destination.with_suffix(f"{destination.suffix}.{uuid.uuid4().hex}.new")
    destination.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(temporary)
    built = False
    try:
        connection.executescript(OMIM_SCHEMA)
        rows = {
            "mimTitles.txt": _omim_rows(files["mimTitles.txt"], (
                (("MIM Number", "Mim Number")),
                (("Preferred Title; symbol", "Preferred Title")),
            )),
            "mim2gene.txt": _omim_rows(files["mim2gene.txt"], (
                (("MIM Number", "Mim Number")),
                (("MIM Entry Type",)),
                (("Approved Gene Symbol",)),
            )),
            "genemap2.txt": _omim_rows(files["genemap2.txt"], (
                (("MIM Number", "Mim Number")),
                (("Phenotypes", "Phenotype")),
                (("Approved Gene Symbol", "Gene Symbols")),
            )),
            "morbidmap.txt": _omim_rows(files["morbidmap.txt"], (
                (("MIM Number", "Mim Number")),
                (("Phenotype", "Phenotypes")),
                ((
                    "Gene/Locus And Other Related Symbols",
                    "Gene Symbols",
                    "Approved Gene Symbol",
                )),
            )),
        }
        titles: dict[str, str] = {}
        for row in rows["mimTitles.txt"]:
            mim = _omim_field(row, "MIM Number", "Mim Number")
            titles[mim] = row.get("Preferred Title; symbol", "") or row.get("Preferred Title", "")
        genes: dict[str, tuple[str, str, str]] = {}
        for row in rows["mim2gene.txt"]:
            mim = _omim_field(row, "MIM Number", "Mim Number")
            if _omim_field(row, "MIM Entry Type").lower() not in {"gene", "gene/phenotype"}:
                continue
            genes[mim] = (
                _omim_field(row, "Approved Gene Symbol").upper(),
                _omim_field(row, "Entrez Gene ID"),
                _omim_field(row, "Ensembl Gene ID"),
            )
        for mim, (symbol, entrez, ensembl) in genes.items():
            if symbol:
                connection.execute("INSERT OR REPLACE INTO genes VALUES(?,?,?,?,?)", (mim, symbol, entrez, ensembl, titles.get(mim, "")))

        seen: set[tuple[str, str, str, str, str]] = set()
        # genemap2 is the primary map. morbidmap is retained as a compatible
        # fallback, with stable approved symbols anchored through mim2gene so
        # aliases are not presented as independent associated genes.
        for filename in ("genemap2.txt", "morbidmap.txt"):
            for row in rows[filename]:
                gene_mim = _omim_field(row, "MIM Number", "Mim Number")
                approved = _omim_field(row, "Approved Gene Symbol").upper()
                mapped = genes.get(gene_mim, ("", "", ""))[0]
                symbol = mapped or approved
                if not symbol:
                    continue
                raw_values = row.get("Phenotype", "") or row.get("Phenotypes", "")
                phenotypes = [item.strip() for item in raw_values.split(";") if item.strip()]
                for raw in phenotypes:
                    phenotype, phenotype_mim, mapping_key, inheritance = _parse_omim_phenotype(raw)
                    # Inheritance is data, not identity: genemap2 carries it in
                    # the phenotype string while morbidmap's format never does,
                    # so keying on it would store the same association twice.
                    # genemap2 is processed first, so its richer row wins.
                    key = (
                        symbol, gene_mim, phenotype_mim or phenotype.casefold(),
                        mapping_key,
                    )
                    if key in seen:
                        continue
                    seen.add(key)
                    connection.execute(
                        "INSERT INTO phenotypes(gene_mim,symbol,phenotype_mim,phenotype,mapping_key,inheritance,cytoband,raw_phenotype,source_file) VALUES(?,?,?,?,?,?,?,?,?)",
                        (gene_mim, symbol, phenotype_mim, phenotype, mapping_key, inheritance,
                         row.get("Cyto Location", ""), raw, filename),
                    )
        metadata = {
            "schema_version": str(OMIM_SCHEMA_VERSION), "installed_at": utc_now(),
            "source_checksums": json.dumps({name: sha256(path) for name, path in files.items()}, sort_keys=True),
            "license": "User-provided OMIM data; not redistributed by this software",
        }
        connection.executemany("INSERT INTO metadata VALUES(?,?)", metadata.items())
        connection.commit()
        counts = {
            "genes": connection.execute("SELECT count(*) FROM genes").fetchone()[0],
            "phenotypes": connection.execute("SELECT count(*) FROM phenotypes").fetchone()[0],
        }
        if not counts["genes"] or not counts["phenotypes"]:
            raise ValueError("the OMIM files produced an empty gene or phenotype index")
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise ValueError("the prepared OMIM index failed its integrity check")
        built = True
    finally:
        connection.close()
        if not built:
            temporary.unlink(missing_ok=True)
    try:
        temporary.replace(destination)
        try:
            destination.chmod(0o600)
        except OSError:
            pass
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return {"installed": True, "counts": counts, "path": str(destination)}


class GeneKnowledgeStore:
    def __init__(self, public_database: Path, private_database: Path):
        self.public_database = public_database
        self.private_database = private_database

    @staticmethod
    def _rows(connection: sqlite3.Connection, query: str, parameters: tuple = ()) -> list[dict]:
        connection.row_factory = sqlite3.Row
        return [dict(row) for row in connection.execute(query, parameters).fetchall()]

    def status(self) -> dict[str, object]:
        resources: list[dict] = []
        error = ""
        if self.public_database.is_file():
            try:
                with closing(_open_ro(self.public_database)) as connection:
                    resources = self._rows(connection, "SELECT * FROM resources ORDER BY id")
            except sqlite3.Error as exc:
                error = f"Public gene-knowledge database could not be read: {exc}"
        else:
            error = "Bundled gene-knowledge database is missing"
        omim: dict[str, object] = {"installed": False, "license": "User installation required; OMIM data are not shipped"}
        if self.private_database.is_file():
            try:
                with closing(_open_ro(self.private_database)) as connection:
                    metadata = dict(connection.execute("SELECT key,value FROM metadata"))
                    omim = {
                        "installed": True, "installed_at": metadata.get("installed_at", ""),
                        "genes": connection.execute("SELECT count(*) FROM genes").fetchone()[0],
                        "phenotypes": connection.execute("SELECT count(*) FROM phenotypes").fetchone()[0],
                        "license": metadata.get("license", ""),
                    }
            except sqlite3.Error as exc:
                omim = {"installed": False, "error": str(exc), "license": "User installation required; OMIM data are not shipped"}
        return {"available": bool(resources), "error": error, "resources": resources, "omim": omim}

    def install_omim(self, source_dir: Path) -> dict[str, object]:
        return build_omim_database(source_dir.expanduser().resolve(), self.private_database)

    def _identity(self, connection: sqlite3.Connection, identifier: str) -> dict | None:
        key = identifier.split(".")[0].upper()
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT * FROM genes WHERE symbol=? COLLATE NOCASE OR hgnc_id=? OR ensembl_gene_id=?",
            (key, key, key),
        ).fetchone()
        if row:
            return dict(row)
        aliases = connection.execute(
            "SELECT g.* FROM gene_aliases a JOIN genes g USING(hgnc_id) WHERE a.alias=? COLLATE NOCASE",
            (key,),
        ).fetchall()
        return dict(aliases[0]) if len(aliases) == 1 else None

    def gene(self, identifier: str) -> dict[str, object]:
        result: dict[str, object] = {
            "query": identifier, "found": False, "identity": None, "aliases": [],
            "iuis": [], "clingen_validity": [], "clingen_dosage": None,
            "omim": [], "omim_installed": self.private_database.is_file(),
        }
        if not self.public_database.is_file():
            return result
        with closing(_open_ro(self.public_database)) as connection:
            identity = self._identity(connection, identifier)
            symbol = (identity or {}).get("symbol", identifier.upper())
            result.update({
                "found": bool(identity), "identity": identity,
                "aliases": self._rows(connection, "SELECT alias,kind FROM gene_aliases WHERE hgnc_id=? ORDER BY kind,alias", ((identity or {}).get("hgnc_id", ""),)),
                "iuis": self._rows(connection, "SELECT * FROM iuis_assertions WHERE gene_symbol=? COLLATE NOCASE ORDER BY major_category,disease", (symbol,)),
                "clingen_validity": self._rows(connection, "SELECT * FROM clingen_validity WHERE gene_symbol=? COLLATE NOCASE ORDER BY disease,moi", (symbol,)),
            })
            dosage = self._rows(connection, "SELECT * FROM clingen_dosage WHERE gene_symbol=? COLLATE NOCASE", (symbol,))
            result["clingen_dosage"] = dosage[0] if dosage else None
        if self.private_database.is_file():
            with closing(_open_ro(self.private_database)) as connection:
                # Group at read time as well: databases built before the
                # ingestion dedup keyed on inheritance hold the same
                # association twice (a genemap2 row carrying inheritance and
                # a bare morbidmap row); max() keeps the informative one.
                result["omim"] = self._rows(
                    connection,
                    "SELECT p.gene_mim,p.symbol,p.phenotype_mim,p.phenotype,p.mapping_key,"
                    "max(p.inheritance) AS inheritance,max(p.cytoband) AS cytoband,"
                    "max(p.raw_phenotype) AS raw_phenotype,min(p.source_file) AS source_file,"
                    "max(g.title) AS gene_title "
                    "FROM phenotypes p LEFT JOIN genes g USING(gene_mim,symbol) "
                    "WHERE p.symbol=? COLLATE NOCASE "
                    "GROUP BY p.gene_mim,p.symbol,p.phenotype_mim,p.phenotype,p.mapping_key "
                    "ORDER BY p.phenotype",
                    (symbol,),
                )
        return result

    def filter_catalog(self) -> dict[str, object]:
        if not self.public_database.is_file():
            return {"iuis_categories": [], "iuis_category_genes": {}, "omim_genes": []}
        with closing(_open_ro(self.public_database)) as connection:
            iuis_categories = self._rows(connection, "SELECT major_category AS category,count(DISTINCT gene_symbol) AS genes FROM iuis_assertions WHERE gene_symbol<>'' GROUP BY major_category ORDER BY major_category")
            iuis_category_genes: dict[str, list[str]] = {}
            for item in iuis_categories:
                iuis_category_genes[item["category"]] = [
                    row[0] for row in connection.execute(
                        "SELECT DISTINCT gene_symbol FROM iuis_assertions WHERE major_category=? AND gene_symbol<>'' ORDER BY gene_symbol",
                        (item["category"],),
                    )
                ]
        omim_genes: list[str] = []
        if self.private_database.is_file():
            with closing(_open_ro(self.private_database)) as connection:
                omim_genes = [row[0] for row in connection.execute("SELECT DISTINCT symbol FROM phenotypes ORDER BY symbol")]
        return {
            "iuis_categories": iuis_categories,
            "iuis_category_genes": iuis_category_genes,
            "omim_genes": omim_genes,
        }
