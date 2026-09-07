#!/usr/bin/env python3
"""Offline regression tests for the documentation checker."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location("check_documentation", Path(__file__).resolve().parents[1] / "scripts/check_documentation.py")
DOCS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DOCS)


class DocumentationTests(unittest.TestCase):
    def test_headings_duplicates_and_code(self):
        self.assertEqual(DOCS.anchors("# Title\n## A `word`?\n## A word?\n```bash\n# Not a heading\n```\n"), {"title", "a-word", "a-word-1"})

    def test_local_links_anchors_images_and_external(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "other.md").write_text("# Valid heading\n")
            (root / "image.png").write_bytes(b"synthetic")
            page = root / "page.md"
            page.write_text("[ok](other.md#valid-heading) ![img](image.png) [web](https://example.com)\n[bad](other.md#missing) [gone](gone.md)\n```text\n[ignored](not-real.md)\n```\n")
            errors = DOCS.check_file(page)
            self.assertEqual(len(errors), 2, errors)
            self.assertIn("missing heading", errors[0])
            self.assertIn("missing link target", errors[1])

    def test_shell_blocks_are_syntax_checked_not_executed(self):
        with tempfile.TemporaryDirectory() as folder:
            page = Path(folder) / "page.md"
            marker = Path(folder) / "must-not-exist"
            page.write_text(f'```bash\ntouch "{marker}"\n```\n\n```bash\nif true; then\n```\n')
            errors = DOCS.check_file(page)
            self.assertEqual(len(errors), 1, errors)
            self.assertFalse(marker.exists())

    def test_private_link_error_does_not_echo_secret(self):
        with tempfile.TemporaryDirectory() as folder:
            page = Path(folder) / "page.md"
            page.write_text("https://data.omim.org/downloads/SYNTHETIC-SECRET/file.txt")
            errors = DOCS.check_file(page)
            self.assertEqual(len(errors), 1)
            self.assertNotIn("SYNTHETIC-SECRET", errors[0])

    def test_generated_reference_tracks_metric_changes(self):
        registry = {"annotators": [{"id": "a", "match": {"scope": "allele"}}], "predictors": [{"id": "p", "label": "Predictor", "resource_id": "r", "annotator_id": "a", "metrics": [{"field": "score"}]}]}
        first = DOCS.predictor_reference(registry)
        registry["predictors"][0]["metrics"].append({"field": "extra"})
        self.assertNotEqual(first, DOCS.predictor_reference(registry))

    def test_synthetic_demo_csq_does_not_split_source_labels(self):
        spec = importlib.util.spec_from_file_location("make_demo_vcf", DOCS.ROOT / "scripts/make_demo_vcf.py")
        demo = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(demo)
        fields = demo.CSQ_FIELDS.split("|")
        scored = []
        for record in demo.RECORDS:
            info = record.split("\t")[7]
            consequences = info.split("CSQ=", 1)[1].split(";", 1)[0]
            for consequence in consequences.split(","):
                values = consequence.split("|")
                self.assertEqual(len(values), len(fields))
                entry = dict(zip(fields, values))
                if entry["FuncVEP_CTI"]:
                    scored.append(entry)
        self.assertEqual(len(scored), 1)
        self.assertEqual(scored[0]["FuncVEP_match_status"], "exact")
        self.assertEqual(scored[0]["Gene"], scored[0]["FuncVEP_source_gene"])


if __name__ == "__main__":
    unittest.main()
