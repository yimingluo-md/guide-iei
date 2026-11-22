#!/usr/bin/env python3
"""Plan collision-free VCF chromosome names before filtering or annotation."""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

if __package__:
    from .vcf_io import open_text
else:
    from vcf_io import open_text


def primary_name(name):
    bare = name.removeprefix("chr")
    if bare in {"MT", "M"}:
        return "MT"
    if re.fullmatch(r"(?:[1-9]|1[0-9]|2[0-2]|X|Y)", bare):
        return bare
    return None


def plan(vcf, bed, observed=None):
    declared = {}
    records = {}
    with open_text(vcf) as stream:
        for line in stream:
            if line.startswith("##contig=<"):
                match = re.search(r"(?:<|,)ID=([^,>]+)", line)
                if match:
                    declared[match[1]] = None
            elif not line.startswith("#"):
                if observed is not None:
                    break
                records[line.partition("\t")[0]] = None
    if observed is not None:
        records = dict.fromkeys(name.rstrip("\n") for name in observed if name.strip())
    bed_names = set()
    if bed:
        with open_text(bed) as stream:
            for line in stream:
                if line.strip() and not line.startswith("#"):
                    bed_names.add(line.partition("\t")[0])
    targets = {}
    for name in bed_names:
        primary = primary_name(name)
        if primary and primary in targets:
            raise ValueError(f"BED contains two chromosome names for {primary}: {targets[primary]} and {name}")
        if primary:
            targets[primary] = name
    prefixed = any(n.startswith("chr") for n in bed_names)
    owners = {}
    mapping = []
    for source in dict.fromkeys((*declared, *records)):
        primary = primary_name(source)
        dest = source
        if primary:
            default = ("chrM" if primary == "MT" else "chr" + primary) if prefixed else primary
            dest = targets.get(primary, default)
        elif source not in bed_names:
            alias = source[3:] if source.startswith("chr") else "chr" + source
            if alias in bed_names:
                dest = alias
        if dest in owners:
            raise ValueError(f"VCF contigs {owners[dest]} and {source} both map to {dest}. "
                             "Reconcile their names in the source VCF before annotation.")
        owners[dest] = source
        if source != dest:
            mapping.append((source, dest))
    missing = [name for name in records if name not in declared]
    return mapping, missing


CONTIG_ID = re.compile(r"((?:<|,)ID=)([^,>]+)")
BREAKEND = re.compile(r"([\[\]])([^\[\]]+):([0-9]+)([\[\]])")


def rewrite(vcf, map_file, extra_header, out):
    renames = dict(line.split("\t") for line in map_file.read_text().splitlines())
    additional = extra_header.read_text()
    with open_text(vcf) as stream:
        for line in stream:
            if line.startswith("#"):
                if line.startswith("##contig=<"):
                    line = CONTIG_ID.sub(lambda m: m[1] + renames.get(m[2], m[2]), line, count=1)
                elif line.startswith("#CHROM\t"):
                    out.write(additional)
                out.write(line)
                continue
            # Preserve genotypes and annotations verbatim, including on wide
            # cohort rows. Only chromosome references change.
            chrom, _, rest = line.partition("\t")
            if "[" in rest or "]" in rest or "CHR2=" in rest:
                fields = rest.split("\t", 7)
                fields[3] = BREAKEND.sub(
                    lambda m: f"{m[1]}{renames.get(m[2], m[2])}:{m[3]}{m[4]}", fields[3])
                fields[6] = re.sub(
                    r"(^|;)CHR2=([^;\n]+)",
                    lambda m: m[1] + "CHR2=" + ",".join(renames.get(c, c) for c in m[2].split(",")),
                    fields[6])
                rest = "\t".join(fields)
            out.write(renames.get(chrom, chrom) + "\t" + rest)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vcf", required=True)
    parser.add_argument("--bed", help="Use this BED's chromosome labels (default: Ensembl primary names)")
    parser.add_argument("--observed-stdin", action="store_true", help="Read contigs collected during full VCF validation")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--extra-header", type=Path, help="Write declarations for observed but undeclared contigs")
    parser.add_argument("--apply", action="store_true", help="Apply the prepared map and extra header to stdout")
    args = parser.parse_args()
    if args.apply:
        if args.extra_header is None:
            parser.error("--apply requires --extra-header")
        rewrite(args.vcf, args.output, args.extra_header, sys.stdout)
        return 0
    try:
        mapping, missing = plan(args.vcf, args.bed, sys.stdin if args.observed_stdin else None)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        if args.extra_header:
            for name in missing:
                if re.search(r'[<>,\s"]', name):
                    raise ValueError(f"Cannot create a VCF contig declaration for {name!r}")
            names = dict(mapping)
            args.extra_header.write_text("".join(f"##contig=<ID={names.get(n, n)}>\n" for n in missing))
        args.output.write_text("".join(f"{old}\t{new}\n" for old, new in mapping))
    except ValueError as exc:
        parser.exit(2, f"ERROR: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
