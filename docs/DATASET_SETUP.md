---
title: "Annotation dataset setup"
parent: Reference
nav_order: 2
---

# Annotation dataset setup

[Manual home](index.md)

The easiest route is inside the application: **Annotate VCF → Set up
annotation datasets**. One-click actions install the recommended automatic
downloads for either exome or whole-genome analysis and refresh changing public
sources such as ClinVar. dbNSFP registration and licensed PromoterAI setup are
shown separately with direct source links and guided instructions. SCREEN
Registry V4 cCRE regions and the hg19 input bundle ship with the native
release; LoGoFunc remains an optional public research annotation, and FuncVEP
is an optional licensed archive that GUIDE-IEI can download after the user
reviews and acknowledges the upstream terms. An existing official ZIP can be
selected instead. Each dataset card
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

Register at
[dbnsfp.org/download](https://www.dbnsfp.org/download) for an academic access
code. Copy the link whose filename ends in `_grch38.gz` from the instruction
email and paste it into the dbNSFP card; do not use the similarly named
`_grch37.gz` link. The release version may change over time. GUIDE-IEI
automatically derives the `.tbi` and `.md5`
companions, downloads all three with eight resumable connections, verifies the
published checksum and index, and installs them. Outlook Safe Links are
accepted; the private link is not retained in logs or configuration.

Advanced users who already have a complete local release can still run
`scripts/prepare_dbnsfp.sh /path/to/download_folder`. Legacy per-chromosome ZIP
releases remain supported through that command-line path.

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

## 6. FuncVEP (optional, licensed archive)

Review the supported official FuncVEP archive's
[PolyForm Strict License 1.0.0](https://polyformproject.org/licenses/strict/1.0.0)
for your intended use. The upstream terms permit qualifying noncommercial use
but do not grant distribution or software-modification rights; the Zenodo record does not expose
a separate dataset-license field, so confirm that your local use is authorized.
In the Annotation datasets screen, acknowledge the terms and select **Download
and install FuncVEP**. GUIDE-IEI resumably downloads the pinned archive from
the [official Zenodo record](https://zenodo.org/records/20595206), verifies its
published checksum, extracts only the three FuncVEP scores, and builds a GRCh38
tabix index. An existing official ZIP can be selected instead. All processing
is local; the archive and its contents are never uploaded or redistributed.
Allow at least 24 GiB free for the automatic download and preparation.

Command-line equivalents:

```bash
# Automatic official download and preparation after reviewing the terms:
bash scripts/download_funcvep.sh \
  config/annotation.config.yaml --acknowledge-license

# Or prepare an existing official ZIP:
bash scripts/prepare_funcvep.sh \
  /absolute/path/to/FuncVEP_and_ClinVEP_scores_all_possible_missense_variants.zip \
  config/annotation.config.yaml --acknowledge-license
```

The resulting annotations require an exact genomic allele plus stable Ensembl
gene ID match. The upstream archive's ClinVEP columns are intentionally not
prepared or integrated.

## 7. CADD whole-genome scores (optional, WGS only, ~83 GiB)

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
