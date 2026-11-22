"""Read the public GenCC submissions export and group current assertions for review."""
import csv
import re
import sqlite3
from contextlib import closing
from pathlib import Path

EXPORT_URL = "https://thegencc.org/download/action/submissions-export-csv?format=new"
COLUMNS = (
    "sgc_id", "version_number", "gene_curie", "gene_symbol", "disease_curie",
    "disease_title", "disease_original_curie", "disease_original_title",
    "moi_curie", "moi_title", "classification_curie", "classification_title",
    "submitter_curie", "submitter_title", "submitted_as_date", "submitted_as_public_report_url",
    "submitted_as_disease_id", "submitted_as_disease_name", "submitted_as_pmids",
)
# GenCC Conflict Viewer criteria. Supportive and Animal Model Only do not vote.
HIGH = {"GENCC:100001", "GENCC:100002", "GENCC:100003"}
LOW = {"GENCC:100004", "GENCC:100005", "GENCC:100006", "GENCC:100008"}


class CachedSnapshotError(ValueError):
    """The previous database cannot supply its advertised GenCC submissions."""


def _cached_rows(source, previous_digest):
    try:
        with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as prior:
            metadata = prior.execute("SELECT source_sha256,record_count FROM resources WHERE id='gencc'").fetchone()
            if not metadata or metadata[0] != previous_digest:
                raise CachedSnapshotError("GenCC snapshot changed during refresh")
            count = 0
            for count, row in enumerate(prior.execute(f"SELECT {','.join(COLUMNS)} FROM gencc"), 1):
                yield row
            if not count or count != metadata[1]:
                raise CachedSnapshotError("GenCC cached submission count differs from its manifest or is empty")
    except sqlite3.DatabaseError as exc:
        raise CachedSnapshotError("Cannot read cached GenCC submissions") from exc


def populate(connection, source: Path, previous_digest: str = "") -> int:
    fields = ["sgc_id TEXT PRIMARY KEY", "version_number INTEGER NOT NULL"]
    fields.extend(f"{column} TEXT NOT NULL" for column in COLUMNS[2:])
    connection.execute(f"CREATE TABLE gencc({','.join(fields)})")
    columns = ",".join(COLUMNS)
    insert = f"INSERT INTO gencc({columns}) VALUES({','.join('?' for _ in COLUMNS)})"
    if previous_digest:
        with closing(_cached_rows(source, previous_digest)) as rows:
            try:
                connection.executemany(insert, rows)
            except sqlite3.IntegrityError as exc:
                raise CachedSnapshotError("Incompatible cached GenCC submissions") from exc
    else:
        updates = ",".join(f"{column}=excluded.{column}" for column in COLUMNS[1:])
        sql = insert + f" ON CONFLICT(sgc_id) DO UPDATE SET {updates} WHERE excluded.version_number>gencc.version_number"
        seen = set()
        with source.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream, strict=True)
            missing = set(COLUMNS) - set(reader.fieldnames or [])
            if missing:
                raise ValueError("Missing GenCC export columns: " + ", ".join(sorted(missing)))
            for row in reader:
                if None in row or any(row[k] is None for k in COLUMNS):
                    raise ValueError(f"Incomplete GenCC record at line {reader.line_num}")
                record = {k: row[k].strip() for k in COLUMNS}
                version = int(record["version_number"])
                if (version < 1 or not re.fullmatch(r"SGC-\d+", record["sgc_id"])
                        or not re.fullmatch(r"HGNC:\d+", record["gene_curie"])
                        or not all(record[k] for k in ("disease_curie","disease_title","moi_curie","moi_title",
                                                       "classification_curie","classification_title","submitter_curie","submitter_title"))):
                    raise ValueError(f"Invalid GenCC assertion at line {reader.line_num}")
                key = (record["sgc_id"], version)
                if key in seen:
                    raise ValueError(f"Duplicate GenCC submission version {key}")
                seen.add(key)
                connection.execute(sql, tuple(record.values()))
    count = connection.execute("SELECT count(*) FROM gencc").fetchone()[0]
    if not count:
        raise ValueError("GenCC export contains no submissions")
    connection.execute("CREATE INDEX gencc_gene_lookup ON gencc(gene_curie,disease_curie,moi_curie)")
    return count


def gene(connection, hgnc_id):
    cursor = connection.cursor()
    cursor.row_factory = sqlite3.Row
    resource = cursor.execute("SELECT release FROM resources WHERE id='gencc'").fetchone()
    if resource is None:
        return {"available": False, "groups": []}
    rows = cursor.execute(
        "SELECT * FROM gencc WHERE gene_curie=? ORDER BY disease_title,moi_title,submitter_title,sgc_id", (hgnc_id,)
    )
    groups = {}
    for item in rows:
        row = dict(item)
        key = (row["disease_curie"], row["moi_curie"])
        group = groups.setdefault(key, {
            "disease": row["disease_title"], "disease_id": key[0], "inheritance": row["moi_title"],
            "inheritance_id": key[1], "submissions": [],
        })
        group["submissions"].append(row)
    for group in groups.values():
        submissions = group["submissions"]
        high = {row["submitter_curie"] for row in submissions if row["classification_curie"] in HIGH}
        low = {row["submitter_curie"] for row in submissions if row["classification_curie"] in LOW}
        group["conflict"] = bool(high and low and len(high | low) > 1)
        group["classifications"] = list(dict.fromkeys(row["classification_title"] for row in
            sorted(submissions, key=lambda row: row["classification_curie"])))
    return {"available": True, "release": resource[0], "groups": list(groups.values())}
