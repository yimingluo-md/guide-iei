"""Local interval lookup for release-specific element–gene link databases."""
from contextlib import closing
from pathlib import Path
import re
import sqlite3

from pipeline.genia_alleles import normalize_chrom

SCHEMA = 1
PROVIDER = "encode_re2g"
RELEASE = "GRCh38-DHS-v1-20261120"
DATABASE = f"{PROVIDER}/{RELEASE}/links.sqlite3"


# Each registered source supplies its own release, score semantics and index.
# Query parameters can select a provider ID, never a filesystem path.
PROVIDERS = {
    PROVIDER: dict(label="ENCODE-rE2G", release=RELEASE, database=DATABASE,
                   score_label="ENCODE-rE2G score", source_base="https://www.encodeproject.org/files/"),
}


def provider_index(root, payload):
    provider = payload.get("provider", PROVIDER)
    if not isinstance(provider, str) or provider not in PROVIDERS:
        raise ValueError("Unknown element–gene link provider")
    return root / PROVIDERS[provider]["database"]


def open_index(path):
    return sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)


def metadata(connection):
    meta = dict(connection.execute("SELECT key,value FROM metadata"))
    if meta.get("schema") != str(SCHEMA) or meta.get("assembly") != "GRCh38" or meta.get("complete") != "1" or int(meta.get("max_width", 0)) < 1:
        raise ValueError("Incompatible or incomplete element–gene link index")
    return meta


def installed(path):
    if not path.is_file():
        return False
    try:
        with closing(open_index(path)) as db:
            meta = metadata(db)
            return meta["provider"] == PROVIDER and meta["release"] == RELEASE
    except (sqlite3.Error, ValueError, KeyError):
        return False


def interval(variant):
    """Convert a sequence-resolved VCF allele to affected BED coordinates."""
    if not isinstance(variant, dict):
        raise ValueError("Expected a VCF allele")
    chrom = normalize_chrom(variant.get("chrom"))
    pos = variant.get("pos")
    ref, alt = variant.get("ref"), variant.get("alt")
    if type(pos) is not int or pos < 1 or pos > 2**31-1:
        raise ValueError("Invalid VCF position")
    if not all(isinstance(a, str) and re.fullmatch("[ACGTNacgtn]+", a) for a in (ref, alt)):
        return None
    ref, alt = ref.upper(), alt.upper()
    if ref == alt:
        return None
    left, right = pos - 1, pos - 1 + len(ref)
    prefix = 0
    while prefix < min(len(ref), len(alt)) and ref[prefix] == alt[prefix]:
        prefix += 1
    left += prefix
    suffix = 0
    while suffix < min(len(ref), len(alt)) - prefix and ref[-1-suffix] == alt[-1-suffix]:
        suffix += 1
    right -= suffix
    return chrom, left, right


def matches(db, meta, variant, *, genes=None):
    span = interval(variant)
    return _overlap_rows(db, meta, span, genes, stored_contigs(db)) if span else None


def stored_contigs(db):
    """Find legacy spellings with one index seek per distinct contig."""
    aliases = {}
    previous = ""
    while True:
        row = db.execute("SELECT chrom FROM links INDEXED BY interval_lookup "
                         "WHERE chrom>? ORDER BY chrom LIMIT 1", (previous,)).fetchone()
        if row is None:
            return aliases
        previous = row[0]
        aliases.setdefault(normalize_chrom(previous), []).append(previous)


def _overlap_rows(db, meta, span, genes, contigs):
    chrom, left, right = span
    names = contigs.get(chrom)
    if not names:
        return ()
    # For an insertion, both flanks must lie within the element.
    window = "l.chrom IN (" + ",".join("?" for _ in names) + ") AND l.start BETWEEN ? AND ? AND l.end>?"
    params = [*names, left-int(meta["max_width"])+1, right-1, left]
    if genes is None:
        sql = """SELECT l.start,l.end,l.element,l.gene,l.symbol,l.tss,l.distance,l.score,
                        s.context,s.accession,s.annotation,s.model,s.ontology,s.systems,l.context_label
                 FROM links l INDEXED BY interval_lookup JOIN sources s ON s.id=l.source WHERE """
        sql += window + " ORDER BY l.gene,s.context,l.score DESC,s.accession,l.start,l.end"
    elif len(genes) <= 32:
        # Probe the target-first index for short lists. Both indexes are present in existing installs.
        sql = "SELECT DISTINCT l.symbol FROM links l INDEXED BY target_lookup WHERE " + window
        sql += " AND l.symbol COLLATE NOCASE IN (" + ",".join("?" for _ in genes) + ")"
        params.extend(genes)
    else:
        # Larger lists use one interval probe and membership checks in the caller.
        sql = "SELECT DISTINCT l.symbol FROM links l INDEXED BY interval_lookup WHERE " + window
    return db.execute(sql, params)


def lookup(path, variant):
    if not path.is_file():
        return {"status": "not_installed", "links": []}
    with closing(open_index(path)) as db:
        db.row_factory = sqlite3.Row
        meta = metadata(db)
        rows = matches(db, meta, variant)
        links = [dict(row) for row in rows] if rows is not None else []
        return {"status": "unsupported" if rows is None else "matched" if links else "no_published_link",
                "provider": meta["provider"], "release": meta["release"], "links": links,
                "label": PROVIDERS[meta["provider"]]["label"], "score_label": PROVIDERS[meta["provider"]]["score_label"],
                "source_base": PROVIDERS[meta["provider"]]["source_base"]}


def gene_windows(db, symbols, width):
    """Conservative bounds from two covering-index seeks per gene and chromosome."""
    windows = {}
    for symbol in symbols:
        chrom = ""
        while True:
            first = db.execute("""
                SELECT chrom,start FROM links INDEXED BY target_lookup
                WHERE symbol=? COLLATE NOCASE AND chrom>? ORDER BY chrom,start LIMIT 1
                """, (symbol, chrom)).fetchone()
            if not first:
                break
            chrom, start = first
            last = db.execute("""
                SELECT start FROM links INDEXED BY target_lookup
                WHERE symbol=? COLLATE NOCASE AND chrom=? ORDER BY start DESC LIMIT 1
                """, (symbol, chrom)).fetchone()[0]
            windows.setdefault(normalize_chrom(chrom), []).append((start, last + width, symbol))
    return windows


def target_search_status(path, gene):
    """Check the active release and an exact symbol without receiving any alleles."""
    if not isinstance(gene, str):
        raise ValueError("Expected a target gene symbol as text")
    if not path.is_file():
        raise ValueError("Install ENCODE-rE2G in Annotation datasets first")
    with closing(open_index(path)) as db:
        meta = metadata(db)
        known = bool(gene and db.execute(
            "SELECT 1 FROM links WHERE symbol=? COLLATE NOCASE LIMIT 1", (gene,)
        ).fetchone())
        return {"index": [meta["provider"], meta["release"], meta["source_catalog_sha256"]],
                "known": known}


def filter_targets(path, payload):
    variants, genes = payload.get("variants"), payload.get("genes")
    if not isinstance(variants, list) or len(variants) > 500 or not isinstance(genes, list) or (variants and not genes) or len(genes) > 50000:
        raise ValueError("Expected up to 500 variants and a nonempty gene list")
    if not path.is_file():
        raise ValueError("Install ENCODE-rE2G in Annotation datasets first")
    with closing(open_index(path)) as db:
        meta = metadata(db)
        if not all(isinstance(gene, str) for gene in genes):
            raise ValueError("Expected gene symbols as text")
        accepted = {gene.upper() for gene in genes}
        contigs = stored_contigs(db) if variants else {}
        # For a batch, amortize indexed bounds across its variants. Very large
        # lists or tiny batches are cheaper to test from the interval side.
        windows = None
        if 8 < len(accepted) <= min(4096, 8 * len(variants)):
            windows = gene_windows(db, accepted, int(meta["max_width"]))
        hits = {}
        for variant in variants:
            if not isinstance(variant, dict) or not isinstance(variant.get("key"), str):
                raise ValueError("Expected a variant key")
            span = interval(variant)
            if span is None:
                continue
            candidates = accepted
            if windows is not None:
                chrom, left, right = span
                candidates = [symbol for start, end, symbol in windows.get(chrom, ())
                              if start < right and end > left]
            if candidates:
                rows = _overlap_rows(db, meta, span, candidates, contigs)
                symbols = sorted({row[0] for row in rows if row[0].upper() in accepted})
                if symbols:
                    hits[variant["key"]] = symbols
        return {"matches": list(hits), "targets": hits}


def target_regions(path, gene):
    """Load published intervals for one target, collapsing duplicate biosamples."""
    if not path.is_file():
        raise ValueError("Install ENCODE-rE2G before searching predicted target genes")
    if not gene:
        raise ValueError("Enter a predicted target gene symbol")
    regions = {}
    with closing(open_index(path)) as db:
        metadata(db)
        for chrom, start, end in db.execute(
            "SELECT DISTINCT chrom,start,end FROM links WHERE symbol=? COLLATE NOCASE ORDER BY start,end", (gene,)
        ):
            starts, ends = regions.setdefault(normalize_chrom(chrom), ([], []))
            if ends and start < ends[-1]:
                ends[-1] = max(ends[-1], end)
            else:
                starts.append(start)
                ends.append(end)
    return regions


def region_matcher(regions):
    from bisect import bisect_left
    def overlaps(chrom, pos, ref, alt):
        span = interval(dict(chrom=chrom, pos=pos, ref=ref, alt=alt))
        if span is None or span[0] not in regions:
            return False
        chrom, left, right = span
        starts, ends = regions[chrom]
        index = bisect_left(starts, right) - 1
        return index >= 0 and ends[index] > left
    return overlaps
