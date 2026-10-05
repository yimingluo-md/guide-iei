#!/usr/bin/env python3
"""Release-time preparation of small GRCh38 starter annotation resources.

This module does not enable plugins, modify a user's configuration, or download
anything. Sources are explicit local files. pysam is a build-time dependency,
not a new dependency of the desktop service. Completed outputs are published as
one directory; an unsuccessful preparation never replaces a previous bundle.
"""
from __future__ import annotations

import argparse
import bisect
import csv
import gzip
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterable, TextIO

from .indexed_scores import MANIFEST_SCHEMA, validate_manifest

SCHEMA = "guide-iei.starter-resource/v1"
LICENSE_NOTICES = Path(__file__).resolve().parents[1] / "config/starter-licenses.json"
PREPARER_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
PRIMARY = tuple(str(n) for n in range(1, 23)) + ("X", "Y")
AM_COLUMNS = ["chrom", "position", "reference", "alternate", "transcript_id",
              "protein_change", "score", "prediction", "mane_transcript_id", "source_table"]
CADD_COLUMNS = ["chrom", "position", "reference", "alternate", "phred"]
AM_LABELS = {"likely_benign", "ambiguous", "likely_pathogenic"}
ATTRIBUTE = re.compile(r'(\w+) "([^"]*)"')


def open_text(path: Path) -> TextIO:
    return gzip.open(path, "rt", encoding="utf-8") if path.suffix == ".gz" else path.open(encoding="utf-8")


def file_identity(path: Path) -> dict:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    # Never leak the build machine's directories into public metadata.
    return {"name": path.name, "size_bytes": path.stat().st_size, "sha256": digest.hexdigest()}


def write_license_notices(kind: str, destination: Path) -> dict:
    """Notices travel with each resource; no fabricated click-through consent.

    The same curated source supplies the offline UI. Preparation fails rather
    than producing a resource without its applicable license and attribution.
    """
    catalog = json.loads(LICENSE_NOTICES.read_text(encoding="utf-8"))
    if catalog.get("schema") != "guide-iei.starter-licenses/v1":
        raise ValueError("unrecognized starter license catalog")
    resource = next((r for r in catalog["resources"] if r["id"] == "starter_" + kind), None)
    if resource is None:
        raise ValueError("starter resource license notice is missing")
    terms = catalog["license_texts"].get(resource["license_id"])
    if not terms or not terms.get("text", "").strip() or not terms.get("url"):
        raise ValueError("starter resource full license text is missing")
    if not all(resource.get(field) for field in ("attribution", "changes", "source_url", "source_notice")):
        raise ValueError("starter resource attribution or modification notice is missing")
    notices = {"schema": catalog["schema"], "reviewed_on": catalog["reviewed_on"],
               "scope": catalog["scope"], "resource": resource, "license": terms}
    destination.write_text(json.dumps(notices, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return file_identity(destination)


def contig(value: str) -> str:
    value = value.removeprefix("chr")
    return "MT" if value == "M" else value


def stable_id(value: str) -> str:
    return value.split(".", 1)[0]


def mane_select(path: Path) -> dict[str, dict]:
    result = {}
    with open_text(path) as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        required = {"Ensembl_nuc", "MANE_status", "symbol", "GRCh38_chr"}
        if not required <= set(reader.fieldnames or []):
            raise ValueError("MANE summary lacks required columns")
        for row in reader:
            if row["MANE_status"] != "MANE Select":
                continue
            accession = re.fullmatch(r"NC_0*(\d+)\.\d+", row["GRCh38_chr"])
            if not accession or not 1 <= int(accession[1]) <= 24:
                continue  # no patches, alternate contigs, or mitochondrion
            tx = row["Ensembl_nuc"]
            if not re.fullmatch(r"ENST\d+\.\d+", tx):
                raise ValueError(f"invalid versioned MANE transcript: {tx}")
            key = stable_id(tx)
            if key in result:
                raise ValueError(f"duplicate MANE Select transcript: {key}")
            number = int(accession[1])
            result[key] = {"transcript": tx, "symbol": row["symbol"],
                           "chrom": {23: "X", 24: "Y"}.get(number, str(number))}
    if not result:
        raise ValueError("MANE summary contains no primary-chromosome Select transcripts")
    return result


class Intervals:
    """Union of zero-based, half-open intervals with bounded lookup memory."""

    def __init__(self, values: dict[str, list[tuple[int, int]]]):
        self.regions = {}
        self.starts = {}
        for chrom, rows in values.items():
            merged = []
            for start, end in sorted(rows):
                if start < 0 or end <= start:
                    raise ValueError("invalid genomic interval")
                if merged and start <= merged[-1][1]:
                    merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
                else:
                    merged.append((start, end))
            self.regions[chrom] = tuple(merged)
            self.starts[chrom] = tuple(start for start, _ in merged)

    def contains(self, chrom: str, position: int) -> bool:
        index = bisect.bisect_right(self.starts.get(chrom, ()), position - 1) - 1
        return index >= 0 and position - 1 < self.regions[chrom][index][1]

    def bases(self) -> int:
        return sum(end - start for rows in self.regions.values() for start, end in rows)


def annotation_regions(gtf: Path, mane: dict[str, dict] | None = None) -> tuple[Intervals, dict]:
    """All CDS intervals, or MANE essential splice sites with gene context.

    GTF is 1-based closed. Splice sites are the first/last two INTRONIC
    bases, not the terminal exon bases. Genomic left/right boundaries are the
    same on both strands; the donor/acceptor labels are reversed on minus.
    """
    cds = defaultdict(list)
    exons = defaultdict(set)
    contexts = {}
    with open_text(gtf) as handle:
        for line in handle:
            if line.startswith("#") or not line.strip():
                continue
            fields = line.rstrip("\n").split("\t")
            if len(fields) != 9:
                raise ValueError("GTF row must have nine columns")
            chrom, _, feature, start, end, _, strand, _, attributes = fields
            chrom = contig(chrom)
            if chrom not in PRIMARY or feature not in {"CDS", "stop_codon", "exon"}:
                continue
            attrs = dict(ATTRIBUTE.findall(attributes))
            start, end = int(start), int(end)
            if start < 1 or end < start:
                raise ValueError("invalid GTF coordinates")
            if mane is None:
                biotype = attrs.get("gene_type") or attrs.get("gene_biotype")
                # Ensembl GTF CDS features exclude the terminal stop codon.
                # Its SNVs are coding changes too (e.g. stop_lost).
                if feature in {"CDS", "stop_codon"} and biotype == "protein_coding":
                    cds[chrom].append((start - 1, end))
                continue
            tx = attrs.get("transcript_id", "")
            selected = mane.get(stable_id(tx))
            # Boundary derivation must use the actual pinned MANE version.
            if not selected or tx != selected["transcript"] or feature != "exon":
                continue
            if selected["chrom"] != chrom or strand not in {"+", "-"}:
                raise ValueError(f"inconsistent MANE GTF context for {tx}")
            # The pinned MANE summary and GTF can use different names for the
            # SAME exact versioned transcript (e.g. LOC versus Ensembl names).
            # The source SpliceAI VCF uses GTF names. Accept only these two
            # transcript-anchored names, never a broad/fuzzy gene alias lookup.
            symbols = tuple(sorted({selected["symbol"], attrs.get("gene_name") or selected["symbol"]}))
            context = (chrom, strand, symbols)
            if tx in contexts and contexts[tx] != context:
                raise ValueError(f"conflicting GTF contexts for {tx}")
            contexts[tx] = context
            exons[tx].add((start, end))
    if mane is None:
        result = Intervals(cds)
        if not result.bases():
            raise ValueError("GTF contains no primary-chromosome CDS intervals")
        return result, {}
    missing = {row["transcript"] for row in mane.values()} - set(exons)
    if missing:
        raise ValueError(f"MANE GTF is missing {len(missing)} selected transcripts")
    sites = defaultdict(set)
    for tx, rows in exons.items():
        chrom, strand, symbols = contexts[tx]
        ordered = sorted(rows)
        for (_, left_end), (right_start, _) in zip(ordered, ordered[1:]):
            if right_start <= left_end + 1:
                raise ValueError(f"overlapping or adjacent exons in {tx}")
            for position in {left_end + 1, left_end + 2, right_start - 2, right_start - 1}:
                if left_end < position < right_start:
                    sites[(chrom, position)].update(symbols)
                    cds[chrom].append((position - 1, position))
    return Intervals(cds), dict(sites)


@contextmanager
def selected_lines(path: Path, regions: Intervals | None = None):
    """Use a local tabix index when available; plain fixtures can stream.

    This avoids parsing billions of noncoding CADD records or nonessential
    SpliceAI records during release preparation. Merged intervals avoid
    duplicate reads at overlapping transcript boundaries.
    """
    if regions is not None and any(Path(str(path) + suffix).is_file() for suffix in (".tbi", ".csi")):
        import pysam
        index_path = next(str(path) + suffix for suffix in (".tbi", ".csi") if Path(str(path) + suffix).is_file())
        with pysam.TabixFile(str(path), index=index_path) as index:
            def lines():
                yield from index.header
                for chrom in index.contigs:
                    for start, end in regions.regions.get(contig(chrom), ()):
                        yield from index.fetch(chrom, start, end)
            yield lines()
    else:
        with open_text(path) as handle:
            yield handle


def tabular_rows(path: Path, required: set[str], regions: Intervals | None = None) -> Iterable[dict[str, str]]:
    with selected_lines(path, regions) as handle:
        header = None
        for number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            if header is None:
                candidate = line.rstrip("\r\n").lstrip("#").split("\t")
                if required <= set(candidate):
                    if len(candidate) != len(set(candidate)):
                        raise ValueError(f"duplicate header columns in {path.name}")
                    header = candidate
                elif not line.startswith("#"):
                    raise ValueError(f"unrecognized schema in {path.name}")
                continue
            if line.startswith("#"):
                continue
            fields = line.rstrip("\r\n").split("\t")
            if len(fields) != len(header):
                raise ValueError(f"wrong column count in {path.name}:{number}")
            yield dict(zip(header, fields))
        if header is None:
            raise ValueError(f"required header not found in {path.name}")


def snv(chrom: str, position: str, ref: str, alt: str) -> tuple[str, int, str, str]:
    chrom = contig(chrom)
    pos = int(position)
    if chrom not in PRIMARY or pos < 1 or ref not in "ACGT" or alt not in "ACGT" or len(ref) != 1 or len(alt) != 1 or ref == alt:
        raise ValueError("expected a primary-chromosome, biallelic GRCh38 SNV")
    return chrom, pos, ref, alt


def number_in_range(value: str, minimum: float, maximum: float) -> None:
    parsed = float(value)
    if not math.isfinite(parsed) or not minimum <= parsed <= maximum:
        raise ValueError(f"score outside [{minimum}, {maximum}]: {value}")


def alphamissense_rows(paths: list[Path], mane: dict, stats: Counter) -> Iterable[list[str]]:
    required = {"CHROM", "POS", "REF", "ALT", "genome", "transcript_id", "protein_variant", "am_pathogenicity", "am_class"}
    covered = set()
    for path in paths:
        for row in tabular_rows(path, required):
            stats["source_rows"] += 1
            tx = row["transcript_id"]
            selected = mane.get(stable_id(tx))
            if not selected:
                continue
            if row["genome"] != "hg38":
                raise ValueError("AlphaMissense source must be hg38")
            chrom, pos, ref, alt = snv(row["CHROM"], row["POS"], row["REF"], row["ALT"])
            if chrom != selected["chrom"]:
                stats["excluded_other_locus"] += 1
                continue
            if not re.fullmatch(r"ENST\d+\.\d+", tx) or not re.fullmatch(r"[A-Z][1-9]\d*[A-Z]", row["protein_variant"]):
                raise ValueError("invalid AlphaMissense transcript/protein key")
            number_in_range(row["am_pathogenicity"], 0, 1)
            if row["am_class"] not in AM_LABELS:
                raise ValueError("unrecognized AlphaMissense classification")
            stats["retained_source_rows"] += 1
            if tx != selected["transcript"]:
                stats["source_transcript_version_differs_from_mane"] += 1
            covered.add(stable_id(tx))
            # Preserve source-version provenance; duplicate resolution uses the
            # runtime's stable transcript + protein-change key, not this version.
            yield [chrom, str(pos), ref, alt, tx, row["protein_variant"],
                   row["am_pathogenicity"], row["am_class"], selected["transcript"],
                   "canonical" if "uniprot_id" in row else "isoforms"]
    stats["mane_transcripts_with_source_scores"] = len(covered)
    stats["mane_transcripts_without_source_scores"] = len(mane) - len(covered)


def cadd_rows(path: Path, cds: Intervals, stats: Counter) -> Iterable[list[str]]:
    for row in tabular_rows(path, {"Chrom", "Pos", "Ref", "Alt", "PHRED"}, cds):
        stats["source_rows"] += 1
        chrom = contig(row["Chrom"])
        if not cds.contains(chrom, int(row["Pos"])):
            continue
        if len(row["Ref"]) != 1 or len(row["Alt"]) != 1:
            stats["excluded_indels"] += 1
            continue
        chrom, pos, ref, alt = snv(chrom, row["Pos"], row["Ref"], row["Alt"])
        number_in_range(row["PHRED"], 0, 100)
        yield [chrom, str(pos), ref, alt, row["PHRED"]]


def spliceai_rows(paths: list[Path], sites: dict, stats: Counter) -> Iterable[list[str]]:
    regions = defaultdict(list)
    for chrom, pos in sites:
        regions[chrom].append((pos - 1, pos))
    intervals = Intervals(regions)
    for path in paths:
        with selected_lines(path, intervals) as handle:
            header_seen = False
            for line in handle:
                if line.startswith("#"):
                    if line.startswith("#CHROM\t"):
                        header_seen = True
                    continue
                if not header_seen:
                    raise ValueError("SpliceAI VCF header is missing")
                fields = line.rstrip("\r\n").split("\t")
                if len(fields) != 8:
                    raise ValueError("SpliceAI source must be a sites-only eight-column VCF")
                chrom, pos = contig(fields[0]), int(fields[1])
                symbols = sites.get((chrom, pos))
                stats["source_rows"] += 1
                if not symbols:
                    continue
                if len(fields[3]) != 1 or len(fields[4]) != 1 or "," in fields[4]:
                    raise ValueError("Pinned SpliceAI source must contain biallelic SNVs only; "
                                     f"unexpected indel or multi-allelic row at {chrom}:{pos}")
                chrom, pos, ref, alt = snv(chrom, fields[1], fields[3], fields[4])
                info = dict(item.split("=", 1) for item in fields[7].split(";") if "=" in item)
                if "SpliceAI" not in info:
                    raise ValueError("essential-site source record lacks SpliceAI")
                selected = []
                for entry in info["SpliceAI"].split(","):
                    values = entry.split("|")
                    if len(values) != 10 or values[0] != alt:
                        raise ValueError("malformed or wrong-allele SpliceAI entry")
                    for value in values[2:6]:
                        number_in_range(value, 0, 1)
                    if any(abs(int(value)) > 500 for value in values[6:]):
                        raise ValueError("SpliceAI delta position exceeds D=500")
                    if values[1] in symbols:
                        selected.append(entry)
                if not selected:
                    stats["excluded_other_gene"] += 1
                    continue
                # No patient columns or unrelated INFO fields enter the bundle.
                yield [chrom, str(pos), ".", ref, alt, ".", ".", "SpliceAI=" + ",".join(sorted(set(selected)))]
            if not header_seen:
                raise ValueError("SpliceAI VCF header is missing")


def indexed_manifest(kind: str, release: str) -> dict:
    am = kind == "alphamissense"
    prefix = "StarterAM" if am else "StarterCADD"
    dimensions = {"allele": {"chrom": "chrom", "position": "position", "reference": "reference", "alternate": "alternate"}}
    required = ["allele"]
    outputs = [{"id": prefix + "_phred", "column": "phred", "type": "number", "direction": "higher", "description": f"Original CADD Phred score ({release}); coding SNVs only"}]
    if am:
        required += ["ensembl_transcript_id", "protein_change"]
        dimensions.update({"ensembl_transcript_id": {"column": "transcript_id", "normalization": "strip_version"},
                           "protein_change": {"column": "protein_change", "normalization": "one_letter_protein_change"}})
        outputs = [{"id": prefix + "_score", "column": "score", "type": "number", "minimum": 0, "maximum": 1, "direction": "higher", "description": "AlphaMissense score, exact allele + stable transcript + protein change"},
                   {"id": prefix + "_prediction", "column": "prediction", "type": "string", "direction": "none", "description": "Original AlphaMissense model classification"}]
    payload = {"manifest_schema": MANIFEST_SCHEMA, "resource": {"id": "starter_" + kind, "name": "Starter " + kind, "release": release},
               "assembly": "GRCh38", "table": {"columns": AM_COLUMNS if am else CADD_COLUMNS},
               "match": {"required": required, "dimensions": dimensions}, "outputs": outputs,
               "provenance": {"match": prefix + "_match", "match_status": prefix + "_match_status",
                              "source_target": prefix + "_source_target", "allele_available": prefix + "_allele_available"}}
    if am:
        payload["applicability"] = {"consequences": ["missense_variant"]}
    return validate_manifest(payload)


def am_runtime_key(columns: list[str]) -> tuple[str, ...]:
    """The IndexedScores strip_version contract (source versions stay in rows)."""
    return (*columns[:4], stable_id(columns[4]), columns[5])


def validate_am_runtime_keys(lines: Iterable[str]) -> int:
    """Reject collisions in a coordinate-sorted table, with bounded memory.

    Keep all keys at a position: version-first sorting can interleave protein
    changes, so comparing only adjacent lines would miss the original defect.
    """
    coordinate, seen, count = None, set(), 0
    for line in lines:
        if line.startswith("#"):
            continue
        columns = line.rstrip("\r\n").split("\t")
        if len(columns) != len(AM_COLUMNS):
            raise ValueError("invalid AlphaMissense table column count")
        current = tuple(columns[:2])
        if current != coordinate:
            coordinate, seen = current, set()
        key = am_runtime_key(columns)
        if key in seen:
            raise ValueError(f"duplicate AlphaMissense runtime key: {key}")
        seen.add(key)
        count += 1
    return count


def resolve_am_duplicates(lines: Iterable[str], stats: Counter, policy: str,
                          examples: list) -> Iterable[str]:
    """Resolve source overlap without choosing a score by magnitude.

    Canonical source precedence is unchanged. Within a source, exact pinned
    MANE-version rows take precedence over older/different versions. Conflicts
    among the winning tier still fail or withhold the entire key. No scores
    are averaged or selected by magnitude. Memory stays bounded per key.
    """
    key = None
    entries, conflicts, disagreements = {}, set(), set()
    mane_target = None

    def emit():
        if not entries:
            return None
        preferred = "canonical" if "canonical" in entries else "isoforms"
        if preferred in conflicts:
            if policy == "error":
                raise ValueError(f"conflicting scores for duplicate key: {key}")
            stats["ambiguous_keys_withheld"] += 1
            if len(examples) < 20:
                examples.append({"key": list(key), "source_table": preferred})
            return None
        selected = entries[preferred].rstrip("\n").split("\t")
        if preferred in disagreements and selected[4] == selected[8]:
            stats["mane_version_conflicts_resolved"] += 1
        if len(entries) == 2:
            stats["canonical_isoform_overlaps"] += 1
            if entries["canonical"].split("\t")[6:8] != entries["isoforms"].split("\t")[6:8]:
                stats["canonical_isoform_disagreements"] += 1
        return entries[preferred]

    for line in lines:
        columns = line.rstrip("\n").split("\t")
        current = am_runtime_key(columns)
        if key is not None and current != key:
            selected = emit()
            if selected is not None:
                yield selected
            entries, conflicts, disagreements = {}, set(), set()
            mane_target = None
        key = current
        if mane_target is not None and columns[8] != mane_target:
            raise ValueError(f"inconsistent pinned MANE target for duplicate key: {key}")
        mane_target = columns[8]
        source = columns[-1]
        if source not in {"canonical", "isoforms"}:
            raise ValueError("unknown AlphaMissense source table")
        if source in entries:
            previous = entries[source].rstrip("\n").split("\t")
            agrees = columns[6:8] == previous[6:8]
            if agrees:
                stats["identical_duplicates_removed"] += 1
                if line != entries[source]:
                    stats["equivalent_version_duplicates_removed"] += 1
            else:
                disagreements.add(source)
            rank, old_rank = columns[4] == columns[8], previous[4] == previous[8]
            if rank > old_rank:
                entries[source] = line
                conflicts.discard(source)
            elif rank == old_rank:
                if not agrees:
                    conflicts.add(source)
                # Equivalent rows retain deterministic version provenance even
                # if a caller supplies a different order within this key.
                elif line < entries[source]:
                    entries[source] = line
        else:
            entries[source] = line
    selected = emit()
    if selected is not None:
        yield selected


def write_sorted(rows: Iterable[list[str]], destination: Path, kind: str, stats: Counter,
                 ambiguous_source_policy: str = "error", withheld_examples: list | None = None) -> None:
    """Disk-backed sorting and duplicate validation; never retain the table in RAM."""
    import pysam  # release preparation dependency only

    if ambiguous_source_policy not in {"error", "withhold"}:
        raise ValueError("unsupported ambiguous source policy")
    if withheld_examples is None:
        withheld_examples = []

    vcf = kind == "spliceai"
    raw = destination.parent / "rows.unsorted.tsv"
    ordered = destination.parent / "rows.sorted.tsv"
    with raw.open("w", encoding="utf-8") as handle:
        for count, row in enumerate(rows, 1):
            # Temporary sort-only column groups stable IDs BEFORE protein and
            # version. Never rewrite the published source transcript column.
            sort_row = row + [stable_id(row[4])] if kind == "alphamissense" else row
            handle.write("\t".join(sort_row) + "\n")
            if count % 5_000_000 == 0:
                print(f"{kind}: selected {count:,} source rows", file=sys.stderr, flush=True)
    print(f"{kind}: sorting selected rows", file=sys.stderr, flush=True)
    keys = ["-k1,1", "-k2,2n"] + (["-k4,4", "-k5,5"] if vcf else ["-k3,3", "-k4,4"])
    if kind == "alphamissense":
        # Canonical published predictions take precedence over supplemental
        # isoform predictions for the SAME biological key, irrespective of
        # file order or score. Exact MANE-version preference resolves only
        # within-source version conflicts; unresolved conflicts remain errors.
        keys += ["-k11,11", "-k6,6", "-k10,10", "-k5,5"]
    subprocess.run(["sort", "-T", str(destination.parent), "-t", "\t", *keys, "-o", str(ordered), str(raw)], check=True, env={**os.environ, "LC_ALL": "C"})
    with pysam.BGZFile(str(destination), "wb") as output, ordered.open(encoding="utf-8") as source:
        if vcf:
            header = '##fileformat=VCFv4.2\n##reference=GRCh38\n##source=GUIDE-IEI-starter-SpliceAI-MANE1.5-D500-M1\n'
            header += '##INFO=<ID=SpliceAI,Number=.,Type=String,Description="ALLELE|SYMBOL|DS_AG|DS_AL|DS_DG|DS_DL|DP_AG|DP_AL|DP_DG|DP_DL">\n'
            header += "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
        else:
            header = "#" + "\t".join(AM_COLUMNS if kind == "alphamissense" else CADD_COLUMNS) + "\n"
        output.write(header.encode())
        last_key, last_line = None, None
        source_rows = (line.rsplit("\t", 1)[0] + "\n" for line in source) if kind == "alphamissense" else source
        resolved = resolve_am_duplicates(source_rows, stats, ambiguous_source_policy, withheld_examples) if kind == "alphamissense" else source_rows
        for line in resolved:
            columns = line.rstrip("\n").split("\t")
            key = am_runtime_key(columns) if kind == "alphamissense" else tuple(columns[i] for i in ([0, 1, 3, 4] if vcf else range(4)))
            if key == last_key:
                if line != last_line:
                    raise ValueError(f"conflicting scores for duplicate key: {key}")
                stats["identical_duplicates_removed"] += 1
                continue
            output.write(line.encode())
            stats["output_rows"] += 1
            if stats["output_rows"] % 5_000_000 == 0:
                print(f"{kind}: compressed {stats['output_rows']:,} resolved rows", file=sys.stderr, flush=True)
            last_key, last_line = key, line
    if not stats["output_rows"]:
        raise ValueError("starter subset is empty")
    print(f"{kind}: indexing and verifying {stats['output_rows']:,} rows", file=sys.stderr, flush=True)
    if vcf:
        pysam.tabix_index(str(destination), preset="vcf", force=True)
    else:
        pysam.tabix_index(str(destination), seq_col=0, start_col=1, end_col=1, force=True)
    # Exercise the index and compare counts before publishing the directory.
    with pysam.TabixFile(str(destination)) as index:
        lines = (line for chrom in index.contigs for line in index.fetch(chrom))
        count = validate_am_runtime_keys(lines) if kind == "alphamissense" else sum(1 for _ in lines)
        if count != stats["output_rows"]:
            raise ValueError("tabix round-trip count differs from prepared rows")
    raw.unlink()
    ordered.unlink()


def prepare(kind: str, sources: list[Path], output: Path, *, release: str,
            mane_summary: Path | None = None, gtf: Path | None = None,
            source_manifest: Path | None = None, ambiguous_source_policy: str = "error") -> dict:
    """Prepare into a NEW directory. Existing installations are never touched."""
    if kind not in {"alphamissense", "cadd", "spliceai"}:
        raise ValueError("unknown starter resource")
    if output.exists():
        raise ValueError("output already exists; use a new versioned build directory")
    if not sources or (kind == "cadd" and len(sources) != 1):
        raise ValueError("expected source files (exactly one for CADD)")
    if kind in {"alphamissense", "spliceai"} and mane_summary is None:
        raise ValueError("MANE summary is required")
    if kind in {"cadd", "spliceai"} and gtf is None:
        raise ValueError("GTF is required")
    if not release.strip():
        raise ValueError("source release is required")
    identities = [file_identity(path) for path in sources]
    source_complete = None
    if kind == "spliceai":
        if source_manifest is None:
            raise ValueError("SpliceAI requires the pinned source release manifest")
        upstream = json.loads(source_manifest.read_text(encoding="utf-8"))
        if (not isinstance(upstream, dict)
                or not isinstance(upstream.get("scientific_configuration"), dict)
                or not isinstance(upstream.get("files"), list)
                or any(not isinstance(row, dict) or not isinstance(row.get("vcf"), str)
                       for row in upstream.get("files", []))):
            raise ValueError("SpliceAI source manifest has an invalid object/settings/files schema")
        settings = upstream.get("scientific_configuration", {})
        if (upstream.get("status") != "passed"
                or upstream.get("genome_assembly") != "GRCh38/hg38"
                or settings.get("distance") != 500 or settings.get("mask") != 1
                or settings.get("spliceai_version") != "1.3.1"
                or settings.get("annotation_release") != "MANE.GRCh38.v1.5.Select"):
            raise ValueError("SpliceAI source manifest must specify GRCh38 MANE v1.5 D=500 M=1")
        if file_identity(gtf)["sha256"] != settings.get("annotation_source_sha256"):
            raise ValueError("SpliceAI boundary GTF differs from the scoring source annotation")
        source_files = {Path(row["vcf"]).name: row for row in upstream.get("files", [])}
        for identity in identities:
            declared = source_files.get(identity["name"], {})
            if (declared.get("vcf_sha256") != identity["sha256"]
                    or declared.get("vcf_bytes") != identity["size_bytes"]):
                raise ValueError("SpliceAI input does not match its release manifest")
        source_complete = {path.name for path in sources} == set(source_files)
        for path in sources:
            index_path = Path(str(path) + ".tbi")
            if index_path.is_file():
                identity = file_identity(index_path)
                if identity["sha256"] != source_files[path.name].get("index_sha256"):
                    raise ValueError("SpliceAI index does not match its release manifest")
                identities.append({"role": "index", **identity})
        identities.append({"role": "source_manifest", **file_identity(source_manifest)})
    for label, path in (("mane_summary", mane_summary), ("gtf", gtf)):
        if path is not None:
            identities.append({"role": label, **file_identity(path)})
    stats = Counter()
    mane = mane_select(mane_summary) if mane_summary else None
    if kind == "alphamissense":
        rows = alphamissense_rows(sources, mane, stats)
        coverage = "MANE Select stable transcript IDs; exact allele and protein-change matching required; original transcript versions retained"
    elif kind == "cadd":
        intervals, _ = annotation_regions(gtf)
        stats["coverage_bases"] = intervals.bases()
        rows = cadd_rows(sources[0], intervals, stats)
        coverage = "Primary-chromosome protein-coding CDS + stop-codon union; SNVs only; Phred only; no intronic padding"
    else:
        intervals, sites = annotation_regions(gtf, mane)
        stats["coverage_bases"] = intervals.bases()
        rows = spliceai_rows(sources, sites, stats)
        coverage = "MANE Select essential donor/acceptor intronic +/-1,2 sites; SNVs only; D=500; M=1"
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".starter-", dir=output.parent) as temporary:
        stage = Path(temporary) / "resource"
        stage.mkdir()
        filename = kind + (".vcf.gz" if kind == "spliceai" else ".tsv.gz")
        score_path = stage / filename
        withheld_examples = []
        write_sorted(rows, score_path, kind, stats, ambiguous_source_policy, withheld_examples)
        files = [file_identity(score_path), file_identity(Path(str(score_path) + ".tbi"))]
        if kind != "spliceai":
            manifest = indexed_manifest(kind, release)
            manifest["files"] = {
                role: {"name": identity["name"], "size": identity["size_bytes"],
                       "sha256": identity["sha256"]}
                for role, identity in zip(("data", "index"), files)
            }
            validate_manifest(manifest)
            (stage / "indexed.manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
            files.append(file_identity(stage / "indexed.manifest.json"))
        files.append(write_license_notices(kind, stage / "LICENSE-NOTICES.json"))
        payload = {"schema": SCHEMA, "resource": "starter_" + kind, "release": release,
                   "assembly": "GRCh38", "coverage": coverage, "sources": identities,
                   "statistics": dict(stats), "files": files,
                   "release_status": "development-not-for-distribution",
                   "preparer_sha256": PREPARER_SHA256}
        if kind == "alphamissense":
            payload["ambiguous_source_policy"] = ambiguous_source_policy
            payload["deduplication_key"] = "allele+stable_transcript_id+protein_change"
            payload["duplicate_precedence"] = ["canonical_before_isoforms", "exact_pinned_mane_version_within_source"]
            payload["withheld_examples"] = withheld_examples
        if source_complete is not None:
            payload["all_source_chromosomes_supplied"] = source_complete
        (stage / "preparation.json").write_text(json.dumps(payload, indent=2) + "\n")
        stage.rename(output)
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=["alphamissense", "cadd", "spliceai"])
    parser.add_argument("--source", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--release", required=True)
    parser.add_argument("--mane-summary", type=Path)
    parser.add_argument("--gtf", type=Path)
    parser.add_argument("--source-manifest", type=Path)
    parser.add_argument("--ambiguous-source-policy", choices=["error", "withhold"], default="error",
                        help="Fail on conflicting source records, or withhold their entire key with an audit entry (AlphaMissense only)")
    args = parser.parse_args(argv)
    try:
        result = prepare(args.kind, args.source, args.output, release=args.release,
                         mane_summary=args.mane_summary, gtf=args.gtf,
                         source_manifest=args.source_manifest,
                         ambiguous_source_policy=args.ambiguous_source_policy)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"Starter preparation failed: {exc}\n")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
