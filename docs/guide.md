---
title: User Guide
nav_order: 2
has_children: true
has_toc: false
---

# User Guide

Practical guidance for clinician-researchers and wet-lab scientists. The guide assumes
familiarity with clinical genetics — candidate genes, variant classes,
population frequencies — and assumes **no** software or bioinformatics
experience beyond a download and a double-click. Chapters follow the order in
which the work is encountered: orientation, installation, dataset setup,
then the first analysis.

This website publishes the curated User Guide. Additional technical and
developer documentation exists in the
[GitHub repository](https://github.com/yimingluo-md/guide-iei/tree/main/docs),
but is not included in this guide website. Links to those documents open GitHub;
the remaining documentation will be reviewed separately before website publication.

Screenshots illustrate synthetic demonstration data and example workstation
states. Their variants, scores, and classifications are not validation controls
or evidence about real patients. Your installed resources and counts may differ.

Stuck during setup or import? See [installation troubleshooting](guide/02-install.md#common-first-run-problems)
or [quality control](guide/10-quality-control.md).

GUIDE-IEI software is MIT-licensed. Annotation datasets retain their own
licenses; some are restricted to noncommercial use.
[View dataset licenses](guide/03-datasets.md#data-sources-and-licenses).

## Recent updates

These updates are included in the **v0.6.3 Apple Silicon beta**. [Download and install the Mac app](guide/02-install.md).

- **GPN-Star PHRED.** Rank scored variants by evolutionary constraint in Variant Review. [Reading the predictor cards](guide/09-reading-a-variant.md)
- **Finding regulatory variants.** Search a gene or gene set for variants in linked regulatory elements. Intergenic hits can also be reviewed using SCREEN tissue activity. [Whole-genome analysis](guide/05-whole-genome.md#predicted-regulatory-target-genes)
- **Reviewing an existing cohort.** After updating, open **Cohort Search → Manage samples** to identify entries that need reindexing. [Updating older cohort entries](guide/08-cohort-search.md#updating-older-cohort-entries)
- **Revisiting frameshifts.** If you previously reviewed a frameshift at an exon boundary on a multiallelic record, rerun annotation to refresh its LOFTEE 50-bp result. [Reading a variant page](guide/09-reading-a-variant.md)

[Full changelog on GitHub](../CHANGELOG.md)

## Chapters

1. [What GUIDE-IEI does](guide/01-what-guide-iei-does.md)
2. [Install it on your computer](guide/02-install.md)
3. [Set up the annotation datasets](guide/03-datasets.md)
4. [Your first exome: VCF to reviewed shortlist](guide/04-first-exome.md)
5. [Whole-genome analysis](guide/05-whole-genome.md)
6. [Trio analysis](guide/06-trio.md)
7. [Phenotypes and the Sample Library](guide/07-phenotypes-and-library.md)
8. [Cohort analysis: review and search](guide/08-cohort-search.md)
9. [Reading a variant page](guide/09-reading-a-variant.md)
10. [Quality control and sanity checks](guide/10-quality-control.md)
