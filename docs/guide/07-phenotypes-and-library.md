---
title: "Phenotypes and the Sample Library"
parent: User Guide
nav_order: 7
---

# Phenotypes and the Sample Library

An analysis session produces two things worth keeping: the genomic dataset
as reviewed, and the clinical context that made the review interpretable.
GUIDE-IEI persists both locally — genomic datasets in the **Sample
Library**, clinical context as **phenotype records** — and connects them
through one organizing principle: *the individual, the specimen, and the
dataset are different things*. One person may have an exome, a later
genome, and a reannotation of either; conflating those layers is how
clinical records get duplicated and contradicted. The library keeps them
distinct: an individual carries the phenotype record, links to one or more
samples, and each sample may carry several genomic datasets — one per
import.

## What is stored at import

After any review intake, **Keep in Sample Library** is on by default (with
**Include qualifying variants in Cohort Search** alongside it); choose
**Review once** when no trace should remain. A kept dataset stores the
managed review VCF — content-addressed, so re-importing identical data
costs no additional space — together with provenance that answers, years
later, *exactly what produced this list*: the original file's name,
location, and checksum; assay scope and import profile; the QC and
prefilter settings; the installed annotation dataset versions; and a hash
of the complete import configuration. Two datasets with different settings
hashes were produced by different rules and are labeled as such wherever
they meet — the comparability question is made visible, not averaged away.

## Reopening a review

**Open review** on any dataset restores the variant list exactly as
imported — no recomputation, no re-annotation, seconds not hours.
Selections of several individuals open together (**Open combined
review**), with the joint-versus-separately-called semantics described in
[Cohort analysis](08-cohort-search.md). Maintenance actions live on each
card: edit the sample label, link a phenotype record, manage its Cohort
Search entry, or remove the dataset — removal never touches the original
VCF, which remains wherever it is stored.

## Phenotype records

Phenotype information is stored on the **individual** and linked to VCF
sample IDs, so a rerun or second assay never requires re-entering the
clinical picture. A record carries sex at birth, ages at evaluation and
onset, present features, pertinent negatives, current diagnosis, a
free-text summary, and notes; only the individual identifier is required.
Two boundaries are deliberate:

- **Reported race and ethnicity are stored as reported, and nothing
  more.** They are never treated as genetic ancestry and never used to
  choose a population-frequency filter — self-report and genetic ancestry
  are different data, and conflating them produces exactly the frequency
  errors discussed in [Reading a variant page](09-reading-a-variant.md).
- **No phenotype-driven prioritization.** This release does not map text
  to HPO terms or re-rank variants by phenotype match. The record informs
  the reviewer reading a variant — it appears on the variant page for any
  linked sample — but it never silently reorders the evidence.

**Entering records.** Single cases go in through **Phenotypes → Manual
entry**. A clinic's existing spreadsheet goes through bulk import (`.csv`,
`.tsv`, `.xlsx`): the importer previews the source rows, suggests column
mappings, and requires confirmation of the individual-ID mapping before
anything is written. Sample-ID matching against the library is exact and
case-sensitive — near-misses are shown as suggestions, never
auto-matched — and unmatched links are retained so they connect
automatically when the corresponding VCF arrives later. Column mappings
can be saved as named profiles for the next batch, and every import
records its source file, checksum, and row-level outcome counts. For
existing individuals, the default merges only nonblank fields; full
replacement and skip are explicit choices.

## Storage as the library grows

The **Storage** page accounts for every byte the software holds — managed
library files, the cohort database, caches, staging, and logs — and its
cleanup removes only what can be rebuilt: uploads, caches, and incomplete
files, never an original VCF and never a managed library dataset.
Compact-profile storage is modest (roughly 3–12 GB per 100 whole genomes;
exomes far less); the separate database compaction step reclaims freed
space when large removals have accumulated. Storage locations, including
placing the library on an external disk, are configurable and documented
in the technical reference.

Technical reference:
[Sample Library & storage](../SAMPLE_LIBRARY_AND_STORAGE.md) and
[Phenotype input](../PHENOTYPE_INPUT.md).

Next: [Cohort analysis: review and search](08-cohort-search.md)
