#!/usr/bin/env python3
"""Synthetic tests for private, component-based GenIA imports.

The fixtures in this module describe only the public file shapes understood by
the importer.  They contain no rows copied from the registered-user exports.
"""

from __future__ import annotations

import csv
import gzip
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import local_service.genia as genia_module

from local_service.gene_knowledge import GeneKnowledgeStore, PUBLIC_SCHEMA
from local_service.genia import (
    COMPONENT_LABELS,
    GeniaStore,
    build_genia_database,
    detect_component,
    inspect_genia_sources,
)


GEI_HEADER = [
    "ID",
    "Condition's_name",
    "Synonyms",
    "Gene",
    "MOI[ℹ]",
    "MOA[ℹ]",
    "Pub.Year[ℹ]",
    "IUIS.classification[ℹ]",
    "Curated[ℹ]",
    "#Cases[ℹ]",
    "#Fams[ℹ]",
    "OMIM",
    "MONDO",
    "ClinGen.class",
    "LastUpdated",
]

DISEASE_HEADER = [
    "id",
    "term",
    "acronym",
    "alt_term",
    "gene_id",
    "gene",
    "MOI",
    "MOA",
    "pub_year",
    "IEI",
    "IUIS_tbl",
    "OMIM_ID",
    "MONDO_ID",
    "ClinGen_class",
    "ClinGen_rev_date",
    "last_updated",
    "num_cases",
    "num_fams",
]

DISEASE_PHENOTYPE_HEADER = [
    "disease_id",
    "disease_name",
    "OMIM_ID",
    "gene_id",
    "gene",
    "MOI",
    "clinterm_id",
    "clinterm",
    "HPO_term",
    "HPO_ID",
    "rank",
    "count_yes",
    "percent_yes",
    "count_no",
    "percent_no",
    "count_unrep",
    "percent_unrep",
]

PHENOTYPE_HEADER = [
    "id",
    "term",
    "alt_term",
    "description",
    "HPO_term",
    "HPO_ID",
    "NCIT",
    "MESH",
    "ICD10",
    "EFO",
    "OAE",
    "last_updated",
    "parent_terms",
]


def write_table(
    path: Path,
    header: list[str],
    rows: list[list[object]],
    *,
    delimiter: str,
) -> Path:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter=delimiter)
        writer.writerow(header)
        writer.writerows(rows)
    return path


def gei_row(
    *,
    source_id: str = "NFKB1D",
    disease: str = "Synthetic NFKB1 deficiency",
    gene: str = "NFKB1",
    curated: str = "Yes",
    mondo: str = "33261",
) -> list[object]:
    return [
        source_id,
        disease,
        "Synthetic alternate name",
        gene,
        "AD",
        "Loss of function",
        "2026",
        "Synthetic category",
        curated,
        "4",
        "3",
        "600000",
        mondo,
        "Definitive",
        "2026-01-01",
    ]


def disease_row(
    *,
    disease_id: str = "101",
    term: str = "Synthetic NFKB1 deficiency",
    acronym: str = "NFKB1D",
    gene: str = "NFKB1",
    iei: str = "Y",
    mondo: str = "MONDO:33261",
) -> list[object]:
    return [
        disease_id,
        term,
        acronym,
        "Synthetic alternate name",
        "4790",
        gene,
        "AD",
        "Loss of function",
        "2026",
        iei,
        "Synthetic category",
        "600000",
        mondo,
        "Definitive",
        "2026-01-02",
        "2026-01-03",
        "4",
        "3",
    ]


def disease_phenotype_row(
    *,
    disease_id: str = "101",
    gene: str = "NFKB1",
    clinical_term_id: str = "CT1",
) -> list[object]:
    return [
        disease_id,
        "Synthetic NFKB1 deficiency",
        "600000",
        "4790",
        gene,
        "AD",
        clinical_term_id,
        "Synthetic recurrent infection",
        "Recurrent infections",
        "HP:0000001",
        "1",
        "3",
        "75",
        "1",
        "25",
        "0",
        "0",
    ]


def phenotype_row(
    *,
    clinical_term_id: str = "CT1",
    description: str = "First description line\nsecond description line",
) -> list[object]:
    return [
        clinical_term_id,
        "Synthetic recurrent infection",
        "Synthetic alternate term",
        description,
        "Recurrent infections",
        "HP:0000001",
        "NCIT:C0001",
        "MESH:D0001",
        "D00",
        "EFO:0001",
        "OAE:0001",
        "2026-01-04",
        "Synthetic parent",
    ]


def write_variant_vcf(
    path: Path,
    records: list[tuple[str, int, str, str, str, str, str, str]] | None = None,
) -> Path:
    records = records or [
        ("1", 100, "GENIA-V1", "A", "G", "NC", "p.Synthetic1", "0_subjects"),
        ("chr1", 101, "GENIA-V2", "C", "T", "RF", "p.Synthetic2", "2_subjects"),
    ]
    lines = [
        "##fileformat=VCFv4.2",
        "##contig=<ID=1,assembly=hg38>",
        '##INFO=<ID=Variant,Number=1,Type=String,Description="Variant short name">',
        '##INFO=<ID=Class,Number=1,Type=String,Description="Classification">',
        '##INFO=<ID=Relevant_in,Number=1,Type=String,Description="Reported subjects">',
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO",
    ]
    lines.extend(
        "\t".join(
            [
                chrom,
                str(pos),
                record_id,
                ref,
                alt,
                ".",
                ".",
                f"Variant={short_name};Class={classification};Relevant_in={relevant}",
            ]
        )
        for chrom, pos, record_id, ref, alt, classification, short_name, relevant in records
    )
    payload = ("\n".join(lines) + "\n")
    if path.name.endswith(".gz") or "gzip" in path.name:
        with gzip.open(path, "wt", encoding="utf-8", newline="") as handle:
            handle.write(payload)
    else:
        path.write_text(payload, encoding="utf-8")
    return path


class GeniaComponentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def component_sources(self) -> dict[str, Path]:
        return {
            "gei_disease": write_table(
                self.root / "opaque relationship download.data",
                GEI_HEADER,
                [gei_row()],
                delimiter=",",
            ),
            "disease_catalog": write_table(
                self.root / "catalog without expected name.data",
                DISEASE_HEADER,
                [disease_row()],
                delimiter="\t",
            ),
            "disease_phenotypes": write_table(
                self.root / "associations export.data",
                DISEASE_PHENOTYPE_HEADER,
                [disease_phenotype_row()],
                delimiter="\t",
            ),
            "phenotype_vocabulary": write_table(
                self.root / "terms export.data",
                PHENOTYPE_HEADER,
                [phenotype_row()],
                delimiter="\t",
            ),
            "variant_vcf": write_variant_vcf(
                self.root / "compressed variant export gzip.data"
            ),
        }

    def test_detects_every_component_by_schema_with_arbitrary_filenames(self) -> None:
        sources = self.component_sources()
        detected = {
            role: detect_component(path)["role"] for role, path in sources.items()
        }
        self.assertEqual(detected, {role: role for role in COMPONENT_LABELS})
        inspection = inspect_genia_sources(list(reversed(sources.values())))
        self.assertEqual(
            set(inspection["selected_components"]),
            set(COMPONENT_LABELS),
        )
        self.assertEqual(inspection["missing_components"], [])

    def test_each_component_and_an_arbitrary_subset_install_independently(self) -> None:
        sources = self.component_sources()
        expected_capability = {
            "gei_disease": "gene_disease",
            "disease_catalog": "gene_disease",
            "disease_phenotypes": "phenotype_evidence",
            "phenotype_vocabulary": "phenotype_details",
            "variant_vcf": "variant_evidence",
        }
        for role, path in sources.items():
            with self.subTest(role=role):
                destination = self.root / f"{role}.sqlite3"
                result = build_genia_database([path], destination)
                self.assertEqual(result["updated_components"], [role])
                self.assertEqual(set(result["components"]), {role})
                self.assertTrue(result["capabilities"][expected_capability[role]])

        subset_database = self.root / "subset.sqlite3"
        build_genia_database(
            [sources["gei_disease"], sources["disease_phenotypes"]],
            subset_database,
        )
        subset = GeniaStore(subset_database).status()
        self.assertEqual(
            set(subset["components"]),
            {"gei_disease", "disease_phenotypes"},
        )
        self.assertTrue(subset["capabilities"]["gene_disease"])
        self.assertTrue(subset["capabilities"]["phenotype_evidence"])
        self.assertFalse(subset["capabilities"]["phenotype_details"])
        self.assertFalse(subset["capabilities"]["variant_evidence"])

    def test_partial_update_replaces_selected_and_preserves_omitted_components(self) -> None:
        sources = self.component_sources()
        destination = self.root / "genia.sqlite3"
        build_genia_database(
            [sources["gei_disease"], sources["variant_vcf"]], destination
        )
        store = GeniaStore(destination)
        variant_component = dict(store.status()["components"]["variant_vcf"])

        replacement = write_table(
            self.root / "a newly named relationship file",
            GEI_HEADER,
            [
                gei_row(
                    source_id="NFKB1-NEW",
                    disease="Replacement synthetic relationship",
                    curated="Ongoing",
                )
            ],
            delimiter=",",
        )
        result = build_genia_database([replacement], destination)

        self.assertEqual(result["updated_components"], ["gei_disease"])
        self.assertEqual(
            result["components"]["variant_vcf"],
            variant_component,
        )
        self.assertEqual(
            [row["disease_name"] for row in store.gene("NFKB1")["relationships"]],
            ["Replacement synthetic relationship"],
        )
        self.assertEqual(
            store.variant("1", 100, "A", "G")["records"][0]["record_id"],
            "GENIA-V1",
        )

    def test_embedded_newline_is_parsed_as_one_phenotype_record(self) -> None:
        vocabulary = write_table(
            self.root / "multiline.tsv",
            PHENOTYPE_HEADER,
            [phenotype_row(description="Line one\nLine two")],
            delimiter="\t",
        )
        destination = self.root / "genia.sqlite3"
        build_genia_database([vocabulary], destination)
        with sqlite3.connect(destination) as connection:
            rows = connection.execute(
                "SELECT clinical_term_id,description FROM phenotype_terms"
            ).fetchall()
        self.assertEqual(rows, [("CT1", "Line one Line two")])

    def test_html_is_rejected_without_changing_the_working_database(self) -> None:
        source = write_table(
            self.root / "working.csv", GEI_HEADER, [gei_row()], delimiter=","
        )
        destination = self.root / "genia.sqlite3"
        build_genia_database([source], destination)
        before = destination.read_bytes()
        bad = self.root / "downloaded export.tsv"
        bad.write_text(
            "<!doctype html><title>Registration required</title>",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(ValueError, "web page instead of GenIA data"):
            build_genia_database([bad], destination)

        self.assertEqual(destination.read_bytes(), before)
        self.assertEqual(
            GeniaStore(destination).gene("NFKB1")["relationships"][0]["disease_name"],
            "Synthetic NFKB1 deficiency",
        )

    def test_complete_reinstall_can_recover_a_corrupt_local_index(self) -> None:
        sources = self.component_sources()
        destination = self.root / "genia.sqlite3"
        destination.write_bytes(b"not a sqlite database")

        result = build_genia_database(list(sources.values()), destination)

        self.assertTrue(result["installed"])
        self.assertEqual(set(result["components"]), set(COMPONENT_LABELS))
        self.assertNotIn("error", result)

    def test_mondo_and_curation_values_are_normalized_without_losing_raw_status(self) -> None:
        source = write_table(
            self.root / "relationships.csv",
            GEI_HEADER,
            [
                gei_row(source_id="YES", disease="Curated relation", curated="Yes"),
                gei_row(source_id="ONGOING", disease="Ongoing relation", curated="Ongoing"),
                gei_row(source_id="NO", disease="Uncurated relation", curated="No"),
            ],
            delimiter=",",
        )
        destination = self.root / "genia.sqlite3"
        build_genia_database([source], destination)
        relationships = GeniaStore(destination).gene("NFKB1")["relationships"]
        by_name = {row["disease_name"]: row for row in relationships}

        self.assertEqual(by_name["Curated relation"]["curation_status"], "curated")
        self.assertEqual(by_name["Ongoing relation"]["curation_status"], "ongoing")
        self.assertEqual(by_name["Uncurated relation"]["curation_status"], "not_curated")
        self.assertEqual(by_name["Ongoing relation"]["curation_status_raw"], "Ongoing")
        self.assertEqual(
            {row["mondo_id"] for row in relationships},
            {"MONDO:0033261"},
        )

    def test_variant_import_maps_nc_and_rf_and_excludes_ambiguous_n(self) -> None:
        source = write_variant_vcf(
            self.root / "variants.vcf.gz",
            [
                ("1", 100, "GENIA-NC", "a", "g", "NC", "p.SyntheticNC", "0_subjects"),
                ("chr1", 101, "GENIA-RF", "c", "t", "RF", "p.SyntheticRF", "2_subjects"),
                ("1", 102, "GENIA-N", "N", "A", "NC", "p.Ambiguous", "0_subjects"),
            ],
        )
        destination = self.root / "genia.sqlite3"
        result = build_genia_database([source], destination)
        store = GeniaStore(destination)

        self.assertEqual(result["components"]["variant_vcf"]["record_count"], 2)
        self.assertEqual(
            result["warnings"],
            [
                "1 GenIA source allele was not indexed for exact matching: "
                "1 contains ambiguous N bases."
            ],
        )
        nc = store.variant("chr1", 100, "A", "G")
        rf = store.variant("1", 101, "c", "t")
        self.assertEqual(nc["records"][0]["class_label"], "Not classified")
        self.assertEqual(nc["records"][0]["relevant_subjects"], 0)
        self.assertEqual(rf["records"][0]["class_label"], "Risk factor")
        self.assertEqual(rf["records"][0]["relevant_subjects"], 2)
        self.assertEqual(store.variant("1", 100, "A", "T")["records"], [])
        self.assertEqual(store.variant("1", 102, "N", "A")["records"], [])
        with sqlite3.connect(destination) as connection:
            rejected = connection.execute(
                "SELECT source_record_id,reason FROM rejected_variants"
            ).fetchall()
        self.assertEqual(rejected[0][0], "GENIA-N")
        self.assertIn("ambiguous N allele", rejected[0][1])

    def test_unreadable_index_requires_explicit_subset_replacement(self) -> None:
        source = self.component_sources()["gei_disease"]
        destination = self.root / "genia.sqlite3"
        destination.write_bytes(b"not a sqlite database")

        with self.assertRaisesRegex(ValueError, "explicitly replace"):
            build_genia_database([source], destination)
        result = build_genia_database(
            [source], destination, replace_unreadable=True,
        )

        self.assertTrue(result["replaced_unreadable"])
        self.assertEqual(set(result["components"]), {"gei_disease"})

    def test_status_rejects_logically_inconsistent_component_metadata(self) -> None:
        source = self.component_sources()["gei_disease"]
        destination = self.root / "genia.sqlite3"
        build_genia_database([source], destination)
        with sqlite3.connect(destination) as connection:
            connection.execute(
                "UPDATE components SET record_count=record_count+1 WHERE id='gei_disease'"
            )

        status = GeniaStore(destination).status()
        self.assertFalse(status["installed"])
        self.assertIn("record count mismatch", status["error"])
        self.assertEqual(GeniaStore(destination).gene("NFKB1")["relationships"], [])

    def test_updates_to_one_database_are_serialized(self) -> None:
        destination = self.root / "genia.sqlite3"
        source = self.root / "unused.tsv"
        source.write_text("unused", encoding="utf-8")
        active = 0
        maximum_active = 0
        guard = threading.Lock()

        def fake_build(*args, **kwargs):
            nonlocal active, maximum_active
            with guard:
                active += 1
                maximum_active = max(maximum_active, active)
            time.sleep(0.05)
            with guard:
                active -= 1
            return {"installed": True}

        with patch.object(
            genia_module, "_build_genia_database_unlocked", side_effect=fake_build,
        ):
            threads = [
                threading.Thread(
                    target=build_genia_database,
                    args=([source], destination),
                )
                for _ in range(2)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=2)

        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(maximum_active, 1)

    def test_incidental_grch38_text_does_not_establish_vcf_assembly(self) -> None:
        source = self.root / "generic.vcf"
        source.write_text(
            "\n".join([
                "##fileformat=VCFv4.2",
                "##source=generated from GRCh38 notes",
                '##INFO=<ID=Variant,Number=1,Type=String,Description="name">',
                '##INFO=<ID=Class,Number=1,Type=String,Description="class">',
                '##INFO=<ID=Relevant_in,Number=1,Type=String,Description="subjects">',
                "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO",
                "1\t100\tGENIA-V1\tA\tG\t.\t.\tVariant=x;Class=P;Relevant_in=1_subject",
                "",
            ]),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(ValueError, "consistently declare GRCh38"):
            detect_component(source)

    def test_top_level_vcf_assembly_declaration_is_accepted(self) -> None:
        source = self.root / "assembly.vcf"
        source.write_text(
            "\n".join([
                "##fileformat=VCFv4.2",
                "##assembly=GRCh38",
                '##INFO=<ID=Variant,Number=1,Type=String,Description="name">',
                '##INFO=<ID=Class,Number=1,Type=String,Description="class">',
                '##INFO=<ID=Relevant_in,Number=1,Type=String,Description="subjects">',
                "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO",
                "1\t100\tGENIA-V1\tA\tG\t.\t.\tVariant=x;Class=P;Relevant_in=1_subject",
                "",
            ]),
            encoding="utf-8",
        )

        self.assertEqual(detect_component(source)["role"], "variant_vcf")

    def test_missing_reference_contig_returns_a_structured_lookup_error(self) -> None:
        source = self.component_sources()["variant_vcf"]
        destination = self.root / "genia.sqlite3"
        build_genia_database([source], destination)
        with sqlite3.connect(destination) as connection:
            connection.execute(
                "UPDATE components SET release_hint='GRCh38;reference_left_aligned' "
                "WHERE id='variant_vcf'"
            )
        reference = self.root / "reference.fa.gz"
        reference.write_bytes(b"placeholder")

        class MissingContigReference:
            def __init__(self, path):
                pass

            def fetch(self, *args):
                raise KeyError("contig 1 is unavailable")

            def close(self):
                pass

        with patch(
            "pipeline.loftee_ptc_50bp.IndexedFasta", MissingContigReference,
        ):
            result = GeniaStore(destination, reference).variant(
                "1", 100, "A", "AA",
            )

        self.assertFalse(result["available"])
        self.assertIn("could not be normalized", result["error"])


class GeneKnowledgeGeniaIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.public_database = self.root / "public.sqlite3"
        with sqlite3.connect(self.public_database) as connection:
            connection.executescript(PUBLIC_SCHEMA)
            connection.execute(
                "INSERT INTO genes VALUES(?,?,?,?,?,?,?)",
                (
                    "HGNC:7794",
                    "NFKB1",
                    "nuclear factor kappa B subunit 1",
                    "ENSG00000109320",
                    "4790",
                    "gene with protein product",
                    "Approved",
                ),
            )
            connection.execute(
                "INSERT INTO gene_aliases VALUES(?,?,?)",
                ("EBP1", "HGNC:7794", "previous"),
            )
        self.genia_database = self.root / "genia.sqlite3"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_gene_store_resolves_alias_joins_components_and_exposes_filter(self) -> None:
        gei = write_table(
            self.root / "relationships.csv", GEI_HEADER, [gei_row()], delimiter=","
        )
        diseases = write_table(
            self.root / "diseases.tsv",
            DISEASE_HEADER,
            [
                disease_row(),
                disease_row(
                    disease_id="202",
                    term="Synthetic non-IEI disease",
                    acronym="OTHER",
                    gene="OTHER1",
                    iei="N",
                ),
            ],
            delimiter="\t",
        )
        associations = write_table(
            self.root / "associations.tsv",
            DISEASE_PHENOTYPE_HEADER,
            [disease_phenotype_row()],
            delimiter="\t",
        )
        vocabulary = write_table(
            self.root / "terms.tsv",
            PHENOTYPE_HEADER,
            [phenotype_row()],
            delimiter="\t",
        )
        build_genia_database(
            [gei, diseases, associations, vocabulary], self.genia_database
        )
        store = GeneKnowledgeStore(
            self.public_database,
            self.root / "omim.sqlite3",
            self.genia_database,
        )

        result = store.gene("EBP1")
        self.assertTrue(result["found"])
        self.assertEqual(result["identity"]["symbol"], "NFKB1")
        self.assertEqual(len(result["genia"]["relationships"]), 1)
        relationship = result["genia"]["relationships"][0]
        self.assertEqual(relationship["disease_id"], "101")
        self.assertEqual(relationship["curation_status"], "curated")
        self.assertEqual(len(relationship["phenotypes"]), 1)
        self.assertEqual(
            relationship["phenotypes"][0]["phenotype_description"],
            "First description line second description line",
        )
        self.assertEqual(store.filter_catalog()["genia_gei_genes"], ["NFKB1"])
        self.assertTrue(store.status()["genia"]["capabilities"]["gene_disease"])

    def test_phenotype_association_file_is_useful_without_other_components(self) -> None:
        associations = write_table(
            self.root / "standalone.tsv",
            DISEASE_PHENOTYPE_HEADER,
            [disease_phenotype_row()],
            delimiter="\t",
        )
        build_genia_database([associations], self.genia_database)

        result = GeniaStore(self.genia_database).gene("NFKB1")
        self.assertEqual(len(result["relationships"]), 1)
        self.assertEqual(
            result["relationships"][0]["relationship_source"],
            "phenotype_export",
        )
        self.assertEqual(len(result["relationships"][0]["phenotypes"]), 1)
        self.assertEqual(GeniaStore(self.genia_database).gei_genes(), [])


if __name__ == "__main__":
    unittest.main()
