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
  * writes TWO deliberately separate INFO fields, each per-ALT (Number=A):

      - ``ClinVar_path_aa_match``        — 1 when the ALT is a missense at the
        SAME RESIDUE as a reported pathogenic/likely-pathogenic missense,
        regardless of which substitution (the reasoning clinicians apply as
        PM5). Residue-level matching is intentional.
      - ``ClinVar_path_aa_change_match`` — 1 when the ALT produces the SAME
        AMINO-ACID CHANGE as a reported P/LP variant, through any nucleotide
        change (the reasoning clinicians apply as PS1).

    Values are comma-separated per ALT allele; on multiallelic records a
    match belonging to one ALT is never copied to its siblings (CSQ entries
    are attributed via ALLELE_NUM when the annotation carries it).

Zygosity / sample genotype columns are passed through untouched — this only
appends to INFO, so the annotated VCF still preserves GT.

The reference catalog is a TSV of ``SYMBOL<TAB>protein_position<TAB>alt_aa``
(one row per reported change; ``alt_aa`` may be ``-`` when unknown). Legacy
2-column files load as residue-only: the change-level flag then stays 0.

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
class Reference:
    """Catalog of reported pathogenic missense: residues and exact changes.

    ``residue_refs`` maps (SYMBOL, position) to the set of reference amino
    acids the catalog knows there ("-" when unknown). A patient entry whose
    OWN reference residue differs is a different isoform numbering, not the
    same residue — matching it would claim PM5/PS1 evidence for an
    incompatible transcript.
    """

    def __init__(self):
        self.residue_refs: dict[tuple[str, str], set[str]] = {}
        self.changes: set[tuple[str, str, str, str]] = set()

    def add_residue(self, sym: str, pos: str, ref_aa: str) -> None:
        self.residue_refs.setdefault((sym, pos), set()).add(ref_aa or "-")

    def __len__(self):
        return len(self.residue_refs)


def load_reference(path: str) -> Reference:
    """Load the pathogenic-missense catalog (residues + exact changes).

    Formats accepted: 4 columns (SYMBOL, pos, ref_aa, alt_aa — current),
    3 columns (SYMBOL, pos, alt_aa — transitional), 2 columns (residue
    only — legacy). Missing residue fields load as "-" (unknown, matches
    anything) so older catalogs keep working until their next rebuild.
    """
    ref = Reference()
    if not path or not os.path.exists(path):
        return ref
    with _open_r(path) as fh:
        for line in fh:
            line = line.rstrip("\n")
            if not line or line.startswith("#"):
                continue
            parts = [part.strip() for part in line.split("\t")]
            if len(parts) < 2:
                continue
            sym, pos = parts[0], parts[1]
            if not (sym and pos and pos != "-"):
                continue
            if len(parts) >= 4:
                ref_aa, alt_aa = parts[2] or "-", parts[3] or "-"
            else:
                # 2- and 3-column catalogs predate the reference-residue
                # column. Without a reference residue the exact-change claim
                # cannot be guarded against incompatible isoforms, so such
                # rows contribute residue-level (PM5-style) evidence only;
                # the change flag stays 0 until the catalog rebuilds.
                ref_aa, alt_aa = "-", "-"
            ref.add_residue(sym, pos, ref_aa)
            if alt_aa != "-":
                ref.changes.add((sym, pos, ref_aa, alt_aa))
    return ref


# --------------------------------------------------------------------------- #
# core: does a variant's CSQ collide with a known pathogenic residue?
# --------------------------------------------------------------------------- #
def match_alleles(info_csq: str, fields: list[str], ref: Reference,
                  n_alts: int, idx_sym: int, idx_pos: int, idx_csq: int,
                  idx_allele_num: int, idx_aa: int) -> tuple[list[int], list[int]]:
    """Per-ALT residue and exact-change matches for one record.

    CSQ entries are attributed to their ALT via ALLELE_NUM; when the
    annotation lacks ALLELE_NUM, an entry counts toward every ALT (the
    legacy record-level behavior, now the explicit fallback)."""
    residue_flags = [0] * n_alts
    change_flags = [0] * n_alts
    for entry in info_csq.split(","):
        vals = entry.split("|")
        if max(idx_sym, idx_pos, idx_csq) >= len(vals):
            continue
        if "missense_variant" not in vals[idx_csq]:
            continue
        pos = vals[idx_pos]
        if not pos or pos == "-":
            continue
        sym = vals[idx_sym]
        catalog_refs = ref.residue_refs.get((sym, pos))
        if not catalog_refs:
            continue
        patient_ref = patient_alt = ""
        if idx_aa >= 0 and idx_aa < len(vals) and "/" in vals[idx_aa]:
            patient_ref, _, patient_alt = vals[idx_aa].partition("/")
            patient_ref, patient_alt = patient_ref.strip(), patient_alt.strip()
        # Same residue requires the same reference amino acid where both
        # sides know it; a differing reference means a different isoform
        # numbering, and flagging it would claim evidence for an
        # incompatible transcript. "-" (unknown) on either side matches.
        if patient_ref and not ({patient_ref, "-"} & catalog_refs):
            continue
        targets = range(n_alts)
        if idx_allele_num >= 0 and idx_allele_num < len(vals):
            number = vals[idx_allele_num].strip()
            if number.isdigit() and 1 <= int(number) <= n_alts:
                targets = [int(number) - 1]
        change_hit = bool(patient_alt) and (
            (sym, pos, patient_ref or "-", patient_alt) in ref.changes
            or (sym, pos, "-", patient_alt) in ref.changes
        )
        for target in targets:
            residue_flags[target] = 1
            if change_hit:
                change_flags[target] = 1
    return residue_flags, change_flags


def _extract_info_csq(info_field: str) -> str | None:
    for kv in info_field.split(";"):
        if kv.startswith("CSQ="):
            return kv[4:]
    return None


# --------------------------------------------------------------------------- #
# driver
# --------------------------------------------------------------------------- #
def annotate(in_path: str, out_path: str, ref: Reference,
             info_key: str = "ClinVar_path_aa_match",
             clinvar_release: str = "NA") -> dict:
    """Stream the VCF, add both INFO flags, return summary counts."""
    header: list[str] = []
    change_key = "ClinVar_path_aa_change_match" \
        if info_key == "ClinVar_path_aa_match" else f"{info_key}_change"
    stats = {"records": 0, "matched": 0, "change_matched": 0, "missense": 0,
             "csq_present": False, "ref_size": len(ref)}

    with _open_r(in_path) as fin, _open_w(out_path) as fout:
        fields = None
        idx_sym = idx_pos = idx_csq = idx_allele_num = idx_aa = -1
        wrote_header = False

        for line in fin:
            if line.startswith("##"):
                header.append(line.rstrip("\n"))
                continue
            if line.startswith("#CHROM"):
                # finalize header: parse CSQ + inject our new INFO line
                fields = parse_csq_format(header)
                if fields:
                    # bind the resolved schema by value: a later rebind of
                    # `fields` must not silently change column resolution
                    def _fi(name, _fields=fields):
                        return _fields.index(name) if name in _fields else -1
                    idx_sym = _fi("SYMBOL")
                    idx_pos = _fi("Protein_position")
                    idx_csq = _fi("Consequence")
                    idx_allele_num = _fi("ALLELE_NUM")
                    idx_aa = _fi("Amino_acids")
                residue_info = (
                    f'##INFO=<ID={info_key},Number=A,Type=Integer,'
                    f'Description="Per ALT: 1 when this allele is a missense at the same '
                    f'protein residue (SYMBOL+Protein_position, any substitution) as a '
                    f'pathogenic/likely-pathogenic missense ClinVar variant — PM5-style '
                    f'residue-level evidence (ClinVar release {clinvar_release}); else 0">'
                )
                change_info = (
                    f'##INFO=<ID={change_key},Number=A,Type=Integer,'
                    f'Description="Per ALT: 1 when this allele produces the same amino-acid '
                    f'change as a pathogenic/likely-pathogenic ClinVar variant through any '
                    f'nucleotide change — PS1-style exact-change evidence (ClinVar release '
                    f'{clinvar_release}); else 0">'
                )
                for h in header:
                    # Idempotency: a second pass over already-annotated
                    # output must replace our header lines, not duplicate
                    # them.
                    if h.startswith(f"##INFO=<ID={info_key},") or \
                            h.startswith(f"##INFO=<ID={change_key},"):
                        continue
                    fout.write(h + "\n")
                fout.write(residue_info + "\n")
                fout.write(change_info + "\n")
                fout.write(line)  # the #CHROM line
                wrote_header = True
                usable = fields is not None and idx_sym >= 0 and idx_pos >= 0 and idx_csq >= 0
                # csq_present now reports what its name says: whether the VCF
                # declared a CSQ schema (it previously mirrored reference-set
                # emptiness, an unrelated quantity).
                stats["csq_present"] = fields is not None
                stats["csq_usable"] = usable
                continue

            # data record
            if not wrote_header:
                # VCF with no #CHROM? pass through defensively
                fout.write(line)
                continue
            stats["records"] += 1
            cols = line.rstrip("\n").split("\t")
            # Pad short records (sites-only VCFs, truncated final lines) up to
            # the INFO column: the flag assignment below writes cols[7]
            # unconditionally and list assignment does not extend.
            if len(cols) < 8:
                cols += ["."] * (8 - len(cols))
            info = cols[7]
            n_alts = max(1, len(cols[4].split(","))) if len(cols) > 4 else 1
            residue_flags = [0] * n_alts
            change_flags = [0] * n_alts
            if fields and idx_sym >= 0 and idx_pos >= 0 and idx_csq >= 0 and ref:
                csq = _extract_info_csq(info)
                if csq:
                    if "missense_variant" in csq:
                        stats["missense"] += 1
                    residue_flags, change_flags = match_alleles(
                        csq, fields, ref, n_alts,
                        idx_sym, idx_pos, idx_csq, idx_allele_num, idx_aa,
                    )
                    if any(residue_flags):
                        stats["matched"] += 1
                    if any(change_flags):
                        stats["change_matched"] += 1
            keys = (
                f"{info_key}={','.join(map(str, residue_flags))};"
                f"{change_key}={','.join(map(str, change_flags))}"
            )
            # Strip a previous pass's values so re-running replaces them.
            retained = [
                item for item in info.split(";")
                if item not in ("", ".")
                and not item.startswith(f"{info_key}=")
                and not item.startswith(f"{change_key}=")
            ]
            cols[7] = ";".join(retained + [keys]) if retained else keys
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
    ap.add_argument("--allow-missing-reference", action="store_true",
                    help="proceed with an all-zero flag when no reference is "
                         "available (an explicit choice, not a silent default)")
    args = ap.parse_args(argv)

    info_key = args.info_key or "ClinVar_path_aa_match"
    ref_path = args.reference

    if args.config and (ref_path is None):
        # Narrow handling: a config-resolution failure must be visible, not
        # silently degrade into an all-zero flag column that is
        # indistinguishable from "no ClinVar residue matches in this sample".
        try:
            import yaml
        except ImportError as exc:
            print(f"WARN  aa-match config resolution unavailable: {exc}",
                  file=sys.stderr)
        else:
            try:
                cfg = yaml.safe_load(open(args.config))
                dest = (cfg.get("clinvar", {}) or {}).get("dest_dir", "references/clinvar")
                ref_path = os.path.join(os.path.dirname(os.path.abspath(args.config)),
                                        "..", dest, "clinvar_aa_reference.tsv")
                ref_path = os.path.normpath(ref_path)
            except (OSError, yaml.YAMLError, AttributeError, KeyError,
                    TypeError, ValueError) as exc:
                print(f"WARN  aa-match config resolution failed: {exc}",
                      file=sys.stderr)
                ref_path = None

    ref = load_reference(ref_path) if ref_path else set()
    if not ref:
        message = (f"aa-match reference empty or missing ({ref_path}); "
                   "every record would be flagged 0")
        if not args.allow_missing_reference:
            print(f"ERROR {message}. Rebuild the reference, or pass "
                  "--allow-missing-reference to proceed deliberately.",
                  file=sys.stderr)
            return 3
        print(f"WARN  {message}; proceeding as requested.", file=sys.stderr)

    stats = annotate(args.input, args.output, ref,
                     info_key=info_key, clinvar_release=args.clinvar_release)
    print(f"[aa_match] ref_residues={stats['ref_size']} records={stats['records']} "
          f"missense={stats['missense']} residue_matched={stats['matched']} "
          f"change_matched={stats['change_matched']} "
          f"csq_usable={stats.get('csq_usable')}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
