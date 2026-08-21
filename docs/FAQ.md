---
title: FAQ
nav_order: 4
---

# FAQ

[Manual home](index.md)

## Can GUIDE-IEI analyze somatic variants?

Not in the current release. GUIDE-IEI is a germline analysis platform: its
filters and population-frequency logic assume constitutional variants, and it
is not a tumor pipeline. Somatic and mosaic variants do matter in IEI — for
example, somatic *FAS* variants in ALPS and mosaic *NLRP3* variants in CAPS
are well described — but reliably detecting them requires appropriate
upstream sequencing depth and calling.

A **simple allele-fraction filter** is planned: it would use the sample's
allele depth (AD/DP) to surface variants whose allele fraction departs from
germline expectations (roughly 50% heterozygous / 100% homozygous), flagging
possible somatic or mosaic events for manual review. Allele fractions from a
standard germline caller are suggestive, not diagnostic; deep targeted
sequencing remains the appropriate confirmation.

## Does GUIDE-IEI ever send data off my machine?

Annotation and review are fully local: patient VCFs, genotypes, and
phenotype records do not leave the computer. There is **one deliberate,
opt-in exception**: on the variant page, an indel with no precomputed
SpliceAI score offers a **"Get SpliceAI score online"** action. Invoking it
transmits that single variant's position and alleles (chromosome, position,
REF, ALT — nothing else) to the Broad Institute's public SpliceAI Lookup
service and displays the returned scores, clearly labeled as an online
result. The lookup never runs automatically, never in batch, and each
result is stored locally so a variant is transmitted at most once. Dataset
downloads and ClinVar refreshes also use the internet, but these transfer
only public reference data *to* the machine.

## Can I review a multi-sample cohort VCF?

Yes — through **Cohort search**, which is the workflow built for cohorts.
The review workspace opens a complete file for a single patient or a small
family; a joint-called cohort (tens of samples) exceeds what a browser can
hold in memory no matter how the file is prepared, so cohort-scale files
are refused with directions rather than left to freeze the tab. The
designed path: index the annotated cohort VCF once in **Cohort search**
(the records live in a local database, not the browser), then query
carriers of a candidate gene or an exact variant, and send the matched
findings into the review workspace — only the matched records, with their
complete evidence, are ever opened.

## Where do I ask a question that is not answered here?

Open an issue on the
[GitHub repository](https://github.com/yimingluo-md/guide-iei/issues).
