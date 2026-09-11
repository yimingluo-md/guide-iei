#!/usr/bin/env python3
"""The release inventory is evidence, not an implicit license approval."""
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class EngineAuditTests(unittest.TestCase):
    def test_inventory_is_internally_consistent_and_not_clearance(self):
        report = json.loads((ROOT / "docs/releases/0.6.2-engine-inventory.json").read_text())
        self.assertEqual(report["review_status"], "inventory_not_redistribution_clearance")
        self.assertRegex(report["image_id"], r"^sha256:[0-9a-f]{64}$")
        self.assertEqual(report["binary_package_count"], len(report["packages"]))
        sources = {(row["source"], row["source_version"]) for row in report["packages"]}
        self.assertEqual(report["source_package_count"], len(sources))
        self.assertEqual(sources, {(row["name"], row["version"]) for row in report["source_packages"]})
        self.assertGreater(len(report["cpan_distributions"]), 0)
        self.assertTrue(any("jkOwnLib" in row["path"] for row in report["license_evidence"]))
        for row in report["license_evidence"]:
            self.assertRegex(row["sha256"], r"^[0-9a-f]{64}$")
        self.assertTrue(report["limitations"])


if __name__ == "__main__":
    unittest.main()
