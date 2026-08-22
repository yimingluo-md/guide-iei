---
title: "Cohort analysis: review and search"
parent: User Guide
nav_order: 8
---

# Cohort analysis: review and search

A jointly-called cohort VCF — tens of samples in one file — supports two
complementary ways of working, and GUIDE-IEI provides both: **cohort
review**, where the variant list is examined and filtered directly and each
variant reports its carriers; and **cohort search**, where the indexed
cohort answers genotype-first questions across every stored case.

## Cohort review: the variant list, with carriers

Importing a multi-sample VCF (16 or more samples) switches the review
workspace into **cohort review mode** automatically. The change is in what
a row represents: instead of one row per variant per patient, the table
shows **one row per variant**, and the genotype column reports how many
individuals carry it (for example, `3/88 carry`). Every familiar filter
applies unchanged — population frequency, IMPACT, gene lists, predictors,
ClinVar — so the screening loop is the same one used for a single exome.
The **One row per variant** display (on by default) additionally collapses
multiple transcript or gene consequences of the same variant — MANE Select
plus MANE Plus Clinical, or overlapping genes — into a single row with a
+N indicator ([Reading a variant page](09-reading-a-variant.md)).

Opening a variant adds a **Carriers** panel to the evidence: each carrying
individual with genotype, depth, genotype quality, allele depths, and
allele balance. The question this chapter exists for — *which of my 88
patients carry this candidate?* — is answered there, one click deep.

Two capabilities are deliberately absent in cohort mode, because they are
meaningless across a cohort: trio/family analysis and the per-patient
phenotype tab (both remain available when reviewing an individual or a
small family, where rows are per-sample as before — files with 2–15
samples keep the family-oriented behavior unchanged).

**Two forms of carrier count, by provenance.** A single jointly-called
file shows `3/88 carry`: every individual was genotyped at every site, so
non-carriers are confirmed reference and the denominator is earned.
**Separately-called aggregation** — several files whose samples sum to 16
or more, or a library selection spanning source files — shows `3 carry`
with **no denominator**: an individual whose file has no record at a site
is *not* confirmed reference. The missing denominator is itself the
signal. Aggregated imports also apply the candidate-import popmax
threshold at parse time, warn when the files were annotated against
different ClinVar releases, and refuse duplicate sample names across
files.

**Routes into cohort review:**

- **Import → Review annotated VCF** with either scope. A cohort **exome**
  file (16+ samples) is prepared on the local service automatically under
  the Exome scope: PASS-or-unfiltered records in coding regions, filtered
  by a gnomAD popmax threshold (default ≤ 0.01, set in the **Cohort exome
  candidate import** panel; clear it to keep common variants). A cohort **genome**
  takes the Whole genome scope with its full prefilter (population
  frequency plus the non-coding retention criteria). Either way, the
  prepared result opens in cohort mode, and the prefilter is what keeps
  the carrier volume reviewable.
- **Import → Review annotated VCF** with several exome files at once:
  when the sample columns sum to 16+, the files are aggregated as
  separately called (above).
- **Sample Library → any selection → Open combined review**: a selection
  within one source file keeps joint semantics; a selection spanning
  files — including Select all over a heterogeneous library — opens as a
  separately-called aggregation.

## Reviewing individuals and subsets from a cohort

The Sample Library lists every individual of an imported cohort as its own
dataset. From there:

- **Open review** on one individual opens a normal single-patient review —
  the service projects that person's genotype column and carried variants
  from the stored file.
- **Select several individuals** (checkboxes, or **Select all**) and
  **Open combined review** — for example, three affected members of one
  family within the cohort. With 15 or fewer selected, the review behaves
  like a family file, including trio analysis when applicable; with 16 or
  more, cohort mode applies. Selecting every individual reopens the whole
  cohort.
- **Remove selected** deletes the chosen datasets from the library and
  Cohort Search in one action, after confirmation; original source VCFs
  are never deleted.

## Cohort search: genotype-first questions

Cohort review answers "what variants are here, and who carries each."
**Cohort search** answers the inverse: *who carries qualifying variants in
this gene?* or *who carries this exact variant?* — across every indexed
case, not only one import. Membership is managed entirely through the
**Sample Library**: keep a review with "Include qualifying variants in
Cohort Search" enabled, or use the library's per-dataset and bulk actions
(Add to Cohort Search, Remove selected). The records live in a local
database on this workstation.

Four query forms: an **exact variant** (locus or rsID); **qualifying
variants in one gene**; a **gene list** — paste symbols or insert a saved
list from Gene lists (an IUIS panel, a custom panel), up to 2,000 genes
per query; and a **genomic region** (`chrom:start-end`, up to 5 Mb) for
non-coding questions — who carries anything in this enhancer window, this
promoter, this topological neighborhood. Opening the region tab selects
all IMPACT tiers, because non-coding records are MODIFIER and the coding
default would hide them. The gene and region forms share the qualifying
filters (impact, popmax, predictors, ClinVar). This is the scalable route for large collections:
screening 50 genomes against a panel is one query, and only the matched
findings are ever opened. Matched findings flow into the review
workspace with each record's complete evidence restored.

When a new gene–disease association is published, this is the two-minute
check across the entire collection — without touching the original files.

## Bounds worth knowing

- **Review size.** A browser review stays responsive to roughly 350,000
  rows — routine loads sit far below it (a genome ~40k rows, a genome trio
  ~120k, an 88-sample cohort exome ~87k), and the line is reached around
  8–9 genomes opened together. Beyond it, and beyond ~3 million carrier
  genotypes for unfiltered callsets, the import stops with directions —
  open fewer individuals, or use the gene-list search — rather than
  freezing. Large collections belong in Cohort search, which has no such
  limit.
- **No cohort allele frequencies.** The software deliberately does not
  compute them: callability, capture, and retention profiles are not
  comparable across heterogeneous imports. Positive carrier findings are
  meaningful; absence from an index is not evidence of absence.
- **Annotation granularity.** Cohort rows carry the same per-transcript
  annotation as any review; carrier evidence is genotype-level. Full
  sample FORMAT evidence for any record is restored on demand when opened.

Technical reference: [The review workbench](../REVIEW_WORKBENCH.md).

Next: [Reading a variant page](09-reading-a-variant.md)
