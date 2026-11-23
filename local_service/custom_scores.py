"""Project user-selected GRCh38 TSV columns into a local allele lookup index."""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import sqlite3
import time
import zlib
from contextlib import closing, contextmanager
from pathlib import Path

from pipeline.genia_alleles import normalize_allele, normalize_chrom

ROLES = {
    "chrom": ("chrom", "chromosome", "chr"), "pos": ("pos", "position"),
    "ref": ("ref", "reference", "reference_allele"), "alt": ("alt", "alternate", "alternate_allele"),
    "strand": ("strand",), "gene": ("gene_id", "ensembl_gene", "gene", "symbol"),
    "transcript": ("transcript_id", "ensembl_transcript", "transcript", "feature"),
}
REQUIRED = ("chrom", "pos", "ref", "alt")
CONTEXT = ("strand", "gene", "transcript")
MISSING = {"", ".", "NA", "N/A"}
STRANDS = {"+": "+", "1": "+", "+1": "+", "-": "-", "-1": "-"}
BIN_SIZE = 1024


def score_value(value):
    value = value.strip()
    return None if value.upper() in MISSING else value


def source_paths(raw):
    if not isinstance(raw, list) or not raw or not all(isinstance(p, str) for p in raw):
        raise ValueError("Choose one or more local tab-separated files.")
    paths = [Path(p).expanduser().resolve() for p in raw]
    if len(set(paths)) != len(paths):
        raise ValueError("A file was selected twice.")
    for path in paths:
        if not path.is_file():
            raise ValueError(f"Source file unavailable: {path.name}")
    return paths


@contextmanager
def open_table(path):
    opener = gzip.open if path.suffix.lower() in {".gz", ".bgz", ".bgzf"} else open
    try:
        with opener(path, "rt", encoding="utf-8-sig", newline="") as stream:
            yield stream
    except (UnicodeError, gzip.BadGzipFile, EOFError, zlib.error) as exc:
        raise ValueError(f"{path.name} is not a readable UTF-8 TSV or its compressed stream is damaged.") from exc


def header(stream):
    for line in stream:
        if line.startswith("##") or not line.strip():
            continue
        names = [field.strip() for field in line.rstrip("\r\n").split("\t")]
        if len(names) < 5 or not all(names) or len(set(names)) != len(names):
            raise ValueError("Expected a tab-separated header with unique, nonempty column names.")
        return names
    raise ValueError("No header found.")


def fields(line, names):
    values = line.rstrip("\r\n").split("\t")
    if len(values) != len(names):
        raise ValueError(f"Expected {len(names)} columns, found {len(values)}.")
    return values


def inspect(paths):
    names = []
    examples = []
    for path in paths:
        with open_table(path) as stream:
            current = header(stream)
            if names and set(current) != set(names):
                raise ValueError(f"{path.name} has a different set of columns. Import it separately.")
            if not names:
                names = current
            if not examples:
                order = [current.index(col) for col in names]
                for line in stream:
                    if line.strip():
                        values = fields(line, current)
                        examples.append([values[i] for i in order])
                    if len(examples) == 3:
                        break
    if not examples:
        raise ValueError("No data rows in the selected files.")
    lower = {name.lstrip("#").lower(): name for name in names}
    mapping = {role: next((lower[k] for k in keys if k in lower), "") for role, keys in ROLES.items()}
    return {"columns": names, "mapping": mapping, "examples": examples}


def validate(spec, columns):
    name, mapping, scores = (spec.get(k) for k in ("name", "mapping", "scores"))
    if not isinstance(name, str) or not name.strip() or len(name) > 120:
        raise ValueError("Enter a dataset name of up to 120 characters.")
    if not isinstance(mapping, dict) or set(mapping) - ROLES.keys() or any(not mapping.get(k) for k in REQUIRED):
        raise ValueError("Map chromosome, position, REF and ALT.")
    if type(spec.get("dash_is_missing_strand", False)) is not bool or (
        spec.get("dash_is_missing_strand") and not mapping.get("strand")
    ):
        raise ValueError("The missing-strand option requires a mapped strand column.")
    if not isinstance(scores, list) or not scores:
        raise ValueError("Select at least one score column.")
    labels = []
    used = [value for value in mapping.values() if value]
    for score in scores:
        if not isinstance(score, dict) or not isinstance(score.get("label"), str):
            raise ValueError("Give each score a display name.")
        label = score["label"].strip()
        if not label or len(label) > 120 or label.casefold() in labels:
            raise ValueError("Choose unique score names of 1 to 120 characters.")
        labels.append(label.casefold())
        used.append(score.get("column"))
    if any(col not in columns for col in used) or len(set(used)) != len(used):
        raise ValueError("Use a different source column for each matching field and score.")


def strand(value):
    if value in MISSING:
        return ""
    if value not in STRANDS:
        raise ValueError(f"Unrecognized strand {value!r}.")
    return STRANDS[value]


@contextmanager
def _index_lock(root):
    # Different workbench profiles can share one annotation drive. SQLite
    # releases the reservation on process exit, so a retry can reclaim staging.
    root.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(root / "import.lock", timeout=0)) as guard:
        try:
            guard.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as exc:
            if "locked" in str(exc):
                raise ValueError("Another custom score import is in progress.") from exc
            raise
        yield


def remove(root, dataset_id):
    if (not isinstance(dataset_id, str) or not dataset_id or dataset_id.startswith(".")
            or Path(dataset_id).name != dataset_id or any(c in dataset_id for c in ("\\", ":", "\x00"))):
        raise ValueError("Invalid custom score dataset ID.")
    with _index_lock(root):
        (root / (dataset_id + ".sqlite3")).unlink(missing_ok=True)
    return {"id": dataset_id}


def install(root, paths, spec, report):
    with _index_lock(root):
        for partial in root.glob("*.partial"):
            if re.fullmatch(r"[a-f0-9]{64}\.partial", partial.name):
                partial.unlink()
        return _install_locked(root, paths, spec, report)


def _install_locked(root, paths, spec, report):
    preview = inspect(paths)
    validate(spec, preview["columns"])
    name = spec["name"].strip()
    identifier = hashlib.sha256(name.casefold().encode()).hexdigest()
    meta = {"id": identifier, "name": name, "mapping": spec["mapping"], "scores": spec["scores"],
            "files": [p.name for p in paths], "assembly": "GRCh38",
            "dash_is_missing_strand": spec.get("dash_is_missing_strand", False)}
    target = root / (identifier + ".sqlite3")
    if target.exists() and spec.get("replace") is not True:
        raise ValueError("A dataset with this name is installed. Choose a new name or confirm replacement.")
    staging = root / (identifier + ".partial")
    count = 0
    skipped = 0
    last_report = 0.0
    try:
        with closing(sqlite3.connect(staging)) as db:
            db.execute("PRAGMA journal_mode=OFF")  # Unpublished, rebuildable staging database.
            db.execute("CREATE TABLE blocks(chrom TEXT, bin INTEGER, payload BLOB)")
            db.execute("CREATE TABLE metadata(doc TEXT)")
            db.execute("PRAGMA temp_store=FILE")
            db.execute("CREATE TEMP TABLE spool(chrom TEXT, bin INTEGER, seq INTEGER, line TEXT, "
                       "PRIMARY KEY(chrom, bin, seq)) WITHOUT ROWID")
            batch = []
            for path in paths:
                with open_table(path) as stream:
                    names = header(stream)
                    validate(spec, names)
                    ix = {role: names.index(col) for role, col in spec["mapping"].items() if col}
                    score_ix = [names.index(score["column"]) for score in spec["scores"]]
                    for number, line in enumerate(stream, 1):
                        if not line.strip():
                            continue
                        try:
                            row = fields(line, names)
                            chrom = normalize_chrom(row[ix["chrom"]])
                            ref, alt = row[ix["ref"]].strip().upper(), row[ix["alt"]].strip().upper()
                            try:
                                pos = int(row[ix["pos"]])
                            except ValueError as exc:
                                raise ValueError("position must be an integer") from exc
                            if pos < 1:
                                raise ValueError("position must be positive")
                            if ref not in {"-", "*"} and not re.fullmatch(r"[ACGTN]+", ref):
                                raise ValueError("REF must contain only A, C, G, T, or N")
                            context = [row[ix[k]].strip() if k in ix else "" for k in CONTEXT]
                            context[0] = "" if meta["dash_is_missing_strand"] and context[0] == "-" else strand(context[0])
                            if (ref in {"-", "*"} or alt in {"-", "*", "."}
                                    or "," in alt or re.fullmatch(r"<[^<>]+>", alt)
                                    or "[" in alt or "]" in alt
                                    or (ref == alt and re.fullmatch(r"[ACGTN]+", ref))):
                                skipped += 1
                                continue
                            pos, ref, alt = normalize_allele(pos, ref, alt)
                        except ValueError as exc:
                            raise ValueError(f"{path.name}, data row {number:,}: {exc}") from exc
                        line = "\t".join([str(pos), ref, alt, *context, *(score_value(row[i]) or "" for i in score_ix)])
                        batch.append((chrom, pos // BIN_SIZE, count, line))
                        count += 1
                        if len(batch) == 2048:
                            db.executemany("INSERT INTO spool VALUES(?,?,?,?)", batch)
                            batch.clear()
                        if count % 8192 == 0 and time.monotonic() - last_report > 3:
                            report(f"Importing {path.name} · {count:,} rows read", None)
                            last_report = time.monotonic()
            if not count:
                raise ValueError(f"No indexable variants. Skipped {skipped:,} wild-type or unsupported allele rows (including multi-ALT rows).")
            db.executemany("INSERT INTO spool VALUES(?,?,?,?)", batch)
            meta.update(rows=count, skipped_rows=skipped)
            report(f"Indexing {count:,} rows", None)
            block = []
            previous = None
            for chrom, bin_id, line in db.execute("SELECT chrom,bin,line FROM spool ORDER BY chrom,bin,seq"):
                key = (chrom, bin_id)
                if block and (key != previous or len(block) == 2048):
                    write_block(db, previous, block)
                    block.clear()
                block.append(line)
                previous = key
            if block:
                write_block(db, previous, block)
            db.execute("CREATE INDEX locus ON blocks(chrom, bin)")
            db.execute("INSERT INTO metadata VALUES (?)", (json.dumps(meta),))
            db.commit()
        os.replace(staging, target)
    finally:
        staging.unlink(missing_ok=True)
    return meta


def write_block(db, key, rows):
    blob = zlib.compress("\n".join(rows).encode(), level=1)
    db.execute("INSERT INTO blocks VALUES (?, ?, ?)", (*key, blob))


def datasets(root, variant=None):
    result = []
    for path in sorted(root.glob("*.sqlite3")):
        name = path.name
        if name.startswith("."):
            continue
        try:
            with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
                row = db.execute("SELECT doc FROM metadata").fetchone()
                meta = json.loads(row[0]) if row else None
                if not (isinstance(meta, dict) and isinstance(meta.get("name"), str)
                        and meta.get("id") == path.stem and isinstance(meta.get("rows"), int)
                        and isinstance(meta.get("mapping"), dict) and isinstance(meta.get("scores"), list)
                        and all(isinstance(s, dict) and isinstance(s.get("label"), str)
                                and isinstance(s.get("column"), str) for s in meta["scores"])):
                    raise ValueError("Invalid custom score metadata")
                name = meta["name"]
                if variant is not None:
                    meta["matches"] = matches(db, meta["mapping"], variant)
        except (sqlite3.Error, OSError, ValueError, zlib.error, TypeError):
            meta = {"id": path.stem, "name": name, "scores": [], "rows": 0,
                    "error": f"Cannot read {name}. Check file access or re-import the original table."}
            if variant is not None:
                meta["matches"] = []
        result.append(meta)
    return result


def identifier(value, role):
    patterns = (("ensembl_gene", r"ENSG\d+"),) if role == "gene" else (
        ("ensembl_transcript", r"ENST\d+"), ("refseq", r"(?:NM|NR|XM|XR)_\d+"))
    for namespace, pattern in patterns:
        match = re.fullmatch(f"({pattern})(?:\\.([0-9]+))?", value, re.IGNORECASE)
        if match:
            return namespace, match[1].upper(), match[2]
    if role == "gene":
        if value.isdecimal():
            return "numeric_gene", value, None
        return "symbol", value.casefold(), None
    return role, value, None


def context_match(role, value, candidates):
    """True for verified context, False for a contradiction, None for insufficient evidence."""
    candidates = [c for c in candidates if c and c not in MISSING and c != "—"]
    if value in MISSING or value == "—" or not candidates:
        return None
    namespace, base, version = identifier(value, role)
    identifiers = [identifier(c, role) for c in candidates]
    comparable = [item for item in identifiers if item[0] == namespace]
    if not comparable:
        return None
    versions = {item[2] for item in comparable if item[1] == base}
    if not versions:
        return False
    if version is None or version in versions:
        return True
    return None if versions == {None} else False


def matches(db, mapping, variant):
    try:
        chrom = normalize_chrom(variant.get("chrom"))
        pos, ref, alt = normalize_allele(variant.get("pos"), str(variant.get("ref", "")), str(variant.get("alt", "")))
    except ValueError:
        return []
    allele = [str(pos), ref, alt]
    expected = {"strand": {variant.get("strand")}, "gene": {variant.get("gene"), variant.get("geneId")},
                "transcript": {variant.get("transcript"), variant.get("versionedTranscript")}}
    result = []
    for (blob,) in db.execute("SELECT payload FROM blocks WHERE chrom=? AND bin=?", (chrom, pos // BIN_SIZE)):
        for line in zlib.decompress(blob).decode().split("\n"):
            row = line.split("\t")
            if row[:3] != allele:
                continue
            context = {}
            unresolved = []
            for role, value in zip(CONTEXT, row[3:6]):
                if not mapping.get(role):
                    continue
                context[role] = value
                match = context_match(role, value, expected[role])
                if match is None:
                    unresolved.append(role)
                elif not match:
                    break
            else:
                result.append({"context": context, "unresolved": unresolved,
                               "values": [score_value(v) for v in row[6:]]})
    return result
