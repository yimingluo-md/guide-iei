---
title: "Your first exome: VCF to reviewed shortlist"
parent: User Guide
nav_order: 4
---

# Your first exome: VCF to reviewed shortlist

This chapter follows a single exome from VCF to a reviewed list of
candidate variants. The first pass takes 20–30 minutes at the screen; the
annotation run itself usually completes in minutes for an exome.

> Screenshots are being added to this chapter. Every screen described below
> corresponds to what appears in the application.

## Prerequisites

1. **GUIDE-IEI installed** and the workbench running
   ([Install it on your computer](02-install.md)) — started with
   `bash scripts/start_workbench.sh`, opened at `http://127.0.0.1:3000`.
2. **Annotation datasets installed** — at minimum the one-click
   **Recommended for exome** set plus dbNSFP
   ([Set up the annotation datasets](03-datasets.md)).
3. **A VCF** — single- or multi-sample, `.vcf` or `.vcf.gz`. GRCh38 is used
   directly; a GRCh37/hg19 file from an older pipeline is converted
   automatically with full quality control
   ([GRCh37 input](../GRCH37_INPUT.md)).

Without a patient VCF at hand, the walkthrough can be followed with the
synthetic regression VCF in the repository's `test/` folder — public
control variants and one artificial sample, no patient data.

## Step 1 — Import the VCF

The application opens on the **Import VCF** screen, which offers two
routes:

- **Run VEP first** (the default) — the VCF has not yet been annotated.
  This walkthrough takes this route.
- **Review annotated VCF** — the file was already annotated by GUIDE-IEI or
  a compatible VEP pipeline and only review is needed.

Drag the `.vcf`/`.vcf.gz` file onto the drop zone, or click to browse.

*[Screenshot: Import screen, file dropped, "Run VEP first" selected]*

## Step 2 — Confirm readiness and settings

The next screen presents two things.

**Dataset readiness** — every annotation source, marked available or
missing. Required sources must all be present before the run can start;
optional sources (LoGoFunc, CADD) simply annotate when present. If a
required source is missing, the screen links directly to **Set up
annotation datasets**.

**Run settings** — the defaults are appropriate for a first exome run:

- **PASS variants only**: on. Variants flagged as unreliable during
  generation of the VCF (alignment and variant calling) are excluded.
  Records the upstream caller never filtered (FILTER `.`) are retained —
  absence of filtering is not evidence of failure — and a run note is
  recorded when an entire callset arrives unfiltered.
- **Coding + splice regions**: on. Annotation is restricted to coding exons
  and canonical splice sites.

*[Screenshot: parameters screen with dataset readiness list]*

Start the run.

## Step 3 — The run

The run screen reports progress through each stage: input checks, the
ClinVar download (refreshed and version-stamped at every run), region
filtering, VEP annotation, and the post-processing that refines
loss-of-function calls. An exome typically completes in minutes; the page
can be left and revisited, as jobs continue in the background.

On completion, two artifacts are written beside the output VCF: the
annotated VCF itself, and an **annotation-QC certificate** summarizing how
completely each source annotated the data
([Quality control](10-quality-control.md)).

*[Screenshot: run progress with stage list]*

## Step 4 — Open the review

The completed run proceeds to review intake. Two defaults deserve
attention:

- **Keep in Sample Library**: on. The reviewed dataset is retained and can
  be reopened at any time without re-importing
  ([Phenotypes and the Sample Library](07-phenotypes-and-library.md)).
- **Include qualifying variants in Cohort Search**: on. The sample's
  qualifying variants join the local cohort index, so future carrier
  searches include this case ([Cohort search](08-cohort-search.md)).

Choose **Review once** instead when the analysis should leave no stored
trace. After intake, each VCF sample may be linked to an individual — new
or existing — or left unlinked.

## Step 5 — The review workspace

The workspace has three areas:

- **Left rail** — navigation among the variant list, gene lists, sample
  library, cohort search, storage, and the glossary.
- **Variant table** — one row per variant: consequence, gene, IMPACT,
  population frequency, and predictor summaries.
- **Detail panel** — selecting a row opens the complete evidence for that
  variant: transcript consequences, predictor scores, ClinVar and ClinGen
  records, the gene's constraint and IUIS context, and the sample's
  genotype evidence.

*[Screenshot: review workspace with a variant selected]*

## Step 6 — Filter to a shortlist

The filter panel narrows the table without discarding anything — removing
a filter restores the rows. A reasonable first pass for a suspected
monogenic condition:

1. **Population frequency** — gnomAD popmax ≤ 0.01, or unavailable.
2. **IMPACT** — begin with HIGH and MODERATE.
3. **Gene lists** — restrict to an IUIS IEI gene list or a custom panel
   when candidate genes are in mind, or omit to remain genome-wide.
4. **ClinVar** — optionally surface variants with existing pathogenic or
   conflicting reports first.

An exome typically lands in the low hundreds of rows after steps 1–2 — a
manageable review set. A markedly different figure — single digits, or
tens of thousands — is grounds to stop and consult
[Quality control](10-quality-control.md) before interpreting anything.

*[Screenshot: filter panel with popmax and IMPACT set]*

## Step 7 — Read the evidence

Work through the shortlist row by row. For each variant, the detail panel
presents the evidence neutrally — GUIDE-IEI assigns no classifications, by
design ([What GUIDE-IEI does](01-what-guide-iei-does.md)). The meaning of
each evidence block, and its limits, are the subject of
[Reading a variant page](09-reading-a-variant.md); underlined terms
anywhere in the interface are click-to-define glossary entries.

## Step 8 — Where this leads

- **A candidate you believe is pathogenic/likely pathogenic and
  responsible for the patient's manifestations** → confirm in a
  CLIA-certified laboratory before any clinical action (see the project's
  *Research use only* statement).
- **A compelling VUS** → contact a laboratory with the capacity for the
  relevant functional experiment; searching recent publications is a good
  way to identify the lead scientists working on that gene.
- **Nothing convincing** → the sample remains in the library. When new
  knowledge emerges — a new gene–disease association, a ClinVar
  reclassification — the review can be reopened, or the cohort searched,
  without re-requesting data.

## Continuing

- Analyzing a genome: [Whole-genome analysis](05-whole-genome.md)
- Parental samples available: [Trio analysis](06-trio.md)
- Cases accumulating: [Cohort search](08-cohort-search.md)
