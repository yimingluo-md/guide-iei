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

Opening a variant adds a **Carriers** panel to the evidence: each carrying
individual with genotype, depth, genotype quality, allele depths, and
allele balance. The question this chapter exists for — *which of my 88
patients carry this candidate?* — is answered there, one click deep.

Two capabilities are deliberately absent in cohort mode, because they are
meaningless across a cohort: trio/family analysis and the per-patient
phenotype tab (both remain available when reviewing an individual or a
small family, where rows are per-sample as before — files with 2–15
samples keep the family-oriented behavior unchanged).

**Routes into cohort review:**

- **Import → Review annotated VCF → Whole genome scope** for a large
  cohort file: the local service prefilters and compacts it first
  (population frequency, retention criteria), and the prepared result
  opens in cohort mode. This is the recommended route for a fresh cohort
  file, because the prefilter is what keeps the carrier volume reviewable.
- **Sample Library → Select all → Open combined review**: reopens a
  stored cohort in cohort review mode at any time.

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
this gene?* or *who carries this exact variant?* — across every file ever
indexed, not only one import. Index the annotated cohort VCF once
(Cohort search → import; records live in a local database), then query by
gene, variant, or rsID. Matched findings can be sent into the review
workspace, where the service restores each record's complete evidence.

When a new gene–disease association is published, this is the two-minute
check across the entire collection — without touching the original files.

## Bounds worth knowing

- **Carrier volume.** Cohort review stores one entry per carrier per
  variant. Rare-variant sets (the prepared import) keep this small; an
  *unfiltered* cohort, where common variants are carried by nearly
  everyone, can exceed the browser's capacity. The import refuses beyond
  ~3 million carrier entries with directions to apply the
  population-frequency prefilter — a named limit rather than a frozen tab.
- **No cohort allele frequencies.** The software deliberately does not
  compute them: callability, capture, and retention profiles are not
  comparable across heterogeneous imports. Positive carrier findings are
  meaningful; absence from an index is not evidence of absence.
- **Annotation granularity.** Cohort rows carry the same per-transcript
  annotation as any review; carrier evidence is genotype-level. Full
  sample FORMAT evidence for any record is restored on demand when opened.

Technical reference: [The review workbench](../REVIEW_WORKBENCH.md).

Next: [Reading a variant page](09-reading-a-variant.md)
