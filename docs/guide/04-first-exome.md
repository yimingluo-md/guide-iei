---
title: "Your first exome: VCF to reviewed shortlist"
parent: User Guide
nav_order: 4
---

# Your first exome: VCF to reviewed shortlist

This walkthrough takes one exome VCF from import to a reviewed shortlist of
candidate variants. Plan for 20–30 minutes at the screen the first time;
the annotation run itself usually takes minutes for an exome.

> Screenshots are being added to this chapter. Every screen described below
> matches what you will see in the application.

## Before you start

You need three things:

1. **GUIDE-IEI installed** and the workbench running
   ([Install it on your computer](02-install.md)) — start it with
   `bash scripts/start_workbench.sh` and open `http://127.0.0.1:3000` in
   your browser.
2. **Annotation datasets installed** — at minimum the one-click
   **Recommended for exome** set, plus dbNSFP
   ([Set up the annotation datasets](03-datasets.md)).
3. **A VCF file** from your sequencing provider — single- or multi-sample,
   `.vcf` or `.vcf.gz`. GRCh38 is used directly; a GRCh37/hg19 file from an
   older pipeline is converted automatically with full quality control
   ([GRCh37 input](../GRCH37_INPUT.md)).

No patient VCF yet? You can follow along with the synthetic regression VCF
in the repository's `test/` folder — it contains public control variants and
one artificial sample, no patient data.

## Step 1 — Import the VCF

The application opens on the **Import VCF** screen with two routes:

- **Run VEP first** (the default) — your VCF has not been annotated yet.
  This walkthrough uses this route.
- **Review annotated VCF** — the file was already annotated by GUIDE-IEI or
  a compatible VEP pipeline, and you only want to review it.

Drag your `.vcf`/`.vcf.gz` file onto the drop zone (or click to browse).

*[Screenshot: Import screen, file dropped, "Run VEP first" selected]*

## Step 2 — Check readiness and settings

The next screen shows two things:

**Dataset readiness** — every annotation source with a green (available) or
amber (missing) marker. Required sources must all be green before the run
can start; optional sources (LoGoFunc, PromoterAI) simply annotate when
present. If something required is missing, the screen links you directly to
**Set up annotation datasets**.

**Run settings** — the defaults are right for a first exome run:

- **PASS variants only**: on. Variants your sequencing pipeline itself
  flagged as unreliable are excluded.
- **Coding + splice regions**: on. Annotation is restricted to coding exons
  and splice sites, which is what makes an exome run take minutes on a
  workstation.

*[Screenshot: parameters screen with dataset readiness list]*

Start the run.

## Step 3 — Watch the run (or walk away)

The run screen shows live progress through the stages: input checks, the
latest ClinVar download (fetched fresh on every run and version-stamped),
region filtering, VEP annotation, and the post-processing that refines
loss-of-function calls. An exome typically finishes in minutes; you can
leave the page and come back — jobs continue in the background.

When the run completes, two artifacts are written next to the output VCF:
the annotated VCF itself, and an **annotation-QC certificate** summarizing
how completely each source annotated your data
([Quality control](10-quality-control.md)).

*[Screenshot: run progress with stage list]*

## Step 4 — Open the review

The completed run opens into the review intake. Two defaults matter here:

- **Keep in Sample Library**: on. The reviewed dataset is stored so you can
  reopen it any time without re-importing
  ([Phenotypes and the Sample Library](07-phenotypes-and-library.md)).
- **Include qualifying variants in Cohort Search**: on. The sample's
  qualifying variants join your local cohort index so future "who else
  carries this?" searches include it
  ([Cohort search](08-cohort-search.md)).

Choose **Review once** instead if this analysis should leave no stored
trace. After intake, each VCF sample can be linked to an individual (new or
existing) or left unlinked for now.

## Step 5 — The review workspace

The workspace has three areas:

- **Left rail** — navigation between the variant list, gene lists, sample
  library, cohort search, storage, and the glossary.
- **Variant table** — one row per variant, with consequence, gene, IMPACT,
  population frequency, and predictor summaries.
- **Detail panel** — click any row to open the full evidence for that
  variant: transcript consequences, predictor scores, ClinVar/ClinGen
  records, the gene's constraint and IUIS context, and the sample's
  genotype evidence.

*[Screenshot: review workspace with a variant selected]*

## Step 6 — Filter to a shortlist

The filter panel narrows the table without ever deleting anything —
removing a filter restores the rows. A sensible first pass for a suspected
monogenic condition:

1. **Population frequency** — gnomAD popmax ≤ 0.01, or unavailable.
2. **IMPACT** — start with HIGH and MODERATE.
3. **Gene lists** — restrict to an IUIS IEI gene list or your own panel if
   you have candidate genes in mind, or skip this to stay genome-wide.
4. **ClinVar** — optionally surface variants with existing pathogenic or
   conflicting reports first.

Expect an exome to land in the low hundreds of rows after steps 1–2 —
a manageable review set. If you see something wildly different (single
digits, or tens of thousands), stop and check
[Quality control](10-quality-control.md) before drawing conclusions.

*[Screenshot: filter panel with popmax and IMPACT set]*

## Step 7 — Read the evidence

Work through the shortlist row by row. For each variant, the detail panel
presents the evidence neutrally — GUIDE-IEI does not classify variants, by
design ([What GUIDE-IEI does](01-what-guide-iei-does.md)). What each field
means, and how to weigh it, has its own chapter:
[Reading a variant page](09-reading-a-variant.md). Underlined terms
anywhere in the interface are click-to-define glossary entries.

## Step 8 — Where this leads

- A **plausible candidate** in a known IEI gene → confirm in a
  CLIA-certified laboratory before any clinical action (see the project's
  *Research use only* statement).
- A **compelling VUS** → consider functional follow-up; the evidence panel
  gives you the transcript, protein consequence, and predictor context a
  functional-validation plan starts from.
- **Nothing convincing** → the sample stays in your library. When new
  knowledge arrives — a new gene–disease paper, a ClinVar update — reopen
  the review or search your cohort in seconds rather than re-requesting
  data.

## What's next

- Sequencing a genome instead? [Whole-genome analysis](05-whole-genome.md)
- Have parental samples? [Trio analysis](06-trio.md)
- Multiple cases accumulating? [Cohort search](08-cohort-search.md)
