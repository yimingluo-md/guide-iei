#!/usr/bin/env python3
"""Local individual metadata and phenotype import support.

The store deliberately keeps reported race/ethnicity separate from any future
genetic ancestry result. CSV, TSV and XLSX parsing use only Python's standard
library so the workstation service remains lightweight and offline.
"""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import posixpath
import re
import sqlite3
import uuid
import zipfile
from collections import Counter, defaultdict
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree


MAX_UPLOAD_BYTES = 20 * 1024 * 1024
LIST_FIELDS = {
    "sample_ids", "reported_race", "reported_ethnicity",
    "present_features", "absent_features",
}
KNOWN_FIELDS = (
    "individual_id", "sample_ids", "sex_at_birth",
    "age_at_evaluation", "age_at_evaluation_unit",
    "age_at_onset", "age_at_onset_unit",
    "reported_race", "reported_ethnicity",
    "phenotype_summary", "present_features", "absent_features",
    "current_diagnosis", "notes", "source_date",
)
FIELD_ALIASES = {
    "individual_id": (
        "individualid", "subjectid", "patientid", "caseid", "studyid",
        "participantid", "individual", "subject", "patient", "case",
    ),
    "sample_ids": (
        "sampleid", "sampleids", "vcfsample", "vcfsampleid", "sequencingsample",
        "specimenid", "sample", "samples",
    ),
    "sex_at_birth": ("sexatbirth", "birthsex", "sex", "gender"),
    "age_at_evaluation": (
        "ageatevaluation", "ageatassessment", "currentage", "age",
    ),
    "age_at_evaluation_unit": ("ageatevaluationunit", "ageunit", "ageunits"),
    "age_at_onset": ("ageatonset", "onsetage", "ageofdiseaseonset"),
    "age_at_onset_unit": ("ageatonsetunit", "onsetageunit"),
    "reported_race": ("reportedrace", "selfreportedrace", "race"),
    "reported_ethnicity": (
        "reportedethnicity", "selfreportedethnicity", "ethnicity",
    ),
    "phenotype_summary": (
        "phenotypesummary", "clinicalsummary", "clinicalphenotype",
        "phenotype", "summary",
    ),
    "present_features": (
        "presentfeatures", "positivefeatures", "clinicalfeatures",
        "featurespresent", "phenotypespresent",
    ),
    "absent_features": (
        "absentfeatures", "negativefeatures", "pertinentnegatives",
        "featuresabsent", "phenotypesabsent",
    ),
    "current_diagnosis": (
        "currentdiagnosis", "workingdiagnosis", "diagnosis", "diagnoses",
    ),
    "notes": ("notes", "comments", "comment"),
    "source_date": (
        "sourcedate", "phenotypedate", "evaluationdate", "assessmentdate",
    ),
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def compact_header(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def clean(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def split_values(value: str) -> list[str]:
    return list(dict.fromkeys(
        item.strip() for item in re.split(r"[;|\n]+", value or "") if item.strip()
    ))


def parse_age(value: str) -> float | None:
    if not value:
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", value)
    if not match:
        raise ValueError(f"invalid age value: {value}")
    parsed = float(match.group())
    if parsed < 0 or parsed > 130:
        raise ValueError(f"age value is outside the supported range: {value}")
    return parsed


def infer_age_unit(value: str, explicit: str) -> str:
    unit = explicit.strip().casefold()
    source = value.casefold()
    if not unit:
        for token, normalized in (
            ("year", "years"), ("yr", "years"),
            ("month", "months"), ("mo", "months"),
            ("week", "weeks"), ("wk", "weeks"),
            ("day", "days"),
        ):
            if token in source:
                return normalized
        return "years"
    if unit.startswith(("y", "yr")):
        return "years"
    if unit.startswith(("m", "mo")):
        return "months"
    if unit.startswith(("w", "wk")):
        return "weeks"
    if unit.startswith("d"):
        return "days"
    raise ValueError(f"unsupported age unit: {explicit}")


def decode_upload(filename: str, encoded: str) -> bytes:
    if not filename or not encoded:
        raise ValueError("filename and content_base64 are required")
    try:
        content = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError("content_base64 is invalid") from exc
    if len(content) > MAX_UPLOAD_BYTES:
        raise ValueError("phenotype input is larger than 20 MB")
    return content


def _column_number(reference: str) -> int:
    letters = re.match(r"[A-Z]+", reference.upper())
    result = 0
    for letter in letters.group() if letters else "":
        result = result * 26 + ord(letter) - 64
    return max(0, result - 1)


def _xlsx_sheets(content: bytes) -> tuple[zipfile.ZipFile, list[tuple[str, str]]]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
        workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
        relationships = ElementTree.fromstring(
            archive.read("xl/_rels/workbook.xml.rels")
        )
    except (zipfile.BadZipFile, KeyError, ElementTree.ParseError) as exc:
        raise ValueError("the XLSX workbook is invalid or unsupported") from exc
    relation_targets = {
        relation.attrib["Id"]: relation.attrib["Target"]
        for relation in relationships
    }
    rel_key = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
    sheets = []
    for sheet in workbook.iter(
        "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}sheet"
    ):
        target = relation_targets.get(sheet.attrib.get(rel_key, ""))
        if not target:
            continue
        if target.startswith("/"):
            path = target.lstrip("/")
        else:
            path = posixpath.normpath(posixpath.join("xl", target))
        sheets.append((sheet.attrib.get("name", "Sheet"), path))
    if not sheets:
        raise ValueError("the XLSX workbook contains no readable sheets")
    return archive, sheets


def _xlsx_rows(content: bytes, requested_sheet: str | None) -> tuple[list[str], list[list[str]]]:
    archive, sheets = _xlsx_sheets(content)
    sheet_names = [name for name, _ in sheets]
    selected = requested_sheet if requested_sheet in sheet_names else sheet_names[0]
    sheet_path = dict(sheets)[selected]
    shared: list[str] = []
    try:
        shared_root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
        for item in shared_root.iter(
            "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}si"
        ):
            shared.append("".join(
                node.text or "" for node in item.iter(
                    "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t"
                )
            ))
    except KeyError:
        pass
    try:
        root = ElementTree.fromstring(archive.read(sheet_path))
    except (KeyError, ElementTree.ParseError) as exc:
        raise ValueError(f"the XLSX sheet {selected!r} cannot be read") from exc
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    rows: list[list[str]] = []
    for row in root.iter(f"{ns}row"):
        values: dict[int, str] = {}
        for cell in row.findall(f"{ns}c"):
            index = _column_number(cell.attrib.get("r", ""))
            cell_type = cell.attrib.get("t")
            raw_node = cell.find(f"{ns}v")
            raw = raw_node.text if raw_node is not None and raw_node.text else ""
            if cell_type == "s" and raw.isdigit() and int(raw) < len(shared):
                value = shared[int(raw)]
            elif cell_type == "inlineStr":
                value = "".join(
                    node.text or "" for node in cell.iter(f"{ns}t")
                )
            elif cell_type == "b":
                value = "TRUE" if raw == "1" else "FALSE"
            else:
                value = raw
            values[index] = clean(value)
        if values:
            width = max(values) + 1
            rows.append([values.get(index, "") for index in range(width)])
        else:
            rows.append([])
    archive.close()
    return sheet_names, rows


def _delimited_rows(content: bytes, filename: str) -> list[list[str]]:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("cp1252")
    sample = text[:65536]
    extension = Path(filename).suffix.casefold()
    delimiter = "\t" if extension == ".tsv" else ","
    try:
        delimiter = csv.Sniffer().sniff(sample, delimiters=",\t;").delimiter
    except csv.Error:
        pass
    return [
        [clean(value) for value in row]
        for row in csv.reader(io.StringIO(text), delimiter=delimiter)
    ]


def suggest_header_row(rows: list[list[str]]) -> int:
    candidates = rows[:20]
    if not candidates:
        return 1
    scored = []
    for index, row in enumerate(candidates):
        nonempty = [clean(value) for value in row if clean(value)]
        textual = sum(bool(re.search(r"[A-Za-z]", value)) for value in nonempty)
        unique = len(set(value.casefold() for value in nonempty))
        scored.append((textual * 3 + unique + len(nonempty), -index, index + 1))
    return max(scored)[2]


def unique_headers(row: list[str]) -> list[str]:
    result: list[str] = []
    counts: Counter[str] = Counter()
    for index, value in enumerate(row):
        base = clean(value) or f"Column {index + 1}"
        counts[base] += 1
        result.append(base if counts[base] == 1 else f"{base} ({counts[base]})")
    return result


def suggest_mapping(columns: list[str]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    normalized = {column: compact_header(column) for column in columns}
    used = set()
    for field, aliases in FIELD_ALIASES.items():
        exact = next(
            (column for column, value in normalized.items()
             if column not in used and value in aliases),
            None,
        )
        if exact:
            mapping[field] = exact
            used.add(exact)
    return mapping


def parse_table(
    filename: str,
    content: bytes,
    sheet: str | None = None,
    header_row: int | None = None,
) -> dict:
    extension = Path(filename).suffix.casefold()
    if extension == ".xlsx":
        sheet_names, rows = _xlsx_rows(content, sheet)
        selected_sheet = sheet if sheet in sheet_names else sheet_names[0]
    elif extension in {".csv", ".tsv", ".txt"}:
        sheet_names = []
        selected_sheet = None
        rows = _delimited_rows(content, filename)
    else:
        raise ValueError("phenotype input must be .csv, .tsv, or .xlsx")
    if not rows or not any(any(clean(value) for value in row) for row in rows):
        raise ValueError("phenotype input contains no data")
    suggested = suggest_header_row(rows)
    chosen = int(suggested if header_row is None else header_row)
    if chosen < 1 or chosen > len(rows):
        raise ValueError("header_row is outside the input")
    columns = unique_headers(rows[chosen - 1])
    data_rows = []
    for source_row in rows[chosen:]:
        padded = source_row + [""] * max(0, len(columns) - len(source_row))
        record = dict(zip(columns, padded[:len(columns)]))
        if any(clean(value) for value in record.values()):
            data_rows.append(record)
    return {
        "sheet_names": sheet_names,
        "selected_sheet": selected_sheet,
        "suggested_header_row": suggested,
        "header_row": chosen,
        "columns": columns,
        "suggested_mapping": suggest_mapping(columns),
        "row_count": len(data_rows),
        "rows": data_rows,
    }


def normalize_sex(value: str) -> str:
    normalized = value.strip().casefold()
    return {
        "f": "female", "female": "female",
        "m": "male", "male": "male",
        "unknown": "unknown", "unk": "unknown", "u": "unknown",
        "other": "other", "intersex": "other",
    }.get(normalized, value.strip())


class PhenotypeStore:
    def __init__(self, database_path: Path):
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=60)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=60000")
        return connection

    @contextmanager
    def _session(self):
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._session() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS phenotype_individuals (
                    individual_id TEXT PRIMARY KEY,
                    sex_at_birth TEXT,
                    age_at_evaluation REAL,
                    age_at_evaluation_unit TEXT,
                    age_at_onset REAL,
                    age_at_onset_unit TEXT,
                    reported_race_json TEXT NOT NULL DEFAULT '[]',
                    reported_ethnicity_json TEXT NOT NULL DEFAULT '[]',
                    phenotype_summary TEXT,
                    present_features_json TEXT NOT NULL DEFAULT '[]',
                    absent_features_json TEXT NOT NULL DEFAULT '[]',
                    current_diagnosis TEXT,
                    notes TEXT,
                    source_date TEXT,
                    custom_fields_json TEXT NOT NULL DEFAULT '{}',
                    source_name TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS phenotype_sample_links (
                    individual_id TEXT NOT NULL REFERENCES phenotype_individuals(individual_id)
                      ON DELETE CASCADE,
                    sample_id TEXT NOT NULL,
                    PRIMARY KEY(individual_id, sample_id)
                );

                CREATE TABLE IF NOT EXISTS phenotype_import_profiles (
                    name TEXT PRIMARY KEY,
                    mapping_json TEXT NOT NULL,
                    sheet_name TEXT,
                    header_row INTEGER NOT NULL DEFAULT 1,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS phenotype_import_runs (
                    id TEXT PRIMARY KEY,
                    source_name TEXT NOT NULL,
                    source_sha256 TEXT NOT NULL,
                    imported_at TEXT NOT NULL,
                    profile_name TEXT,
                    mapping_json TEXT NOT NULL,
                    row_count INTEGER NOT NULL,
                    created_count INTEGER NOT NULL,
                    updated_count INTEGER NOT NULL,
                    skipped_count INTEGER NOT NULL
                );

                CREATE INDEX IF NOT EXISTS phenotype_sample_links_sample_idx
                  ON phenotype_sample_links(sample_id);
                CREATE INDEX IF NOT EXISTS phenotype_individuals_updated_idx
                  ON phenotype_individuals(updated_at DESC);
                """
            )

    def stats(self) -> dict:
        with self._session() as connection:
            row = connection.execute(
                """
                SELECT
                  (SELECT COUNT(*) FROM phenotype_individuals) AS individuals,
                  (SELECT COUNT(*) FROM phenotype_sample_links) AS sample_links,
                  (SELECT COUNT(DISTINCT psl.sample_id)
                   FROM phenotype_sample_links psl
                   JOIN cohort_samples cs ON cs.name = psl.sample_id) AS matched_samples,
                  (SELECT COUNT(*) FROM phenotype_import_runs) AS imports
                """
            ).fetchone()
        return dict(row)

    def _serialize(self, row: sqlite3.Row, sample_ids: list[str]) -> dict:
        result = dict(row)
        for field in (
            "reported_race", "reported_ethnicity",
            "present_features", "absent_features", "custom_fields",
        ):
            result[field] = json.loads(result.pop(f"{field}_json") or "[]" if field != "custom_fields" else result.pop("custom_fields_json") or "{}")
        result["sample_ids"] = sample_ids
        return result

    def get(self, individual_id: str) -> dict | None:
        with self._session() as connection:
            row = connection.execute(
                "SELECT * FROM phenotype_individuals WHERE individual_id = ?",
                (individual_id,),
            ).fetchone()
            if not row:
                return None
            samples = [
                item["sample_id"] for item in connection.execute(
                    "SELECT sample_id FROM phenotype_sample_links "
                    "WHERE individual_id = ? ORDER BY sample_id",
                    (individual_id,),
                ).fetchall()
            ]
        return self._serialize(row, samples)

    def by_sample(self, sample_id: str) -> list[dict]:
        with self._session() as connection:
            ids = [
                row["individual_id"] for row in connection.execute(
                    "SELECT individual_id FROM phenotype_sample_links "
                    "WHERE sample_id = ? COLLATE NOCASE ORDER BY individual_id",
                    (sample_id,),
                ).fetchall()
            ]
        return [record for value in ids if (record := self.get(value))]

    def list(self, query: str = "", limit: int = 500) -> list[dict]:
        parameters: list = []
        where = "1"
        if query.strip():
            token = f"%{query.strip()}%"
            where = """
              (p.individual_id LIKE ? COLLATE NOCASE
               OR p.phenotype_summary LIKE ? COLLATE NOCASE
               OR p.present_features_json LIKE ? COLLATE NOCASE
               OR p.absent_features_json LIKE ? COLLATE NOCASE
               OR p.current_diagnosis LIKE ? COLLATE NOCASE
               OR EXISTS (
                 SELECT 1 FROM phenotype_sample_links link
                 WHERE link.individual_id = p.individual_id
                   AND link.sample_id LIKE ? COLLATE NOCASE
               ))
            """
            parameters = [token] * 6
        with self._session() as connection:
            ids = [
                row["individual_id"] for row in connection.execute(
                    f"SELECT p.individual_id FROM phenotype_individuals p "
                    f"WHERE {where} ORDER BY p.updated_at DESC LIMIT ?",
                    (*parameters, max(1, min(int(limit), 5000))),
                ).fetchall()
            ]
        return [record for value in ids if (record := self.get(value))]

    def profiles(self) -> list[dict]:
        with self._session() as connection:
            rows = connection.execute(
                "SELECT * FROM phenotype_import_profiles ORDER BY name"
            ).fetchall()
        return [
            {
                "name": row["name"],
                "mapping": json.loads(row["mapping_json"]),
                "sheet_name": row["sheet_name"],
                "header_row": row["header_row"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]

    def preview(self, payload: dict) -> dict:
        filename = clean(payload.get("filename"))
        content = decode_upload(filename, payload.get("content_base64", ""))
        parsed = parse_table(
            filename, content, payload.get("sheet"), payload.get("header_row")
        )
        parsed["rows"] = parsed["rows"][:25]
        return parsed

    def _records(self, payload: dict) -> tuple[bytes, dict, list[dict], dict]:
        filename = clean(payload.get("filename"))
        content = decode_upload(filename, payload.get("content_base64", ""))
        parsed = parse_table(
            filename, content, payload.get("sheet"), payload.get("header_row")
        )
        mapping = payload.get("mapping")
        if not isinstance(mapping, dict) or not mapping.get("individual_id"):
            raise ValueError("the individual ID column must be mapped")
        invalid_columns = [
            column for column in mapping.values()
            if column and column not in parsed["columns"]
        ]
        if invalid_columns:
            raise ValueError(f"mapped column does not exist: {invalid_columns[0]}")
        records = [
            self._normalize_row(
                row, mapping, bool(payload.get("preserve_unmapped", True))
            )
            for row in parsed["rows"]
        ]
        records = [record for record in records if record["individual_id"]]
        if not records:
            raise ValueError("no rows contain an individual ID")
        return content, parsed, records, mapping

    def _normalize_row(
        self, row: dict[str, str], mapping: dict[str, str], preserve_unmapped: bool
    ) -> dict:
        def value(field: str) -> str:
            return clean(row.get(mapping.get(field, ""), ""))

        age_evaluation_raw = value("age_at_evaluation")
        age_onset_raw = value("age_at_onset")
        mapped_columns = {column for column in mapping.values() if column}
        custom = {
            key: clean(item) for key, item in row.items()
            if preserve_unmapped and key not in mapped_columns and clean(item)
        }
        return {
            "individual_id": value("individual_id"),
            "sample_ids": split_values(value("sample_ids")),
            "sex_at_birth": normalize_sex(value("sex_at_birth")),
            "age_at_evaluation": parse_age(age_evaluation_raw),
            "age_at_evaluation_unit": (
                infer_age_unit(age_evaluation_raw, value("age_at_evaluation_unit"))
                if age_evaluation_raw else ""
            ),
            "age_at_onset": parse_age(age_onset_raw),
            "age_at_onset_unit": (
                infer_age_unit(age_onset_raw, value("age_at_onset_unit"))
                if age_onset_raw else ""
            ),
            "reported_race": split_values(value("reported_race")),
            "reported_ethnicity": split_values(value("reported_ethnicity")),
            "phenotype_summary": value("phenotype_summary"),
            "present_features": split_values(value("present_features")),
            "absent_features": split_values(value("absent_features")),
            "current_diagnosis": value("current_diagnosis"),
            "notes": value("notes"),
            "source_date": value("source_date"),
            "custom_fields": custom,
        }

    def _sample_validation(self, records: list[dict]) -> dict:
        with self._session() as connection:
            known = [
                row["name"] for row in connection.execute(
                    "SELECT DISTINCT name FROM cohort_samples ORDER BY name"
                ).fetchall()
            ]
        exact = set(known)
        normalized: dict[str, list[str]] = defaultdict(list)
        for sample in known:
            normalized[sample.strip().casefold()].append(sample)
        matched: set[str] = set()
        unmatched: set[str] = set()
        ambiguous: dict[str, list[str]] = {}
        suggestions: dict[str, str] = {}
        for sample in {
            item for record in records for item in record["sample_ids"]
        }:
            if sample in exact:
                matched.add(sample)
                continue
            candidates = normalized.get(sample.strip().casefold(), [])
            if len(candidates) == 1:
                unmatched.add(sample)
                suggestions[sample] = candidates[0]
            elif len(candidates) > 1:
                ambiguous[sample] = candidates
            else:
                unmatched.add(sample)
        return {
            "matched_sample_ids": sorted(matched),
            "unmatched_sample_ids": sorted(unmatched),
            "ambiguous_sample_ids": ambiguous,
            "case_insensitive_suggestions": suggestions,
        }

    def validate(self, payload: dict) -> dict:
        _, parsed, records, _ = self._records(payload)
        counts = Counter(record["individual_id"] for record in records)
        duplicates = sorted(key for key, count in counts.items() if count > 1)
        sample_status = self._sample_validation(records)
        return {
            "row_count": parsed["row_count"],
            "valid_individuals": len(records),
            "duplicate_individual_ids": duplicates,
            **sample_status,
            "preview": records[:20],
        }

    def save_individual(self, payload: dict) -> dict:
        individual_id = clean(payload.get("individual_id"))
        if not individual_id:
            raise ValueError("individual_id is required")
        record = {
            "individual_id": individual_id,
            "sample_ids": (
                split_values(payload.get("sample_ids"))
                if isinstance(payload.get("sample_ids"), str)
                else [clean(value) for value in payload.get("sample_ids", []) if clean(value)]
            ),
            "sex_at_birth": normalize_sex(clean(payload.get("sex_at_birth"))),
            "age_at_evaluation": parse_age(clean(payload.get("age_at_evaluation"))),
            "age_at_evaluation_unit": clean(payload.get("age_at_evaluation_unit")) or "years",
            "age_at_onset": parse_age(clean(payload.get("age_at_onset"))),
            "age_at_onset_unit": clean(payload.get("age_at_onset_unit")) or "years",
            "reported_race": (
                split_values(payload.get("reported_race"))
                if isinstance(payload.get("reported_race"), str)
                else payload.get("reported_race", [])
            ),
            "reported_ethnicity": (
                split_values(payload.get("reported_ethnicity"))
                if isinstance(payload.get("reported_ethnicity"), str)
                else payload.get("reported_ethnicity", [])
            ),
            "phenotype_summary": clean(payload.get("phenotype_summary")),
            "present_features": (
                split_values(payload.get("present_features"))
                if isinstance(payload.get("present_features"), str)
                else payload.get("present_features", [])
            ),
            "absent_features": (
                split_values(payload.get("absent_features"))
                if isinstance(payload.get("absent_features"), str)
                else payload.get("absent_features", [])
            ),
            "current_diagnosis": clean(payload.get("current_diagnosis")),
            "notes": clean(payload.get("notes")),
            "source_date": clean(payload.get("source_date")),
            "custom_fields": payload.get("custom_fields", {}),
        }
        self._upsert(record, "replace", clean(payload.get("source_name")) or "manual")
        return self.get(individual_id)

    def _upsert(self, record: dict, mode: str, source_name: str) -> str:
        now = utc_now()
        existing = self.get(record["individual_id"])
        if existing and mode == "skip_existing":
            return "skipped"
        if existing and mode == "update_nonblank":
            merged = dict(existing)
            for key, value in record.items():
                if key == "individual_id":
                    continue
                if value not in ("", None, [], {}):
                    if key == "sample_ids":
                        merged[key] = list(dict.fromkeys(existing[key] + value))
                    elif key == "custom_fields":
                        merged[key] = {**existing[key], **value}
                    else:
                        merged[key] = value
            record = merged
        with self._session() as connection:
            connection.execute(
                """
                INSERT INTO phenotype_individuals(
                  individual_id, sex_at_birth,
                  age_at_evaluation, age_at_evaluation_unit,
                  age_at_onset, age_at_onset_unit,
                  reported_race_json, reported_ethnicity_json,
                  phenotype_summary, present_features_json, absent_features_json,
                  current_diagnosis, notes, source_date, custom_fields_json,
                  source_name, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(individual_id) DO UPDATE SET
                  sex_at_birth=excluded.sex_at_birth,
                  age_at_evaluation=excluded.age_at_evaluation,
                  age_at_evaluation_unit=excluded.age_at_evaluation_unit,
                  age_at_onset=excluded.age_at_onset,
                  age_at_onset_unit=excluded.age_at_onset_unit,
                  reported_race_json=excluded.reported_race_json,
                  reported_ethnicity_json=excluded.reported_ethnicity_json,
                  phenotype_summary=excluded.phenotype_summary,
                  present_features_json=excluded.present_features_json,
                  absent_features_json=excluded.absent_features_json,
                  current_diagnosis=excluded.current_diagnosis,
                  notes=excluded.notes,
                  source_date=excluded.source_date,
                  custom_fields_json=excluded.custom_fields_json,
                  source_name=excluded.source_name,
                  updated_at=excluded.updated_at
                """,
                (
                    record["individual_id"], record["sex_at_birth"],
                    record["age_at_evaluation"], record["age_at_evaluation_unit"],
                    record["age_at_onset"], record["age_at_onset_unit"],
                    json.dumps(record["reported_race"]),
                    json.dumps(record["reported_ethnicity"]),
                    record["phenotype_summary"],
                    json.dumps(record["present_features"]),
                    json.dumps(record["absent_features"]),
                    record["current_diagnosis"], record["notes"], record["source_date"],
                    json.dumps(record.get("custom_fields") or {}, sort_keys=True),
                    source_name, existing["created_at"] if existing else now, now,
                ),
            )
            connection.execute(
                "DELETE FROM phenotype_sample_links WHERE individual_id = ?",
                (record["individual_id"],),
            )
            connection.executemany(
                "INSERT INTO phenotype_sample_links(individual_id, sample_id) VALUES (?, ?)",
                [(record["individual_id"], sample) for sample in record["sample_ids"]],
            )
        return "updated" if existing else "created"

    def import_records(self, payload: dict) -> dict:
        content, parsed, records, mapping = self._records(payload)
        counts = Counter(record["individual_id"] for record in records)
        duplicates = sorted(key for key, count in counts.items() if count > 1)
        if duplicates:
            raise ValueError(
                "duplicate individual IDs must be resolved before import: "
                + ", ".join(duplicates[:10])
            )
        mode = clean(payload.get("update_mode")) or "update_nonblank"
        if mode not in {"update_nonblank", "replace", "skip_existing"}:
            raise ValueError("unsupported update_mode")
        filename = clean(payload.get("filename"))
        outcomes = Counter(
            self._upsert(record, mode, filename) for record in records
        )
        profile_name = clean(payload.get("profile_name"))
        with self._session() as connection:
            if profile_name:
                connection.execute(
                    """
                    INSERT INTO phenotype_import_profiles(
                      name, mapping_json, sheet_name, header_row, updated_at
                    ) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(name) DO UPDATE SET
                      mapping_json=excluded.mapping_json,
                      sheet_name=excluded.sheet_name,
                      header_row=excluded.header_row,
                      updated_at=excluded.updated_at
                    """,
                    (
                        profile_name, json.dumps(mapping, sort_keys=True),
                        parsed["selected_sheet"], parsed["header_row"], utc_now(),
                    ),
                )
            run_id = uuid.uuid4().hex
            connection.execute(
                """
                INSERT INTO phenotype_import_runs(
                  id, source_name, source_sha256, imported_at, profile_name,
                  mapping_json, row_count, created_count, updated_count, skipped_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id, filename, hashlib.sha256(content).hexdigest(), utc_now(),
                    profile_name or None, json.dumps(mapping, sort_keys=True),
                    len(records), outcomes["created"], outcomes["updated"],
                    outcomes["skipped"],
                ),
            )
        return {
            "import_id": run_id,
            "created": outcomes["created"],
            "updated": outcomes["updated"],
            "skipped": outcomes["skipped"],
            "row_count": len(records),
            "stats": self.stats(),
        }
