---
title: "Whole-genome analysis"
parent: User Guide
nav_order: 5
---

# Whole-genome analysis

Extending analysis from the exome to the whole genome changes three things:
the computational cost of annotation, the strategy required to reduce
several million variants to a reviewable candidate set, and the nature of
the evidence available for the non-coding candidates that emerge.

## Annotating a genome

A whole-genome VCF typically contains 4–5 million PASS variants, and
annotation time scales with variant count rather than genome size.
Annotating a full genome on a standard workstation is supported but slow —
hours to more than a day, depending on hardware. In practice, two
approaches are reasonable: begin the run at the end of the workday and let
it complete overnight, or, where institutional high-performance computing
is available, perform annotation there and return the annotated VCF to
GUIDE-IEI for review (**Import → Review annotated VCF**). The review
process described below is designed for a standard workstation regardless
of where annotation was performed.

## From 4 million variants to a reviewable set

No reviewer can, or should, examine a whole genome variant by variant. The
default review profile (**Compact WGS**) therefore applies a
candidate-selection step whose guiding principle is *preserved sensitivity
for plausibly disease-relevant variation*: a variant is excluded only when
there is affirmative evidence it is common or falls outside every category
of interest — never because data are missing.

Two conditions are required of every variant: a PASS or unfiltered (`.`)
site FILTER with acceptable call quality, and a gnomAD popmax ≤ 0.01 **or no available
popmax value** — an unscored variant is deliberately retained rather than
discarded for lack of data. A variant meeting these conditions is kept if
it satisfies **any** of four criteria:

1. it lies within coding exons or canonical splice sites;
2. SpliceAI ≥ a pre-defined cutoff (default 0.5), capturing predicted
   splice-altering variants at any depth within an intron;
3. |PromoterAI| ≥ a pre-defined cutoff (default 0.8), capturing predicted
   expression-altering promoter variants; or
4. it overlaps an ENCODE candidate cis-regulatory element (this criterion
   can be broadened to all non-coding sequence, or omitted).

Because the precomputed SpliceAI and PromoterAI tables score
single-nucleotide variants but not every indel, an intronic or promoter
indel that *cannot* be scored is retained rather than silently lost, and is
flagged (`IEI_UNSCORED_INDEL`) so the reviewer knows the score is absent
rather than reassuring. A variant whose score is present but sub-threshold
does not use this exception. For these unscored indels, an on-demand
SpliceAI score can be requested for the individual variant under review
([Reading a variant page](09-reading-a-variant.md)).

In a representative genome, this strategy reduced approximately 4.3 million
PASS variants to roughly 23,000 retained records — a set that preserves
every rare coding variant together with the qualifying non-coding
candidates, at a size a human reviewer can filter and interrogate. The
selection runs once per genome, in minutes; reopening the same genome with
unchanged settings does not repeat it.

An alternative profile (**Full WGS**) indexes every PASS carrier call for
exhaustive searchability, at a cost of roughly 10 GB of database per
genome. For routine review, the compact profile is the appropriate
default.

## Interpreting non-coding candidates

The review workspace is unchanged from exome analysis, with regulatory
context added, and candidates can be examined by the criterion through
which they qualified. In practice, the developer recommends concentrating
first on the two non-coding variant classes with relatively
well-established disease mechanisms and better prediction:

1. **Deep intronic splice-disrupting variants (SpliceAI).** A high delta
   score deep within an intron predicts disruption of normal splicing —
   cryptic exon inclusion, exon skipping, or intron retention. Whether
   the score reflects loss of a native site or gain of a cryptic one, the
   functional consequence is corruption of the normal transcript:
   effectively a **loss-of-function mechanism**. SpliceAI does not
   predict expression increase.
2. **Promoter variants (PromoterAI).** PromoterAI
   ([Illumina, *Science* 2025](https://www.science.org/doi/10.1126/science.ads7373))
   is a deep neural network from the group that produced SpliceAI; it
   reads the sequence context around a transcription start site and
   returns a **signed** score from −1 to +1 for a variant's predicted
   effect on expression. The sign matters clinically: a negative score
   predicts under-expression — a haploinsufficiency-type mechanism —
   while a positive score predicts **over-expression**, a dosage-gain
   mechanism no splice predictor can capture.

Both predictions remain predictions: a qualifying score nominates a
mechanism to be tested — transcript analysis for splice candidates,
expression studies for promoter candidates — not a conclusion.

**Regulatory-element overlap** is a broader and correspondingly weaker
criterion, and reading it requires one piece of background. Much of the
genome's regulatory logic is written in **epigenomic** marks rather than
sequence: chromatin accessibility, promoter- and enhancer-associated
histone modifications (H3K4me3, H3K27ac), and CTCF binding. The ENCODE
consortium integrated these assays across hundreds of biosamples into a
registry of **candidate cis-regulatory elements (cCREs)** — approximately
2.37 million short segments of GRCh38, each classified by its biochemical
signature (promoter-like, proximal or distal enhancer-like, CTCF-bound,
chromatin-accessible). *Candidate* is the operative word: a cCRE is a
reproducible biochemical signature, not a demonstrated regulatory
function.

Crucially, the registry is genome-wide but **activity is
cell-type-specific**: an element may be open and active in one lineage
and silent in another. For a disease of the immune system, the
informative question is therefore not "does this variant fall in a cCRE?"
but "is that element active in the relevant immune cell types — and was
that cell type actually assayed?" GUIDE-IEI prepares the SCREEN data to
answer exactly that question, at two levels: organ- and tissue-level
classifications for body-wide context, and a curated immune-cell layer
with donor-level evidence. The detailed semantics of this display are
covered in [Reading a variant page](09-reading-a-variant.md).

Prediction in this territory is younger than for splicing or promoters;
future development is planned for regulatory-region prediction, including
assignment of regulatory elements to their target genes and variant-level
impact scores.

*[Screenshot: whole-genome variant detail with Regulatory evidence tab]*

Three interpretive boundaries deserve emphasis, because non-coding
evidence invites over-reading:

1. **Position is not mechanism.** A regulatory element may act on the
   nearest gene, a distant gene, several genes, or none of clinical
   relevance. The genes displayed are proximity context, not predicted
   targets.
2. **Absent evidence is not negative evidence.** The context layer
   preserves which assays were actually performed; a cell type without
   data is reported as unavailable, never as inactive.
3. **Overlap is not independent evidence of pathogenicity.** It
   establishes only that the variant lies within sequence with regulatory
   potential; the causal argument must be built from converging evidence
   and, ultimately, functional study.

## Immune-context filtering

For IEI work specifically, the variant list can be restricted to variants
whose overlapping element shows *positive* activity in immune-related
tissues or curated immune-cell contexts, using the built-in Immune core
and Immune all sets or user-defined selections. Consistent with the
principle above, missing or negative context never excludes a variant
automatically.

## Practical notes

- For multi-gigabyte files, provide the file's location on disk rather
  than uploading through the browser, which would create an unnecessary
  copy.
- Regulatory review is available only for whole-genome imports; an exome
  cannot support it and the corresponding interface is hidden.
- A compact-profile genome adds roughly 30–120 MB to the local library;
  the annotated source VCF remains wherever it is stored.

Technical reference: [The review workbench](../REVIEW_WORKBENCH.md) and
[SCREEN tissue & immune data](../SCREEN_TISSUE_IMMUNE_DATA.md).

Next: [Trio analysis](06-trio.md)
