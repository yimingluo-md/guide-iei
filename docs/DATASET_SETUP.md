---
title: "Annotation dataset setup"
parent: Reference
nav_order: 2
---

# Annotation dataset setup

[Manual home](index.md)

The easiest route is inside the application: **Run VEP first → Set up
annotation datasets**. One-click actions install the recommended automatic
downloads for either exome or whole-genome analysis and refresh changing public
sources such as ClinVar. dbNSFP registration and licensed PromoterAI setup are
shown separately with direct source links and guided instructions. SCREEN
Registry V4 cCRE regions and the hg19 input bundle ship with the native
release; LoGoFunc remains an optional research annotation. Each dataset card
explains in plain language what the dataset adds; the technical details behind
every card are in the [dataset reference](dataset-reference.md), and screen
behavior in [Reference setup](REFERENCE_SETUP.md).

Everything below is the equivalent command-line route.

## 1. Build the container image (once)

```bash
bash docker/build.sh
```

(`scripts/setup_environment.sh --install` offers this too.)

The application's one-click recommended-dataset action performs this check
automatically: when the native `bgzip`, `tabix`, and `samtools` set is absent
and the configured local image has not been built, it builds the image before
starting large downloads. Direct command-line users can still build it
explicitly as shown above.

## 2. Freely downloadable references

```bash
# VEP cache, FASTA, LOFTEE, required SpliceAI MANE SNVs, RepeatMasker,
# SegDup, hg19->hg38 chain
bash scripts/download_references.sh config/annotation.config.yaml
```

Large downloads are fetched from a pinned, checksum-verified Hugging Face
mirror first (much faster than the canonical EBI/Ensembl servers), falling
back to the canonical sources automatically. Set `IEI_REFERENCE_MIRROR=off` to
force canonical sources only.

The one-click installer runs the mirror-backed and canonical-source reference
groups in two bounded parallel lanes. This changes only scheduling: the
SpliceAI source, payload, validation, and download connection count are not
changed. Docker fallback uses only the locally built project image and never
pulls a same-named image implicitly from a registry.

## 3. dbNSFP (registration required, ~52 GB)

dbNSFP is **not** auto-downloaded: register at
[dbnsfp.org/download](https://www.dbnsfp.org/download) for an academic access
code, then download the single GRCh38 BGZF file
(`dbNSFP5.4a_grch38.gz` + `.tbi` + `.md5`) linked from your instruction email
and install it:

```bash
bash scripts/prepare_dbnsfp.sh /path/to/download_folder
#   (verify + install, no rebuild; legacy per-chromosome ZIPs still work)
```

Advisory only — report whether a newer academic dbNSFP release exists:

```bash
python3 pipeline/check_dbnsfp_version.py --config config/annotation.config.yaml
```

## 4. PromoterAI (licensed, optional)

PromoterAI is licensed by Illumina and is not downloaded or shipped. After
obtaining `tss.tsv` + `promoterAI_tss500.tsv.gz` from Illumina, prepare them
locally:

```bash
bash scripts/prepare_promoterai.sh /path/to/PromoterAI
```

The same preparation is available on the Annotation datasets screen.

## 5. LoGoFunc (optional research annotation)

Optional LoGoFunc missense-mechanism predictions can be downloaded from Zenodo
in the Annotation datasets screen, or from the command line:

```bash
bash scripts/download_logofunc.sh config/annotation.config.yaml
# If the 3.66 GB source is already present, validate/link it without copying:
bash scripts/prepare_logofunc.sh /path/to/LoGoFunc
```

## 6. CADD whole-genome scores (optional, WGS only, ~83 GiB)

Coding-region CADD scores are already included in dbNSFP; this optional
download adds CADD for **non-coding** whole-genome positions. It can be
downloaded/resumed from the dataset setup screen. The action fetches only the
official score-only SNV and gnomAD r4.0 indel tables, indexes, and MD5 files,
verifies them, and uses the standard VEP CADD plugin directly. It never
downloads the much larger `inclAnno` tables or creates a duplicate combined
VCF.

## ClinVar and ClinGen stay fresh automatically

The latest ClinVar is fetched (version-stamped) on every annotation run, and
**Refresh changing sources** in the UI (or
`scripts/update_refreshable_datasets.sh`) updates ClinVar + ClinGen expert
variant curations on demand.

Next: [Running annotation](RUNNING_ANNOTATION.md)
