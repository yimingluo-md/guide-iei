---
title: "Understanding the output"
parent: Reference
nav_order: 5
---

# Understanding the output

[Manual home](index.md)

## The annotated VCF

The pipeline produces an annotated, bgzipped VCF. All VEP annotations live in
the `CSQ` INFO field, with every transcript consequence retained and the
preferred consequence for each ALT allele + gene marked `PICK=1`. MANE Select
and MANE Plus Clinical are preferred by the explicit pick order; LOFTEE,
plugins and custom tracks are CSQ subfields; the amino-acid-match adds
`INFO/ClinVar_path_aa_match` (0/1). The `##INFO` header for that flag records
the ClinVar release used. Sample columns (`FORMAT` / genotype) are passed
through unchanged, so zygosity is preserved.

## Loss-of-function curation details

For frameshift consequences, the postprocessor recalculates LOFTEE's
`50_BP_RULE` at the premature termination codon created by the shifted
reading frame. Successful calculations replace the value inside
`CSQ/LoF_info`. `LoF_50_BP_RULE_original`, `LoF_50_BP_RULE_PTC`,
`PTC_dist_from_last_exon`, and `PTC_calc_status` preserve the comparison and
provenance. The original `LoF=HC/LC` classification is retained.

For multi-indel events, `INFO/IEI_HAPLOTYPE_FRAME` records the sample,
transcript, partner variant(s), combined protein consequence, and one of three
states: fully restored and phase-confirmed, partially restored (another
allele copy remains disrupted), or possible restoration with unresolved
phase. The review UI excludes only the fully restored state by default. It
keeps partial and unresolved events visible and retains the original
per-variant LOFTEE annotation for audit.

## The annotation-QC certificate

Every completed run also writes two annotation-completeness artifacts beside
the final VCF:

- `<final.vcf.gz>.annotation_qc.json` for software/audit use
- `<final.vcf.gz>.annotation_qc.html` for human review

The certificate uses source-appropriate denominators: AlphaMissense and CADD
on missense records, LOFTEE on predicted loss-of-function records, and
SpliceAI on transcript-overlapping MANE SNVs. It records coverage, limited
examples of missing annotations, ClinVar matches, repeat/segdup overlaps,
configured resource versions, and whether promoterAI is installed. `WARN`
means annotation coverage needs review; it does not remove variants or assign
clinical significance.

## Notes on the container and annotation sources

- The diagnostic profile uses **dbNSFP v5.4a**, the current academic release
  when this profile was updated, distributed by the dbNSFP project as a
  single GRCh38-sorted, tabix-indexed BGZF file ready for VEP. Recent dbNSFP
  releases are built on newer transcript sets than the pinned VEP 113
  image/cache. Coordinate-level dbNSFP lookup works by GRCh38 allele, but
  transcript-specific fields must be regression-tested across this release
  difference. Run `pipeline/check_dbnsfp_version.py` to see update and
  compatibility recommendations before changing either resource.
- **VEP gnomAD frequencies can differ slightly from the gnomAD Browser.**
  Depending on the VEP cache release and matching path, a variant without an
  rsID may not receive a gnomAD frequency even when the normalized allele is
  present in the Browser. Therefore, a blank gnomAD annotation means
  “unavailable from this VEP annotation,” not definitive absence from gnomAD.
  This is expected to have limited practical impact because variants without
  rsIDs are generally rare, but important candidates should be confirmed in
  the gnomAD Browser by normalized chromosome, position, REF, and ALT.
- **LOFTEE must be the `grch38` branch** for GRCh38 (GERP bigwig + GRCh38
  conservation SQL). This is baked into the image at `/opt/vep/src/loftee`
  and exposed to VEP as `$LOFTEE_DIR`; `loftee_path: auto` in the config
  resolves to it inside the container.
- **BCFtools/liftover** is built from pinned bcftools and plugin commits for
  assembly-gap-aware hg19 intake. It uses both source and destination FASTAs,
  remaps genotype/allele-indexed fields, and audits source calls that become
  the GRCh38 reference. See [GRCh37 input](GRCH37_INPUT.md).
- See `docker/README.md` for Singularity build instructions and compatibility
  details.
