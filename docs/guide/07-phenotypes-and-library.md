---
title: "Phenotypes and the Sample Library"
parent: User Guide
nav_order: 7
---

# Phenotypes and the Sample Library

A reusable case record has two distinct components: the genomic dataset and
the clinical context in which it is interpreted. GUIDE-IEI stores both locally
while keeping the individual, specimen, and imported dataset separate.

One individual may have several specimens, assays, or reanalyses. The
phenotype record belongs to the individual; samples link the individual to one
or more imported genomic datasets. This structure avoids duplicating or
silently overwriting clinical information.

## What is stored at import

After any review intake, **Keep in Sample Library** is on by default (with
**Include qualifying variants in Cohort Search** alongside it); choose
**Review once** when no trace should remain. For each retained dataset,
GUIDE-IEI records the source file, assay scope, import settings,
annotation-resource versions, and other provenance needed to understand how
the review set was produced. Technical identifiers, checksums, and
configuration hashes remain available under provenance details.

A multi-sample VCF is shown as one callset with an expandable sample list.
Importing the same file again reuses its existing library records. A new
annotation of the same calls becomes the current version while the prior one
remains available under **Previous versions**. If sample names match but the
called variants or genotypes changed, GUIDE-IEI asks whether this is an update
or a separate dataset. Only the current version is placed in Cohort Search.

## Reopening a review

**Open review** on any dataset restores the managed variants without
re-annotation. When the complete transcript expansion would be too large for
the browser, the list uses MANE/PICK/per-gene representatives and restores the
complete transcript table when a variant is opened.
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

The **Storage** page reports the local disk space used by managed datasets,
the cohort database, caches, staging files, and logs. Its cleanup removes only
what can be rebuilt: uploads, caches, and incomplete files, never an original
VCF and never a managed library dataset.
Compact-profile storage is modest (roughly 3–12 GB per 100 whole genomes;
exomes far less); the separate database compaction step reclaims freed
space when large removals have accumulated. Storage locations, including
placing the library on an external disk, are configurable and documented
in the technical reference.

Local storage does not remove the need for institutional safeguards. Users
remain responsible for workstation encryption, access control, backup,
retention, and any applicable privacy requirements.

Technical reference:
[Sample Library & storage](../SAMPLE_LIBRARY_AND_STORAGE.md) and
[Phenotype input](../PHENOTYPE_INPUT.md).

Next: [Cohort analysis: review and search](08-cohort-search.md)
