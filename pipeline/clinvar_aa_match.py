#!/usr/bin/env python3
"""ClinVar amino-acid-match post-processing for VEP **VCF** output.

Reimplements the awk step in vep_hg38.sh for VCF (INFO/CSQ) instead of --tab.

Original logic (tab output)
---------------------------
A reference dict was built from a VEP-annotated *pathogenic* ClinVar file,
keyed  SYMBOL "_" Protein_position  for rows whose Consequence contained
"missense_variant" and Protein_position != "-".  Each sample row then got a
new column ``ClinVar_path_amino_acid_match`` = 1 if its (SYMBOL,
Protein_position) was in that dict, else 0.

This module
-----------
Does the same match against VCF output:
  * parses the ``CSQ`` format from the VCF header,
  * for each variant, reads SYMBOL / Protein_position / Consequence from each
    CSQ (transcript) entry,
  * looks the (SYMBOL, Protein_position) pair up in a reference catalog of
    pathogenic-missense residues (built by build_clinvar_aa_reference.sh),
  * writes a new INFO field ``ClinVar_path_aa_match`` (0/1) on every record,
    and a matching ``##INFO`` header line.

Zygosity / sample genotype columns are passed through untouched — this only
appends to INFO, so the annotated VCF still preserves GT.

The reference catalog is a 2-column TSV: ``SYMBOL<TAB>protein_position``
(one residue per line; positions in the same string format VEP emits, so both
sides are directly comparable).

Usage:
    clinvar_aa_match.py --input sample.vep.vcf.gz --output sample.aamatch.vcf.gz \\
        [--reference references/clinvar/clinvar_aa_reference.tsv] \\
        [--config config/annotation.config.yaml] [--info-key ClinVar_path_aa_match]
"""
from __future__ import annotations

import argparse
import gzip
import os
import sys


# --------------------------------------------------------------------------- #
# small gzip-aware IO (stdlib only — no pysam/bcftools dependency)
# --------------------------------------------------------------------------- #
def _open_r(path):
    if path == "-":
        return sys.stdin
    return gzip.open(path, "rt") if path.endswith(".gz") else open(path, "rt")


def _open_w(path):
    if path == "-":
        return sys.stdout
    # NOTE: for a real bgzip .gz the run script re-bgzips/indexes afterwards;
    # gzip here keeps the module dependency-free and the content is identical.
    return gzip.open(path, "wt") if path.endswith(".gz") else open(path, "wt")


# --------------------------------------------------------------------------- #
# CSQ header parsing
# --------------------------------------------------------------------------- #
def parse_csq_format(header_lines: list[str]) -> list[str] | None:
    """Return the ordered CSQ subfield names from the ##INFO CSQ header, or None."""
    for line in header_lines:
        if line.startswith("##INFO=<ID=CSQ,") and "Format:" in line:
            fmt = line.split("Format:", 1)[1]
            fmt = fmt.rstrip().rstrip('">').strip()
            return fmt.split("|")
    return None


# --------------------------------------------------------------------------- #
# reference catalog
# --------------------------------------------------------------------------- #
def load_reference(path: str) -> set[tuple[str, str]]:
    """Load the (SYMBOL, protein_position) catalog of pathogenic-missense residues."""
    ref: set[tuple[str, str]] = set()
    if not path or not os.path.exists(path):
        return ref
    with _open_r(path) as fh:
        for line in fh:
            line = line.rstrip("\n")
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            sym, pos = parts[0].strip(), parts[1].strip()
            if sym and pos and pos != "-":
                ref.add((sym, pos))
    return ref


# --------------------------------------------------------------------------- #
# core: does a variant's CSQ collide with a known pathogenic residue?
# --------------------------------------------------------------------------- #
def variant_matches(info_csq: str, fields: list[str], ref: set[tuple[str, str]],
                    idx_sym: int, idx_pos: int, idx_csq: int) -> bool:
    """True if ANY transcript CSQ of this variant is a missense at a residue
    present in the pathogenic reference catalog."""
    for entry in info_csq.split(","):
        vals = entry.split("|")
        if max(idx_sym, idx_pos, idx_csq) >= len(vals):
            continue
        consequence = vals[idx_csq]
        if "missense_variant" not in consequence:
            continue
        pos = vals[idx_pos]
        if not pos or pos == "-":
            continue
        sym = vals[idx_sym]
        if (sym, pos) in ref:
            return True
    return False


def _extract_info_csq(info_field: str) -> str | None:
    for kv in info_field.split(";"):
        if kv.startswith("CSQ="):
            return kv[4:]
    return None


# --------------------------------------------------------------------------- #
# driver
# --------------------------------------------------------------------------- #
def annotate(in_path: str, out_path: str, ref: set[tuple[str, str]],
             info_key: str = "ClinVar_path_aa_match",
             clinvar_release: str = "NA") -> dict:
    """Stream the VCF, add the INFO flag, return summary counts."""
    header: list[str] = []
    stats = {"records": 0, "matched": 0, "missense": 0,
             "csq_present": bool(ref) and True, "ref_size": len(ref)}

    with _open_r(in_path) as fin, _open_w(out_path) as fout:
        fields = None
        idx_sym = idx_pos = idx_csq = -1
        wrote_header = False

        for line in fin:
            if line.startswith("##"):
                header.append(line.rstrip("\n"))
                continue
            if line.startswith("#CHROM"):
                # finalize header: parse CSQ + inject our new INFO line
                fields = parse_csq_format(header)
                if fields:
                    def _fi(name):
                        return fields.index(name) if name in fields else -1
                    idx_sym = _fi("SYMBOL")
                    idx_pos = _fi("Protein_position")
                    idx_csq = _fi("Consequence")
                new_info = (
                    f'##INFO=<ID={info_key},Number=1,Type=Integer,'
                    f'Description="1 if variant shares a residue (SYMBOL+Protein_position) '
                    f'with a pathogenic/likely-pathogenic missense ClinVar variant '
                    f'(ClinVar release {clinvar_release}); else 0">'
                )
                for h in header:
                    fout.write(h + "\n")
                fout.write(new_info + "\n")
                fout.write(line)  # the #CHROM line
                wrote_header = True
                usable = fields is not None and idx_sym >= 0 and idx_pos >= 0 and idx_csq >= 0
                stats["csq_usable"] = usable
                continue

            # data record
            if not wrote_header:
                # VCF with no #CHROM? pass through defensively
                fout.write(line)
                continue
            stats["records"] += 1
            cols = line.rstrip("\n").split("\t")
            info = cols[7] if len(cols) > 7 else ""
            flag = 0
            if fields and idx_sym >= 0 and idx_pos >= 0 and idx_csq >= 0 and ref:
                csq = _extract_info_csq(info)
                if csq:
                    if "missense_variant" in csq:
                        stats["missense"] += 1
                    if variant_matches(csq, fields, ref, idx_sym, idx_pos, idx_csq):
                        flag = 1
                        stats["matched"] += 1
            # append INFO key
            if info in (".", ""):
                cols[7] = f"{info_key}={flag}"
            else:
                cols[7] = f"{info};{info_key}={flag}"
            fout.write("\t".join(cols) + "\n")

    return stats


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", required=True, help="VEP-annotated VCF (.vcf/.vcf.gz)")
    ap.add_argument("--output", required=True)
    ap.add_argument("--reference", default=None,
                    help="pathogenic-missense residue TSV (SYMBOL<TAB>protein_position)")
    ap.add_argument("--config", default=None,
                    help="config YAML (to locate the reference + info key if not given)")
    ap.add_argument("--info-key", default=None)
    ap.add_argument("--clinvar-release", default="NA")
    args = ap.parse_args(argv)

    info_key = args.info_key or "ClinVar_path_aa_match"
    ref_path = args.reference

    if args.config and (ref_path is None):
        try:
            import yaml
            cfg = yaml.safe_load(open(args.config))
            dest = (cfg.get("clinvar", {}) or {}).get("dest_dir", "references/clinvar")
            ref_path = os.path.join(os.path.dirname(os.path.abspath(args.config)),
                                    "..", dest, "clinvar_aa_reference.tsv")
            ref_path = os.path.normpath(ref_path)
        except Exception:
            ref_path = None

    ref = load_reference(ref_path) if ref_path else set()
    if not ref:
        print(f"WARN  aa-match reference empty or missing "
              f"({ref_path}); flag will be 0 for all records.", file=sys.stderr)

    stats = annotate(args.input, args.output, ref,
                     info_key=info_key, clinvar_release=args.clinvar_release)
    print(f"[aa_match] ref_residues={stats['ref_size']} records={stats['records']} "
          f"missense={stats['missense']} matched={stats['matched']} "
          f"csq_usable={stats.get('csq_usable')}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
