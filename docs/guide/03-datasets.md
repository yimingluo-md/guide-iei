---
title: "Set up the annotation datasets"
parent: User Guide
nav_order: 3
---

# Set up the annotation datasets

The annotation engine is only as informative as its reference data. This
chapter covers the one-click setup, the two datasets that require a request
to their providers (dbNSFP registration and the PromoterAI license), and the
optional datasets — with disk-space planning first.

All of this is done inside the application, at **Run VEP first → Set up
annotation datasets**. Each dataset is presented as a card describing in
plain language what it contributes; this chapter is a tour of those cards.

![The dataset setup screen: engine and reference status, one-click download tiles for the exome and WGS profiles, and the registration-gated dbNSFP and PromoterAI cards](../assets/img/dataset-cards.png)

## Plan disk space first

| Dataset | Size | Contributes |
|---|---|---|
| Core references (VEP cache, genome FASTA, LOFTEE data, region tracks) | ~30 GB | required for all analyses |
| dbNSFP (registration required) | ~52 GB | protein-effect predictors |
| SpliceAI MANE SNV scores | 27 GB | splice predictions |
| ENCODE SCREEN + tissue/immune contexts | ~2 GB | WGS regulatory review |
| PromoterAI (licensed; recommended for WGS) | <1 GB | promoter predictions |
| CADD whole-genome (optional) | ~83 GB | non-coding CADD only |
| LoGoFunc (optional) | ~4 GB | GOF/LOF missense mechanism |

As a rule of thumb, **~110 GB accommodates exome work; 150–250 GB the full
whole-genome stack.** The datasets need not reside on the internal drive:
the **Storage** page can place them on an external SSD, and the application
verifies free space before each large download rather than failing partway.

Downloads are fetched from a checksum-verified fast mirror first, with the
official sources as automatic fallback. Initial setup takes an hour or two
on a typical connection, and interrupted downloads resume rather than
restart.

## Step 1 — One click for the public datasets

Two actions install everything that is freely downloadable:

- **Recommended for exome** — the core references plus the datasets used in
  exome review.
- **Recommended for WGS** — the exome set plus what whole-genome review
  adds (SpliceAI genome-wide context, the ENCODE SCREEN regulatory data
  with tissue and immune contexts). PromoterAI is also recommended for
  WGS, but because it is licensed it cannot be included in the one-click
  download — it is set up once in Step 3 below.

Progress is reported per dataset. A third action, **Refresh changing
sources**, updates ClinVar and ClinGen curations on demand; ClinVar is
additionally refreshed at every annotation run, so annotations never rest
on a stale snapshot.

## Step 2 — dbNSFP: the one registration

dbNSFP assembles the field's protein-effect predictors (AlphaMissense,
CADD, REVEL, SIFT, PolyPhen-2, and others) into a single resource. It is
free for academic use, but its license does not permit redistribution, so
each user registers once:

1. At **[dbnsfp.org/download](https://www.dbnsfp.org/download)**, request
   the free **academic** access using an institutional email address.
2. The instruction email links the current academic release. Download the
   three GRCh38 files of the single-file release —
   `dbNSFP5.4a_grch38.gz`, its `.tbi` index, and its `.md5` checksum —
   into one folder (~52 GB; a download manager such as `aria2c` is
   convenient, but a browser suffices).
3. On the dataset screen, point the **dbNSFP** card at that folder. The
   application verifies the checksum, validates the format, and installs
   the file; no rebuild is needed.

![The dbNSFP card: choose the downloaded folder and the workstation validates and installs it; the PromoterAI card follows the same pattern](../assets/img/dataset-dbnsfp-card.png)

## Step 3 — PromoterAI: recommended for whole-genome work

PromoterAI ([Illumina, *Science* 2025](https://www.science.org/doi/10.1126/science.ads7373))
predicts whether a promoter variant alters expression of its gene; scores
range from −1 (under-expression) to +1 (over-expression). The precomputed
scores are **free for academic and non-commercial research** but licensed
by Illumina, so GUIDE-IEI neither downloads nor redistributes them. The
one-time setup:

1. At the
   **[Illumina PromoterAI repository](https://github.com/Illumina/PromoterAI)**,
   follow the access instructions for the precomputed scores: complete the
   non-commercial license agreement, after which the download link arrives
   by email. (Commercial use is licensed separately by Illumina.)
2. Download the two files — `tss.tsv` and `promoterAI_tss500.tsv.gz` —
   into one local folder.
3. On the dataset screen, open the **PromoterAI** card and select that
   folder. Validation and installation are automatic; nothing is uploaded
   anywhere, and the source files are removed only after installation
   succeeds.

PromoterAI annotation applies to whole-genome analysis only, promoters
lying outside the exome's coding scope.

## Step 4 — Optional datasets

- **CADD whole-genome** (83 GB). Coding-region CADD scores are already
  present in dbNSFP; this large download adds CADD for **non-coding**
  positions only, as a complement to SpliceAI, PromoterAI, and cCRE
  evidence in whole-genome review.
- **LoGoFunc**
  ([Stein et al., *Genome Medicine* 2023](https://genomemedicine.biomedcentral.com/articles/10.1186/s13073-023-01261-9)).
  Predicts whether a pathogenic missense variant acts through gain or loss
  of function — mechanistic context that frequency and deleteriousness
  scores do not provide. One click from Zenodo (~4 GB).

Already bundled with the software, requiring no download: the ENCODE SCREEN
cCRE regions, the GRCh37→GRCh38 conversion data, and the gene-knowledge
resources (gnomAD constraint, HGNC, the IUIS IEI classification, and
ClinGen gene–disease validity and dosage). Licensed OMIM files are never
downloaded, but an institution's own copy can be indexed under **Gene
knowledge**.

## Verifying the installation

The dataset screen itself is the status display: each card reports
installed-or-missing, with versions. For deeper verification, the
regression panel annotates eight public control variants and asserts the
expected value from every installed predictor
([Quality control](10-quality-control.md)):

```bash
bash scripts/run_annotation_regression.sh
```

Technical reference: [Annotation dataset setup](../DATASET_SETUP.md) and
[Dataset reference](../dataset-reference.md).

Next: [Your first exome](04-first-exome.md)
