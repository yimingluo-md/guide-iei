#!/usr/bin/env python3
"""Reduce VEP --tab output of pathogenic-missense ClinVar to the aa-match catalog.

Reads the VEP tab file (columns include SYMBOL, Protein_position, Consequence)
and writes the 2-column reference TSV SYMBOL<TAB>Protein_position for rows whose
Consequence contains missense_variant and Protein_position != "-", de-duplicated.

This is the reference-building half of the vep_hg38.sh awk step, made explicit
so the matcher (clinvar_aa_match.py) stays pure and testable.

    reduce_vep_to_aa_reference.py --input clinvar.vep.tsv --output aa_reference.tsv
"""
from __future__ import annotations

import argparse
import gzip
import sys


def _open_r(path):
    return gzip.open(path, "rt") if path.endswith(".gz") else open(path, "rt")


def _open_w(path):
    return gzip.open(path, "wt") if path.endswith(".gz") else open(path, "wt")


def reduce_tab(in_path: str, out_path: str) -> int:
    header = None
    skipped_before_header = 0
    pairs: set[tuple[str, str, str, str]] = set()
    with _open_r(in_path) as fh:
        for line in fh:
            if line.startswith("##"):
                continue
            if line.startswith("#"):
                # VEP tab header row, e.g. "#Uploaded_variation\tSYMBOL\t..."
                header = line.lstrip("#").rstrip("\n").split("\t")
                continue
            if header is None:
                skipped_before_header += 1
                continue
            cols = line.rstrip("\n").split("\t")
            row = dict(zip(header, cols))
            cons = row.get("Consequence", "")
            pos = row.get("Protein_position", "-")
            sym = row.get("SYMBOL", "")
            if "missense_variant" in cons and pos and pos != "-" and sym and sym != "-":
                amino = row.get("Amino_acids", "")
                if "/" in amino:
                    ref_aa, _, alt_aa = amino.partition("/")
                    ref_aa = ref_aa.strip() or "-"
                    alt_aa = alt_aa.strip() or "-"
                else:
                    ref_aa = alt_aa = "-"
                pairs.add((sym, pos, ref_aa, alt_aa))
    if header is None and skipped_before_header:
        # Data rows with no VEP tab header (--no_headers output, or a header
        # consumed upstream) previously produced a silent empty reference
        # that was then stamped as current — fail loudly instead.
        raise ValueError(
            f"no VEP tab header line found in {in_path} but "
            f"{skipped_before_header} data row(s) were present; refusing to "
            "write an empty aa-match reference"
        )
    if header is not None and not pairs:
        # A headered file with zero pathogenic missense rows is a truncated
        # or wrong input, not a real ClinVar release: writing (and stamping)
        # an empty catalog would silently flag 0 for every record.
        raise ValueError(
            f"no pathogenic missense rows found in {in_path}; refusing to "
            "write an empty aa-match reference"
        )
    with _open_w(out_path) as out:
        for sym, pos, ref_aa, alt_aa in sorted(pairs):
            out.write(f"{sym}\t{pos}\t{ref_aa}\t{alt_aa}\n")
    return len(pairs)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    args = ap.parse_args(argv)
    n = reduce_tab(args.input, args.output)
    print(f"[reduce] wrote {n} unique (SYMBOL, Protein_position) residues", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
