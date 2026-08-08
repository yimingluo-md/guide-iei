#!/usr/bin/env python3
import gzip
import tempfile
import unittest
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from local_service.ccre_context import CcreContextStore
from pipeline.build_gene_tss import build_gene_tss


class CcreContextTests(unittest.TestCase):
    def test_builds_gene_level_tss_and_returns_every_gene_in_window(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            gtf = root / "genes.gtf.gz"
            with gzip.open(gtf, "wt", encoding="utf-8") as handle:
                handle.write(
                    '1\ttest\tgene\t50\t80\t.\t+\t.\tgene_id "ENSG1.2"; gene_name "PLUS"; gene_biotype "protein_coding";\n'
                    '1\ttest\tgene\t350\t400\t.\t-\t.\tgene_id "ENSG2"; gene_name "MINUS"; gene_biotype "lncRNA";\n'
                    '1\ttest\tgene\t700500\t700600\t.\t+\t.\tgene_id "ENSG3"; gene_name "OUTSIDE"; gene_biotype "protein_coding";\n'
                    'GL0001\ttest\tgene\t1\t2\t.\t+\t.\tgene_id "ALT1"; gene_name "ALT";\n'
                    '1\ttest\ttranscript\t10\t20\t.\t+\t.\tgene_id "IGNORED";\n'
                )
            gene_tss = root / "gene_tss.tsv"
            count = build_gene_tss(
                gtf, gene_tss, assembly="GRCh38", release="113"
            )
            self.assertEqual(count, 3)
            table = gene_tss.read_text(encoding="utf-8")
            self.assertIn("1\t50\tPLUS\tENSG1\t+\tprotein_coding", table)
            self.assertIn("1\t400\tMINUS\tENSG2\t-\tlncRNA", table)
            self.assertNotIn("ALT1", table)

            ccre = root / "ccre.bed.gz"
            with gzip.open(ccre, "wt", encoding="utf-8") as handle:
                handle.write("1\t100\t200\tEH38E0000001\tpELS\n")
            store = CcreContextStore()
            result = store.query(
                chrom="chr1", pos=150, ref="A", alt="G",
                ccre_path=ccre, gene_tss_path=gene_tss,
                resource_version="SCREEN Registry V4",
                gene_source="Ensembl release 113 gene-level TSS",
            )
            self.assertEqual(result["status"], "overlap")
            self.assertEqual(result["overlaps"][0]["accession"], "EH38E0000001")
            self.assertEqual(result["overlaps"][0]["class"], "pELS")
            genes = result["overlaps"][0]["nearby_genes"]
            self.assertEqual([gene["symbol"] for gene in genes], ["PLUS", "MINUS"])
            self.assertEqual(genes[0]["distance_bp"], 51)
            self.assertEqual(genes[1]["distance_bp"], 200)
            self.assertNotIn("OUTSIDE", {gene["symbol"] for gene in genes})

            no_overlap = store.query(
                chrom="1", pos=250, ref="A", alt="G",
                ccre_path=ccre, gene_tss_path=gene_tss,
                resource_version="SCREEN Registry V4",
                gene_source="Ensembl release 113 gene-level TSS",
            )
            self.assertEqual(no_overlap["status"], "no_overlap")
            self.assertEqual(no_overlap["overlaps"], [])

    def test_missing_resource_is_not_reported_as_no_overlap(self):
        result = CcreContextStore().query(
            chrom="1", pos=100, ref="A", alt="G",
            ccre_path=None, gene_tss_path=None,
            resource_version="SCREEN Registry V4",
            gene_source="Ensembl release 113 gene-level TSS",
        )
        self.assertEqual(result["status"], "resource_unavailable")
        self.assertFalse(result["resource_available"])


if __name__ == "__main__":
    unittest.main()
