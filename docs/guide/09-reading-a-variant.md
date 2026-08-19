---
title: "Reading a variant page"
parent: User Guide
nav_order: 9
---

# Reading a variant page

What each block of evidence on the variant detail view asserts — and, just
as important, what it does not. GUIDE-IEI presents this evidence neutrally:
there are no verdict labels or pass/fail colors, because the weight of any
single line of evidence depends on the gene, the inheritance model, and the
patient. This chapter gives you the meaning; the weighing is yours.

*[Screenshot: variant detail view with evidence sections]*

## Population frequency

gnomAD frequencies with **popmax** (the highest frequency in any major
population) as the headline number.

One caveat specific to this annotation path: a **blank gnomAD value means
"unavailable from this VEP annotation," not "absent from gnomAD."**
Depending on the VEP cache's matching path, a variant without an rsID may
receive no frequency even when the allele is in the gnomAD Browser. For any
candidate you are taking seriously, confirm by normalized chromosome,
position, REF, and ALT in the gnomAD Browser directly.

## Consequence and transcripts

Every transcript consequence is retained, with the preferred one per
allele/gene marked (MANE Select first, then MANE Plus Clinical, then VEP's
pick). When your candidate's mechanism is transcript-dependent — a variant
coding in one isoform and intronic in another — the full transcript table
is the place to look. IMPACT (HIGH/MODERATE/…) is VEP's coarse consequence
tier, useful for filtering, too coarse for judging an individual variant.

## Loss-of-function evidence

For putative LoF variants (stop-gained, frameshift, essential splice),
three layers:

- **LOFTEE HC/LC** — HC (high-confidence) means the variant survived
  LOFTEE's transcript-level checks for known LoF-rescue patterns
  (non-canonical splice contexts, terminal-exon position, and similar).
  LC (low-confidence) names the filter that tripped — it is a specific,
  inspectable reason, not a score.
- **The 50-bp rule, recalculated.** For frameshifts, standard LOFTEE
  applies the last-exon-junction 50-bp rule at the variant's own position;
  GUIDE-IEI recalculates it at the **premature termination codon the
  shifted reading frame actually creates**, and records both results with
  provenance fields. When the two disagree, the PTC-based value is the one
  in the main annotation.
- **Frame-restoring haplotypes.** When the same sample carries nearby
  indels that together restore the reading frame, the combined
  consequence is computed per haplotype. Fully restored, phase-confirmed
  events are excluded from the default view; partially restored or
  phase-unresolved events stay visible with their state labeled.

## Missense and mechanism predictors

The dbNSFP panel (AlphaMissense, CADD, REVEL, SIFT, PolyPhen-2, and the
rest) reported side by side. Predictors disagree routinely; agreement
across methodologically different predictors is more informative than any
single value, and all of them are genome-wide statistical statements that
know nothing about your patient. If LoGoFunc is installed, its
gain-vs-loss-of-function call adds mechanism context — particularly
relevant in IEI, where GOF and LOF in the same gene can produce different
diseases.

## Splicing

SpliceAI delta scores with the affected position and type (donor/acceptor,
gain/loss). Scores come from the precomputed MANE table; the commonly used
research threshold is 0.5, but treat it as a convention, not a boundary —
a 0.45 near a weak splice site in your candidate gene deserves eyes.
Deep intronic variants are exactly where SpliceAI earns its place; they
reach the review set through the SpliceAI retention route in whole-genome
work. An `IEI_UNSCORED_INDEL` flag means the variant was retained despite
having no precomputed score — the score is missing, not reassuring.

For an unscored **indel**, the variant page offers **"Get SpliceAI score
online (Broad lookup)"** — a per-variant request to the Broad Institute's
public SpliceAI service, which computes scores for arbitrary variants on
demand. This is the one deliberate exception to GUIDE-IEI's fully-local
operation: it happens only when you click, it transmits only the variant's
position and alleles (never sample, genotype, or phenotype data), and the
result is stored locally so the variant is not sent twice. Results are
labeled as online lookups (masked scores, 500 bp window) and are shown for
your reading only — they never enter the VCF or any filter.

## ClinVar and ClinGen

- **ClinVar** entries are **reports**, not established facts — submitters
  differ in rigor, and classifications age. The annotation is refreshed at
  every run, and the `ClinVar_path_aa_match` flag additionally marks
  variants causing the **same amino-acid change** as a reported
  pathogenic/likely-pathogenic variant (a different nucleotide change with
  the same protein consequence).
- **ClinGen** adds two expert-panel layers: allele-level assertions from
  the Evidence Repository (with disease- and inheritance-specific detail),
  and gene-level validity/dosage curation. One dosage caution the
  interface repeats: a haploinsufficiency score of 30 means
  "autosomal-recessive phenotype," **not** haploinsufficiency evidence.

## Gene context

The Gene tab joins the bundled knowledge: gnomAD constraint (how depleted
the gene is for LoF/missense variation in the population), IUIS IEI
classification and disease association, and ClinGen gene–disease validity.
Constraint tells you about the gene, not the variant: a truncating variant
in a LoF-tolerant gene warrants skepticism; the same variant in a highly
constrained IUIS gene warrants attention.

## Call-quality flags

RepeatMasker and segmental-duplication overlap flags mark regions where
short-read calling is error-prone. They are reasons to scrutinize the
sample's read evidence (depth, allele balance, genotype quality on the
sample tab) — not reasons to auto-dismiss.

## Regulatory evidence (whole-genome imports)

Covered in [Whole-genome analysis](05-whole-genome.md): cCRE overlap,
gene-TSS proximity context, and tissue/immune activity — position and
context, never independent pathogenicity evidence.

Next: [Quality control and sanity checks](10-quality-control.md)
