# GRCh37/hg19 input policy

The pipeline has one canonical annotation and cohort assembly: **GRCh38**.
It does not maintain a parallel GRCh37 VEP cache and plugin database.

## Preferred order

1. If FASTQ, BAM, or CRAM data are available, align and call variants against
   GRCh38. This is preferable for reportable findings because coordinate
   liftover cannot revisit mapping, local assembly, or callability.
2. If only a GRCh37/hg19 small-variant VCF is available, select
   **GRCh37 / hg19 — liftover to GRCh38** in the workbench or pass:

   ```bash
   bash scripts/run_annotation.sh \
     --input legacy.grch37.vcf.gz \
     --output results/legacy.vep.vcf.gz \
     --input-assembly GRCh37
   ```

3. Annotate, review, and cohort-index the resulting GRCh38 representation.

`--input-assembly auto` uses `##reference`, the chromosome-1 contig length, or
the pipeline target marker. It fails if the header is ambiguous. An explicit
choice is accepted for an ambiguous header but rejected when it conflicts with
reliable header evidence.

## Conversion behavior

The conversion runs before GRCh38 PASS/coding-region filtering and uses
[Picard LiftoverVcf](https://gatk.broadinstitute.org/hc/en-us/articles/360036733851-LiftoverVcf-Picard)
with UCSC's `hg19ToHg38.over.chain.gz`. The downloaded chain's query and target
contigs are normalized to the Ensembl `1..22/X/Y/MT` convention used by the
configured FASTA and VEP cache; input `chr1`/`chrM` labels are normalized in
the derived copy while the original label remains in INFO. The pipeline then
validates and left-normalizes REF/ALT against the configured GRCh38 FASTA.

The initial validated scope is:

- chromosomes 1–22, X, Y, and mitochondrial sequence;
- sequence-resolved SNVs and short indels;
- maximum REF or ALT length controlled by `liftover.max_allele_length`
  (default 50 bp).

Symbolic alleles, breakends, spanning deletions, longer alleles, and
non-primary/decoy/alt contigs are not silently discarded. They are retained in
an `*.liftover-unsupported.vcf.gz` artifact. Picard mapping/reference failures
are retained separately in `*.liftover-rejected.vcf.gz`.

Every converted record contains:

- `IEI_ORIGINAL_ASSEMBLY`
- `IEI_ORIGINAL_CHROM`
- `IEI_ORIGINAL_POS`
- `IEI_ORIGINAL_REF`
- `IEI_ORIGINAL_ALT`

These survive VEP annotation. The review UI and cohort results display both
representations and label the call **Lifted from GRCh37**.

## QC and provenance

The derived GRCh38 VCF has two JSON sidecars:

- `*.liftover.qc.json` — attempted, lifted, unsupported, Picard-rejected,
  allele-changed, and reverse-complemented counts plus rejection reasons.
- `*.liftover.provenance.json` — input identity, Picard version, chain SHA-256,
  target sequence-dictionary SHA-256, policy, and artifact paths.

Accepted plus rejected/unsupported input records must reconcile or the
conversion fails. An unlifted record must never be interpreted as absent or
homozygous reference.

Identical conversions are cached using input, chain, target dictionary, tool,
and policy identities. This makes large-lab ingestion a one-time conversion
cost rather than a per-query operation.

## Setup

Build the updated image and download the small chain file:

```bash
bash docker/build.sh
bash scripts/download_references.sh \
  config/annotation.config.yaml --only liftover
```

The image pins Picard 3.3.0. The target GRCh38 FASTA is the same reference used
by VEP; a sequence dictionary is generated beside it on first use.

## Clinical interpretation

A lifted call is a converted legacy call, not a call generated natively against
GRCh38. Keep the original VCF and read-level evidence. For a candidate used in
clinical reporting, inspect evidence on the original build and follow the
laboratory's confirmation policy; re-alignment/re-calling on GRCh38 is
preferred when source reads are available.
