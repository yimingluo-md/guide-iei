---
title: "Set up the annotation datasets"
parent: User Guide
nav_order: 3
---

# Set up the annotation datasets

The annotation engine is only as good as its reference data. This chapter
covers the one-click setup, the two datasets that need a request to their
providers (dbNSFP registration, PromoterAI license), and the optional
extras — with disk-space planning up front.

Everything here happens inside the application: **Run VEP first → Set up
annotation datasets**. Every dataset appears as a card explaining in plain
language what it adds; this chapter is the tour of those cards.

*[Screenshot: Set up annotation datasets screen with dataset cards]*

## Plan your disk space first

| What | Size | Needed for |
|---|---|---|
| Core references (VEP cache, genome FASTA, LOFTEE data, region tracks) | ~30 GB | everything |
| dbNSFP (registration required) | ~52 GB | protein-effect predictors |
| SpliceAI MANE SNV scores | 27 GB | splice predictions |
| ENCODE SCREEN + tissue/immune contexts | ~2 GB | WGS regulatory review |
| PromoterAI (licensed; recommended for WGS) | <1 GB | promoter predictions |
| CADD whole-genome (optional) | ~83 GB | non-coding CADD only |
| LoGoFunc (optional) | ~4 GB | GOF/LOF missense mechanism |

Rule of thumb: **~110 GB for exome work; 150–250 GB for the full
whole-genome stack.** Datasets do not need to live on your internal drive —
the **Storage** page lets you place them on an external SSD, and the
application checks free space before every large download rather than
failing halfway.

Downloads come from a checksum-verified fast mirror first, with the
official sources as automatic fallback — expect the initial setup to take
an hour or two on a typical connection, all resumable if interrupted.

## Step 1 — One click for the public datasets

Two buttons install everything freely downloadable:

- **Recommended for exome** — the core references plus the datasets an
  exome review uses.
- **Recommended for WGS** — the exome set plus what whole-genome review
  adds (SpliceAI genome-wide context, the ENCODE SCREEN regulatory data
  with tissue and immune contexts). PromoterAI is also recommended for
  WGS, but because it is licensed it cannot be included in the one-click
  download — set it up once in Step 3 below.

Progress is shown per dataset; interrupted downloads resume rather than
restart. A third action, **Refresh changing sources**, updates ClinVar and
ClinGen curations on demand — and ClinVar is additionally refreshed
automatically at every annotation run, so your annotations never rely on a
stale snapshot.

## Step 2 — dbNSFP: the one registration

dbNSFP bundles the field's protein-effect predictors (AlphaMissense, CADD,
REVEL, SIFT, PolyPhen-2, and many more) into one resource. It is free for
academic use but its license does not permit anyone to redistribute it —
so every user registers once:

1. Go to **[dbnsfp.org/download](https://www.dbnsfp.org/download)** and
   request the free **academic** access with your institutional email.
2. The instruction email links the current academic release. Download the
   three GRCh38 files of the single-file release —
   `dbNSFP5.4a_grch38.gz`, its `.tbi` index, and its `.md5` checksum —
   into one folder. (~52 GB; a download manager like `aria2c` helps but a
   browser works.)
3. On the dataset screen, point the **dbNSFP** card at that folder. The
   application verifies the checksum, validates the format, and installs
   it. Nothing needs to be rebuilt.

*[Screenshot: dbNSFP card with guided source-folder selection]*

## Step 3 — PromoterAI: recommended for whole-genome work

PromoterAI ([Illumina, *Science* 2025](https://www.science.org/doi/10.1126/science.ads7373))
predicts whether a promoter variant alters its gene's expression — scores
run from −1 (under-expression) to +1 (over-expression). Its precomputed
scores are **free for academic and non-commercial research** but licensed
by Illumina, so GUIDE-IEI neither downloads nor redistributes them. The
one-time setup:

1. Go to the
   **[Illumina PromoterAI repository](https://github.com/Illumina/PromoterAI)**
   and follow its access instructions for the precomputed scores: complete
   the non-commercial license agreement, and the download link arrives by
   email. (Commercial use is licensed separately by Illumina.)
2. Download the two files — `tss.tsv` and `promoterAI_tss500.tsv.gz` —
   into one local folder.
3. On the dataset screen, open the **PromoterAI** card and choose that
   folder. Validation and installation are automatic; nothing is uploaded
   anywhere, and the source files are removed only after installation
   succeeds.

PromoterAI annotations apply to whole-genome analysis only (promoters are
outside the exome's coding scope).

## Step 4 — Optional datasets

- **CADD whole-genome** (83 GB). Coding-region CADD scores are already in
  dbNSFP; this large download adds CADD for **non-coding** positions only,
  as a complement to SpliceAI/PromoterAI/cCRE evidence in whole-genome
  review.
- **LoGoFunc**
  ([Stein et al., *Genome Medicine* 2023](https://genomemedicine.biomedcentral.com/articles/10.1186/s13073-023-01261-9)).
  Predicts whether a pathogenic missense variant acts through gain or loss
  of function — mechanism context that frequency and deleteriousness
  scores don't give. One click from Zenodo (~4 GB).

Already bundled with the software, nothing to download: the ENCODE SCREEN
cCRE regions, the GRCh37→GRCh38 conversion data, and the gene-knowledge
resources (gnomAD constraint, HGNC, IUIS IEI classification, ClinGen
gene–disease validity and dosage). Licensed OMIM files are never
downloaded, but can be indexed from your own local copy under **Gene
knowledge** if your institution has access.

## How you know it worked

The dataset screen is itself the status display: every card shows
installed-or-missing, with versions. For a deeper check, the regression
panel annotates eight public control variants and asserts that every
installed predictor produced the expected values
([Quality control](10-quality-control.md)):

```bash
bash scripts/run_annotation_regression.sh
```

Technical reference: [Annotation dataset setup](../DATASET_SETUP.md) and
[Dataset reference](../dataset-reference.md).

Next: [Your first exome](04-first-exome.md)
