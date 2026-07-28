#!/usr/bin/env python3
import base64
import io
import tempfile
import unittest
import zipfile
from pathlib import Path

from local_service.cohort_store import CohortStore
from local_service.phenotype_store import PhenotypeStore, parse_table
from local_service.test_cohort_store import write_vcf


def encoded(value: bytes) -> str:
    return base64.b64encode(value).decode()


def xlsx_bytes() -> bytes:
    target = io.BytesIO()
    with zipfile.ZipFile(target, "w") as archive:
        archive.writestr(
            "xl/workbook.xml",
            """<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
              xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
              <sheets><sheet name="Patients" sheetId="1" r:id="rId1"/></sheets>
            </workbook>""",
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            """<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
              <Relationship Id="rId1"
                Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"
                Target="worksheets/sheet1.xml"/>
            </Relationships>""",
        )
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            """<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
              <sheetData>
                <row r="1">
                  <c r="A1" t="inlineStr"><is><t>Patient ID</t></is></c>
                  <c r="B1" t="inlineStr"><is><t>VCF Sample</t></is></c>
                  <c r="C1" t="inlineStr"><is><t>Reported Race</t></is></c>
                </row>
                <row r="2">
                  <c r="A2" t="inlineStr"><is><t>CASE-X</t></is></c>
                  <c r="B2" t="inlineStr"><is><t>P1</t></is></c>
                  <c r="C2" t="inlineStr"><is><t>Self-described</t></is></c>
                </row>
              </sheetData>
            </worksheet>""",
        )
    return target.getvalue()


class PhenotypeStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.database = self.root / "cohort.sqlite3"
        cohort = CohortStore(self.database)
        vcf = self.root / "cohort.vcf"
        write_vcf(vcf)
        cohort.import_paths([str(vcf)])
        self.store = PhenotypeStore(self.database)

    def tearDown(self):
        self.temp.cleanup()

    def test_manual_individual_keeps_reported_race_and_ethnicity_separate(self):
        saved = self.store.save_individual({
            "individual_id": "CASE-1",
            "sample_ids": ["P1"],
            "sex_at_birth": "F",
            "age_at_evaluation": "12",
            "age_at_evaluation_unit": "years",
            "reported_race": "Race A; Race B",
            "reported_ethnicity": "Ethnicity C",
            "phenotype_summary": "Recurrent bacterial infections",
            "present_features": "pneumonia; low IgG",
            "absent_features": "eczema",
        })
        self.assertEqual(saved["sex_at_birth"], "female")
        self.assertEqual(saved["reported_race"], ["Race A", "Race B"])
        self.assertEqual(saved["reported_ethnicity"], ["Ethnicity C"])
        self.assertEqual(self.store.by_sample("P1")[0]["individual_id"], "CASE-1")
        self.assertEqual(self.store.stats()["matched_samples"], 1)

    def test_csv_preview_validation_import_and_saved_profile(self):
        content = (
            b"Subject ID,VCF Sample,Sex,Reported Race,Reported Ethnicity,"
            b"Clinical Summary,Present Features,Pertinent Negatives,Extra Lab Field\n"
            b"CASE-1,P1,F,Race A,Ethnicity A,Infections,Low IgG,No eczema,IgG 300\n"
            b"CASE-2,FUTURE_SAMPLE,M,Race B,Ethnicity B,Autoinflammation,Fever,,CRP 80\n"
        )
        base = {
            "filename": "phenotypes.csv",
            "content_base64": encoded(content),
        }
        preview = self.store.preview(base)
        self.assertEqual(preview["row_count"], 2)
        self.assertEqual(preview["suggested_mapping"]["individual_id"], "Subject ID")
        self.assertEqual(preview["suggested_mapping"]["reported_race"], "Reported Race")

        payload = {
            **base,
            "header_row": preview["header_row"],
            "mapping": preview["suggested_mapping"],
            "preserve_unmapped": True,
            "profile_name": "Lab export",
            "update_mode": "update_nonblank",
        }
        validation = self.store.validate(payload)
        self.assertEqual(validation["matched_sample_ids"], ["P1"])
        self.assertEqual(validation["unmatched_sample_ids"], ["FUTURE_SAMPLE"])
        self.assertEqual(validation["duplicate_individual_ids"], [])

        imported = self.store.import_records(payload)
        self.assertEqual(imported["created"], 2)
        self.assertEqual(self.store.get("CASE-1")["custom_fields"], {
            "Extra Lab Field": "IgG 300",
        })
        self.assertEqual(self.store.profiles()[0]["name"], "Lab export")

    def test_duplicate_individual_ids_block_import(self):
        content = b"Case,Sample\nDUP,P1\nDUP,P2\n"
        payload = {
            "filename": "duplicates.csv",
            "content_base64": encoded(content),
            "mapping": {"individual_id": "Case", "sample_ids": "Sample"},
        }
        validation = self.store.validate(payload)
        self.assertEqual(validation["duplicate_individual_ids"], ["DUP"])
        with self.assertRaisesRegex(ValueError, "duplicate individual IDs"):
            self.store.import_records(payload)

    def test_xlsx_preview_reads_sheet_and_suggests_columns(self):
        parsed = parse_table("phenotypes.xlsx", xlsx_bytes())
        self.assertEqual(parsed["sheet_names"], ["Patients"])
        self.assertEqual(parsed["row_count"], 1)
        self.assertEqual(parsed["rows"][0]["Patient ID"], "CASE-X")
        self.assertEqual(parsed["suggested_mapping"]["sample_ids"], "VCF Sample")


if __name__ == "__main__":
    unittest.main()
