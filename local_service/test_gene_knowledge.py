import csv
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from contextlib import closing, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch
from urllib.parse import quote

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from local_service import gencc
from scripts.build_gene_knowledge import fetch_gencc, gencc_release, main as refresh_gene_knowledge
from local_service.gene_knowledge import (
    GeneKnowledgeStore, build_omim_database, build_public_database,
    extract_omim_download_urls,
    summarize_iuis_measure, summarize_iuis_other_cells,
)


class GeneKnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.hgnc = self.root / "hgnc.tsv"
        self.hgnc.write_text(
            "hgnc_id\tsymbol\tname\tlocus_type\tstatus\talias_symbol\tprev_symbol\tentrez_id\tensembl_gene_id\n"
            "HGNC:1\tNFKB1\tnuclear factor\tgene with protein product\tApproved\tNFKB-1|KBF1\tEBP1\t4790\tENSG00000109320\n"
        )
        self.iuis = self.root / "iuis.tsv"
        self.iuis.write_text(
            "source_row\tgene_symbol\tsource_genetic_defect\tdisease\tinheritance\tmechanism\tis_dominant\tis_recessive\tis_x_linked\tomim\tt_cell_count\tb_cell_count\timmunoglobulin_levels\tneutrophil_count\tother_affected_cells\tassociated_features\tmajor_category\tsubcategory\n"
            "2\tNFKB1\tNFKB1\tNFKB1 deficiency\tAD\tGOF\ttrue\tfalse\tfalse\t616576\tNormal\tNormal, low memory B cells\tLow\tNormal\tLow NK\tRecurrent infection and autoimmunity\tImmune dysregulation\tAutoinflammation\n"
        )
        self.validity = self.root / "validity.csv"
        self.validity.write_text(
            '"CLINGEN GENE DISEASE VALIDITY CURATIONS",""\n'
            '"GENE SYMBOL","GENE ID (HGNC)","DISEASE LABEL","DISEASE ID (MONDO)","MOI","SOP","CLASSIFICATION","ONLINE REPORT","CLASSIFICATION DATE","GCEP"\n'
            '"NFKB1","HGNC:1","immunodeficiency","MONDO:1","AD","SOP10","Definitive","https://example.test","2026-01-01","Immune GCEP"\n'
        )
        self.dosage = self.root / "dosage.tsv"
        columns = ["Gene Symbol", "Gene ID", "cytoBand", "Genomic Location", "Haploinsufficiency Score", "Haploinsufficiency Description"]
        columns += [f"Haploinsufficiency PMID{i}" for i in range(1, 7)]
        columns += ["Triplosensitivity Score", "Triplosensitivity Description"]
        columns += [f"Triplosensitivity PMID{i}" for i in range(1, 7)]
        columns += ["Date Last Evaluated", "Haploinsufficiency Disease ID", "Triplosensitivity Disease ID"]
        with self.dosage.open("w", newline="") as handle:
            writer = csv.writer(handle, delimiter="\t")
            writer.writerow(["#" + columns[0], *columns[1:]])
            row = ["NFKB1", "4790", "4q24", "chr4:1-2", "3", "Sufficient evidence", "1", "", "", "", "", "", "0", "No evidence", "", "", "", "", "", "", "2026-01-02", "MONDO:1", ""]
            writer.writerow(row)
        self.public = self.root / "public.sqlite3"
        build_public_database(
            hgnc=self.hgnc, iuis=self.iuis, clingen_validity=self.validity,
            clingen_dosage=self.dosage, destination=self.public,
            releases={key: {"release": "test", "source_url": "https://example.test"} for key in ("hgnc", "iuis", "clingen_validity", "clingen_dosage")},
        )

    def tearDown(self):
        self.temporary.cleanup()

    def test_public_gene_query_resolves_previous_symbol_and_preserves_assertions(self):
        store = GeneKnowledgeStore(self.public, self.root / "private.sqlite3")
        result = store.gene("EBP1")
        self.assertEqual(result["identity"]["symbol"], "NFKB1")
        self.assertEqual(result["iuis"][0]["disease"], "NFKB1 deficiency")
        self.assertEqual(result["iuis"][0]["mechanism"], "GOF")
        self.assertEqual(result["iuis"][0]["b_cell_summary"], "Reduced|Normal|Functional / subset abnormality")
        self.assertEqual(result["iuis"][0]["associated_features"], "Recurrent infection and autoimmunity")
        self.assertEqual(result["clingen_validity"][0]["classification"], "Definitive")
        self.assertEqual(result["clingen_dosage"]["hi_score"], "3")
        self.assertFalse(result["omim_installed"])

    def test_gencc_validators_preserve_case_legacy_cache_and_checksums(self):
        source, headers = self.root / "data.csv", self.root / "headers"
        source.write_bytes(b"test export")
        md5 = hashlib.md5(source.read_bytes()).hexdigest()
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        previous = self.root / "cached.sqlite3"
        for stored, expected in [(md5, f'"{md5}"'), (f'W/"{md5}"', f'W/"{md5}"'),
                                 (f'"{md5}-3"', f'"{md5}-3"'), ('"MiXeD.r2"', '"MiXeD.r2"')]:
            with self.subTest(stored=stored):
                headers.write_text(f"HTTP/1.1 200 OK\nETag: {expected}\n")
                metadata = gencc_release(source, headers, "snapshot")
                self.assertEqual((metadata["etag"], metadata["source_sha256"]), (expected, digest))
                with sqlite3.connect(previous) as db:
                    db.execute("CREATE TABLE IF NOT EXISTS resources(id,release,source_sha256,source_url)")
                    db.execute("CREATE TABLE IF NOT EXISTS metadata(key PRIMARY KEY,value)")
                    db.execute("DELETE FROM resources")
                    db.execute("INSERT INTO resources VALUES('gencc','snapshot',?,?)", (digest, gencc.EXPORT_URL))
                    db.execute("INSERT OR REPLACE INTO metadata VALUES('gencc_etag',?)", (stored,))
                with patch("scripts.build_gene_knowledge.subprocess.run") as run:
                    run.return_value.stdout = "304"
                    cached, meta, reused = fetch_gencc(self.root, previous, "today")
                    self.assertTrue(reused)
                    self.assertEqual((cached, meta["source_sha256"]), (previous, digest))
                    self.assertIn(f"If-None-Match: {expected}", run.call_args.args[0])
        headers.write_text(f'ETag: "{md5}"')
        source.write_bytes(b"different")
        with self.assertRaisesRegex(ValueError, "checksum"):
            gencc_release(source, headers, "snapshot")

    def test_gencc_versions_conflicts_withdrawals_and_failed_refresh(self):
        export = self.root / "gencc.csv"
        store = GeneKnowledgeStore(self.public, self.root / "private.sqlite")
        self.assertFalse(store.gene("EBP1")["gencc"]["available"])  # Older bundle.
        def submission(id_, classification, submitter, *, version=1, moi="HP:0000006", disease="MONDO:1"):
            return dict(sgc_id=f"SGC-{id_}", version_number=version, gene_curie="HGNC:1",
                        gene_symbol="OLD", disease_curie=disease, disease_title="Condition",
                        disease_original_curie="OMIM:123456", disease_original_title="Original name",
                        moi_curie=moi, moi_title="Inheritance", classification_curie="GENCC:" + classification,
                        classification_title=classification, submitter_curie=submitter, submitter_title=submitter,
                        submitted_as_date="2025-10-10", submitted_as_public_report_url="https://example.org",
                        submitted_as_disease_id="OMIM:123456", submitted_as_disease_name="Source disease",
                        submitted_as_pmids="1")
        def rebuild(rows):
            with export.open("w", newline="") as handle:
                writer = csv.DictWriter(handle, gencc.COLUMNS)
                writer.writeheader()
                writer.writerows(rows)
            build_public_database(hgnc=self.hgnc, iuis=self.iuis, clingen_validity=self.validity,
                                  clingen_dosage=self.dosage, gencc_source=export, destination=self.public,
                                  releases={"gencc": {"release": "test", "source_url": gencc.EXPORT_URL}})
        high = submission(100, "100001", "ClinGen", version=2)
        low = submission(101, "100004", "Other")
        supportive = submission(102, "100009", "Third")
        rec = submission(103, "100005", "Other", moi="HP:0000007")
        second = submission(104, "100006", "Other", disease="MONDO:2")
        legacy = self.root / "legacy.sqlite3"
        shutil.copyfile(self.public, legacy)
        rebuild([high, submission(100, "100004", "ClinGen"), low, supportive, rec, second])
        groups = store.gene("EBP1")["gencc"]["groups"]
        self.assertEqual(len(groups), 3)
        with closing(sqlite3.connect(self.public)) as db:
            for factory in (None, sqlite3.Row, lambda cursor, row: {"custom": row}):
                with self.subTest(factory=factory):
                    db.row_factory = factory
                    result = gencc.gene(db, "HGNC:1")
                    self.assertEqual(result, {"available": True, "release": "test", "groups": groups})
                    self.assertIs(db.row_factory, factory)
        upgraded = GeneKnowledgeStore(legacy, self.root / "private.sqlite", bundled_database=self.public)
        self.assertEqual(upgraded.gene("EBP1")["gencc"]["groups"], groups)
        self.assertEqual([r["id"] for r in upgraded.status()["resources"]].count("gencc"), 1)
        self.assertEqual(upgraded.public_database, legacy)
        flagged, = [g for g in groups if g["conflict"]]
        self.assertEqual(len(flagged["submissions"]), 3)
        self.assertEqual(next(r for r in flagged["submissions"] if r["sgc_id"]=="SGC-100")["version_number"], 2)
        self.assertEqual(store.gene("UNRESOLVED")["gencc"]["groups"], [])
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            rebuild([high, high])
        self.assertEqual(store.gene("HGNC:1")["gencc"]["groups"], groups)
        prior_bundle = self.root / "prior-bundle.sqlite3"
        shutil.copyfile(self.public, prior_bundle)
        rebuild([supportive, high])
        # A newer override takes precedence over the older bundled submissions.
        preferred = GeneKnowledgeStore(self.public, self.root / "private.sqlite", bundled_database=prior_bundle)
        self.assertEqual(preferred.gene("EBP1")["gencc"], store.gene("EBP1")["gencc"])
        refreshed, = store.gene("ENSG00000109320")["gencc"]["groups"]
        self.assertFalse(refreshed["conflict"])
        self.assertEqual(len(refreshed["submissions"]), 2)

    def test_cli_replaces_unusable_cache_and_reuses_healthy_cache(self):
        argv = ["build_gene_knowledge", "--fetch-gencc", "--release-date", "today",
                "--output", str(self.public), "--hgnc", str(self.hgnc), "--iuis", str(self.iuis),
                "--clingen-validity", str(self.validity), "--clingen-dosage", str(self.dosage)]
        export = StringIO()
        writer = csv.DictWriter(export, gencc.COLUMNS)
        writer.writeheader()
        submission = dict.fromkeys(gencc.COLUMNS, "fixture")
        submission.update(sgc_id="SGC-1", version_number=1, gene_curie="HGNC:1")
        writer.writerow(submission)
        validator = 'If-None-Match: W/"r1"'
        source = self.root / "source.csv"
        source.write_bytes(export.getvalue().encode())
        healthy = self.root / "healthy.sqlite3"
        build_public_database(hgnc=self.hgnc, iuis=self.iuis, clingen_validity=self.validity,
                              clingen_dosage=self.dosage, destination=healthy, gencc_source=source,
                              releases={"gencc": {"release": "cached", "source_url": gencc.EXPORT_URL,
                                                  "etag": 'W/"r1"'}})

        def download(command, **kwargs):
            if "-H" in command:
                return subprocess.CompletedProcess(command, 0, stdout="304")
            Path(command[command.index("-D") + 1]).write_text('ETag: W/"r1"\n')
            Path(command[command.index("-o") + 1]).write_bytes(export.getvalue().encode())
            return subprocess.CompletedProcess(command, 0, stdout="200")

        for case, damage, expected in (
            ("non-SQLite file", None, [None]),
            ("obsolete metadata", "DROP TABLE metadata", [None]),
            ("invalid validator type", "UPDATE metadata SET value=x'01' WHERE key='gencc_etag'", [None]),
            ("invalid digest", "UPDATE resources SET source_sha256='' WHERE id='gencc'", [None]),
            ("incompatible submissions", "ALTER TABLE gencc RENAME TO old_submissions", [validator, None]),
            ("missing submissions", "DELETE FROM gencc", [validator, None]),
            ("healthy cache", "", [validator]),
        ):
            with self.subTest(case=case):
                shutil.copyfile(healthy, self.public)
                if damage is None:
                    self.public.write_bytes(b"corrupt previous database")
                elif damage:
                    with closing(sqlite3.connect(self.public)) as db:
                        db.execute(damage)
                        db.commit()
                with patch.object(sys, "argv", argv), redirect_stdout(StringIO()), patch(
                    "scripts.build_gene_knowledge.subprocess.run", side_effect=download
                ) as run:
                    refresh_gene_knowledge()
                commands = [call.args[0] for call in run.call_args_list]
                self.assertEqual([cmd[cmd.index("-H") + 1] if "-H" in cmd else None for cmd in commands], expected)
                with closing(sqlite3.connect(self.public)) as db:
                    result = gencc.gene(db, "HGNC:1")
                    self.assertEqual(result["release"], "cached" if case == "healthy cache" else "today")
                    group, = result["groups"]
                    self.assertEqual(group["submissions"][0]["sgc_id"], "SGC-1")
                manifest = json.loads((self.root / "manifest.json").read_text())
                self.assertEqual(manifest["resources"]["gencc"]["source_sha256"],
                                 hashlib.sha256(export.getvalue().encode()).hexdigest())

        # If the unconditional replacement is invalid too, keep both published files.
        with closing(sqlite3.connect(self.public)) as db:
            db.execute("DROP TABLE gencc")
            db.commit()
        manifest_path = self.root / "manifest.json"
        previous_files = self.public.read_bytes(), manifest_path.read_bytes()
        export = StringIO("invalid CSV")
        with patch.object(sys, "argv", argv), patch("scripts.build_gene_knowledge.subprocess.run", side_effect=download):
            with self.assertRaisesRegex(ValueError, "Missing GenCC export columns"):
                refresh_gene_knowledge()
        self.assertEqual((self.public.read_bytes(), manifest_path.read_bytes()), previous_files)

    def test_cli_refresh_destination_follows_annotation_storage(self):
        from scripts.build_gene_knowledge import managed_public_database
        registry = self.root / "storage.json"
        managed = self.root / "selected data"
        managed.mkdir()
        registry.write_text(json.dumps({"version": 1, "annotation_root": str(managed)}))
        with patch.dict(os.environ, {"IEI_WORKBENCH_STORAGE_REGISTRY": str(registry)}):
            self.assertEqual(managed_public_database(), managed.resolve() / "gene-knowledge/gene_knowledge_public.sqlite3")
            managed.rmdir()
            with self.assertRaisesRegex(ValueError, "unavailable"):
                managed_public_database()

    def test_filter_catalog_keeps_source_specific_gene_sets(self):
        catalog = GeneKnowledgeStore(self.public, self.root / "private.sqlite3").filter_catalog()
        self.assertEqual(catalog["iuis_category_genes"]["Immune dysregulation"], ["NFKB1"])
        self.assertNotIn("clingen_classifications", catalog)

    def test_iuis_summary_is_multilabel_and_preserves_mixed_findings(self):
        self.assertEqual(
            summarize_iuis_measure("Normal B cell numbers, low switched memory B cells"),
            ["Reduced", "Normal", "Functional / subset abnormality"],
        )
        self.assertEqual(
            summarize_iuis_measure("Low IgG and IgA with normal or high IgM"),
            ["Reduced", "Increased", "Normal", "Variable / mixed"],
        )
        self.assertEqual(summarize_iuis_measure("Not assessed"), ["Not assessed"])
        self.assertEqual(
            summarize_iuis_other_cells("Low monocytes, DC, NK"),
            ["NK / innate lymphoid", "Myeloid / phagocyte", "Dendritic cell"],
        )

    def test_user_omim_install_is_separate_from_public_bundle(self):
        source = self.root / "omim"
        source.mkdir()
        (source / "mim2gene.txt").write_text(
            "# MIM Number\tMIM Entry Type (see FAQ 1.3 at https://omim.org/help/faq)\tEntrez Gene ID (NCBI)\tApproved Gene Symbol (HGNC)\tEnsembl Gene ID (Ensembl)\n"
            "164011\tgene/phenotype\t4790\tNFKB1\tENSG00000109320\n"
        )
        (source / "mimTitles.txt").write_text(
            "# Prefix\tMIM Number\tPreferred Title; symbol\tAlternative Title(s); symbol(s)\tIncluded Title(s); symbols\n"
            "*\t164011\tNUCLEAR FACTOR OF KAPPA LIGHT POLYPEPTIDE GENE ENHANCER IN B-CELLS 1; NFKB1\t\t\n"
        )
        # morbidmap's format carries no inheritance in the phenotype string;
        # the same association must not be stored a second time because of it.
        (source / "morbidmap.txt").write_text(
            "# Phenotype\tGene/Locus And Other Related Symbols\tMIM Number\tCyto Location\n"
            "Immunodeficiency 123, 616576 (3)\tNFKB1, EBP1\t164011\t4q24\n"
        )
        (source / "genemap2.txt").write_text(
            "# Chromosome\tMim Number\tGene Symbols\tApproved Gene Symbol\tPhenotypes\n"
            "4\t164011\tNFKB1\tNFKB1\tImmunodeficiency 123, 616576 (3), Autosomal dominant\n"
        )
        private = self.root / "private.sqlite3"
        result = build_omim_database(source, private)
        self.assertEqual(result["counts"]["genes"], 1)
        self.assertEqual(result["counts"]["phenotypes"], 1)
        store = GeneKnowledgeStore(self.public, private)
        gene = store.gene("NFKB1")
        self.assertTrue(gene["omim_installed"])
        self.assertEqual(len(gene["omim"]), 1)
        self.assertEqual(gene["omim"][0]["mapping_key"], "3")
        self.assertEqual(gene["omim"][0]["inheritance"], "Autosomal dominant")
        # A database built before the ingestion fix holds the bare morbidmap
        # duplicate; the read-time grouping must still collapse it.
        with sqlite3.connect(private) as connection:
            connection.execute(
                "INSERT INTO phenotypes(gene_mim,symbol,phenotype_mim,phenotype,mapping_key,inheritance,cytoband,raw_phenotype,source_file) "
                "VALUES('164011','NFKB1','616576','Immunodeficiency 123','3','','4q24','Immunodeficiency 123, 616576 (3)','morbidmap.txt')",
            )
            connection.commit()
        legacy = store.gene("NFKB1")
        self.assertEqual(len(legacy["omim"]), 1)
        self.assertEqual(legacy["omim"][0]["inheritance"], "Autosomal dominant")
        with sqlite3.connect(private) as connection:
            self.assertEqual(
                connection.execute("SELECT DISTINCT symbol FROM phenotypes").fetchall(),
                [("NFKB1",)],
            )
            metadata = dict(connection.execute("SELECT key,value FROM metadata"))
            self.assertNotIn("source_dir", metadata)

    def test_extracts_the_four_omim_links_from_a_complete_email_block(self):
        private_base = "https://data.omim.org/downloads/private-account-key"
        direct = {
            "mim2gene.txt": "https://omim.org/static/omim/data/mim2gene.txt",
            "mimTitles.txt": f"{private_base}/mimTitles.txt",
            "genemap2.txt": f"{private_base}/genemap2.txt",
            "morbidmap.txt": f"{private_base}/morbidmap.txt",
        }
        safe = (
            "https://nam02.safelinks.protection.outlook.com/"
            f"?url={quote(direct['genemap2.txt'], safe='')}\\&data=tracking"
        )
        block = "\n".join([
            "OMIM data account activation",
            direct["mim2gene.txt"],
            direct["mimTitles.txt"],
            f"[{safe}]({safe})",
            direct["morbidmap.txt"],
            "https://omim.org/contact",
            "https://aka.ms/LearnAboutSenderIdentification",
        ])
        extracted = extract_omim_download_urls(block)
        self.assertEqual(tuple(extracted), (
            "mim2gene.txt", "mimTitles.txt", "genemap2.txt", "morbidmap.txt",
        ))
        self.assertEqual(extracted["genemap2.txt"], direct["genemap2.txt"])

    def test_missing_omim_link_error_never_echoes_private_input(self):
        secret = "never-show-this-account-key"
        with self.assertRaises(ValueError) as raised:
            extract_omim_download_urls(
                f"https://data.omim.org/downloads/{secret}/mimTitles.txt"
            )
        self.assertNotIn(secret, str(raised.exception))
        self.assertIn("mim2gene.txt", str(raised.exception))

    def test_omim_import_rejects_a_downloaded_html_error_page(self):
        source = self.root / "invalid-omim"
        source.mkdir()
        for filename in ("mim2gene.txt", "mimTitles.txt", "genemap2.txt", "morbidmap.txt"):
            (source / filename).write_text("<!doctype html><title>Access denied</title>")
        destination = self.root / "private.sqlite3"
        destination.write_bytes(b"previous working index")
        with self.assertRaisesRegex(ValueError, "web page instead of OMIM data"):
            build_omim_database(source, destination)
        self.assertEqual(destination.read_bytes(), b"previous working index")


if __name__ == "__main__":
    unittest.main()
