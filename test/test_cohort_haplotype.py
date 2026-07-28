#!/usr/bin/env python3
"""Cohort-index coverage for haplotype and ClinVar-conflict evidence."""

from __future__ import annotations

import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from local_service.cohort_store import CohortStore  # noqa: E402


class CohortHaplotypeTests(unittest.TestCase):
    def test_confirmed_restoration_and_clinvar_conflict_are_queryable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            vcf = root / "sample.vcf"
            database = root / "cohort.sqlite3"
            csq_fields = [
                "Allele", "Consequence", "IMPACT", "SYMBOL", "Feature",
                "HGVSc", "HGVSp", "MANE_SELECT", "PICK",
                "ClinVar_CLNSIG", "ClinVar_CLNSIGCONF",
            ]
            event = (
                "1:300:A:AT|S1|ENST1|FRAME_RESTORED_CONFIRMED|"
                "1:315:AG:A|ENSP1:10AB%3ECD"
            )
            csq = (
                "AT|frameshift_variant|HIGH|GENE1|ENST1|ENST1:c.30dup|"
                "ENSP1:p.X11fs|NM_1|1|Conflicting_classifications_of_pathogenicity|"
                "Pathogenic(1)%26Uncertain_significance(2)"
            )
            vcf.write_text(
                "##fileformat=VCFv4.2\n"
                "##reference=GRCh38\n"
                "##contig=<ID=1,length=248956422>\n"
                f'##INFO=<ID=CSQ,Number=.,Type=String,Description="Format: {"|".join(csq_fields)}">\n'
                "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\n"
                f"1\t300\t.\tA\tAT\t99\tPASS\tCSQ={csq};"
                f"IEI_HAPLOTYPE_FRAME={event}\tGT:DP:GQ:AD\t1/1:30:99:0,30\n",
                encoding="utf-8",
            )

            store = CohortStore(database)
            imported = store.import_vcf(vcf)
            self.assertEqual(imported["carrier_count"], 1)
            exact = store.query({
                "mode": "variant", "query": "1:300:A:AT",
            })
            self.assertEqual(
                exact["rows"][0]["haplotype_frame_status"],
                "FRAME_RESTORED_CONFIRMED",
            )
            visible = store.query({"mode": "gene", "gene": "GENE1"})
            self.assertEqual(visible["total"], 1)
            excluded = store.query({
                "mode": "gene",
                "gene": "GENE1",
                "exclude_confirmed_frame_restored": True,
            })
            self.assertEqual(excluded["total"], 0)
            conflict = store.query({
                "mode": "gene",
                "gene": "GENE1",
                "exclude_confirmed_frame_restored": False,
                "clinvar_conflict_pathogenic_only": True,
            })
            self.assertEqual(conflict["total"], 1)
            self.assertIn("Pathogenic(1)", conflict["rows"][0]["clinvar_conflicting"])


if __name__ == "__main__":
    unittest.main()
