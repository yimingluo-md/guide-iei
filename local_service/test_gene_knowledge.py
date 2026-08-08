import csv
import tempfile
import unittest
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from local_service.gene_knowledge import (
    GeneKnowledgeStore, build_omim_database, build_public_database,
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
        (source / "morbidmap.txt").write_text(
            "# Phenotype\tGene Symbols\tMIM Number\tCyto Location\n"
            "Immunodeficiency 123, 616576 (3), Autosomal dominant\tNFKB1\t164011\t4q24\n"
        )
        (source / "genemap2.txt").write_text(
            "# Chromosome\tMim Number\tGene Symbols\tApproved Gene Symbol\tPhenotypes\n"
            "4\t164011\tNFKB1\tNFKB1\tImmunodeficiency 123, 616576 (3), Autosomal dominant\n"
        )
        private = self.root / "private.sqlite3"
        result = build_omim_database(source, private)
        self.assertEqual(result["counts"]["genes"], 1)
        store = GeneKnowledgeStore(self.public, private)
        gene = store.gene("NFKB1")
        self.assertTrue(gene["omim_installed"])
        self.assertEqual(len(gene["omim"]), 1)
        self.assertEqual(gene["omim"][0]["mapping_key"], "3")


if __name__ == "__main__":
    unittest.main()
