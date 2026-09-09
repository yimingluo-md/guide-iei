---
title: "AlphaGenome AVI"
parent: Reference
nav_order: 19
---

# AlphaGenome AVI

AlphaGenome AVI provides precomputed genome-wide SNV impact scores for local
whole-genome review, including coding, non-coding and intergenic alleles.
GUIDE-IEI currently integrates the AVI SNV score dataset, not live AlphaGenome
model inference or the Atlas's tissue-specific outputs.

The underlying AlphaGenome model is described in
[Advancing regulatory variant effect prediction with AlphaGenome](https://www.nature.com/articles/s41586-025-10014-0)
(*Nature*, 2026). The separately released AVI dataset and its access terms
are documented on the [official downloads page](https://deepmind.google.com/science/alphagenome/downloads).

## Setup

AVI is included in **Recommended for WGS**. It is not required to run an
annotation, and is not part of the exome setup. CADD whole-genome remains
optional and is not installed by either recommended action.

Review the [AlphaGenome terms](https://deepmind.google.com/science/alphagenome/terms)
and [official downloads page](https://deepmind.google.com/science/alphagenome/downloads)
before installing. AVI SNV scores are identified as a permissive downloadable
artifact for commercial and non-commercial use, subject to those terms—not
an unrestricted clinical-use license. Other Atlas artifacts and API access
have separate conditions. No API key is needed for AVI installation.

Choose **Download / resume** on the AlphaGenome AVI card. This installs the
[preprocessed GUIDE-IEI mirror](https://huggingface.co/datasets/luoyiming1991/alphagenome-avi-grch38)
directly: **75.8 GB downloaded**, about **70.6 GiB installed**, with an
**80 GiB free-space allowance** for a fresh installation. It uses the
Annotation datasets location selected in **Storage → Where data live**.
There is no ZIP chooser, source-archive download, or local conversion in the
user installation workflow. No Hugging Face account or API key is needed.

Keep the computer awake during transfer. Interrupted downloads resume from
verified ranges when you retry the same button. Files are verified against
pinned sizes and SHA-256 checksums before the installation becomes ready.
If the mirror is temporarily unavailable, retry later; GUIDE-IEI does not
fall back to the large source ZIP. A damaged previous installation is retained
in a `.damaged-*` directory during replacement, which requires additional
space for the new copy. Existing source archives are not changed or deleted.

Command-line equivalent:

```bash
bash scripts/download_avi.sh config/annotation.config.yaml
```

The destination is `custom_tracks.AlphaGenomeAVI.dest_dir`. An installation
is not advertised as ready until all downloads and checksums finish.
The VEP track is disabled in the stock command-line config; enable it after
preparation. The UI enables installed AVI by default for WGS runs, while
allowing it to be turned off for a particular run.

The download log shows file transfer, checksum verification, and activation
as separate stages. To recheck an installed bundle's full checksums:

```bash
python3 pipeline/avi_dataset.py status references/alphagenome-avi --strict
```

## Verified source structure and transformation

The September 8 archive stores a BGZF TSV and its TBI without outer ZIP
compression. Its columns are `#CHROM POS REF ALT raw_score PHRED`. The TBI
declares **8,812,917,339 SNV rows**, on chromosomes 1–22, X and Y; mitochondrial
and alternate contigs are not included. Its VCF-style Tabix preset does not
make the TSV itself a valid VCF.

The preparer validates the pinned member size/CRC metadata, index layout,
header, coordinate/allele structure, sorted order, finite numeric values,
and exactly three distinct ALT scores at each position. BGZF decompression
checks block CRCs. Full-source SHA-256 is recorded in the completed manifest.

GUIDE-IEI groups the three rows into **one record per position**, preserving
every upstream score's decimal text:

```text
#CHROM POS   ID REF ALT   QUAL FILTER INFO
1      10001 .  T   A,C,G .    .      raw=-0.03868,-0.032,-0.0372;phred=1.06466,1.3114,1.11839
```

The actual file is tab-separated, BGZF-compressed VCF with `raw` and `phred`
declared `Number=A,Type=Float`. ALT order is A,C,G,T excluding REF. The `chr`
prefix is removed; positions remain 1-based GRCh38. There is no liftover,
rounding, gene assignment, or score-based filtering.

## Annotation and review

The registry ID is `alphagenome_avi`. VEP uses an **exact chromosome, position,
REF and ALT match**, not nearest-gene distance or transcript matching:
`format=vcf,type=exact,short_name=AlphaGenomeAVI,fields=raw%phred`.
VEP emits `AlphaGenomeAVI_raw` and `AlphaGenomeAVI_phred` into CSQ, including
intergenic consequences. The selected ALT receives only its own score.

AVI Phred is visible by default in WGS Variant Review. Raw AVI is optional
in Display settings. Zero and negative raw values are preserved; missing,
malformed, or conflicting values are not treated as zero. Both scores travel
through cohort predictor observations and the review TSV export. Annotation
QC reports eligible primary-contig SNV alleles and scored alleles separately
from transcript-specific predictors.

AVI is an impact prediction, not a clinical P/LP classification. Do not count
correlated predictor outputs as independent evidence. **No AVI-based intake
or retention route is added**: AVI cannot rescue a variant already excluded
by the existing WGS intake rules.

## Published mirror and provenance

Preparation creates an immutable release directory under
`references/alphagenome-avi/releases/atlas-2026-09-08-vcf-v1/` (or the selected
annotation-storage root) with:

- `avi.grch38.vcf.gz`
- `avi.grch38.vcf.gz.tbi`
- `manifest.json` — source identity, SHA-256s, sizes, counts and transformation
- `SOURCE_AND_TERMS.txt` — attribution, upstream source and terms references

`current.json` is the small local installed-release pointer, not a mirror
payload. The large files are separate rather than nested in another ZIP,
so the installer fetches/resumes them without conversion or extraction.

The downloader pins mirror revision
`b1e9bf7d332b8e68515a639e0e6d91bed43abce5` and independent per-file checksums
in `pipeline/avi_mirror.py`. It does not follow the repository's moving `main`
branch. The original published manifest is retained as `mirror-manifest.json`;
the local `manifest.json` adds download provenance and local file timestamps.
The VEP
job-start integrity check hashes the prepared files; readiness polling uses
their size and recorded modification time, hashing if those timestamps change.

The source converter (`scripts/prepare_avi.sh`) is retained for maintainer
reproducibility only, not offered as a user installation option. It preserves
the source ZIP and generates the lossless VCF layout described above.
