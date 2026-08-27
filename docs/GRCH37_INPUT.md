---
title: "GRCh37 input"
parent: Reference
nav_order: 10
---

# GRCh37/hg19 input policy

The pipeline has one canonical annotation and cohort assembly: **GRCh38**.
It does not maintain a parallel GRCh37 VEP cache and plugin database.

## Preferred order

1. If FASTQ, BAM, or CRAM data are available, align and call variants against
   GRCh38. This is preferable for reportable findings because coordinate
   conversion cannot revisit read mapping, local assembly, or callability.
2. If only an hg19/GRCh37 small-variant VCF is available, select
   **GRCh37 / hg19 — liftover to GRCh38** in the workbench or pass:

   ```bash
   bash scripts/run_annotation.sh \
     --input legacy.hg19.vcf.gz \
     --output results/legacy.vep.vcf.gz \
     --input-assembly GRCh37
   ```

3. Annotate, review, and cohort-index the resulting GRCh38 representation.

`--input-assembly auto` uses `##reference`, chromosome-1 contig length, or the
pipeline target marker. It fails if the header is ambiguous. An explicit
choice is accepted for an ambiguous header but rejected when it conflicts with
reliable header evidence.

## Why ordinary coordinate-only liftover is insufficient

Some apparent hg19 variants are differences between the hg19 and GRCh38
reference sequences. For example, an hg19 insertion can become the normal
GRCh38 reference allele across a small assembly gap. Copying the coordinate
while preserving the old REF/ALT can create a false GRCh38 frameshift.

The pipeline therefore uses the published
[BCFtools/liftover](https://github.com/freeseek/score#liftover-vcfs)
implementation (Genovese et al., *Bioinformatics* 2024,
[doi:10.1093/bioinformatics/btae038](https://doi.org/10.1093/bioinformatics/btae038)).
Both the source hg19 FASTA and destination GRCh38 FASTA are supplied. The
plugin can bridge small chain gaps, recognize allele swaps, and remap GT plus
Number=A/R/G annotations such as AD and PL.

BCFtools/liftover 1.20 cannot remap non-diploid or String `Number=G` FORMAT
arrays when the destination reference introduces a new allele. During GRCh37
intake, the pipeline therefore removes only those incompatible fields on the
affected record (for example haploid `PL`, `GP`, or `PRI`). It retains `GT`,
`AD`, `DP`, `GQ`, and every compatible field. Malformed allele-indexed FORMAT
values are handled with the same record-local policy rather than aborting the
entire VCF or dropping the tag globally. Counts by field are written to the
liftover QC and provenance sidecars.

## Reference-correction policy

For each successfully lifted allele:

- If a source ALT becomes the GRCh38 REF and every called sample is `0/0`
  after allele-aware remapping, it is an **assembly reference correction**.
  It is excluded from the VEP input and candidate/cohort lists, but retained in
  `*.liftover-reference-corrections.vcf.gz` with
  `IEI_REFERENCE_CORRECTION`.
- If a source ALT becomes the GRCh38 REF but at least one sample remains
  non-reference, the record is a genuine reviewable GRCh38 variant. It is
  retained and labelled `IEI_ASSEMBLY_ALLELE_SWAP`; the UI shows
  **Assembly allele swap**.
- If GT is absent or entirely missing, the record is retained for review. The
  pipeline does not infer reference status without genotype evidence.
- Unmapped or unsupported records are never interpreted as absent or
  homozygous reference.

This policy fixes the reference-artefact failure mode without using a brittle
blacklist of known loci.

## Conversion scope and artifacts

Conversion runs before GRCh38 PASS/exome-region filtering. The downloaded UCSC
hg19 primary FASTA and chain are normalized to the Ensembl
`1..22/X/Y/MT` convention used by the configured GRCh38 FASTA and VEP cache.
The initial validated scope is:

- chromosomes 1–22, X, Y, and mitochondrial sequence;
- sequence-resolved SNVs and short indels;
- maximum REF or ALT length controlled by `liftover.max_allele_length`
  (default 50 bp).

Symbolic alleles, breakends, spanning deletions, longer alleles, and
non-primary/decoy/alt contigs are retained in
`*.liftover-unsupported.vcf.gz`. Mapping/reference failures are retained in
`*.liftover-rejected.vcf.gz`.

Every converted record retains:

- `IEI_ORIGINAL_ASSEMBLY`
- `IEI_ORIGINAL_CHROM`
- `IEI_ORIGINAL_POS`
- `IEI_ORIGINAL_REF`
- `IEI_ORIGINAL_ALT`

These fields survive VEP annotation. The UI and cohort results display the
original representation and label the call **Lifted from GRCh37**.

## QC and provenance

The derived GRCh38 VCF has two JSON sidecars:

- `*.liftover.qc.json` — attempted, raw-lifted, retained,
  reference-correction, unsupported, and rejected counts plus reasons.
- `*.liftover.provenance.json` — input and reference identities, chain
  SHA-256, exact bcftools version and plugin commit, policy, and artifact paths.

All prepared source allele records must reconcile across retained, correction,
rejected, and unsupported artifacts or conversion fails. QC tracks stable
source-allele identities because introducing a new GRCh38 reference can turn
one source allele into two normalized destination rows. Identical conversions
are cached using input, reference, chain, tool, and policy identities.

## Setup

The native application release includes the normalized hg19 primary FASTA,
indexes, and hg19-to-GRCh38 chain. The workbench validates them under **Annotate VCF →
Set up annotation datasets → Shipped with the software**. If a bundled
file is missing, select **Download bundled files**. The command-line repair is:

```bash
bash docker/build.sh
bash scripts/download_references.sh \
  config/annotation.config.yaml --only liftover
```

The bundle is approximately 915 MB. The validated software pins bcftools 1.20
and the BCFtools/liftover source commit recorded in
`config/annotation.config.yaml`.

## Input-reference limitation

The current preset is the UCSC hg19 primary assembly, with primary contig names
normalized during intake. GRCh37 primary chromosome sequences are normally
equivalent at supported loci, but unusual b37/hs37d5/decoy callsets should not
be assumed compatible without checking their header and reference provenance.
Decoy and alternate-contig calls remain outside the validated scope.

## Clinical interpretation

A lifted call is a converted legacy call, not a call generated natively against
GRCh38. Keep the original VCF and read-level evidence. For a candidate used in
clinical reporting, inspect evidence on the original build and follow the
laboratory's confirmation policy; re-alignment/re-calling on GRCh38 is
preferred when source reads are available.
