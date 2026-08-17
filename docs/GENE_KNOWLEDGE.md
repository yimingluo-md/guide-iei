---
title: "Gene knowledge"
parent: Reference
nav_order: 14
---

# Gene-level knowledge resources

Gene-level evidence is maintained separately from VEP variant annotation. A
resource update changes the local review display immediately and does not
require reannotating or reimporting VCFs.

## Sources and scope

The redistributable `gene_knowledge_public.sqlite3` bundle contains:

- the HGNC complete set for approved symbols, stable HGNC identifiers,
  Ensembl/NCBI identifiers, previous symbols, and aliases;
- the reviewed IUIS October 2024 IEI classification, preserving every disease
  row, major category, subcategory, inheritance, GOF/DN mechanism, T-cell
  count, B-cell count, immunoglobulin level, neutrophil count, other affected
  cells, associated features, and source wording;
- the current ClinGen Gene-Disease Validity summary;
- the ClinGen GRCh38 dosage-sensitivity gene list, retaining haploinsufficiency
  and triplosensitivity scores as separate evaluations.

The exact source URL, download SHA-256, release date, row count, and build time
are stored inside the database. `scripts/update_gene_knowledge.sh` downloads
only official public HGNC and ClinGen sources, validates their schemas, builds
a new SQLite file, and replaces the destination only after a successful build.
The IUIS table remains pinned until a new IUIS release is reviewed explicitly.

ClinGen dosage score 3 means sufficient evidence for dosage pathogenicity.
Score 30 means that a gene is associated with an autosomal-recessive phenotype;
it is not evidence for haploinsufficiency. gnomAD LOEUF/pLI are population
constraint metrics and are displayed separately from ClinGen dosage evidence.

## OMIM licensing and local installation

OMIM data are **not included** in the source repository, public bundle, release
artifacts, or automatic downloads. A user with appropriate OMIM data access can
open **Gene knowledge** and select a local directory containing all four
official flat files:

- `mim2gene.txt`
- `mimTitles.txt`
- `genemap2.txt`
- `morbidmap.txt`

The importer validates the four schemas, records local file checksums, and
creates a private index under the selected Sample Library & Cohort storage
root. Source files remain in their user-managed directory. Credential-bearing
download URLs are never requested, stored, logged, or copied by the software.

If the private index is absent, the UI says **OMIM dataset not installed**. It
must not imply that a gene lacks an OMIM association. Reinstalling replaces the
private index atomically after successful parsing.

## Identity and assertion handling

HGNC is the identity authority. Lookups prefer stable HGNC/Ensembl identifiers
or an approved symbol, then use an alias only when that alias maps
unambiguously to one approved gene. Every IUIS, ClinGen, and OMIM assertion is
retained as a separate source row; the software does not collapse several
diseases, inheritance modes, or mechanisms into a single synthetic label.

The Gene review warns that gene-level association, dosage, and constraint
evidence do not establish the pathogenicity or disease relevance of the
selected variant. ClinGen variant-level evidence is intentionally outside this
gene-level implementation and can be added independently later.

### IUIS immune-profile summaries

IUIS laboratory fields contain heterogeneous prose rather than a controlled
vocabulary. The UI therefore uses non-exclusive tags rather than forcing each
field into one category. Current tags are **Absent / very low**, **Reduced**,
**Increased**, **Normal**, **Variable / mixed**, **Functional / subset
abnormality**, **Not assessed**, and **Descriptive finding**. A phrase such as
"normal B-cell numbers, low switched-memory B cells" receives both Normal and
abnormal-subset tags. Other affected cells receive broad cell/tissue-group
tags such as NK/innate lymphoid, myeloid/phagocyte, dendritic cell, stem or
progenitor, epithelial/skin, or CNS/microglia.

These tags are navigation aids, not new biological assertions. They are
generated deterministically by `local_service/gene_knowledge.py`; the exact
IUIS text is always displayed beneath them, and a blank source cell remains
**Not reported** rather than being interpreted as normal.

## Reproducible public build

For a normal public-resource refresh:

```bash
bash scripts/update_gene_knowledge.sh
```

To build from already downloaded inputs:

```bash
PYTHONPATH=. python3 scripts/build_gene_knowledge.py \
  --hgnc /path/to/hgnc_complete_set.txt \
  --iuis webui/public/bundled-data/iuis_2024_classification.tsv \
  --clingen-validity /path/to/clingen_gene_validity.csv \
  --clingen-dosage /path/to/ClinGen_gene_curation_list_GRCh38.tsv \
  --output /path/to/gene_knowledge_public.sqlite3 \
  --manifest /path/to/gene_knowledge_manifest.json \
  --release-date YYYY-MM-DD
```

The workbench UI's **Check and update now** action writes an updated public
database under Annotation dataset storage. It does not overwrite the bundled
fallback, and it never includes OMIM.
