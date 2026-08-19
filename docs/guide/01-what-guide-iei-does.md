---
title: "What GUIDE-IEI does"
parent: User Guide
nav_order: 1
---

# What GUIDE-IEI does

A brief orientation before installation.

## Two components

1. **An annotation engine** — Ensembl VEP 113 with a curated plugin stack,
   run locally in a container. Each variant is annotated with gnomAD
   frequencies, ClinVar and ClinGen expert curations, the dbNSFP predictor
   panel (AlphaMissense, CADD, and the rest), SpliceAI, LOFTEE with
   GUIDE-IEI's own loss-of-function refinements (PTC-recalculated 50-bp
   rule, frame-restoring haplotype detection), and, for whole-genome work,
   PromoterAI and ENCODE SCREEN cCRE regulatory context with curated tissue
   and immune-cell evidence.

2. **A review workbench** — a local browser application for working the
   annotated result: filter to a shortlist, read each candidate's evidence
   in one place, keep reviewed cases in a persistent sample library, and
   run genotype-first searches across everything you have reviewed before.

Everything runs on your own computer. No patient data leaves your machine
(one opt-in exception: a per-variant online SpliceAI lookup you explicitly
request — see the [FAQ](../FAQ.md)), no account, no subscription.

## Input

A called **VCF** — single- or multi-sample, `.vcf` or `.vcf.gz`:

- **GRCh38** is used directly. **GRCh37/hg19** files are converted through
  a quality-controlled, provenance-preserving liftover
  ([GRCh37 input](../GRCH37_INPUT.md)).
- FASTQ/BAM are not used: alignment and variant calling happen upstream,
  often at the sequencing lab, and the VCF is all GUIDE-IEI needs. For
  high-quality FASTQ processing or re-processing, the developer's personal
  recommendation is **Illumina DRAGEN**, or, as a free option,
  **[LOGAN](https://github.com/CCBR/LOGAN)** from NCI's CCBR.

## What it deliberately does not do

- **Classify variants.** No automated ACMG/AMP verdicts and no clinical
  reports. Evidence is presented neutrally. GUIDE-IEI is for clinicians and
  researchers who already bring a candidate-gene hypothesis for a specific
  patient.
- **Rank by phenotype.** No HPO-driven prioritization — GUIDE-IEI is built
  for IEI, where HPO-driven prioritization may have limited utility.
- **Replace confirmation.** Research use only; anything that would
  influence care needs CLIA-certified confirmation.

For the full scope statement, see
[What GUIDE-IEI is not](https://github.com/yimingluo-md/guide-iei#what-guide-iei-is-not)
on the project page.

## A typical session

1. Import a VCF and annotate.
2. Filter to rare, protein-altering (and, for WGS, qualifying non-coding)
   candidates; restrict to an IUIS or custom gene list, or stay
   genome-wide; refine with the other filters as needed.
3. Review the evidence per candidate — CLIA confirmation for anything
   actionable; for a promising VUS, connect with a lab that has
   functional-study capacity.
4. Keep the sample in the library for genotype-first discovery: when a new
   gene–disease association is published, search every stored case for
   qualifying variants in that gene, without touching the original files.

Next: [Install it on your computer](02-install.md)
