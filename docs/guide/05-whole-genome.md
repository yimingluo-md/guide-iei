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

After essential setup, choose **Install recommended WGS datasets** under
**Import & QC → Set up annotation datasets** for full SpliceAI MANE 1.5,
ENCODE SCREEN tissue/immune contexts, and AlphaGenome AVI. These downloads
extend the starter annotations without reinstalling core references. PromoterAI
is also recommended for WGS, and GenIA for both exome and genome; each requires
user-provided data. dbNSFP, LoGoFunc, FuncVEP, full CADD and OMIM remain optional.
GPN-Star-M and GPN-Star-V can be installed separately for genome-wide SNV scoring, including intergenic variants. Neither model changes which variants pass WGS intake.
See [dataset setup and disk requirements](03-datasets.md).

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
candidate-selection step. It is a candidate set, not an exhaustive inventory
of disease-relevant variation. Missing population frequency passes the frequency
check; a missing predictor score does not satisfy that score's route. A variant
must still meet another route or a defined indel exception below.

Two conditions are required of every variant: a PASS or unfiltered (`.`)
site FILTER with acceptable call quality, and a gnomAD popmax ≤ 0.01 **or no available
popmax value**. Passing the frequency check alone does not retain a variant.
A variant meeting these conditions is kept if
it satisfies **any** of four criteria:

1. it lies within coding exons or canonical splice sites;
2. SpliceAI ≥ a pre-defined cutoff (default 0.5), capturing predicted
   splice-altering variants at any depth within an intron;
3. |PromoterAI| ≥ a pre-defined cutoff (default 0.8), capturing predicted
   expression-altering promoter variants; or
4. it overlaps an ENCODE candidate cis-regulatory element (this criterion
   can be broadened to all non-coding sequence, or omitted).

At a site with several alternate alleles, Compact WGS evaluates each allele
separately. The review VCF preserves the original ALT order, genotypes, and
allele depths. Its `IEI_WGS_RETAINED` field marks which alleles qualified.
Variant Review and Cohort Search use those marks. A spanning-deletion
placeholder (`*`) cannot qualify a site. For a compact file prepared with
an older release, repeat intake from the original annotated WGS VCF to apply
these per-allele decisions.

Because the precomputed SpliceAI and PromoterAI tables score
single-nucleotide variants but not every indel, an intronic or promoter
indel meeting the corresponding unscored-indel exception is retained and is
flagged (`IEI_UNSCORED_INDEL`) so the reviewer knows the score is absent
rather than reassuring. A variant whose score is present but sub-threshold
does not use this exception. For these unscored indels, an on-demand
SpliceAI score can be requested for the individual variant under review
([Reading a variant page](09-reading-a-variant.md)).

The exception requires the relevant predictor field to be declared in the VCF;
the promoter route also needs the installed transcript/TSS map. An entirely
absent annotation source does not activate the exception. These routes still
require frequency and quality checks.

In one internal test genome, the Compact WGS profile reduced approximately
4.3 million PASS variants to about 23,000 retained records. This is an
illustrative example, not an expected acceptance range; results vary with
ancestry, sequencing, variant calling, and annotation completeness. The
selection runs once per genome, in minutes; reopening the same genome with
unchanged settings does not repeat it.

An alternative profile (**Full WGS**) indexes every PASS carrier call for
exhaustive searchability, at a cost of roughly 10 GB of database per
genome. For routine review, the compact profile is the appropriate
default.

## Interpreting non-coding candidates

The review workspace is unchanged from exome analysis, with regulatory
context added, and candidates can be examined by the criterion through
which they qualified. A practical review strategy is to begin with splice
and promoter candidates, for which computational prediction methods are more
mature, and then examine broader regulatory-element overlaps.

1. **Deep intronic splice candidates.** A high SpliceAI delta score predicts
   altered donor or acceptor use and may nominate cryptic exon inclusion,
   exon skipping, intron retention, or another splice change. It does not by
   itself establish loss of function. The molecular consequence depends on
   the affected transcript, reading frame, abundance of the abnormal
   transcript, and the gene's disease mechanism.
2. **Promoter candidates.** PromoterAI estimates whether a variant near a
   transcription start site may reduce or increase expression. A negative
   score predicts reduced expression; a positive score predicts increased
   expression. Clinical relevance depends on whether the transcript is
   disease-relevant, whether the gene is dosage-sensitive, and whether the
   predicted expression change can be confirmed experimentally.

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

For predicted targets of an overlapping regulatory element, open [Element–gene links](#predicted-regulatory-target-genes). Each ENCODE-rE2G score describes a link in a specific reference biosample.

See the [regulatory-evidence reference](../SCREEN_TISSUE_IMMUNE_DATA.md#variant-review-interface-and-filtering)
for display states and assay-availability labels.

Three interpretive boundaries deserve emphasis, because non-coding
evidence invites over-reading:

1. **Position is not mechanism.** A regulatory element may act on the
   nearest gene, a distant gene, several genes, or none of clinical
   relevance. The nearby-gene table lists genes by distance. The Element–gene links tab shows which genes the model predicts as targets.
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
principle above, context does not exclude variants unless you enable the
positive-evidence filter. Once enabled, variants without qualifying positive
context are hidden, including those with unavailable context. Display sets and
variant-list filters are separate controls.

## Predicted regulatory target genes

Choose **Import & QC → Annotation datasets → ENCODE-rE2G → Download / resume**, or install the recommended WGS datasets. The download contains 5.08 GB of published GRCh38 predictions. GUIDE-IEI builds an interval index under your selected annotation storage and resumes an interrupted installation from the last verified file.

Open **Variant Review → Regulatory evidence → Element–gene links**. Expand a gene to see the overlapping element, reference biosample, published model score, distance to the target transcription start site, and ENCODE file accession. Choose **Immune system** to restrict results to ENCODE biosamples with that ontology classification. **Gene knowledge** opens the predicted target’s evidence. Display coordinates start at one. GUIDE-IEI uses the published ENCODE links without an additional score cutoff. ENCODE selected its threshold to recover 70% of experimentally supported enhancer–gene links in its CRISPR benchmark. [ENCODE-rE2G manuscript](https://www.nature.com/articles/s41586-026-10781-4)

The data describe an element’s predicted target in a reference biosample. The score applies to that link. Determining whether the patient's allele alters gene regulation requires additional evidence. A locus can have several targets, including genes beyond the nearby-gene table. Lookup also works for previously annotated GRCh38 variants and requires no SCREEN overlap.

In whole-genome **Variant Review → Filters**, the search box matches partial text in the annotated gene symbol, HGVS, variant ID, locus or sample as you type. Both gene matching modes keep these matches. With a gene set selected, an annotated match must also have a VEP gene in the set.

Choose **Annotated + regulatory targets** to include linked variants. Enter a full target symbol such as `NFKB1` to find rows labeled **Regulatory target: NFKB1**, even when the VEP gene differs. With a gene set selected, that target symbol must belong to the set. If the text also matches a row’s HGVS, variant ID, locus or sample, any linked target in the set qualifies for that row. Leave the box empty to find all matches for the set.

The regulatory tab starts with HIGH, MODERATE, LOW and MODIFIER selected. Each tab remembers its own impact selection during the review. In **Annotated + regulatory targets**, gene-less intergenic and regulatory consequences are eligible for lookup even when VEP leaves MANE and PICK blank. **Clinical transcripts** still filters transcript annotations, and your quality, frequency and impact filters still apply.

**Cohort Search → Predicted target gene** searches variants retained in the cohort index. A compact import can have discarded noncoding variants before this search. For sequence-resolved indels the overlap uses the changed bases after trimming shared sequence. Insertions qualify when the breakpoint falls inside an element. Symbolic alleles are unsupported.

The snapshot uses the 1,458 standard DHS-model, upstream-thresholded ENCODE prediction files. Each file’s original accession identifies its source biosample and ENCODE analysis. [Published manuscript](https://doi.org/10.1038/s41586-026-10781-4) · [ENCODE pipeline](https://www.encodeproject.org/pipelines/ENCPL835OUC/).

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
