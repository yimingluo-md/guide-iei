---
title: "Trio analysis"
parent: User Guide
nav_order: 6
---

# Trio analysis

For a suspected monogenic IEI, parental samples convert two questions that
a singleton exome cannot answer into routine filters: *is this variant de
novo?* and *are these two heterozygous variants in trans?* The first
carries much of the diagnostic yield in sporadic severe disease; the
second is the difference between a compound-heterozygous diagnosis and two
coincidental carrier alleles. GUIDE-IEI's family analysis answers both
from the genotype evidence already in the VCF — and is explicit about the
confidence each answer deserves.

## Input: joint genotyping matters

The strongly preferred input is a single, jointly genotyped VCF containing
proband and both parents, carrying per-sample genotypes with depth,
quality, allele depths, and — when the callset is phased — phase sets. The
reason is epistemic, not technical: a jointly genotyped file states each
parent's genotype *at every site*, so a parental `0/0` is an actual call
with measurable support.

Separate single-sample VCFs can be opened together, but a site absent from
a parental file is **not** a reference call — the parent may simply have
no record there, for any of several reasons upstream. The analysis
therefore never grades such a candidate above **possible de novo**. This
is the same principle the cohort chapters apply to carrier counts: absence
of a record is not evidence of absence of the allele. Where a stronger
claim is needed, joint-genotype the gVCFs upstream, or verify the site in
parental alignments outside this software.

## Setting up the trio

Open **Family analysis** and either assign the three roles to VCF sample
names directly or upload a standard six-column PED/FAM file (sample names
must match the VCF header exactly). Two things the setup deliberately does
*not* do: the affected-status column is parsed but not used to rank
anything, and the **Parental relationships confirmed** checkbox records
reviewer context only — the software performs no kinship testing, and
accordingly never assigns ACMG/AMP PS2 or PM6 on your behalf. If
non-paternity or sample swap is a live possibility, that must be resolved
by other means; the analysis assumes the pedigree you assert.

## De novo screening: what the tiers assert

Each proband carrier receives one of six labels, and the labels are claims
about *evidence*, not about biology:

- **High-confidence de novo candidate** — the child carries the allele,
  the transmitting parent or parents have well-supported reference calls,
  and depth, quality, and allele-balance thresholds are met.
- **Possible de novo** — the model fits, but some parental measurement is
  missing or insufficient; the gap is named.
- **Possible parental mosaicism** — a nominally reference parent shows
  alternate-allele reads above the screening limit. In IEI this tier
  deserves attention rather than dismissal: parental mosaicism both
  explains recurrence risk and is itself well described for several genes.
- **Likely artifact** — the proband's own evidence (depth, quality, or
  allele balance) fails the screen.
- **Mendelian conflict** — the genotypes fit no simple de novo model;
  consider genotyping error, CNV overlap, or sample identity.
- **Inherited** — a transmitting parent carries the allele.

The thresholds behind these tiers (defaults: DP ≥ 10, GQ ≥ 20, proband
allele balance 0.20–0.80, parental alternate reads ≤ 2%) are screening
values, adjustable in the panel — not clinical rules. The variant page
always shows the unmodified genotype, depths, quality, allele balance, and
phase set for all three samples, so the tier can be checked against its
own inputs in one glance.

## Sex chromosomes and mitochondria

X-linked disease (*BTK*, *WAS*, *IL2RG*, and many others) is common in
IEI, and a naive diploid trio model mishandles exactly those genes: a de
novo hemizygous variant written as `1/1` looks like a Mendelian conflict,
and a haploid call at allele balance ~1.0 looks like an artifact. The
analysis therefore models regions with a single transmitting parent
explicitly:

- On **non-pseudoautosomal X in a male proband**, only the mother's
  genotype gates the de novo call, the hemizygous genotype shapes are
  accepted, and the allele-balance upper bound is waived. Inside the
  pseudoautosomal regions the diploid model applies, as it should.
- On **Y**, only the father's genotype is informative.
- **Mitochondrial** variants are treated as maternally transmitted for a
  proband of either sex — a paternal call never marks one inherited — with
  allele-balance gating relaxed for heteroplasmy.

Proband sex is taken from the PED file or set in the mapping panel; left
unknown, the conservative diploid model applies. One consequence for
compound heterozygotes follows directly: in a male proband, a
"compound het" on non-PAR X is biologically impossible — one haplotype —
so such pairs are excluded with that stated reason rather than offered as
candidates; heterozygous-appearing calls there usually mean artifact or an
overlapping CNV.

## Compound heterozygotes: reading phase

Heterozygous proband variants that survive the active filters are grouped
by gene, and each pair is classified by the strength of its trans
evidence: **confirmed trans by inheritance** (one maternal, one paternal),
**confirmed trans by phasing** (opposite haplotypes in one phase set),
**possible trans** (for example, one inherited variant plus one de novo
candidate, without physical phase), **phase unknown**, and **cis /
excluded** (same haplotype, same transmitting parent, or a hemizygous
region — which rules the pair out and is as valuable as a confirmation).
A confirmed-trans pair is a candidate mechanism, not a diagnosis:
transcript compatibility, read evidence, and the gene's disease mechanism
still decide whether two variants in trans amount to biallelic loss.

## Current boundaries

One trio is active per session; separate gVCFs are not joint-genotyped by
the workbench; and read-level review, kinship verification, and
read-backed phasing are not integrated. Beyond the hemizygous and
mitochondrial handling above, fuller sex-aware reasoning — X-inactivation,
carrier-mother segregation across larger pedigrees — awaits a later
pedigree extension.

Technical reference: [Trio analysis (reference)](../TRIO_ANALYSIS.md).

Next: [Phenotypes and the Sample Library](07-phenotypes-and-library.md)
