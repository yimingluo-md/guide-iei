import csv
import sqlite3
import tempfile
import unittest
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.clingen_erepo_annotate import main as annotate_main
from pipeline.prepare_clingen_erepo import read_rows, scan_clinvar, write_sqlite


FIELDS = [
    "Variation", "ClinVar Variation Id", "Allele Registry Id", "HGVS Expressions",
    "HGNC Gene Symbol", "Disease", "Mondo Id", "Mode of Inheritance", "Assertion",
    "Applied Evidence Codes (Met)", "Applied Evidence Codes (Not Met)",
    "Summary of interpretation", "PubMed Articles", "Expert Panel", "Guideline",
    "Approval Date", "Published Date", "Retracted", "Evidence Repo Link", "Uuid",
]


class ClinGenErepoTests(unittest.TestCase):
    def test_preserves_disease_specific_assertions_and_excludes_retractions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.tsv"
            base = {
                "Variation": "NM_1:c.1A>G", "ClinVar Variation Id": "123",
                "Allele Registry Id": "CA1", "HGVS Expressions": "NC_000001.11:g.100A>G",
                "HGNC Gene Symbol": "GENE", "Mondo Id": "MONDO:1",
                "Mode of Inheritance": "Autosomal dominant inheritance",
                "Applied Evidence Codes (Met)": "PS3", "Applied Evidence Codes (Not Met)": "BS1",
                "Summary of interpretation": "Expert summary", "PubMed Articles": "1",
                "Expert Panel": "Test VCEP", "Guideline": "https://example/guideline",
                "Approval Date": "2026-01-01", "Published Date": "2026-01-02",
                "Evidence Repo Link": "https://example/assertion",
            }
            rows = [
                {**base, "Disease": "Disease A", "Assertion": "Pathogenic", "Retracted": "false", "Uuid": "u1"},
                {**base, "Disease": "Disease B", "Assertion": "Uncertain Significance", "Retracted": "false", "Uuid": "u2"},
                {**base, "Disease": "Disease C", "Assertion": "Likely Pathogenic", "Retracted": "true", "Uuid": "u3"},
            ]
            with source.open("w", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=FIELDS, delimiter="\t")
                writer.writeheader(); writer.writerows(rows)
            clinvar = root / "clinvar.vcf"
            clinvar.write_text(
                "##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
                "1\t100\t123\tA\tG\t.\t.\tCLNVI=ClinGen:CA1\n"
            )
            parsed, _ = read_rows(source)
            by_id, _ = scan_clinvar(clinvar, parsed)
            mappings = {row["Uuid"]: (by_id["123"], "ClinVar Variation ID", "") for row in parsed}
            database = root / "assertions.sqlite3"
            write_sqlite(database, parsed, mappings, {"generated_utc": "now"})
            connection = sqlite3.connect(database)
            self.assertEqual(connection.execute("SELECT count(*) FROM assertions").fetchone()[0], 3)
            self.assertEqual(connection.execute("SELECT count(*) FROM assertions WHERE active=1").fetchone()[0], 2)
            diseases = [row[0] for row in connection.execute("SELECT disease FROM assertions WHERE active=1 ORDER BY disease")]
            self.assertEqual(diseases, ["Disease A", "Disease B"])


if __name__ == "__main__":
    unittest.main()
