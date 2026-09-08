#!/usr/bin/env python3
"""The shared PromoterAI evidence rule (review M5).

`test/contracts/promoterai_evidence_cases.json` is the contract; the browser
parser is pinned to the same file in `webui/tests/vcf.test.mjs`. The cohort
index and the whole-genome prefilter are then checked against the rule.
"""

from __future__ import annotations

import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pipeline.promoterai_evidence import (  # noqa: E402
    EXACT_MATCH_VALUES,
    SCORE_FIELDS,
    has_promoterai_schema,
    promoterai_observation,
)
from local_service.cohort_store import annotation_from, decode  # noqa: E402
from local_service.wgs_review import (  # noqa: E402
    VcfHeader,
    WgsPrefilterOptions,
    evaluate_record,
)

CASES = json.loads(
    (ROOT / "test" / "contracts" / "promoterai_evidence_cases.json").read_text(
        encoding="utf-8"
    )
)["cases"]

PLUGIN_FIELDS = (
    "Allele", "ALLELE_NUM", "SYMBOL", "Consequence", "MAX_AF",
    "PromoterAI_score", "PromoterAI_TSS", "PromoterAI_strand",
    "PromoterAI_distance", "PromoterAI_source_transcript", "PromoterAI_match",
)


def observation_dict(record):
    observation = promoterai_observation(record)
    return {
        "score": observation.score,
        "usable_score": observation.usable_score,
        "match_status": observation.match_status,
        "match": observation.match,
        "source_transcript": observation.source_transcript,
        "tss": observation.tss,
        "strand": observation.strand,
        "distance": observation.distance,
    }


class SharedContractTests(unittest.TestCase):
    def test_every_fixture_case(self):
        self.assertGreaterEqual(len(CASES), 10)
        for case in CASES:
            with self.subTest(case=case["name"]):
                self.assertEqual(observation_dict(case["record"]), case["expected"])

    def test_field_list_covers_every_legacy_alias_once(self):
        self.assertEqual(SCORE_FIELDS[0], "PromoterAI_score")
        self.assertEqual(len({f.lower() for f in SCORE_FIELDS}), 3)
        self.assertTrue(has_promoterai_schema(("promoterAI_promoterAI",)))
        self.assertTrue(has_promoterai_schema(("PromoterAI_score", "CADD_phred")))
        self.assertFalse(has_promoterai_schema(("PromoterAI_TSS", "PromoterAI_match")))

    def test_exact_match_values_are_the_plugin_and_registry_names(self):
        self.assertEqual(
            EXACT_MATCH_VALUES,
            {"allele_transcript_tss_strand", "exact_version", "stable_id",
             "stable_transcript_id"},
        )


class CohortColumnTests(unittest.TestCase):
    """The legacy `promoterai` column follows the same rule as the normalised
    observation: a score without complete provenance is withheld."""

    def test_score_only_annotation_is_withheld_from_the_legacy_column(self):
        record = {"SYMBOL": "NFKB1", "Consequence": "upstream_gene_variant",
                  "PromoterAI_score": "0.9"}
        annotation = annotation_from(record)
        self.assertIsNone(annotation["promoterai"])
        normalised = [
            item for item in annotation["_predictions"]
            if item["predictor_id"] == "promoterai"
        ]
        self.assertTrue(normalised)
        self.assertNotIn("score", normalised[0]["values"])

    def test_complete_plugin_output_is_stored(self):
        record = {
            "SYMBOL": "NFKB1", "Consequence": "upstream_gene_variant",
            "PromoterAI_score": "-0.91", "PromoterAI_TSS": "154000",
            "PromoterAI_strand": "-1", "PromoterAI_distance": "-120",
            "PromoterAI_source_transcript": "ENST00000374315.1",
            "PromoterAI_match": "exact_version",
        }
        annotation = annotation_from(record)
        self.assertEqual(annotation["promoterai"], -0.91)
        normalised = [
            item for item in annotation["_predictions"]
            if item["predictor_id"] == "promoterai"
        ]
        self.assertEqual(normalised[0]["match_status"], "exact")
        self.assertEqual(normalised[0]["values"]["score"], -0.91)

    def test_legacy_alias_without_provenance_is_withheld(self):
        annotation = annotation_from({"promoterAI_promoterAI": "-0.95"})
        self.assertIsNone(annotation["promoterai"])

    def test_percent_encoded_values_are_decoded_before_the_rule(self):
        record = {
            "PromoterAI_score": "0.9", "PromoterAI_TSS": "500",
            "PromoterAI_strand": "%2B",  # "+"
            "PromoterAI_source_transcript": "ENST00000000002.3",
            "PromoterAI_match": "exact_version",
        }
        self.assertEqual(decode("%2B"), "+")
        self.assertEqual(annotation_from(record)["promoterai"], 0.9)


class NormalizedObservationAgreementTests(unittest.TestCase):
    """Review follow-up: the normalised observation stored with each
    annotation must agree with the legacy column (and therefore with the
    browser and the prefilter, which run the same contract) on legacy and
    external-input cases, not only on standard plugin output."""

    @staticmethod
    def _promoter(annotation):
        return next(
            item for item in annotation["_predictions"]
            if item["predictor_id"] == "promoterai"
        )

    def test_legacy_alias_with_full_provenance_is_exact_everywhere(self):
        record = {
            "SYMBOL": "NFKB1", "Consequence": "upstream_gene_variant",
            "Feature": "ENST00000000002",
            "PROMOTERAI": "0.9", "PromoterAI_TSS": "500", "PromoterAI_strand": "1",
            "PromoterAI_source_transcript": "ENST00000000002.3",
            "PromoterAI_match": "exact_version",
        }
        shared = promoterai_observation(record)
        annotation = annotation_from(record)
        observation = self._promoter(annotation)
        self.assertEqual(shared.usable_score, 0.9)
        self.assertEqual(annotation["promoterai"], 0.9)
        self.assertEqual(observation["match_status"], "exact")
        self.assertEqual(observation["values"]["score"], 0.9)

    def test_dash_sentinel_strand_is_withheld_everywhere(self):
        # VEP's VCF serializer writes a bare "-" for a missing CSQ value; the
        # plugin encodes minus as "-1" precisely for that reason. A literal
        # "-" is therefore absent, not a minus strand.
        record = {
            "SYMBOL": "NFKB1", "Consequence": "upstream_gene_variant",
            "Feature": "ENST00000000002",
            "PromoterAI_score": "0.9", "PromoterAI_TSS": "500",
            "PromoterAI_strand": "-",
            "PromoterAI_source_transcript": "ENST00000000002.3",
            "PromoterAI_match": "exact_version",
        }
        shared = promoterai_observation(record)
        annotation = annotation_from(record)
        observation = self._promoter(annotation)
        self.assertIsNone(shared.usable_score)
        self.assertIsNone(annotation["promoterai"])
        self.assertEqual(observation["match_status"], "partial")
        self.assertEqual(observation["values"], {})
        self.assertEqual(observation["provenance"]["withheld_metrics"], ["score"])

    def test_column_and_observation_agree_on_every_contract_case(self):
        for case in CASES:
            with self.subTest(case=case["name"]):
                record = {"SYMBOL": "G1", "Consequence": "upstream_gene_variant",
                          "Feature": "ENST1", **case["record"]}
                annotation = annotation_from(record)
                expected = case["expected"]["usable_score"]
                self.assertEqual(annotation["promoterai"], expected)
                promoter = [
                    item for item in annotation["_predictions"]
                    if item["predictor_id"] == "promoterai"
                ]
                if expected is None:
                    self.assertTrue(
                        not promoter or "score" not in promoter[0]["values"]
                    )
                else:
                    self.assertEqual(promoter[0]["match_status"], "exact")
                    self.assertEqual(promoter[0]["values"]["score"], expected)


class PrefilterRouteTests(unittest.TestCase):
    """The PromoterAI retention route qualifies on usable scores only."""

    def _record(self, csq_values: dict[str, str], ref="A", alt="G") -> str:
        values = {"Allele": alt, "ALLELE_NUM": "1", "SYMBOL": "NFKB1",
                  "Consequence": "upstream_gene_variant", "MAX_AF": "0.001"}
        values.update(csq_values)
        csq = "|".join(values.get(field, ".") for field in PLUGIN_FIELDS)
        return f"1\t100\t.\t{ref}\t{alt}\t99\tPASS\tCSQ={csq}\tGT\t0/1\n"

    def setUp(self):
        self.header = VcfHeader(("CASE",), PLUGIN_FIELDS, ("1",))
        self.options = WgsPrefilterOptions(noncoding_mode="none")

    def test_score_only_annotation_does_not_qualify(self):
        retain, _ = evaluate_record(
            self._record({"PromoterAI_score": "0.9"}), self.header, self.options, {}
        )
        self.assertFalse(retain)

    def test_complete_plugin_output_qualifies(self):
        retain, _ = evaluate_record(
            self._record({
                "PromoterAI_score": "-0.9", "PromoterAI_TSS": "154000",
                "PromoterAI_strand": "-1", "PromoterAI_distance": "-120",
                "PromoterAI_source_transcript": "ENST00000374315.1",
                "PromoterAI_match": "exact_version",
            }),
            self.header, self.options, {},
        )
        self.assertTrue(retain)

    def test_below_threshold_complete_output_does_not_qualify(self):
        retain, _ = evaluate_record(
            self._record({
                "PromoterAI_score": "0.5", "PromoterAI_TSS": "154000",
                "PromoterAI_strand": "-1",
                "PromoterAI_source_transcript": "ENST00000374315.1",
                "PromoterAI_match": "stable_id",
            }),
            self.header, self.options, {},
        )
        self.assertFalse(retain)

    def test_unscored_promoter_indel_route_still_uses_schema_presence(self):
        # The "dataset present but this indel unscored" route depends on the
        # schema, not on a usable score, and is unchanged.
        deletion = self._record({}, ref="AT", alt="A")
        retain, reasons = evaluate_record(
            deletion, self.header, self.options, {}, {}, {"1": ((90, 110),)}
        )
        self.assertTrue(retain)
        self.assertEqual(reasons, (("PromoterAI_promoter",),))


if __name__ == "__main__":
    unittest.main()
