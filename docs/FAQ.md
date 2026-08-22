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

Yes — directly. A file with 16 or more samples opens in **cohort review
mode**: one row per variant, the usual filters, and a per-variant
**Carriers** panel listing which individuals carry it with their genotype
evidence. For a large fresh cohort file, import through the **Whole
genome** analysis scope so the local service prefilters it first; a stored
cohort reopens from the Sample Library (Select all → Open combined
review), which also supports opening one individual or a selected subset,
and removing selected datasets in bulk.
**Cohort search** complements this with genotype-first queries — carriers
of a gene or an exact variant across everything indexed. One bound: an
unfiltered cohort where common variants are carried by nearly everyone can
exceed the browser's carrier capacity; the import then directs you to the
population-frequency prefilter instead of freezing. See the
[cohort analysis chapter](guide/08-cohort-search.md).

## Where do I ask a question that is not answered here?

Open an issue on the
[GitHub repository](https://github.com/yimingluo-md/guide-iei/issues).
