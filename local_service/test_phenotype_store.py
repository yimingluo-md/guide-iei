#!/usr/bin/env python3
import base64
import io
import tempfile
import unittest
import zipfile
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

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

    def test_pages_and_identity_picker_reach_every_individual(self):
        ids = [f"Case-{index:04}" for index in range(502)]
        with self.store._session() as connection:
            # A bulk import can give every row the same timestamp.
            connection.executemany(
                "INSERT INTO phenotype_individuals(individual_id, created_at, updated_at) "
                "VALUES (?, '2026-01-01', '2026-01-01')", [(key,) for key in reversed(ids)]
            )
            connection.executemany(
                "INSERT INTO phenotype_sample_links VALUES (?, ?)",
                [(ids[-1], sample) for sample in ("Sample Z", "Sample A")]
            )
        first = self.store.list_page("Case", 500)
        last = self.store.list_page("Case", 500, 500)
        self.assertTrue(first["has_more"])
        self.assertFalse(last["has_more"])
        self.assertEqual([r["individual_id"] for r in first["individuals"] + last["individuals"]], ids)
        self.assertEqual(last["individuals"][-1], self.store.get(ids[-1]))
        self.assertEqual(last["individuals"][-1]["sample_ids"], ["Sample A", "Sample Z"])
        self.assertEqual(self.store.list_page("Sample Z", 1)["individuals"], [self.store.get(ids[-1])])
        self.assertEqual(self.store.individual_ids(), ids)
        self.assertEqual(self.store.list_page("", 500, 502), {"individuals": [], "has_more": False})
        with self.assertRaises(ValueError):
            self.store.list_page(offset=-1)

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
        content = b"Case,Sample\nDUP,P1\nOK1,P2\nDUP,P2\n"
        payload = {
            "filename": "duplicates.csv",
            "content_base64": encoded(content),
            "mapping": {"individual_id": "Case", "sample_ids": "Sample"},
        }
        validation = self.store.validate(payload)
        self.assertEqual(validation["duplicate_individual_ids"], ["DUP"])
        # The offending spreadsheet rows are named, not just counted.
        self.assertEqual(validation["duplicate_individual_rows"], {"DUP": [1, 3]})
        with self.assertRaisesRegex(ValueError, "duplicate individual IDs"):
            self.store.import_records(payload)

    def test_manual_age_pairs_survive_save_and_update(self):
        def assert_ages(evaluation, onset):
            record = self.store.get("AGE-1")
            self.assertEqual((record["age_at_evaluation"], record["age_at_evaluation_unit"]), evaluation)
            self.assertEqual((record["age_at_onset"], record["age_at_onset_unit"]), onset)

        self.store.save_individual({
            "individual_id": "AGE-1",
            "age_at_evaluation": "180 days",
            "age_at_onset": "24 months",
        })
        assert_ages((180, "days"), (24, "months"))

        self.store.save_individual({
            "individual_id": "AGE-1",
            "age_at_evaluation": 0,
            "age_at_evaluation_unit": " Wk ",
            "age_at_onset": "180 years",
            "age_at_onset_unit": "MO",
        })
        assert_ages((0, "weeks"), (180, "months"))

    def test_manual_blank_and_missing_ages_match_import(self):
        self.store.save_individual({
            "individual_id": "MANUAL", "age_at_evaluation": 3, "age_at_onset": 1,
        })
        self.store.save_individual({
            "individual_id": "MANUAL",
            "age_at_evaluation": "   ",
            "age_at_evaluation_unit": "unknown",
            "age_at_onset_unit": "unknown",
        })
        self.store.import_records({
            "filename": "ages.csv", "header_row": 1,
            "content_base64": encoded(b"ID,Age,Unit,Onset Unit\nIMPORTED, ,unknown,unknown\n"),
            "mapping": {
                "individual_id": "ID", "age_at_evaluation": "Age",
                "age_at_evaluation_unit": "Unit", "age_at_onset_unit": "Onset Unit",
            },
        })
        for individual_id in ("MANUAL", "IMPORTED"):
            record = self.store.get(individual_id)
            for field in ("age_at_evaluation", "age_at_onset"):
                with self.subTest(individual_id=individual_id, field=field):
                    self.assertIsNone(record[field])
                    self.assertEqual(record[f"{field}_unit"], "")

    def test_infant_ages_are_valid_in_their_own_units(self):
        """The 130 ceiling is a YEARS bound: '180 days' is a six-month-old,
        not an out-of-range age — the raw-number check used to reject
        exactly the patients IEI sees most."""
        saved = self.store.save_individual({
            "individual_id": "INFANT-1",
            "age_at_evaluation": "180",
            "age_at_evaluation_unit": "days",
            "age_at_onset": "6",
            "age_at_onset_unit": "weeks",
        })
        self.assertEqual(saved["age_at_evaluation"], 180.0)
        self.assertEqual(saved["age_at_evaluation_unit"], "days")
        # A unit embedded in the value alone is honoured too.
        saved = self.store.save_individual({
            "individual_id": "INFANT-2",
            "age_at_evaluation": "200 days",
        })
        self.assertEqual(saved["age_at_evaluation"], 200.0)
        # The years bound still holds after conversion.
        with self.assertRaisesRegex(ValueError, "outside the supported range"):
            self.store.save_individual({
                "individual_id": "BAD-1",
                "age_at_evaluation": "60000",
                "age_at_evaluation_unit": "days",
            })
        with self.assertRaisesRegex(ValueError, "outside the supported range"):
            self.store.save_individual({
                "individual_id": "BAD-2",
                "age_at_evaluation": "180",
                "age_at_evaluation_unit": "years",
            })

    def test_xlsx_size_and_cell_budgets(self):
        from unittest.mock import patch
        from local_service import phenotype_store as module
        for limit in ("MAX_XLSX_MEMBER_BYTES", "MAX_XLSX_CELLS"):
            with self.subTest(limit=limit), patch.object(module, limit, 2):
                with self.assertRaisesRegex(ValueError, "limit"):
                    parse_table("patients.xlsx", xlsx_bytes())
        self.assertEqual(module._column_number("XFD1"), 16383)
        with self.assertRaisesRegex(ValueError, "limit"):
            module._column_number("XFE1")

    def test_nullable_fields_and_concurrent_nonblank_updates(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        base = self.store.save_individual({"individual_id":"example", "sample_ids": None, "custom_fields": None})
        self.assertEqual(base["sample_ids"], [])
        barrier = Barrier(2)
        def save(key):
            barrier.wait(timeout=5)
            return self.store._upsert({**base, "custom_fields": {key: key}}, "update_nonblank", "test")
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(list(pool.map(save, ["one", "two"])), ["updated", "updated"])
        self.assertEqual(self.store.get("example")["custom_fields"], {"one": "one", "two": "two"})

    def test_xlsx_preview_reads_sheet_and_suggests_columns(self):
        parsed = parse_table("phenotypes.xlsx", xlsx_bytes())
        self.assertEqual(parsed["sheet_names"], ["Patients"])
        self.assertEqual(parsed["row_count"], 1)
        self.assertEqual(parsed["rows"][0]["Patient ID"], "CASE-X")
        self.assertEqual(parsed["suggested_mapping"]["sample_ids"], "VCF Sample")



class AtomicImportTests(unittest.TestCase):
    def test_failed_bulk_import_leaves_no_partial_unaudited_updates(self):
        with tempfile.TemporaryDirectory() as directory:
            store = PhenotypeStore(Path(directory) / "p.sqlite3")
            content = "individual_id,phenotype_summary\nP1,first\nP2,second\n"
            payload = {
                "content_base64": base64.b64encode(content.encode()).decode(),
                "filename": "cases.csv",
                "mapping": {"individual_id": "individual_id",
                            "phenotype_summary": "phenotype_summary"},
            }
            original = store._upsert
            calls = {"n": 0}

            def failing(record, mode, source_name, connection=None):
                calls["n"] += 1
                if calls["n"] == 2:
                    raise RuntimeError("synthetic failure on the second row")
                return original(record, mode, source_name, connection)

            store._upsert = failing
            try:
                with self.assertRaisesRegex(RuntimeError, "second row"):
                    store.import_records(payload)
            finally:
                store._upsert = original
            # Everything rolled back together: no patient rows, no audit run.
            self.assertIsNone(store.get("P1"))
            self.assertIsNone(store.get("P2"))
            import sqlite3
            with sqlite3.connect(Path(directory) / "p.sqlite3") as connection:
                runs = connection.execute(
                    "SELECT COUNT(*) FROM phenotype_import_runs"
                ).fetchone()[0]
            self.assertEqual(runs, 0)

if __name__ == "__main__":
    unittest.main()
