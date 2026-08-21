---
title: "Running annotation"
parent: Reference
nav_order: 3
---

# Running annotation

[Manual home](index.md)

## From the application (recommended)

Open **Import VCF → Run VEP first**. After selecting files, the next screen
shows which required and optional annotation datasets are available and
provides the per-run switches and performance settings. Users do not need to
open or edit YAML.

## From the command line

```bash
# annotate a single- or multi-sample VCF
bash scripts/run_annotation.sh \
    -i /path/to/sample.vcf.gz \
    -o results/sample.vep.vcf.gz \
    -c config/annotation.config.yaml

# Legacy GRCh37/hg19 VCF (conversion occurs before GRCh38 region filtering):
bash scripts/run_annotation.sh \
    -i /path/to/legacy.grch37.vcf.gz \
    -o results/legacy.vep.vcf.gz \
    -c config/annotation.config.yaml \
    --input-assembly GRCh37
```

The run will:

1. retain `FILTER=PASS` and unfiltered (`FILTER=.`) records — per VCFv4.x,
   `.` means site filtering was not applied, which is treated as missing
   evidence rather than failure; records with explicit failure labels are
   excluded — and **restrict the input to coding exons + splice sites** (defaults; builds the BED once from the
   release-matched Ensembl GTF — see *Scope* below),
2. retain all transcript consequences, flag the preferred consequence per ALT
   allele and gene, annotate MANE transcript status for the review UI, and
   download the latest ClinVar (version-stamped),
3. (re)build the ClinVar amino-acid-match catalog if ClinVar changed,
4. build the VEP command from your config and run it in the container,
5. write `results/sample.vep.vcf.gz`,
6. recompute the frameshift PTC 50-bp rule and run sample-specific Haplosaurus
   consequence post-processing, then
7. write `results/sample.vep.aamatch.vcf.gz` (+ tabix index) with the
   `ClinVar_path_aa_match` flag added.

Useful flags: `--dry-run` (print the assembled container command and stop),
`--no-clinvar` (skip the per-run ClinVar download), `--all-variants` (annotate
**every** variant, not just coding+splice — for WGS / non-coding work; see
*Scope* below), `--include-filtered` (retain even explicitly failed calls for deliberate
review/debugging), and `--input-assembly GRCh38|GRCh37|auto`. GRCh38 is the
canonical annotation/cohort assembly; GRCh37 is converted with the
assembly-gap-aware BCFtools/liftover plugin before any GRCh38 region filter,
while `auto` refuses ambiguous headers. See [GRCh37 input](GRCH37_INPUT.md).
The PASS default can also be changed with `run.pass_only`.

## Scope: coding/exome (recommended for a workstation) vs whole-genome

**By default the pipeline restricts annotation to coding exons + splice
sites**, and this is the recommended mode when running on a PC/workstation.
The restriction is a `bcftools view -R <bed>` pre-filter on the input VCF
*before* VEP (`region.coding_only: true`), so VEP only ever processes
on-target variants. The BED is built once from the CDS features of the
**release-matched Ensembl GTF** (same Ensembl release as your VEP cache, so
coordinates line up exactly), with each exon padded by `region.padding_bp`
(default **8 bp**). That 8 bp window captures the essential/consensus splice
sites — it matches both the splice-consensus window dbNSFP itself annotates
(−3 to +8) and the Sequence Ontology `splice_region_variant` definition
(3 exonic / 8 intronic); the essential GT-AG dinucleotides (±1–2) are a subset.

**Why this matters.** The cost driver is variant count, not genome size:

- A whole-genome VCF is typically **~4–5 million** variants; a whole-exome VCF
  is **tens of thousands** after the coding+splice restriction removes ~98–99%.
- Runtime scales with the count. Calibrated on a 64-CPU HPC node with this
  exact plugin stack, VEP annotates **~3.7 million variants/hour** (≈0.27 h per
  million): a 2.43M-variant set took ~32 min, a 47.3M-variant joint call set
  took ~12.6 h. A workstation with 8–18 cores runs several-fold slower per core
  and, more importantly, is memory- and I/O-bound with the large tabix plugins
  (dbNSFP, SpliceAI) mmapped.
- So the coding+splice subset (typically **1–2%** of a WGS callset) is what
  turns a multi-hour, large-RAM HPC job into **minutes** on a
  laptop/workstation.

**Whole-genome / non-coding annotation remains substantially more expensive.**
It is supported from the Import page or with `--all-variants`. WGS intake
validates an existing BGZF/tabix or CSI pair and otherwise creates a sorted,
indexed working copy without changing the submitted VCF. VEP then uses the
configured parallel workers. Expect long annotation walltimes on a
workstation; full-genome score generation still belongs on suitable HPC/GPU
infrastructure. Reviewing an already-annotated WGS VCF is far cheaper — see
[the review workbench](REVIEW_WORKBENCH.md).

**Targeted panels / custom exome capture:** point `region.custom_bed` at your
own BED (gene panel, capture kit) and it is used verbatim instead of the built
coding BED.

## Input conventions

GRCh38 VCFs using UCSC-style `chr1`/`chrM` contig labels are normalized in a
workspace copy to the Ensembl cache convention (`1`/`MT`) before region
filtering. Coordinates, alleles, FORMAT fields, and genotypes are unchanged.

GRCh37/hg19 inputs are never mixed directly into this step. The controlled
intake writes a derived GRCh38 VCF, a reference-correction audit VCF, an
unsupported-record VCF, a liftover-reject VCF, QC JSON, and provenance JSON
while preserving the original locus in INFO. A source ALT that becomes the
GRCh38 reference is excluded from annotation only when every called sample is
reference after genotype-aware allele remapping. The original input is not
modified. Re-alignment/re-calling against GRCh38 is preferred when reads are
available. See [GRCh37 input](GRCH37_INPUT.md).

## Configuration

For scripted/CLI use and administrator reference locations,
`config/annotation.config.yaml` is the default template. See
[Annotation sources](ANNOTATIONS.md) for what every source is, which tier it
belongs to, and how to obtain it.

Next: [The review workbench](REVIEW_WORKBENCH.md)
