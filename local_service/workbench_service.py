#!/usr/bin/env python3
"""Persistent, loopback-only annotation job service.

The service intentionally uses only the Python standard library. It queues
calls to the existing preflight and annotation scripts, stores job metadata in
SQLite, and never sends VCF data over the network.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import html
import json
import os
import platform
import queue
import re
import shutil
import signal
import socketserver
import sqlite3
import subprocess
import sys
import tempfile
import threading
import traceback
import time
import uuid
from contextlib import closing, contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable
import urllib.error
import urllib.request
from urllib.parse import parse_qs, quote, unquote, urlencode, urlparse

from local_service.ccre_context import CcreContextStore
from local_service.container_startup import DockerStartup, mac_tool_path, managed_colima_environment
from local_service.container_workspace import workspace_directory, check_ready as check_container_workspace
from local_service.clingen_erepo import ClinGenErepoStore
from local_service import cohort_store as cohort_module
from local_service.cohort_store import CohortStore
from local_service.errors import CohortMergeBusyError, NotFoundError
from local_service.gene_knowledge import (
    OMIM_FILES,
    GeneKnowledgeStore,
    build_omim_database,
    extract_omim_download_urls,
    validate_omim_download_url,
)
from local_service.genia import COMPONENT_LABELS
from local_service.phenotype_store import PhenotypeStore
from local_service.screen_context import ScreenContextStore
from local_service.job_progress import JobProgressTracker
from local_service.software_update import SoftwareUpdater
from local_service.sample_library import SampleLibrary
from local_service.storage_locations import (
    STORAGE_KINDS,
    STORAGE_MARKER,
    StorageLocationRegistry,
    StorageRegistryError,
    path_is_dir,
    storage_path_warning,
)
from local_service.wgs_review import WgsPrefilterOptions, WgsReviewStore
from pipeline.indexed_scores import (
    load_manifest as load_indexed_scores_manifest,
    validate_manifest_files,
    validate_manifest_registry_contract,
)
from pipeline.funcvep_dataset import PINNED_RELEASE as FUNCVEP_PINNED_RELEASE
from pipeline.avi_dataset import RELEASE as AVI_RELEASE, valid_bundle as valid_avi_bundle
from pipeline.predictor_registry import (
    Adapter,
    load_registry as load_predictor_registry,
)


SERVICE_VERSION = "0.13.0"
FUNCVEP_ARCHIVE_NAME = FUNCVEP_PINNED_RELEASE.archive_name
CONTAINER_FINGERPRINT_LABEL = "org.guide-iei.source-fingerprint"
CONTAINER_FINGERPRINT_FILES = (
    ".dockerignore",
    "Dockerfile",
    "build.sh",
    "PromoterAI.pm",
    "LoGoFunc.pm",
    "IndexedScores.pm",
    "prune_kent.py",
    "UPSTREAM-MODIFICATIONS.txt",
)
# Exit code that asks the launcher (scripts/start_workbench.sh or a packaged
# supervisor) to start the service again — used to activate pending
# storage-location changes from inside the app without rerunning the script.
RESTART_EXIT_CODE = 75
TERMINAL_STATUSES = {"succeeded", "failed", "cancelled", "interrupted"}
ALLOWED_PROFILES = {"local", "wsl-local"}
ANNOTATION_SOURCE_PATHS = {
    "dbnsfp": ("plugins", "dbNSFP"),
    "loftee": ("plugins", "LoF"),
    "spliceai": ("plugins", "SpliceAI"),
    "repeatmasker": ("custom_tracks", "RepeatMasker"),
    "segdup": ("custom_tracks", "SegDup"),
    "promoterai": ("plugins", "PromoterAI"),
    "cadd_wgs": ("plugins", "CADD_WGS"),
    "logofunc": ("plugins", "LoGoFunc"),
    "funcvep": ("plugins", "FuncVEP"),
    "alphagenome_avi": ("custom_tracks", "AlphaGenomeAVI"),
    "clinvar": ("custom_tracks", "ClinVar"),
    "loftee_ptc_50bp": ("post_processing", "loftee_ptc_50bp"),
    "clinvar_aa_match": ("post_processing", "clinvar_aa_match"),
    "liftover": ("liftover", "grch37_to_grch38"),
    "ccre": ("wgs_review", "ccre"),
    "screen_context": ("wgs_review", "screen_context"),
    "clingen_erepo": ("clingen_erepo", None),
    "genia": ("genia", None),
}
REQUIRED_DIAGNOSTIC_SOURCES = {"dbnsfp", "loftee", "spliceai", "loftee_ptc_50bp", "clingen_erepo"}
SOURCE_RECOMMENDATION_DEFAULTS = {
    "dbnsfp": "required",
    "loftee": "required",
    "spliceai": "required",
    "repeatmasker": "included",
    "segdup": "included",
    "promoterai": "recommended_wgs",
    "alphagenome_avi": "recommended_wgs",
    "cadd_wgs": "optional",
    "logofunc": "optional",
    "funcvep": "optional",
    "clinvar": "recommended",
    "loftee_ptc_50bp": "included",
    "clinvar_aa_match": "included",
    "liftover": "included",
    "ccre": "included",
    "screen_context": "recommended_wgs",
    "clingen_erepo": "required",
    "genia": "optional",
}
DBNSFP_OPTIONAL_PREDICTORS = [
    {"id": "metarnn", "label": "MetaRNN", "category": "Ensemble", "columns": ["MetaRNN_score", "MetaRNN_pred"]},
    {"id": "primateai", "label": "PrimateAI", "category": "Protein model", "columns": ["PrimateAI_score", "PrimateAI_pred"]},
    {"id": "gerp", "label": "GERP++ RS", "category": "Conservation", "columns": ["GERP++_RS"]},
    {"id": "phylop100", "label": "phyloP 100-way", "category": "Conservation", "columns": ["phyloP100way_vertebrate"]},
    {"id": "phastcons100", "label": "phastCons 100-way", "category": "Conservation", "columns": ["phastCons100way_vertebrate"]},
    {"id": "sift4g", "label": "SIFT4G", "category": "Established", "columns": ["SIFT4G_score", "SIFT4G_pred"]},
    {"id": "polyphen_hvar", "label": "PolyPhen HVAR", "category": "Established", "columns": ["Polyphen2_HVAR_score", "Polyphen2_HVAR_pred"]},
    {"id": "mutation_taster", "label": "MutationTaster", "category": "Established", "columns": ["MutationTaster_score", "MutationTaster_pred"]},
    {"id": "mutation_assessor", "label": "MutationAssessor", "category": "Established", "columns": ["MutationAssessor_score", "MutationAssessor_pred"]},
    {"id": "provean", "label": "PROVEAN", "category": "Established", "columns": ["PROVEAN_score", "PROVEAN_pred"]},
    {"id": "vest4", "label": "VEST4", "category": "Ensemble", "columns": ["VEST4_score"]},
    {"id": "meta_svm", "label": "MetaSVM", "category": "Ensemble", "columns": ["MetaSVM_score", "MetaSVM_pred"]},
    {"id": "meta_lr", "label": "MetaLR", "category": "Ensemble", "columns": ["MetaLR_score", "MetaLR_pred"]},
    {"id": "m_cap", "label": "M-CAP", "category": "Ensemble", "columns": ["M-CAP_score", "M-CAP_pred"]},
    {"id": "mutpred2", "label": "MutPred2", "category": "Ensemble", "columns": ["MutPred2_score", "MutPred2_pred"]},
    {"id": "mvp", "label": "MVP", "category": "Ensemble", "columns": ["MVP_score"]},
    {"id": "gmvp", "label": "gMVP", "category": "Ensemble", "columns": ["gMVP_score"]},
    {"id": "mpc", "label": "MPC", "category": "Regional constraint", "columns": ["MPC_score"]},
    {"id": "deogen2", "label": "DEOGEN2", "category": "Ensemble", "columns": ["DEOGEN2_score", "DEOGEN2_pred"]},
    {"id": "bayesdel_addaf", "label": "BayesDel addAF", "category": "Ensemble", "columns": ["BayesDel_addAF_score", "BayesDel_addAF_pred"]},
    {"id": "bayesdel_noaf", "label": "BayesDel noAF", "category": "Ensemble", "columns": ["BayesDel_noAF_score", "BayesDel_noAF_pred"]},
    {"id": "clinpred", "label": "ClinPred", "category": "Ensemble", "columns": ["ClinPred_score", "ClinPred_pred"]},
    {"id": "list_s2", "label": "LIST-S2", "category": "Ensemble", "columns": ["LIST-S2_score", "LIST-S2_pred"]},
    {"id": "varity_r", "label": "VARITY R", "category": "Protein model", "columns": ["VARITY_R_score"]},
    {"id": "varity_er", "label": "VARITY ER", "category": "Protein model", "columns": ["VARITY_ER_score"]},
    {"id": "esm1b", "label": "ESM1b", "category": "Protein language model", "columns": ["ESM1b_score", "ESM1b_pred"]},
    {"id": "phactboost", "label": "PHACTboost", "category": "Protein model", "columns": ["PHACTboost_score"]},
    {"id": "mutformer", "label": "MutFormer", "category": "Protein language model", "columns": ["MutFormer_score"]},
    {"id": "mutscore", "label": "MutScore", "category": "Protein model", "columns": ["MutScore_score"]},
    {"id": "popeve", "label": "popEVE", "category": "Protein model", "columns": ["popEVE_score", "popEVE_pred"]},
]
_PRIVATE_DATABASES = (
    "cohort.sqlite3", "workbench.sqlite3", "bulk-intake.sqlite3",
    "spliceai-lookup-cache.sqlite3",
)
_DBNSFP_GRCH38_FILENAME = re.compile(
    r"^dbNSFP(?P<version>[A-Za-z0-9._-]+)_grch38\.gz$"
)
_OMIM_MAX_FILE_BYTES = 1024 * 1024 * 1024
_OMIM_BETWEEN_FILES_DELAY = 2.0
_OMIM_RATE_LIMIT_RETRY_DELAYS = (10.0, 30.0, 60.0)
_OMIM_MAX_RETRY_AFTER = 300.0


class _OmimRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Permit an OMIM download to redirect only to the expected OMIM file."""

    def __init__(self, filename: str):
        super().__init__()
        self.filename = filename

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        try:
            validate_omim_download_url(newurl, self.filename)
        except ValueError as exc:
            # The caller converts this into a filename-only error. Keeping the
            # credential out of the exception message prevents accidental logs.
            raise urllib.error.HTTPError(
                req.full_url, code, "unsafe OMIM redirect refused", headers, fp
            ) from exc
        return super().redirect_request(req, fp, code, msg, headers, newurl)

ANNOTATION_SOURCE_SETUP = {
    "dbnsfp": {
        "setup_mode": "manual",
        "prepare_id": "dbnsfp",
        "access": "registration",
        "recommendation": "required",
        "reference_url": "https://www.dbnsfp.org/download",
        "reference_label": "dbNSFP academic download registration",
        "size_hint": "approximately 52 GB download; installed as-is",
        "instructions": [
            "Register with your institutional email at the dbNSFP academic download page (free for academic use).",
            "Copy the link ending in dbNSFP<version>_grch38.gz from the instruction email and paste it here; do not choose the similarly named _grch37.gz link. An Outlook Safe Links URL is accepted.",
            "GUIDE-IEI derives the matching .tbi and .md5 links, downloads all three files with eight resumable connections, verifies the published checksum and index, and installs them automatically.",
            "The private academic link is used only for this local download, is never written to the job log or configuration, and is deleted from temporary storage when the job ends.",
            "Coding-region CADD, AlphaMissense, REVEL and the other bundled predictors all come from this one dataset.",
        ],
    },
    "loftee": {
        "setup_mode": "bundled",
        "download_id": "loftee",
        "reference_url": "https://github.com/konradjk/loftee",
        "reference_label": "LOFTEE project",
        "size_hint": "approximately 15 GB",
        "instructions": [
            "Installed by the one-click dataset setup; the button here re-downloads only LOFTEE's data files if they are reported missing.",
        ],
    },
    "spliceai": {
        "setup_mode": "download",
        "download_id": "spliceai",
        "reference_url": "https://doi.org/10.1016/j.cell.2018.12.015",
        "reference_label": "SpliceAI published manuscript",
        "size_hint": "approximately 27 GB plus index",
        "instructions": [
            "Click Download and keep the computer awake; the download is resumable.",
            "These are Ensembl's SpliceAI scores recalculated directly on GRCh38 — not lifted over from the original hg19 scores, whose coordinate conversion is known to contain errors.",
            "Download completeness, BGZF structure, and index are checked before SpliceAI is marked ready.",
        ],
    },
    "repeatmasker": {
        "setup_mode": "bundled",
        "download_id": "repeatmasker",
        "reference_url": "https://www.repeatmasker.org/",
        "reference_label": "RepeatMasker project",
        "size_hint": "approximately 50 MB",
        "instructions": [
            "Installed by the one-click dataset setup; the button here re-downloads only this track if it is reported missing.",
            "Variants inside repetitive DNA are flagged in the review workspace so call quality can be weighed.",
        ],
    },
    "segdup": {
        "setup_mode": "bundled",
        "download_id": "segdup",
        "reference_url": (
            "https://genome.ucsc.edu/cgi-bin/hgTables?db=hg38&"
            "hgta_group=varRep&hgta_track=genomicSuperDups"
        ),
        "reference_label": "UCSC hg38 genomicSuperDups",
        "size_hint": "approximately 2 MB",
        "instructions": [
            "Installed by the one-click dataset setup; the button here re-downloads only this track if it is reported missing.",
            "Variants inside segmental duplications — near-identical genomic copies — are flagged in the review workspace so call quality can be weighed.",
        ],
    },
    "promoterai": {
        "setup_mode": "prepare",
        "access": "license",
        "recommendation": "recommended_wgs",
        "prepare_id": "promoterai",
        "reference_url": "https://github.com/Illumina/PromoterAI",
        "reference_label": "Illumina PromoterAI access information",
        "size_hint": "licensed files; one-time local preparation",
        "instructions": [
            "Request PromoterAI access from Illumina and obtain tss.tsv plus promoterAI_tss500.tsv.gz.",
            "Keep both licensed files in one local folder; nothing is uploaded or redistributed.",
            "Click Choose folder and pick that folder — validation and installation are automatic, and the source files are removed only after installation succeeds.",
            "Available for whole-genome annotation only.",
        ],
    },
    "cadd_wgs": {
        "setup_mode": "download",
        "access": "terms",
        "recommendation": "optional",
        "download_id": "cadd_wgs",
        "reference_url": "https://kircherlab.bihealth.org/download/CADD/v1.7/GRCh38/",
        "reference_label": "CADD v1.7 downloads",
        "size_hint": "about 83 GiB; resumable; non-commercial use",
        "instructions": [
            "Coding-region CADD scores are already included with dbNSFP — install this only for whole-genome, non-coding analysis.",
            "Click Download / resume to fetch the official score tables (~83 GiB, resumable); every file is verified before installation.",
            "CADD is free for non-commercial use; review the official terms before enabling it.",
            "Scores cover all single-base changes and known gnomAD indels; other indels simply remain unscored and are conservatively retained.",
        ],
    },
    "alphagenome_avi": {
        "setup_mode": "download",
        "access": "terms",
        "recommendation": "recommended_wgs",
        "download_id": "alphagenome_avi",
        "reference_url": "https://www.nature.com/articles/s41586-025-10014-0",
        "reference_label": "AlphaGenome published manuscript",
        "size_hint": "75.8 GB prepared download; allow 80 GiB free for setup",
        "instructions": [
            "Review the AlphaGenome terms on the official downloads page before installing.",
            "Click Download / resume to install the prepared GUIDE-IEI Hugging Face mirror into Annotation datasets storage. No account, API key, source ZIP, or local conversion is needed.",
            "The release is pinned and every downloaded file is SHA-256 verified before activation. Interrupted downloads resume; keep the computer awake and retry this same button if needed.",
            "AVI Phred scores annotate exact SNV alleles, including intergenic variants. No transcript or gene match is required. Indels and unsupported contigs remain unscored.",
            "AVI is not used to decide which variants are retained during WGS intake. Existing source archives are not modified or removed.",
        ],
    },
    "logofunc": {
        "setup_mode": "prepare",
        "access": "terms",
        "recommendation": "optional",
        "download_id": "logofunc",
        "prepare_id": "logofunc",
        "reference_url": "https://genomemedicine.biomedcentral.com/articles/10.1186/s13073-023-01261-9",
        "reference_label": "LoGoFunc published manuscript",
        "size_hint": "3.66 GB; GRCh38 canonical missense SNVs",
        "instructions": [
            "Click Download from Zenodo, or use Choose file for a copy downloaded elsewhere; the table is checksum-verified and moved into managed storage.",
            "Predictions apply only when the variant matches the source transcript and amino-acid change exactly; mismatches are shown as such rather than silently reassigned.",
            "Academic use only per the source; a research mechanism hint — not a clinical classification and not a replacement for LOFTEE.",
        ],
    },
    "funcvep": {
        "setup_mode": "prepare",
        "access": "license",
        "recommendation": "optional",
        "prepare_id": "funcvep",
        "reference_url": "https://www.nature.com/articles/s41588-026-02727-3",
        "reference_label": "FuncVEP published manuscript",
        "size_hint": "4.24 GB archive; 24 GiB free for automatic setup",
        "instructions": [
            "The upstream FuncVEP project identifies PolyForm Strict License 1.0.0. Review those terms and acknowledge that your intended use is permitted.",
            "The upstream terms permit qualifying noncommercial uses but do not grant distribution or software-modification rights; confirm that your intended local use of the score archive is authorized.",
            "After acknowledgement, GUIDE-IEI downloads the pinned official ZIP resumably from Zenodo, verifies it, extracts only the three FuncVEP scores, and builds a local GRCh38 tabix index.",
            "An already-downloaded official ZIP can be selected instead. The archive is never uploaded or bundled with GUIDE-IEI; an automatic download remains in Annotation datasets storage for reproducibility and resume support.",
            "A score is attached only when both the genomic allele and stable Ensembl gene ID match. These are functional-effect predictions, not clinical classifications; ClinVEP columns are intentionally not imported.",
        ],
    },
    "clinvar": {
        "setup_mode": "download",
        "download_id": "clinvar",
        "reference_url": "https://www.ncbi.nlm.nih.gov/clinvar/",
        "reference_label": "ClinVar website",
        "size_hint": "updated weekly",
        "instructions": [
            "Click Download latest to fetch the current weekly release.",
            "Reports come from many submitters and can conflict; the review workspace shows review status and conflicting submissions alongside each report.",
            "Automatic refresh before each annotation run keeps reports current, so this button is rarely needed.",
        ],
    },
    "loftee_ptc_50bp": {
        "setup_mode": "bundled",
        # The source clone does not carry the release payload's large native
        # reference bundle. The cCRE repair also fetches the release-matched
        # GTF used by this required frameshift postprocessor.
        "download_id": "ccre",
        "reference_url": "",
        "reference_label": "",
        "size_hint": "",
        "instructions": [
            "Included with the software — no action needed.",
            "Uses the bundled gene models and reference genome to re-check each frameshift at the position of the new stop codon it creates.",
        ],
    },
    "clinvar_aa_match": {
        "setup_mode": "bundled",
        "reference_url": "",
        "reference_label": "",
        "size_hint": "",
        "instructions": [
            "Included with the software — no action needed.",
            "Transcript-specific protein evidence is rebuilt automatically from the installed ClinVar and ClinGen releases and, when available, the user-provided GenIA variant export.",
        ],
    },
    "liftover": {
        "setup_mode": "bundled",
        "download_id": "liftover",
        "access": "bundled",
        "recommendation": "included",
        "reference_url": "https://github.com/freeseek/score#liftover-vcfs",
        "reference_label": "BCFtools/liftover documentation and publication",
        "size_hint": "approximately 915 MB; included with the software",
        "instructions": [
            "The exact GRCh37/hg19 reference and conversion chain are part of the standard setup; click Download bundled files if either is reported missing.",
            "Converted variants keep their original GRCh37/hg19 coordinates for review, and calls that only reflect reference differences between the builds are set aside in an audit file instead of entering the analysis.",
            "When raw sequencing reads are available, re-alignment to GRCh38 is preferable to conversion.",
        ],
    },
    "screen_context": {
        "setup_mode": "download",
        "download_id": "screen_context",
        "reference_url": "https://www.nature.com/articles/s41586-025-09909-9",
        "reference_label": "ENCODE Registry V4 published manuscript",
        "size_hint": "approximately 1.5 GB verified download; kept in Annotation datasets storage",
        "instructions": [
            "Click Download to fetch the prepared bundle from the public mirror; every file is verified before installation.",
            "Built from public ENCODE SCREEN data: where the aggregate cCRE map says a regulatory region exists, this layer shows which tissues and immune cell types it is active in.",
            "A missing assay is shown as unavailable evidence, never as a negative result.",
            "To rebuild from the original ENCODE sources instead (about 32 GB of downloads and several hours): bash scripts/prepare_screen_ccre_data.sh <annotation-storage>/screen-context",
        ],
    },
    "ccre": {
        "setup_mode": "bundled",
        "download_id": "ccre",
        "access": "bundled",
        "recommendation": "included",
        "reference_url": "https://www.nature.com/articles/s41586-025-09909-9",
        "reference_label": "ENCODE Registry V4 published manuscript",
        "size_hint": "approximately 25 MB; included with the software",
        "instructions": [
            "Part of the standard setup; click Download bundled files if it is reported missing.",
            "cCREs are candidate cis-regulatory elements — promoters, enhancers and similar regions ENCODE identified as likely to control gene activity.",
            "This map is aggregate-level (combined across samples); the ENCODE tissue and immune contexts dataset adds tissue- and cell-specific activity.",
            "Overlap keeps a variant for whole-genome review; it does not by itself mean the variant is pathogenic, nor that the nearest gene is the regulated one.",
        ],
    },
    "clingen_erepo": {
        "setup_mode": "download",
        "download_id": "clingen_erepo",
        "reference_url": "https://clinicalgenome.org/",
        "reference_label": "ClinGen website",
        "size_hint": "approximately 35 MB source; compact local VCF and SQLite snapshot",
        "instructions": [
            "Click Install latest (or Check and update) to fetch the official public export; a failed update leaves the previous working copy unchanged.",
            "Every disease- and inheritance-specific expert-panel assertion is kept separately rather than collapsed into one verdict.",
            "Annotation uses only this local snapshot — your variants are never sent to ClinGen.",
        ],
    },
    "genia": {
        "setup_mode": "manual",
        "access": "registration",
        "recommendation": "optional",
        "reference_url": "https://geniadb.org/",
        "reference_label": "GenIA website and registration",
        "size_hint": "small private local index; source files are not copied",
        "instructions": [
            "Select any one or any subset of the supported GenIA exports; unselected installed components are preserved.",
            "GUIDE-IEI detects each component by its schema, builds its own local index, and does not require the downloaded VCF index.",
        ],
    },
}
RESOURCE_DOWNLOAD_COMMANDS = {
    "alphagenome_avi": ("scripts/download_avi.sh",),
    "spliceai": ("scripts/download_references.sh", "--only", "spliceai"),
    "cadd_wgs": ("scripts/download_cadd_wgs.sh",),
    "clinvar": ("scripts/fetch_clinvar.sh",),
    "liftover": ("scripts/download_references.sh", "--only", "liftover"),
    "logofunc": ("scripts/download_logofunc.sh",),
    "ccre": ("scripts/download_references.sh", "--only", "ccre"),
    "loftee": ("scripts/download_references.sh", "--only", "loftee"),
    "repeatmasker": ("scripts/download_references.sh", "--only", "repeatmasker"),
    "segdup": ("scripts/download_references.sh", "--only", "segdup"),
    "gene_knowledge": ("scripts/update_gene_knowledge.sh",),
    "clingen_erepo": ("scripts/update_clingen_erepo.sh",),
    # Default: verified prepared-bundle download from the public mirror
    # (~1.5 GB); "screen_context_build" is the reproducible from-source build
    # (~32 GB of downloads plus hours of processing).
    "screen_context": ("scripts/download_screen_context_bundle.sh",),
    "screen_context_build": ("scripts/prepare_screen_ccre_data.sh",),
    "recommended_exome": ("scripts/install_recommended_datasets.sh", "exome"),
    "recommended_wgs": ("scripts/install_recommended_datasets.sh", "whole_genome"),
    "refresh_updates": ("scripts/update_refreshable_datasets.sh",),
}
# Conservative minimum free-space checks for downloads started from the UI.
# The downloaders themselves remain resumable; this guard prevents starting a
# large resource on a volume that clearly cannot hold its finished payload.
GIB = 1024 ** 3

# Broad SpliceAI Lookup API (interactive single-variant use only; see
# https://github.com/broadinstitute/SpliceAI-lookup). The site's defaults for
# variant interpretation: masked scores, 500 bp window.
SPLICEAI_LOOKUP_URL = "https://spliceai-38-xwkwwwxdwq-uc.a.run.app/spliceai/"
SPLICEAI_LOOKUP_DISTANCE = 500
SPLICEAI_LOOKUP_MASK = 1
RESOURCE_DOWNLOAD_OUTPUTS = {
    "spliceai": [(("plugins", "SpliceAI", "snv"), 30 * GIB, False)],
    # Check the two CADD payloads together when they share a filesystem.
    "cadd_wgs": [
        (("plugins", "CADD_WGS", "snv"), 75 * GIB, False),
        (("plugins", "CADD_WGS", "indels"), 15 * GIB, False),
    ],
    "clinvar": [(("clinvar", "dest_dir"), 2 * GIB, True)],
    "liftover": [
        (("liftover", "grch37_to_grch38", "source_fasta"), int(1.5 * GIB), False),
        (("liftover", "grch37_to_grch38", "chain"), int(0.5 * GIB), False),
    ],
    "logofunc": [(("plugins", "LoGoFunc", "file"), 5 * GIB, False)],
    # The preparation streams its 11 GB member into a much smaller score-only
    # indexed table; sorting and atomic publication need at least 20 decimal GB.
    "funcvep_preparation": [(("plugins", "FuncVEP", "file"), 19 * GIB, False)],
    "alphagenome_avi": [(("custom_tracks", "AlphaGenomeAVI", "dest_dir"), 80 * GIB, True)],
    # Automatic setup also stores the 4.24 GB source ZIP on the same volume.
    "funcvep_download_preparation": [(("plugins", "FuncVEP", "file"), 24 * GIB, False)],
    # SCREEN preparation also downloads the release-matched Ensembl GTF used
    # to derive the bundled +/-500 kb gene-TSS context table.
    "ccre": [(("wgs_review", "ccre", "bed"), 3 * GIB, False)],
    "loftee": [(("plugins", "LoF", "human_ancestor_fa"), 20 * GIB, False)],
    "repeatmasker": [(("custom_tracks", "RepeatMasker", "file"), 1 * GIB, False)],
    "segdup": [(("custom_tracks", "SegDup", "file"), 1 * GIB, False)],
    "clingen_erepo": [(("clingen_erepo", "dest_dir"), 1 * GIB, True)],
    "recommended_exome": [
        (("reference", "vep_cache_dir"), 35 * GIB, True),
        (("reference", "fasta", "path"), 2 * GIB, False),
        (("plugins", "LoF", "human_ancestor_fa"), 20 * GIB, False),
        (("plugins", "SpliceAI", "snv"), 30 * GIB, False),
        (("clinvar", "dest_dir"), 2 * GIB, True),
        (("clingen_erepo", "dest_dir"), 1 * GIB, True),
    ],
    "recommended_wgs": [
        (("custom_tracks", "AlphaGenomeAVI", "dest_dir"), 80 * GIB, True),
        (("reference", "vep_cache_dir"), 35 * GIB, True),
        (("reference", "fasta", "path"), 2 * GIB, False),
        (("plugins", "LoF", "human_ancestor_fa"), 20 * GIB, False),
        (("plugins", "SpliceAI", "snv"), 30 * GIB, False),
        (("clinvar", "dest_dir"), 2 * GIB, True),
        (("clingen_erepo", "dest_dir"), 1 * GIB, True),
    ],
    "refresh_updates": [
        (("clinvar", "dest_dir"), 2 * GIB, True),
        (("clingen_erepo", "dest_dir"), 1 * GIB, True),
    ],
    "dbnsfp_download": [
        (("plugins", "dbNSFP", "path"), 60 * GIB, False),
    ],
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def is_wsl() -> bool:
    return bool(os.environ.get("WSL_DISTRO_NAME") or os.environ.get("WSL_INTEROP"))


def default_state_dir() -> Path:
    override = os.environ.get("IEI_WORKBENCH_STATE_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".iei-variant-review"


def _directory_size(path: Path) -> int:
    try:
        exists = path.exists()
    except OSError:
        return 0
    if not exists:
        return 0
    total = 0
    try:
        for root, _directories, files in os.walk(path, onerror=lambda _error: None):
            for name in files:
                item = Path(root) / name
                try:
                    if not item.is_symlink():
                        total += item.stat().st_size
                except OSError:
                    continue
    except OSError:
        pass
    return total


class ServiceAlreadyRunningError(RuntimeError):
    """Another service process already owns this state directory."""


# Exit status for the launcher: not a crash (no restart loop), not a clean
# stop, not the in-app restart code 75.
EXIT_ALREADY_RUNNING = 4


class JobStore:
    """Small SQLite repository; each operation owns its connection."""

    def __init__(self, database_path: Path):
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    @contextmanager
    def _session(self):
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._session() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS annotation_jobs (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    status TEXT NOT NULL,
                    profile TEXT NOT NULL,
                    analysis_scope TEXT NOT NULL DEFAULT 'exome',
                    input_assembly TEXT NOT NULL DEFAULT 'GRCh38',
                    input_path TEXT NOT NULL,
                    output_path TEXT NOT NULL,
                    final_output_path TEXT,
                    config_path TEXT NOT NULL,
                    coding_only INTEGER NOT NULL DEFAULT 1,
                    include_filtered INTEGER NOT NULL DEFAULT 0,
                    use_clinvar INTEGER NOT NULL DEFAULT 1,
                    command_json TEXT NOT NULL DEFAULT '[]',
                    log_path TEXT NOT NULL,
                    pid INTEGER,
                    exit_code INTEGER,
                    error TEXT
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS annotation_jobs_created_idx "
                "ON annotation_jobs(created_at DESC)"
            )
            job_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(annotation_jobs)"
                ).fetchall()
            }
            if "input_assembly" not in job_columns:
                connection.execute(
                    "ALTER TABLE annotation_jobs "
                    "ADD COLUMN input_assembly TEXT NOT NULL DEFAULT 'GRCh38'"
                )
            if "analysis_scope" not in job_columns:
                connection.execute(
                    "ALTER TABLE annotation_jobs "
                    "ADD COLUMN analysis_scope TEXT NOT NULL DEFAULT 'exome'"
                )
            # Jobs still marked running belong to a previous service process.
            # Their pipeline scripts run in their own session and may well be
            # alive; remember the pids so the service can reclaim them
            # (audit H6) before the rows are marked interrupted.
            self.orphaned_running_jobs: list[tuple[str, int]] = [
                (str(row["id"]), int(row["pid"]))
                for row in connection.execute(
                    "SELECT id, pid FROM annotation_jobs "
                    "WHERE status = 'running' AND pid IS NOT NULL"
                ).fetchall()
                if isinstance(row["pid"], int) and row["pid"] > 0
            ]
            now = utc_now()
            connection.execute(
                """
                UPDATE annotation_jobs
                SET status = 'interrupted', updated_at = ?, finished_at = ?,
                    error = COALESCE(error, 'The local service stopped while this job was running.')
                WHERE status = 'running'
                """,
                (now, now),
            )

    def create(self, values: dict) -> dict:
        columns = ", ".join(values)
        placeholders = ", ".join("?" for _ in values)
        with self._session() as connection:
            connection.execute(
                f"INSERT INTO annotation_jobs ({columns}) VALUES ({placeholders})",
                tuple(values.values()),
            )
        return self.get(values["id"])

    def update(self, job_id: str, **values) -> dict | None:
        if not values:
            return self.get(job_id)
        values["updated_at"] = utc_now()
        assignments = ", ".join(f"{column} = ?" for column in values)
        with self._session() as connection:
            connection.execute(
                f"UPDATE annotation_jobs SET {assignments} WHERE id = ?",
                (*values.values(), job_id),
            )
        return self.get(job_id)

    def get(self, job_id: str) -> dict | None:
        with self._session() as connection:
            row = connection.execute(
                "SELECT * FROM annotation_jobs WHERE id = ?", (job_id,)
            ).fetchone()
        return self._serialize(row) if row else None

    def list(self, limit: int = 100) -> list[dict]:
        with self._session() as connection:
            rows = connection.execute(
                "SELECT * FROM annotation_jobs ORDER BY created_at DESC LIMIT ?",
                (max(1, min(limit, 500)),),
            ).fetchall()
        return [self._serialize(row) for row in rows]

    def queued_ids(self) -> list[str]:
        with self._session() as connection:
            rows = connection.execute(
                "SELECT id FROM annotation_jobs WHERE status = 'queued' ORDER BY created_at"
            ).fetchall()
        return [row["id"] for row in rows]

    def active_count(self) -> int:
        with self._session() as connection:
            return connection.execute(
                "SELECT COUNT(*) FROM annotation_jobs WHERE status IN ('queued', 'running')"
            ).fetchone()[0]

    @staticmethod
    def _serialize(row: sqlite3.Row) -> dict:
        result = dict(row)
        for key in ("coding_only", "include_filtered", "use_clinvar"):
            result[key] = bool(result[key])
        result["command"] = json.loads(result.pop("command_json") or "[]")
        return result


class AnnotationJobService:
    def __init__(
        self,
        pipeline_root: Path,
        state_dir: Path,
        start_worker: bool = True,
        storage_registry: StorageLocationRegistry | None = None,
        auto_start_docker: bool = False,
    ):
        self.pipeline_root = pipeline_root.resolve()
        self.state_dir = state_dir.resolve()
        self.storage_registry = storage_registry or StorageLocationRegistry(
            self.pipeline_root,
            self.state_dir,
            registry_path=self.state_dir.parent / f".{self.state_dir.name}.storage-registry.json",
            persist=True,
        )
        # Roots are intentionally captured at service start. A location change
        # is persisted safely, but takes effect after a workbench restart so a
        # running job can never switch filesystems halfway through a run.
        self.annotation_root = self.storage_registry.root("annotation")
        self.workspace_dir = (
            self.state_dir
            if self.storage_registry.describe("temporary").get("follows_data_root")
            else self.storage_registry.root("temporary")
        )
        if (
            path_is_dir(self.state_dir)
            and self.storage_registry.root("data") == self.state_dir
            and not self.storage_registry.is_default("data")
        ):
            self.storage_registry.ensure_marker_identity(
                "data", self.state_dir, required=True, service=SERVICE_VERSION
            )
        if (
            path_is_dir(self.workspace_dir)
            and self.storage_registry.root("temporary") == self.workspace_dir
            and not self.storage_registry.is_default("temporary")
        ):
            self.storage_registry.ensure_marker_identity(
                "temporary", self.workspace_dir, required=True, service=SERVICE_VERSION
            )
        if not path_is_dir(self.state_dir) and (
            self.storage_registry.root("data") == self.state_dir
            and not self.storage_registry.is_default("data")
        ):
            raise ValueError(
                "configured Sample Library & Cohort storage is unavailable. "
                "Reconnect the selected drive; the workbench will not create a fallback database."
            )
        if not path_is_dir(self.workspace_dir) and (
            self.storage_registry.root("temporary") == self.workspace_dir
            and not self.storage_registry.is_default("temporary")
        ):
            raise ValueError(
                "configured temporary workspace is unavailable. Reconnect the selected drive; "
                "the workbench will not fall back to a different folder."
            )
        try:
            self.state_dir.mkdir(parents=True, exist_ok=True)
            self.workspace_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ValueError(
                f"configured workbench storage is unavailable or not writable: {exc}"
            ) from exc
        # Exclusive ownership of the state directory BEFORE any recovery
        # step. A second service process on the same state (a double
        # launch, or a launcher that only probed the UI port) used to mark
        # the live instance's running job interrupted, kill its downloads
        # and delete its secrets, and only then fail to bind (audit H7).
        self._instance_lock_handle = self._acquire_instance_lock()
        self.logs_dir = self.state_dir / "logs"
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.docker_startup = DockerStartup(self.logs_dir / "docker-startup.log")
        self.resource_logs_dir = self.state_dir / "resource-logs"
        self.resource_logs_dir.mkdir(parents=True, exist_ok=True)
        self.resource_secrets_dir = self.state_dir / "resource-secrets"
        self.resource_secrets_dir.mkdir(parents=True, exist_ok=True)
        self.store = JobStore(self.state_dir / "workbench.sqlite3")
        self._terminate_orphaned_annotation_jobs()
        self.cohort = CohortStore(
            self.state_dir / "cohort.sqlite3",
            enable_auto_index=True,
            workspace_dir=self.workspace_dir,
        )
        self.wgs_review = WgsReviewStore(
            self.state_dir, self.cohort, workspace_dir=self.workspace_dir
        )
        self.ccre_context_store = CcreContextStore(self.cohort.hts_backend)
        self.screen_context_store = ScreenContextStore()
        self.screen_context_pointer = self.state_dir / "screen-context.json"
        self._wgs_review_files: dict[str, Path] = {}
        self._wgs_review_jobs: dict[str, dict] = {}
        self._wgs_review_threads: dict[str, threading.Thread] = {}
        self._wgs_review_lock = threading.Lock()
        self.phenotypes = PhenotypeStore(self.state_dir / "cohort.sqlite3")
        self.sample_library = SampleLibrary(
            self.state_dir, self.cohort, workspace_dir=self.workspace_dir
        )
        gene_knowledge_override = self.annotation_root / "gene-knowledge" / "gene_knowledge_public.sqlite3"
        genia_database = self.annotation_root / "genia" / "genia.sqlite3"
        genia_reference_fasta = (
            self.annotation_root
            / "fasta"
            / "Homo_sapiens.GRCh38.dna.primary_assembly.fa.gz"
        )
        try:
            annotation_config = self._load_config(
                self.pipeline_root / "config" / "annotation.config.yaml"
            )
            genia_block = annotation_config.get("genia") or {}
            reference_block = annotation_config.get("reference") or {}
            fasta_block = reference_block.get("fasta") or {}
            configured_genia = self._resolved_reference_path(genia_block.get("database"))
            configured_fasta = self._resolved_reference_path(fasta_block.get("path"))
            if configured_genia is not None:
                genia_database = configured_genia
            if configured_fasta is not None:
                genia_reference_fasta = configured_fasta
        except (OSError, UnicodeError, ValueError):
            # Keep the service usable when annotation settings are temporarily
            # unreadable; the profile endpoint reports that configuration
            # problem separately, and these stock managed paths remain safe.
            pass
        self.gene_knowledge = GeneKnowledgeStore(
            gene_knowledge_override if gene_knowledge_override.is_file() else
            self.pipeline_root / "webui" / "public" / "bundled-data" / "gene_knowledge_public.sqlite3",
            self.state_dir / "gene-knowledge" / "omim.sqlite3",
            genia_database,
            genia_reference_fasta,
        )
        self.genia = self.gene_knowledge.genia
        self.clingen_erepo = ClinGenErepoStore(
            self.annotation_root / "clingen_erepo" / "clingen_erepo.sqlite3",
            self.annotation_root / "clingen_erepo" / "manifest.json",
        )
        self.software_updater = SoftwareUpdater(
            self.pipeline_root, self.state_dir
        )
        # A fresh process IS the restart an install asked for.
        self.software_updater.clear_restart_pending()
        self._software_update_lock = threading.Lock()
        self.job_progress = JobProgressTracker()
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._processes: dict[str, subprocess.Popen] = {}
        self._process_lock = threading.Lock()
        self._resource_jobs: dict[str, dict] = {}
        self._resource_processes: dict[str, subprocess.Popen] = {}
        self._resource_threads: dict[str, threading.Thread] = {}
        self._resource_lock = threading.Lock()
        self._resource_pids_lock = threading.Lock()
        self._harden_private_permissions()
        self._storage_jobs: dict[str, dict] = {}
        self._storage_threads: dict[str, threading.Thread] = {}
        self._storage_lock = threading.Lock()
        self._storage_transition = threading.Condition(threading.Lock())
        self._active_storage_mutations = 0
        self._migration_reserved = False
        self._migration_reservation_reason = ""
        self._storage_usage_cache: dict[str, tuple[float, int]] = {}
        self._storage_usage_lock = threading.Lock()
        self._restart_requested = False
        self._quit_requested = False
        self._engine_setup_reserved = False
        self._instance_id = os.environ.get("IEI_DESKTOP_INSTANCE_ID") or uuid.uuid4().hex
        self._stop = threading.Event()
        self._recover_stale_storage_migrations()
        self._terminate_orphaned_resource_jobs()
        self._remove_stale_resource_secrets()
        self._bulk_intake_lock = threading.Lock()
        self._bulk_intake_thread: threading.Thread | None = None
        self._bulk_intake_cancelled: set[str] = set()
        if start_worker:
            self._resume_bulk_intake()
        self._worker: threading.Thread | None = None
        # Worker health (audit M22): every unexpected exception inside the
        # worker is counted and kept here, so /api/health can say the queue
        # is unattended instead of jobs silently staying "queued".
        self._worker_failures = 0
        self._worker_last_error: str | None = None
        self._worker_last_error_at: str | None = None
        for job_id in self.store.queued_ids():
            self._queue.put(job_id)
        if auto_start_docker:
            try:
                config = self._load_config(self.pipeline_root / "config/annotation.config.yaml")
                self.docker_startup.start((config.get("container") or {}).get("runtime", "docker"))
            except (OSError, ValueError):
                pass  # The annotation profile reports config errors separately.
        if start_worker:
            self._worker = threading.Thread(
                target=self._worker_loop, name="annotation-worker", daemon=True
            )
            self._worker.start()

    def worker_health(self) -> dict:
        """Liveness of the single annotation worker, for /api/health.

        ``alive`` is False when the thread was never started (test
        instances) or has exited; ``failures`` counts exceptions that
        escaped a job (the job itself was marked failed where the store
        allowed it; nothing is re-run automatically because the outcome of
        a half-finished annotation is unknown).
        """
        return {
            "alive": bool(self._worker is not None and self._worker.is_alive()),
            "queued": self._queue.qsize(),
            "failures": self._worker_failures,
            "last_error": self._worker_last_error,
            "last_error_at": self._worker_last_error_at,
        }

    def _recover_stale_storage_migrations(self) -> None:
        """Remove only staging roots recorded by an interrupted prior process."""
        for record in self.storage_registry.migration_history():
            if record.get("status") not in {"queued", "running"}:
                continue
            job_id = str(record.get("id") or "")
            kind = str(record.get("kind") or "")
            destination_text = str(record.get("destination") or "")
            if not job_id or kind not in STORAGE_KINDS or not destination_text:
                continue
            destination = Path(destination_text).expanduser().resolve(strict=False)
            staging = destination.parent / f".{destination.name}.iei-migrating-{job_id[:12]}"
            configured = self.storage_registry.root(kind)
            recovered_active = configured == destination and destination.is_dir()
            cleanup_error = ""
            if not recovered_active:
                for candidate in (staging, destination):
                    if not candidate.exists():
                        continue
                    # The exact destination is deleted only when it carries the
                    # marker written by this workbench. An arbitrary user folder
                    # is never a recovery target.
                    if candidate == destination:
                        try:
                            marker = self.storage_registry.marker(candidate)
                        except StorageRegistryError:
                            marker = None
                        if (
                            not marker
                            or marker.get("kind") != kind
                            or marker.get("migration_id") != job_id
                        ):
                            continue
                    try:
                        shutil.rmtree(candidate)
                    except OSError as exc:
                        cleanup_error = f"; partial copy remains at {candidate}: {exc}"
            updated = {
                **record,
                "status": "succeeded" if recovered_active else "interrupted",
                "finished_at": utc_now(),
                "restart_required": recovered_active,
                "staging_path": "" if not cleanup_error else str(staging),
                "message": (
                    "Recovered an activated migration after an interrupted shutdown."
                    if recovered_active else "Interrupted migration staging was cleaned; the original location remains active."
                ),
                "error": cleanup_error.lstrip("; "),
            }
            self.storage_registry.record_migration(updated)

    def capabilities(self) -> dict:
        in_wsl = is_wsl()
        hardware = self._hardware_profile()
        annotation_profile = self._annotation_profile()
        annotation_profile["defaults"]["fork"] = hardware[
            "recommended_vep_workers"
        ]
        runtimes = [
            runtime for runtime in ("docker", "podman", "apptainer", "singularity")
            if shutil.which(runtime)
        ]
        profile = {
            "id": "wsl-local" if in_wsl else "local",
            "label": "WSL workstation" if in_wsl else "Local workstation",
            "available": bool(shutil.which("bash")),
        }
        return {
            "service": "IEI Variant Review local service",
            "version": SERVICE_VERSION,
            "platform": platform.system().lower(),
            "wsl": in_wsl,
            "pipeline_root": str(self.pipeline_root),
            "cohort_database": str(self.cohort.database_path),
            "phenotype_database": str(self.phenotypes.database_path),
            "sample_library_directory": str(self.sample_library.root),
            # The annotation screen only needs root state. Avoid walking a
            # multi-gigabyte cache every time capabilities are refreshed; the
            # Storage page obtains fresh size values explicitly.
            "storage": self.storage_configuration(include_usage=False),
            "container_runtimes": runtimes,
            "hardware": hardware,
            "profiles": [profile],
            "input_assemblies": [
                {
                    "id": "GRCh38",
                    "label": "GRCh38 / hg38 — annotate directly",
                },
                {
                    "id": "GRCh37",
                    "label": "GRCh37 / hg19 — liftover to GRCh38",
                },
                {
                    "id": "auto",
                    "label": "Detect from VCF header",
                },
            ],
            "defaults": {
                "output_directory": str(self.workspace_dir / "results") if os.environ.get("IEI_DESKTOP_APP") == "1" else str(self.pipeline_root / "results"),
                "config_path": str(self.pipeline_root / "config" / "annotation.config.yaml"),
                "analysis_scope": "exome",
                "coding_only": True,
                "include_filtered": False,
                "use_clinvar": True,
                "input_assembly": "auto",
            },
            "annotation_profile": annotation_profile,
        }

    def import_sample_library(self, payload: dict) -> dict:
        self._ensure_active_storage_available(require_workspace=True)
        sources = payload.get("sources")
        if not isinstance(sources, list) or not sources:
            raise ValueError("sources must contain at least one review VCF")
        results = []
        profile = self._annotation_profile()
        installed_versions = {
            source["id"]: source.get("version", "")
            for source in profile.get("sources", [])
            if source.get("installed") or source.get("available")
        }
        bundle = {
            "workbench_service": SERVICE_VERSION,
            "foundations": {
                item["id"]: item.get("version")
                for item in profile.get("foundations", [])
                if item.get("available")
            },
            "provenance_note": (
                "Installed workstation bundle at import time; an externally annotated "
                "VCF may contain annotations from a different bundle."
            ),
        }
        for source in sources:
            if not isinstance(source, dict):
                raise ValueError("each source must be an object")
            if source.get("review_id"):
                path = self.wgs_review_file(str(source["review_id"]))
            else:
                path = Path(str(source.get("path") or "")).expanduser().resolve()
            options = {**payload, **source}
            options.pop("sources", None)
            options.setdefault("annotation_bundle", bundle)
            options.setdefault("resource_versions", installed_versions)
            # A browser-uploaded source lives in the workspace staging area,
            # which cleanup deletes: its path must never replace a durable
            # workstation location on existing datasets.
            declared = str(options.get("original_path") or path)
            try:
                uploads_root = (self.workspace_dir / "uploads").resolve()
                options["original_is_ephemeral"] = (
                    uploads_root in Path(declared).expanduser().resolve().parents
                )
            except OSError:
                options["original_is_ephemeral"] = False
            results.append(self.sample_library.import_vcf(path, options))
        return {"imports": results, "datasets": [dataset for result in results for dataset in result["datasets"]]}

    def inspect_sample_library(self, payload: dict) -> dict:
        """Classify retained-import identity before any library rows change."""
        self._ensure_active_storage_available(require_workspace=True)
        sources = payload.get("sources")
        if not isinstance(sources, list) or not sources:
            raise ValueError("sources must contain at least one review VCF")
        results = []
        for source in sources:
            if not isinstance(source, dict):
                raise ValueError("each source must be an object")
            if source.get("review_id"):
                path = self.wgs_review_file(str(source["review_id"]))
            else:
                path = Path(str(source.get("path") or "")).expanduser().resolve()
            inspected = self.sample_library.inspect_vcf(path, {
                "analysis_scope": payload.get("analysis_scope") or "exome",
            })
            results.append({
                **inspected,
                "source_name": str(source.get("original_name") or path.name),
                "path": str(path),
            })
        return {"inspections": results}

    def cleanup_storage(self, categories: list[str]) -> dict:
        if self.storage_migration_active():
            raise ValueError("wait for the active storage migration before cleaning cache files")
        self._ensure_storage_idle()
        with self._wgs_review_lock:
            if any(
                job.get("status") in {"queued", "running"}
                for job in self._wgs_review_jobs.values()
            ):
                raise ValueError("wait for the active WGS review import before cleaning caches")
        result = self.sample_library.cleanup(categories)
        self._invalidate_storage_size_cache(self.state_dir, self.workspace_dir)
        return result

    def compact_storage(self) -> dict:
        if self.storage_migration_active():
            raise ValueError("wait for the active storage migration before compacting SQLite")
        result = self.sample_library.compact_database()
        self._invalidate_storage_size_cache(self.state_dir)
        return result

    # ------------------------------------------------------------------
    # Workstation storage locations
    # ------------------------------------------------------------------
    def _cached_storage_size(self, path: Path, max_age_seconds: float = 300.0) -> int:
        key = str(path.resolve(strict=False))
        now = time.monotonic()
        # Keep the scan itself under the lock. Storage usage is requested
        # infrequently and this prevents concurrent HTTP requests from starting
        # duplicate recursive walks over a large annotation bundle.
        with self._storage_usage_lock:
            cached = self._storage_usage_cache.get(key)
            if cached and now - cached[0] < max_age_seconds:
                return cached[1]
            value = _directory_size(path)
            self._storage_usage_cache[key] = (now, value)
        return value

    def _invalidate_storage_size_cache(self, *paths: Path) -> None:
        keys = {str(path.resolve(strict=False)) for path in paths}
        with self._storage_usage_lock:
            for key in keys:
                self._storage_usage_cache.pop(key, None)

    def storage_configuration(self, *, include_usage: bool = True) -> dict:
        """Describe configured locations without creating a fallback store."""
        configuration = self.storage_registry.as_dict(
            active_data_root=self.state_dir,
            active_annotation_root=self.annotation_root,
            active_temporary_root=self.workspace_dir,
        )
        configured_annotation_root = self.storage_registry.root("annotation")
        configured_data_root = self.storage_registry.root("data")
        configured_temporary_root = self.storage_registry.root("temporary")
        annotation_bytes = (
            self._cached_storage_size(configured_annotation_root)
            if include_usage else None
        )
        data_bytes = (
            self._cached_storage_size(configured_data_root)
            if include_usage else None
        )
        workspace_is_within_data = (
            configured_temporary_root == configured_data_root
            or configured_data_root in configured_temporary_root.parents
        )
        temporary_bytes = (
            None if workspace_is_within_data or not include_usage
            else self._cached_storage_size(configured_temporary_root)
        )
        for location in configuration["locations"]:
            if location["id"] == "annotation":
                location["used_bytes"] = annotation_bytes
                location["included_with_data"] = False
            elif location["id"] == "data":
                location["used_bytes"] = data_bytes
                location["included_with_data"] = False
            else:
                location["used_bytes"] = temporary_bytes
                location["included_with_data"] = workspace_is_within_data
        configuration["annotation_bytes"] = annotation_bytes
        configuration["active_data_root"] = str(self.state_dir)
        configuration["active_annotation_root"] = str(self.annotation_root)
        configuration["active_temporary_root"] = str(self.workspace_dir)
        return configuration

    def _ensure_active_storage_available(
        self, *, require_workspace: bool = False, require_annotation_root: bool = False
    ) -> None:
        if self.storage_restart_required():
            raise ValueError(
                "A storage location change is pending. Restart the workbench before starting or changing data."
            )
        if self.storage_migration_active():
            raise ValueError(
                "A storage migration is running. Wait for the verified copy to finish before starting or changing workbench data."
            )
        if not path_is_dir(self.state_dir) or not os.access(self.state_dir, os.W_OK):
            raise ValueError(
                "Sample Library & Cohort storage is unavailable. Reconnect the selected drive; "
                "the workbench will not create a fallback database."
            )
        if require_workspace and (
            not path_is_dir(self.workspace_dir)
            or not os.access(self.workspace_dir, os.W_OK)
        ):
            raise ValueError(
                "Temporary workspace is unavailable. Reconnect the selected drive; "
                "the workbench will not fall back to a different folder."
            )
        if require_annotation_root and (
            not path_is_dir(self.annotation_root)
            or not os.access(self.annotation_root, os.R_OK)
        ):
            raise ValueError(
                "Annotation dataset storage is unavailable. Reconnect the selected drive before downloading or annotating."
            )

    def storage_stats(self) -> dict:
        configuration = self.storage_configuration()
        if not path_is_dir(self.state_dir):
            # Keep the Storage page useful when a selected external drive was
            # unplugged after startup. Do not open SQLite or create a fallback
            # database merely to produce a status response.
            stats = {
                "state_dir": str(self.state_dir),
                "workspace_dir": str(self.workspace_dir),
                "locations": {
                    "database": 0,
                    "managed_library": 0,
                    "uploads": 0,
                    "cohort_cache": 0,
                    "wgs_review_cache": 0,
                    "projections": 0,
                    "logs": 0,
                    "other": 0,
                },
                "total_bytes": 0,
                "datasets": 0,
                "managed_unique_files": 0,
                "database_page_bytes": 0,
                "database_reclaimable_bytes": 0,
                "storage_error": "Sample Library & Cohort storage is unavailable. Reconnect the selected drive.",
            }
        else:
            try:
                stats = self.sample_library.storage_stats()
            except (OSError, sqlite3.Error) as exc:
                stats = {
                    "state_dir": str(self.state_dir),
                    "workspace_dir": str(self.workspace_dir),
                    "locations": {
                        "database": 0,
                        "managed_library": 0,
                        "uploads": 0,
                        "cohort_cache": 0,
                        "wgs_review_cache": 0,
                        "projections": 0,
                        "logs": 0,
                        "other": 0,
                    },
                    "total_bytes": 0,
                    "datasets": 0,
                    "managed_unique_files": 0,
                    "database_page_bytes": 0,
                    "database_reclaimable_bytes": 0,
                    "storage_error": f"Sample Library & Cohort storage could not be read: {exc}",
                }
        stats["storage_configuration"] = configuration
        stats["annotation_bytes"] = configuration["annotation_bytes"] or 0
        return stats

    @staticmethod
    def _storage_kind(value: object) -> str:
        kind = str(value or "").strip().lower()
        if kind not in STORAGE_KINDS:
            raise ValueError("storage location must be annotation, data, or temporary")
        return kind

    def _safe_storage_path(self, raw: object) -> Path:
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError("a local folder path is required")
        text = raw.strip()
        # A WSL user may paste the Windows spelling shown by the Storage page.
        # Translate the common mounted-drive form rather than creating a folder
        # literally named ``C:\\...`` beneath the Linux working directory.
        windows_path = re.match(r"^([A-Za-z]):[\\/]?(.*)$", text)
        if is_wsl() and windows_path:
            drive, remainder = windows_path.groups()
            text = "/mnt/" + drive.lower() + "/" + remainder.replace("\\", "/")
        elif windows_path and os.name != "nt":
            raise ValueError(
                "Windows drive paths can be used from Windows or WSL only; choose a local macOS/Linux path here"
            )
        try:
            path = Path(text).expanduser().resolve(strict=False)
        except OSError as exc:
            raise ValueError(f"storage path cannot be inspected: {text} ({exc})") from exc
        home = Path.home().resolve()
        protected = {Path(path.anchor), home, self.pipeline_root}
        if path in protected:
            raise ValueError("choose a dedicated subfolder, not a filesystem root, home folder, or the software folder")
        return path

    @staticmethod
    def _is_empty_directory(path: Path) -> bool:
        try:
            if not path.is_dir():
                return False
            ignored = {".DS_Store", "Thumbs.db", STORAGE_MARKER}
            return not any(item.name not in ignored for item in path.iterdir())
        except OSError:
            return False

    @staticmethod
    def _paths_overlap(first: Path, second: Path) -> bool:
        first = first.resolve(strict=False)
        second = second.resolve(strict=False)
        return first == second or first in second.parents or second in first.parents

    def _test_storage_path(self, kind: str, path: Path, *, allow_missing: bool = True) -> dict:
        try:
            path_exists = path.exists()
        except OSError as exc:
            raise ValueError(f"storage path cannot be inspected: {path} ({exc})") from exc
        parent = path if path_exists else path.parent
        if not path_is_dir(parent):
            raise ValueError(f"parent folder does not exist: {parent}")
        if kind == "annotation" and path_is_dir(path):
            if not os.access(path, os.R_OK):
                raise ValueError(f"annotation folder is not readable: {path}")
        else:
            if not os.access(parent, os.W_OK):
                raise ValueError(f"folder is not writable: {parent}")
            probe = parent / f".iei-workbench-write-test-{uuid.uuid4().hex}"
            try:
                probe.write_text("ok", encoding="utf-8")
                probe.unlink()
            except OSError as exc:
                raise ValueError(f"folder is not writable: {parent} ({exc})") from exc
        try:
            usage = shutil.disk_usage(parent)
            free_bytes, total_bytes = usage.free, usage.total
        except OSError:
            free_bytes = total_bytes = None
        return {
            "id": kind,
            "path": str(path),
            "parent": str(parent),
            "exists": path_is_dir(path),
            "empty": self._is_empty_directory(path) if path_exists else True,
            "free_bytes": free_bytes,
            "total_bytes": total_bytes,
            "warning": storage_path_warning(path, kind),
            "ready": True,
            "allow_missing": allow_missing,
        }

    def test_storage_location(self, payload: dict) -> dict:
        kind = self._storage_kind(payload.get("kind"))
        path = self._safe_storage_path(payload.get("path"))
        result = self._test_storage_path(kind, path)
        # Path-specific warning rather than the current configured-root warning.
        result["warning"] = storage_path_warning(path, kind)
        return result

    def _write_storage_marker(
        self, path: Path, kind: str, *, storage_id: str | None = None,
        migration_id: str = "",
    ) -> str:
        return self.storage_registry.write_marker(
            path, kind, storage_id=storage_id, service=SERVICE_VERSION,
            migration_id=migration_id,
        )

    def set_storage_location(self, payload: dict) -> dict:
        """Persist a future location without moving existing files.

        A restart is deliberately required: this prevents a running SQLite
        connection, tabix reader, or VEP process from seeing mixed roots.
        """
        kind = self._storage_kind(payload.get("kind"))
        if kind == "temporary" and bool(payload.get("follow_data_root")):
            self._ensure_storage_idle()
            self.storage_registry.set_root("temporary", None)
            restart_required = self.workspace_dir != self.state_dir
            return {
                "storage": self.storage_configuration(),
                "restart_required": restart_required,
                "message": (
                    "Temporary workspace will follow Sample Library & Cohort storage after restart."
                    if restart_required else "Temporary workspace already follows Sample Library & Cohort storage."
                ),
            }
        path = self._safe_storage_path(payload.get("path"))
        self._ensure_storage_idle()
        self._test_storage_path(kind, path)
        if path.exists() and not path.is_dir():
            raise ValueError("storage path exists but is not a folder")
        # Data and temporary locations are created as dedicated roots. Existing
        # non-empty resource roots are valid because users may already have
        # installed VEP datasets there.
        marker = self.storage_registry.marker(path) if path.exists() else None
        if marker is not None and marker.get("kind") != kind:
            raise ValueError(
                f"this folder is marked for {marker.get('kind') or 'another storage type'}, not {kind}"
            )
        if kind in {"data", "temporary"} and path.exists() and not self._is_empty_directory(path):
            if marker is None:
                raise ValueError(
                    "choose an empty dedicated folder or a previously managed workbench folder with a valid storage marker"
                )
        data_root = self.storage_registry.root("data")
        temporary_root = self.storage_registry.root("temporary")
        if kind == "temporary" and self._paths_overlap(path, data_root):
            raise ValueError(
                "choose a temporary workspace that is separate from and does not contain the Sample Library location"
            )
        if (
            kind == "data"
            and temporary_root != data_root
            and self._paths_overlap(path, temporary_root)
        ):
            raise ValueError(
                "choose a Sample Library location that is separate from the configured temporary workspace"
            )
        if not path.exists():
            path.mkdir(parents=True, exist_ok=False)
        storage_id = str((marker or {}).get("storage_id") or "")
        if not storage_id and os.access(path, os.W_OK):
            storage_id = self._write_storage_marker(path, kind)
        elif kind in {"data", "temporary"} and not storage_id:
            raise ValueError("managed Sample Library and temporary folders must be writable and carry an identity marker")
        self.storage_registry.set_root(kind, path, storage_id=storage_id)
        active = {"annotation": self.annotation_root, "data": self.state_dir, "temporary": self.workspace_dir}[kind]
        restart_required = active != path
        return {
            "storage": self.storage_configuration(),
            "restart_required": restart_required,
            "message": (
                "Location saved. Restart the workbench before starting another import, download, or annotation job."
                if restart_required else "This location is already active."
            ),
        }

    def _ensure_storage_idle(self, *, ignore_migration_reservation: bool = False) -> None:
        if self.storage_migration_active() or (
            self._migration_reserved and not ignore_migration_reservation
        ):
            raise ValueError("wait for the active storage migration before changing storage")
        running_jobs = [
            job["id"] for job in self.store.list(500)
            if job["status"] in {"queued", "running"}
        ]
        with self._resource_lock:
            running_resources = [
                job["resource_id"] for job in self._resource_jobs.values()
                if job["status"] in {"queued", "running"}
            ]
        with self._wgs_review_lock:
            running_wgs = [
                job["id"] for job in self._wgs_review_jobs.values()
                if job["status"] in {"queued", "running"}
            ]
        bulk_active = self._bulk_intake_active()
        if (running_jobs or running_resources or running_wgs
                or self.cohort.has_active_import() or bulk_active):
            details = []
            if running_jobs:
                details.append("annotation job")
            if running_resources:
                details.append("annotation dataset download")
            if running_wgs:
                details.append("whole-genome prefilter")
            if self.cohort.has_active_import():
                details.append("cohort import")
            if bulk_active:
                details.append("bulk import")
            raise ValueError("wait for the active " + ", ".join(details) + " before changing storage")

    def _bulk_intake_active(self) -> bool:
        """True while a bulk-intake job is queued or running.

        Storage migration snapshots the databases; snapshotting mid-batch
        and activating the copy would lose everything the queue imported
        after the snapshot, so bulk intake counts as active storage work.
        """
        try:
            with closing(self._bulk_intake_connect()) as connection:
                row = connection.execute(
                    "SELECT 1 FROM bulk_jobs WHERE status IN ('queued','running') LIMIT 1"
                ).fetchone()
            return row is not None
        except sqlite3.Error:
            thread = self._bulk_intake_thread
            return bool(thread and thread.is_alive())

    def storage_restart_required(self) -> bool:
        return any((
            self.storage_registry.root("annotation") != self.annotation_root,
            self.storage_registry.root("data") != self.state_dir,
            self.storage_registry.root("temporary") != self.workspace_dir,
        ))

    def begin_storage_mutation(self, *, allow_pending_restart: bool = False) -> None:
        """Reserve a normal state mutation against a migration snapshot."""
        with self._storage_transition:
            if self._migration_reserved or self.storage_migration_active():
                raise ValueError(
                    (self._migration_reservation_reason
                     or "a storage migration is running")
                    + "; wait for it to finish before changing workbench data"
                )
            if self.storage_restart_required() and not allow_pending_restart:
                raise ValueError(
                    "a storage location change is pending; restart the workbench before changing workbench data"
                )
            self._active_storage_mutations += 1
        # Tell a running cohort merge that this request is about to write,
        # so it pauses between chunks long enough for the lock to be taken
        # (audit M27).
        cohort_module.WRITE_COORDINATOR.enter()

    def end_storage_mutation(self) -> None:
        cohort_module.WRITE_COORDINATOR.leave()
        with self._storage_transition:
            self._active_storage_mutations = max(0, self._active_storage_mutations - 1)
            self._storage_transition.notify_all()

    def storage_migration_active(self) -> bool:
        with self._storage_lock:
            return any(
                job["status"] in {"queued", "running"}
                for job in self._storage_jobs.values()
            )

    def storage_migrations(self) -> list[dict]:
        with self._storage_lock:
            in_memory = [self._storage_job_copy(job) for job in sorted(
                self._storage_jobs.values(), key=lambda job: job["created_at"], reverse=True
            )]
        seen = {job["id"] for job in in_memory}
        persisted = [
            job for job in self.storage_registry.migration_history()
            if job.get("id") not in seen
        ]
        return sorted(
            [*in_memory, *persisted],
            key=lambda job: str(job.get("created_at") or ""),
            reverse=True,
        )

    @staticmethod
    def _storage_job_copy(job: dict) -> dict:
        value = dict(job)
        value.pop("_source", None)
        value.pop("_destination", None)
        return value

    def _update_storage_job(self, job_id: str, **values) -> None:
        with self._storage_lock:
            job = self._storage_jobs.get(job_id)
            if job:
                job.update(values)

    @property
    def restart_requested(self) -> bool:
        return self._restart_requested

    def with_job_progress(self, job: dict) -> dict:
        """Attach a live progress snapshot to a running job's payload.

        Computed only here, on poll: an unwatched run costs nothing. Any
        tracker failure degrades to a job without a bar, never a broken
        endpoint."""
        try:
            progress = self.job_progress.snapshot(job)
        except Exception:
            progress = None
        if progress is not None:
            job = {**job, "progress": progress}
        return job

    def software_update_install(self) -> dict:
        """Install the latest release. Refused while anything is running —
        replacing scripts underneath a live annotation job is the hazard."""
        with self._software_update_lock:
            return self._software_update_reserved(self.software_updater.install)

    def software_update_rollback(self) -> dict:
        with self._software_update_lock:
            return self._software_update_reserved(self.software_updater.rollback)

    def _software_update_reserved(self, operation):
        """Run an update operation under the migration-style reservation.

        Checking idleness once at entry is not enough: the download and
        verification can take minutes, and a job submitted in that window
        would have its scripts swapped underneath it. The reservation
        makes begin_storage_mutation refuse new jobs, imports, and
        migrations for the whole install, and the restart endpoint is
        likewise refused while it holds."""
        with self._storage_transition:
            if self._migration_reserved or self.storage_migration_active():
                raise ValueError(
                    "wait for the running storage migration to finish "
                    "before updating the software"
                )
            if self._active_storage_mutations > 1:
                # This request's own reservation accounts for one.
                raise ValueError(
                    "wait for the active import or data change to finish "
                    "before updating the software"
                )
            self._migration_reserved = True
            self._migration_reservation_reason = (
                "a software update is installing"
            )
        try:
            self._ensure_software_update_idle()
            return operation()
        finally:
            with self._storage_transition:
                self._migration_reserved = False
                self._migration_reservation_reason = ""
                self._storage_transition.notify_all()

    def _ensure_software_update_idle(self) -> None:
        try:
            self._ensure_storage_idle(ignore_migration_reservation=True)
        except ValueError as exc:
            raise ValueError(
                str(exc).replace(
                    "before changing storage", "before updating the software"
                )
            ) from exc

    def request_service_restart(self) -> dict:
        """Mark this process for supervised restart once nothing is running.

        Storage roots are deliberately frozen for the process lifetime, so a
        pending location change activates on the next start. The launcher
        restarts the service when it exits with RESTART_EXIT_CODE; the review
        UI stays open and reconnects when the service is back.
        """
        with self._storage_transition:
            if self._migration_reserved or self.storage_migration_active():
                raise ValueError(
                    "wait for the running storage migration to finish before restarting"
                )
            if self._active_storage_mutations:
                raise ValueError(
                    "wait for the active import or data change to finish before restarting"
                )
        try:
            self._ensure_storage_idle(ignore_migration_reservation=True)
        except ValueError as exc:
            raise ValueError(
                str(exc).replace("before changing storage", "before restarting")
            ) from exc
        relaunch = self.software_updater.full_relaunch_required()
        if relaunch["required"]:
            # Audit M21: restarting only the Python service would leave the
            # old interface bundle (or old dependencies) serving the new
            # API. Refuse, and say what actually finishes the update.
            raise ValueError(
                "An installed update changed the interface"
                + (" and its components" if relaunch["dependencies_updated"] else "")
                + "; an in-app restart cannot apply it. Close the GUIDE-IEI "
                "launcher window completely, then start GUIDE-IEI again."
            )
        self._restart_requested = True
        return {
            "restarting": True,
            "exit_code": RESTART_EXIT_CODE,
            "message": "The workbench service is restarting; pending storage locations become active.",
        }

    def lifecycle_status(self) -> dict:
        """Small, patient-identifier-free status for desktop and browser controls."""
        annotations = self.store.active_count()
        with self._resource_lock:
            downloads = sum(job["status"] in {"queued", "running"} for job in self._resource_jobs.values())
        blockers = []
        if self._engine_setup_reserved:
            blockers.append("annotation-engine setup")
        elif (self._migration_reserved and not self._quit_requested) or self.storage_migration_active():
            blockers.append("a storage migration or software update")
        if self._active_storage_mutations:
            blockers.append("an import or data change")
        with self._wgs_review_lock:
            if any(job["status"] in {"queued", "running"} for job in self._wgs_review_jobs.values()):
                blockers.append("a whole-genome prefilter")
        if self.cohort.has_active_import() or self._bulk_intake_active():
            blockers.append("a sample or cohort import")
        setup_marker = os.environ.get("IEI_DESKTOP_SETUP_MARKER")
        if setup_marker and Path(setup_marker).is_file():
            blockers.append("annotation-environment preparation")
        try:
            build = json.loads((self.pipeline_root / "desktop-build.json").read_text())
        except (OSError, ValueError):
            build = {}
        if not isinstance(build, dict):
            build = {}
        return {"service": "GUIDE-IEI", "instance_id": self._instance_id,
                "build_id": build.get("build_id"), "version": SERVICE_VERSION,
                "desktop_app": bool(build), "quitting": self._quit_requested,
                "annotations": annotations, "downloads": downloads,
                "blockers": blockers}

    def request_service_quit(self, payload: dict) -> dict:
        if payload.get("confirm") is not True:
            raise ValueError("Confirm quitting GUIDE-IEI before stopping the workbench.")
        if payload.get("instance_id") != self._instance_id:
            raise ValueError("The running workbench changed. Refresh before quitting.")
        with self._storage_transition:
            status = self.lifecycle_status()
            if status["blockers"]:
                raise ValueError("Wait for " + ", ".join(status["blockers"]) + " to finish before quitting.")
            # Serialize against new imports/downloads/updates while shutdown
            # runs. Active annotation and download processes use shutdown's
            # existing interruption/cleanup path, not an unscoped kill.
            self._migration_reserved = True
            self._migration_reservation_reason = "the workbench is shutting down"
            self._quit_requested = True
            self._restart_requested = False
        return {"quitting": True, "message": "Quit accepted. GUIDE-IEI is shutting down; this browser tab stays open. You can close it now. To start again, open the GUIDE-IEI app from Applications (or your original launcher)."}

    def start_storage_migration(self, payload: dict) -> dict:
        with self._storage_transition:
            if self._migration_reserved or self.storage_migration_active():
                raise ValueError("another storage migration is already running")
            if self.storage_restart_required():
                raise ValueError("restart the workbench before starting another storage migration")
            if self._active_storage_mutations:
                raise ValueError("wait for the active import or data change before migrating storage")
            self._migration_reserved = True
        try:
            return self._start_storage_migration_reserved(payload)
        finally:
            with self._storage_transition:
                self._migration_reserved = False
                self._storage_transition.notify_all()

    def _start_storage_migration_reserved(self, payload: dict) -> dict:
        kind = self._storage_kind(payload.get("kind"))
        destination = self._safe_storage_path(payload.get("path"))
        self._ensure_storage_idle(ignore_migration_reservation=True)
        source = {
            "annotation": self.annotation_root,
            "data": self.state_dir,
            "temporary": self.workspace_dir,
        }[kind]
        if not source.is_dir():
            raise ValueError(f"current {kind} storage is unavailable: {source}")
        if destination.exists():
            raise ValueError("migration destination must be a new folder path that does not yet exist")
        location_test = self._test_storage_path(kind, destination)
        if self._paths_overlap(source, destination):
            raise ValueError("migration destination must not be inside the current storage location")
        with self._storage_lock:
            if any(job["status"] in {"queued", "running"} for job in self._storage_jobs.values()):
                raise ValueError("another storage migration is already running")
            job_id = uuid.uuid4().hex
            total_bytes = self._migration_total_bytes(kind, source)
            # A small safety margin covers SQLite snapshots, metadata, and
            # filesystem allocation overhead. The final copy still verifies
            # every ordinary file before atomically switching the registry.
            required_free_bytes = total_bytes + max(512 * 1024 ** 2, total_bytes // 20)
            if (
                location_test["free_bytes"] is not None
                and location_test["free_bytes"] < required_free_bytes
            ):
                raise ValueError(
                    "not enough free space for a verified migration: "
                    f"need at least {required_free_bytes:,} bytes, "
                    f"found {location_test['free_bytes']:,} bytes"
                )
            job = {
                "id": job_id,
                "kind": kind,
                "source": str(source),
                "destination": str(destination),
                "status": "queued",
                "progress": 0.0,
                "bytes_total": total_bytes,
                "required_free_bytes": required_free_bytes,
                "bytes_copied": 0,
                "message": "Queued safe storage migration.",
                "created_at": utc_now(),
                "started_at": None,
                "finished_at": None,
                "error": "",
                "restart_required": False,
                "staging_path": str(
                    destination.parent / f".{destination.name}.iei-migrating-{job_id[:12]}"
                ),
                "_source": source,
                "_destination": destination,
            }
            self.storage_registry.record_migration(self._storage_job_copy(job))
            self._storage_jobs[job_id] = job
            thread = threading.Thread(
                target=self._run_storage_migration,
                args=(job_id,),
                name=f"storage-migration-{job_id[:8]}",
                daemon=True,
            )
            self._storage_threads[job_id] = thread
            thread.start()
            return self._storage_job_copy(job)

    @staticmethod
    def _migration_named_roots() -> tuple[str, ...]:
        return ("uploads", "cohort-vcf-cache", "cohort-staging", "wgs-review-cache")

    def _migration_total_bytes(self, kind: str, source: Path) -> int:
        if kind != "temporary":
            return _directory_size(source)
        return sum(_directory_size(source / name) for name in self._migration_named_roots())

    def _run_storage_migration(self, job_id: str) -> None:
        with self._storage_lock:
            job = self._storage_jobs.get(job_id)
            if not job:
                return
            kind = job["kind"]
            source = job["_source"]
            destination = job["_destination"]
        staging = destination.parent / f".{destination.name}.iei-migrating-{job_id[:12]}"
        self._update_storage_job(job_id, status="running", started_at=utc_now(), message="Preparing verified copy…")
        with self._storage_lock:
            self.storage_registry.record_migration(self._storage_job_copy(self._storage_jobs[job_id]))
        activated = False
        root_switched = False
        temporary_paths_rewritten = False
        try:
            if staging.exists() or destination.exists():
                raise ValueError("migration staging or destination path already exists")
            staging.mkdir(parents=False)

            def copied(amount: int) -> None:
                with self._storage_lock:
                    live = self._storage_jobs.get(job_id)
                    if not live:
                        return
                    total = max(1, int(live["bytes_total"]))
                    bytes_copied = int(live.get("bytes_copied", 0)) + amount
                    live.update({
                        "bytes_copied": bytes_copied,
                        "progress": min(99.0, 100.0 * bytes_copied / total),
                        "message": "Copying and verifying files…",
                    })

            if kind == "annotation":
                self._copy_tree_verified(source, staging, copied)
            elif kind == "data":
                self._copy_data_root_verified(source, staging, copied)
                self._rewrite_data_root_paths(staging, source, destination)
            else:
                self._copy_temporary_root_verified(source, staging, copied)

            storage_id = self._write_storage_marker(
                staging, kind, migration_id=job_id
            )
            if self._stop.is_set():
                raise RuntimeError("the local service stopped before migration could be activated")
            staging.replace(destination)
            activated = True
            if kind == "temporary":
                # The copied workspace must exist before live database paths
                # can point at it. This update is transactional and is reversed
                # if registry activation fails.
                self._rewrite_temporary_paths(source, destination)
                temporary_paths_rewritten = True
            # The copy inherited the ambient umask (0755 dirs, 0644 files,
            # fresh 0644 sqlite backups): tighten the destination before
            # success — the window until the restart re-runs __init__
            # hardening must not leave PHI world-readable at the new root.
            self._harden_root(destination)
            self.storage_registry.set_root(kind, destination, storage_id=storage_id)
            root_switched = True
            with self._storage_lock:
                live = self._storage_jobs[job_id]
                live.update(
                    status="succeeded",
                    progress=100.0,
                    bytes_copied=max(int(live["bytes_total"]), int(live["bytes_copied"])),
                    message="Copy verified. The original location was preserved; restart the workbench to use the new location.",
                    finished_at=utc_now(),
                    restart_required=True,
                    original_retained=True,
                    staging_path="",
                )
                completed = self._storage_job_copy(self._storage_jobs[job_id])
            try:
                self.storage_registry.record_migration(completed)
            except Exception as history_exc:
                # The root switch itself was already persisted by set_root;
                # failure to append audit history — of ANY exception type, not
                # just OSError — must not roll it back or delete the verified
                # destination the registry now points at.
                self._update_storage_job(
                    job_id,
                    message=(
                        "Copy verified and location activated; migration history could not be updated: "
                        f"{history_exc}. Restart the workbench to use the new location."
                    ),
                )
                return
        except Exception as exc:
            rollback_error = ""
            if temporary_paths_rewritten:
                try:
                    self._rewrite_temporary_paths(destination, source)
                except Exception as rollback_exc:  # pragma: no cover - catastrophic I/O failure
                    rollback_error = f"; temporary path rollback failed: {rollback_exc}"
            if root_switched:
                # The registry already points at the destination: deleting it
                # here would destroy the live storage root (total loss of the
                # migrated cohort database and annotation files). Preserve it
                # and surface the follow-up failure instead.
                self._update_storage_job(
                    job_id,
                    status="failed",
                    message=(
                        "Migration activated the new location but a follow-up "
                        f"step failed: {exc}. The verified copy at "
                        f"{destination} was preserved and remains active."
                    ),
                    finished_at=utc_now(),
                    error=str(exc) + rollback_error,
                    restart_required=True,
                )
                with self._storage_lock:
                    failed = self._storage_job_copy(self._storage_jobs[job_id])
                try:
                    self.storage_registry.record_migration(failed)
                except Exception:
                    pass
                return
            cleanup_target = destination if activated else staging
            try:
                if cleanup_target.exists():
                    shutil.rmtree(cleanup_target)
            except OSError as cleanup_exc:
                rollback_error += f"; partial copy remains at {cleanup_target}: {cleanup_exc}"
            self._update_storage_job(
                job_id,
                status="failed",
                message="Storage migration failed; the original location remains active.",
                finished_at=utc_now(),
                error=str(exc) + rollback_error,
                staging_path=str(cleanup_target) if cleanup_target.exists() else "",
            )
            with self._storage_lock:
                failed = self._storage_job_copy(self._storage_jobs[job_id])
            self.storage_registry.record_migration(failed)

    def _copy_temporary_root_verified(self, source: Path, destination: Path, copied: Callable[[int], None]) -> None:
        for name in self._migration_named_roots():
            child = source / name
            if child.exists():
                self._copy_tree_verified(child, destination / name, copied)

    def _copy_data_root_verified(self, source: Path, destination: Path, copied: Callable[[int], None]) -> None:
        database_names = {"cohort.sqlite3", "workbench.sqlite3"}

        def skip(relative: Path) -> bool:
            name = relative.name
            return (
                relative.parent == Path(".")
                and (
                    name in database_names
                    or name.endswith((".sqlite3-wal", ".sqlite3-shm", ".sqlite3-journal"))
                )
            )

        self._copy_tree_verified(source, destination, copied, skip=skip)
        for name in database_names:
            source_database = source / name
            if source_database.is_file():
                destination_database = destination / name
                self._backup_sqlite(source_database, destination_database)
                copied(source_database.stat().st_size)

    @staticmethod
    def _backup_sqlite(source: Path, destination: Path) -> None:
        source_connection = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
        destination_connection = sqlite3.connect(destination)
        try:
            source_connection.backup(destination_connection)
            result = destination_connection.execute("PRAGMA integrity_check").fetchone()[0]
            if result != "ok":
                raise RuntimeError(f"SQLite integrity check failed for {source.name}: {result}")
            if os.name == "posix":
                try:
                    os.chmod(destination, 0o600)
                except OSError:
                    pass
        finally:
            destination_connection.close()
            source_connection.close()

    def _copy_tree_verified(
        self,
        source: Path,
        destination: Path,
        copied: Callable[[int], None],
        *,
        skip: Callable[[Path], bool] | None = None,
    ) -> None:
        source = source.resolve()
        for root, directories, files in os.walk(source):
            if self._stop.is_set():
                raise RuntimeError("the local service stopped during storage migration")
            root_path = Path(root)
            relative_root = root_path.relative_to(source)
            directories[:] = [
                name for name in directories
                if not (skip and skip(relative_root / name))
            ]
            target_root = destination / relative_root
            target_root.mkdir(parents=True, exist_ok=True)
            # ``os.walk`` does not descend into directory symlinks. Preserve
            # their link text explicitly rather than silently making an empty
            # destination directory.
            for name in list(directories):
                source_child = root_path / name
                if source_child.is_symlink():
                    self._copy_symlink_verified(source_child, target_root / name)
                    directories.remove(name)
            for name in files:
                relative = relative_root / name
                if skip and skip(relative):
                    continue
                self._copy_file_verified(root_path / name, target_root / name)
                try:
                    copied((root_path / name).stat().st_size)
                except FileNotFoundError:
                    pass

    def _copy_file_verified(self, source: Path, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.is_symlink():
            self._copy_symlink_verified(source, destination)
            return
        source_before = source.stat()
        source_hash = hashlib.sha256()
        # "wb", not "xb": writes land only inside this job's own staging
        # directory (destination existence is guarded at migration start), and
        # exclusive-create made any retry over a leftover partial copy abort
        # on the first already-copied file.
        with source.open("rb") as input_handle, destination.open("wb") as output_handle:
            while block := input_handle.read(8 * 1024 * 1024):
                if self._stop.is_set():
                    raise RuntimeError("the local service stopped during storage migration")
                output_handle.write(block)
                source_hash.update(block)
            output_handle.flush()
            os.fsync(output_handle.fileno())
        source_after = source.stat()
        if (
            source_before.st_size != source_after.st_size
            or source_before.st_mtime_ns != source_after.st_mtime_ns
        ):
            raise RuntimeError(f"source changed during storage migration: {source}")
        destination_hash = hashlib.sha256()
        with destination.open("rb") as handle:
            while block := handle.read(8 * 1024 * 1024):
                if self._stop.is_set():
                    raise RuntimeError("the local service stopped during storage migration")
                destination_hash.update(block)
        if source_hash.digest() != destination_hash.digest():
            raise RuntimeError(f"checksum verification failed for {source}")
        shutil.copystat(source, destination, follow_symlinks=False)

    @staticmethod
    def _copy_symlink_verified(source: Path, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        link_target = os.readlink(source)
        os.symlink(link_target, destination)
        if not destination.is_symlink() or os.readlink(destination) != link_target:
            raise RuntimeError(f"symlink verification failed for {source}")

    @staticmethod
    def _rewrite_prefixed_path(value: object, old_root: Path, new_root: Path) -> str | None:
        if not isinstance(value, str) or not value:
            return None
        try:
            old_value = Path(value).expanduser().resolve(strict=False)
            relative = old_value.relative_to(old_root)
        except ValueError:
            return None
        return str(new_root / relative)

    def _rewrite_data_root_paths(self, destination_root: Path, old_root: Path, new_root: Path) -> None:
        cohort_database = destination_root / "cohort.sqlite3"
        if cohort_database.is_file():
            connection = sqlite3.connect(cohort_database)
            try:
                # Isolate each column: on an older schema a missing column
                # raises OperationalError, and one column's failure must not
                # abort the remaining rewrites (leaving the table half-pointed
                # at the old root).
                for column in ("managed_path", "managed_index_path", "original_path"):
                    try:
                        rows = connection.execute(
                            f"SELECT id,{column} FROM library_datasets WHERE {column} IS NOT NULL"
                        ).fetchall()
                        for row_id, value in rows:
                            replacement = self._rewrite_prefixed_path(value, old_root, new_root)
                            if replacement:
                                # Library paths under the selected data root
                                # are intentionally portable across a drive
                                # mount-path change.
                                replacement = Path(replacement).relative_to(new_root).as_posix()
                                connection.execute(
                                    f"UPDATE library_datasets SET {column}=? WHERE id=?",
                                    (replacement, row_id),
                                )
                    except sqlite3.OperationalError:
                        continue
                try:
                    for column in ("path", "prepared_path", "index_path"):
                        rows = connection.execute(
                            f"SELECT id,{column} FROM cohort_files WHERE {column} IS NOT NULL"
                        ).fetchall()
                        for row_id, value in rows:
                            replacement = self._rewrite_prefixed_path(value, old_root, new_root)
                            if replacement:
                                connection.execute(
                                    f"UPDATE cohort_files SET {column}=? WHERE id=?",
                                    (replacement, row_id),
                                )
                except sqlite3.OperationalError:
                    pass
                connection.commit()
            finally:
                connection.close()
        workbench_database = destination_root / "workbench.sqlite3"
        if workbench_database.is_file():
            connection = sqlite3.connect(workbench_database)
            try:
                try:
                    for column in ("input_path", "output_path", "config_path", "log_path", "final_output_path"):
                        rows = connection.execute(
                            f"SELECT id,{column} FROM annotation_jobs WHERE {column} IS NOT NULL"
                        ).fetchall()
                        for row_id, value in rows:
                            replacement = self._rewrite_prefixed_path(value, old_root, new_root)
                            if replacement:
                                connection.execute(
                                    f"UPDATE annotation_jobs SET {column}=? WHERE id=?",
                                    (replacement, row_id),
                                )
                except sqlite3.OperationalError:
                    pass
                connection.commit()
            finally:
                connection.close()

    def _rewrite_temporary_paths(self, old_root: Path, new_root: Path) -> None:
        # The service remains on the old workspace until restart, but database
        # records are updated now so the new process will reopen the copied
        # indexed VCF cache rather than a stale absolute path.
        if self.cohort.database_path.is_file():
            connection = sqlite3.connect(self.cohort.database_path)
            try:
                try:
                    for column in ("path", "prepared_path", "index_path"):
                        rows = connection.execute(
                            f"SELECT id,{column} FROM cohort_files WHERE {column} IS NOT NULL"
                        ).fetchall()
                        for row_id, value in rows:
                            replacement = self._rewrite_prefixed_path(value, old_root, new_root)
                            if replacement:
                                connection.execute(
                                    f"UPDATE cohort_files SET {column}=? WHERE id=?",
                                    (replacement, row_id),
                                )
                except sqlite3.OperationalError:
                    pass
                try:
                    rows = connection.execute(
                        "SELECT id,original_path FROM library_datasets WHERE original_path IS NOT NULL"
                    ).fetchall()
                    for row_id, value in rows:
                        replacement = self._rewrite_prefixed_path(value, old_root, new_root)
                        if replacement:
                            # A temporary source is outside the Sample Library
                            # root, so it remains an absolute path after move.
                            connection.execute(
                                "UPDATE library_datasets SET original_path=? WHERE id=?",
                                (replacement, row_id),
                            )
                except sqlite3.OperationalError:
                    pass
                connection.commit()
            finally:
                connection.close()

    def open_storage_location(self, payload: dict) -> dict:
        kind = self._storage_kind(payload.get("kind"))
        path = self.storage_registry.root(kind)
        if not path.is_dir():
            raise ValueError(f"configured {kind} storage is unavailable: {path}")
        try:
            if os.name == "nt":
                os.startfile(str(path))  # type: ignore[attr-defined]
            elif platform.system() == "Darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except OSError as exc:
            raise ValueError(f"could not open folder: {exc}") from exc
        return {"opened": str(path)}

    def resource_downloads(self) -> list[dict]:
        with self._resource_lock:
            jobs = [
                self._resource_job_copy(job)
                for job in self._resource_jobs.values()
            ]
        # Timestamps have second precision. A fast retry in that same second
        # must sort ahead of its failed predecessor in the setup UI.
        return sorted(reversed(jobs), key=lambda job: job["created_at"], reverse=True)

    def start_engine_setup(self, payload: dict) -> dict:
        if payload.get("confirm") is not True:
            raise ValueError("Confirm annotation-engine setup before installing tools.")
        if platform.system() != "Darwin":
            raise ValueError("On Windows, start Docker Desktop and enable WSL integration. On Linux, install/start your configured container runtime, then retry recommended dataset setup.")
        # This endpoint owns the reservation itself, just like migration. It
        # must not race annotation, imports, another setup, or an update.
        with self._storage_transition:
            if self._active_storage_mutations or self.storage_restart_required():
                raise ValueError("Finish the active data change or pending storage restart before setting up the annotation engine.")
            self._ensure_storage_idle()
            if self.store.active_count():
                raise ValueError("Wait for queued and running annotations before setting up the engine.")
            self._migration_reserved = True
            self._engine_setup_reserved = True
            self._migration_reservation_reason = "annotation-engine setup is running"
        try:
            config_path = self._write_resource_config("annotation_engine")
            command = ["env", f"IEI_PYTHON_BIN={sys.executable}", "PYTHONUNBUFFERED=1",
                       f"IEI_CONTAINER_WORK_DIR={workspace_directory(self.state_dir)}",
                       "bash", str(self.pipeline_root / "scripts/setup_environment.sh"),
                       "--install", "--yes", "--engine-only", "--config", str(config_path)]
            return self._start_resource_job("annotation_engine", command, "installation", (config_path,))
        except BaseException:
            self._finish_engine_setup()
            raise

    def _finish_engine_setup(self) -> None:
        with self._storage_transition:
            self._engine_setup_reserved = False
            self._migration_reserved = False
            self._migration_reservation_reason = ""
            self._storage_transition.notify_all()

    def cancel_engine_setup(self, payload: dict) -> dict:
        if payload.get("confirm") is not True:
            raise ValueError("Confirm stopping annotation-engine preparation.")
        job_id = str(payload.get("job_id") or "")
        with self._resource_lock:
            job = self._resource_jobs.get(job_id)
            if not job or job.get("resource_id") != "annotation_engine":
                raise ValueError("annotation-engine setup job not found")
            if job["status"] in {"queued", "running"}:
                job["_cancel_requested"] = True
                process = self._resource_processes.get(job_id)
            else:
                process = None
        if process is not None:
            threading.Thread(target=self._stop_resource_process, args=(process, job_id),
                             name="stop-engine-setup", daemon=True).start()
        return {"stopping": True}

    def start_resource_download(self, resource_id: str) -> dict:
        self._ensure_active_storage_available(require_annotation_root=True)
        if resource_id not in RESOURCE_DOWNLOAD_COMMANDS:
            raise ValueError(f"resource cannot be downloaded from the UI: {resource_id}")
        installed_profile = False
        if resource_id in {"recommended_exome", "recommended_wgs"}:
            profile_name = (
                "exome" if resource_id == "recommended_exome" else "whole_genome"
            )
            installed_profile = bool(
                self._annotation_profile()
                .get("recommended_profiles", {})
                .get(profile_name, {})
                .get("installed", False)
            )
        # Avoid walking large installed cache directories merely to calculate
        # first-install disk allowance. The fast installer will record a
        # successful no-op and point users to the separate update action.
        if not installed_profile:
            self._ensure_annotation_download_space(resource_id)
        specification = RESOURCE_DOWNLOAD_COMMANDS[resource_id]
        if resource_id == "gene_knowledge":
            destination = self.annotation_root / "gene-knowledge" / "gene_knowledge_public.sqlite3"
            command = [
                "bash", str(self.pipeline_root / specification[0]), str(destination),
                str(destination.with_name("manifest.json")),
            ]
        elif resource_id in {"screen_context", "screen_context_build"}:
            # Both scripts take a data root, not a config file. Downloads and
            # prepared matrices live under annotation storage; the post-job
            # hook registers the pointer.
            command = [
                "bash", str(self.pipeline_root / specification[0]),
                str(self.annotation_root / "screen-context"),
            ]
        else:
            config_path = self._write_resource_config(resource_id)
            command = [
                "bash", str(self.pipeline_root / specification[0]), str(config_path),
                *specification[1:],
            ]
        return self._start_resource_job(resource_id, command, "download")

    def _ensure_annotation_download_space(
        self,
        resource_id: str,
        output_overrides: dict[tuple[str, ...], Path] | None = None,
    ) -> None:
        output_specs = RESOURCE_DOWNLOAD_OUTPUTS.get(resource_id)
        if not output_specs:
            return
        config = self._load_config(
            self.pipeline_root / "config" / "annotation.config.yaml"
        )
        locations: dict[int, dict] = {}
        for key_path, required, is_directory in output_specs:
            resolved = (output_overrides or {}).get(key_path)
            if resolved is None:
                value: object = config
                for key in key_path:
                    value = value.get(key) if isinstance(value, dict) else None
                resolved = self._resolved_reference_path(value)
            destination = (
                resolved
                if resolved is not None and is_directory
                else resolved.parent if resolved is not None
                else self.annotation_root
            )
            probe = destination
            while not probe.exists() and probe != probe.parent:
                probe = probe.parent
            if not probe.is_dir():
                raise ValueError(
                    f"configured destination parent does not exist for {resource_id}: {destination}"
                )
            try:
                device = probe.stat().st_dev
            except OSError as exc:
                raise ValueError(
                    f"configured destination cannot be inspected for {resource_id}: {destination} ({exc})"
                ) from exc
            entry = locations.setdefault(device, {
                "probe": probe,
                "required": 0,
                "destinations": [],
            })
            credit = self._resumable_download_credit(resolved, is_directory)
            entry["required"] += max(0, required - credit)
            entry["destinations"].append(destination)

        for entry in locations.values():
            probe = entry["probe"]
            required = entry["required"]
            try:
                free = shutil.disk_usage(probe).free
            except OSError as exc:
                raise ValueError(
                    "available space for annotation datasets could not be measured: "
                    f"{probe} ({exc})"
                ) from exc
            if free < required:
                destination_text = ", ".join(
                    str(path) for path in dict.fromkeys(entry["destinations"])
                )
                raise ValueError(
                    f"{resource_id} needs at least {required / GIB:.0f} GiB free at "
                    f"{destination_text}; only {free / GIB:.1f} GiB is available. "
                    "Choose a larger annotation location in Storage first."
                )

    @staticmethod
    def _resumable_download_credit(path: Path | None, is_directory: bool) -> int:
        if path is None:
            return 0
        candidates: list[Path] = []
        if is_directory:
            # Bulk installers may target an extracted VEP cache or a directory
            # containing an already installed snapshot. Count allocated files
            # rather than demanding the full first-install allowance again.
            # _directory_size already counts everything allocated inside
            # the directory (including in-progress .part files); probing a
            # hardcoded ClinVar filename mis-credited every other
            # directory resource (VEP cache, dbNSFP).
            directory_credit = _directory_size(path) if path.is_dir() else 0
        else:
            directory_credit = 0
            candidates.append(path)
        credit = directory_credit
        for candidate in candidates:
            try:
                if candidate.is_file():
                    credit = max(credit, candidate.stat().st_size)
            except OSError:
                pass
            part = Path(str(candidate) + ".part")
            try:
                if part.is_file():
                    credit = max(credit, part.stat().st_size)
            except OSError:
                pass
            # parallel_fetch preallocates this sparse file to the complete
            # remote size. Its apparent size is therefore not download
            # progress. When range metadata is unavailable, credit only blocks
            # already allocated on disk; those blocks will be overwritten and
            # do not require a second allocation during a conservative restart.
            parallel = Path(str(candidate) + ".parallel")
            try:
                if parallel.is_file():
                    status = parallel.stat()
                    allocated = int(getattr(status, "st_blocks", 0) or 0) * 512
                    credit = max(credit, min(status.st_size, allocated))
            except OSError:
                pass
            state_path = Path(str(candidate) + ".ranges.json")
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
                total = int(state.get("size") or 0)
                chunk_size = int(state.get("chunk_size") or 0)
                completed = {int(index) for index in state.get("completed", [])}
                completed_bytes = sum(
                    max(0, min(chunk_size, total - index * chunk_size))
                    for index in completed
                )
                credit = max(credit, completed_bytes)
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                pass
        return credit

    def start_promoterai_preparation(self, payload: dict) -> dict:
        self._ensure_active_storage_available(require_annotation_root=True)
        source_value = payload.get("source_dir") if isinstance(payload, dict) else None
        if not isinstance(source_value, str) or not source_value.strip():
            raise ValueError("select the local folder containing the two Illumina PromoterAI files")
        source_dir = Path(source_value).expanduser().resolve()
        if not source_dir.is_dir():
            raise ValueError(f"PromoterAI source folder does not exist: {source_dir}")
        required_files = [
            source_dir / "tss.tsv",
            source_dir / "promoterAI_tss500.tsv.gz",
        ]
        missing = [str(path) for path in required_files if not path.is_file() or path.stat().st_size == 0]
        if missing:
            raise ValueError("PromoterAI source folder is missing: " + ", ".join(missing))
        config_path = self._write_resource_config("promoterai")
        command = [
            "bash",
            str(self.pipeline_root / "scripts" / "prepare_promoterai.sh"),
            str(source_dir),
            str(config_path),
            "--remove-source-after-success",
        ]
        return self._start_resource_job("promoterai", command, "preparation")

    @staticmethod
    def _selected_source_path(raw: str) -> Path:
        """Normalize a native picker result, including Windows paths in WSL."""
        text = raw.strip()
        windows_path = re.match(r"^([A-Za-z]):[\\/]?(.*)$", text)
        if is_wsl() and windows_path:
            drive, remainder = windows_path.groups()
            text = "/mnt/" + drive.lower() + "/" + remainder.replace("\\", "/")
        return Path(text).expanduser().resolve()

    def choose_local_resource_source(self, payload: dict) -> dict:
        """Open the workstation's native file/folder chooser.

        Browsers intentionally hide absolute local paths. Because this service
        is loopback-only, a native chooser is both faster and safer than
        uploading tens of gigabytes through a browser merely to recover a path.
        """
        resource_id = str(payload.get("resource_id") or "").strip().lower()
        choices = {
            "dbnsfp": ("folder", "Choose the unzipped dbNSFP release folder"),
            "promoterai": ("folder", "Choose the folder containing the two PromoterAI files"),
            "logofunc": ("file", "Choose the downloaded LoGoFunc .csv.gz file"),
            "funcvep": ("file", "Choose the downloaded official FuncVEP .zip archive"),
            "omim": ("folder", "Choose the folder containing the four OMIM data files"),
            "genia": ("files", "Choose one or more GenIA export files"),
            "storage_annotation": ("folder", "Choose the folder for annotation datasets"),
            "storage_data": ("folder", "Choose the folder for the Sample Library & Cohort"),
            "storage_temporary": ("folder", "Choose the folder for the temporary workspace"),
        }
        if resource_id not in choices:
            raise ValueError(
                "resource picker supports dbNSFP, PromoterAI, LoGoFunc, FuncVEP, OMIM, GenIA, or a storage location"
            )
        selection_type, prompt = choices[resource_id]
        system = platform.system()
        command: list[str]
        if system == "Darwin":
            escaped_prompt = prompt.replace("\\", "\\\\").replace('"', '\\"')
            if selection_type == "files":
                script = (
                    f'set chosenFiles to choose file with prompt "{escaped_prompt}" '
                    "with multiple selections allowed\n"
                    'set outputText to ""\n'
                    "repeat with chosenFile in chosenFiles\n"
                    "set outputText to outputText & POSIX path of chosenFile & linefeed\n"
                    "end repeat\n"
                    "return outputText"
                )
                command = ["osascript", "-e", script]
            else:
                verb = "choose folder" if selection_type == "folder" else "choose file"
                command = [
                    "osascript", "-e",
                    f'POSIX path of ({verb} with prompt "{escaped_prompt}")',
                ]
        elif os.name == "nt" or is_wsl():
            powershell = "powershell.exe"
            if is_wsl() and not shutil.which(powershell):
                candidate = Path(
                    "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
                )
                powershell = str(candidate) if candidate.is_file() else powershell
            if selection_type == "folder":
                script = (
                    "Add-Type -AssemblyName System.Windows.Forms;"
                    "$d=New-Object System.Windows.Forms.FolderBrowserDialog;"
                    f"$d.Description={json.dumps(prompt)};"
                    "if($d.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK)"
                    "{[Console]::Write($d.SelectedPath)}"
                )
            else:
                file_filter = (
                    "ZIP archive (*.zip)|*.zip|All files (*.*)|*.*"
                    if resource_id in {"funcvep", "alphagenome_avi"}
                    else "GenIA exports (*.csv;*.tsv;*.vcf;*.vcf.gz)|*.csv;*.tsv;*.vcf;*.vcf.gz|All files (*.*)|*.*"
                    if resource_id == "genia"
                    else "Compressed table (*.csv.gz)|*.csv.gz|All files (*.*)|*.*"
                )
                script = "".join([
                    "Add-Type -AssemblyName System.Windows.Forms;"
                    "$d=New-Object System.Windows.Forms.OpenFileDialog;"
                    f"$d.Title={json.dumps(prompt)};"
                    f"$d.Filter={json.dumps(file_filter)};",
                    "$d.Multiselect=$true;" if selection_type == "files" else "",
                    "if($d.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK)",
                    (
                        "{foreach($f in $d.FileNames){[Console]::WriteLine($f)}}"
                        if selection_type == "files"
                        else "{[Console]::Write($d.FileName)}"
                    ),
                ])
            command = [powershell, "-NoProfile", "-STA", "-Command", script]
        elif shutil.which("zenity"):
            command = ["zenity", "--file-selection", f"--title={prompt}"]
            if selection_type == "folder":
                command.append("--directory")
            elif selection_type == "files":
                command.extend(["--multiple", "--separator=\n"])
        elif shutil.which("kdialog"):
            command = [
                "kdialog",
                "--getexistingdirectory" if selection_type == "folder" else "--getopenfilename",
                str(Path.home()),
            ]
            if selection_type == "files":
                command.extend(["--multiple", "--separate-output"])
        else:
            raise ValueError(
                "a native file chooser is unavailable; install zenity or kdialog, then try again"
            )
        try:
            result = subprocess.run(
                command, capture_output=True, text=True, timeout=600, check=False
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ValueError(f"could not open the local file chooser: {exc}") from exc
        selected = result.stdout.strip()
        if result.returncode != 0 or not selected:
            diagnostic = f"{result.stderr}\n{result.stdout}".lower()
            if not selected and (
                result.returncode in {0, 1}
                or "cancel" in diagnostic
                or "user canceled" in diagnostic
            ):
                return {"cancelled": True, "resource_id": resource_id}
            raise ValueError(result.stderr.strip() or "the local file chooser did not return a selection")
        selected_values = selected.splitlines() if selection_type == "files" else [selected]
        paths = [self._selected_source_path(value) for value in selected_values if value.strip()]
        valid = all(
            path.is_dir() if selection_type == "folder" else path.is_file()
            for path in paths
        )
        if not paths or not valid:
            raise ValueError(f"one or more selected {selection_type} are no longer available")
        if selection_type == "files":
            inspection = self.genia.inspect(paths)
            return {
                "cancelled": False,
                "resource_id": resource_id,
                "selection_type": selection_type,
                "paths": [str(path) for path in paths],
                "names": [path.name for path in paths],
                "detected": inspection.get("files", []),
            }
        path = paths[0]
        return {
            "cancelled": False,
            "resource_id": resource_id,
            "selection_type": selection_type,
            "path": str(path),
            "name": path.name,
        }

    @staticmethod
    def _authorized_dbnsfp_url(raw: str) -> tuple[str, str, str]:
        """Unwrap and constrain a user-authorized dbNSFP academic link."""
        value = html.unescape(raw.strip()).strip("<>").strip()
        if not value or len(value) > 20_000:
            raise ValueError("paste the dbNSFP download link from the academic instruction email")
        parsed = urlparse(value)
        host = (parsed.hostname or "").lower()
        if host == "safelinks.protection.outlook.com" or host.endswith(
            ".safelinks.protection.outlook.com"
        ):
            target = (parse_qs(parsed.query).get("url") or [""])[0].strip()
            parsed = urlparse(target)
            host = (parsed.hostname or "").lower()
        try:
            port = parsed.port
        except ValueError as exc:
            raise ValueError("the dbNSFP download link has an invalid port") from exc
        if (
            parsed.scheme.lower() != "https"
            or host != "dist.genos.us"
            or port not in {None, 443}
            or parsed.username is not None
            or parsed.password is not None
            or not parsed.path.startswith("/academic/")
        ):
            raise ValueError(
                "paste the authorized dbNSFP link from the instruction email "
                "(it must resolve to https://dist.genos.us/academic/…)"
            )
        filename = unquote(Path(parsed.path).name)
        match = _DBNSFP_GRCH38_FILENAME.fullmatch(filename)
        if not match:
            raise ValueError(
                "the authorized link must end in dbNSFP<version>_grch38.gz; "
                "do not use the similarly named _grch37.gz link"
            )
        return parsed._replace(fragment="").geturl(), filename, match.group("version")

    def _write_resource_secret(self, prefix: str, value: str) -> Path:
        self.resource_secrets_dir.mkdir(parents=True, exist_ok=True)
        if os.name == "posix":
            os.chmod(self.resource_secrets_dir, 0o700)
        path = self.resource_secrets_dir / f"{prefix}.{uuid.uuid4().hex}.secret"
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(value + "\n")
        return path

    def start_dbnsfp_download(self, payload: dict) -> dict:
        self._ensure_active_storage_available(require_annotation_root=True)
        raw_url = payload.get("download_url") if isinstance(payload, dict) else None
        if not isinstance(raw_url, str):
            raise ValueError("paste the dbNSFP download link from the academic instruction email")
        authorized_url, filename, _version = self._authorized_dbnsfp_url(raw_url)
        destination = self.annotation_root / "dbnsfp" / filename
        self._ensure_annotation_download_space(
            "dbnsfp_download",
            {("plugins", "dbNSFP", "path"): destination},
        )
        secret_path = self._write_resource_secret("dbnsfp-url", authorized_url)
        try:
            config_path = self._write_resource_config(
                "dbnsfp", dbnsfp_filename=filename
            )
            command = [
                "bash",
                str(self.pipeline_root / "scripts" / "download_dbnsfp.sh"),
                str(secret_path),
                str(config_path),
            ]
            return self._start_resource_job(
                "dbnsfp", command, "download", sensitive_paths=(secret_path,)
            )
        except Exception:
            # A failure before the worker owns the file must not retain the
            # private academic access URL until the next service restart.
            secret_path.unlink(missing_ok=True)
            raise

    def start_logofunc_preparation(self, payload: dict) -> dict:
        self._ensure_active_storage_available(require_annotation_root=True)
        source_value = payload.get("source_path") if isinstance(payload, dict) else None
        if not isinstance(source_value, str) or not source_value.strip():
            raise ValueError("select the downloaded LoGoFunc file or its containing folder")
        source_path = Path(source_value).expanduser().resolve()
        if not source_path.exists():
            raise ValueError(f"LoGoFunc source does not exist: {source_path}")
        config_path = self._write_resource_config("logofunc")
        command = [
            "bash",
            str(self.pipeline_root / "scripts" / "prepare_logofunc.sh"),
            str(source_path),
            str(config_path),
        ]
        return self._start_resource_job("logofunc", command, "preparation")

    def start_avi_preparation(self, payload: dict) -> dict:
        # Older open UI tabs get an actionable response, not a large conversion.
        raise ValueError("AVI ZIP import has been replaced by the prepared mirror. Refresh the page and use Download / resume on the AlphaGenome AVI card.")

    def start_funcvep_preparation(self, payload: dict) -> dict:
        """Download or prepare a user-authorized FuncVEP archive locally."""
        self._ensure_active_storage_available(require_annotation_root=True)
        if payload.get("license_accepted") is not True:
            raise ValueError(
                "confirm that you reviewed the FuncVEP license and that your intended use is permitted"
            )
        config_path = self._write_resource_config("funcvep")
        source_value = payload.get("source_path") if isinstance(payload, dict) else None
        if isinstance(source_value, str) and source_value.strip():
            source_path = Path(source_value).expanduser().resolve()
            if not source_path.is_file():
                raise ValueError(f"FuncVEP source archive does not exist: {source_path}")
            if source_path.suffix.lower() != ".zip":
                raise ValueError("select the official FuncVEP .zip archive")
            self._ensure_annotation_download_space("funcvep_preparation")
            command = [
                "bash",
                str(self.pipeline_root / "scripts" / "prepare_funcvep.sh"),
                str(source_path),
                str(config_path),
                "--acknowledge-license",
            ]
        else:
            archive_path = self.annotation_root / "funcvep" / FUNCVEP_ARCHIVE_NAME
            self._ensure_annotation_download_space(
                "funcvep_download_preparation",
                {("plugins", "FuncVEP", "file"): archive_path},
            )
            command = [
                "bash",
                str(self.pipeline_root / "scripts" / "download_funcvep.sh"),
                str(config_path),
                "--acknowledge-license",
            ]
        return self._start_resource_job("funcvep", command, "preparation")

    def _download_omim_file(
        self,
        url: str,
        filename: str,
        destination: Path,
        progress: Callable[[float], None],
        on_rate_limit: Callable[[float, int, int], None] | None = None,
    ) -> None:
        """Download one licensed OMIM file without exposing its URL."""
        validate_omim_download_url(url, filename)
        opener = urllib.request.build_opener(_OmimRedirectHandler(filename))
        rate_limit_attempts = 0
        while True:
            request = urllib.request.Request(
                url,
                headers={
                    "User-Agent": f"GUIDE-IEI/{SERVICE_VERSION}",
                    "Accept": "text/plain, application/octet-stream;q=0.9, */*;q=0.1",
                },
            )
            try:
                with opener.open(request, timeout=30) as response:
                    final_url = response.geturl() if hasattr(response, "geturl") else url
                    validate_omim_download_url(final_url, filename)
                    raw_length = response.headers.get("Content-Length") if response.headers else None
                    try:
                        expected = int(raw_length) if raw_length else 0
                    except (TypeError, ValueError):
                        expected = 0
                    if expected > _OMIM_MAX_FILE_BYTES:
                        raise ValueError(f"{filename} is unexpectedly larger than 1 GiB")
                    descriptor = os.open(
                        destination,
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                        0o600,
                    )
                    written = 0
                    last_bucket = -1
                    with os.fdopen(descriptor, "wb") as handle:
                        while True:
                            if self._stop.is_set():
                                raise RuntimeError("the local service stopped during OMIM installation")
                            chunk = response.read(1024 * 1024)
                            if not chunk:
                                break
                            handle.write(chunk)
                            written += len(chunk)
                            if written > _OMIM_MAX_FILE_BYTES:
                                raise ValueError(f"{filename} is unexpectedly larger than 1 GiB")
                            if expected:
                                bucket = min(20, int((written / expected) * 20))
                                if bucket != last_bucket:
                                    last_bucket = bucket
                                    progress(min(1.0, written / expected))
                    if written == 0:
                        raise ValueError(f"{filename} downloaded as an empty file")
                    if expected and written != expected:
                        raise ValueError(f"{filename} download ended before all bytes arrived")
                    progress(1.0)
                    return
            except urllib.error.HTTPError as exc:
                if (
                    exc.code == HTTPStatus.TOO_MANY_REQUESTS
                    and rate_limit_attempts < len(_OMIM_RATE_LIMIT_RETRY_DELAYS)
                ):
                    destination.unlink(missing_ok=True)
                    fallback = _OMIM_RATE_LIMIT_RETRY_DELAYS[rate_limit_attempts]
                    delay = self._omim_retry_after(exc, fallback)
                    rate_limit_attempts += 1
                    if on_rate_limit:
                        on_rate_limit(
                            delay,
                            rate_limit_attempts,
                            len(_OMIM_RATE_LIMIT_RETRY_DELAYS),
                        )
                    self._wait_for_omim_download(delay)
                    continue
                if exc.code == HTTPStatus.TOO_MANY_REQUESTS:
                    raise ValueError(
                        f"{filename} was rate-limited by OMIM (HTTP 429) after "
                        f"{rate_limit_attempts} automatic retries; wait a few minutes "
                        "and try the same link block again"
                    ) from None
                raise ValueError(
                    f"{filename} could not be downloaded (OMIM returned HTTP {exc.code}); "
                    "the account link may be expired or unauthorized"
                ) from None
            except urllib.error.URLError:
                raise ValueError(
                    f"{filename} could not connect to OMIM; check the internet connection and retry"
                ) from None
            except TimeoutError:
                raise ValueError(
                    f"{filename} timed out while connecting to OMIM; retry the installation"
                ) from None
            except (ValueError, RuntimeError):
                raise
            except Exception:
                raise ValueError(
                    f"{filename} download failed unexpectedly; retry with links from the OMIM data-account email"
                ) from None

    @staticmethod
    def _omim_retry_after(error: urllib.error.HTTPError, fallback: float) -> float:
        """Return a bounded Retry-After delay without including a private URL."""
        raw_value = error.headers.get("Retry-After") if error.headers else None
        if raw_value:
            try:
                seconds = float(raw_value.strip())
            except (TypeError, ValueError):
                try:
                    retry_at = parsedate_to_datetime(raw_value)
                    if retry_at.tzinfo is None:
                        retry_at = retry_at.replace(tzinfo=timezone.utc)
                    seconds = (retry_at - datetime.now(timezone.utc)).total_seconds()
                except (TypeError, ValueError, OverflowError):
                    seconds = fallback
            if seconds >= 0:
                return max(1.0, min(_OMIM_MAX_RETRY_AFTER, seconds))
        return fallback

    def _wait_for_omim_download(self, seconds: float) -> None:
        """Wait interruptibly so service shutdown does not hang on backoff."""
        if self._stop.wait(seconds):
            raise RuntimeError("the local service stopped during OMIM installation")

    def start_omim_download(self, payload: dict) -> dict:
        """Download and install a user's four licensed OMIM files.

        The pasted block and extracted URLs are captured only by the in-memory
        worker closure. They are never placed in argv, a job record, a config,
        a log, or the generated OMIM database.
        """
        self._ensure_active_storage_available()
        raw_block = payload.get("link_block") if isinstance(payload, dict) else None
        urls = extract_omim_download_urls(raw_block)

        def install(report: Callable[[str, float | None], None]) -> dict:
            private_root = self.state_dir / "gene-knowledge"
            private_root.mkdir(parents=True, exist_ok=True)
            if os.name == "posix":
                os.chmod(private_root, 0o700)
            report("Preparing private OMIM download space…", 0.0)
            with tempfile.TemporaryDirectory(
                prefix=".omim-staging-", dir=private_root
            ) as staging_value:
                staging = Path(staging_value)
                if os.name == "posix":
                    os.chmod(staging, 0o700)
                download_weight = 60.0 / len(OMIM_FILES)
                for index, filename in enumerate(OMIM_FILES):
                    base = index * download_weight
                    if index:
                        report(
                            f"Waiting briefly before downloading {filename} to respect OMIM request limits…",
                            base,
                        )
                        self._wait_for_omim_download(_OMIM_BETWEEN_FILES_DELAY)
                    report(f"Downloading {filename}…", base)

                    def file_progress(fraction: float, *, name=filename, start=base):
                        report(
                            f"Downloading {name} — {fraction * 100:.0f}%",
                            start + fraction * download_weight,
                        )

                    def rate_limit_progress(
                        delay: float,
                        attempt: int,
                        attempts: int,
                        *,
                        name=filename,
                        start=base,
                    ):
                        report(
                            f"OMIM rate-limited {name}; retrying in {delay:.0f} seconds "
                            f"({attempt} of {attempts})…",
                            start,
                        )

                    self._download_omim_file(
                        urls[filename],
                        filename,
                        staging / filename,
                        file_progress,
                        rate_limit_progress,
                    )
                report("Validating the four OMIM file schemas…", 65.0)
                result = build_omim_database(
                    staging, self.gene_knowledge.private_database
                )
                report("Checking the private OMIM index…", 95.0)
            return {"counts": result["counts"]}

        return self._start_internal_resource_job(
            "omim",
            "installation",
            install,
            files=list(OMIM_FILES),
        )

    # ------------------------------------------------------------------
    # Persistent registry of live resource-job process groups. A service that
    # dies without running shutdown() (SIGKILL, crash, closed terminal) must
    # not leave invisible orphan downloaders competing with the next
    # instance's jobs for the same output files.
    # ------------------------------------------------------------------
    @staticmethod
    def _harden_root(root: Path) -> None:
        """Best-effort privacy for a PHI-bearing root and its databases."""
        if os.name != "posix":
            return
        for directory in (root, root / "sample-library", root / "uploads"):
            try:
                if directory.is_dir():
                    os.chmod(directory, 0o700)
            except OSError:
                pass
        for name in _PRIVATE_DATABASES:
            target = root / name
            try:
                if target.is_file():
                    os.chmod(target, 0o600)
            except OSError:
                pass

    def _harden_private_permissions(self) -> None:
        """Keep patient-bearing state private to the owning user.

        Directory and database modes otherwise inherit the ambient umask
        (0755/0644 under the common 022), leaving PHI world-readable on a
        shared machine. Mode 0700 on the state root denies traversal to
        every other user regardless of inner file modes; the databases are
        tightened directly as well. Best effort — permission models vary
        (network shares, Windows), so failures are ignored.
        """
        if os.name != "posix":
            return
        for directory in (
            self.state_dir,
            self.resource_secrets_dir,
            self.state_dir / "sample-library",
            # The workspace root holds upload staging — the first place a
            # patient VCF lands — and may be a separately configured
            # location outside the state root.
            self.workspace_dir,
            self.workspace_dir / "uploads",
        ):
            try:
                if directory.is_dir():
                    os.chmod(directory, 0o700)
            except OSError:
                pass
        # The actual databases: the job store is workbench.sqlite3 and the
        # sample library shares cohort.sqlite3 — the earlier list named two
        # files that never exist, silently leaving workbench.sqlite3 at the
        # umask default.
        for name in _PRIVATE_DATABASES:
            target = self.state_dir / name
            try:
                if target.is_file():
                    os.chmod(target, 0o600)
            except OSError:
                pass

    def _active_resource_pids_path(self) -> Path:
        return self.state_dir / "resource-job-pids.json"

    def _remove_stale_resource_secrets(self) -> None:
        try:
            paths = list(self.resource_secrets_dir.iterdir())
        except OSError:
            return
        for path in paths:
            try:
                if path.is_file() or path.is_symlink():
                    path.unlink()
            except OSError:
                pass

    def _rewrite_active_resource_pids(self, mutate) -> None:
        # The write is atomic but the read-modify-write was not: two jobs
        # registering concurrently could lose one PID from the crash-cleanup
        # registry, leaving an invisible orphan process.
        with self._resource_pids_lock:
            path = self._active_resource_pids_path()
            try:
                data = json.loads(path.read_text()) if path.exists() else {}
            except (OSError, ValueError):
                data = {}
            if not isinstance(data, dict):
                data = {}
            mutate(data)
            temporary = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
            temporary.write_text(json.dumps(data))
            temporary.replace(path)

    def _record_active_resource_pid(self, job_id: str, pid: int) -> None:
        self._rewrite_active_resource_pids(lambda data: data.__setitem__(job_id, pid))

    def _clear_active_resource_pid(self, job_id: str) -> None:
        self._rewrite_active_resource_pids(lambda data: data.pop(job_id, None))

    def _acquire_instance_lock(self):
        """Hold an exclusive advisory lock on <state_dir>/service.lock.

        The lock lives for the life of this process (the handle is kept on
        the service and released by shutdown()); the kernel drops it if the
        process dies, so a crash never leaves the state directory locked.
        Filesystems without flock support fall back to no lock with a
        warning rather than refusing to start.
        """
        lock_path = self.state_dir / "service.lock"
        try:
            handle = lock_path.open("a+")
        except OSError as exc:
            print(f"WARN  cannot open {lock_path}: {exc}; single-instance guard disabled")
            return None
        try:
            import fcntl
        except ImportError:  # non-posix: no advisory locking available
            return handle
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.close()
            raise ServiceAlreadyRunningError(
                f"another GUIDE-IEI service already owns {self.state_dir} "
                "(it holds service.lock); this instance will not touch that state. "
                "Use the running workbench, or stop it before starting another."
            ) from None
        except OSError as exc:
            print(f"WARN  advisory locking unavailable on {lock_path} ({exc}); single-instance guard disabled")
            return handle
        try:
            handle.seek(0)
            handle.truncate()
            handle.write(f"{os.getpid()}\n")
            handle.flush()
        except OSError:
            pass
        return handle

    def _release_instance_lock(self) -> None:
        handle = getattr(self, "_instance_lock_handle", None)
        if handle is None:
            return
        self._instance_lock_handle = None
        try:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except (ImportError, OSError):
            pass
        try:
            handle.close()
        except OSError:
            pass

    def _terminate_orphaned_annotation_jobs(self) -> None:
        """Stop pipeline runs left behind by a previous service process.

        Annotation subprocesses start in their own session, so the death of
        the service (SIGTERM from the launcher, a closed terminal) never
        stopped them. The job row was marked interrupted, but VEP kept
        writing the output for hours, and a resubmitted job raced it for
        the same output path (audit H6). Same PID-reuse guard as the
        resource-job reclaim: only a group whose leader still runs one of
        this pipeline's own scripts is signalled.
        """
        orphans = list(getattr(self.store, "orphaned_running_jobs", []))
        if os.name != "posix" or not orphans:
            return
        for job_id, pid in orphans:
            try:
                probe = subprocess.run(
                    ["ps", "-o", "command=", "-p", str(pid)],
                    capture_output=True, text=True, check=False,
                )
            except OSError:
                continue
            if str(self.pipeline_root) not in probe.stdout:
                continue
            try:
                os.killpg(pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                continue
            print(
                f"stopped orphaned annotation job {job_id} (pgid {pid}) left by a "
                "previous workbench session; resubmit the job to run it again"
            )
            try:
                self.store.update(
                    job_id,
                    error="The local service stopped while this job was running; "
                          "its pipeline process was stopped at the next start.",
                    pid=None,
                )
            except Exception:  # noqa: BLE001 - bookkeeping only
                pass

    def _terminate_orphaned_resource_jobs(self) -> None:
        path = self._active_resource_pids_path()
        if os.name != "posix" or not path.is_file():
            return
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            data = {}
        for job_id, pid in (data.items() if isinstance(data, dict) else []):
            if not isinstance(pid, int) or pid <= 0:
                continue
            # Guard against PID reuse: only signal a group whose leader is
            # still one of this pipeline's own scripts.
            try:
                probe = subprocess.run(
                    ["ps", "-o", "command=", "-p", str(pid)],
                    capture_output=True, text=True, check=False,
                )
            except OSError:
                continue
            if str(self.pipeline_root) not in probe.stdout:
                continue
            try:
                os.killpg(pid, signal.SIGTERM)
                print(
                    f"stopped orphaned dataset job {job_id} (pgid {pid}) left by a "
                    "previous workbench session; restart the download to resume it"
                )
            except (ProcessLookupError, PermissionError):
                pass
        path.unlink(missing_ok=True)

    def _start_resource_job(
        self,
        resource_id: str,
        command: list[str],
        operation: str,
        sensitive_paths: tuple[Path, ...] = (),
    ) -> dict:
        with self._resource_lock:
            for job in self._resource_jobs.values():
                if (
                    job["resource_id"] == resource_id
                    and job["status"] in {"queued", "running"}
                ):
                    for path in sensitive_paths:
                        path.unlink(missing_ok=True)
                    return self._resource_job_copy(job)
            job_id = uuid.uuid4().hex
            job = {
                "id": job_id,
                "resource_id": resource_id,
                "status": "queued",
                "progress": None,
                "message": "Waiting to start…",
                "created_at": utc_now(),
                "started_at": None,
                "finished_at": None,
                "exit_code": None,
                "error": "",
                "log_path": str(self.resource_logs_dir / f"{job_id}.log"),
                "operation": operation,
                "_command": command,
                "_sensitive_paths": [str(path) for path in sensitive_paths],
            }
            self._resource_jobs[job_id] = job
            thread = threading.Thread(
                target=self._run_resource_download,
                args=(job_id,),
                name=f"resource-{resource_id}",
                daemon=True,
            )
            self._resource_threads[job_id] = thread
            thread.start()
            return self._resource_job_copy(job)

    def _start_internal_resource_job(
        self,
        resource_id: str,
        operation: str,
        runner: Callable[[Callable[[str, float | None], None]], dict],
        **public_values,
    ) -> dict:
        """Start a memory-only resource worker with no credential-bearing argv."""
        with self._resource_lock:
            for job in self._resource_jobs.values():
                if (
                    job["resource_id"] == resource_id
                    and job["status"] in {"queued", "running"}
                ):
                    return self._resource_job_copy(job)
            job_id = uuid.uuid4().hex
            job = {
                "id": job_id,
                "resource_id": resource_id,
                "status": "queued",
                "progress": None,
                "message": "Waiting to start…",
                "created_at": utc_now(),
                "started_at": None,
                "finished_at": None,
                "exit_code": None,
                "error": "",
                "log_path": str(self.resource_logs_dir / f"{job_id}.log"),
                "operation": operation,
                "_runner": runner,
                **public_values,
            }
            self._resource_jobs[job_id] = job
            thread = threading.Thread(
                target=self._run_internal_resource_job,
                args=(job_id,),
                name=f"resource-{resource_id}",
                daemon=True,
            )
            self._resource_threads[job_id] = thread
            thread.start()
            return self._resource_job_copy(job)

    @staticmethod
    def _resource_job_copy(job: dict) -> dict:
        result = dict(job)
        result.pop("_command", None)
        result.pop("_runner", None)
        result.pop("_sensitive_paths", None)
        result.pop("_cancel_requested", None)
        path = Path(result["log_path"])
        if path.exists():
            with path.open("rb") as handle:
                handle.seek(0, os.SEEK_END)
                size = handle.tell()
                handle.seek(max(0, size - 16_000))
                result["log"] = handle.read().decode("utf-8", errors="replace")
        else:
            result["log"] = ""
        return result

    def _update_resource_job(self, job_id: str, **values) -> None:
        with self._resource_lock:
            job = self._resource_jobs.get(job_id)
            if job:
                job.update(values)

    def _run_internal_resource_job(self, job_id: str) -> None:
        with self._resource_lock:
            job = self._resource_jobs.get(job_id)
            if not job:
                return
            runner = job.get("_runner")
            operation = str(job.get("operation") or "installation")
        if not callable(runner):
            self._update_resource_job(
                job_id,
                status="failed",
                finished_at=utc_now(),
                exit_code=1,
                error="internal resource worker is unavailable",
            )
            return
        self._update_resource_job(
            job_id,
            status="running",
            started_at=utc_now(),
            message=f"Starting {operation}…",
        )
        log_path = self.resource_logs_dir / f"{job_id}.log"
        last_message = ""
        try:
            with log_path.open("a", encoding="utf-8", buffering=1) as log:
                log.write("Local credential-safe resource worker started.\n")

                def report(message: str, progress: float | None = None) -> None:
                    nonlocal last_message
                    last_message = message
                    log.write(message + "\n")
                    self._update_resource_job(
                        job_id,
                        message=message,
                        progress=(
                            max(0.0, min(100.0, float(progress)))
                            if progress is not None else None
                        ),
                    )

                result = runner(report)
                log.write(f"{operation.capitalize()} complete.\n")
            self._update_resource_job(
                job_id,
                status="succeeded",
                progress=100.0,
                message=f"{operation.capitalize()} complete.",
                finished_at=utc_now(),
                exit_code=0,
                error="",
                result=result,
            )
        except Exception as exc:
            safe_error = str(exc) or f"{operation.capitalize()} failed"
            try:
                with log_path.open("a", encoding="utf-8", buffering=1) as log:
                    log.write("ERROR: " + safe_error + "\n")
            except OSError:
                pass
            self._update_resource_job(
                job_id,
                status="failed",
                message=last_message or f"{operation.capitalize()} failed.",
                finished_at=utc_now(),
                exit_code=1,
                error=safe_error,
            )
        finally:
            # Release the closure holding the private links as soon as the
            # worker completes. The public job copy never exposed it.
            with self._resource_lock:
                current = self._resource_jobs.get(job_id)
                if current:
                    current.pop("_runner", None)

    _RESOURCE_STAGE_LINE = re.compile(r"===\s*(.+?)\s*===\s*$")
    _RESOURCE_PERCENT = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*%")
    _RESOURCE_TIMESTAMP = re.compile(r"^\[\d{2}:\d{2}:\d{2}\]\s*")
    _RESOURCE_METER_NOISE = re.compile(r"^[\s\d.#kKMGTiB%/:s-]+$")
    _RESOURCE_VOLUME_SPEED = re.compile(r"([\d.]+)\s*GiB\s+([\d.]+)\s*MiB/s")

    @classmethod
    def _resource_progress_update(cls, stage: str, line: str) -> tuple[str, dict | None]:
        """Turn one raw downloader log line into (stage, UI update or None).

        Raw curl/parallel_fetch transfer meters are unreadable as a job
        message; show "<dataset> — NN%" (plus volume and speed when present),
        pass ordinary log lines through without their timestamp, and drop
        meter noise that carries no percentage.
        """
        stripped = line.strip()
        if not stripped:
            return stage, None
        stage_match = cls._RESOURCE_STAGE_LINE.search(stripped)
        if stage_match:
            stage = stage_match.group(1)
            return stage, {"message": f"{stage}…", "progress": None}
        percent_match = cls._RESOURCE_PERCENT.search(stripped)
        if percent_match:
            percent = min(100.0, float(percent_match.group(1)))
            message = f"{stage} — {percent:.0f}%" if stage else f"{percent:.0f}%"
            detail = cls._RESOURCE_VOLUME_SPEED.search(stripped)
            if detail:
                message += f" · {detail.group(1)} GiB · {detail.group(2)} MiB/s"
            return stage, {"message": message, "progress": percent}
        if cls._RESOURCE_METER_NOISE.match(stripped):
            return stage, None
        return stage, {"message": cls._RESOURCE_TIMESTAMP.sub("", stripped)}

    def _run_resource_download(self, job_id: str) -> None:
        try:
            self._run_resource_download_impl(job_id)
        finally:
            if (self._resource_jobs.get(job_id) or {}).get("resource_id") == "annotation_engine":
                self._finish_engine_setup()

    def _run_resource_download_impl(self, job_id: str) -> None:
        with self._resource_lock:
            job = self._resource_jobs.get(job_id)
            if not job:
                return
            resource_id = job["resource_id"]
            command = list(job["_command"])
            operation = job.get("operation", "download")
        self._update_resource_job(
            job_id,
            status="running",
            started_at=utc_now(),
            message=f"Starting {operation}…",
        )
        log_path = self.resource_logs_dir / f"{job_id}.log"
        exit_code = 1
        last_line = ""
        process: subprocess.Popen | None = None
        try:
            with log_path.open("a", encoding="utf-8", buffering=1) as log:
                log.write("$ " + " ".join(json.dumps(item) for item in command) + "\n")
                with self._resource_lock:
                    if job.get("_cancel_requested"):
                        raise RuntimeError("Annotation-engine preparation stopped by the user.")
                    process = subprocess.Popen(
                        command, cwd=self.pipeline_root, stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT, text=True, bufsize=1,
                        start_new_session=(os.name == "posix"),
                    )
                    self._resource_processes[job_id] = process
                if os.name == "posix":
                    self._record_active_resource_pid(job_id, process.pid)
                assert process.stdout is not None
                stage_label = ""
                with process.stdout:
                    for line in process.stdout:
                        log.write(line)
                        stage_label, updates = self._resource_progress_update(
                            stage_label, line
                        )
                        if resource_id == "annotation_engine" and not self._RESOURCE_STAGE_LINE.search(line):
                            # Structured installer guidance must reach the
                            # native startup window, not disappear into Log.
                            prefix = "GUIDE_IEI_SETUP_ERROR: "
                            updates = ({"message": line.strip()[len(prefix):], "progress": None}
                                       if line.startswith(prefix) else None)
                        if updates:
                            if updates.get("message"):
                                last_line = updates["message"]
                            self._update_resource_job(job_id, **updates)
                exit_code = process.wait()
            if job.get("_cancel_requested"):
                raise RuntimeError("Annotation-engine preparation stopped by the user.")
            if exit_code:
                raise RuntimeError(last_line or f"download exited with code {exit_code}")
            if resource_id == "annotation_engine":
                # A profile may have been created after this service started
                # (e.g. first setup failed, then Retry recovered it). Carry the
                # same process-local connection into validation and later jobs.
                if platform.system() == "Darwin":
                    os.environ.update(managed_colima_environment(dict(os.environ)))
                config = self._load_config(self.pipeline_root / "config/annotation.config.yaml")
                status = self._container_image_status(config)
                if not status["available"]:
                    raise RuntimeError(status.get("message") or "Annotation-engine validation failed; see the setup log.")
            elif resource_id == "gene_knowledge":
                updated = self.annotation_root / "gene-knowledge" / "gene_knowledge_public.sqlite3"
                if not updated.is_file():
                    raise RuntimeError("gene-knowledge update did not create its database")
                self.gene_knowledge.public_database = updated
            elif resource_id == "clingen_erepo":
                status = self.clingen_erepo.status()
                if not status.get("available"):
                    raise RuntimeError(status.get("error") or "ClinGen snapshot validation failed")
            elif resource_id in {"screen_context", "screen_context_build", "recommended_wgs"}:
                # Register the freshly downloaded/prepared bundle; install
                # validates the manifest and matrices before storing the
                # pointer. The recommended-WGS bulk install includes the
                # bundle, so registration must happen there too (skipped
                # quietly if a registration already points at it).
                manifest = (
                    self.annotation_root / "screen-context" / "prepared"
                    / "screen.registry-v4.immune-contexts.json"
                )
                if resource_id != "recommended_wgs" or manifest.is_file():
                    self.install_screen_context({"manifest_path": str(manifest)})
            self._update_resource_job(
                job_id,
                status="succeeded",
                progress=100.0,
                message=f"{operation.capitalize()} complete.",
                finished_at=utc_now(),
                exit_code=0,
                error="",
            )
        except Exception as exc:
            self._update_resource_job(
                job_id,
                status="interrupted" if job.get("_cancel_requested") else "failed",
                message=last_line or f"{operation.capitalize()} failed.",
                finished_at=utc_now(),
                exit_code=exit_code,
                error=str(exc),
            )
        finally:
            # Audit M24: a failure inside the read loop (a log write error,
            # a progress-store error, undecodable output) left the child
            # running while its tracking was removed — invisible to crash
            # cleanup, and the next start of the same download launched a
            # second writer on the same .part file. Stop and reap the child
            # BEFORE its pid record goes, so no untracked writer survives.
            self._stop_resource_process(process, job_id)
            with self._resource_lock:
                self._resource_processes.pop(job_id, None)
                sensitive_paths = list(
                    (self._resource_jobs.get(job_id) or {}).get("_sensitive_paths", [])
                )
            for path in sensitive_paths:
                Path(path).unlink(missing_ok=True)
            self._clear_active_resource_pid(job_id)

    @staticmethod
    def _stop_resource_process(
        process: "subprocess.Popen | None", job_id: str, grace_seconds: float = 10.0
    ) -> None:
        """Terminate and reap a still-running download child (its whole
        process group on POSIX, since it was started in a new session)."""
        if process is None or process.poll() is not None:
            return
        def _signal(sig) -> None:
            try:
                if os.name == "posix":
                    os.killpg(process.pid, sig)
                else:
                    process.terminate() if sig == signal.SIGTERM else process.kill()
            except (ProcessLookupError, PermissionError, OSError):
                pass
        _signal(signal.SIGTERM)
        try:
            process.wait(timeout=grace_seconds)
        except subprocess.TimeoutExpired:
            _signal(signal.SIGKILL)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                print(
                    f"WARN resource job {job_id}: child pid {process.pid} did not "
                    "exit after SIGKILL",
                    file=sys.stderr,
                )
        if process.stdout is not None:
            try:
                process.stdout.close()
            except OSError:
                pass

    def review_file(self, job_id: str) -> Path:
        """Return only an output path already recorded for a local annotation job."""
        job = self.store.get(job_id)
        if not job:
            raise NotFoundError("job not found")
        candidate = Path(
            job.get("final_output_path") or job.get("output_path") or ""
        ).expanduser().resolve()
        recorded = {
            Path(value).expanduser().resolve()
            for value in (job.get("final_output_path"), job.get("output_path"))
            if value
        }
        if candidate not in recorded:
            raise ValueError("job output path is invalid")
        if not candidate.is_file():
            raise FileNotFoundError("annotated VCF output is no longer available")
        if not re.search(r"\.vcf(?:\.gz)?$", candidate.name, re.IGNORECASE):
            raise ValueError("job output is not a VCF")
        return candidate

    def prefilter_wgs_review(
        self, payload: dict, progress: Callable[[dict], None] | None = None
    ) -> dict:
        """Create a cached, indexed WGS review VCF using chromosome readers."""
        self._ensure_active_storage_available(require_workspace=True)
        source = self._required_path(payload, "path", must_exist=True)
        if not re.search(r"\.vcf(?:\.gz)?$", source.name, re.IGNORECASE):
            raise ValueError("path must end in .vcf or .vcf.gz")
        filters = payload.get("filters") or {}
        if not isinstance(filters, dict):
            raise ValueError("filters must be an object")
        options = WgsPrefilterOptions.from_payload(filters)
        config_value = payload.get("config_path") or (
            self.pipeline_root / "config" / "annotation.config.yaml"
        )
        config_path = Path(config_value).expanduser().resolve()
        config = self._load_config(config_path)
        region = config.get("region") or {}
        exome_bed_value = region.get("custom_bed") or region.get("bed")
        exome_bed_path = self._resolved_reference_path(exome_bed_value)
        if exome_bed_path is None:
            raise ValueError(
                "the annotation config names no GRCh38 coding+splice BED "
                "(region.bed / region.custom_bed)"
            )
        if not exome_bed_path.is_file():
            # First use on this workstation: run the same one-time build an
            # exome-mode annotation performs, so every entry path — exome or
            # whole-genome, annotate or review — self-initializes instead of
            # telling the user to run something else first. Both callers are
            # background workers, so the ~2-minute build is fine here.
            if progress:
                progress({
                    "phase": "preparing_index",
                    "message": "Building the GRCh38 coding+splice region set (one-time)…",
                })
            build = subprocess.run(
                [
                    "bash",
                    str(self.pipeline_root / "scripts" / "build_coding_bed.sh"),
                    str(config_path),
                ],
                cwd=self.pipeline_root, capture_output=True, text=True,
            )
            if build.returncode != 0 or not exome_bed_path.is_file():
                detail = (build.stderr or build.stdout or "").strip().splitlines()
                raise ValueError(
                    "the GRCh38 coding+splice BED could not be built: "
                    + (detail[-1] if detail else f"exit code {build.returncode}")
                )
        ccre = ((config.get("wgs_review") or {}).get("ccre") or {})
        ccre_bed_path = self._resolved_reference_path(ccre.get("bed"))
        promoterai = ((config.get("plugins") or {}).get("PromoterAI") or {})
        promoter_map_path = self._resolved_reference_path(
            promoterai.get("transcript_map")
        )
        result = self.wgs_review.prefilter(
            source,
            options,
            exome_bed_path,
            ccre_bed_path,
            promoter_map_path,
            progress,
        )
        review_id = uuid.uuid4().hex
        output_path = Path(result["path"]).resolve()
        with self._wgs_review_lock:
            self._wgs_review_files[review_id] = output_path
            for stale_id in list(self._wgs_review_files)[:-30]:
                # Delete the backing file with its registry entry:
                # otherwise outputs accumulate forever while their
                # download links 404 for files still on disk. Prefilter
                # caching hands the SAME output file to repeated opens of a
                # genome, so the file is removed only when no remaining
                # registry entry still references it.
                stale_path = self._wgs_review_files.pop(stale_id, None)
                if (
                    stale_path is not None
                    and stale_path not in self._wgs_review_files.values()
                ):
                    try:
                        stale_path.unlink(missing_ok=True)
                    except OSError:
                        pass
        return {
            **{key: value for key, value in result.items() if key != "path"},
            "id": review_id,
            "filename": output_path.name,
        }

    # ------------------------------------------------------------------
    # Bulk intake: a durable, resumable queue for importing many annotated
    # VCFs (e.g. hundreds of genomes) into the Sample Library and Cohort
    # search. The item list is recorded up front in SQLite; a single worker
    # processes items sequentially and survives service restarts — prefilter
    # caching and library de-duplication make re-running an item idempotent,
    # so resume is simply "reset running items to queued and continue."
    # ------------------------------------------------------------------

    def _bulk_intake_connect(self) -> sqlite3.Connection:
        # Transient OperationalErrors (SQLITE_IOERR under file-descriptor
        # pressure or journal recovery on CI runners) killed the worker
        # thread outright and, worse, could surface an empty table to the
        # already-running guard right after a committed INSERT. A short
        # retry heals the transient class; persistent failures still raise.
        last_error: sqlite3.OperationalError | None = None
        for attempt in range(5):
            try:
                connection = sqlite3.connect(
                    self.state_dir / "bulk-intake.sqlite3", timeout=60
                )
                break
            except sqlite3.OperationalError as error:
                last_error = error
                if self._stop.is_set():
                    raise
                time.sleep(0.2 * (attempt + 1))
        else:
            raise last_error  # type: ignore[misc]
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=60000")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS bulk_jobs (
              id TEXT PRIMARY KEY,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              status TEXT NOT NULL,
              options TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS bulk_items (
              job_id TEXT NOT NULL REFERENCES bulk_jobs(id) ON DELETE CASCADE,
              position INTEGER NOT NULL,
              path TEXT NOT NULL,
              status TEXT NOT NULL,
              error TEXT NOT NULL DEFAULT '',
              datasets INTEGER NOT NULL DEFAULT 0,
              updated_at TEXT NOT NULL,
              PRIMARY KEY (job_id, position)
            );
            CREATE INDEX IF NOT EXISTS bulk_items_status_idx
              ON bulk_items(job_id, status, position);
            """
        )
        return connection

    @staticmethod
    def _expand_bulk_paths(paths: list, recursive: bool) -> list[str]:
        expanded: list[str] = []
        seen: set[str] = set()
        for raw in paths:
            text = str(raw or "").strip()
            if not text:
                continue
            candidate = Path(text).expanduser()
            entries: list[Path]
            if candidate.is_dir():
                pattern = "**/*" if recursive else "*"
                entries = sorted(
                    item for item in candidate.glob(pattern)
                    if item.is_file()
                    and re.search(r"\.vcf(?:\.gz)?$", item.name, re.IGNORECASE)
                )
            else:
                entries = [candidate]
            for item in entries:
                resolved = str(item.resolve())
                if resolved not in seen:
                    seen.add(resolved)
                    expanded.append(resolved)
        return expanded

    def start_bulk_intake(self, payload: dict) -> dict:
        self._ensure_active_storage_available(require_workspace=True)
        if self.storage_migration_active() or self._migration_reserved:
            raise ValueError(
                "a storage migration is in progress — start the bulk import "
                "after it completes"
            )
        scope = str(payload.get("analysis_scope") or "whole_genome")
        if scope not in {"exome", "whole_genome"}:
            raise ValueError("analysis_scope must be exome or whole_genome")
        raw_paths = payload.get("paths")
        if not isinstance(raw_paths, list):
            raise ValueError("paths must be a list of VCF files or directories")
        items = self._expand_bulk_paths(raw_paths, bool(payload.get("recursive", True)))
        if not items:
            raise ValueError("no .vcf/.vcf.gz files were found in the given paths")
        if len(items) > 5000:
            raise ValueError(f"{len(items)} files exceeds the 5000-file queue limit")
        filters = payload.get("filters")
        if not isinstance(filters, dict) or not filters:
            filters = (
                {"max_gnomad_popmax": 0.01, "min_spliceai": None,
                 "min_promoterai_abs": None, "noncoding_mode": "none"}
                if scope == "exome" else {}
            )
        WgsPrefilterOptions.from_payload(filters)  # validate now, loudly
        options = {
            "analysis_scope": scope,
            "filters": filters,
            "include_in_cohort": bool(payload.get("include_in_cohort", True)),
        }
        with self._bulk_intake_lock:
            with closing(self._bulk_intake_connect()) as connection, connection:
                active = connection.execute(
                    "SELECT id FROM bulk_jobs WHERE status IN ('queued','running')"
                ).fetchone()
                if active:
                    raise ValueError(
                        f"bulk intake {active['id']} is still running — wait for it "
                        "to finish or cancel it first"
                    )
                worker = self._bulk_intake_thread
                if worker and worker.is_alive():
                    # The database is the source of truth, but a live
                    # worker with no visible job row means the row is in
                    # flux (or was lost to an I/O hiccup) — never run two
                    # workers over one queue database.
                    raise ValueError(
                        "a bulk intake worker is still finishing — wait a "
                        "moment and try again"
                    )
                job_id = uuid.uuid4().hex
                now = utc_now()
                connection.execute(
                    "INSERT INTO bulk_jobs (id, created_at, updated_at, status, options)"
                    " VALUES (?,?,?,?,?)",
                    (job_id, now, now, "queued", json.dumps(options)),
                )
                connection.executemany(
                    "INSERT INTO bulk_items (job_id, position, path, status, updated_at)"
                    " VALUES (?,?,?,?,?)",
                    [(job_id, index, path, "queued", now) for index, path in enumerate(items)],
                )
            self._start_bulk_intake_worker(job_id)
        return self.bulk_intake_snapshot(job_id)

    def _start_bulk_intake_worker(self, job_id: str) -> None:
        if self._bulk_intake_thread and self._bulk_intake_thread.is_alive():
            return
        self._bulk_intake_thread = threading.Thread(
            target=self._run_bulk_intake, args=(job_id,),
            name=f"bulk-intake-{job_id[:8]}", daemon=True,
        )
        self._bulk_intake_thread.start()

    def _resume_bulk_intake(self) -> None:
        with closing(self._bulk_intake_connect()) as connection, connection:
            connection.execute(
                "UPDATE bulk_items SET status='queued', updated_at=?"
                " WHERE status='running'", (utc_now(),)
            )
            row = connection.execute(
                "SELECT id FROM bulk_jobs WHERE status IN ('queued','running')"
                " ORDER BY created_at LIMIT 1"
            ).fetchone()
        if row:
            self._start_bulk_intake_worker(row["id"])

    def _run_bulk_intake(self, job_id: str) -> None:
        try:
            self._run_bulk_intake_inner(job_id)
        except sqlite3.OperationalError as error:
            if self._stop.is_set():
                # Service shut down mid-item (the state dir may already be
                # gone — routine in tests and at exit): the interrupted
                # item is reset to queued and resumed at the next start,
                # so a raw thread-death traceback here is pure noise.
                return
            print(
                f"[local-service] bulk intake {job_id} stopped on a "
                f"database error: {error}",
                file=sys.stderr,
            )
            try:
                with closing(self._bulk_intake_connect()) as connection, connection:
                    connection.execute(
                        "UPDATE bulk_jobs SET status='failed', updated_at=?"
                        " WHERE id=? AND status IN ('queued','running')",
                        (utc_now(), job_id),
                    )
            except sqlite3.Error:
                pass

    def _run_bulk_intake_inner(self, job_id: str) -> None:
        with closing(self._bulk_intake_connect()) as connection, connection:
            row = connection.execute(
                "SELECT options FROM bulk_jobs WHERE id=?", (job_id,)
            ).fetchone()
            if not row:
                return
            options = json.loads(row["options"])
            connection.execute(
                "UPDATE bulk_jobs SET status='running', updated_at=? WHERE id=?",
                (utc_now(), job_id),
            )
        while not self._stop.is_set():
            if job_id in self._bulk_intake_cancelled:
                with closing(self._bulk_intake_connect()) as connection, connection:
                    connection.execute(
                        "UPDATE bulk_items SET status='skipped', updated_at=?"
                        " WHERE job_id=? AND status='queued'", (utc_now(), job_id)
                    )
                    connection.execute(
                        "UPDATE bulk_jobs SET status='cancelled', updated_at=? WHERE id=?",
                        (utc_now(), job_id),
                    )
                self._bulk_intake_cancelled.discard(job_id)
                return
            with closing(self._bulk_intake_connect()) as connection, connection:
                item = connection.execute(
                    "SELECT position, path FROM bulk_items"
                    " WHERE job_id=? AND status='queued' ORDER BY position LIMIT 1",
                    (job_id,),
                ).fetchone()
                if item:
                    connection.execute(
                        "UPDATE bulk_items SET status='running', updated_at=?"
                        " WHERE job_id=? AND position=?",
                        (utc_now(), job_id, item["position"]),
                    )
            if not item:
                with closing(self._bulk_intake_connect()) as connection, connection:
                    connection.execute(
                        "UPDATE bulk_jobs SET status='completed', updated_at=? WHERE id=?",
                        (utc_now(), job_id),
                    )
                return
            status, error, dataset_count = "succeeded", "", 0
            try:
                dataset_count = self._bulk_intake_item(item["path"], options)
            except Exception as exc:
                status, error = "failed", str(exc)[:400]
            with closing(self._bulk_intake_connect()) as connection, connection:
                connection.execute(
                    "UPDATE bulk_items SET status=?, error=?, datasets=?, updated_at=?"
                    " WHERE job_id=? AND position=?",
                    (status, error, dataset_count, utc_now(), job_id, item["position"]),
                )

    def _bulk_intake_item(self, path: str, options: dict) -> int:
        result = self.prefilter_wgs_review({
            "path": path, "filters": options.get("filters") or {},
        })
        prepared = self.wgs_review_file(result["id"])
        scope = options.get("analysis_scope") or "whole_genome"
        imported = self.sample_library.import_vcf(prepared, {
            "analysis_scope": scope,
            "index_scope": "compact",
            "include_in_cohort": bool(options.get("include_in_cohort", True)),
            "capture_kit": "",
            "qc_settings": {},
            "prefilter_settings": options.get("filters") or {},
            "retention_routes": (
                ["exome region", "popmax prefilter"] if scope == "exome"
                else ["coding/essential-splice", "SpliceAI", "promoterAI", "noncoding"]
            ),
            "source_record_count": result.get("records_scanned"),
            "retained_record_count": result.get("records_retained"),
            "original_path": path,
            "original_name": Path(path).name,
        })
        return len(imported.get("datasets") or [])

    def bulk_intake_snapshot(self, job_id: str | None = None) -> dict:
        with closing(self._bulk_intake_connect()) as connection:
            if job_id is None:
                row = connection.execute(
                    "SELECT * FROM bulk_jobs ORDER BY created_at DESC LIMIT 1"
                ).fetchone()
            else:
                row = connection.execute(
                    "SELECT * FROM bulk_jobs WHERE id=?", (job_id,)
                ).fetchone()
            if not row:
                return {"job": None}
            counts = {
                status: count for status, count in connection.execute(
                    "SELECT status, COUNT(*) FROM bulk_items WHERE job_id=?"
                    " GROUP BY status", (row["id"],)
                )
            }
            current = connection.execute(
                "SELECT path FROM bulk_items WHERE job_id=? AND status='running'"
                " ORDER BY position LIMIT 1", (row["id"],)
            ).fetchone()
            failures = [
                {"path": item["path"], "error": item["error"]}
                for item in connection.execute(
                    "SELECT path, error FROM bulk_items"
                    " WHERE job_id=? AND status='failed' ORDER BY position LIMIT 20",
                    (row["id"],),
                )
            ]
            datasets = connection.execute(
                "SELECT COALESCE(SUM(datasets),0) FROM bulk_items WHERE job_id=?",
                (row["id"],),
            ).fetchone()[0]
        total = sum(counts.values())
        return {"job": {
            "id": row["id"],
            "status": row["status"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "options": json.loads(row["options"]),
            "total": total,
            "queued": counts.get("queued", 0),
            "running": counts.get("running", 0),
            "succeeded": counts.get("succeeded", 0),
            "failed": counts.get("failed", 0),
            "skipped": counts.get("skipped", 0),
            "datasets": datasets,
            "current_path": current["path"] if current else None,
            "failures": failures,
        }}

    def cancel_bulk_intake(self, job_id: str) -> dict:
        self._bulk_intake_cancelled.add(str(job_id))
        with closing(self._bulk_intake_connect()) as connection, connection:
            row = connection.execute(
                "SELECT status FROM bulk_jobs WHERE id=?", (str(job_id),)
            ).fetchone()
            if not row:
                raise NotFoundError("bulk intake job not found")
            if row["status"] not in {"queued", "running"}:
                self._bulk_intake_cancelled.discard(str(job_id))
        return self.bulk_intake_snapshot(str(job_id))

    def spliceai_lookup(self, payload: dict) -> dict:
        """Fetch SpliceAI scores for one variant from the Broad's public API.

        This is the single deliberate exception to "nothing leaves this
        machine": the user explicitly clicks per variant, and only
        chrom/pos/ref/alt are transmitted — no genotype, sample, or
        phenotype data. Results are cached locally so a variant is never
        queried twice. The API is interactive-use-only (a few requests per
        minute); this endpoint is never called in a batch.
        """
        chrom = str(payload.get("chrom") or "").strip().removeprefix("chr")
        ref = str(payload.get("ref") or "").strip().upper()
        alt = str(payload.get("alt") or "").strip().upper()
        try:
            pos = int(payload.get("pos"))
        except (TypeError, ValueError) as exc:
            raise ValueError("pos must be a positive integer") from exc
        if not chrom or chrom not in {*map(str, range(1, 23)), "X", "Y", "MT", "M"}:
            raise ValueError(f"unsupported chromosome for SpliceAI lookup: {chrom!r}")
        if not re.fullmatch(r"[ACGT]+", ref) or not re.fullmatch(r"[ACGT]+", alt):
            raise ValueError("ref and alt must be plain ACGT sequences")
        distance = SPLICEAI_LOOKUP_DISTANCE
        mask = SPLICEAI_LOOKUP_MASK
        variant_key = f"{chrom}-{pos}-{ref}-{alt}"

        cache_path = self.state_dir / "spliceai-lookup-cache.sqlite3"
        with closing(sqlite3.connect(cache_path, timeout=30)) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS lookups ("
                "variant_key TEXT NOT NULL, distance INTEGER NOT NULL,"
                "mask INTEGER NOT NULL, retrieved_at TEXT NOT NULL,"
                "response TEXT NOT NULL,"
                "PRIMARY KEY (variant_key, distance, mask))"
            )
            row = connection.execute(
                "SELECT retrieved_at, response FROM lookups"
                " WHERE variant_key = ? AND distance = ? AND mask = ?",
                (variant_key, distance, mask),
            ).fetchone()
            if row is not None:
                return self._spliceai_lookup_result(
                    json.loads(row[1]), variant_key, row[0], cached=True
                )

        base_url = os.environ.get("IEI_SPLICEAI_LOOKUP_URL", SPLICEAI_LOOKUP_URL)
        query = urlencode({
            "hg": "38", "distance": distance, "mask": mask, "variant": variant_key,
        })
        request = urllib.request.Request(
            f"{base_url}?{query}",
            headers={"User-Agent": "GUIDE-IEI variant workbench (single-variant interactive lookup)"},
        )
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise ValueError(
                f"the Broad SpliceAI service rejected the request (HTTP {exc.code}); "
                "it may be rate-limited — wait a minute and try again"
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ValueError(
                "the Broad SpliceAI service could not be reached — check the "
                "internet connection and try again"
            ) from exc
        if isinstance(body, dict) and body.get("error"):
            raise ValueError(f"Broad SpliceAI service error: {body['error']}")
        if not isinstance(body, dict) or not isinstance(body.get("scores"), list):
            raise ValueError("unexpected response format from the Broad SpliceAI service")

        retrieved_at = utc_now()
        with closing(sqlite3.connect(cache_path, timeout=30)) as connection:
            connection.execute(
                "INSERT OR REPLACE INTO lookups"
                " (variant_key, distance, mask, retrieved_at, response)"
                " VALUES (?, ?, ?, ?, ?)",
                (variant_key, distance, mask, retrieved_at, json.dumps(body)),
            )
            connection.commit()
        return self._spliceai_lookup_result(body, variant_key, retrieved_at, cached=False)

    @staticmethod
    def _spliceai_lookup_result(
        body: dict, variant_key: str, retrieved_at: str, cached: bool
    ) -> dict:
        transcripts = []
        for entry in body.get("scores") or []:
            if not isinstance(entry, dict):
                continue
            transcripts.append({
                "gene": entry.get("g_name") or "",
                "transcript": entry.get("t_id") or "",
                "refseq": (entry.get("t_refseq_ids") or [None])[0],
                "mane_select": entry.get("t_priority") == "MS",
                "strand": entry.get("t_strand") or "",
                "scores": {
                    "acceptor_gain": {"delta": entry.get("DS_AG"), "position": entry.get("DP_AG")},
                    "acceptor_loss": {"delta": entry.get("DS_AL"), "position": entry.get("DP_AL")},
                    "donor_gain": {"delta": entry.get("DS_DG"), "position": entry.get("DP_DG")},
                    "donor_loss": {"delta": entry.get("DS_DL"), "position": entry.get("DP_DL")},
                },
            })
        # MANE Select first, then the rest in API order.
        transcripts.sort(key=lambda item: not item["mane_select"])
        return {
            "variant": variant_key,
            "distance": SPLICEAI_LOOKUP_DISTANCE,
            "masked": bool(SPLICEAI_LOOKUP_MASK),
            "retrieved_at": retrieved_at,
            "cached": cached,
            "source": "Broad SpliceAI Lookup API",
            "transcripts": transcripts,
        }

    def ccre_context(self, payload: dict) -> dict:
        """Return local SCREEN overlap and Ensembl TSS proximity context."""
        config_value = payload.get("config_path") or (
            self.pipeline_root / "config" / "annotation.config.yaml"
        )
        config_path = Path(config_value).expanduser().resolve()
        config = self._load_config(config_path)
        review = config.get("wgs_review") or {}
        ccre = review.get("ccre") or {}
        gene_tss = review.get("gene_tss") or {}
        try:
            pos = int(payload.get("pos"))
        except (TypeError, ValueError) as exc:
            raise ValueError("pos must be a positive integer") from exc
        try:
            window_bp = int(gene_tss.get("window_bp") or 500_000)
        except (TypeError, ValueError) as exc:
            raise ValueError("wgs_review.gene_tss.window_bp must be an integer") from exc
        if window_bp < 1 or window_bp > 2_000_000:
            raise ValueError("wgs_review.gene_tss.window_bp must be 1-2000000")
        release = str(
            gene_tss.get("ensembl_release")
            or (config.get("container") or {}).get("vep_image_tag")
            or ""
        )
        release_match = re.search(r"(\d+)", release)
        release_label = release_match.group(1) if release_match else release
        try:
            return self.ccre_context_store.query(
                chrom=str(payload.get("chrom") or ""),
                pos=pos,
                ref=str(payload.get("ref") or ""),
                alt=str(payload.get("alt") or ""),
                ccre_path=self._resolved_reference_path(ccre.get("bed")),
                gene_tss_path=self._resolved_reference_path(gene_tss.get("path")),
                resource_version=str(ccre.get("version") or "SCREEN cCRE"),
                gene_source=(
                    f"Ensembl release {release_label} gene-level TSS"
                    if release_label else "release-matched Ensembl gene-level TSS"
                ),
                window_bp=window_bp,
            )
        except OSError as exc:
            raise ValueError(f"cCRE context resource could not be read: {exc}") from exc

    def _screen_context_manifest(self, config: dict) -> Path | None:
        configured = (
            ((config.get("wgs_review") or {}).get("screen_context") or {})
            .get("manifest")
        )
        environment = os.environ.get("IEI_SCREEN_CONTEXT_MANIFEST", "")
        selected = environment or configured
        if selected:
            return self._resolved_reference_path(selected)
        if self.screen_context_pointer.is_file():
            try:
                pointer = json.loads(
                    self.screen_context_pointer.read_text(encoding="utf-8")
                )
                manifest = str(pointer.get("manifest_path") or "").strip()
                return Path(manifest).expanduser().resolve() if manifest else None
            except (OSError, json.JSONDecodeError, AttributeError):
                return None
        return None

    @staticmethod
    def _screen_context_manifest_candidate(value: str) -> Path:
        path = Path(value).expanduser().resolve()
        if path.is_dir():
            candidates = (
                path / "screen.registry-v4.immune-contexts.json",
                path / "prepared" / "screen.registry-v4.immune-contexts.json",
            )
            path = next((candidate for candidate in candidates if candidate.is_file()), candidates[0])
        return path

    def install_screen_context(self, payload: dict) -> dict:
        """Validate and remember an existing prepared SCREEN context bundle.

        The large matrices remain at their user-selected annotation location;
        only a small local pointer is stored with workbench state.
        """
        manifest_value = str(payload.get("manifest_path") or "").strip()
        if not manifest_value:
            raise ValueError("a prepared SCREEN manifest or folder path is required")
        manifest = self._screen_context_manifest_candidate(manifest_value)
        if not manifest.is_file():
            raise ValueError(
                "screen.registry-v4.immune-contexts.json was not found at the selected location"
            )
        catalog = self.screen_context_store.catalog(manifest)
        if not catalog.get("available"):
            raise ValueError(str(catalog.get("message") or "SCREEN context bundle is unavailable"))
        value = {
            "schema_version": 1,
            "manifest_path": str(manifest),
            "installed_at": datetime.now(timezone.utc).isoformat(),
        }
        temporary = self.screen_context_pointer.with_name(
            f".{self.screen_context_pointer.name}.{uuid.uuid4().hex}.partial"
        )
        try:
            temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
            temporary.replace(self.screen_context_pointer)
        finally:
            temporary.unlink(missing_ok=True)
        return {**catalog, "manifest_path": str(manifest)}

    def screen_context_catalog(self, payload: dict | None = None) -> dict:
        payload = payload or {}
        config_value = payload.get("config_path") or (
            self.pipeline_root / "config" / "annotation.config.yaml"
        )
        config = self._load_config(Path(config_value).expanduser().resolve())
        return self.screen_context_store.catalog(self._screen_context_manifest(config))

    def screen_context(self, payload: dict) -> dict:
        config_value = payload.get("config_path") or (
            self.pipeline_root / "config" / "annotation.config.yaml"
        )
        config = self._load_config(Path(config_value).expanduser().resolve())
        return self.screen_context_store.evidence(
            self._screen_context_manifest(config), payload
        )

    def filter_screen_context(self, payload: dict) -> dict:
        config_value = payload.get("config_path") or (
            self.pipeline_root / "config" / "annotation.config.yaml"
        )
        config = self._load_config(Path(config_value).expanduser().resolve())
        return self.screen_context_store.filter_variants(
            self._screen_context_manifest(config), payload
        )

    def remove_cohort_samples(self, sample_ids) -> dict:
        """Remove cohort entries AND clear the library linkage that named them.

        The Cohort Search manager deletes cohort_samples rows directly. Library
        datasets that pointed at those rows must not keep the dangling
        cohort_file_id: a later cohort file receiving that id would otherwise
        be resolved as the removed dataset (and removing the dataset would then
        delete the other file's rows).
        """
        identities = self.sample_library.cohort_entry_identities(
            sample_ids if isinstance(sample_ids, list) else []
        )
        result = self.cohort.remove_samples(sample_ids)
        detached = self.sample_library.detach_cohort_entries(identities)
        if isinstance(result, dict):
            result = {**result, "library_datasets_detached": detached}
        return result

    def start_cohort_import(self, payload: dict) -> dict:
        """Start a full or conservatively prefiltered cohort import."""
        self._ensure_active_storage_available(require_workspace=True)
        paths = payload.get("paths")
        if not isinstance(paths, list):
            raise ValueError("paths must be a list of VCF files or directories")
        profile = str(payload.get("import_profile") or "full")
        if profile not in {"full", "prefiltered"}:
            raise ValueError("import_profile must be 'full' or 'prefiltered'")
        analysis_scope = str(payload.get("analysis_scope") or (
            "whole_genome" if profile == "prefiltered" else "exome"
        ))
        if analysis_scope not in {"exome", "whole_genome"}:
            raise ValueError("analysis_scope must be 'exome' or 'whole_genome'")
        if profile == "prefiltered":
            analysis_scope = "whole_genome"
        filters = payload.get("filters") or {}
        if not isinstance(filters, dict):
            raise ValueError("filters must be an object")
        normalized_filters = (
            json.loads(json.dumps(asdict(WgsPrefilterOptions.from_payload(filters))))
            if profile == "prefiltered" else {}
        )

        prefilter = None
        if profile == "prefiltered":
            config_path = payload.get("config_path")

            def prefilter_source(
                source: Path, progress: Callable[[dict], None]
            ) -> tuple[Path, dict]:
                request = {
                    "path": str(source),
                    "filters": normalized_filters,
                }
                if config_path:
                    request["config_path"] = config_path
                result = self.prefilter_wgs_review(request, progress=progress)
                return self.wgs_review_file(result["id"]), result

            prefilter = prefilter_source

        return self.cohort.start_import_paths(
            paths,
            recursive=bool(payload.get("recursive", True)),
            force=bool(payload.get("force", False)),
            allow_unknown_assembly=bool(
                payload.get("allow_unknown_assembly", False)
            ),
            import_profile=profile,
            analysis_scope=analysis_scope,
            prefilter_options=normalized_filters,
            prefilter=prefilter,
        )

    def start_wgs_review(self, payload: dict) -> dict:
        """Run indexed WGS preparation in the background for progress polling."""
        self._ensure_active_storage_available(require_workspace=True)
        with self._wgs_review_lock:
            if any(
                job["status"] in {"queued", "running"}
                for job in self._wgs_review_jobs.values()
            ):
                raise ValueError("another whole-genome prefilter is already running")
            job_id = uuid.uuid4().hex
            job = {
                "id": job_id,
                "status": "queued",
                "phase": "queued",
                "progress": 0.0,
                "message": "Queued whole-genome indexing and prefiltering.",
                "created_at": utc_now(),
                "started_at": None,
                "finished_at": None,
                "records_scanned": 0,
                "records_retained": 0,
                "reader_count": 1,
                "result": None,
                "error": "",
            }
            self._wgs_review_jobs[job_id] = job
            terminal = [
                key for key, value in self._wgs_review_jobs.items()
                if value["status"] in {"succeeded", "failed"}
            ]
            for key in terminal[:-20]:
                self._wgs_review_jobs.pop(key, None)
                self._wgs_review_threads.pop(key, None)
            thread = threading.Thread(
                target=self._run_wgs_review,
                args=(job_id, dict(payload)),
                name=f"wgs-review-{job_id[:8]}",
                daemon=True,
            )
            self._wgs_review_threads[job_id] = thread
            thread.start()
            return dict(job)

    def get_wgs_review_job(self, job_id: str) -> dict | None:
        with self._wgs_review_lock:
            job = self._wgs_review_jobs.get(job_id)
            return dict(job) if job else None

    def _update_wgs_review_job(self, job_id: str, **changes) -> None:
        with self._wgs_review_lock:
            if job_id in self._wgs_review_jobs:
                self._wgs_review_jobs[job_id].update(changes)

    def _run_wgs_review(self, job_id: str, payload: dict) -> None:
        self._update_wgs_review_job(
            job_id,
            status="running",
            phase="preparing_index",
            started_at=utc_now(),
        )

        def report(update: dict) -> None:
            allowed = {
                key: update[key]
                for key in (
                    "phase", "progress", "message", "records_scanned",
                    "records_retained", "reader_count",
                )
                if key in update
            }
            self._update_wgs_review_job(job_id, **allowed)

        try:
            result = self.prefilter_wgs_review(payload, progress=report)
            self._update_wgs_review_job(
                job_id,
                status="succeeded",
                phase="complete",
                progress=100.0,
                message="WGS indexing and prefiltering complete.",
                finished_at=utc_now(),
                records_scanned=result["records_scanned"],
                records_retained=result["records_retained"],
                reader_count=result["reader_count"],
                result=result,
            )
        except Exception as error:
            self._update_wgs_review_job(
                job_id,
                status="failed",
                phase="failed",
                message="Whole-genome indexing or prefiltering failed.",
                finished_at=utc_now(),
                error=str(error),
            )

    def wgs_review_file(self, review_id: str) -> Path:
        with self._wgs_review_lock:
            candidate = self._wgs_review_files.get(review_id)
        if candidate is None:
            raise NotFoundError("WGS review file not found")
        candidate = candidate.resolve()
        cache_root = self.wgs_review.cache_dir.resolve()
        if cache_root not in candidate.parents:
            raise ValueError("WGS review output path is invalid")
        if not candidate.is_file():
            raise FileNotFoundError("prefiltered WGS review VCF is no longer available")
        return candidate

    @staticmethod
    def _hardware_profile() -> dict:
        logical_cpus = max(1, int(os.cpu_count() or 1))
        # VEP forks are CPU- and memory-intensive. Half the logical threads,
        # capped at eight, is a conservative workstation default that leaves
        # capacity for the operating system, browser, and container runtime.
        recommended = max(1, min(8, logical_cpus // 2))
        maximum = max(recommended, min(32, max(1, logical_cpus - 1)))
        return {
            "logical_cpus": logical_cpus,
            "recommended_vep_workers": recommended,
            "max_vep_workers": maximum,
        }

    def _installed_vep_releases(self, config: dict) -> tuple[int | None, int | None]:
        container_tag = str((config.get("container") or {}).get("vep_image_tag") or "")
        container_match = re.search(r"release[_-]?(\d+)", container_tag, re.I)
        container_release = int(container_match.group(1)) if container_match else None

        cache_root = self._resolved_reference_path(
            (config.get("reference") or {}).get("vep_cache_dir")
        )
        cache_releases: list[int] = []
        species = str((config.get("reference") or {}).get("species") or "homo_sapiens")
        species_dir = cache_root / species if cache_root else None
        if species_dir and species_dir.is_dir():
            for candidate in species_dir.iterdir():
                match = re.match(r"^(\d+)_GRCh38$", candidate.name)
                if candidate.is_dir() and match:
                    cache_releases.append(int(match.group(1)))
        # A newer cache may be present after a staged or failed update. Report
        # the cache paired with the active container, not merely the newest
        # directory on disk.
        active_cache_release = (
            container_release
            if container_release in cache_releases
            else max(cache_releases) if cache_releases else None
        )
        return active_cache_release, container_release

    def submit(self, payload: dict) -> dict:
        self._ensure_active_storage_available(require_workspace=True, require_annotation_root=True)
        with self._resource_lock:
            downloading = [
                job["resource_id"]
                for job in self._resource_jobs.values()
                if (
                    job["status"] in {"queued", "running"}
                    and job["resource_id"] != "omim"
                )
            ]
        if downloading:
            raise ValueError(
                "wait for annotation dataset download to finish: "
                + ", ".join(sorted(set(downloading)))
            )
        input_path = self._required_path(payload, "input_path", must_exist=True)
        output_path = self._required_path(payload, "output_path", must_exist=False)
        input_name = input_path.name.lower()
        if not (input_name.endswith(".vcf") or input_name.endswith(".vcf.gz")):
            raise ValueError("input_path must end in .vcf or .vcf.gz")
        if [suffix.lower() for suffix in output_path.suffixes[-2:]] != [".vcf", ".gz"]:
            raise ValueError("output_path must end in .vcf.gz")
        if input_path == output_path:
            raise ValueError("input_path and output_path must be different")
        output_path.parent.mkdir(parents=True, exist_ok=True)

        config_value = payload.get("config_path") or (
            self.pipeline_root / "config" / "annotation.config.yaml"
        )
        config_path = Path(config_value).expanduser().resolve()
        if not config_path.is_file():
            raise ValueError(f"config_path does not exist: {config_path}")

        profile = str(payload.get("profile") or ("wsl-local" if is_wsl() else "local"))
        if profile not in ALLOWED_PROFILES:
            raise ValueError(f"unsupported execution profile: {profile}")
        if profile == "wsl-local" and not is_wsl():
            raise ValueError("wsl-local is available only when the service runs inside WSL")
        input_assembly = str(payload.get("input_assembly") or "auto")
        if input_assembly not in {"GRCh38", "GRCh37", "auto"}:
            raise ValueError("input_assembly must be GRCh38, GRCh37, or auto")
        default_scope = (
            "exome" if bool(payload.get("coding_only", True)) else "whole_genome"
        )
        analysis_scope = str(payload.get("analysis_scope") or default_scope)
        if analysis_scope not in {"exome", "whole_genome"}:
            raise ValueError("analysis_scope must be exome or whole_genome")
        coding_only = analysis_scope == "exome"

        job_id = uuid.uuid4().hex
        annotation_options = payload.get("annotation_options") or {}
        if not isinstance(annotation_options, dict):
            raise ValueError("annotation_options must be an object")
        annotation_options = {**annotation_options, "analysis_scope": analysis_scope}
        config_path = self._write_job_config(
            job_id, config_path, annotation_options
        )
        now = utc_now()
        job = self.store.create(
            {
                "id": job_id,
                "created_at": now,
                "updated_at": now,
                "status": "queued",
                "profile": profile,
                "analysis_scope": analysis_scope,
                "input_assembly": input_assembly,
                "input_path": str(input_path),
                "output_path": str(output_path),
                "config_path": str(config_path),
                "coding_only": int(coding_only),
                "include_filtered": int(bool(payload.get("include_filtered", False))),
                "use_clinvar": int(bool(payload.get("use_clinvar", True))),
                "log_path": str(self.logs_dir / f"{job_id}.log"),
            }
        )
        self._queue.put(job_id)
        return job

    def stage_file(
        self, filename: str, relative_path: str, batch_id: str, length: int, stream
    ) -> dict:
        """Stream a browser-selected VCF into workstation-local job storage."""
        self._ensure_active_storage_available(require_workspace=True)
        if length < 1:
            raise ValueError("the selected file is empty")
        if length > 2_000_000_000_000:
            raise ValueError("the selected file is too large")
        safe_batch = "".join(
            character for character in batch_id if character.isalnum() or character in "-_"
        )[:80]
        if not safe_batch:
            raise ValueError("a valid upload batch is required")
        raw_parts = Path(relative_path or filename).parts
        safe_parts = [
            part for part in raw_parts
            if part not in {"", ".", "..", "/", "\\"}
        ]
        safe_parts = [
            "".join(
                character for character in part
                if character.isalnum() or character in " ._-()"
            ).strip()
            for part in safe_parts
        ]
        safe_parts = [part for part in safe_parts if part]
        safe_name = Path(filename).name
        if not safe_parts:
            safe_parts = [safe_name]
        elif safe_parts[-1] != safe_name:
            safe_parts[-1] = safe_name
        lower_name = safe_name.lower()
        if not (lower_name.endswith(".vcf") or lower_name.endswith(".vcf.gz")):
            raise ValueError("only .vcf and .vcf.gz files can be staged")

        batch_root = (self.workspace_dir / "uploads" / safe_batch).resolve()
        destination = (batch_root.joinpath(*safe_parts)).resolve()
        if batch_root not in destination.parents:
            raise ValueError("invalid relative file path")
        destination.parent.mkdir(parents=True, exist_ok=True)
        # Unique partial name: a retried upload of the same relative path
        # previously interleaved writes into one shared .partial and the last
        # replace published a corrupted file that passed the byte-count check.
        temporary = destination.with_name(
            f"{destination.name}.{uuid.uuid4().hex}.partial"
        )
        remaining = length
        with temporary.open("wb") as handle:
            while remaining:
                chunk = stream.read(min(1024 * 1024, remaining))
                if not chunk:
                    temporary.unlink(missing_ok=True)
                    raise ValueError("upload ended before the complete file was received")
                handle.write(chunk)
                remaining -= len(chunk)
        temporary.replace(destination)
        if os.name == "posix":
            try:
                # Staged uploads carry patient variants; do not let the
                # ambient umask leave them readable to other local users.
                os.chmod(destination, 0o600)
            except OSError:
                pass
        return {
            "path": str(destination),
            "filename": destination.name,
            "relative_path": str(Path(*safe_parts)),
            "bytes": length,
        }

    def _load_config(self, config_path: Path) -> dict:
        try:
            import yaml
        except ImportError as exc:
            raise ValueError(
                "PyYAML is required for the annotation settings screen "
                "(pip install pyyaml)."
            ) from exc
        config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        if not isinstance(config, dict):
            raise ValueError(f"annotation config is not a YAML object: {config_path}")
        return config

    def _resolved_reference_path(self, value) -> Path | None:
        if not isinstance(value, str) or not value.strip():
            return None
        path = Path(value).expanduser()
        if path.is_absolute():
            return path.resolve()
        # All stock configuration resource paths are intentionally relative to
        # ``references/``. Resolve that prefix through the user-selected
        # annotation root while retaining arbitrary project-relative paths.
        normalized = value.replace("\\", "/")
        while normalized.startswith("./"):
            normalized = normalized[2:]
        if normalized == "references" or normalized.startswith("references/"):
            suffix = normalized.removeprefix("references").lstrip("/")
            return (self.annotation_root / suffix).resolve()
        return (self.pipeline_root / path).resolve()

    def _absolutize_annotation_paths(self, value):
        """Return a config value with managed ``references/`` paths absolute."""
        if isinstance(value, dict):
            return {key: self._absolutize_annotation_paths(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._absolutize_annotation_paths(item) for item in value]
        if isinstance(value, str):
            normalized = value.replace("\\", "/")
            while normalized.startswith("./"):
                normalized = normalized[2:]
            if normalized == "references" or normalized.startswith("references/"):
                resolved = self._resolved_reference_path(value)
                return str(resolved) if resolved else value
        return value

    def _managed_preparation_paths(
        self,
        config: dict,
        resource_id: str,
        preferred_name: str | None = None,
    ) -> dict[str, str]:
        """Canonical destinations for user-supplied annotation datasets."""
        if resource_id == "dbnsfp":
            root = self.annotation_root / "dbnsfp"
            current = str(
                ((config.get("plugins") or {}).get("dbNSFP") or {}).get("path")
                or "dbNSFP5.4a_grch38.gz"
            )
            name = preferred_name or Path(current).name
            manifest = root / "dbnsfp.installed.json"
            if preferred_name is None and manifest.is_file():
                try:
                    installed = json.loads(manifest.read_text(encoding="utf-8"))
                    candidate = str(installed.get("filename") or "")
                    if (
                        _DBNSFP_GRCH38_FILENAME.fullmatch(candidate)
                        and (root / candidate).is_file()
                        and Path(str(root / candidate) + ".tbi").is_file()
                    ):
                        name = candidate
                except (OSError, ValueError, TypeError):
                    pass
            match = _DBNSFP_GRCH38_FILENAME.fullmatch(name)
            result = {"path": str(root / name)}
            if match:
                result["version"] = match.group("version")
            return result
        if resource_id == "promoterai":
            root = self.annotation_root / "promoterai"
            return {
                "file": str(root / "promoterai_scores.tsv.gz"),
                "transcript_map": str(root / "promoterai_transcripts.tsv"),
                "manifest": str(root / "promoterai.manifest.json"),
            }
        if resource_id == "logofunc":
            current = str(
                ((config.get("plugins") or {}).get("LoGoFunc") or {}).get("file")
                or "LoGoFuncVotingEnsemble_metadata_preds_final.csv.gz"
            )
            root = self.annotation_root / "logofunc"
            return {
                "file": str(root / Path(current).name),
                "manifest": str(root / "logofunc.manifest.json"),
            }
        if resource_id == "alphagenome_avi":
            root = self.annotation_root / "alphagenome-avi"
            release = root / "releases" / AVI_RELEASE
            return {"dest_dir": str(root), "file": str(release / "avi.grch38.vcf.gz"),
                    "manifest": str(release / "manifest.json")}
        if resource_id == "funcvep":
            root = self.annotation_root / "funcvep"
            return {
                "file": str(root / "funcvep_scores.grch38.tsv.gz"),
                "manifest": str(root / "funcvep.manifest.json"),
            }
        return {}

    @staticmethod
    def _generic_indexed_install_is_valid(
        resource_id: str,
        score_path: Path,
        manifest_path: Path,
        *,
        registry=None,
    ) -> bool:
        """Validate one registry-declared generic indexed-score installation."""
        index_path = Path(str(score_path) + ".tbi")
        if not all(path.is_file() for path in (score_path, index_path, manifest_path)):
            return False
        try:
            payload = load_indexed_scores_manifest(manifest_path)
            registry = registry or load_predictor_registry()
            resource = registry.resource(resource_id)
            annotators = [
                annotator for annotator in registry.annotators
                if annotator.resource_id == resource_id
                and annotator.adapter is Adapter.GENERIC_INDEXED_LOOKUP
            ]
            if len(annotators) != 1:
                return False
            annotator = annotators[0]
            validate_manifest_registry_contract(
                payload,
                (
                    predictor for predictor in registry.predictors
                    if predictor.annotator_id == annotator.id
                ),
                resource=resource,
                annotator=annotator,
            )
            validate_manifest_files(payload, score_path, index_path)
            return True
        except (OSError, AttributeError, TypeError, ValueError, KeyError):
            return False

    def _set_managed_preparation_paths(
        self,
        config: dict,
        resource_id: str,
        *,
        require_installed: bool,
        preferred_name: str | None = None,
    ) -> None:
        paths = self._managed_preparation_paths(
            config, resource_id, preferred_name=preferred_name
        )
        if not paths:
            return
        if resource_id == "alphagenome_avi":
            if require_installed and not valid_avi_bundle(Path(paths["manifest"])):
                return
            config.setdefault("custom_tracks", {}).setdefault("AlphaGenomeAVI", {}).update(paths)
            return
        primary = Path(paths["path"] if resource_id == "dbnsfp" else paths["file"])
        required = [primary, Path(str(primary) + ".tbi")]
        if resource_id in {"promoterai", "logofunc", "funcvep"}:
            required.append(Path(paths["manifest"]))
        if resource_id == "promoterai":
            required.append(Path(paths["transcript_map"]))
        if require_installed:
            if not all(path.is_file() for path in required):
                return
            if "manifest" in paths:
                try:
                    registry = load_predictor_registry()
                except (OSError, ValueError):
                    return
                generic_resources = {
                    annotator.resource_id for annotator in registry.annotators
                    if annotator.adapter is Adapter.GENERIC_INDEXED_LOOKUP
                }
                if (
                    resource_id in generic_resources
                    and not self._generic_indexed_install_is_valid(
                        resource_id,
                        primary,
                        Path(paths["manifest"]),
                        registry=registry,
                    )
                ):
                    return
        plugin_name = {
            "dbnsfp": "dbNSFP",
            "promoterai": "PromoterAI",
            "logofunc": "LoGoFunc",
            "funcvep": "FuncVEP",
        }[resource_id]
        config.setdefault("plugins", {}).setdefault(plugin_name, {}).update(paths)

    def _prefer_installed_managed_resources(self, config: dict) -> dict:
        for resource_id in ("dbnsfp", "promoterai", "logofunc", "funcvep", "alphagenome_avi"):
            self._set_managed_preparation_paths(
                config, resource_id, require_installed=True
            )
        return config

    def annotation_engine_status(self, config: dict) -> dict:
        """Read-only check of the prepared workspace, not the signed app tree."""
        status = self._container_image_status(config)
        status = {**status, "busy": self._engine_setup_reserved}
        if not status.get("available") or status["busy"]:
            return status
        container = config.get("container") or {}
        runtime = str(container.get("runtime") or "docker")
        if runtime not in {"docker", "podman"}:
            return status
        image = str(container.get("image") or "vep-annotate:latest")
        directory = workspace_directory(self.state_dir)
        try:
            check_container_workspace(directory, runtime, image)
        except (OSError, ValueError) as exc:
            return {**status, "available": False, "state": "sharing_required", "message": str(exc)}
        return status

    def _container_image_status(self, config: dict) -> dict:
        container = config.get("container") or {}
        runtime = str(container.get("runtime") or "docker")
        image = str(container.get("image") or "vep-annotate:latest")
        startup_status = self.docker_startup.status if runtime == "docker" else {}
        if startup_status.get("state") == "starting":
            return {"available": False, "state": "runtime_starting", "runtime": runtime,
                    "image": image, "message": startup_status["message"]}
        runtime_label = {
            "docker": "Docker",
            "podman": "Podman",
            "apptainer": "Apptainer",
            "singularity": "Singularity",
        }.get(runtime, runtime)
        if not shutil.which(runtime):
            return {
                "available": False,
                "state": "runtime_missing",
                "runtime": runtime,
                "image": image,
                "message": (
                    f"{runtime_label} is not installed or is not available to the local service."
                ),
            }
        if runtime in {"docker", "podman"}:
            try:
                fingerprint = hashlib.sha256()
                fingerprint.update(b"GUIDE-IEI container inputs v1\n")
                for name in CONTAINER_FINGERPRINT_FILES:
                    content = (self.pipeline_root / "docker" / name).read_bytes()
                    file_hash = hashlib.sha256(content).hexdigest()
                    fingerprint.update(f"{file_hash}  {name}\n".encode("utf-8"))
                expected_fingerprint = fingerprint.hexdigest()
            except OSError as exc:
                return {
                    "available": False,
                    "state": "image_stale",
                    "runtime": runtime,
                    "image": image,
                    "message": f"The annotation engine cannot be verified because a build input is unavailable: {exc}",
                }
            try:
                result = subprocess.run(
                    [
                        runtime,
                        "image",
                        "inspect",
                        "--format",
                        f'{{{{ index .Config.Labels "{CONTAINER_FINGERPRINT_LABEL}" }}}}',
                        image,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                return {
                    "available": False,
                    "state": "runtime_unavailable",
                    "runtime": runtime,
                    "image": image,
                    "message": f"{runtime_label} did not respond. Start or restart it, then refresh this page.",
                }
            except OSError as exc:
                return {
                    "available": False,
                    "state": "runtime_unavailable",
                    "runtime": runtime,
                    "image": image,
                    "message": f"{runtime_label} could not be checked: {exc}",
                }
            if result.returncode == 0:
                if result.stdout.strip() != expected_fingerprint:
                    return {
                        "available": False,
                        "state": "image_stale",
                        "runtime": runtime,
                        "image": image,
                        "message": (
                            "The annotation engine is from an older GUIDE-IEI version. "
                            "Use Set up annotation engine in Import & QC (Mac), or retry recommended dataset setup, "
                            "or run: bash docker/build.sh"
                        ),
                    }
                return {
                    "available": True,
                    "state": "ready",
                    "runtime": runtime,
                    "image": image,
                    "message": "Available",
                }
            diagnostic = f"{result.stderr}\n{result.stdout}".lower()
            connection_markers = (
                "cannot connect to the docker daemon",
                "failed to connect to the docker api",
                "is the docker daemon running",
                "error during connect",
                "docker daemon is not running",
                "cannot connect to podman",
                "unable to connect to podman",
                "podman.sock",
                "docker.sock",
                "connection refused",
            )
            if any(marker in diagnostic for marker in connection_markers):
                start_hint = "Start Docker Desktop" if runtime == "docker" else "Start the Podman machine"
                return {
                    "available": False,
                    "state": "runtime_unavailable",
                    "runtime": runtime,
                    "image": image,
                    "message": startup_status.get("message") if startup_status.get("state") in {"failed", "unavailable"} else f"{runtime_label} is installed but is not running. {start_hint}, then refresh this page.",
                }
            if "permission denied" in diagnostic:
                return {
                    "available": False,
                    "state": "runtime_unavailable",
                    "runtime": runtime,
                    "image": image,
                    "message": f"{runtime_label} is installed, but the local service cannot access it. Check runtime permissions, then refresh this page.",
                }
            return {
                "available": False,
                "state": "image_missing",
                "runtime": runtime,
                "image": image,
                "message": f"Pinned VEP image {image} is not installed. Build it with: bash docker/build.sh",
            }
        available = Path(image).expanduser().is_file()
        return {
            "available": available,
            "state": "ready" if available else "image_missing",
            "runtime": runtime,
            "image": image,
            "message": (
                "Available"
                if available
                else f"Pinned VEP image file is missing: {Path(image).expanduser()}"
            ),
        }

    def _container_image_available(self, config: dict) -> bool:
        """Compatibility wrapper for callers that need only readiness."""
        return bool(self._container_image_status(config)["available"])

    def _dbnsfp_header_columns(self, config: dict) -> set[str]:
        block = ((config.get("plugins") or {}).get("dbNSFP") or {})
        path = self._resolved_reference_path(block.get("path"))
        if not path or not path.is_file():
            return set()
        opener = gzip.open if path.name.lower().endswith((".gz", ".bgz")) else open
        try:
            with opener(path, "rt", encoding="utf-8", errors="replace") as handle:
                return set(handle.readline().rstrip("\r\n").lstrip("#").split("\t"))
        except (OSError, UnicodeError):
            return set()

    def _annotation_profile(self) -> dict:
        config_path = self.pipeline_root / "config" / "annotation.config.yaml"
        # Card copy is written for clinicians and wet-lab scientists: the tool
        # name stays as the title (it is what reports and literature use); the
        # subtitle answers "what question does this dataset answer for me".
        labels = {
            "dbnsfp": ("dbNSFP", "How damaging is each amino-acid change? One database bundling an extensive set of published predictors — AlphaMissense, REVEL, CADD (coding regions), SIFT, PolyPhen, MetaRNN, PrimateAI, conservation scores, and more"),
            "loftee": ("LOFTEE", "Transcript-level predicted loss-of-function annotation"),
            "spliceai": ("SpliceAI", "Predicts whether a variant disrupts RNA splicing, including variants outside the classic splice-site positions"),
            "repeatmasker": ("Repetitive-region flag", "Marks variants inside repetitive DNA, where sequencing and variant calling are less reliable"),
            "segdup": ("Duplicated-region flag", "Marks variants in segmental duplications — genomic segments with near-identical copies elsewhere in the genome, a classic source of false variant calls"),
            "promoterai": ("PromoterAI", "Predicts whether a variant near a gene's transcription start disrupts that gene's expression. Requires two files licensed from Illumina; nothing is uploaded anywhere"),
            "cadd_wgs": ("CADD scores for non-coding regions", "Genome-wide CADD deleteriousness scores for variants outside protein-coding regions. Coding-region CADD is already included with dbNSFP — install this only for whole-genome, non-coding analysis"),
            "logofunc": ("LoGoFunc", "Research-grade prediction of whether a missense variant causes gain of function, loss of function, or neither — a mechanism hint, not a clinical classifier"),
            "funcvep": ("FuncVEP", "Research-grade estimates of a missense variant's functional effect from three complementary model settings — not a clinical pathogenicity classification"),
            "alphagenome_avi": ("AlphaGenome AVI", "Genome-wide predicted functional impact of single-nucleotide variants, shown as an AVI Phred score"),
            "clinvar": ("ClinVar", "What clinical laboratories have reported about each variant. Reports come from many submitters and can conflict; review status matters. Refreshed automatically before every run"),
            "loftee_ptc_50bp": ("Nonsense-mediated decay 50-bp rule re-calculation", "Re-checks frameshift variants at the position of the new stop codon they create and flags cases where the rule suggests possible NMD escape"),
            "clinvar_aa_match": ("Clinical protein-change and residue matching", "Identifies P/LP reports in ClinVar, ClinGen, and an installed GenIA variant export with the same protein change or a different missense change at the same residue. These are candidate PS1/PM5 evidence only; the reviewer must confirm transcript, condition, review status, disease mechanism, and evidence independence"),
            "liftover": ("GRCh37/hg19 input conversion", "Lets you analyze VCFs made against the older GRCh37/hg19 reference. Variants are converted to GRCh38 with safeguards and a full audit trail — nothing is silently dropped"),
            "ccre": ("ENCODE cCRE regions", "The genome-wide catalog of candidate cis-regulatory elements (cCREs) — regions such as promoters and enhancers likely to control gene activity. Aggregate level (combined across samples, not tissue-specific); used by whole-genome import to keep potentially regulatory variants"),
            "screen_context": ("ENCODE tissue and immune contexts (SCREEN)", "For whole-genome analyses: SCREEN records a positive regulatory signature in reference tissues and immune cell types, adding context to the aggregate cCRE map"),
            "clingen_erepo": ("ClinGen", "Variant interpretations from ClinGen's disease-specific expert panels — the highest review level available. Stored locally; your variants are never sent to any server"),
            "genia": ("GenIA", "Registered-user GenIA evidence for immune gene–disease knowledge, reported phenotypes, and exact GRCh38 alleles. Install any available component; source files stay on this computer"),
        }
        try:
            config = self._prefer_installed_managed_resources(
                self._load_config(config_path)
            )
        except ValueError as exc:
            return {
                "ready": False,
                "datasets_ready": False,
                "execution_ready": False,
                "error": str(exc),
                "foundations": [],
                "sources": [],
                "recommended_profiles": {
                    "exome": {"installed": False, "missing": []},
                    "whole_genome": {"installed": False, "missing": []},
                },
                "dbnsfp_predictors": [],
                "defaults": {"fork": 8},
            }

        def source_path(source_id: str, block: dict) -> list[Path]:
            if source_id == "dbnsfp":
                values = [block.get("path")]
            elif source_id == "loftee":
                values = [
                    block.get("human_ancestor_fa"),
                    block.get("conservation_file"),
                    block.get("gerp_bigwig"),
                ]
            elif source_id == "spliceai":
                values = [block.get("snv")]
            elif source_id == "promoterai":
                values = [
                    block.get("file"),
                    block.get("transcript_map"),
                    block.get("manifest"),
                ]
            elif source_id == "cadd_wgs":
                values = [block.get("snv"), block.get("indels")]
            elif source_id == "logofunc":
                values = [block.get("file"), block.get("manifest")]
            elif source_id in {"funcvep", "alphagenome_avi"}:
                values = [block.get("file"), block.get("manifest")]
            elif source_id == "screen_context":
                # The active bundle is normally recorded by the installed
                # pointer file, not the config; resolved separately below.
                values = [block.get("manifest")]
            elif source_id == "loftee_ptc_50bp":
                values = [
                    block.get("gtf"),
                    ((config.get("reference") or {}).get("fasta") or {}).get("path"),
                ]
            elif source_id == "clinvar_aa_match":
                values = []
            elif source_id == "liftover":
                values = [block.get("source_fasta"), block.get("chain")]
            elif source_id == "ccre":
                values = [
                    block.get("bed"),
                    ((config.get("wgs_review") or {}).get("gene_tss") or {}).get("path"),
                ]
            elif source_id == "clingen_erepo":
                values = [block.get("database"), block.get("vcf"), block.get("manifest")]
            elif source_id == "genia":
                values = [block.get("database")]
            else:
                values = [block.get("file")]
            return [
                resolved for value in values
                if (resolved := self._resolved_reference_path(value)) is not None
            ]

        try:
            indexed_registry = load_predictor_registry()
            generic_indexed_resources = {
                annotator.resource_id for annotator in indexed_registry.annotators
                if annotator.adapter is Adapter.GENERIC_INDEXED_LOOKUP
            }
        except (OSError, ValueError):
            indexed_registry = None
            generic_indexed_resources = set()

        sources = []
        for source_id, location in ANNOTATION_SOURCE_PATHS.items():
            parent = config.get(location[0]) or {}
            block = parent if location[1] is None else parent.get(location[1]) or {}
            paths = source_path(source_id, block)
            enabled = bool(block.get("enabled", False))
            required = source_id in REQUIRED_DIAGNOSTIC_SOURCES or bool(
                block.get("required", False)
            )
            # ClinVar can be fetched when a run starts, so an absent local copy
            # is not a setup blocker.
            auto_fetch = source_id == "clinvar" and bool(
                (config.get("clinvar") or {}).get("auto_fetch", True)
            )
            # An UNCONFIGURED source (no paths) is not an installed one:
            # `not []` is True, which advertised every unconfigured source —
            # including required diagnostic sources — as ready, letting the
            # operator start a run that fails (or silently skips) later.
            installed = bool(paths) and all(path.exists() for path in paths)
            if (
                installed
                and indexed_registry is None
                and block.get("file")
                and block.get("manifest")
            ):
                # A corrupt registry must not crash the dataset panel. Fail
                # closed for manifest-driven predictor installations while
                # leaving unrelated resources usable.
                installed = False
            if source_id == "clinvar_aa_match":
                # Not a dataset: the residue-matching feature ships with the
                # software and its table is rebuilt automatically from the
                # downloaded ClinVar release (run_annotation.sh builds and
                # release-stamps it), so there is nothing for the operator to
                # install. The empty-paths rule above must not label this
                # derived feature "Bundled file missing".
                installed = True
            if source_id == "screen_context":
                # The active bundle is the pointer-registered (or env/config
                # named) manifest, independent of the config path above.
                active_manifest = self._screen_context_manifest(config)
                installed = bool(active_manifest and active_manifest.is_file())
                if active_manifest is not None:
                    paths = [active_manifest]
            if installed and source_id == "promoterai" and paths:
                score_path = paths[0]
                installed = (
                    score_path.exists()
                    and (
                        Path(str(score_path) + ".tbi").exists()
                        or Path(str(score_path) + ".csi").exists()
                    )
                    and all(path.exists() for path in paths[1:])
                )
            if installed and source_id == "logofunc" and paths:
                score_path = paths[0]
                installed = (
                    score_path.exists()
                    and Path(str(score_path) + ".tbi").exists()
                    and all(path.exists() for path in paths[1:])
                )
            if installed and source_id in generic_indexed_resources and paths:
                score_path = paths[0]
                installed = self._generic_indexed_install_is_valid(
                    source_id,
                    score_path,
                    paths[-1],
                    registry=indexed_registry,
                )
            if installed and source_id == "alphagenome_avi":
                installed = len(paths) == 2 and valid_avi_bundle(paths[1])
                if installed:
                    installed = paths[0].resolve() == (paths[1].parent / "avi.grch38.vcf.gz").resolve()
            if installed and source_id in {
                "dbnsfp", "spliceai", "cadd_wgs", "repeatmasker", "segdup", "clinvar"
            }:
                installed = all(
                    path.exists()
                    and (
                        not path.name.endswith(".gz")
                        or Path(str(path) + ".tbi").exists()
                        or Path(str(path) + ".csi").exists()
                    )
                    for path in paths
                )
            if installed and source_id == "ccre" and paths:
                bed_path = paths[0]
                installed = (
                    bed_path.exists()
                    and (
                        Path(str(bed_path) + ".tbi").exists()
                        or Path(str(bed_path) + ".csi").exists()
                    )
                    and all(path.exists() for path in paths[1:])
                )
            if installed and source_id == "clingen_erepo" and paths:
                database, vcf, manifest = paths
                installed = database.is_file() and vcf.is_file() and manifest.is_file() \
                    and self.clingen_erepo.status().get("available", False)
            if source_id == "genia":
                genia_status = self.genia.status()
                installed = bool(
                    paths and paths[0].is_file()
                    and (genia_status.get("capabilities") or {}).get("variant_evidence")
                )
            if installed and source_id == "liftover" and paths:
                source_fasta = paths[0]
                installed = (
                    source_fasta.exists()
                    and Path(str(source_fasta) + ".fai").exists()
                    and Path(str(source_fasta) + ".gzi").exists()
                    and all(path.exists() for path in paths[1:])
                )
            available = auto_fetch or installed
            label, description = labels[source_id]
            setup = ANNOTATION_SOURCE_SETUP[source_id]
            access = str(setup.get("access") or (
                "bundled" if setup.get("setup_mode") == "bundled" else "public"
            ))
            recommendation = str(
                setup.get("recommendation")
                or SOURCE_RECOMMENDATION_DEFAULTS.get(source_id)
                or ("required" if required else "optional")
            )
            source_version = str(block.get("version") or "")
            if source_id == "dbnsfp" and not installed:
                # The stock config's initial release is only a fallback path;
                # do not present it as the version the user must download.
                source_version = ""
            if source_id == "clingen_erepo" and installed:
                generated = str(self.clingen_erepo.status().get("generated_utc") or "")
                source_version = generated[:10]
            if source_id == "genia" and installed:
                component = (self.genia.status().get("components") or {}).get("variant_vcf") or {}
                source_version = str(component.get("installed_at") or "")[:10]
            available_in = (
                ["whole_genome"]
                if source_id in {"promoterai", "cadd_wgs", "ccre", "screen_context", "alphagenome_avi"}
                else ["exome", "whole_genome"]
            )
            sources.append({
                "id": source_id,
                "label": label,
                "description": description,
                "enabled": enabled,
                "required": required,
                "available": available,
                "installed": installed,
                "configured_paths": [str(path) for path in paths],
                "version": source_version,
                "available_in": available_in,
                "access": access,
                "recommendation": recommendation,
                **setup,
                "status": (
                    "ready" if installed else
                    "required_missing" if enabled and required else
                    "optional_missing"
                ),
            })

        fasta = ((config.get("reference") or {}).get("fasta") or {})
        cache_path = self._resolved_reference_path(
            (config.get("reference") or {}).get("vep_cache_dir")
        )
        fasta_path = self._resolved_reference_path(fasta.get("path"))
        cache_release, container_release = self._installed_vep_releases(config)
        container_status = self._container_image_status(config)
        container_available = bool(container_status["available"])
        foundations = [
            {
                "id": "vep_container",
                "label": "Ensembl VEP (annotation engine)",
                "description": "The core program that annotates your variants (Ensembl VEP). Version-locked so results are reproducible.",
                "available": container_available,
                "required": True,
                "version": container_release,
                "state": container_status["state"],
                "message": container_status["message"],
            },
            {
                "id": "vep_cache",
                "label": "Ensembl VEP cache (gene & transcript database)",
                "description": "The local copy of human gene, transcript, and population data the engine reads (Ensembl VEP cache). Large (~25 GB) but one-time.",
                "available": bool(cache_path and cache_path.exists()),
                "required": True,
                "version": cache_release,
            },
            {
                "id": "reference_fasta",
                "label": "Reference genome (GRCh38)",
                "description": "The standard human genome sequence your variants are compared against.",
                "available": bool(fasta_path and fasta_path.exists()),
                "required": True,
                "version": None,
            },
        ]
        dataset_foundations_ready = all(
            item["available"]
            for item in foundations
            if item["id"] != "vep_container"
        )
        required_sources_ready = all(
            source["available"]
            for source in sources
            if source["enabled"] and source["required"]
        )
        datasets_ready = dataset_foundations_ready and required_sources_ready
        execution_ready = container_available
        ready = datasets_ready and execution_ready

        source_by_id = {source["id"]: source for source in sources}
        automatic_exome_ids = (
            "loftee", "spliceai", "repeatmasker", "segdup", "clinvar",
            "clingen_erepo", "loftee_ptc_50bp",
        )

        def automatic_profile(source_ids: tuple[str, ...]) -> dict:
            missing = []
            for item in foundations:
                if item["id"] != "vep_container" and not item["available"]:
                    missing.append(item["id"])
            for source_id in source_ids:
                source = source_by_id.get(source_id)
                if not source or not source["installed"]:
                    missing.append(source_id)
            return {"installed": not missing, "missing": missing}

        recommended_profiles = {
            "exome": automatic_profile(automatic_exome_ids),
            # CADD WGS is deliberately NOT part of the recommended set: it is
            # an optional research annotation (83 GiB, non-commercial terms)
            # installed from its own card. The SCREEN context layer powers the
            # whole-genome Regulatory evidence tab and is small, so it is.
            "whole_genome": automatic_profile(
                automatic_exome_ids + ("ccre", "screen_context", "alphagenome_avi")
            ),
        }
        dbnsfp_header = self._dbnsfp_header_columns(config)
        dbnsfp_predictors = [
            {
                **predictor,
                "available": bool(dbnsfp_header) and all(
                    column in dbnsfp_header for column in predictor["columns"]
                ),
            }
            for predictor in DBNSFP_OPTIONAL_PREDICTORS
        ]
        return {
            "ready": ready,
            # Runtime/image problems are shown once on the container foundation
            # row. They block VEP execution, but are not annotation-dataset
            # installation errors and must not be duplicated as a page alert.
            "datasets_ready": datasets_ready,
            "execution_ready": execution_ready,
            "error": "",
            "foundations": foundations,
            "sources": sources,
            "recommended_profiles": recommended_profiles,
            "dbnsfp_predictors": dbnsfp_predictors,
            "defaults": {
                "fork": int((config.get("run") or {}).get("fork", 8)),
            },
        }

    def _write_job_config(
        self, job_id: str, base_config_path: Path, options: dict
    ) -> Path:
        config = self._absolutize_annotation_paths(
            self._prefer_installed_managed_resources(
                self._load_config(base_config_path)
            )
        )
        analysis_scope = str(options.get("analysis_scope") or "exome")
        if analysis_scope not in {"exome", "whole_genome"}:
            raise ValueError("analysis_scope must be exome or whole_genome")
        if analysis_scope == "exome":
            unavailable = [
                source_id for source_id in ("promoterai", "cadd_wgs", "ccre")
                if options.get(source_id) is True
            ]
            if unavailable:
                raise ValueError(
                    "annotation source is available only for whole-genome analysis: "
                    + ", ".join(unavailable)
                )
            for source_id in ("promoterai", "cadd_wgs", "ccre"):
                location = ANNOTATION_SOURCE_PATHS[source_id]
                config.setdefault(location[0], {}).setdefault(
                    location[1], {}
                )["enabled"] = False
        config.setdefault("region", {})["coding_only"] = analysis_scope == "exome"
        for source_id in REQUIRED_DIAGNOSTIC_SOURCES:
            if options.get(source_id) is False:
                raise ValueError(
                    f"{source_id} is required by the diagnostic annotation profile"
                )
        for source_id, location in ANNOTATION_SOURCE_PATHS.items():
            if source_id not in options:
                continue
            parent = config.setdefault(location[0], {})
            block = parent if location[1] is None else parent.setdefault(location[1], {})
            block["enabled"] = bool(options[source_id]) and (
                analysis_scope == "whole_genome"
                or source_id not in {"promoterai", "cadd_wgs", "ccre", "alphagenome_avi"}
            )
        if "fork" in options:
            try:
                fork = int(options["fork"])
            except (TypeError, ValueError) as exc:
                raise ValueError("VEP workers must be a number") from exc
            if fork < 1 or fork > 64:
                raise ValueError("VEP workers must be between 1 and 64")
            config.setdefault("run", {})["fork"] = fork
        if "dbnsfp_predictors" in options:
            selected = options["dbnsfp_predictors"]
            if not isinstance(selected, list) or not all(
                isinstance(value, str) for value in selected
            ):
                raise ValueError("dbnsfp_predictors must be a list of predictor IDs")
            catalog = {
                predictor["id"]: predictor
                for predictor in DBNSFP_OPTIONAL_PREDICTORS
            }
            unknown = sorted(set(selected) - set(catalog))
            if unknown:
                raise ValueError(
                    "unsupported dbNSFP predictor selection: " + ", ".join(unknown)
                )
            header_columns = self._dbnsfp_header_columns(config)
            requested_columns = [
                column
                for predictor_id in dict.fromkeys(selected)
                for column in catalog[predictor_id]["columns"]
            ]
            unavailable = [
                column for column in requested_columns
                if column not in header_columns
            ]
            if unavailable:
                raise ValueError(
                    "selected dbNSFP fields are not available in the installed "
                    "dataset: " + ", ".join(unavailable)
                )
            dbnsfp = config.setdefault("plugins", {}).setdefault("dbNSFP", {})
            base_columns = dbnsfp.get("columns") or []
            if not isinstance(base_columns, list):
                raise ValueError(
                    "UI predictor selection requires dbNSFP columns to be a YAML list"
                )
            dbnsfp["columns"] = list(dict.fromkeys(base_columns + requested_columns))

        # These consumers historically resolve paths relative to the config
        # location. Make the two affected values absolute in the generated,
        # per-job config so its state-directory location is transparent.
        fasta = (config.get("reference") or {}).get("fasta") or {}
        if fasta.get("path"):
            fasta["path"] = str(self._resolved_reference_path(fasta["path"]))
        ptc = (config.get("post_processing") or {}).get("loftee_ptc_50bp") or {}
        if ptc.get("gtf"):
            ptc["gtf"] = str(self._resolved_reference_path(ptc["gtf"]))
        clinvar = config.get("clinvar") or {}
        if clinvar.get("dest_dir"):
            clinvar["dest_dir"] = str(
                self._resolved_reference_path(clinvar["dest_dir"])
            )

        import yaml
        config_dir = self.state_dir / "job-configs"
        config_dir.mkdir(parents=True, exist_ok=True)
        destination = config_dir / f"{job_id}.annotation.yaml"
        destination.write_text(
            "# Generated by IEI Variant Review from UI settings.\n"
            + yaml.safe_dump(config, sort_keys=False),
            encoding="utf-8",
        )
        return destination

    def _write_resource_config(
        self, resource_id: str, *, dbnsfp_filename: str | None = None
    ) -> Path:
        """Write a short-lived, root-resolved config for a UI resource action."""
        import yaml

        config = self._load_config(
            self.pipeline_root / "config" / "annotation.config.yaml"
        )
        if resource_id in {"dbnsfp", "promoterai", "logofunc", "funcvep", "alphagenome_avi"}:
            self._set_managed_preparation_paths(
                config,
                resource_id,
                require_installed=False,
                preferred_name=dbnsfp_filename if resource_id == "dbnsfp" else None,
            )
        else:
            config = self._prefer_installed_managed_resources(config)
        config = self._absolutize_annotation_paths(config)
        config_dir = self.state_dir / "resource-configs"
        config_dir.mkdir(parents=True, exist_ok=True)
        destination = config_dir / f"{resource_id}.{uuid.uuid4().hex}.yaml"
        destination.write_text(
            "# Generated by IEI Variant Review for a local dataset action.\n"
            + yaml.safe_dump(config, sort_keys=False),
            encoding="utf-8",
        )
        return destination

    @staticmethod
    def _required_path(payload: dict, key: str, must_exist: bool) -> Path:
        raw = payload.get(key)
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError(f"{key} is required")
        path = Path(raw).expanduser().resolve()
        if must_exist and not path.is_file():
            raise ValueError(f"{key} does not exist: {path}")
        return path

    def cancel(self, job_id: str) -> dict:
        job = self.store.get(job_id)
        if not job:
            raise NotFoundError(f"job not found: {job_id}")
        if job["status"] in TERMINAL_STATUSES:
            return job
        if job["status"] == "queued":
            return self.store.update(
                job_id,
                status="cancelled",
                finished_at=utc_now(),
                error="Cancelled before execution.",
            )
        with self._process_lock:
            process = self._processes.get(job_id)
        if process and process.poll() is None:
            try:
                if os.name == "posix":
                    os.killpg(process.pid, signal.SIGTERM)
                else:
                    process.terminate()
            except ProcessLookupError:
                pass
        return self.store.update(
            job_id,
            status="cancelled",
            finished_at=utc_now(),
            error="Cancelled by the user.",
        )

    def log_tail(self, job_id: str, max_bytes: int = 64_000) -> str:
        job = self.store.get(job_id)
        if not job:
            raise NotFoundError(f"job not found: {job_id}")
        path = Path(job["log_path"])
        if not path.exists():
            return ""
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - max_bytes))
            return handle.read().decode("utf-8", errors="replace")

    def _commands(self, job: dict) -> list[list[str]]:
        preflight = [
            "bash",
            str(self.pipeline_root / "scripts" / "preflight.sh"),
            job["config_path"],
            job["input_path"],
            job["output_path"],
            "--input-assembly",
            job["input_assembly"],
        ]
        annotation = [
            "bash",
            str(self.pipeline_root / "scripts" / "run_annotation.sh"),
            "--input",
            job["input_path"],
            "--output",
            job["output_path"],
            "--config",
            job["config_path"],
            "--input-assembly",
            job["input_assembly"],
        ]
        if not job["coding_only"]:
            annotation.append("--all-variants")
        if job["include_filtered"]:
            annotation.append("--include-filtered")
        if not job["use_clinvar"]:
            annotation.append("--no-clinvar")
        return [preflight, annotation]

    def _worker_loop(self) -> None:
        while not self._stop.is_set():
            try:
                job_id = self._queue.get(timeout=0.25)
            except queue.Empty:
                continue
            if job_id is None:
                self._queue.task_done()
                return
            try:
                while self.docker_startup.status["state"] == "starting" and not self._stop.is_set():
                    self._stop.wait(.25)
                if self._stop.is_set():
                    return  # Keep its stored status queued for the next launch.
                self._run_job(job_id)
            except Exception as exc:  # noqa: BLE001 - the boundary IS the point
                # Audit M22: an exception that escaped _run_job (a store
                # error before the job's own try block, an unplugged data
                # drive, a bug) used to kill this thread, leaving every later
                # job "queued" until the next restart with nothing visible in
                # the UI. Record it, fail the job as far as the store allows,
                # and keep serving the queue. The job is NOT re-queued: a
                # run that died at an unknown point may have written a
                # partial output under the final name, so its outcome must
                # be judged by the user, not retried behind their back.
                self._note_worker_failure(job_id, exc)
            finally:
                self._queue.task_done()

    def _note_worker_failure(self, job_id: str, exc: BaseException) -> None:
        self._worker_failures += 1
        self._worker_last_error = f"{type(exc).__name__}: {exc}"
        self._worker_last_error_at = utc_now()
        print(
            f"ERROR annotation worker: job {job_id} escaped with "
            f"{self._worker_last_error}",
            file=sys.stderr,
        )
        traceback.print_exc(file=sys.stderr)
        try:
            current = self.store.get(job_id)
            if current and current["status"] in {"queued", "running"}:
                self.store.update(
                    job_id,
                    status="failed",
                    finished_at=utc_now(),
                    exit_code=1,
                    pid=None,
                    error=(
                        "The annotation worker hit an internal error while "
                        f"handling this job ({type(exc).__name__}); see the "
                        "service log. The output, if any, must not be trusted."
                    ),
                )
        except Exception as store_error:  # noqa: BLE001
            print(
                f"ERROR annotation worker: could not mark job {job_id} failed: "
                f"{type(store_error).__name__}: {store_error}",
                file=sys.stderr,
            )
        finally:
            with self._process_lock:
                process = self._processes.pop(job_id, None)
            if process is not None and process.poll() is None:
                try:
                    process.terminate()
                except OSError:
                    pass

    def _run_job(self, job_id: str) -> None:
        job = self.store.get(job_id)
        if not job or job["status"] != "queued":
            return
        commands = self._commands(job)
        self.store.update(
            job_id,
            status="running",
            started_at=utc_now(),
            command_json=json.dumps(commands),
            error=None,
        )
        log_path = Path(job["log_path"])
        exit_code = 0
        try:
            with log_path.open("a", encoding="utf-8", buffering=1) as log:
                log.write(f"[{utc_now()}] Job {job_id} started\n")
                for command in commands:
                    current = self.store.get(job_id)
                    if not current or current["status"] in {"cancelled", "interrupted"}:
                        return
                    log.write("$ " + " ".join(json.dumps(item) for item in command) + "\n")
                    with self._process_lock:
                        # Shutdown snapshots owned processes under this lock.
                        # Do not spawn an untracked child after that snapshot.
                        if self._stop.is_set():
                            self.store.update(job_id, status="interrupted",
                                              finished_at=utc_now(), pid=None,
                                              error="The local service was stopped.")
                            return
                        process = subprocess.Popen(
                            command,
                            cwd=self.pipeline_root,
                            stdout=log,
                            stderr=subprocess.STDOUT,
                            start_new_session=(os.name == "posix"),
                        )
                        self._processes[job_id] = process
                    self.store.update(job_id, pid=process.pid)
                    exit_code = process.wait()
                    with self._process_lock:
                        self._processes.pop(job_id, None)
                    current = self.store.get(job_id)
                    if current and current["status"] in {"cancelled", "interrupted"}:
                        return
                    if exit_code:
                        raise RuntimeError(
                            f"Command failed with exit code {exit_code}. See the job log."
                        )
                final_output = self._resolve_final_output(Path(job["output_path"]))
                log.write(f"[{utc_now()}] Job completed: {final_output}\n")
            self.store.update(
                job_id,
                status="succeeded",
                finished_at=utc_now(),
                final_output_path=str(final_output),
                exit_code=0,
                pid=None,
            )
        except Exception as exc:
            current = self.store.get(job_id)
            if current and current["status"] in {"cancelled", "interrupted"}:
                return
            self.store.update(
                job_id,
                status="failed",
                finished_at=utc_now(),
                exit_code=exit_code or 1,
                pid=None,
                error=str(exc),
            )
        finally:
            with self._process_lock:
                self._processes.pop(job_id, None)

    @staticmethod
    def _resolve_final_output(output_path: Path) -> Path:
        # The run's own sidecar names the file it actually produced —
        # authoritative, no mtime guessing (equal timestamps on
        # coarse-granularity filesystems mis-picked a stale sibling).
        sidecar = Path(f"{output_path}.deliverable")
        if sidecar.is_file():
            try:
                name = sidecar.read_text().strip().split("\n")[0]
                # A malformed sidecar (empty, path separators, junk bytes)
                # must fall back, not fail a successful run: with_name
                # raises ValueError on bad names and read_text can raise
                # UnicodeDecodeError.
                if name and os.sep not in name and "/" not in name:
                    named = output_path.with_name(name)
                    if named.is_file():
                        return named
            except (OSError, ValueError):
                pass
        # Legacy runs without a sidecar: prefer the ClinVar
        # amino-acid-match sibling only when it is at least as new as this
        # run's base output — an existence-only check let a previous run's
        # .aamatch.vcf.gz masquerade as the current result.
        aamatch = Path(str(output_path)[:-7] + ".aamatch.vcf.gz")
        if not aamatch.exists():
            return output_path
        if not output_path.exists():
            return aamatch
        if aamatch.stat().st_mtime >= output_path.stat().st_mtime:
            return aamatch
        return output_path

    def shutdown(self) -> None:
        self._stop.set()
        self.docker_startup.stop()
        # The bulk-intake worker stops between items on _stop. Give it a
        # moment to finish the current bookkeeping write; a worker deep in a
        # long prefilter is left as a daemon — the interrupted item is reset
        # to queued and resumed at the next service start.
        bulk_thread = self._bulk_intake_thread
        if bulk_thread and bulk_thread.is_alive():
            bulk_thread.join(timeout=2)
        with self._process_lock:
            running = list(self._processes.items())
            # Persist the reason BEFORE signalling the child. Otherwise its
            # wait() can wake and classify SIGTERM as failure before this write.
            for job_id, process in running:
                if process.poll() is None:
                    self.store.update(
                        job_id, status="interrupted", finished_at=utc_now(),
                        error="The local service was stopped.",
                    )
        for job_id, process in running:
            if process.poll() is None:
                try:
                    if os.name == "posix":
                        os.killpg(process.pid, signal.SIGTERM)
                    else:
                        process.terminate()
                except (ProcessLookupError, PermissionError):
                    pass
        with self._resource_lock:
            resource_running = list(self._resource_processes.items())
            resource_threads = list(self._resource_threads.values())
        with self._storage_lock:
            storage_threads = list(self._storage_threads.values())
        for job_id, process in resource_running:
            if process.poll() is None:
                try:
                    if os.name == "posix":
                        os.killpg(process.pid, signal.SIGTERM)
                    else:
                        process.terminate()
                except (ProcessLookupError, PermissionError):
                    pass
                self._update_resource_job(
                    job_id,
                    status="interrupted",
                    finished_at=utc_now(),
                    error="The local service was stopped.",
                )
        self._queue.put(None)
        if self._worker:
            self._worker.join(timeout=5)
        for thread in resource_threads:
            thread.join(timeout=2)
        # Storage copies inspect ``_stop`` between chunks and never activate
        # the new root after shutdown. The original source remains untouched.
        for thread in storage_threads:
            thread.join(timeout=5)
        self._release_instance_lock()


class WorkbenchRequestHandler(BaseHTTPRequestHandler):
    service: AnnotationJobService
    server_version = f"IEIWorkbench/{SERVICE_VERSION}"

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self._cors_headers()
        self.end_headers()

    def do_GET(self) -> None:
        try:
            self._get()
        except CohortMergeBusyError as exc:
            self._json({"error": str(exc), "kind": "cohort_merge_busy"}, HTTPStatus.SERVICE_UNAVAILABLE)
        except NotFoundError as exc:
            self._json({"error": str(exc)}, HTTPStatus.NOT_FOUND)
        except ValueError as exc:
            self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:  # noqa: BLE001 - HTTP boundary (audit M23)
            self._internal_error(exc)

    def _internal_error(self, exc: BaseException) -> None:
        """Answer an unexpected failure with a JSON 500 instead of a dropped
        connection (audit M23: sqlite3.Error / OSError / RuntimeError from a
        handler surfaced in the UI as "Failed to fetch").

        The response carries the exception TYPE and a short reference; the
        message and traceback — which may name workstation paths — go to
        the service log under that reference.
        """
        reference = uuid.uuid4().hex[:12]
        print(
            f"ERROR request {self.command} {urlparse(self.path).path} "
            f"[{reference}] {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        traceback.print_exc(file=sys.stderr)
        try:
            self._json(
                {
                    "error": (
                        "The local service hit an internal error handling this "
                        "request; see the service log for reference "
                        f"{reference}."
                    ),
                    "kind": type(exc).__name__,
                    "reference": reference,
                },
                HTTPStatus.INTERNAL_SERVER_ERROR,
            )
        except (OSError, ValueError):
            # Headers already sent, or the client is gone: nothing to answer.
            pass

    def _get(self) -> None:
        # DNS rebinding makes an attacker's page SAME-origin with this
        # loopback service, so CORS never applies and unguarded GETs hand
        # over PHI. The Host check severs that path; loopback browsers and
        # local tools are unaffected.
        if self._reject_cross_site():
            return
        parsed_url = urlparse(self.path)
        path = parsed_url.path
        query = parse_qs(parsed_url.query)
        if getattr(self.server, "web_root", None) and not path.startswith("/api/"):
            from local_service.static_site import serve_static
            serve_static(self, self.server.web_root)
            return
        if path == "/api/service/status":
            self._json(self.service.lifecycle_status())
            return
        if path == "/api/annotation-engine/status":
            config = self.service._load_config(self.service.pipeline_root / "config/annotation.config.yaml")
            self._json(self.service.annotation_engine_status(config))
            return
        if path == "/api/health":
            worker = self.service.worker_health()
            self._json({
                "ok": True,
                "version": SERVICE_VERSION,
                "worker": worker,
                # False when the queue is unattended (worker thread gone) or
                # a job escaped its boundary since startup — the UI surfaces
                # it; the service keeps answering.
                "worker_ok": bool(worker["alive"] and worker["failures"] == 0),
            })
        elif path == "/api/software-update/status":
            self._json(self.service.software_updater.status())
        elif path == "/api/capabilities":
            self._json(self.service.capabilities())
        elif path == "/api/jobs":
            self._json({"jobs": [
                self.service.with_job_progress(job)
                for job in self.service.store.list()
            ]})
        elif path == "/api/resource-downloads":
            self._json({"jobs": self.service.resource_downloads()})
        elif path == "/api/gene-knowledge/status":
            self._json(self.service.gene_knowledge.status())
        elif path == "/api/gene-knowledge/filters":
            self._json(self.service.gene_knowledge.filter_catalog())
        elif path.startswith("/api/gene-knowledge/gene/"):
            identifier = unquote(path.removeprefix("/api/gene-knowledge/gene/"))
            self._json(self.service.gene_knowledge.gene(identifier))
        elif path == "/api/genia/variant":
            try:
                chrom = (query.get("chrom") or [""])[0]
                pos = int((query.get("pos") or ["0"])[0])
                ref = (query.get("ref") or [""])[0]
                alt = (query.get("alt") or [""])[0]
                if not chrom or pos < 1 or not ref or not alt:
                    raise ValueError("chrom, pos, ref, and alt are required")
                self._json(self.service.genia.variant(chrom, pos, ref, alt))
            except ValueError as exc:
                self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        elif path == "/api/clingen-erepo/status":
            self._json(self.service.clingen_erepo.status())
        elif path == "/api/clingen-erepo/variant":
            try:
                chrom = (query.get("chrom") or [""])[0]
                pos = int((query.get("pos") or ["0"])[0])
                ref = (query.get("ref") or [""])[0]
                alt = (query.get("alt") or [""])[0]
                if not chrom or pos < 1 or not ref or not alt:
                    raise ValueError("chrom, pos, ref, and alt are required")
                self._json(self.service.clingen_erepo.variant(chrom, pos, ref, alt))
            except ValueError as exc:
                self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        elif path == "/api/cohort/stats":
            self._json(self.service.cohort.stats())
        elif path == "/api/cohort/profiles":
            self._json({"profiles": self.service.cohort.profiles()})
        elif path == "/api/sample-library":
            self._json({"datasets": self.service.sample_library.list(
                query=(query.get("query") or [""])[0],
                limit=int((query.get("limit") or ["500"])[0]),
            )})
        elif path == "/api/sample-library/profiles":
            self._json({"profiles": self.service.sample_library.profiles()})
        elif path == "/api/bulk-intake":
            self._json(self.service.bulk_intake_snapshot())
        elif path.startswith("/api/bulk-intake/"):
            self._json(self.service.bulk_intake_snapshot(path.split("/")[3]))
        elif path == "/api/storage":
            self._json(self.service.storage_stats())
        elif path == "/api/storage/locations":
            self._json(self.service.storage_configuration(include_usage=False))
        elif path == "/api/storage/migrations":
            self._json({"jobs": self.service.storage_migrations()})
        elif path.startswith("/api/sample-library/") and path.endswith("/file"):
            dataset_id = path.split("/")[3]
            mutation_started = False
            try:
                # Reviews open the dataset's own sample projection; a
                # multi-sample managed source is never handed to the browser
                # whole. Building that projection WRITES cache files, so this
                # GET must hold the storage-mutation reservation like its
                # POST siblings — a migration snapshot walked mid-write
                # either failed verification or silently lost the files.
                self.service.begin_storage_mutation()
                mutation_started = True
                self._file(self.service.sample_library.review_file(dataset_id))
            except KeyError:
                self._json({"error": "library dataset not found"}, HTTPStatus.NOT_FOUND)
            except FileNotFoundError as exc:
                self._json({"error": str(exc)}, HTTPStatus.NOT_FOUND)
            except (ValueError, RuntimeError) as exc:
                self._json(
                    {"error": f"sample projection failed: {exc}"},
                    HTTPStatus.BAD_REQUEST,
                )
            finally:
                if mutation_started:
                    self.service.end_storage_mutation()
        elif path.startswith("/api/sample-library/") and path.endswith("/phenotype"):
            dataset_id = path.split("/")[3]
            phenotype = self.service.sample_library.phenotype(dataset_id)
            self._json({"phenotype": phenotype})
        elif path.startswith("/api/sample-library/"):
            dataset_id = path.removeprefix("/api/sample-library/")
            record = self.service.sample_library.get(dataset_id)
            self._json(
                record if record else {"error": "library dataset not found"},
                HTTPStatus.OK if record else HTTPStatus.NOT_FOUND,
            )
        elif path == "/api/screen-context/catalog":
            self._json(self.service.screen_context_catalog())
        elif path == "/api/cohort/samples":
            self._json({"samples": self.service.cohort.list_samples(
                query=(query.get("query") or [""])[0],
                limit=int((query.get("limit") or ["500"])[0]),
            )})
        elif path.startswith("/api/cohort/import-jobs/"):
            job_id = path.removeprefix("/api/cohort/import-jobs/")
            job = self.service.cohort.get_import_job(job_id)
            self._json(
                job if job else {"error": "cohort import job not found"},
                HTTPStatus.OK if job else HTTPStatus.NOT_FOUND,
            )
        elif path == "/api/phenotypes/stats":
            self._json(self.service.phenotypes.stats())
        elif path == "/api/phenotypes/profiles":
            self._json({"profiles": self.service.phenotypes.profiles()})
        elif path == "/api/phenotypes":
            self._json({"individuals": self.service.phenotypes.list(
                query=(query.get("query") or [""])[0],
                limit=int((query.get("limit") or ["500"])[0]),
            )})
        elif path.startswith("/api/phenotypes/by-sample/"):
            sample_id = unquote(path.removeprefix("/api/phenotypes/by-sample/"))
            self._json({"individuals": self.service.phenotypes.by_sample(sample_id)})
        elif path.startswith("/api/phenotypes/individual/"):
            individual_id = unquote(path.removeprefix("/api/phenotypes/individual/"))
            record = self.service.phenotypes.get(individual_id)
            self._json(
                record if record else {"error": "individual not found"},
                HTTPStatus.OK if record else HTTPStatus.NOT_FOUND,
            )
        elif path.startswith("/api/jobs/") and path.endswith("/log"):
            job_id = path.split("/")[3]
            try:
                self._json({"job_id": job_id, "log": self.service.log_tail(job_id)})
            except KeyError:
                self._json({"error": "job not found"}, HTTPStatus.NOT_FOUND)
        elif path.startswith("/api/jobs/") and path.endswith("/review-file"):
            job_id = path.split("/")[3]
            try:
                self._file(self.service.review_file(job_id))
            except KeyError:
                self._json({"error": "job not found"}, HTTPStatus.NOT_FOUND)
            except FileNotFoundError as exc:
                self._json({"error": str(exc)}, HTTPStatus.NOT_FOUND)
            except ValueError as exc:
                self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        elif path.startswith("/api/cohort/sample-review/"):
            # Projected stored-review VCFs are files on disk streamed here,
            # never strings inside the JSON that described them (audit M32).
            parts = path.split("/")
            try:
                if len(parts) != 6:
                    raise KeyError("review export not found")
                self._file(self.service.cohort.review_export_file(parts[4], parts[5]))
            except KeyError:
                self._json({"error": "review export not found"}, HTTPStatus.NOT_FOUND)
            except FileNotFoundError as exc:
                self._json({"error": str(exc)}, HTTPStatus.NOT_FOUND)
            except ValueError as exc:
                self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        elif path.startswith("/api/wgs-review/") and path.endswith("/file"):
            review_id = path.split("/")[3]
            try:
                self._file(self.service.wgs_review_file(review_id))
            except KeyError:
                self._json({"error": "WGS review file not found"}, HTTPStatus.NOT_FOUND)
            except FileNotFoundError as exc:
                self._json({"error": str(exc)}, HTTPStatus.NOT_FOUND)
            except ValueError as exc:
                self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        elif path.startswith("/api/wgs-review/"):
            job_id = path.removeprefix("/api/wgs-review/")
            job = self.service.get_wgs_review_job(job_id)
            self._json(
                job if job else {"error": "WGS review job not found"},
                HTTPStatus.OK if job else HTTPStatus.NOT_FOUND,
            )
        elif path.startswith("/api/jobs/"):
            job_id = path.split("/")[3]
            job = self.service.store.get(job_id)
            if job:
                job = self.service.with_job_progress(job)
            self._json(job if job else {"error": "job not found"},
                       HTTPStatus.OK if job else HTTPStatus.NOT_FOUND)
        else:
            self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def _reject_cross_site(self) -> bool:
        """Refuse mutating requests that did not come from this machine's UI.

        The service binds loopback, but that alone does not stop a malicious
        webpage: simple no-preflight POSTs (text/plain) reach 127.0.0.1 from
        any site the browser visits, and DNS rebinding defeats same-origin
        checks entirely unless the Host header is verified. Non-browser
        local clients (curl, the pipeline) send no Origin and pass.
        """
        host = (self.headers.get("Host") or "").strip()
        hostname = host[: host.rfind(":")] if host.count(":") == 1 else host
        if hostname.startswith("[") and "]" in hostname:
            hostname = hostname[: hostname.index("]") + 1]
        if hostname not in {"127.0.0.1", "localhost", "[::1]"}:
            self._json({"error": "request Host is not this workstation"},
                       HTTPStatus.FORBIDDEN)
            return True
        origin = self.headers.get("Origin")
        if origin and not self._LOOPBACK_ORIGIN.match(origin):
            self._json({"error": "cross-site requests are not accepted"},
                       HTTPStatus.FORBIDDEN)
            return True
        return False

    def do_POST(self) -> None:
        if self._reject_cross_site():
            return
        path = urlparse(self.path).path
        mutation_started = False
        try:
            # Migration takes a point-in-time verified copy. Do not permit a
            # concurrent endpoint to mutate SQLite or cache files after that
            # point; read-only GET endpoints remain available for progress.
            storage_mutation = (
                path in {
                    "/api/annotation-files/stage",
                    "/api/jobs",
                    "/api/wgs-review",
                    "/api/cohort/import",
                    "/api/sample-library/import",
                    "/api/storage/cleanup",
                    "/api/storage/location",
                    "/api/storage/migrate",
                    "/api/storage/compact",
                    "/api/cohort/import-jobs",
                    "/api/cohort/samples/remove",
                    "/api/phenotypes/import",
                    "/api/phenotypes/individual",
                    "/api/gene-knowledge/omim/download",
                    "/api/gene-knowledge/omim/install",
                    "/api/gene-knowledge/genia/install",
                    "/api/screen-context/install",
                    "/api/software-update/install",
                    "/api/software-update/rollback",
                }
                or path.startswith("/api/resource-downloads/")
                or path.startswith("/api/resource-preparations/")
                or path.startswith("/api/sample-library/")
                or path == "/api/bulk-intake"
                or path.startswith("/api/bulk-intake/")
                or path == "/api/spliceai-lookup"
                or (path.startswith("/api/jobs/") and path.endswith("/cancel"))
            )
            if storage_mutation and path != "/api/storage/migrate":
                self.service.begin_storage_mutation(
                    allow_pending_restart=path == "/api/storage/location"
                )
                mutation_started = True
            if path == "/api/annotation-files/stage":
                length = int(self.headers.get("Content-Length", "0"))
                self._json(
                    self.service.stage_file(
                        unquote(self.headers.get("X-File-Name", "")),
                        unquote(self.headers.get("X-Relative-Path", "")),
                        self.headers.get("X-Upload-Batch", ""),
                        length,
                        self.rfile,
                    ),
                    HTTPStatus.CREATED,
                )
                return
            if path == "/api/jobs":
                self._json(self.service.submit(self._body()), HTTPStatus.CREATED)
                return
            if path == "/api/wgs-review":
                self._json(
                    self.service.start_wgs_review(self._body()),
                    HTTPStatus.ACCEPTED,
                )
                return
            if path == "/api/spliceai-lookup":
                self._json(self.service.spliceai_lookup(self._body()))
                return
            if path == "/api/ccre-context":
                self._json(self.service.ccre_context(self._body()))
                return
            if path == "/api/screen-context":
                self._json(self.service.screen_context(self._body()))
                return
            if path == "/api/screen-context/filter":
                self._json(self.service.filter_screen_context(self._body()))
                return
            if path == "/api/screen-context/install":
                self._json(self.service.install_screen_context(self._body()))
                return
            if path.startswith("/api/resource-downloads/"):
                resource_id = unquote(
                    path.removeprefix("/api/resource-downloads/")
                )
                self._json(
                    self.service.start_resource_download(resource_id),
                    HTTPStatus.ACCEPTED,
                )
                return
            if path == "/api/local-resource-source/choose":
                self._json(self.service.choose_local_resource_source(self._body()))
                return
            if path == "/api/service/restart":
                result = self.service.request_service_restart()
                self._json(result, HTTPStatus.ACCEPTED)

                def _shutdown_after_response(server=self.server):
                    # Give the response a moment to flush before stopping
                    # serve_forever; in-flight handler threads still finish.
                    time.sleep(0.3)
                    server.shutdown()

                threading.Thread(target=_shutdown_after_response, daemon=True).start()
                return
            if path == "/api/service/quit":
                result = self.service.request_service_quit(self._body())
                def _quit_after_response(server=self.server):
                    time.sleep(0.3)
                    server.shutdown()
                try:
                    self._json(result, HTTPStatus.ACCEPTED)
                finally:
                    threading.Thread(target=_quit_after_response, daemon=True).start()
                return
            if path == "/api/annotation-engine/setup":
                self._json(self.service.start_engine_setup(self._body()), HTTPStatus.ACCEPTED)
                return
            if path == "/api/annotation-engine/cancel":
                self._json(self.service.cancel_engine_setup(self._body()), HTTPStatus.ACCEPTED)
                return
            if path == "/api/resource-preparations/promoterai":
                self._json(
                    self.service.start_promoterai_preparation(self._body()),
                    HTTPStatus.ACCEPTED,
                )
                return
            if path == "/api/resource-preparations/dbnsfp":
                self._json(
                    self.service.start_dbnsfp_download(self._body()),
                    HTTPStatus.ACCEPTED,
                )
                return
            if path == "/api/resource-preparations/logofunc":
                self._json(
                    self.service.start_logofunc_preparation(self._body()),
                    HTTPStatus.ACCEPTED,
                )
                return
            if path == "/api/resource-preparations/alphagenome_avi":
                self._json(self.service.start_avi_preparation(self._body()), HTTPStatus.ACCEPTED)
                return
            if path == "/api/resource-preparations/funcvep":
                self._json(
                    self.service.start_funcvep_preparation(self._body()),
                    HTTPStatus.ACCEPTED,
                )
                return
            if path == "/api/gene-knowledge/omim/download":
                self._json(
                    self.service.start_omim_download(self._body()),
                    HTTPStatus.ACCEPTED,
                )
                return
            if path == "/api/gene-knowledge/omim/install":
                body = self._body()
                source_dir = str(body.get("source_dir") or "").strip()
                if not source_dir:
                    raise ValueError("source_dir is required")
                self._json(
                    self.service.gene_knowledge.install_omim(Path(source_dir)),
                    HTTPStatus.CREATED,
                )
                return
            if path == "/api/gene-knowledge/genia/inspect":
                body = self._body()
                raw_paths = body.get("paths")
                if not isinstance(raw_paths, list) or not raw_paths:
                    raise ValueError("paths must contain one or more GenIA export files")
                paths = [self.service._selected_source_path(str(value)) for value in raw_paths]
                self._json(self.service.genia.inspect(paths))
                return
            if path == "/api/gene-knowledge/genia/install":
                body = self._body()
                raw_paths = body.get("paths")
                if not isinstance(raw_paths, list) or not raw_paths:
                    raise ValueError("paths must contain one or more GenIA export files")
                self.service._ensure_active_storage_available(require_annotation_root=True)
                paths = [self.service._selected_source_path(str(value)) for value in raw_paths]
                if len(paths) > len(COMPONENT_LABELS):
                    raise ValueError("select at most one file for each supported GenIA component")
                if not all(path.is_file() for path in paths):
                    raise ValueError("one or more selected GenIA files are unavailable")
                self._json(
                    self.service.gene_knowledge.install_genia(
                        paths,
                        replace_unreadable=body.get("replace_unreadable") is True,
                    ),
                    HTTPStatus.CREATED,
                )
                return
            if path == "/api/cohort/import":
                body = self._body()
                paths = body.get("paths")
                if not isinstance(paths, list):
                    raise ValueError("paths must be a list of VCF files or directories")
                self._json(self.service.cohort.import_paths(
                    paths,
                    recursive=bool(body.get("recursive", True)),
                    force=bool(body.get("force", False)),
                    allow_unknown_assembly=bool(
                        body.get("allow_unknown_assembly", False)
                    ),
                ))
                return
            if path == "/api/bulk-intake":
                self._json(self.service.start_bulk_intake(self._body()), HTTPStatus.CREATED)
                return
            if path.startswith("/api/bulk-intake/") and path.endswith("/cancel"):
                try:
                    self._json(self.service.cancel_bulk_intake(path.split("/")[3]))
                except KeyError:
                    self._json({"error": "bulk intake job not found"}, HTTPStatus.NOT_FOUND)
                return
            if path == "/api/sample-library/bulk":
                body = self._body(max_bytes=4_000_000)
                dataset_ids = body.get("dataset_ids")
                if not isinstance(dataset_ids, list):
                    raise ValueError("dataset_ids must be a list")
                self._json(self.service.sample_library.bulk_apply(
                    [str(value) for value in dataset_ids],
                    str(body.get("action") or ""),
                ))
                return
            if path == "/api/sample-library/review-record":
                body = self._body()
                try:
                    self._json(self.service.sample_library.original_review_record(
                        str(body.get("dataset_id") or ""),
                        str(body.get("variant_key") or ""),
                    ))
                except KeyError:
                    self._json(
                        {"error": "library dataset not found"},
                        HTTPStatus.NOT_FOUND,
                    )
                except FileNotFoundError as exc:
                    self._json({"error": str(exc)}, HTTPStatus.NOT_FOUND)
                except (OSError, RuntimeError, ValueError) as exc:
                    self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
                return
            if path == "/api/sample-library/review-file":
                body = self._body()
                dataset_ids = body.get("dataset_ids")
                if not isinstance(dataset_ids, list):
                    raise ValueError("dataset_ids must be a list")
                try:
                    self._file(
                        self.service.sample_library.review_file_combined(
                            [str(value) for value in dataset_ids]
                        )
                    )
                except KeyError:
                    self._json({"error": "library dataset not found"}, HTTPStatus.NOT_FOUND)
                except FileNotFoundError as exc:
                    self._json({"error": str(exc)}, HTTPStatus.NOT_FOUND)
                return
            if path == "/api/sample-library/import":
                self._json(
                    self.service.import_sample_library(self._body()),
                    HTTPStatus.CREATED,
                )
                return
            if path == "/api/sample-library/inspect":
                self._json(self.service.inspect_sample_library(self._body()))
                return
            if path.startswith("/api/sample-library/") and path.endswith("/identity"):
                dataset_id = path.split("/")[3]
                self._json(self.service.sample_library.map_identity(dataset_id, self._body()))
                return
            if path.startswith("/api/sample-library/") and path.endswith("/metadata"):
                dataset_id = path.split("/")[3]
                self._json(self.service.sample_library.update_metadata(dataset_id, self._body()))
                return
            if path.startswith("/api/sample-library/") and path.endswith("/reindex"):
                dataset_id = path.split("/")[3]
                body = self._body()
                self._json(self.service.sample_library.reindex(
                    dataset_id, full_wgs=bool(body.get("full_wgs", False))
                ))
                return
            if path.startswith("/api/sample-library/") and path.endswith("/activate-version"):
                dataset_id = path.split("/")[3]
                self._json(self.service.sample_library.activate_version(dataset_id))
                return
            if path.startswith("/api/sample-library/") and path.endswith("/cohort/remove"):
                dataset_id = path.split("/")[3]
                self._json(self.service.sample_library.exclude_from_cohort(dataset_id))
                return
            if path.startswith("/api/sample-library/") and path.endswith("/remove"):
                dataset_id = path.split("/")[3]
                body = self._body()
                self._json(self.service.sample_library.remove(
                    dataset_id,
                    remove_managed_file=bool(body.get("remove_managed_file", True)),
                ))
                return
            if path == "/api/storage/cleanup":
                body = self._body()
                self._json(self.service.cleanup_storage(body.get("categories") or []))
                return
            if path == "/api/storage/test-location":
                self._json(self.service.test_storage_location(self._body()))
                return
            if path == "/api/storage/location":
                self._json(self.service.set_storage_location(self._body()))
                return
            if path == "/api/storage/migrate":
                self._json(self.service.start_storage_migration(self._body()), HTTPStatus.ACCEPTED)
                return
            if path == "/api/storage/open":
                self._json(self.service.open_storage_location(self._body()))
                return
            if path == "/api/storage/compact":
                body = self._body()
                if body.get("confirmation") != "COMPACT":
                    raise ValueError("confirmation must be COMPACT")
                self._json(self.service.compact_storage())
                return
            if path == "/api/cohort/import-jobs":
                self._json(
                    self.service.start_cohort_import(self._body()),
                    HTTPStatus.ACCEPTED,
                )
                return
            if path == "/api/cohort/samples/remove":
                body = self._body()
                self._json(self.service.remove_cohort_samples(
                    body.get("sample_ids")
                ))
                return
            if path == "/api/cohort/query":
                self._json(self.service.cohort.query(self._body()))
                return
            if path == "/api/cohort/variant-detail":
                body = self._body()
                self._json(self.service.cohort.variant_detail(
                    body.get("variant_key")
                ))
                return
            if path == "/api/cohort/review-records":
                body = self._body()
                self._json(self.service.cohort.review_records(
                    body.get("selections")
                ))
                return
            if path == "/api/cohort/sample-review":
                body = self._body()
                self._json(self.service.cohort.sample_review_files(
                    body.get("sample_ids")
                ))
                return
            if path == "/api/phenotypes/preview":
                self._json(self.service.phenotypes.preview(
                    self._body(max_bytes=30_000_000)
                ))
                return
            if path == "/api/phenotypes/validate":
                self._json(self.service.phenotypes.validate(
                    self._body(max_bytes=30_000_000)
                ))
                return
            if path == "/api/phenotypes/import":
                self._json(self.service.phenotypes.import_records(
                    self._body(max_bytes=30_000_000)
                ))
                return
            if path == "/api/phenotypes/individual":
                self._json(
                    self.service.phenotypes.save_individual(self._body()),
                    HTTPStatus.CREATED,
                )
                return
            if path.startswith("/api/jobs/") and path.endswith("/cancel"):
                job_id = path.split("/")[3]
                self._json(self.service.cancel(job_id))
                return
            if path == "/api/software-update/check":
                # A POST so no-cors cross-site GETs (an <img> tag on any
                # web page) can never trigger the outbound release lookup:
                # it must stay strictly click-driven, as documented.
                self._json(self.service.software_updater.check())
                return
            if path == "/api/software-update/install":
                self._json(self.service.software_update_install())
                return
            if path == "/api/software-update/rollback":
                self._json(self.service.software_update_rollback())
                return
            self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        except CohortMergeBusyError as exc:
            self._json({"error": str(exc), "kind": "cohort_merge_busy"}, HTTPStatus.SERVICE_UNAVAILABLE)
        except NotFoundError as exc:
            self._json({"error": str(exc)}, HTTPStatus.NOT_FOUND)
        except KeyError as exc:
            # A bare KeyError here is a request payload missing a field
            # (payload["x"]), not a missing resource: 400, naming the field.
            field = str(exc.args[0]) if exc.args else "?"
            self._json(
                {"error": f"missing required field: {field}"},
                HTTPStatus.BAD_REQUEST,
            )
        except (ValueError, json.JSONDecodeError) as exc:
            self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except sqlite3.OperationalError as exc:
            # The cohort merge now commits in bounded chunks (audit M27), so
            # a writer normally waits a fraction of a second. If the lock
            # still times out while an import is finishing, say so plainly
            # instead of reporting an internal error.
            if "locked" in str(exc).lower() and self.service.cohort.has_active_import():
                self._json(
                    {
                        "error": (
                            "An import is finishing (merging into the cohort index); "
                            "editing is temporarily unavailable — retry in a moment."
                        ),
                        "kind": "import_finishing",
                    },
                    HTTPStatus.CONFLICT,
                )
            else:
                self._internal_error(exc)
        except Exception as exc:  # noqa: BLE001 - HTTP boundary (audit M23)
            self._internal_error(exc)
        finally:
            if mutation_started:
                self.service.end_storage_mutation()

    def _body(self, max_bytes: int = 1_000_000) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length < 0:
            raise ValueError("Content-Length must be nonnegative")
        if length > max_bytes:
            raise ValueError("request body is too large")
        raw = self.rfile.read(length)
        value = json.loads(raw or b"{}")
        if not isinstance(value, dict):
            raise ValueError("JSON body must be an object")
        return value

    def _json(self, value, status: HTTPStatus = HTTPStatus.OK) -> None:
        payload = json.dumps(value).encode("utf-8")
        self.send_response(status)
        self._cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _file(self, path: Path) -> None:
        content_type = (
            "application/gzip"
            if path.name.lower().endswith(".gz")
            else "text/plain; charset=utf-8"
        )
        self.send_response(HTTPStatus.OK)
        self._cors_headers()
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(path.stat().st_size))
        # HTTP headers are Latin-1, but workstation filenames may be UTF-8.
        # Keep the legacy fallback ASCII and carry the exact name in RFC 5987
        # form; encoding also prevents filename control characters becoming
        # response headers.
        encoded_name = quote(path.name, safe="")
        self.send_header("Content-Disposition", (
            f'attachment; filename="{encoded_name}"; filename*=UTF-8\'\'{encoded_name}'
        ))
        self.end_headers()
        with path.open("rb") as handle:
            shutil.copyfileobj(handle, self.wfile, length=1024 * 1024)

    # Loopback origins on any port: the UI's port is user-configurable
    # (IEI_UI_PORT), and a loopback page is the only legitimate caller.
    _LOOPBACK_ORIGIN = re.compile(
        r"^https?://(127\.0\.0\.1|localhost|\[::1\])(:\d+)?$"
    )

    def _cors_headers(self) -> None:
        origin = self.headers.get("Origin")
        if origin and self._LOOPBACK_ORIGIN.match(origin):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Expose-Headers", "Content-Disposition")
        self.send_header(
            "Access-Control-Allow-Headers",
            "Content-Type, X-File-Name, X-Relative-Path, X-Upload-Batch",
        )

    def log_message(self, fmt: str, *args) -> None:
        print(f"[local-service] {self.address_string()} {fmt % args}")


class LoopbackHTTPServer(ThreadingHTTPServer):
    """HTTP server that avoids a slow reverse-DNS lookup during local startup."""

    def server_bind(self) -> None:
        socketserver.TCPServer.server_bind(self)
        self.server_name = str(self.server_address[0])
        self.server_port = int(self.server_address[1])


def create_server(
    service: AnnotationJobService, host: str = "127.0.0.1", port: int = 43117,
    web_root: Path | None = None,
) -> ThreadingHTTPServer:
    handler = type(
        "ConfiguredWorkbenchRequestHandler",
        (WorkbenchRequestHandler,),
        {"service": service},
    )
    server = LoopbackHTTPServer((host, port), handler)
    server.web_root = web_root.resolve() if web_root else None
    return server


def _install_shutdown_signal_handlers(server) -> None:
    """Turn SIGTERM/SIGHUP/SIGINT into an orderly shutdown.

    Under the launcher the service is a background child of a background
    subshell: bash hands it SIGINT as SIG_IGN, so CPython never installs
    its KeyboardInterrupt handler, and the supervisor's `kill` (SIGTERM) or
    a closed terminal (SIGHUP) ended the process immediately — shutdown()
    never ran and running pipelines were orphaned (audit H6). The handler
    stops the serve loop from another thread (shutdown() would deadlock if
    called on the serving thread); main() then runs service.shutdown().
    """
    def handle(signum, _frame):
        print(f"received signal {signum}; stopping the local service")
        threading.Thread(target=server.shutdown, name="signal-shutdown", daemon=True).start()

    for name in ("SIGTERM", "SIGHUP", "SIGINT"):
        signum = getattr(signal, name, None)
        if signum is None:
            continue
        try:
            signal.signal(signum, handle)
        except (ValueError, OSError):  # not the main thread / unsupported
            pass


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=43117, type=int)
    parser.add_argument("--state-dir", type=Path)
    parser.add_argument("--storage-registry", type=Path)
    parser.add_argument("--web-root", type=Path, help="Serve the packaged static interface on the API port")
    parser.add_argument(
        "--pipeline-root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    args = parser.parse_args()
    if platform.system() == "Darwin":
        os.environ["PATH"] = mac_tool_path()
        # Process-local selection is inherited by status probes, dataset jobs
        # and annotation children, without changing the user's Docker context.
        os.environ.update(managed_colima_environment(dict(os.environ)))
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        parser.error("this workstation service may bind only to a loopback address")

    pipeline_root = args.pipeline_root.resolve()
    default_data_root = default_state_dir()
    try:
        registry = StorageLocationRegistry(
            pipeline_root,
            default_data_root,
            registry_path=args.storage_registry,
            persist=True,
        )
    except StorageRegistryError as exc:
        parser.error(str(exc))
    environment_override = os.environ.get("IEI_WORKBENCH_STATE_DIR", "").strip()
    state_dir = (
        args.state_dir.expanduser().resolve()
        if args.state_dir else
        Path(environment_override).expanduser().resolve()
        if environment_override else registry.root("data")
    )
    try:
        state_exists = state_dir.exists()
        state_is_directory = state_dir.is_dir()
    except OSError as exc:
        parser.error(
            f"configured Sample Library & Cohort storage cannot be inspected: {state_dir} ({exc})"
        )
    if not state_exists:
        if not registry.is_default("data") and not args.state_dir and not environment_override:
            parser.error(
                "configured Sample Library & Cohort storage is unavailable: "
                f"{state_dir}. Reconnect the selected drive; the service will not create a fallback database."
            )
        try:
            state_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            parser.error(f"state directory cannot be created: {state_dir} ({exc})")
        state_is_directory = True
    if not state_is_directory:
        parser.error(f"state directory is not a folder: {state_dir}")
    if not args.state_dir and not environment_override and not registry.is_default("data"):
        try:
            registry.ensure_marker_identity(
                "data", state_dir, required=True, service=SERVICE_VERSION
            )
        except StorageRegistryError as exc:
            parser.error(str(exc))
    workspace_dir = (
        state_dir if registry.describe("temporary").get("follows_data_root")
        else registry.root("temporary")
    )
    try:
        workspace_exists = workspace_dir.exists()
        workspace_is_directory = workspace_dir.is_dir()
    except OSError as exc:
        parser.error(
            f"configured temporary workspace cannot be inspected: {workspace_dir} ({exc})"
        )
    if not workspace_exists:
        if not registry.is_default("temporary"):
            parser.error(
                "configured temporary workspace is unavailable: "
                f"{workspace_dir}. Reconnect the selected drive; the service will not fall back."
            )
        try:
            workspace_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            parser.error(f"temporary workspace cannot be created: {workspace_dir} ({exc})")
        workspace_is_directory = True
    if not workspace_is_directory:
        parser.error(f"temporary workspace is not a folder: {workspace_dir}")
    if not registry.is_default("temporary"):
        try:
            registry.ensure_marker_identity(
                "temporary", workspace_dir, required=True, service=SERVICE_VERSION
            )
        except StorageRegistryError as exc:
            parser.error(str(exc))
    annotation_root = registry.root("annotation")
    if not registry.is_default("annotation") and annotation_root.is_dir():
        try:
            registry.ensure_marker_identity(
                "annotation", annotation_root,
                required=bool(registry.storage_id("annotation")),
                service=SERVICE_VERSION,
            )
        except StorageRegistryError as exc:
            parser.error(str(exc))

    try:
        service = AnnotationJobService(
            pipeline_root, state_dir, storage_registry=registry, auto_start_docker=True
        )
    except ServiceAlreadyRunningError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(EXIT_ALREADY_RUNNING)
    try:
        server = create_server(service, args.host, args.port, web_root=args.web_root)
    except OSError as exc:
        # Bind failure after the lock was acquired: another process (not a
        # GUIDE-IEI service, or one on a different state directory) holds
        # the port. Release our state cleanly instead of crash-looping.
        service.shutdown()
        print(f"ERROR: cannot listen on http://{args.host}:{args.port}: {exc}", file=sys.stderr)
        raise SystemExit(EXIT_ALREADY_RUNNING)
    print(f"IEI local service listening on http://{args.host}:{args.port}")
    print(f"State: {service.state_dir}")
    _install_shutdown_signal_handlers(server)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        service.shutdown()
    if service.restart_requested:
        print("Restart requested from the app; exiting for the supervisor to relaunch.")
        raise SystemExit(RESTART_EXIT_CODE)


if __name__ == "__main__":
    main()
