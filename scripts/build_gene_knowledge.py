#!/usr/bin/env python3
"""Build the redistributable HGNC/IUIS/ClinGen gene-knowledge database."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from local_service.gene_knowledge import build_public_database, sha256


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hgnc", required=True, type=Path)
    parser.add_argument("--iuis", required=True, type=Path)
    parser.add_argument("--clingen-validity", required=True, type=Path)
    parser.add_argument("--clingen-dosage", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--release-date", required=True)
    args = parser.parse_args()
    releases = {
        "hgnc": {
            "release": args.release_date,
            "source_url": "https://storage.googleapis.com/public-download-files/hgnc/tsv/tsv/hgnc_complete_set.txt",
        },
        "iuis": {
            "release": "October 2024",
            "source_url": "https://iuis.org/committees/iei/",
        },
        "clingen_validity": {
            "release": args.release_date,
            "source_url": "https://search.clinicalgenome.org/kb/gene-validity/download",
        },
        "clingen_dosage": {
            "release": args.release_date,
            "source_url": "https://ftp.clinicalgenome.org/ClinGen_gene_curation_list_GRCh38.tsv",
        },
    }
    result = build_public_database(
        hgnc=args.hgnc,
        iuis=args.iuis,
        clingen_validity=args.clingen_validity,
        clingen_dosage=args.clingen_dosage,
        destination=args.output,
        releases=releases,
    )
    source_paths = {
        "hgnc": args.hgnc, "iuis": args.iuis,
        "clingen_validity": args.clingen_validity,
        "clingen_dosage": args.clingen_dosage,
    }
    manifest_resources = {
        key: {
            **releases[key], "source_sha256": sha256(path),
            "record_count": result["counts"][key],
        }
        for key, path in source_paths.items()
    }
    args.manifest.write_text(
        json.dumps({**result, "resources": manifest_resources}, indent=2, sort_keys=True) + "\n"
    )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
