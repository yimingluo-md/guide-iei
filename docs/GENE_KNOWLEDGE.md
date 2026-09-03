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

OMIM data are **not included** in the source repository, public bundle, or
release artifacts. A user with appropriate OMIM data access can open **Import
& QC**, choose **Set up annotation datasets**, and use the **OMIM** card under
**Optional add-ons**, directly after CADD non-coding and LoGoFunc. Paste the
complete data-account email or the four authorized download links there.
GUIDE-IEI recognizes direct OMIM links and Outlook Safe Links, downloads the
following files into private temporary storage, validates them, and creates a
local private index:

- `mim2gene.txt`
- `mimTitles.txt`
- `genemap2.txt`
- `morbidmap.txt`

The pasted text and credential-bearing URLs remain in memory only. They are not
placed in command arguments, logs, configuration, job history, or index
metadata. Temporary raw files are removed after the attempt; the derived index
and filename-keyed checksums remain under the selected Sample Library & Cohort
storage root. Users who already downloaded the files can instead select their
containing folder; those user-managed source files are not changed.

If the private index is absent, the UI says **OMIM dataset not installed**. It
must not imply that a gene lacks an OMIM association. Reinstalling replaces the
private index atomically only after all four schemas, record counts, and SQLite
integrity have been validated.

## GenIA registration and component installation

[GenIA](https://geniadb.org/) is an optional registered-user source described
in its [published paper](https://doi.org/10.1016/j.jaci.2023.11.022). No GenIA
credentials, download URLs, data, or source files are bundled with or retained
by GUIDE-IEI. The user selects one or more exports under **Import &
QC → Set up annotation datasets → User action needed → GenIA**. The installer
detects each component by its schema rather than its filename and creates a
private derived SQLite database in Annotation datasets storage. It records
per-component source filename, checksum, schema fingerprint, row count, and
installation time, but does not copy the selected source files.

Each of the five components can be installed alone or in any subset:

- the **GEI gene–disease list** supplies gene–disease relationships and the
  source's curated, ongoing, not-curated, or unknown status;
- the **GenIA disease catalog** supplies disease identifiers,
  cross-references, and IEI markers. When installed without the GEI list,
  catalog records explicitly marked `IEI=Y` can still appear as relationships,
  with curation status left unknown;
- **disease–phenotype associations** supply gene-, disease-, and HPO-linked
  phenotype observations and source frequencies. They remain usable without
  the other components and can form their own relationship rows;
- the **GenIA phenotype vocabulary** enriches installed phenotype observations
  with definitions, alternate terms, and hierarchy. On its own it does not
  assert a gene–disease relationship; and
- the **GenIA GRCh38 variant export** supports separate exact-allele evidence
  in Variant Review and during annotation. It is not required for the gene
  knowledge features above, and its downloaded CSI index is not required.

When compatible components coexist, GUIDE-IEI joins them conservatively by
their supplied disease and gene identifiers or unambiguous source keys. The
**GenIA GEI gene** filter is populated only from the GEI gene–disease list.
Disease-catalog, disease–phenotype, and phenotype-vocabulary rows do not add
genes to that filter. Updating a subset
replaces only those selected components and
preserves omitted installed components. The new database is published only
after all selected imports and an integrity check succeed, so a malformed or
misidentified source leaves the prior installation unchanged.

If the existing derived index itself is unreadable, the installer cannot copy
omitted components from it. Select all five exports for a complete recovery,
or select an available subset, then explicitly confirm **Replace the unreadable derived GenIA index**; the
replacement then contains only that subset, and omitted gene, phenotype, or
variant capabilities are removed until their exports are installed again.

## Identity and assertion handling

HGNC is the identity authority. Lookups prefer stable HGNC/Ensembl identifiers
or an approved symbol, then use an alias only when that alias maps
unambiguously to one approved gene. Every IUIS, ClinGen, OMIM, and GenIA assertion is
retained as a separate source row; the software does not collapse several
diseases, inheritance modes, or mechanisms into a single synthetic label.

The Gene review warns that gene-level association, dosage, and constraint
evidence do not establish the pathogenicity or disease relevance of the
selected variant. ClinGen and GenIA exact-allele evidence remain separate from
gene-level knowledge; neither is inferred from a selected transcript.

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
