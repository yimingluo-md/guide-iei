# Configuration reference

`annotation.config.yaml` drives the whole pipeline. Copy it to
`annotation.config.local.yaml` (gitignored) to customize paths without
touching the tracked file; scripts accept a config path as `$1`.

## The toggle contract

Every annotation source has the same three-field contract:

```yaml
SomeSource:
  enabled: true|false     # include this source at all?
  required: false         # if true, a missing file ABORTS the run;
                          # if false (default), a missing file is SKIPPED with a warning
  path: "..."             # (or file:/files:/snv:/... depending on source)
```

This is what makes the repo clone-and-run: enable everything, and any
reference you have not downloaded yet is silently skipped rather than
crashing VEP. Turn a source off entirely with `enabled: false`.

## Sections

| Section | Purpose |
|---------|---------|
| `container` | runtime (docker/podman/singularity/apptainer), image name, VEP tag, LOFTEE branch |
| `reference` | species, assembly, VEP cache dir, genome FASTA |
| `input` / `liftover` | input assembly default and controlled GRCh37/hg19-to-GRCh38 conversion |
| `run` | `fork` (parallelism), buffer size |
| `output` | `format: vcf` (preserves zygosity) or `tab`; bgzip; VEP stats html |
| `core` | pick, symbol, hgvs, sift, polyphen, gnomAD AFs — mirrors `vep_hg38.sh` |
| `plugins` | dbNSFP, required LoF (LOFTEE), required SpliceAI, optional WGS-only CADD/PromoterAI, optional LoGoFunc |
| `custom_tracks` | RepeatMasker, SegDup, ClinVar (`--custom`) |
| `wgs_review` | native import resources, including the pinned SCREEN cCRE BED and release-matched Ensembl gene-TSS context |
| `clinvar` | auto-fetch latest NCBI ClinVar per run |
| `post_processing` | required local frameshift PTC-based LOFTEE 50-bp correction and ClinVar amino-acid-match INFO flag |

`reference.assembly` must remain `GRCh38`. `input.default_assembly` controls
intake (`GRCh38`, `GRCh37`, or `auto`); it does not select a parallel VEP
database. See [`../docs/GRCH37_INPUT.md`](../docs/GRCH37_INPUT.md).

## Output format — why VCF

`output.format: vcf` makes VEP emit an annotated **VCF**, so the sample
genotype columns (`GT`, zygosity) are preserved and all annotations live in
`INFO/CSQ`. The original `vep_hg38.sh` used `--tab`, which drops zygosity.
Set `format: tab` only if you explicitly want the flat table and don't need
genotypes.

## Availability tiers

See [`../docs/ANNOTATIONS.md`](../docs/ANNOTATIONS.md) for the full table of
which sources the download helper fetches automatically vs. which you must
supply yourself. SpliceAI MANE SNVs and score-only CADD v1.7 WGS files are
downloadable in the UI; dbNSFP registration still happens on the official site,
then the UI downloads from the pasted authorized link; PromoterAI is prepared
from user-licensed Illumina files. LoGoFunc is a public,
optional Zenodo download installed from the local UI or with
`scripts/download_logofunc.sh` / `scripts/prepare_logofunc.sh`.

`plugins.dbNSFP.version` is the explicit release contract. Check it against the
configured filename, the official releases page, and the pinned VEP release:

```bash
python3 pipeline/check_dbnsfp_version.py \
  --config config/annotation.config.yaml
```

The check is advisory, so an offline annotation run never depends on the
dbnsfp.org website.
