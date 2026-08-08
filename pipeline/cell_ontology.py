"""Pinned Cell Ontology parsing shared by SCREEN preparation stages."""
from __future__ import annotations

from pathlib import Path
from typing import Any


CELL_ONTOLOGY_RELEASE = "v2026-06-08"
CELL_ONTOLOGY_SHA256 = "73996c6349283e7a8cbd8183367a1cb810d0077e9c291ae0c72bd31e869f8c1c"
CELL_ONTOLOGY_SOURCE_URL = (
    "https://github.com/obophenotype/cell-ontology/releases/download/"
    f"{CELL_ONTOLOGY_RELEASE}/cl-basic.obo"
)


def parse_cell_ontology(path: Path) -> dict[str, Any]:
    """Read an OBO basic graph, retaining active Cell Ontology terms."""
    terms: dict[str, dict[str, Any]] = {}
    header: dict[str, str] = {}
    current: dict[str, Any] | None = None
    in_header = True

    def commit() -> None:
        nonlocal current
        if current and current.get("id", "").startswith("CL:") \
                and not current.get("is_obsolete"):
            current["parents"] = sorted(set(current.get("parents", [])))
            terms[current["id"]] = current
        current = None

    with path.open(encoding="utf-8") as handle:
        for raw in handle:
            line = raw.rstrip("\n")
            if line == "[Term]":
                commit()
                current = {"parents": []}
                in_header = False
                continue
            if line.startswith("["):
                commit()
                in_header = False
                continue
            if in_header and ": " in line:
                key, value = line.split(": ", 1)
                if key in {"data-version", "date", "ontology"}:
                    header[key] = value
                continue
            if current is None or ": " not in line:
                continue
            key, value = line.split(": ", 1)
            if key == "id":
                current["id"] = value
            elif key == "name":
                current["name"] = value
            elif key == "is_a":
                # OBO may place an inferred-edge qualifier before `! name`.
                current["parents"].append(value.split(maxsplit=1)[0])
            elif key == "is_obsolete" and value.casefold() == "true":
                current["is_obsolete"] = True
    commit()
    if not terms:
        raise RuntimeError(f"Cell Ontology contains no active CL terms: {path}")
    return {"header": header, "terms": terms}


def ontology_ancestors(ontology: dict[str, Any]) -> dict[str, set[str]]:
    """Return the transitive `is_a` ancestors for every parsed term."""
    terms = ontology["terms"]
    cache: dict[str, set[str]] = {}

    def one(term_id: str, visiting: set[str] | None = None) -> set[str]:
        if term_id in cache:
            return cache[term_id]
        visiting = set() if visiting is None else visiting
        if term_id in visiting:
            raise RuntimeError(f"cycle in Cell Ontology at {term_id}")
        result: set[str] = set()
        for parent in terms.get(term_id, {}).get("parents", []):
            # is_a targets include obsolete CL terms and non-CL IDs
            # (UBERON/GO/PATO); only admitted terms are valid ancestors —
            # dangling IDs made consumers KeyError or silently miss.
            if parent not in terms:
                continue
            result.add(parent)
            result.update(one(parent, visiting | {term_id}))
        cache[term_id] = result
        return result

    for term_id in terms:
        one(term_id)
    return cache
