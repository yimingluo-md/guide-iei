# SCREEN tissue and immune cCRE data

This preparation is for later tissue- and cell-specific variant review. It is
separate from the smaller Registry V4 BED used for the whole-genome import
region filter. It retains categorical cCRE calls only; per-assay Z scores are
not downloaded into the final dataset.

## Prepared scope

The release is pinned to **SCREEN Registry V4, GRCh38** and contains two
complementary layers:

- 38 organ/tissue aggregate classifications. The `.noccl` source is used so
  cancer cell lines do not contribute to these aggregate calls.
- 448 individual immune/hematopoietic biosamples selected from the official
  Registry V4 experiment lists using ENCODE ontology metadata, rather than
  fragile text matching on biosample names.

Source endpoints are the [SCREEN downloads page](https://screen.wenglab.org/downloads),
the official [Registry V4 GRCh38 cCRE table](https://downloads.wenglab.org/Registry-V4/GRCh38-cCREs.bed),
the Registry V4 [experiment lists](https://users.moore-lab.org/ENCODE-cCREs/Pipeline-Input-Files/hg38-Experiment-Lists.tar.gz),
and ENCODE's public experiment metadata API. The generated selection and
download manifests retain the resolved source URLs and checksums.

The immune selection includes a biosample if ENCODE identifies it as a
hematopoietic cell/leukocyte, as part of the immune or hematopoietic system, or
as an immune tissue (blood, bone marrow, spleen, thymus, lymph node, or
lymphoid tissue). Metadata retain sample type, ontology term, lineage, life
stage, sex, treatments, diseases, donor identifier, and available assays so a
later interface can group and filter the collection without changing the
prepared data.

Broad lineage assignment uses exact `is_a` ancestry from the pinned Cell
Ontology whenever the biosample has a `CL:` identifier. Registry profiles with
non-CL identifiers (including NTR and EFO terms) use an explicitly recorded
fallback over ontology-derived names and slims with word-boundary-safe regular
expressions. This prevents substring collisions such as `mast cell` or `club
cell` being interpreted as `T cell`, and recognizes `NK cell` at a string
boundary. The assignment method is retained for every source profile.

Registry V4 currently provides these immune evidence tiers:

| Tier | Biosamples | Interpretation |
|---|---:|---|
| Full classification | 16 | DNase, H3K4me3, H3K27ac, and CTCF are available |
| Partial classification | 91 | DNase plus only some classifier assays are available |
| Accessibility only | 341 | DNase is available but the histone/CTCF classifier panel is not |

A missing assay is **unavailable**, not negative. Accessibility-only evidence
can support open chromatin but cannot be interpreted as a complete
promoter/enhancer classification.

## AlphaGenome-style immune-cell curation

The 448 profiles above are intentionally broad source data, not the default
immune-cell display. A second, small curation layer uses the data-engineering
principles described for [AlphaGenome](https://www.nature.com/articles/s41586-025-10014-0):
standardized ontology identifiers, explicit experiment audits, baseline-state
selection, and biological-replicate grouping. It does **not** use AlphaGenome
predictions and does not claim to reproduce AlphaGenome's quantitative track
processing.

The versioned policy is
`config/screen/alphagenome_encode_audit_policy.json`. Its source article,
supplement URL, retrieval date, and SHA-256 are recorded in that file. For each
of the 664 ENCODE experiments contributing to the 448 SCREEN profiles, the
preparation retrieves embedded experiment, biosample, donor, perturbation, and
audit metadata. It applies these rules:

1. Exclude every ENCODE `ERROR` audit.
2. Exclude the 11 critical `NOT_COMPLIANT` audit categories specified by the
   AlphaGenome supplementary methods. Warnings and other non-critical
   `NOT_COMPLIANT` categories do not exclude the experiment. They remain
   visible as per-experiment, per-profile, and per-context quality flags.
3. Do not apply AlphaGenome's possible FRiP rescue. The categorical SCREEN
   downloads do not provide one harmonized FRiP value on which to reproduce
   that rule defensibly.
4. Require a released experiment and an exact Cell Ontology (`CL:`) term.
5. For the default baseline view, require a primary, untreated, non-disease,
   unmodified, unsynchronized cell profile with a consistent donor.
6. Restrict the default view to leukocytes and hematopoietic progenitors.
   Immune-adjacent profiles such as marrow stroma or lymphatic endothelium stay
   in the complete source layer but are not silently labeled as immune cells.

The real Registry V4 preparation on 2026-08-01 EDT / 2026-08-02 UTC produced
**28 exact Cell Ontology contexts and 166 donor representatives**. The retained evidence is 1
full-classification, 48 partial-classification, and 117 accessibility-only
profiles. This is why a full-classification-only policy would be
scientifically unusable: it would reduce the baseline view to one donor. The
complete 448-profile matrix remains unchanged, and every excluded profile has
one or more machine-readable reasons. Exclusion counts overlap because a
profile can fail more than one rule.

Those 166 context-level representatives come from **97 globally distinct
donors**. Twenty-three donors occur in more than one context, and one donor
occurs in nine. The hierarchy is resolved with the pinned official Cell
Ontology basic release `v2026-06-08` (SHA-256 recorded in the context
manifest). In that release, 21 of the 28 selected contexts descend from
another selected context; there are 41 transitive parent/child pairs, 20 of
which share at least one donor. Parent rows are marked as summary contexts and
must not be read as independent replication of child rows.

The audit flags are deliberately not converted into new exclusions after the
fact. Among retained donor representatives, four profiles carry a tolerated
`NOT_COMPLIANT` category (three `insufficient read depth`, one `insufficient
read length`); ordinary ENCODE `WARNING` categories are also surfaced. This
preserves the pinned policy while making its practical behavior visible.

### Donor-aware categorical consensus

Contexts are grouped by exact Cell Ontology CURIE, not by free-text names. A
donor contributes at most one vote to a context; if duplicate profiles ever
exist for the same donor and ontology, the deterministic preference is full,
then partial, then accessibility-only evidence, followed by stable matrix
index. Life stage, sex, evidence tier, and the selected profile are preserved.

Assay capability is reported per context, not inferred only from the broad
full/partial/accessibility tier. Counts are provided separately for DNase,
H3K4me3 (promoter classification), H3K27ac (enhancer classification), CTCF,
any specific classifier, and the complete four-assay panel. Eleven contexts
have no donor with any histone/CTCF classifier assay; only 14 have at least two
such donors. A cCRE with no activity in one of those 11 contexts is labeled
`no_classifying_evidence_in_context`, rather than a plain `not_detected` that
could be mistaken for a fully assayed negative call.

SCREEN classes are categorical and therefore are **never averaged**. For a
cCRE, the software reports exact donor counts for PLS, pELS, dELS, CA variants,
CTCF, TF, and Low-DNase. pELS and dELS can jointly support an enhancer-like
consensus, while promoter-versus-enhancer or other specific-class disagreement
is reported as discordance. Accessibility-only donors can support accessible
chromatin but never establish promoter or enhancer identity. `not_detected`
means not detected in these represented SCREEN profiles; it does not mean the
element is universally inactive.

Cross-context output reports both context/donor observations and the number of
globally distinct detected donors. It also lists detected parent/child pairs
and their shared-donor counts. This makes dependence visible without
discarding useful broad contexts such as generic T cell.

### IEI coverage limits

The baseline layer is not a comprehensive immune-cell atlas. Registry V4 has
no selected source profiles for neutrophil/granulocyte, plasma-cell, mast-cell,
or erythroid/megakaryocyte contexts. Its single dendritic-cell source profile
fails the pinned experiment-audit policy, so dendritic cells are also absent
from the baseline view. This is especially important for neutrophil and
antibody-deficiency phenotypes: absence of a cell-specific cCRE call is
absence of data, not evidence of inactivity. Blood, bone marrow, spleen,
thymus, and lymphoid-tissue aggregates provide broader tissue-level context
but do not replace the missing cell types.

Activated and stimulated profiles remain in the immutable 448-profile source
matrix, but this preparation intentionally does **not** build an activated
companion view. Only the conservative baseline-primary-cell view is exposed
through the curated context layer for now.

The SCREEN downloads page also lists gallbladder, nose, parathyroid gland, and
urinary bladder aggregates, but all four source URLs returned HTTP 404 when
checked on 2026-08-01 EDT / 2026-08-02 UTC. For parathyroid, both SCREEN's
published misspelling (`paraythroid_gland`) and the corrected
`parathyroid_gland` URL were tested. These unavailable files are recorded in
the selection manifest and are not represented by misleading all-zero matrix
columns.

## Categorical encoding

Each byte is one SCREEN categorical call:

| Code | Display | Meaning |
|---:|---|---|
| 0 | inactive | Low-DNase/not active in this aggregate or biosample |
| 1 | PLS | Promoter-like signature |
| 2 | pELS | Proximal enhancer-like signature |
| 3 | dELS | Distal enhancer-like signature |
| 4 | CA-H3K4me3 | Chromatin accessibility with H3K4me3 |
| 5 | CA-CTCF | Chromatin accessibility with CTCF |
| 6 | CA-TF | Chromatin accessibility with TF binding |
| 7 | CA | Chromatin accessibility without a more specific signature |
| 8 | TF | TF binding without chromatin accessibility |

`CA-only` in the source is normalized to the SCREEN display label `CA`, while
the source vocabulary and checksums remain documented. Low-DNase rows are read
and validated during preparation but compacted to byte zero rather than
retained as large text tables.

The encoding vocabulary is complete even when a class is absent from a given
release. In the current immune and tissue matrices, code 8 (`TF`) has zero
observed calls; provenance records observed and supported-but-unobserved labels
separately.

## Output layout

The prepared directory contains:

- `screen.registry-v4.catalog.sqlite3`: cCRE coordinates and R-tree interval
  index, class-code definitions, and tissue/immune biosample metadata.
- `screen.registry-v4.tissues.u8`: cCRE-major unsigned-byte matrix with shape
  `2,348,854 x 38`.
- `screen.registry-v4.immune.u8`: cCRE-major unsigned-byte matrix with shape
  `2,348,854 x 448`.
- `screen.registry-v4.prepared.json`: dimensions, checksums, byte sizes,
  per-column class summaries, and scientific interpretation notes.
- `screen.registry-v4.immune-contexts.sqlite3`: all 448 profile decisions,
  exact ontology contexts, donor representatives, evidence tiers, and
  machine-readable exclusion reasons.
- `screen.registry-v4.immune-contexts.json`: curation provenance, source and
  artifact checksums, assay capability, ontology hierarchy, global donor
  dependence, lineage coverage, audit-warning counts, and context summaries.
- `screen.registry-v4.immune-context-members.tsv`: human-readable audit table
  of each retained donor vote.

The resumable ENCODE metadata cache is stored at
`source/encode-experiment-curation.json`. It is a provenance input, not a file
that must be loaded during variant review. The curation artifacts add less
than 0.5 MB; they reuse the existing cCRE-major immune matrix rather than
creating another gigabyte-scale matrix.

Because rows are cCRE-major, one local interval lookup followed by a contiguous
matrix slice retrieves every tissue or immune call for a cCRE without loading
the complete dataset into memory.

## Variant-review interface and filtering

The local workbench uses the prepared context manifest configured at
`wgs_review.screen_context.manifest`. `IEI_SCREEN_CONTEXT_MANIFEST` can
override that path without editing YAML. The smaller Registry V4 BED remains
the whole-genome import/prefilter resource; the matrices are read only when a
variant is reviewed or the user explicitly enables a context filter.

The distributed configuration intentionally leaves the manifest path empty so
it never embeds a developer-specific filesystem location. A local installation
can either set the YAML value or launch the workbench with, for example:

```bash
IEI_SCREEN_CONTEXT_MANIFEST="/path/to/SCREEN/Registry-V4/prepared/screen.registry-v4.immune-contexts.json" \
  bash scripts/start_workbench.sh
```

For a **whole-genome import**, the bottom of the ordinary variant overview
contains a compact SCREEN summary. **Regulatory evidence** opens a
variant-scoped workspace with three deliberately separate modules. These
controls and the workspace are intentionally hidden for exome imports:

1. SCREEN observed tissue/cell evidence (implemented).
2. Regulatory element–gene links such as R2G (to be developed).
3. Gene-specific variant-effect predictions such as AlphaGenome (to be
   developed).

No combined regulatory score is calculated. The Registry class is labeled
the **overall cell-type-agnostic class**. Tissue aggregates and curated
baseline immune contexts are shown underneath it; the closest VEP gene and
the genes in the ±500 kb TSS table remain proximity context, not cCRE targets.
cCRE intervals are defined by chromatin evidence, not by a noncoding-only
rule. A genomic region can simultaneously have a transcript-specific coding
consequence and be a regulatory element. The regulatory element may act on the
same gene, another gene, or multiple genes; the positional overlap itself is
not independent pathogenicity evidence.

The immune display preserves these primary states:

- classified promoter-, enhancer-, CTCF-, or TF-associated evidence;
- `CA-H3K4me3` as **H3K4me3-associated**, not silently called a promoter;
- **mixed classifications** when donor calls support more than one specific
  class family;
- **accessible only** when activity is present without a resolved class;
- **accessible · classification unavailable** when activity is present but
  no donor in the context has a histone/CTCF classifier assay;
- **not detected**, always paired with the context assay-capability badge.

Every immune row displays detected/represented donor counts and “classifier
assays X of Y donors” before the user interprets the state. Exact categorical
donor counts remain available. Nested Cell Ontology contexts are marked
because parent and child rows can share donors and are not independent
replicates.

The default display uses the smaller **Immune core** context set. **Immune
all** adds every curated immune context, including nested Cell Ontology
contexts, while retaining the immune-related tissue aggregates. **Select all**
adds every non-immune tissue aggregate as well. Users can create, update, and
remove multiple named local context sets. Choosing a display set does not
filter the variant list. Filtering is a separate, opt-in control in the
whole-genome results sidebar:

- **Immune context** is the simple filter and means positive evidence in any
  immune-related tissue aggregate or curated immune-cell context;
- only positive tissue/context activity can qualify a variant;
- a variant qualifies when **any** selected context has positive evidence;
- not detected, missing classifiers, and unavailable resources never exclude
  variants unless the user has intentionally enabled a positive-evidence
  filter that the variant fails to satisfy.

Context filtering is performed in bounded local batches against the SQLite
interval index and compact matrices. It does not add fields to VEP CSQ,
rewrite the imported VCF, or send coordinates outside the workstation.

## Reproducible preparation

```bash
bash scripts/prepare_screen_ccre_data.sh /path/to/SCREEN/Registry-V4 6
```

The workflow downloads/resumes official sources, caches ENCODE metadata,
validates cCRE order, row counts, schemas, status vocabulary, and class
vocabulary, builds the baseline immune contexts, then writes all prepared
artifacts with SHA-256 provenance. On macOS, the UCSC `bigBedToBed` binary may
require the `xz` runtime (`brew install xz`).

It also downloads the exact Cell Ontology `v2026-06-08` basic OBO release and
verifies its pinned SHA-256. A small checked-in content-alignment fixture binds
three exact cCRE catalog rows and their complete matrix-row hashes to named
immune and tissue columns. This is a structural regression test—not a claim
that an intergenic locus is a universal biological negative control—and it
fails if row order, column labels, or categorical content drift.

The same validation recomputes the categorical histogram of every final matrix
column and compares it with the corresponding pre-transpose build summary. It
also requires those histograms to be unique before treating them as column
identities. The current release passes for all 448 immune and 38 tissue
columns. Marker-gene behavior may be reviewed as a qualitative biological
sanity check, but fixed marker thresholds are intentionally not correctness
gates: cCRE class is not gene expression, small cell-type denominators are
unstable, and a legitimate source-release update can change those percentages.

To refresh only the ENCODE experiment metadata and immune curation after the
large matrices already exist:

```bash
bash scripts/prepare_screen_immune_contexts.sh /path/to/SCREEN/Registry-V4 8
```

The metadata cache is updated in batches and is safe to resume. The script
validates the selection, policy, ontology checksum, matrix dimensions, byte
size, full matrix SHA-256, and pinned content alignment before replacing the
small context artifacts.

For a reproducible command-line inspection of one cCRE:

```bash
python3 pipeline/screen_immune_curation.py summarize-ccre \
  --context-manifest /path/to/SCREEN/Registry-V4/prepared/screen.registry-v4.immune-contexts.json \
  --ccre EH38E2776516
```

By default the summary omits contexts with no detected activity; add
`--include-inactive` for the complete 28-context result. The same command
accepts `--row-index` instead of `--ccre`.

### Reproducibility boundary

Registry V4 files and their checksums pin the categorical source release. The
ENCODE REST response is a dated metadata snapshot and can change as audits are
updated; its complete compact response and checksum are retained so an old
preparation remains auditable. Re-running later intentionally creates a new
snapshot. This dataset is bulk-profile evidence, not single-cell evidence, and
it does not prove that a cCRE regulates a nearby gene or that absence in the
represented cells is biologically universal.

This dataset does not establish which gene a cCRE regulates. Gene proximity,
including the nearest gene, must remain explicitly labeled as genomic context
rather than target-gene evidence.
