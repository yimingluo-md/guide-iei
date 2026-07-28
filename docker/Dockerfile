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
#      human_ancestor.fa.gz.
#   3. bcftools -- PASS/region filtering and contig-label normalization happen
#      before VEP. The official image does not include this executable.
#   4. Picard -- allele-aware GRCh37/hg19 VCF liftover into the canonical
#      GRCh38 annotation assembly.
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

# Validated bundle pin. Change only as part of a coordinated software release
# that also updates and regression-tests the cache and annotation resources.
ARG VEP_TAG=release_113.4
FROM ensemblorg/ensembl-vep:${VEP_TAG}

# --- LOFTEE branch: MUST be grch38 for a GRCh38 pipeline -----------------------
ARG LOFTEE_BRANCH=grch38
# LOFTEE is cloned here and added to PERL5LIB so `--plugin LoF` resolves LoF.pm.
ARG LOFTEE_DIR=/opt/vep/src/loftee
ARG PICARD_VERSION=3.3.0

# The base image runs as the unprivileged `vep` user; drop to root to install.
USER root

# samtools (LOFTEE), bcftools (VCF preprocessing), and git (clone LOFTEE).
# Clean apt lists in the same layer to keep the image small.
RUN apt-get update && apt-get install -y --no-install-recommends \
        samtools \
        bcftools \
        git \
        openjdk-17-jre-headless \
        curl \
        ca-certificates && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Clone the LOFTEE grch38 branch. Depth 1: we only need the working tree.
# The official VEP image also bundles an older /plugins/LoF.pm. VEP resolves
# plugin entry points from /plugins before PERL5LIB, so replace that one with
# the cloned GRCh38 entry point; helper modules still resolve from LOFTEE_DIR.
RUN git clone --depth 1 --branch ${LOFTEE_BRANCH} \
        https://github.com/konradjk/loftee.git ${LOFTEE_DIR} && \
    cp ${LOFTEE_DIR}/LoF.pm /plugins/LoF.pm && \
    chown -R vep:vep ${LOFTEE_DIR}

# Make LOFTEE importable by VEP's Perl and expose its dir as an env var so the
# pipeline can pass loftee_path:$LOFTEE_DIR without hard-coding it.
ENV LOFTEE_DIR=${LOFTEE_DIR}
ENV PERL5LIB=${LOFTEE_DIR}:${PERL5LIB}

# LOFTEE also needs the Perl module DBD::SQLite (to read loftee.sql / *.sql
# conservation DB). Install via cpanm (present in the base image).
RUN cpanm --notest DBD::SQLite && rm -rf /root/.cpanm

# Pin Picard so liftover behavior is reproducible across workstations.
RUN mkdir -p /opt/picard && \
    curl -fsSL --retry 3 \
      "https://github.com/broadinstitute/picard/releases/download/${PICARD_VERSION}/picard.jar" \
      -o /opt/picard/picard.jar && \
    chmod 0444 /opt/picard/picard.jar
COPY picard /usr/local/bin/picard
RUN chmod 0755 /usr/local/bin/picard
ENV PICARD_VERSION=${PICARD_VERSION}

# Ensembl's SpliceAI plugin unconditionally calls add_file() for both SNV and
# indel parameters, although this pipeline's required public MANE resource is
# SNV-only. Guard absent parameters so a single indexed source is valid; if an
# indel file is configured it is still loaded normally.
RUN sed -i \
      's/\$self->add_file(\$param_hash->{snv});/\$self->add_file(\$param_hash->{snv}) if defined(\$param_hash->{snv}) \&\& length(\$param_hash->{snv});/' \
      /plugins/SpliceAI.pm && \
    sed -i \
      's/\$self->add_file(\$param_hash->{indel});/\$self->add_file(\$param_hash->{indel}) if defined(\$param_hash->{indel}) \&\& length(\$param_hash->{indel});/' \
      /plugins/SpliceAI.pm

# Return to the unprivileged user for runtime.
USER vep
WORKDIR /data
