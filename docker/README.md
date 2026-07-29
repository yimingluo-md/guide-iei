# VEP + LOFTEE container

Modernized replacement for [yk-tanigawa/docker-ensembl-vep-loftee](https://github.com/yk-tanigawa/docker-ensembl-vep-loftee)
(which pins VEP 101/105 and LOFTEE `master`, and therefore cannot run
AlphaMissense — needs VEP ≥ 110 — or the GRCh38 LOFTEE invocation).

## What this image is

It layers on the **official** `ensemblorg/ensembl-vep` image and adds the two
pieces that image does not bundle:

| Layer | Source | Why |
|-------|--------|-----|
| VEP + full plugin stack (`/plugins`) | `ensemblorg/ensembl-vep:release_113.4` | `vep` binary, AlphaMissense/CADD/REVEL/SpliceAI/LoF `.pm`, bgzip/tabix |
| **LOFTEE (grch38 branch)** | `konradjk/loftee@grch38` → `/opt/vep/src/loftee` | only branch supporting GRCh38 `loftee.sql` + GERP bigwig |
| **samtools** | apt | LOFTEE `ancestral.pm` runs `samtools faidx` on `human_ancestor.fa.gz` |
| **bcftools 1.20** | pinned source build | PASS/region filtering, normalization, and `chr` to Ensembl contig normalization |
| **BCFtools/liftover** | pinned `freeseek/score` plugin source | assembly-gap-aware hg19/GRCh37 VCF conversion to canonical GRCh38 with GT/AD/PL remapping |

The image also applies a narrow compatibility guard to Ensembl's bundled
`SpliceAI.pm`: absent `snv` or `indel` parameters are skipped instead of being
passed to `add_file()`. This permits the required public MANE SNV-only dataset;
an indel dataset is still loaded whenever it is configured.
| **DBD::SQLite** | cpanm | LOFTEE reads the `loftee.sql` conservation DB |

`LOFTEE_DIR=/opt/vep/src/loftee` is exported and prepended to `PERL5LIB`, so
`--plugin LoF,loftee_path:$LOFTEE_DIR,...` resolves without hard-coding.

## Build

```bash
# from repo root
docker/build.sh
```

The supported image is pinned to `release_113.4` (CADD 1.7 + gnomAD v4.1,
matching the validated reference stack). Do not update the base VEP tag by
itself: upgrades must coordinate the cache, GTF/exome regions, LOFTEE and plugin
resources, then pass the annotation-completeness regression panel.

## Version compatibility notes

- **AlphaMissense** plugin requires VEP **≥ 110**. The pinned 113.4 bundle is
  validated.
- **LOFTEE branch MUST be `grch38`** for a GRCh38 pipeline. The `master`
  branch is GRCh37-only and will fail with "no such table: gerp_*" against
  GRCh38 data.
- The base image already contains the VEP_plugins repo under `/plugins` and
  all its Perl/Python dependencies, so we do not re-install them.

## Singularity / Apptainer

```bash
# build a .sif directly from the local docker image, or from a pushed image:
apptainer build vep-annotate.sif docker-daemon://vep-annotate:latest
# or
apptainer build vep-annotate.sif docker://<your-registry>/vep-annotate:latest
```
