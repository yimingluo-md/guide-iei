#!/usr/bin/env python3
"""Reproducibly derive starter gene identities from the pinned MANE 1.5 inputs.

No scores, model weights, inferred synonyms or clinical data are included.
The mapping uses only names attached to the same exact versioned transcript.
"""
import argparse
import csv
import gzip
import hashlib
import json
from pathlib import Path
import re

PINS = {
    "mane_summary": "d10ace2720681a3b2e0eefd9da4f551274a6b4141ac9bfd6a2565dfb6e9ad55c",
    "gtf": "71d4b3c89c9d948683bf0db5d81b00d9ae9f3b943177b74cbd87e68f08e34d66",
}


def build(summary, gtf):
    for role, path in (("mane_summary", summary), ("gtf", gtf)):
        if hashlib.sha256(path.read_bytes()).hexdigest() != PINS[role]:
            raise ValueError(f"{role} differs from the pinned MANE 1.5 source")
    selected = {}
    with gzip.open(summary, "rt") as handle:
        for row in csv.DictReader(handle, delimiter="\t"):
            if row["MANE_status"] == "MANE Select":
                selected[row["Ensembl_nuc"]] = row
    genes, seen, owners = {}, set(), {}
    with gzip.open(gtf, "rt") as handle:
        for line in handle:
            if line.startswith("#"):
                continue
            fields = line.rstrip().split("\t")
            if len(fields) != 9 or fields[2] != "transcript":
                continue
            attrs = dict(re.findall(r'(\w+) "([^"]*)"', fields[8]))
            tx = attrs.get("transcript_id")
            if tx not in selected:
                continue
            row = selected[tx]
            gene = attrs["gene_id"].split(".")[0]
            if gene != row["Ensembl_Gene"].split(".")[0] or tx in seen:
                raise ValueError(f"conflicting transcript identity: {tx}")
            seen.add(tx)
            names = sorted({row["symbol"], attrs["gene_name"]})
            chrom = fields[0].removeprefix("chr")
            if gene in genes:
                raise ValueError(f"multiple MANE Select transcripts for {gene}")
            genes[gene] = {"chrom": chrom, "symbols": names, "transcript": tx}
            for name in names:
                owners.setdefault((chrom, name), set()).add(gene)
    if set(selected) != seen:
        raise ValueError("MANE Select transcript missing from GTF")
    # An ambiguous name cannot identify which source gene supplied a score.
    ambiguous = sorted([list(key) for key, ids in owners.items() if len(ids) != 1])
    for gene, row in genes.items():
        row["symbols"] = [s for s in row["symbols"] if len(owners[(row["chrom"], s)]) == 1]
    return {"schema": "guide-iei.spliceai-gene-map/v1", "assembly": "GRCh38",
            "mane_version": "1.5", "source_sha256": PINS,
            "attribution": "NCBI/Ensembl MANE v1.5 summary and Ensembl genomic GTF; names joined by exact versioned transcript and stable Ensembl gene ID",
            "excluded_ambiguous_symbols": ambiguous, "genes": genes}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("summary", type=Path)
    parser.add_argument("gtf", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    result = build(args.summary, args.gtf)
    args.output.write_text(json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n")
    print(f"Wrote {len(result['genes'])} stable gene identities; {len(result['excluded_ambiguous_symbols'])} ambiguous names withheld")
