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
| **DBD::SQLite** | cpanm | LOFTEE reads the `loftee.sql` conservation DB |

`LOFTEE_DIR=/opt/vep/src/loftee` is exported and prepended to `PERL5LIB`, so
`--plugin LoF,loftee_path:$LOFTEE_DIR,...` resolves without hard-coding.

## Build

```bash
# from repo root
docker/build.sh                      # reads VEP tag / LOFTEE branch from config
# or explicitly:
VEP_TAG=release_114.1 docker/build.sh
```

Any `release_>=110` tag works. Default is `release_113.4`
(CADD 1.7 + gnomAD v4.1, matching the reference stack in `vep_hg38.sh`).

## Version compatibility notes

- **AlphaMissense** plugin requires VEP **≥ 110**. The default 113.4 is fine.
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
