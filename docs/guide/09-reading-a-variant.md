---
title: "Reading a variant page"
parent: User Guide
nav_order: 9
---

# Reading a variant page

What each block of evidence on the variant page asserts — and, equally
important, what it does not. GUIDE-IEI presents evidence neutrally: no
verdict labels, no pass/fail coloring, because the weight of any single
line of evidence depends on the gene, the inheritance model, and the
patient. This chapter supplies the meaning of each block; the weighing
belongs to the reviewer.

*[Screenshot: variant detail view with evidence sections]*

## Population frequency

gnomAD frequencies, with **popmax** — the highest frequency observed in any
major population — as the headline value.

One caveat is specific to this annotation path: a **blank gnomAD value
means "unavailable from this VEP annotation," not "absent from gnomAD."**
Depending on the VEP cache's matching path, a variant without an rsID may
receive no frequency even when the allele is present in the gnomAD Browser.
Any candidate under serious consideration should be confirmed in the gnomAD
Browser directly, by normalized chromosome, position, REF, and ALT.

## Consequence and transcripts

Every transcript consequence is retained, with the preferred consequence
per allele and gene marked (MANE Select first, then MANE Plus Clinical,
then VEP's selection). When a candidate's mechanism is
transcript-dependent — coding in one isoform, intronic in another — the
full transcript table is the appropriate reference. IMPACT
(HIGH/MODERATE/…) is VEP's coarse consequence tier: useful for filtering,
too coarse for judging an individual variant.

## Loss-of-function evidence

For putative loss-of-function variants (stop-gained, frameshift, essential
splice), three layers are presented:

- **LOFTEE HC/LC.** High-confidence (HC) indicates the variant survived
  LOFTEE's transcript-level checks for recognized LoF-rescue patterns —
  non-canonical splice contexts, terminal-exon position, and related
  escapes. Low-confidence (LC) names the specific filter that was
  triggered: an inspectable reason, not a score.
- **The 50-bp rule, recalculated.** For frameshifts, standard LOFTEE
  applies the last-exon-junction 50-bp rule at the variant's own position.
  GUIDE-IEI recalculates it at the **premature termination codon the
  shifted reading frame actually generates**, recording both results with
  provenance. Where the two disagree, the PTC-based value carries the main
  annotation.
- **Frame-restoring haplotypes.** When a sample carries nearby indels that
  jointly restore the reading frame, the combined consequence is evaluated
  per haplotype. Fully restored, phase-confirmed events are excluded from
  the default view; partially restored or phase-unresolved events remain
  visible with their state labeled.

## Missense and mechanism predictors

The dbNSFP panel (AlphaMissense, CADD, REVEL, SIFT, PolyPhen-2, and
others) is reported side by side. Predictors disagree routinely; agreement
across methodologically distinct predictors is more informative than any
single value, and all are population-level statistical statements with no
knowledge of the individual patient. When LoGoFunc is installed, its
gain- versus loss-of-function prediction adds mechanistic context — of
particular relevance in IEI, where GOF and LOF variants in the same gene
can produce distinct diseases.

## Splicing

SpliceAI delta scores are shown with the affected position and type
(donor/acceptor, gain/loss), drawn from the precomputed MANE table. The
research convention of 0.5 is a convention, not a boundary: a score of
0.45 adjacent to a weak splice site in a compelling candidate gene
warrants attention. Deep intronic variants are precisely where SpliceAI
contributes most; in whole-genome work they reach the review set through
the SpliceAI selection criterion. An `IEI_UNSCORED_INDEL` flag indicates a
variant retained despite the absence of a precomputed score — the score is
missing, not reassuring.

For an unscored **indel**, the variant page offers **"Get SpliceAI score
online (Broad lookup)"** — a per-variant request to the Broad Institute's
public SpliceAI service, which computes scores for arbitrary variants on
demand. This is the single deliberate exception to GUIDE-IEI's fully local
operation: it occurs only on an explicit click, transmits only the
variant's position and alleles — never sample, genotype, or phenotype
data — and the result is stored locally so a variant is sent at most once.
Results are labeled as online lookups (masked scores, 500 bp window) and
inform the reviewer only; they never enter the VCF or any filter.

## ClinVar and ClinGen

- **ClinVar** entries are **reports**, not established facts: submitters
  differ in rigor, and classifications age. The annotation is refreshed at
  every run, and the `ClinVar_path_aa_match` flag additionally marks
  variants producing the **same amino-acid change** as a reported
  pathogenic or likely-pathogenic variant through a different nucleotide
  change.
- **ClinGen** contributes two expert-panel layers: allele-level assertions
  from the Evidence Repository, with disease- and inheritance-specific
  detail, and gene-level validity and dosage curation. One dosage caution
  bears repeating: a haploinsufficiency score of 30 denotes an
  autosomal-recessive phenotype, **not** evidence of haploinsufficiency.

## Gene context

The Gene tab joins the bundled knowledge: gnomAD constraint (the gene's
depletion for LoF and missense variation in the population), IUIS IEI
classification and disease association, and ClinGen gene–disease validity.
Constraint characterizes the gene, not the variant: a truncating variant
in a LoF-tolerant gene invites skepticism, while the same variant in a
highly constrained IUIS gene invites attention.

## Call-quality flags

RepeatMasker and segmental-duplication overlap flags mark regions where
short-read variant calling is error-prone. They are grounds for examining
the sample's read-level evidence — depth, allele balance, genotype
quality — not grounds for automatic dismissal.

## Regulatory evidence (whole-genome imports)

Discussed in [Whole-genome analysis](05-whole-genome.md): cCRE overlap,
gene-TSS proximity context, and tissue/immune activity constitute position
and context, never independent evidence of pathogenicity.

Next: [Quality control and sanity checks](10-quality-control.md)
