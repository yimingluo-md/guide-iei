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

The reference catalog is a TSV of
``SYMBOL<TAB>protein_position<TAB>ref_aa<TAB>alt_aa<TAB>transcript`` (one row
per reported change; ``-`` marks unknown fields). The transcript column
records which transcript's numbering the position uses; matching requires
the patient CSQ entry to come from that same transcript (version-stripped),
because a position number is only meaningful within one transcript. Legacy
2-/3-column files load as residue-only (the change-level flag stays 0) and
4-column files match without the transcript gate, both guarded by the
reference-residue check until their next rebuild.

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

    ``residues`` maps (SYMBOL, position) to {transcript: {ref_aa, ...}} —
    the reference amino acids the catalog knows at that position, PER
    TRANSCRIPT. Protein positions are only meaningful within one
    transcript's numbering, so both structures carry the version-stripped
    transcript VEP --pick chose at build time; a patient entry matches
    only against rows numbered on its own transcript. A patient entry
    whose OWN reference residue differs from those rows is a different
    isoform numbering and never matches.

    ``transcript_aware`` is a property of the loaded FILE: catalogs that
    predate the transcript column (rows loaded under "-") fall back to
    the reference-residue guard alone, but a transcript-aware catalog
    never lets a stray "-" row reopen cross-transcript matching.

    ``changes`` holds (SYMBOL, position, ref_aa, alt_aa, transcript) —
    transcript-keyed for the same reason: an exact change recorded on one
    isoform's numbering is not evidence on another's.
    """

    def __init__(self):
        self.residues: dict[tuple[str, str], dict[str, set[str]]] = {}
        self.changes: set[tuple[str, str, str, str, str]] = set()
        self.transcript_aware = False

    def add_residue(self, sym: str, pos: str, ref_aa: str,
                    transcript: str = "-") -> None:
        transcript = transcript or "-"
        self.residues.setdefault((sym, pos), {}).setdefault(
            transcript, set()
        ).add(ref_aa or "-")
        if transcript != "-":
            self.transcript_aware = True

    def __len__(self):
        return len(self.residues)


def load_reference(path: str) -> Reference:
    """Load the pathogenic-missense catalog (residues + exact changes).

    Formats accepted: 5 columns (SYMBOL, pos, ref_aa, alt_aa, transcript —
    current), 4 columns (no transcript), 3 columns (SYMBOL, pos, alt_aa —
    transitional), 2 columns (residue only — legacy). Missing residue and
    transcript fields load as "-" (unknown, matches anything) so older
    catalogs keep working until their next rebuild.
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
            transcript = parts[4] if len(parts) >= 5 and parts[4] else "-"
            if transcript != "-":
                transcript = transcript.split(".")[0]
            ref.add_residue(sym, pos, ref_aa, transcript)
            if alt_aa != "-":
                ref.changes.add((sym, pos, ref_aa, alt_aa, transcript))
    return ref


# --------------------------------------------------------------------------- #
# core: does a variant's CSQ collide with a known pathogenic residue?
# --------------------------------------------------------------------------- #
def match_alleles(info_csq: str, fields: list[str], ref: Reference,
                  n_alts: int, idx_sym: int, idx_pos: int, idx_csq: int,
                  idx_allele_num: int, idx_aa: int,
                  idx_feature: int = -1) -> tuple[list[int], list[int], int]:
    """Per-ALT residue and exact-change matches for one record.

    CSQ entries are attributed to their ALT via ALLELE_NUM; when the
    annotation lacks ALLELE_NUM, an entry counts toward every ALT (the
    legacy record-level behavior, now the explicit fallback). The third
    return value counts entries at catalog residues that were skipped by
    the transcript gate — a nonzero count with zero matches is the
    signature of a catalog built against a different VEP cache."""
    residue_flags = [0] * n_alts
    change_flags = [0] * n_alts
    tx_blocked = 0
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
        rows = ref.residues.get((sym, pos))
        if not rows:
            continue
        # The catalog's position was numbered against the transcript VEP
        # --pick chose at build time — recorded per row. A patient CSQ
        # entry from another transcript is a different coordinate system:
        # position 100 there is not the catalog's residue 100 even when
        # the reference amino acid happens to coincide. Enforced whenever
        # the catalog is transcript-aware and the patient entry names its
        # transcript; both unknown spellings ("" and "-") fall back to the
        # residue guard alone.
        patient_tx = ""
        if idx_feature >= 0 and idx_feature < len(vals):
            patient_tx = vals[idx_feature].strip().split(".")[0]
            if patient_tx == "-":
                patient_tx = ""
        if ref.transcript_aware and patient_tx:
            row_refs = rows.get(patient_tx)
            if row_refs is None:
                tx_blocked += 1
                continue
            catalog_txs: tuple[str, ...] = (patient_tx,)
            catalog_refs = row_refs
        else:
            catalog_txs = tuple(rows)
            catalog_refs = set().union(*rows.values())
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
        # The exact-change lookup is keyed to the same transcript(s) the
        # entry matched against: a change recorded on another isoform's
        # numbering is not this residue's change.
        change_hit = bool(patient_alt) and any(
            (sym, pos, patient_ref or "-", patient_alt, tx) in ref.changes
            or (sym, pos, "-", patient_alt, tx) in ref.changes
            for tx in catalog_txs
        )
        for target in targets:
            residue_flags[target] = 1
            if change_hit:
                change_flags[target] = 1
    return residue_flags, change_flags, tx_blocked


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
             "csq_present": False, "ref_size": len(ref), "tx_gate_blocked": 0}

    with _open_r(in_path) as fin, _open_w(out_path) as fout:
        fields = None
        idx_sym = idx_pos = idx_csq = idx_allele_num = idx_aa = idx_feature = -1
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
                    idx_feature = _fi("Feature")
                residue_info = (
                    f'##INFO=<ID={info_key},Number=A,Type=Integer,'
                    f'Description="Per ALT: 1 when this allele is a missense at the same '
                    f'protein residue (same transcript numbering, any substitution) as a '
                    f'pathogenic/likely-pathogenic missense ClinVar variant — PM5-style '
                    f'residue-level evidence (ClinVar release {clinvar_release}); else 0">'
                )
                change_info = (
                    f'##INFO=<ID={change_key},Number=A,Type=Integer,'
                    f'Description="Per ALT: 1 when this allele produces the same amino-acid '
                    f'change (same transcript numbering) as a pathogenic/likely-pathogenic '
                    f'ClinVar variant through any nucleotide change — PS1-style '
                    f'exact-change evidence (ClinVar release {clinvar_release}); else 0">'
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
                    residue_flags, change_flags, tx_blocked = match_alleles(
                        csq, fields, ref, n_alts,
                        idx_sym, idx_pos, idx_csq, idx_allele_num, idx_aa,
                        idx_feature,
                    )
                    stats["tx_gate_blocked"] += tx_blocked
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
                    help="pathogenic-missense catalog TSV (SYMBOL, position, "
                         "ref_aa, alt_aa, transcript)")
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
          f"tx_gate_blocked={stats['tx_gate_blocked']} "
          f"csq_usable={stats.get('csq_usable')}", file=sys.stderr)
    if stats["tx_gate_blocked"] and not stats["matched"]:
        # Every candidate sat at a catalog residue but on a transcript the
        # catalog was never numbered against, and nothing matched at all:
        # the signature of a catalog built with a different VEP cache (or
        # RefSeq vs Ensembl annotation), which would otherwise present as
        # an unremarkable all-negative sample.
        print(
            f"WARN  aa-match: {stats['tx_gate_blocked']} annotation(s) at "
            "catalog residues were skipped because their transcripts are "
            "not in the catalog, and no record matched — the catalog and "
            "this annotation may come from different VEP caches; rebuild "
            "the aa-match reference.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
