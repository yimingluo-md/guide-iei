#!/usr/bin/env python3
"""Download metadata and prepare categorical SCREEN Registry V4 annotations.

This intentionally does not retain assay Z-scores.  It prepares two layers:

* organ/tissue aggregate cCRE classes (excluding cancer cell lines), and
* individual immune/hematopoietic biosample classes selected with ENCODE
  ontology metadata.

The final matrices contain one unsigned byte per cCRE/biosample.  Zero means
Low-DNase/inactive; positive values are stable class codes documented in the
manifest and SQLite catalog.  Evidence completeness is stored per biosample so
partial SCREEN classifications are never presented as complete calls.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import gzip
import hashlib
import json
import platform
import re
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
import time
import urllib.parse
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

try:
    from .cell_ontology import (
        CELL_ONTOLOGY_RELEASE,
        CELL_ONTOLOGY_SOURCE_URL,
        ontology_ancestors,
        parse_cell_ontology,
    )
except ImportError:  # Direct `python pipeline/screen_ccre_dataset.py` use.
    from cell_ontology import (  # type: ignore
        CELL_ONTOLOGY_RELEASE,
        CELL_ONTOLOGY_SOURCE_URL,
        ontology_ancestors,
        parse_cell_ontology,
    )


REGISTRY = "SCREEN Registry V4"
ASSEMBLY = "GRCh38"
ASSAYS = ("DNase", "H3K4me3", "H3K27ac", "CTCF")
BASE = "https://downloads.wenglab.org"
CCRE_URL = f"{BASE}/Registry-V4/GRCh38-cCREs.bed"
EXPERIMENT_LISTS_URL = (
    "https://users.moore-lab.org/ENCODE-cCREs/Pipeline-Input-Files/"
    "hg38-Experiment-Lists.tar.gz"
)

# Official SCREEN2.0 ontology download map, Registry V4 (2026-08-01).
# Four additional entries displayed by the SCREEN downloads page returned 404
# when independently checked on 2026-08-01.  They are recorded separately
# below and must not be represented as all-zero/inactive columns.
TISSUE_FILES = {
    "adipose": "adipose.noccl.cCREs.bed",
    "adrenal gland": "adrenal_gland.noccl.cCREs.bed",
    "blood": "blood.noccl.cCREs.bed",
    "blood vessel": "blood_vessel.noccl.cCREs.bed",
    "bone": "bone.noccl.cCREs.bed",
    "bone marrow": "bone_marrow.noccl.cCREs.bed",
    "brain": "brain.noccl.cCREs.bed",
    "breast": "breast.noccl.cCREs.bed",
    "connective tissue": "connective_tissue.noccl.cCREs.bed",
    "embryo": "embryo.noccl.cCREs.bed",
    "epithelium": "epithelium.noccl.cCREs.bed",
    "esophagus": "esophagus.noccl.cCREs.bed",
    "eye": "eye.noccl.cCREs.bed",
    "heart": "heart.noccl.cCREs.bed",
    "kidney": "kidney.noccl.cCREs.bed",
    "large intestine": "large_intestine.noccl.cCREs.bed",
    "limb": "limb.noccl.cCREs.bed",
    "liver": "liver.noccl.cCREs.bed",
    "lung": "lung.noccl.cCREs.bed",
    "lymphoid tissue": "lymphoid_tissue.noccl.cCREs.bed",
    "mouth": "mouth.noccl.cCREs.bed",
    "muscle": "muscle.noccl.cCREs.bed",
    "nerve": "nerve.noccl.cCREs.bed",
    "ovary": "ovary.noccl.cCREs.bed",
    "pancreas": "pancreas.noccl.cCREs.bed",
    "penis": "penis.noccl.cCREs.bed",
    "placenta": "placenta.noccl.cCREs.bed",
    "prostate": "prostate.noccl.cCREs.bed",
    "skin": "skin.noccl.cCREs.bed",
    "small intestine": "small_intestine.noccl.cCREs.bed",
    "spinal cord": "spinal_cord.noccl.cCREs.bed",
    "spleen": "spleen.noccl.cCREs.bed",
    "stomach": "stomach.noccl.cCREs.bed",
    "testis": "testis.noccl.cCREs.bed",
    "thymus": "thymus.noccl.cCREs.bed",
    "thyroid": "thyroid.noccl.cCREs.bed",
    "uterus": "uterus.noccl.cCREs.bed",
    "vagina": "vagina.noccl.cCREs.bed",
}

UNAVAILABLE_TISSUE_FILES = {
    "gallbladder": "gallbladder.noccl.cCREs.bed",
    "nose": "nose.noccl.cCREs.bed",
    "parathyroid gland": "parathyroid_gland.noccl.cCREs.bed",
    "urinary bladder": "urinary_bladder.noccl.cCREs.bed",
}
UNAVAILABLE_TISSUE_PUBLISHED_ALTERNATES = {
    # SCREEN's page publishes the misspelled filename below; both it and the
    # corrected filename were tested so future refreshes remain auditable.
    "parathyroid gland": "paraythroid_gland.noccl.cCREs.bed",
}

CLASS_CODES = {
    "PLS": 1,
    "pELS": 2,
    "dELS": 3,
    "CA-H3K4me3": 4,
    "CA-CTCF": 5,
    "CA-TF": 6,
    "CA-only": 7,
    "CA": 7,
    "TF-only": 8,
    "TF": 8,
}
CLASS_LABELS = {
    0: ("inactive", "Low DNase / not active in this aggregate or biosample"),
    1: ("PLS", "Promoter-like signature"),
    2: ("pELS", "Proximal enhancer-like signature"),
    3: ("dELS", "Distal enhancer-like signature"),
    4: ("CA-H3K4me3", "Chromatin accessibility with H3K4me3"),
    5: ("CA-CTCF", "Chromatin accessibility with CTCF"),
    6: ("CA-TF", "Chromatin accessibility with transcription-factor binding"),
    7: ("CA", "Chromatin accessibility without a more specific signature"),
    8: ("TF", "Transcription-factor binding without chromatin accessibility"),
}
FULL_STATUS = "All-data/Full-classification"
PARTIAL_STATUS = "Missing-data/Partial-classification"
AGGREGATE_STATUS = "Aggregated-Samples"
LINEAGE_ONTOLOGY_ROOTS = (
    ("T cell", {"CL:0000084"}),
    ("B cell", {"CL:0000236"}),
    ("NK/ILC", {"CL:0000623", "CL:0001065"}),
    ("Dendritic cell", {"CL:0000451"}),
    ("Monocyte/macrophage", {"CL:0000576", "CL:0000235"}),
    ("Granulocyte", {"CL:0000094"}),
    ("Mast cell", {"CL:0000097"}),
    ("Hematopoietic progenitor", {"CL:0008001"}),
    ("Erythroid/megakaryocyte", {"CL:0000764", "CL:0000763", "CL:0000233"}),
)
LINEAGE_FALLBACK_PATTERNS = (
    ("T cell", (r"\bt[- ]cell\b", r"\bthymocyte\b")),
    ("B cell", (r"\bb[- ]cell\b", r"\bplasma cell\b")),
    ("NK/ILC", (r"\bnatural killer\b", r"\binnate lymphoid\b", r"\bnk(?: cell)?\b")),
    ("Dendritic cell", (r"\bdendritic\b",)),
    ("Monocyte/macrophage", (r"\bmonocyte\b", r"\bmacrophage\b")),
    ("Granulocyte", (r"\bneutrophil\b", r"\beosinophil\b", r"\bbasophil\b", r"\bgranulocyte\b")),
    ("Mast cell", (r"\bmast cell\b",)),
    ("Hematopoietic progenitor", (r"\bprogenitor\b", r"\bstem cell\b")),
    ("Erythroid/megakaryocyte", (r"\berythro\w*", r"\bmegakaryo\w*", r"\bplatelet\b")),
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def run_curl(url: str, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    partial = output.with_name(output.name + ".part")
    command = [
        "curl", "-fsSL", "--retry", "6", "--retry-all-errors",
        "--retry-delay", "2", "--connect-timeout", "30",
        "--speed-limit", "10240", "--speed-time", "30",
        "-C", "-", "-o", str(partial), url,
    ]
    subprocess.run(command, check=True)
    partial.replace(output)


def read_experiment_lists(path: Path) -> dict[str, dict[str, dict[str, str]]]:
    result: dict[str, dict[str, dict[str, str]]] = {}
    with tarfile.open(path, "r:gz") as archive:
        members = {Path(member.name).name: member for member in archive.getmembers()}
        # SCREEN's downloadable biosample-specific cCRE classifications are
        # constructed from these four signal files. ATAC is displayed
        # separately by SCREEN and can contain repeated profiles per name, but
        # is not part of the categorical BED filename or classifier here.
        for assay in ASSAYS:
            member = members.get(f"{assay}-List.txt")
            if member is None:
                raise ValueError(f"experiment archive lacks {assay}-List.txt")
            extracted = archive.extractfile(member)
            if extracted is None:
                raise ValueError(f"could not read {member.name}")
            by_name: dict[str, dict[str, str]] = {}
            for number, raw in enumerate(extracted, 1):
                fields = raw.decode("utf-8").rstrip("\n").split("\t")
                if len(fields) != 3 or not all(fields):
                    raise ValueError(f"invalid {assay} list row {number}")
                experiment, signal_file, name = fields
                if name in by_name:
                    raise ValueError(f"duplicate {assay} biosample name: {name}")
                by_name[name] = {
                    "experiment_accession": experiment,
                    "signal_file_accession": signal_file,
                }
            result[assay] = by_name
    return result


def query_encode_direct(accession: str) -> dict[str, Any]:
    completed = subprocess.run(
        [
            "curl", "-fsSL", "--connect-timeout", "30", "--max-time", "180",
            "--retry", "5", "--retry-all-errors", "--retry-delay", "2",
            f"https://www.encodeproject.org/experiments/{accession}/?format=json",
        ],
        check=True,
        capture_output=True,
    )
    row = json.loads(completed.stdout)
    # Retired accessions redirect to a replacement. Registry V4 still uses the
    # retired accession, so keep it as the metadata lookup key.
    row["accession"] = accession
    return row


def query_encode_chunk(experiments: list[str]) -> list[dict[str, Any]]:
    parameters: list[tuple[str, str]] = [("type", "Experiment")]
    parameters.extend(("accession", accession) for accession in experiments)
    for field in (
        "accession", "biosample_summary", "biosample_ontology",
        "replicates.library.biosample.life_stage",
        "replicates.library.biosample.sex",
        "replicates.library.biosample.disease_term_name",
        "replicates.library.biosample.treatments.treatment_term_name",
    ):
        parameters.append(("field", field))
    parameters.extend((("format", "json"), ("limit", "all")))
    url = "https://www.encodeproject.org/search/?" + urllib.parse.urlencode(parameters)
    for attempt in range(1, 7):
        try:
            completed = subprocess.run(
                [
                    "curl", "-fsSL", "--connect-timeout", "30",
                    "--max-time", "240", "--retry", "2", url,
                ],
                check=True,
                capture_output=True,
            )
            payload = json.loads(completed.stdout)
            rows = payload.get("@graph", [])
            received = {row.get("accession") for row in rows}
            missing = sorted(set(experiments) - received)
            for accession in missing:
                try:
                    rows.append(query_encode_direct(accession))
                except Exception:
                    # Preserve the accession and explicitly empty metadata. It
                    # cannot pass the ontology-based immune selection and is
                    # therefore excluded without guessing from its name — but
                    # the failure is tagged so it is retried on the next run
                    # instead of being cached as permanently complete.
                    rows.append({
                        "accession": accession,
                        "replicates": [],
                        "iei_metadata_fetch_failed": True,
                    })
            return rows
        except Exception:
            if attempt == 6:
                raise
            time.sleep(2**attempt)
    raise AssertionError("unreachable")


def unique_nested(rows: Iterable[dict[str, Any]], path: tuple[str, ...]) -> list[str]:
    values: set[str] = set()
    for row in rows:
        current: Any = row
        for key in path:
            if not isinstance(current, dict):
                current = None
                break
            current = current.get(key)
        if isinstance(current, str) and current:
            values.add(current)
    return sorted(values)


def flattened_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, list):
        result: list[str] = []
        for item in value:
            result.extend(flattened_strings(item))
        return result
    return []


def compact_encode_metadata(row: dict[str, Any]) -> dict[str, Any]:
    ontology = row.get("biosample_ontology") or {}
    replicates = row.get("replicates") or []
    biosamples = [
        replicate.get("library", {}).get("biosample", {})
        for replicate in replicates
        if isinstance(replicate, dict)
    ]
    treatments: set[str] = set()
    diseases: set[str] = set()
    for biosample in biosamples:
        for treatment in biosample.get("treatments") or []:
            for value in flattened_strings(treatment.get("treatment_term_name")):
                treatments.add(value)
        for disease in flattened_strings(biosample.get("disease_term_name")):
            diseases.add(disease)
    return {
        "experiment_accession": row.get("accession"),
        "metadata_status": (
            "fetch_failed" if row.get("iei_metadata_fetch_failed") else "ok"
        ),
        "display_name": row.get("biosample_summary") or "",
        "ontology_id": ontology.get("term_id") or "",
        "ontology_name": ontology.get("term_name") or "",
        "sample_type": ontology.get("classification") or "",
        "organ_slims": sorted(ontology.get("organ_slims") or []),
        "system_slims": sorted(ontology.get("system_slims") or []),
        "cell_slims": sorted(ontology.get("cell_slims") or []),
        "life_stages": unique_nested(biosamples, ("life_stage",)),
        "sexes": unique_nested(biosamples, ("sex",)),
        "treatments": sorted(treatments),
        "diseases": sorted(diseases),
    }


def immune_relevant(metadata: dict[str, Any]) -> bool:
    cells = {value.lower() for value in metadata["cell_slims"]}
    systems = {value.lower() for value in metadata["system_slims"]}
    organs = {value.lower() for value in metadata["organ_slims"]}
    classification = metadata["sample_type"].lower()
    if {"hematopoietic cell", "leukocyte"} & cells:
        return True
    if {"immune system", "hematopoietic system"} & systems:
        return True
    immune_organs = {
        "blood", "bone marrow", "spleen", "thymus", "lymph node",
        "lymphoid tissue",
    }
    return classification == "tissue" and bool(immune_organs & organs)


def lineage_assignment(
    metadata: dict[str, Any],
    ancestors_by_term: dict[str, set[str]] | None = None,
) -> tuple[str, str]:
    """Assign a broad lineage from CL ancestry, with a safe label fallback."""
    ontology_id = str(metadata.get("ontology_id") or "")
    if ontology_id.startswith("CL:") and ancestors_by_term is not None \
            and ontology_id in ancestors_by_term:
        term_and_ancestors = ancestors_by_term[ontology_id] | {ontology_id}
        for label, roots in LINEAGE_ONTOLOGY_ROOTS:
            if term_and_ancestors & roots:
                return label, "cell_ontology_ancestry"
        default = (
            "Mixed immune tissue"
            if str(metadata.get("sample_type") or "").casefold() == "tissue"
            else "Other hematopoietic/immune"
        )
        return default, "cell_ontology_ancestry_unmapped"

    terms = " ".join([
        str(metadata.get("ontology_name") or ""),
        *(str(value) for value in metadata.get("cell_slims") or []),
    ]).casefold()
    for label, patterns in LINEAGE_FALLBACK_PATTERNS:
        if any(re.search(pattern, terms) for pattern in patterns):
            return label, "boundary_safe_ontology_label_fallback"
    default = (
        "Mixed immune tissue"
        if str(metadata.get("sample_type") or "").casefold() == "tissue"
        else "Other hematopoietic/immune"
    )
    return default, "boundary_safe_ontology_label_fallback_unmapped"


def lineage_for(
    metadata: dict[str, Any],
    ancestors_by_term: dict[str, set[str]] | None = None,
) -> str:
    return lineage_assignment(metadata, ancestors_by_term)[0]


def count_ccres(path: Path) -> tuple[int, str]:
    count = 0
    current_chrom = ""
    previous_start = -1
    completed_chroms: set[str] = set()
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for number, line in enumerate(handle, 1):
            digest.update(line)
            fields = line.rstrip(b"\n").split(b"\t")
            if len(fields) != 6:
                raise ValueError(f"unexpected cCRE BED schema on row {number}")
            chrom = fields[0].decode()
            start = int(fields[1])
            if start < 0 or int(fields[2]) <= start:
                raise ValueError(f"invalid cCRE interval on row {number}")
            if chrom != current_chrom:
                if chrom in completed_chroms:
                    raise ValueError(f"cCRE BED contig repeats on row {number}: {chrom}")
                if current_chrom:
                    completed_chroms.add(current_chrom)
                current_chrom = chrom
                previous_start = -1
            if start < previous_start:
                raise ValueError(f"cCRE BED is not sorted within {chrom} on row {number}")
            previous_start = start
            count += 1
    return count, digest.hexdigest()


def build_manifest(args: argparse.Namespace) -> None:
    experiment_lists = read_experiment_lists(args.experiment_lists)
    ccre_count, ccre_sha = count_ccres(args.ccre_bed)
    cell_ontology = parse_cell_ontology(args.cell_ontology)
    cell_ancestors = ontology_ancestors(cell_ontology)
    names = list(experiment_lists["DNase"])
    experiment_to_name = {
        experiment_lists["DNase"][name]["experiment_accession"]: name
        for name in names
    }
    metadata_cache = args.metadata_cache
    if metadata_cache.exists():
        cached = json.loads(metadata_cache.read_text())
        by_experiment = cached["experiments"]
    else:
        by_experiment: dict[str, dict[str, Any]] = {}
    # Rows whose metadata fetch failed on a previous run must be retried, not
    # treated as covered: a transient network failure was previously cached
    # forever and the biosample silently vanished from the immune matrix.
    covered = {
        accession
        for accession, row in by_experiment.items()
        if row.get("metadata_status", "ok") == "ok"
    }
    pending = sorted(set(experiment_to_name) - covered)
    if pending:
        chunks = (
            [[accession] for accession in pending]
            if len(pending) <= 10
            else [pending[i:i + 75] for i in range(0, len(pending), 75)]
        )
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.metadata_workers) as pool:
            futures = [
                pool.submit(
                    (lambda accession: [query_encode_direct(accession)]), chunk[0]
                ) if len(pending) <= 10 else pool.submit(query_encode_chunk, chunk)
                for chunk in chunks
            ]
            completed_count = 0
            for future in concurrent.futures.as_completed(futures):
                for row in future.result():
                    compact = compact_encode_metadata(row)
                    by_experiment[compact["experiment_accession"]] = compact
                completed_count += 1
                print(f"ENCODE metadata batches: {completed_count}/{len(chunks)}", flush=True)
                atomic_json(metadata_cache, {
                    "source": "ENCODE REST search API",
                    "retrieved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "complete": False,
                    "experiments": by_experiment,
                })
        if not set(experiment_to_name).issubset(by_experiment):
            raise RuntimeError("ENCODE metadata cache does not cover every DNase biosample")
        by_experiment = {
            accession: by_experiment[accession]
            for accession in experiment_to_name
        }
        fetch_failed = sorted(
            accession
            for accession, row in by_experiment.items()
            if row.get("metadata_status", "ok") != "ok"
        )
        if fetch_failed:
            print(
                f"WARN  ENCODE metadata could not be fetched for "
                f"{len(fetch_failed)} biosample(s); they are excluded from "
                "the immune selection this run and will be retried next run: "
                + ", ".join(fetch_failed[:5]),
                flush=True,
            )
        atomic_json(metadata_cache, {
            "source": "ENCODE REST search API",
            "retrieved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            # A cache holding fetch-failed rows is NOT complete; stamping it
            # complete made a transient failure a permanent silent exclusion.
            "complete": not fetch_failed,
            "metadata_fetch_failed": fetch_failed,
            "experiments": by_experiment,
        })

    immune: list[dict[str, Any]] = []
    for name in names:
        dnase = experiment_lists["DNase"][name]
        metadata = by_experiment.get(dnase["experiment_accession"])
        if metadata is None or not immune_relevant(metadata):
            continue
        assay_data = {
            assay: experiment_lists[assay].get(name)
            for assay in ASSAYS
        }
        signal_ids = [
            assay_data[assay]["signal_file_accession"]
            for assay in ASSAYS if assay_data[assay] is not None
        ]
        available = [assay for assay in ASSAYS if assay_data[assay] is not None]
        if set(available) == set(ASSAYS):
            tier = "full_classification"
        elif available == ["DNase"]:
            tier = "accessibility_only"
        else:
            tier = "partial_classification"
        donor_match = re.search(r"(ENCDO[A-Z0-9]+)$", name)
        lineage, lineage_method = lineage_assignment(metadata, cell_ancestors)
        immune.append({
            "index": len(immune),
            "screen_name": name,
            "donor_accession": donor_match.group(1) if donor_match else "",
            "metadata": metadata,
            "lineage": lineage,
            "lineage_assignment_method": lineage_method,
            "assays_available": available,
            "evidence_tier": tier,
            "assays": assay_data,
            "source_filename": "_".join(signal_ids) + ".bigBed",
            "source_url": f"{BASE}/Registry-V4/{'_'.join(signal_ids)}.bigBed",
        })

    tissues = [
        {
            "index": index,
            "name": name,
            "source_filename": filename,
            "source_url": f"{BASE}/{filename}",
        }
        for index, (name, filename) in enumerate(TISSUE_FILES.items())
    ]
    manifest = {
        "schema_version": 1,
        "registry": REGISTRY,
        "assembly": ASSEMBLY,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": {
            "ccre_url": CCRE_URL,
            "ccre_path": str(args.ccre_bed.resolve()),
            "ccre_sha256": ccre_sha,
            "ccre_count": ccre_count,
            "experiment_lists_url": EXPERIMENT_LISTS_URL,
            "experiment_lists_path": str(args.experiment_lists.resolve()),
            "experiment_lists_sha256": sha256_file(args.experiment_lists),
            "encode_metadata_path": str(metadata_cache.resolve()),
            "cell_ontology_path": str(args.cell_ontology.resolve()),
            "cell_ontology_sha256": sha256_file(args.cell_ontology),
            "cell_ontology_release": CELL_ONTOLOGY_RELEASE,
            "cell_ontology_source_url": CELL_ONTOLOGY_SOURCE_URL,
            "cell_ontology_data_version": cell_ontology["header"].get("data-version", ""),
            "selection": (
                "DNase-profiled biosamples with ENCODE ontology metadata indicating "
                "hematopoietic/leukocyte, immune/hematopoietic system, or an immune tissue"
            ),
        },
        "class_codes": {
            str(code): {"label": label, "description": description}
            for code, (label, description) in CLASS_LABELS.items()
        },
        "tissues": tissues,
        "tissues_unavailable": [
            {
                "name": name,
                "source_filename": filename,
                "source_url": f"{BASE}/{filename}",
                "alternate_source_urls_tested": [
                    f"{BASE}/{UNAVAILABLE_TISSUE_PUBLISHED_ALTERNATES[name]}"
                ] if name in UNAVAILABLE_TISSUE_PUBLISHED_ALTERNATES else [],
                "reason": (
                    "listed by SCREEN but tested source URL(s) returned HTTP 404 "
                    "on 2026-08-01 EDT / 2026-08-02 UTC"
                ),
            }
            for name, filename in UNAVAILABLE_TISSUE_FILES.items()
        ],
        "immune_biosamples": immune,
        "counts": {
            "tissues": len(tissues),
            "tissues_unavailable": len(UNAVAILABLE_TISSUE_FILES),
            "immune_biosamples": len(immune),
            "immune_by_tier": dict(Counter(row["evidence_tier"] for row in immune)),
            "immune_by_lineage": dict(Counter(row["lineage"] for row in immune)),
            "immune_by_lineage_assignment_method": dict(Counter(
                row["lineage_assignment_method"] for row in immune
            )),
        },
    }
    atomic_json(args.output, manifest)
    print(json.dumps(manifest["counts"], indent=2, sort_keys=True))


def ucsc_binary_url() -> str:
    system = platform.system().lower()
    machine = platform.machine().lower()
    if system == "darwin" and machine in {"arm64", "aarch64"}:
        directory = "macOSX.arm64"
    elif system == "darwin" and machine in {"x86_64", "amd64"}:
        directory = "macOSX.x86_64"
    elif system == "linux" and machine in {"x86_64", "amd64"}:
        directory = "linux.x86_64"
    elif system == "linux" and machine in {"arm64", "aarch64"}:
        directory = "linux.aarch64"
    else:
        raise RuntimeError(f"no pinned UCSC binary directory for {system}/{machine}")
    return f"https://hgdownload.soe.ucsc.edu/admin/exe/{directory}/bigBedToBed"


def ensure_bigbed_tool(source_root: Path) -> Path:
    existing = shutil.which("bigBedToBed")
    if existing:
        return Path(existing)
    target = source_root / "tools" / "bigBedToBed"
    if not target.exists():
        run_curl(ucsc_binary_url(), target)
        target.chmod(0o755)
    completed = subprocess.run([str(target)], capture_output=True, text=True)
    diagnostic = completed.stdout + completed.stderr
    if "bigBedToBed" not in diagnostic:
        extra = ""
        if platform.system().lower() == "darwin" and "liblzma" in diagnostic:
            extra = " Install the xz runtime (`brew install xz`) and retry."
        raise RuntimeError(
            f"downloaded bigBedToBed cannot run (exit {completed.returncode}): "
            f"{diagnostic.strip()}.{extra}"
        )
    atomic_json(target.with_suffix(".manifest.json"), {
        "source_url": ucsc_binary_url(),
        "sha256": sha256_file(target),
        "size": target.stat().st_size,
    })
    return target


def gzip_stream_download(url: str, target: Path, expected_rows: int) -> dict[str, Any]:
    sidecar = target.with_suffix(target.suffix + ".manifest.json")
    if target.exists() and sidecar.exists():
        state = json.loads(sidecar.read_text())
        if state.get("source_url") == url and state.get("rows") == expected_rows \
                and state.get("compressed_size") == target.stat().st_size:
            return state
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".part")
    partial.unlink(missing_ok=True)
    command = [
        # -sS matches every other curl call here and matters more for this
        # one: the progress meter is an unbounded stderr writer, and with
        # stderr drained only after stdout, a filled pipe buffer deadlocks
        # this multi-hundred-MB streaming download.
        "curl", "-fsSL", "--retry", "6", "--retry-all-errors",
        "--retry-delay", "2", "--connect-timeout", "30",
        "--speed-limit", "10240", "--speed-time", "30", url,
    ]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if process.stdout is None:
        raise RuntimeError("curl stdout pipe unavailable")
    digest = hashlib.sha256()
    row_count = 0
    first = b""
    with partial.open("wb") as raw_output:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw_output, compresslevel=1, mtime=0) as zipped:
            while True:
                chunk = process.stdout.read(4 * 1024 * 1024)
                if not chunk:
                    break
                if not first:
                    first = chunk.split(b"\n", 1)[0]
                row_count += chunk.count(b"\n")
                digest.update(chunk)
                zipped.write(chunk)
    stderr = (process.stderr.read() if process.stderr else b"").decode(errors="replace")
    return_code = process.wait()
    if return_code:
        partial.unlink(missing_ok=True)
        raise RuntimeError(f"curl failed for {url}: {stderr.strip()}")
    first_fields = first.count(b"\t") + 1
    if row_count != expected_rows or first_fields != 11:
        partial.unlink(missing_ok=True)
        raise RuntimeError(
            f"invalid aggregate BED {url}: rows={row_count}, first_fields={first_fields}"
        )
    partial.replace(target)
    state = {
        "source_url": url,
        "rows": row_count,
        "uncompressed_sha256": digest.hexdigest(),
        "compressed_sha256": sha256_file(target),
        "compressed_size": target.stat().st_size,
        "compression": "gzip level 1; source was uncompressed BED",
    }
    atomic_json(sidecar, state)
    return state


def download_sources(args: argparse.Namespace) -> None:
    manifest = json.loads(args.manifest.read_text())
    expected_rows = manifest["source"]["ccre_count"]
    source_root = args.source_dir
    tool = ensure_bigbed_tool(source_root)
    tasks: list[tuple[str, dict[str, Any]]] = []
    for row in manifest["tissues"]:
        tasks.append(("tissue", row))
    for row in manifest["immune_biosamples"]:
        tasks.append(("immune", row))

    results: dict[str, Any] = {"tissues": {}, "immune_biosamples": {}, "bigBedToBed": str(tool)}

    def one(kind: str, row: dict[str, Any]) -> tuple[str, int, dict[str, Any]]:
        if kind == "tissue":
            target = source_root / "tissues" / (row["source_filename"] + ".gz")
            state = gzip_stream_download(row["source_url"], target, expected_rows)
        else:
            target = source_root / "immune" / row["source_filename"]
            sidecar = target.with_suffix(target.suffix + ".manifest.json")
            if target.exists() and sidecar.exists():
                cached = json.loads(sidecar.read_text())
                if cached.get("source_url") == row["source_url"] \
                        and cached.get("size") == target.stat().st_size:
                    cached["local_path"] = str(target.resolve())
                    return kind, row["index"], cached
            if not target.exists():
                run_curl(row["source_url"], target)
            state = {
                "source_url": row["source_url"],
                "sha256": sha256_file(target),
                "size": target.stat().st_size,
                "format": "UCSC bigBed",
            }
            atomic_json(sidecar, state)
        state["local_path"] = str(target.resolve())
        return kind, row["index"], state

    completed = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(one, kind, row) for kind, row in tasks]
        for future in concurrent.futures.as_completed(futures):
            kind, index, state = future.result()
            key = "tissues" if kind == "tissue" else "immune_biosamples"
            results[key][str(index)] = state
            completed += 1
            print(f"SCREEN sources: {completed}/{len(tasks)}", flush=True)
    results["created_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    atomic_json(source_root / "download-manifest.json", results)


def normalized_chrom(value: bytes) -> str:
    text = value.decode()
    return text[3:] if text.lower().startswith("chr") else text


def build_catalog(ccre_bed: Path, database: Path, manifest: dict[str, Any]) -> list[bytes]:
    temporary = database.with_name(database.name + ".tmp")
    temporary.unlink(missing_ok=True)
    connection = sqlite3.connect(temporary)
    connection.executescript("""
        PRAGMA journal_mode=OFF;
        PRAGMA synchronous=OFF;
        PRAGMA temp_store=MEMORY;
        CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE contig(contig_id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE);
        CREATE TABLE ccre(
            row_index INTEGER PRIMARY KEY,
            contig_id INTEGER NOT NULL,
            chrom TEXT NOT NULL,
            start INTEGER NOT NULL,
            end INTEGER NOT NULL,
            rdhs_accession TEXT NOT NULL UNIQUE,
            ccre_accession TEXT NOT NULL UNIQUE,
            overall_class TEXT NOT NULL
        );
        -- rtree_i32, not rtree: the default stores coordinates as
        -- 32-bit floats (exact only to 2^24), which rounds every cCRE
        -- bound past 16.8 Mb; genomic positions reach 2.5e8.
        CREATE VIRTUAL TABLE ccre_interval USING rtree_i32(
            row_index, contig_min, contig_max, start, end
        );
        CREATE TABLE class_code(
            code INTEGER PRIMARY KEY,
            label TEXT NOT NULL UNIQUE,
            description TEXT NOT NULL
        );
        CREATE TABLE tissue(
            tissue_index INTEGER PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            source_filename TEXT NOT NULL,
            source_url TEXT NOT NULL
        );
        CREATE TABLE immune_biosample(
            biosample_index INTEGER PRIMARY KEY,
            screen_name TEXT NOT NULL UNIQUE,
            display_name TEXT NOT NULL,
            ontology_id TEXT NOT NULL,
            ontology_name TEXT NOT NULL,
            sample_type TEXT NOT NULL,
            lineage TEXT NOT NULL,
            lineage_assignment_method TEXT NOT NULL,
            evidence_tier TEXT NOT NULL,
            assays_available_json TEXT NOT NULL,
            organ_slims_json TEXT NOT NULL,
            system_slims_json TEXT NOT NULL,
            cell_slims_json TEXT NOT NULL,
            life_stages_json TEXT NOT NULL,
            sexes_json TEXT NOT NULL,
            treatments_json TEXT NOT NULL,
            diseases_json TEXT NOT NULL,
            donor_accession TEXT NOT NULL,
            source_filename TEXT NOT NULL,
            source_url TEXT NOT NULL
        );
    """)
    connection.executemany(
        "INSERT INTO class_code VALUES(?,?,?)",
        [(code, *CLASS_LABELS[code]) for code in sorted(CLASS_LABELS)],
    )
    for key, value in (
        ("registry", REGISTRY), ("assembly", ASSEMBLY),
        ("matrix_layout", "ccre-major contiguous uint8 rows"),
        ("zero_semantics", "Low-DNase/inactive; not deletion or missing genotype"),
        # Two conventions are live in this codebase: this catalog mirrors
        # the source BED, while local_service/ccre_context.py converts to
        # 1-based inclusive. Record ours so joins are deliberate.
        ("coordinate_convention", "BED 0-based half-open (start, end)"),
    ):
        connection.execute("INSERT INTO metadata VALUES(?,?)", (key, value))
    connection.executemany(
        "INSERT INTO tissue VALUES(?,?,?,?)",
        [(r["index"], r["name"], r["source_filename"], r["source_url"]) for r in manifest["tissues"]],
    )
    immune_rows = []
    for row in manifest["immune_biosamples"]:
        metadata = row["metadata"]
        immune_rows.append((
            row["index"], row["screen_name"], metadata["display_name"],
            metadata["ontology_id"], metadata["ontology_name"], metadata["sample_type"],
            row["lineage"], row.get("lineage_assignment_method", "legacy_unrecorded"),
            row["evidence_tier"], json.dumps(row["assays_available"]),
            json.dumps(metadata["organ_slims"]), json.dumps(metadata["system_slims"]),
            json.dumps(metadata["cell_slims"]), json.dumps(metadata["life_stages"]),
            json.dumps(metadata["sexes"]), json.dumps(metadata["treatments"]),
            json.dumps(metadata["diseases"]), row["donor_accession"],
            row["source_filename"], row["source_url"],
        ))
    connection.executemany(
        "INSERT INTO immune_biosample VALUES(" + ",".join("?" for _ in range(20)) + ")",
        immune_rows,
    )

    contigs: dict[str, int] = {}
    accessions: list[bytes] = []
    ccre_batch: list[tuple[Any, ...]] = []
    interval_batch: list[tuple[int, int, int, int, int]] = []
    with ccre_bed.open("rb") as handle:
        for row_index, line in enumerate(handle):
            fields = line.rstrip(b"\n").split(b"\t")
            chrom = normalized_chrom(fields[0])
            if chrom not in contigs:
                contig_id = len(contigs) + 1
                contigs[chrom] = contig_id
                connection.execute("INSERT INTO contig VALUES(?,?)", (contig_id, chrom))
            contig_id = contigs[chrom]
            start, end = int(fields[1]), int(fields[2])
            accessions.append(fields[4])
            ccre_batch.append((
                row_index, contig_id, chrom, start, end,
                fields[3].decode(), fields[4].decode(), fields[5].decode(),
            ))
            interval_batch.append((row_index, contig_id, contig_id, start, end))
            if len(ccre_batch) == 50_000:
                connection.executemany("INSERT INTO ccre VALUES(?,?,?,?,?,?,?,?)", ccre_batch)
                connection.executemany("INSERT INTO ccre_interval VALUES(?,?,?,?,?)", interval_batch)
                ccre_batch.clear(); interval_batch.clear()
        if ccre_batch:
            connection.executemany("INSERT INTO ccre VALUES(?,?,?,?,?,?,?,?)", ccre_batch)
            connection.executemany("INSERT INTO ccre_interval VALUES(?,?,?,?,?)", interval_batch)
    connection.execute("CREATE INDEX ccre_coordinate ON ccre(contig_id,start,end)")
    connection.commit()
    connection.execute("PRAGMA optimize")
    connection.close()
    temporary.replace(database)
    return accessions


def source_line_iterator(path: Path, bigbed_tool: Path | None):
    if path.name.endswith(".bigBed"):
        if bigbed_tool is None:
            raise RuntimeError("bigBedToBed is required for individual SCREEN sources")
        # The UCSC macOS binary does not permit /dev/stdout as its output path.
        # Convert one source at a time in a temporary directory and stream that
        # file into the categorical encoder; it is deleted immediately.
        with tempfile.TemporaryDirectory(prefix="screen-bigbed-") as temporary:
            bed = Path(temporary) / "classification.bed"
            completed = subprocess.run(
                [str(bigbed_tool), str(path), str(bed)],
                capture_output=True,
            )
            if completed.returncode:
                diagnostic = completed.stderr.decode(errors="replace").strip()
                raise RuntimeError(f"bigBedToBed failed for {path}: {diagnostic}")
            with bed.open("rb") as handle:
                yield from handle
    else:
        with gzip.open(path, "rb") as handle:
            yield from handle


def encode_source(
    path: Path,
    expected_accessions: list[bytes],
    expected_statuses: set[bytes],
    bigbed_tool: Path | None,
) -> tuple[bytearray, Counter[str]]:
    codes = bytearray(len(expected_accessions))
    counts: Counter[str] = Counter()
    for index, line in enumerate(source_line_iterator(path, bigbed_tool)):
        if index >= len(expected_accessions):
            raise ValueError(f"{path.name}: more rows than Registry cCRE table")
        fields = line.rstrip(b"\n").split(b"\t", 10)
        if len(fields) != 11:
            raise ValueError(f"{path.name}: invalid BED schema on row {index + 1}")
        if fields[3] != expected_accessions[index]:
            raise ValueError(
                f"{path.name}: cCRE order mismatch on row {index + 1}: "
                f"{fields[3].decode(errors='replace')}"
            )
        raw_class = fields[9].decode()
        status = fields[10]
        if status not in expected_statuses:
            raise ValueError(f"{path.name}: unexpected status {status.decode(errors='replace')}")
        if raw_class.startswith("Low-"):
            code = 0
        else:
            code = CLASS_CODES.get(raw_class)
            if code is None:
                raise ValueError(f"{path.name}: unknown cCRE class {raw_class}")
        codes[index] = code
        counts[CLASS_LABELS[code][0]] += 1
    if sum(counts.values()) != len(expected_accessions):
        raise ValueError(
            f"{path.name}: expected {len(expected_accessions)} rows, got {sum(counts.values())}"
        )
    return codes, counts


def transpose_matrix(sample_major: Path, output: Path, samples: int, ccres: int) -> None:
    try:
        import numpy as np
    except ImportError as error:
        raise RuntimeError("SCREEN preparation requires NumPy for matrix transposition") from error
    source = np.memmap(sample_major, dtype=np.uint8, mode="r", shape=(samples, ccres))
    destination = np.memmap(output, dtype=np.uint8, mode="w+", shape=(ccres, samples))
    for start in range(0, ccres, 100_000):
        end = min(ccres, start + 100_000)
        destination[start:end, :] = source[:, start:end].T
    destination.flush()
    del destination, source


def prepare_dataset(args: argparse.Namespace) -> None:
    manifest = json.loads(args.manifest.read_text())
    source_root = args.source_dir
    output = args.output_dir
    output.mkdir(parents=True, exist_ok=True)
    catalog = output / "screen.registry-v4.catalog.sqlite3"
    tissue_matrix = output / "screen.registry-v4.tissues.u8"
    immune_matrix = output / "screen.registry-v4.immune.u8"
    final_manifest = output / "screen.registry-v4.prepared.json"
    for path in (catalog, tissue_matrix, immune_matrix, final_manifest):
        if path.exists() and not args.force:
            raise RuntimeError(f"prepared output exists; use --force to replace: {path}")
    bigbed_tool = ensure_bigbed_tool(source_root)
    accessions = build_catalog(args.ccre_bed, catalog, manifest)
    expected_count = manifest["source"]["ccre_count"]
    if len(accessions) != expected_count:
        raise RuntimeError("cCRE catalog count changed after manifest creation")

    summaries: dict[str, Any] = {"tissues": {}, "immune_biosamples": {}}
    with tempfile.TemporaryDirectory(prefix="screen-prep-", dir=output) as temporary_name:
        temporary = Path(temporary_name)
        for collection, rows, statuses, final_path in (
            ("tissues", manifest["tissues"], {AGGREGATE_STATUS.encode()}, tissue_matrix),
            (
                "immune_biosamples", manifest["immune_biosamples"],
                {FULL_STATUS.encode(), PARTIAL_STATUS.encode()}, immune_matrix,
            ),
        ):
            sample_major = temporary / f"{collection}.sample-major.u8"
            with sample_major.open("wb") as matrix_handle:
                for number, row in enumerate(rows, 1):
                    if collection == "tissues":
                        path = source_root / "tissues" / (row["source_filename"] + ".gz")
                        tool = None
                    else:
                        path = source_root / "immune" / row["source_filename"]
                        tool = bigbed_tool
                    codes, counts = encode_source(path, accessions, statuses, tool)
                    matrix_handle.write(codes)
                    summaries[collection][str(row["index"])] = dict(counts)
                    print(f"Encoding {collection}: {number}/{len(rows)}", flush=True)
            temporary_output = temporary / final_path.name
            transpose_matrix(sample_major, temporary_output, len(rows), expected_count)
            temporary_output.replace(final_path)

    observed_classes = {}
    for collection, rows in summaries.items():
        totals = Counter()
        for counts in rows.values():
            totals.update(counts)
        observed_classes[collection] = {
            "call_counts": dict(sorted(totals.items())),
            "observed_labels": sorted(label for label, count in totals.items() if count),
            "supported_but_unobserved_labels": sorted(
                label for _code, (label, _description) in CLASS_LABELS.items()
                if label not in totals or totals[label] == 0
            ),
        }
    prepared = {
        "schema_version": 1,
        "registry": REGISTRY,
        "assembly": ASSEMBLY,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "ccre_count": expected_count,
        "catalog": {
            "path": str(catalog.resolve()), "sha256": sha256_file(catalog),
            "size": catalog.stat().st_size,
        },
        "tissue_matrix": {
            "path": str(tissue_matrix.resolve()), "sha256": sha256_file(tissue_matrix),
            "size": tissue_matrix.stat().st_size,
            "shape": [expected_count, len(manifest["tissues"])],
            "layout": "ccre-major uint8",
        },
        "immune_matrix": {
            "path": str(immune_matrix.resolve()), "sha256": sha256_file(immune_matrix),
            "size": immune_matrix.stat().st_size,
            "shape": [expected_count, len(manifest["immune_biosamples"])],
            "layout": "ccre-major uint8",
        },
        "class_codes": manifest["class_codes"],
        "summaries": summaries,
        "observed_classes": observed_classes,
        "scientific_notes": [
            "Tissue classes are aggregate evidence and do not identify a causal cell type.",
            "CA-only is displayed canonically as CA while the source vocabulary remains documented.",
            "Immune evidence tier records full, partial, or accessibility-only assay completeness.",
            "A missing assay is unavailable, not negative.",
        ],
    }
    atomic_json(final_manifest, prepared)
    print(json.dumps({
        "catalog_bytes": catalog.stat().st_size,
        "tissue_matrix_bytes": tissue_matrix.stat().st_size,
        "immune_matrix_bytes": immune_matrix.stat().st_size,
    }, indent=2))


def validate_column_histograms(
    selection: dict[str, Any], prepared: dict[str, Any]
) -> dict[str, Any]:
    """Check every post-transpose column against its pre-transpose summary."""
    try:
        import numpy as np
    except ImportError as error:
        raise RuntimeError("SCREEN alignment validation requires NumPy") from error

    result = {}
    labels = [CLASS_LABELS[code][0] for code in sorted(CLASS_LABELS)]
    for collection, matrix_name in (
        ("immune_biosamples", "immune_matrix"),
        ("tissues", "tissue_matrix"),
    ):
        rows = selection[collection]
        shape = tuple(prepared[matrix_name]["shape"])
        if shape[1] != len(rows):
            raise RuntimeError(f"{collection} column count does not match selection")
        if any(row["index"] != index for index, row in enumerate(rows)):
            raise RuntimeError(f"{collection} indices do not match list/column order")
        summaries = prepared.get("summaries", {}).get(collection, {})
        if len(summaries) != len(rows):
            raise RuntimeError(f"{collection} pre-transpose summaries are incomplete")
        expected = [
            tuple(int(summaries[str(index)].get(label, 0)) for label in labels)
            for index in range(len(rows))
        ]
        if len(set(expected)) != len(expected):
            raise RuntimeError(
                f"{collection} contains duplicate class histograms; histogram-based "
                "column identity would be ambiguous"
            )
        matrix = np.memmap(
            prepared[matrix_name]["path"], dtype=np.uint8, mode="r", shape=shape
        )
        observed = np.zeros((shape[1], len(labels)), dtype=np.int64)
        for start in range(0, shape[0], 100_000):
            block = np.asarray(matrix[start:min(start + 100_000, shape[0]), :])
            for code in range(len(labels)):
                observed[:, code] += (block == code).sum(axis=0)
        mismatches = [
            index for index, histogram in enumerate(expected)
            if tuple(int(value) for value in observed[index]) != histogram
        ]
        del matrix
        if mismatches:
            preview = ", ".join(map(str, mismatches[:10]))
            raise RuntimeError(
                f"{collection} post-transpose histogram mismatch in column(s): {preview}"
            )
        result[collection] = {
            "columns_checked": len(rows),
            "histograms_unique": True,
        }
    return result


def validate_alignment(args: argparse.Namespace) -> None:
    """Validate pinned biological labels against exact matrix row content."""
    fixture = json.loads(args.fixture.read_text())
    selection = json.loads(args.selection.read_text())
    prepared = json.loads(args.prepared_manifest.read_text())
    if selection["registry"] != fixture["registry"] \
            or selection["assembly"] != fixture["assembly"]:
        raise RuntimeError("alignment fixture release does not match the selection")
    for matrix_name in ("immune_matrix", "tissue_matrix"):
        expected = fixture["matrix_sha256"][matrix_name]
        observed = prepared[matrix_name]["sha256"]
        if observed != expected:
            raise RuntimeError(
                f"{matrix_name} is not the matrix pinned by the alignment fixture: {observed}"
            )
        actual = sha256_file(Path(prepared[matrix_name]["path"]))
        if actual != expected:
            raise RuntimeError(
                f"{matrix_name} file checksum does not match its pinned content: {actual}"
            )
    histogram_validation = validate_column_histograms(selection, prepared)

    for profile in fixture["immune_profiles"]:
        observed = selection["immune_biosamples"][profile["index"]]
        for key in ("screen_name", "evidence_tier"):
            if observed[key] != profile[key]:
                raise RuntimeError(
                    f'immune column {profile["index"]} {key} mismatch: {observed[key]}'
                )
        if observed["metadata"]["ontology_id"] != profile["ontology_id"]:
            raise RuntimeError(f'immune column {profile["index"]} ontology mismatch')
    for tissue in fixture["tissues"]:
        observed = selection["tissues"][tissue["index"]]
        if observed["name"] != tissue["name"]:
            raise RuntimeError(f'tissue column {tissue["index"]} name mismatch')

    catalog = sqlite3.connect(prepared["catalog"]["path"])
    catalog.row_factory = sqlite3.Row
    catalog_rows = {}
    for expected_row in fixture["rows"]:
        observed_row = catalog.execute(
            "SELECT * FROM ccre WHERE row_index=?", (expected_row["row_index"],)
        ).fetchone()
        if observed_row is None:
            catalog.close()
            raise RuntimeError(f'fixture row missing: {expected_row["row_index"]}')
        catalog_rows[expected_row["row_index"]] = observed_row
    catalog.close()
    immune_columns = prepared["immune_matrix"]["shape"][1]
    tissue_columns = prepared["tissue_matrix"]["shape"][1]
    with Path(prepared["immune_matrix"]["path"]).open("rb") as immune_handle, \
            Path(prepared["tissue_matrix"]["path"]).open("rb") as tissue_handle:
        for expected_row in fixture["rows"]:
            observed_row = catalog_rows[expected_row["row_index"]]
            for key in (
                "chrom", "start", "end", "rdhs_accession", "ccre_accession",
                "overall_class",
            ):
                if observed_row[key] != expected_row[key]:
                    raise RuntimeError(
                        f'alignment fixture catalog mismatch at row '
                        f'{expected_row["row_index"]}: {key}'
                    )
            immune_handle.seek(expected_row["row_index"] * immune_columns)
            immune_codes = immune_handle.read(immune_columns)
            tissue_handle.seek(expected_row["row_index"] * tissue_columns)
            tissue_codes = tissue_handle.read(tissue_columns)
            if hashlib.sha256(immune_codes).hexdigest() != expected_row["immune_row_sha256"]:
                raise RuntimeError(
                    f'immune content-alignment mismatch at {expected_row["ccre_accession"]}'
                )
            if hashlib.sha256(tissue_codes).hexdigest() != expected_row["tissue_row_sha256"]:
                raise RuntimeError(
                    f'tissue content-alignment mismatch at {expected_row["ccre_accession"]}'
                )
            for index, code in expected_row["immune_profile_codes"].items():
                if immune_codes[int(index)] != code:
                    raise RuntimeError(
                        f'immune column content mismatch at {expected_row["ccre_accession"]}, '
                        f'profile {index}'
                    )
            for index, code in expected_row["tissue_codes"].items():
                if tissue_codes[int(index)] != code:
                    raise RuntimeError(
                        f'tissue column content mismatch at {expected_row["ccre_accession"]}, '
                        f'tissue {index}'
                    )
    print(json.dumps({
        "status": "passed",
        "fixture": str(args.fixture),
        "rows_checked": len(fixture["rows"]),
        "immune_columns_checked": len(fixture["immune_profiles"]),
        "tissue_columns_checked": len(fixture["tissues"]),
        "all_column_histograms": histogram_validation,
    }, indent=2, sort_keys=True))


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser()
    commands = root.add_subparsers(dest="command", required=True)
    manifest = commands.add_parser("build-manifest")
    manifest.add_argument("--experiment-lists", type=Path, required=True)
    manifest.add_argument("--ccre-bed", type=Path, required=True)
    manifest.add_argument("--cell-ontology", type=Path, required=True)
    manifest.add_argument("--metadata-cache", type=Path, required=True)
    manifest.add_argument("--metadata-workers", type=int, default=4)
    manifest.add_argument("--output", type=Path, required=True)
    manifest.set_defaults(function=build_manifest)
    download = commands.add_parser("download")
    download.add_argument("--manifest", type=Path, required=True)
    download.add_argument("--source-dir", type=Path, required=True)
    download.add_argument("--workers", type=int, default=6)
    download.set_defaults(function=download_sources)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--manifest", type=Path, required=True)
    prepare.add_argument("--ccre-bed", type=Path, required=True)
    prepare.add_argument("--source-dir", type=Path, required=True)
    prepare.add_argument("--output-dir", type=Path, required=True)
    prepare.add_argument("--force", action="store_true")
    prepare.set_defaults(function=prepare_dataset)
    validate = commands.add_parser(
        "validate-alignment",
        help="check pinned cCRE rows against biological matrix column labels",
    )
    validate.add_argument("--fixture", type=Path, required=True)
    validate.add_argument("--selection", type=Path, required=True)
    validate.add_argument("--prepared-manifest", type=Path, required=True)
    validate.set_defaults(function=validate_alignment)
    return root


def main() -> int:
    args = parser().parse_args()
    args.function(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
