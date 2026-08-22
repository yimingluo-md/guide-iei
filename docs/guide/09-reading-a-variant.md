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

**Why a variant can legitimately appear on two transcripts.** Most genes
have exactly one clinically designated transcript (MANE Select), but some
— TCF3 is a classic example — additionally carry a **MANE Plus Clinical**
transcript: an isoform the clinical community needs for interpreting
certain diseases. Both consequences are real and both are retained.
Variants in overlapping genes similarly carry one consequence per gene.
The variant list's **One row per variant** display (on by default) shows
each variant once, represented by its highest-priority transcript, with a
**+N** chip counting the collapsed rows — hover the chip to see them, or
open the variant for the complete transcript table. Turning the toggle
off restores one row per transcript and gene. Nothing is filtered either
way; this is presentation only.

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

The predictor panel is reported side by side, raw values only. Predictors
disagree routinely; agreement across *methodologically distinct*
predictors is more informative than any single value, and all are
population-level statistical statements with no knowledge of the
individual patient. What follows is the complete catalog — what each
score measures and how it was derived — first the standing panel present
on every run, then the optional dbNSFP predictors that are off by
default.

### The standing panel

These columns are pulled from dbNSFP 5.4a (and the genome-wide sources)
on every annotation:

- **AlphaMissense** — DeepMind's missense classifier: a protein language
  model fine-tuned with structural context from AlphaFold and weak labels
  from human/primate population frequency. Score 0–1, higher = more
  likely pathogenic; the authors' three-way call (likely benign /
  ambiguous / likely pathogenic) is shown as their label, not ours.
- **REVEL** — an ensemble over thirteen component predictors, trained on
  rare disease-causing versus rare neutral missense variants —
  deliberately rare-versus-rare, to avoid learning allele frequency.
  Score 0–1, higher = more deleterious.
- **CADD** (raw and PHRED-scaled) — genome-wide deleteriousness trained
  to separate evolutionarily fixed (proxy-neutral) from simulated
  variants; not missense-specific, so it also scores synonymous, splice
  region, and non-coding positions. The PHRED value is a rank: 20 means
  the top 1% of all possible SNVs, 30 the top 0.1%. Whole-genome imports
  add the **CADD_WGS** track so non-coding records are scored too.
- **SIFT** and **PolyPhen-2 (HumDiv)** — the classical pair. SIFT scores
  homolog-alignment intolerance (0–1, *lower* = deleterious); PolyPhen-2
  combines alignment and structural features (0–1, higher = damaging;
  the HumDiv-trained model is the standing column, the HumVar model is
  in the optional set below). Retained for continuity with two decades
  of literature more than for standalone accuracy.
- **MetaRNN** — a recurrent-network meta-predictor over component scores
  and population frequencies. 0–1, higher = deleterious. Being
  frequency-aware, it partially re-counts rarity you have already
  filtered on.
- **PrimateAI** — a deep network using common variation in non-human
  primates as its benign training proxy, an elegant answer to the
  circularity problem below. 0–1, higher = deleterious.
- **Conservation triple** — **GERP++ RS** (substitution deficit at the
  position; higher = stronger constraint), **phyloP100way** (positive =
  conserved, negative = accelerated), **phastCons100way** (0–1
  probability of lying in a conserved element). Nucleotide-level and
  consequence-agnostic — informative precisely because they know nothing
  about proteins.

### Optional dbNSFP predictors

dbNSFP bundles far more predictors than any review needs at once; the
remainder are **off by default** to keep annotated VCFs lean and because
most add correlated rather than independent evidence. Any subset can be
enabled per job under **Run VEP first → Additional dbNSFP predictors**;
the one-click **Recommended extended** selection (marked ✓) covers the
methodologically distinct ones.

| Predictor | Approach | Direction | ✓ |
|---|---|---|---|
| SIFT4G | SIFT recomputed over broader ortholog alignments | lower = deleterious | ✓ |
| PolyPhen-2 HVAR | Same classifier, trained Mendelian-vs-common (the authors' recommendation for Mendelian work) | higher = damaging | ✓ |
| MutationTaster | Bayes classifier over conservation, splice, and mRNA features | probability attached to its disease/polymorphism call | |
| MutationAssessor | Subfamily-specific conservation patterns | higher = greater impact | ✓ |
| PROVEAN | Alignment delta score | more negative = more disruptive | ✓ |
| VEST4 | Random forest, HGMD-vs-common training | 0–1, higher | ✓ |
| MetaSVM / MetaLR | SVM / logistic-regression meta-predictors over component scores + allele frequency | higher = deleterious | ✓ |
| M-CAP | Classifier tuned for high sensitivity on rare missense | 0–1, higher | ✓ |
| MutPred2 | Pathogenicity probability plus inferred molecular mechanism (which property is altered) | 0–1, higher | ✓ |
| MVP | Deep residual network | 0–1, higher | |
| gMVP | Graph attention over local protein structural/functional context | 0–1, higher | |
| MPC | Deleteriousness conditioned on *regional* missense constraint within the gene | higher | ✓ |
| DEOGEN2 | Adds domain, interaction, and pathway context | 0–1, higher | |
| BayesDel (addAF) | Bayesian meta-score integrating allele frequency | higher = deleterious | ✓ |
| BayesDel (noAF) | The same score without the frequency term | higher = deleterious | |
| ClinPred | Boosted trees + random forest trained on ClinVar, frequency-aware | 0–1, higher | ✓ |
| LIST-S2 | Taxonomy-aware conservation (local identity and shared taxa) | 0–1, higher | |
| VARITY R | Trained for rare variants with weighting designed to reduce database circularity | 0–1, higher | ✓ |
| VARITY ER | The extremely-rare-variant counterpart | 0–1, higher | |
| ESM1b | 650M-parameter protein language model, zero-shot (no pathogenicity labels seen in training) | more negative = damaging | ✓ |
| PHACTboost | Gradient boosting over phylogeny-derived PHACT scores | higher | |
| MutFormer | Transformer trained on human protein sequences | higher | |
| MutScore | Adds positional clustering of known pathogenic missense | 0–1, higher | |
| popEVE | Deep generative evolutionary model (EVE lineage) calibrated against population data | more negative = more severe | |

### Reading the panel critically

Three structural caveats apply to any predictor comparison:

- **Circularity.** Many predictors train on ClinVar or HGMD; a variant
  adjacent to known pathogenic entries scores high partly because of that
  adjacency. Their agreement with ClinVar is therefore not independent
  confirmation. ESM1b, popEVE, PrimateAI, and the conservation scores are
  the most label-free lines in the panel.
- **Shared inputs.** The meta-predictors (REVEL, MetaSVM/LR, MetaRNN,
  BayesDel, ClinPred) consume overlapping component sets. Agreement
  *among* them counts for less than agreement *across* method families —
  say, a language model, a conservation score, and an ensemble.
- **Frequency double-counting.** BayesDel addAF, ClinPred, MetaRNN,
  MetaSVM, and MetaLR include allele frequency as a feature. After
  filtering on popmax, part of their score restates what the filter
  already established.

### Gain versus loss of function

When LoGoFunc is installed, its three class probabilities — neutral,
gain-of-function, loss-of-function — add mechanistic context of
particular weight in IEI, where GOF and LOF variants in the same gene
produce distinct diseases (*STAT1*, *STAT3*, *CARD11*, *JAK1* among
them). The prediction is transcript-specific: the page shows the source
transcript and protein change LoGoFunc scored, and flags any mismatch
with the transcript under review rather than silently transferring the
score.

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

Technical reference: [Annotation sources](../ANNOTATIONS.md) — dataset
provenance, versions, and preparation for every source above.

Next: [Quality control and sanity checks](10-quality-control.md)
