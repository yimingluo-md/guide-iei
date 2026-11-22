#!/usr/bin/env python3
"""Import pinned ENCODE predictions. Commit each validated source before continuing."""
import csv
import fcntl
import gzip
import hashlib
import math
from pathlib import Path
import subprocess
from concurrent.futures import ThreadPoolExecutor
import sqlite3
import sys
import tempfile
from contextlib import closing

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from local_service.element_gene_links import DATABASE, PROVIDER, RELEASE, SCHEMA, installed
from pipeline.genia_alleles import normalize_chrom

CATALOG = ROOT / "config/encode-re2g-grch38.tsv"
TABLES = """
    CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT);
    CREATE TABLE IF NOT EXISTS sources(id INTEGER PRIMARY KEY, accession TEXT UNIQUE,
        annotation TEXT, context TEXT, model TEXT, ontology TEXT, systems TEXT);
    CREATE TABLE IF NOT EXISTS links(chrom TEXT,start INTEGER,end INTEGER,element TEXT,
        gene TEXT,symbol TEXT,tss INTEGER,distance REAL,score REAL,source INTEGER, context_label TEXT);
"""


def records(path, source):
    with gzip.open(path, "rt") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        required = {"#chr", "start", "end", "name", "TargetGeneEnsemblID", "TargetGene",
                    "TargetGeneTSS", "distanceToTSS.Feature", "Score", "CellType"}
        missing = required.difference(reader.fieldnames or ())
        if missing:
            raise ValueError(f"{path.name}: missing ENCODE columns: {', '.join(sorted(missing))}")
        for r in reader:
            try:
                if any(r[k] is None for k in required):
                    raise ValueError("incomplete row")
                start, end = int(r["start"]), int(r["end"])
                score = float(r["Score"])
                if not 0 <= start < end or not math.isfinite(score) or not 0 <= score <= 1 or not r["TargetGene"]:
                    raise ValueError("invalid interval, target gene or score")
                yield (normalize_chrom(r["#chr"]), start, end, r["name"], r["TargetGeneEnsemblID"],
                       r["TargetGene"], int(r["TargetGeneTSS"]), float(r["distanceToTSS.Feature"]), score, source, r["CellType"])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{path.name}, line {reader.line_num}: {exc}") from exc


def import_file(db, entry, path, source):
    with db:
        db.execute("INSERT INTO sources VALUES(?,?,?,?,?,?,?)", (source, entry["accession"], entry["annotation"],
                                                              entry["context"], entry["model"], entry["ontology_id"], entry["systems"]))
        db.executemany("INSERT INTO links VALUES(?,?,?,?,?,?,?,?,?,?,?)", records(path, source))


def verified_sources(entries, directory):
    """Prefetch four files while the previous file is being indexed."""
    def download(item):
        _, entry = item
        path = directory / entry["accession"]
        subprocess.run(["curl", "--fail", "--silent", "--show-error", "--location",
                        "--retry", "3", "--connect-timeout", "30", "--speed-limit", "1024",
                        "--speed-time", "90", "--output", str(path), entry["url"]], check=True)
        if path.stat().st_size != int(entry["bytes"]) or hashlib.md5(path.read_bytes()).hexdigest() != entry["md5"]:
            raise ValueError(f"Checksum mismatch for {entry['accession']}. Retry installation.")
        return path
    with ThreadPoolExecutor(max_workers=4) as pool:
        pending = []
        for item in entries:
            pending.append((item, pool.submit(download, item)))
            if len(pending) >= 4:
                (index, entry), future = pending.pop(0)
                yield index, entry, future.result()
        for (index, entry), future in pending:
            yield index, entry, future.result()


def resumable(stage, entries):
    """Only classify confirmed corruption or an incompatible schema/catalog as invalid."""
    try:
        with closing(sqlite3.connect(stage)) as db, closing(sqlite3.connect(":memory:")) as expected:
            if db.execute("PRAGMA quick_check(1)").fetchone() != ("ok",):
                return False
            expected.executescript(TABLES)
            for table in ("metadata", "sources", "links"):
                query = f"PRAGMA table_info({table})"
                if db.execute(query).fetchall() != expected.execute(query).fetchall():
                    return False
            catalog_sources = {(i, entry["accession"]) for i, entry in enumerate(entries, 1)}
            return catalog_sources.issuperset(db.execute("SELECT id,accession FROM sources"))
    except sqlite3.DatabaseError as exc:
        if hasattr(exc, "sqlite_errorcode"):
            if (exc.sqlite_errorcode & 0xff) in (sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB):
                return False
        # Python 3.9 and 3.10 expose only the SQLite error message.
        elif (str(exc) in ("database disk image is malformed", "file is not a database")
              or str(exc).startswith("malformed database schema")):
            return False
        raise


def install(root, catalog=CATALOG):
    final = root / DATABASE
    final.parent.mkdir(parents=True, exist_ok=True)
    with (final.parent / "install.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if installed(final):
            return
        digest = hashlib.sha256(catalog.read_bytes()).hexdigest()
        stage = final.with_name(digest + ".partial.sqlite3")
        with catalog.open() as stream:
            entries = list(csv.DictReader(stream, delimiter="\t"))
        if stage.exists() and not resumable(stage, entries):
            print("Restarting ENCODE-rE2G installation from an invalid partial index", flush=True)
            for suffix in ("-journal", "-wal", "-shm", ""):
                stage.with_name(stage.name + suffix).unlink(missing_ok=True)
        with closing(sqlite3.connect(stage)) as db:
            db.executescript(TABLES)
            done = {r[0] for r in db.execute("SELECT accession FROM sources")}
            remaining = [(i, entry) for i, entry in enumerate(entries, 1) if entry["accession"] not in done]
            with tempfile.TemporaryDirectory(dir=stage.parent) as temp:
                for i, entry, path in verified_sources(remaining, Path(temp)):
                    print(f"{95*(i-1)/len(entries):.1f}%  Preparing ENCODE-rE2G {i}/{len(entries)} {entry['accession']}", flush=True)
                    import_file(db, entry, path, i)
                    path.unlink()
            print("96%  Indexing GRCh38 regulatory intervals", flush=True)
            db.execute("CREATE INDEX IF NOT EXISTS interval_lookup ON links(chrom,start)")
            db.execute("CREATE INDEX IF NOT EXISTS target_lookup ON links(symbol COLLATE NOCASE,chrom,start,end)")
            width = db.execute("SELECT max(end-start) FROM links").fetchone()[0]
            if not width:
                raise ValueError("No prediction rows in ENCODE release")
            with db:
                meta = dict(schema=str(SCHEMA), assembly="GRCh38", provider=PROVIDER, release=RELEASE,
                            complete="1", source_catalog_sha256=digest, max_width=str(width))
                db.executemany("INSERT OR REPLACE INTO metadata VALUES(?,?)", meta.items())
        stage.replace(final)


if __name__ == "__main__":
    install(Path(sys.argv[1]))
