---
title: "Dataset reference"
parent: Reference
nav_order: 7
---

# Dataset reference — technical details

Technical background moved out of the dataset-setup cards during the
clinician-focused language pass (2026-08). This file seeds the future user
webpage; each section holds the process detail the UI no longer shows.

## dbNSFP
- The UI accepts any authorized dbNSFP release whose filename ends in
  `_grch38.gz` (not `_grch37.gz`), including an Outlook Safe Links wrapper. It appends
  `.tbi` and `.md5` to the unwrapped path to obtain the matching companions.
- The bundled downloader uses eight concurrent HTTP ranges, retains verified
  ranges for resume, verifies the published MD5 and tabix index, and installs
  directly into Annotation datasets storage without a second 52 GB copy.
- The private URL is supplied to curl through mode-0600 files rather than
  process arguments and is never written to the job log or configuration.
- Legacy per-chromosome ZIP releases remain supported: they are merged and
  re-sorted on GRCh38 coordinates (needs ~220 GiB of temporary space and
  several hours) by `scripts/prepare_dbnsfp.sh`.

## SpliceAI
- File: `spliceai_scores.masked.snv.ensembl_mane_v1.4.grch38.vcf.gz` (+ .tbi),
  Ensembl's masked MANE v1.4 SNV set, saved to the configured reference
  directory and validated (file + index) before the source is marked ready.

## ClinVar
- The weekly NCBI GRCh38 VCF and index are downloaded and release-stamped;
  a stable `clinvar_latest` copy is what VEP and residue matching read.

## ClinGen Evidence Repository
- The updater validates the export schema, resolves exact GRCh38 alleles
  (ClinVar VariationID → CAID → genomic HGVS with repeat-aware
  normalization), enforces a ≥99.5% mapping rate, and records checksums.
- Retracted rows remain in the audit database but are never emitted as
  active evidence.

## LOFTEE (bundled data)
- LOFTEE code ships in the pinned VEP container; the GRCh38 ancestral
  sequence, conservation database, and GERP bigwig install with the
  reference bundle.

## RepeatMasker / Segmental duplications (bundled tracks)
- Cleaned UCSC hg38 `rmsk` and `genomicSuperDups` tracks; contig names and
  coordinates normalized to the Ensembl GRCh38 convention, BGZF-compressed
  and tabix-indexed.

## PromoterAI (licensed preparation)
- Preparation validates schema/coordinates/alleles/scores, collapses
  transcripts sharing a TSS, BGZF-compresses and indexes the score table,
  and records source and derived-file checksums. Source files are removed
  only after every managed output publishes successfully.

## CADD (non-coding, optional)
- Only the official v1.7 GRCh38 score-only SNV table and gnomAD genomes
  r4.0 indel table are downloaded (plus indexes and MD5 files). The 625 GB /
  11 GB `inclAnno` tables, the 335 GB release bundle, and dbscSNV data are
  deliberately excluded — the VEP CADD plugin cannot emit their columns.

## LoGoFunc
- Pinned Zenodo record 13835271; byte size and MD5s verified; annotation
  requires exact allele + Ensembl transcript + residue + substitution
  agreement, with allele-only matches labeled as such.

## GRCh37/hg19 input conversion
- Uses the pinned BCFtools/liftover plugin (bcftools 1.20, pinned commit)
  with both source and destination FASTAs; remaps GT and Number=A/R/G
  fields when REF/ALT changes; primary contigs retained and normalized.
- Source calls that become the GRCh38 reference with all-reference
  genotypes are excluded from annotation but preserved in a
  reference-corrections audit VCF; unsupported records (symbolic,
  breakends, >50 bp alleles, non-primary contigs) are set aside in their
  own reviewable files.

## ENCODE cCRE regions (SCREEN Registry V4)
- Primary contigs, cCRE accessions and overall classes retained;
  BGZF/tabix BED plus a gene-level TSS table derived from the
  release-matched Ensembl GTF for ±500 kb context.
- Used by the whole-genome import filter only — not added as a VEP
  transcript annotation. The release is pinned so an upstream SCREEN update
  cannot silently change an existing import.

## ENCODE tissue and immune contexts (SCREEN)
- Built under the ENCODE open data-use policy with the pinned Cell Ontology
  release (CC-BY 4.0) and a pinned ENCODE audit policy, all recorded in the
  bundle manifest with per-file SHA-256 checksums.
- A bundle prepared elsewhere can be registered from the Regulatory
  evidence workspace; only a local pointer is stored.
