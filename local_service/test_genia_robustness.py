#!/usr/bin/env python3
"""Focused synthetic robustness contracts for the local GenIA backend."""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import local_service.genia as genia_module
from local_service.gene_knowledge import GeneKnowledgeStore, PUBLIC_SCHEMA
from local_service.genia import SCHEMA_VERSION, GeniaStore
from local_service.test_genia import (
    GEI_HEADER,
    PHENOTYPE_HEADER,
    gei_row,
    phenotype_row,
    write_table,
    write_variant_vcf,
)


def write_header_complete_vcf(path: Path, metadata: list[str]) -> Path:
    path.write_text(
        "\n".join(
            [
                "##fileformat=VCFv4.2",
                *metadata,
                '##INFO=<ID=Variant,Number=1,Type=String,Description="Variant short name">',
                '##INFO=<ID=Class,Number=1,Type=String,Description="Classification">',
                '##INFO=<ID=Relevant_in,Number=1,Type=String,Description="Reported subjects">',
                "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO",
                "1\t100\tSYNTHETIC-1\tA\tG\t.\tPASS\t"
                "Variant=p.Synthetic;Class=VUS;Relevant_in=1_subject",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return path


class GeniaRobustnessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.relationships = write_table(
            self.root / "relationships.csv",
            GEI_HEADER,
            [gei_row()],
            delimiter=",",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_concurrent_installs_to_the_same_database_are_serialized(self) -> None:
        """Both partial installs survive a race at the preservation boundary."""
        terms = write_table(
            self.root / "terms.tsv",
            PHENOTYPE_HEADER,
            [phenotype_row()],
            delimiter="\t",
        )
        destination = self.root / "genia.sqlite3"
        store = GeniaStore(destination)
        preservation_boundary = threading.Barrier(2)
        original_copy = genia_module._copy_unselected_components
        errors: list[BaseException] = []

        def synchronized_copy(
            connection: sqlite3.Connection,
            database: Path,
            selected: set[str],
            *args: object,
            **kwargs: object,
        ) -> None:
            # Snapshot existence before synchronizing. Without install
            # serialization both builders deterministically observe an empty
            # destination and the last rename loses the other component.
            existed = database.is_file()
            try:
                preservation_boundary.wait(timeout=0.5)
            except threading.BrokenBarrierError:
                pass
            if existed:
                original_copy(connection, database, selected, *args, **kwargs)

        def install(path: Path) -> None:
            try:
                # Separate facades are realistic for concurrent service
                # requests and require locking by destination, not instance.
                GeniaStore(destination).install([path])
            except BaseException as exc:  # surfaced in the test thread below
                errors.append(exc)

        with patch.object(
            genia_module,
            "_copy_unselected_components",
            side_effect=synchronized_copy,
        ):
            workers = [
                threading.Thread(target=install, args=(path,))
                for path in (self.relationships, terms)
            ]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join(timeout=5)

        self.assertTrue(all(not worker.is_alive() for worker in workers))
        self.assertEqual(errors, [])
        self.assertEqual(
            set(store.status()["components"]),
            {"gei_disease", "phenotype_vocabulary"},
        )

    def test_status_rejects_incompatible_or_internally_inconsistent_indexes(self) -> None:
        baseline = self.root / "baseline.sqlite3"
        GeniaStore(baseline).install([self.relationships])

        corruptions = {
            "schema version": (
                "UPDATE metadata SET value=? WHERE key='schema_version'",
                (str(SCHEMA_VERSION + 1),),
            ),
            "missing component table": (
                "DROP TABLE gei_relationships",
                (),
            ),
            "missing component columns": (
                "ALTER TABLE gei_relationships RENAME TO old_gei_relationships; "
                "CREATE TABLE gei_relationships(source_id TEXT)",
                (),
            ),
            "component count mismatch": (
                "UPDATE components SET record_count=record_count+1 "
                "WHERE id='gei_disease'",
                (),
            ),
            "unknown component": (
                "UPDATE components SET id='synthetic_unknown' "
                "WHERE id='gei_disease'",
                (),
            ),
        }

        for label, (statement, parameters) in corruptions.items():
            with self.subTest(corruption=label):
                damaged = self.root / f"{label.replace(' ', '-')}.sqlite3"
                shutil.copyfile(baseline, damaged)
                with sqlite3.connect(damaged) as connection:
                    if ";" in statement:
                        connection.executescript(statement)
                    else:
                        connection.execute(statement, parameters)
                    connection.commit()

                status = GeniaStore(damaged).status()
                self.assertFalse(status["installed"])
                self.assertEqual(status["components"], {})
                self.assertTrue(
                    all(not available for available in status["capabilities"].values())
                )
                self.assertTrue(status.get("error"))

    def test_partial_repair_of_unreadable_index_requires_explicit_opt_in(self) -> None:
        destination = self.root / "genia.sqlite3"
        damaged_bytes = b"synthetic unreadable sqlite payload"
        destination.write_bytes(damaged_bytes)
        store = GeniaStore(destination)

        with self.assertRaisesRegex(ValueError, "replace_unreadable=True"):
            store.install([self.relationships])
        self.assertEqual(destination.read_bytes(), damaged_bytes)

        invalid_source = self.root / "invalid.csv"
        invalid_source.write_text("not,a,GenIA,schema\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            store.install([invalid_source], replace_unreadable=True)
        self.assertEqual(destination.read_bytes(), damaged_bytes)

        knowledge = GeneKnowledgeStore(
            self.root / "missing-public.sqlite3",
            self.root / "omim.sqlite3",
            destination,
        )
        repaired = knowledge.install_genia(
            [self.relationships], replace_unreadable=True
        )
        self.assertTrue(repaired["installed"])
        self.assertEqual(set(repaired["components"]), {"gei_disease"})
        self.assertEqual(
            GeniaStore(destination).gene("NFKB1")["relationships"][0]["source_id"],
            "NFKB1D",
        )

    def test_variant_lookup_reports_missing_reference_contig_as_unavailable(self) -> None:
        source = write_variant_vcf(self.root / "variants.vcf")
        destination = self.root / "genia.sqlite3"
        GeniaStore(destination).install([source])
        with sqlite3.connect(destination) as connection:
            connection.execute(
                "UPDATE components SET release_hint=? WHERE id='variant_vcf'",
                ("GRCh38;reference_left_aligned",),
            )
            connection.commit()

        reference = self.root / "reference.fa.gz"
        reference.write_bytes(b"synthetic")
        Path(str(reference) + ".fai").write_text("1\t200\t0\t200\t201\n")
        Path(str(reference) + ".gzi").write_bytes(b"synthetic")

        class MissingContigReference:
            def __init__(self, _path: Path):
                pass

            def fetch(self, chrom: str, _start: int, _end: int) -> str:
                raise KeyError(f"contig absent from FASTA index: {chrom}")

            def close(self) -> None:
                pass

        with patch(
            "pipeline.loftee_ptc_50bp.IndexedFasta",
            MissingContigReference,
        ):
            result = GeniaStore(destination, reference).variant(
                "2", 100, "AA", "A"
            )

        self.assertFalse(result["available"])
        self.assertEqual(result["records"], [])
        self.assertIn("contig", result["error"].casefold())

    def test_inspect_rejects_an_arbitrary_non_genia_vcf(self) -> None:
        arbitrary = self.root / "arbitrary.vcf"
        arbitrary.write_text(
            "##fileformat=VCFv4.2\n"
            "##contig=<ID=1,assembly=GRCh38>\n"
            '##INFO=<ID=DP,Number=1,Type=Integer,Description="Depth">\n'
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
            "1\t100\trs1\tA\tG\t.\tPASS\tDP=20\n",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(ValueError, "GenIA|INFO"):
            GeniaStore(self.root / "unused.sqlite3").inspect([arbitrary])

    def test_inspect_uses_structured_grch38_assembly_metadata(self) -> None:
        accepted = write_header_complete_vcf(
            self.root / "accepted.vcf",
            ["##contig=<ID=1,assembly=hg38>"],
        )
        inspection = GeniaStore(self.root / "unused.sqlite3").inspect([accepted])
        self.assertEqual(inspection["selected_components"], ["variant_vcf"])

        rejected_headers = {
            "structured GRCh37": ["##contig=<ID=1,assembly=GRCh37>"],
            "unstructured GRCh38 text": [
                "##source=An unrelated export whose description mentions GRCh38"
            ],
            "conflicting assemblies": [
                "##contig=<ID=1,assembly=GRCh38>",
                "##contig=<ID=2,assembly=GRCh37>",
            ],
        }
        for label, metadata in rejected_headers.items():
            with self.subTest(metadata=label):
                rejected = write_header_complete_vcf(
                    self.root / f"{label.replace(' ', '-')}.vcf",
                    metadata,
                )
                with self.assertRaisesRegex(ValueError, "GRCh38|assembly"):
                    GeniaStore(self.root / "unused.sqlite3").inspect([rejected])


class GeneKnowledgeGeniaRobustnessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.public = self.root / "public.sqlite3"
        with sqlite3.connect(self.public) as connection:
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
                "INSERT INTO iuis_assertions(gene_symbol,disease,mechanism,major_category) "
                "VALUES(?,?,?,?)",
                ("NFKB1", "Public synthetic deficiency", "LOF", "Synthetic IEI"),
            )
            connection.commit()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_public_gene_results_survive_a_genia_query_error(self) -> None:
        store = GeneKnowledgeStore(
            self.public,
            self.root / "omim.sqlite3",
            self.root / "genia.sqlite3",
        )
        with patch.object(
            store.genia,
            "gene",
            side_effect=sqlite3.DatabaseError("synthetic GenIA read failure"),
        ):
            result = store.gene("NFKB1")

        self.assertTrue(result["found"])
        self.assertEqual(result["identity"]["symbol"], "NFKB1")
        self.assertEqual(result["iuis"][0]["disease"], "Public synthetic deficiency")
        self.assertEqual(result["genia"]["relationships"], [])
        self.assertIn("error", result["genia"])

    def test_missing_public_database_still_finds_a_genia_relationship(self) -> None:
        relationships = write_table(
            self.root / "relationships.csv",
            GEI_HEADER,
            [gei_row()],
            delimiter=",",
        )
        genia_database = self.root / "genia.sqlite3"
        GeniaStore(genia_database).install([relationships])
        store = GeneKnowledgeStore(
            self.root / "missing-public.sqlite3",
            self.root / "omim.sqlite3",
            genia_database,
        )

        result = store.gene("NFKB1")

        self.assertTrue(result["found"])
        self.assertIsNone(result["identity"])
        self.assertEqual(
            result["genia"]["relationships"][0]["disease_name"],
            "Synthetic NFKB1 deficiency",
        )


if __name__ == "__main__":
    unittest.main()
