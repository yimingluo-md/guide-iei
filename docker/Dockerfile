# =============================================================================
# VEP + LOFTEE (GRCh38) container for the diagnostic variant-annotation pipeline
# =============================================================================
#
# Strategy
# --------
# We layer on Ensembl's OFFICIAL `ensemblorg/ensembl-vep` image instead of
# building VEP from source. The official image already ships:
#   * ensembl-vep (the `vep` executable) at a pinned release
#   * the full VEP_plugins stack installed under /plugins
#     (AlphaMissense, CADD, REVEL, SpliceAI, dbNSFP, ...)   -- release >= 110
#   * bgzip / tabix / htsfile
#
# It does NOT include two things this pipeline needs, which we add here:
#   1. LOFTEE  -- lives in a SEPARATE repo (konradjk/loftee), never bundled.
#      We use the `grch38` branch, which is the ONLY branch that supports the
#      GRCh38 loftee.sql (conservation_file) + GERP bigwig invocation used in
#      vep_hg38.sh:  --plugin LoF,loftee_path:...,gerp_bigwig:...
#   2. samtools -- LOFTEE's ancestral.pm shells out to `samtools faidx` to read
#      human_ancestor.fa.gz. The official VEP image copies only bgzip/tabix.
#
# This modernizes yk-tanigawa/docker-ensembl-vep-loftee (VEP 101/105,
# LOFTEE master, Ubuntu 18.04), which cannot run AlphaMissense (needs >=110)
# or the GRCh38 LOFTEE invocation.
#
# Build (from repo root):
#   docker build -t vep-annotate:latest \
#     --build-arg VEP_TAG=release_113.4 \
#     --build-arg LOFTEE_BRANCH=grch38 \
#     -f docker/Dockerfile docker/
#
# Or use docker/build.sh which reads these from config.
# =============================================================================

# Pin the official VEP image. Any release_>=110 tag works (AlphaMissense needs
# >=110). Override with --build-arg VEP_TAG=release_114.x for a newer VEP.
ARG VEP_TAG=release_113.4
FROM ensemblorg/ensembl-vep:${VEP_TAG}

# --- LOFTEE branch: MUST be grch38 for a GRCh38 pipeline -----------------------
ARG LOFTEE_BRANCH=grch38
# LOFTEE is cloned here and added to PERL5LIB so `--plugin LoF` resolves LoF.pm.
ARG LOFTEE_DIR=/opt/vep/src/loftee

# The base image runs as the unprivileged `vep` user; drop to root to install.
USER root

# samtools (for LOFTEE ancestral allele lookup) + git (to clone LOFTEE).
# Clean apt lists in the same layer to keep the image small.
RUN apt-get update && apt-get install -y --no-install-recommends \
        samtools \
        git \
        ca-certificates && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Clone the LOFTEE grch38 branch. Depth 1: we only need the working tree.
RUN git clone --depth 1 --branch ${LOFTEE_BRANCH} \
        https://github.com/konradjk/loftee.git ${LOFTEE_DIR} && \
    chown -R vep:vep ${LOFTEE_DIR}

# Make LOFTEE importable by VEP's Perl and expose its dir as an env var so the
# pipeline can pass loftee_path:$LOFTEE_DIR without hard-coding it.
ENV LOFTEE_DIR=${LOFTEE_DIR}
ENV PERL5LIB=${LOFTEE_DIR}:${PERL5LIB}

# LOFTEE also needs the Perl module DBD::SQLite (to read loftee.sql / *.sql
# conservation DB). Install via cpanm (present in the base image).
RUN cpanm --notest DBD::SQLite && rm -rf /root/.cpanm

# Return to the unprivileged user for runtime.
USER vep
WORKDIR /data
