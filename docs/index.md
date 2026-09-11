---
title: Home
nav_order: 1
---

# GUIDE-IEI Manual

**G**enomic **U**ser-friendly **I**n-depth **D**iagnostic-analysis
**E**nvironment for **I**nborn **E**rrors of **I**mmunity —
a clinician-developed, locally run, open-source WES/WGS analysis platform.

## Where to start

- **New to GUIDE-IEI?** Start with the
  [User Guide](guide.md): what the software does, how to install it, and a
  complete walkthrough of your first exome analysis. (The chapter list is
  on that page.)
- **Looking for technical depth?** The [Reference](reference.md) section
  documents every annotation source, configuration option, and storage
  detail.
- **Puzzled by a term?** The [Glossary](GLOSSARY.md) defines every genetics
  term the software uses, in plain language.
- **Quick answers:** the [FAQ](FAQ.md).

## About the software

GUIDE-IEI annotates exome and genome VCFs with a curated annotation stack
(Ensembl VEP, LOFTEE, dbNSFP, ClinVar, ClinGen, SpliceAI,
[AlphaGenome AVI](ALPHAGENOME_AVI.md), ENCODE SCREEN
regulatory data, and more), with optional registered-user GenIA gene,
phenotype, and exact-allele evidence, and presents the results in a local review
workbench. All analysis is performed on the user's own computer; patient
variants do not leave the machine, with a single opt-in exception described
in the [FAQ](FAQ.md).

Project overview, motivation, and scope:
[GUIDE-IEI on GitHub](https://github.com/yimingluo-md/guide-iei).

## ACMG/AMP 2015 evidence support

“Supported” indicates evidence-review functions that require expert
interpretation, not automatic assignment of ACMG/AMP criteria, evidence
strengths, or final classifications. Availability depends on installed
datasets and input annotations.

| ACMG/AMP criterion | Supported | GUIDE-IEI functions |
|---|:---:|---|
| PVS1 — Loss of function | Yes | VEP consequences, LOFTEE, NMD assessment, frame-restoring analysis |
| PS1 — Same amino-acid change | Yes | ClinVar/ClinGen/GenIA protein-change matching |
| PS2 — Confirmed de novo | Yes | Trio analysis |
| PS3 — Damaging functional evidence | No | — |
| PS4 — Enrichment in affected individuals | No | — |
| PM1 — Hotspot/critical domain | No | — |
| PM2 — Population rarity | Yes | gnomAD annotation |
| PM3 — In trans in recessive disease | Yes | Compound-heterozygote analysis, trio analysis |
| PM4 — Protein-length change | Yes | VEP consequences, frame-restoring haplotype analysis |
| PM5 — Different pathogenic change at the same residue | Yes | ClinVar/ClinGen/GenIA residue matching |
| PM6 — Assumed de novo | No | — |
| PP1 — Cosegregation | Yes | Trio analysis |
| PP2 — Missense disease mechanism | No | — |
| PP3 — Damaging computational evidence | Yes | Multiple computational predictors |
| PP4 — Phenotype specificity | No | — |
| PP5 — Pathogenic source classification | Yes | ClinVar/ClinGen/GenIA classifications |
| BA1 — High population frequency | Yes | gnomAD annotation |
| BS1 — Frequency exceeds disease expectation | Yes | gnomAD annotation |
| BS2 — Healthy-carrier observation | No | — |
| BS3 — Benign functional evidence | No | — |
| BS4 — Nonsegregation | Yes | Trio analysis |
| BP1 — Missense in a truncating-disease gene | Yes | VEP consequences |
| BP2 — Benign-supporting cis/trans observation | Yes | Determined by review status of other variants |
| BP3 — In-frame indel in a nonfunctional repeat | Yes | VEP consequences, genomic repeat annotation |
| BP4 — Benign computational evidence | Yes | Multiple computational predictors |
| BP5 — Alternative molecular diagnosis | Yes | VEP consequences, gene knowledge |
| BP6 — Benign source classification | Yes | ClinVar/ClinGen/GenIA classifications |
| BP7 — Synonymous without splice impact | Yes | VEP consequences |

PP5 and BP6 are included for completeness under the 2015 framework;
subsequent [ClinGen guidance](https://clinicalgenome.org/tools/clingen-variant-classification-guidance/)
discourages their use without evaluating the underlying evidence.

> **Research use only.** GUIDE-IEI is not FDA-cleared and not CLIA-validated.
> Any variant used in patient care must be independently confirmed and
> validated in a CLIA-certified laboratory.
