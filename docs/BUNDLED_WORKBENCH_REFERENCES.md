# Bundled workbench reference data

The local review workbench ships compact gene-level resources under
`webui/public/bundled-data/`. They are loaded automatically when the UI starts;
users do not need to add these fields to the VCF or download separate tables.

`manifest.json` records the exact release, source URL, source SHA-256, input row
counts, output counts, and cleaning method. The full downloaded source files are
not committed because the gnomAD input is approximately 575 MB.

## gnomAD gene constraint

- Release: **gnomAD v4.1.1**
- Official source:
  https://storage.googleapis.com/gcp-public-data--gnomad/release/4.1.1/constraint/gnomad.v4.1.1.constraint_metrics.tsv.bgz
- Release notes:
  https://gnomad.broadinstitute.org/news/2026-03-gnomad-v4-1-1/
- Bundled output: `gnomad_v4.1.1_gene_constraint.tsv`
- Output: one record for each of 19,638 genes

The source has transcript-level rows. Selection is deterministic:

1. Ensembl MANE Select;
2. Ensembl canonical;
3. any MANE Select or canonical transcript;
4. Ensembl protein-coding;
5. longest coding sequence, with transcript ID as a stable tie-breaker.

The bundle retains pLI, LOEUF (`lof.oe_ci.upper`), missense Z, LoF observed and
expected counts, LoF o/e, constraint flags, gene flags, and exome
coverage/SegDup/LCR quality metrics. The UI joins these automatically by
case-insensitive gene symbol. VCF-provided values are only a fallback when the
bundled field is missing.

gnomAD v4.1.1 recommends LOEUF as the primary current LoF-constraint measure.
The release recommends LOEUF below 0.45 when a threshold for highly
LoF-constrained genes is required; the workbench displays the continuous value
and does not convert it into a clinical classification.

## IUIS IEI classification

- Release label: **Updated IEI classification table (October 2024)**
- IUIS committee page: https://iuis.org/committees/iei/
- Official workbook:
  https://wp-iuis.s3.eu-west-1.amazonaws.com/app/uploads/2024/10/30094653/IUIS-IEI-list-for-web-site-July-2024V2.xlsx
- Bundled outputs:
  - `iuis_2024_classification.tsv`
  - `iuis_2024_genes.txt`
  - `iuis_2024_dominant_genes.txt`

The source contains 582 disease rows. The clean table preserves the source row,
raw genetic-defect label, disease, inheritance, GOF/DN field, OMIM number,
category, and subcategory. Composite gene entries are split. Known legacy
symbols are mapped to current VEP-compatible symbols, while the original label
remains beside every normalized record. Cytogenetic and unknown entries are
retained with an empty `gene_symbol`.

The resulting filter sets contain:

- 505 unique normalized IEI gene symbols;
- 137 genes with at least one inheritance entry containing `AD`.

The dominant set is an inheritance filter, not a claim that every variant in
the gene acts dominantly. Variant-specific mechanism and disease context still
require review.

No haploinsufficiency list is inferred from pLI, LOEUF, or IUIS inheritance.
Those concepts are not interchangeable. A lab-curated or separately
authoritative haploinsufficiency list can still be loaded as an override.

## Rebuild or update

For maintainers updating a release:

```bash
bash scripts/update_workbench_references.sh
```

The update script downloads both official sources to a temporary directory,
verifies the pinned SHA-256 checksums, regenerates the compact files, and
validates expected row counts. A changed upstream file intentionally fails
checksum validation so release changes must be reviewed and versioned rather
than silently entering the clinical workbench.
