---
title: "The review workbench"
parent: Reference
nav_order: 4
---

# The review workbench

[Manual home](index.md)

Start the integrated workstation application with:

```bash
bash scripts/start_workbench.sh
```

This starts the loopback-only annotation queue at `127.0.0.1:43117` and the
review UI at `127.0.0.1:3000`. The application lands on **Import VCF**. Choose
**Run VEP first** (the default) or **Review annotated VCF**. The VEP route
accepts dropped `.vcf` / `.vcf.gz` files or a selected folder, then opens a
second screen for parameters and dataset readiness. See `webui/README.md` for
the workflow and WSL2 instructions.

**Choosing a path by file shape.** Exome-scope review parses the VCF in
the browser — appropriate for single-patient files, where it keeps the
entire workflow interactive. A large single-patient file (over ~100 MB
compressed) belongs in the **Whole genome** analysis scope, which prepares
it on the local service. A **cohort-scale file (16+ samples)** belongs in
**Cohort search**, whatever its size: the review workspace opens complete
files for one patient or a small family, and cohort-wide genotype evidence
exceeds browser memory no matter which path prepared the file. Cohort-scale
files are refused at review intake with directions to the cohort workflow.

## Sample Library

The persistent **Sample Library** is the source of truth for retained
workbench imports. **Keep in Sample Library** is enabled by default after
review intake; **Include qualifying variants in Cohort Search** is also
enabled by default. Choose **Review once** when no managed copy or cohort
entry should remain. The library uses the hierarchy Individual →
sample/specimen → genomic dataset/import version → genotype observations, so
one person may have WES, WGS, rerun, or multiple-specimen datasets without
duplicating phenotype data. After import, each VCF sample can be linked to an
existing individual, used to create a new individual, or left as phenotype
unavailable.

Managed review VCFs and indexes are content-addressed under
`~/.iei-variant-review/sample-library/files/`, so identical file content is
not stored twice. Each dataset records the original name/path/checksum when
the path remains available; WES/WGS and Compact/Full scope; QC and WGS
prefilter settings; retention routes; installed annotation/resource versions;
source/retained record counts; timestamps; and a hash of the complete import
settings. The **Sample Library** page reopens a review, edits its sample
label, connects phenotype records, rebuilds its cohort entry, or removes the
managed dataset without deleting an external original VCF.

See [Sample Library & storage](SAMPLE_LIBRARY_AND_STORAGE.md) for the full
model.

## Cohort search

The workbench provides a local **genotype-first cohort search** derived from
the library. Add directories of annotated single- or multi-sample VCFs once,
then query every carrier of an exact variant/rsID or carriers of qualifying
variants in a gene. The indexed variant, annotation, and non-reference
genotype records stay in `~/.iei-variant-review/cohort.sqlite3`. Directly
indexed external VCFs remain in place; library-derived cohort entries can be
rebuilt from their managed review VCF.

Results are grouped as one row per unique allele. Clicking a variant opens its
carrier list plus stored annotation, transcript, genotype, source-profile, and
coordinate details. When matched findings are sent into the main Review
workspace, the service uses tabix to retrieve each exact record from the
indexed prepared VCF and restores its complete populated INFO, VEP CSQ, and
sample FORMAT evidence on demand. This keeps the SQLite index compact while
retaining access to population frequencies and any other annotations present
in the source. Carrier checkboxes can instead load selected individuals'
complete stored review sets, using each file's original Full or Compact WGS
import profile. Complete-set browser loads are limited to 50 sample entries
and 200,000 stored carrier observations; very large full-WGS selections
remain searchable but must be reviewed as matched findings or re-indexed with
the Compact WGS profile. If an indexed source has moved or been deleted,
matched-finding Review reports the source problem and uses the compact SQLite
fields for that carrier instead. The sample manager removes exact sample/file
entries, cascades their genotype rows, and reclaims variants with no remaining
carriers. Reimporting the source VCF restores a removed sample.

Cohort intake validates an existing `.tbi`/`.csi` for coordinate-sorted BGZF
VCFs. A missing index, ordinary gzip stream, uncompressed VCF, or unsorted VCF
is automatically converted to a sorted BGZF working copy under
`~/.iei-variant-review/cohort-vcf-cache/` and indexed there; the source file
is never modified. Prepared copies are fingerprinted by source path, size, and
modification time and reused on later forced imports. Indexed files are read
by four chromosome-sharded worker processes by default. Each worker writes
batched natural-key records to a disposable staging database, after which one
transaction merges the stages into the cohort database. Set
`IEI_COHORT_INDEX_READERS=1` for serial troubleshooting or another positive
integer to tune the reader count. When native `bcftools`/`tabix` are absent,
the workbench uses the configured Docker/Podman image; if neither backend is
available, or a discovered runtime cannot complete preparation, it reports a
warning and retains the serial staged-import fallback.

Every cohort file has a human-readable profile label plus the complete
settings hash. Carrier results show the contributing WES/WGS and Compact/Full
profiles, and searches can be restricted by assay or exact profile. A positive
carrier finding remains useful across heterogeneous profiles; absence from a
candidate index is **not** interpreted as evidence that an individual lacks
the variant. The software deliberately does not calculate cohort allele
frequencies because callability, capture, and candidate-retention profiles may
not be comparable.

## Whole-genome review profiles

**Compact WGS is the default workstation profile.** It runs the same
four-reader candidate filter before SQLite staging. The default requires
`gnomAD popmax <= 0.01` or an unavailable per-variant value, then retains the
union of coding/essential-splice, qualifying SpliceAI, qualifying promoterAI,
and ENCODE SCREEN cCRE-overlap routes. **Full WGS** is an advanced option that
indexes every PASS/unfiltered carrier call from the original VCF. It supports exhaustive
exact searches within those indexed calls but can use roughly 10 GB of SQLite
space for one genome, so it is not recommended for routine workstation use.
Users may replace the cCRE route with all noncoding regions or no additional
noncoding regions. Missing score values do not satisfy a score route. The
database records the Full/Compact profile and Exome/Whole-genome source scope
separately, displays mixed provenance, and warns that an absent noncoding
result is not exhaustive when any compact source is present. The scope label
also determines whether regulatory review is available when a stored
individual is reopened. Legacy Compact entries migrate to Whole genome; older
Full entries are marked `scope not recorded` until refreshed. Import progress
includes prefilter scanned and retained counts as well as staged PASS and
carrier counts.

### Annotated WGS review intake

Annotated WGS review uses a separate workstation-safe intake path. Four
chromosome readers conservatively prefilter the indexed VCF before the reduced
result is opened in the browser. The Import page polls this background task
and shows live preparation, chromosome-filtering, merge, compression, and
indexing progress with scanned/retained record counts. Every PASS variant
overlapping the configured coding+splice BED defines the
exonic/essential-splice route. Defaults are gnomAD popmax `<= 0.01` (or
unavailable), SpliceAI `>= 0.5`, absolute promoterAI score `>= 0.8`, and
SCREEN Registry V4 cCRE overlap. PASS/QC and population frequency are global
requirements; exonic/essential-splice, SpliceAI, promoterAI, and the selected
noncoding region mode are OR routes. The noncoding region control offers cCRE
overlap (default), all noncoding regions, or no additional noncoding regions.
The last option still retains qualifying SpliceAI and promoterAI variants.
CADD, other predictors, and gene lists are not used at import time. The
indexed input and filtered result are fingerprinted and reused when the source
and settings are unchanged. The review copy preserves every retained site but
compacts redundant transcript annotations to MANE, then VEP PICK, then one
fallback per allele/gene. The browser reads BGZF incrementally instead of
materializing the complete decompressed WGS VCF as one large string.

Precomputed SpliceAI MANE and promoterAI score tables may contain SNVs but no
matching indel. To avoid silently discarding these unscored alleles, the
compact WGS import also retains a sequence-resolved intronic indel with no
SpliceAI score and a sequence-resolved promoter indel with no promoterAI
score. Promoter overlap uses the installed Illumina TSS +/- 500 transcript
map. These safety routes still require PASS/QC and the population-frequency
rule, work even when the additional noncoding-region mode is `none`, and add
the allele-specific `IEI_UNSCORED_INDEL` INFO flag for review. A populated
score below its threshold does not use this exception. The corresponding
predictor field must be declared in the VCF schema, so a wholly absent
annotation dataset does not activate the missing-indel route.

For multi-gigabyte inputs, enter the existing absolute workstation path in the
WGS review panel to avoid making an additional browser-upload copy.

## Regulatory evidence (whole-genome imports)

For whole-genome imports, the variant detail screen queries the installed
SCREEN Registry directly for every opened GRCh38 allele. It reports cCRE
overlap or an explicit verified non-overlap. A genomic region can
simultaneously have a transcript-specific coding consequence and be a
regulatory element. The regulatory element may act on the same gene, another
gene, or multiple genes; this positional overlap is not independent
pathogenicity evidence. Each overlapping cCRE includes its EH38E accession,
overall class, and the complete release-matched Ensembl gene-TSS context
within +/-500 kb with strand-aware distance. The review table shows
protein-coding genes by default and offers an **Include non-protein-coding
genes** control without discarding them from the underlying result. This is
proximity context only: the nearest or VEP-annotated gene is not necessarily
regulated by the cCRE, and the software does not present these genes as
predicted targets.

The separately prepared categorical tissue aggregates and ontology-selected
immune/hematopoietic biosamples are documented in
[SCREEN tissue & immune data](SCREEN_TISSUE_IMMUNE_DATA.md). This layer
intentionally omits assay Z scores and preserves evidence-completeness
metadata so unavailable assays are never interpreted as negative results. Its
reproducible AlphaGenome-style curation uses exact Cell Ontology contexts,
ENCODE audits, baseline-state selection, and one categorical vote per donor;
the complete 448-profile source matrix remains available and unchanged. The
prepared baseline layer also records assay-specific capability, global
distinct donor counts, Cell Ontology parent/child dependence, tolerated audit
warnings, and IEI-relevant lineage gaps. Activated/stimulated profiles are not
exposed as a companion view at this stage. When the optional prepared context
manifest is configured, the bottom of the whole-genome variant overview shows
a compact tissue/immune summary and the variant-scoped **Regulatory evidence**
workspace shows categorical tissue and donor-aware immune evidence. The
regulatory tab and controls are hidden for exome imports. Mixed class
families, H3K4me3-associated calls, accessibility-only calls, and
classification-unavailable contexts remain distinct. The built-in display sets
include Immune core, Immune all, and Select all; users may also save multiple
named context sets. A simple whole-genome **Immune context** variant-list
filter requires positive evidence in any immune-related tissue or curated
immune-cell context. Advanced named sets likewise qualify a variant when any
selected context is positive; missing or negative evidence never acts as an
automatic exclusion. SCREEN observations, future regulatory-to-gene links, and
future sequence-model predictions remain separate modules rather than a
combined regulatory score.

## Gene knowledge

The workbench includes compact, versioned gnomAD v4.1.1 constraint, HGNC,
IUIS October 2024, and ClinGen gene-disease-validity/dosage resources. The
variant screen provides source-specific filters and a dedicated **Gene** tab;
these resources are joined during review and do not need to be added to the
VCF. Licensed OMIM files are never shipped or downloaded automatically, but
can be indexed from a user-selected local folder under **Gene knowledge**. See
[Bundled workbench references](BUNDLED_WORKBENCH_REFERENCES.md) and
[Gene knowledge](GENE_KNOWLEDGE.md).

## Phenotype records

The workbench stores individual demographics and plain-text phenotypes
separately from sequencing samples. Records may be entered manually or
imported from mapped CSV, TSV, or XLSX columns, with reusable mapping profiles
and sample-link validation. Reported race and reported ethnicity are kept
separate; neither is treated as genetic ancestry. HPO mapping and
phenotype-based prioritization are not performed in this release. Variant
Review has a dedicated **Phenotype** tab that first uses the stable Sample
Library dataset-to-individual link and retains VCF-sample lookup for legacy or
directly indexed data; a missing link is shown explicitly and can be added
from the phenotype manager. See [Phenotype input](PHENOTYPE_INPUT.md).

## Trio analysis

The workbench supports session-local **trio analysis**. Upload a standard PED
file or manually assign proband, mother, and father to identify
confidence-tiered de novo candidates and compound-heterozygous pairs
classified by parental origin or available phase. A jointly genotyped
multi-sample VCF is preferred; absence from a separate parental VCF is never
interpreted as a confident `0/0` call. See [Trio analysis](TRIO_ANALYSIS.md).

## Storage

The **Storage** page reports database, managed-library, upload, cohort-cache,
WGS-review-cache, and log footprints. It can remove only rebuildable temporary
or cache files and can explicitly compact SQLite to return unused pages to the
filesystem; compaction never deletes samples or variants. As a planning guide,
compact candidate WGS storage is typically about 30–120 MB per sample (roughly
3–12 GB for 100 WGS or 15–60 GB for 500), excluding external original
annotated VCFs. Full-WGS cohort indexing can instead reach hundreds of GB for
100 samples and terabyte scale for several hundred.

Storage can be configured from that page without editing YAML: **Annotation
datasets**, **Sample Library & Cohort**, and an optional **Temporary
workspace** can use separate local SSD locations. A safe **Copy existing
data** migration checks free space, verifies copied files, preserves the
original location, and requires a restart before the new root becomes active.
The application never silently creates an empty default cohort database when a
configured external library drive is disconnected. See
[Sample Library & storage](SAMPLE_LIBRARY_AND_STORAGE.md#configurable-workstation-locations).

Next: [Understanding the output](OUTPUT.md)
