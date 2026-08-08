import argparse
import contextlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.screen_immune_curation import (
    annotate_context_hierarchy,
    choose_contexts,
    compact_experiment,
    parse_cell_ontology,
    prepare_curation,
    profile_curation,
    sha256_file,
    summarize_context_codes,
)


POLICY = {
    "rules": {
        "exclude_not_compliant_categories": [
            "insufficient coverage",
            "poor library complexity",
        ]
    }
}


def experiment(
    accession="ENCSR1",
    *,
    status="released",
    audits=None,
    donor="ENCDO1",
    treatments=None,
    diseases=None,
    health="apparently healthy",
    modifications=None,
    synchronization=None,
):
    biosample = {
        "donor": {"accession": donor} if donor else None,
        "treatments": treatments or [],
        "disease_term_name": diseases or [],
        "health_status": health,
        "applied_modifications": modifications or [],
        "genetic_modifications": [],
        "synchronization": synchronization,
        "life_stage": "adult",
        "sex": "female",
    }
    return {
        "accession": accession,
        "status": status,
        "assay_title": "DNase-seq",
        "biosample_summary": "primary immune cell",
        "biosample_ontology": {
            "term_id": "CL:0001",
            "term_name": "test immune cell",
            "classification": "primary cell",
        },
        "audit": audits or {},
        "replicates": [{"library": {"biosample": biosample, "treatments": []}}],
    }


def selection_profile(index=0, donor="ENCDO1", tier="partial_classification"):
    return {
        "index": index,
        "screen_name": f"profile-{index}",
        "donor_accession": donor,
        "lineage": "T cell",
        "evidence_tier": tier,
        "assays_available": ["DNase"] if tier == "accessibility_only" else ["DNase", "H3K27ac"],
        "assays": {
            "DNase": {"experiment_accession": f"ENCSR{index}"},
            "H3K4me3": None,
            "H3K27ac": None,
            "CTCF": None,
        },
        "metadata": {
            "display_name": "test immune cell",
            "ontology_id": "CL:0001",
            "ontology_name": "test immune cell",
            "sample_type": "primary cell",
            "cell_slims": ["leukocyte", "T cell"],
            "life_stages": ["adult"],
            "sexes": ["female"],
            "treatments": [],
            "diseases": [],
        },
    }


class ScreenImmuneCurationTests(unittest.TestCase):
    def test_cell_ontology_parser_handles_inferred_qualifier(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "cl.obo"
            path.write_text(
                "format-version: 1.2\n"
                "data-version: pinned\n\n"
                "[Term]\n"
                "id: CL:1\n"
                "name: parent\n\n"
                "[Term]\n"
                "id: CL:2\n"
                "name: child\n"
                "is_a: CL:1 {is_inferred=\"true\"} ! parent\n"
            )
            ontology = parse_cell_ontology(path)
            self.assertEqual(ontology["header"]["data-version"], "pinned")
            self.assertEqual(ontology["terms"]["CL:2"]["parents"], ["CL:1"])

    def test_alphagenome_audit_policy_and_state_capture(self):
        warning = compact_experiment(
            experiment(audits={"WARNING": [{"category": "low read depth"}]}),
            "ENCSR1",
            POLICY,
        )
        self.assertTrue(warning["audit_pass"])

        noncritical = compact_experiment(
            experiment(audits={"NOT_COMPLIANT": [{"category": "unrelated category"}]}),
            "ENCSR1",
            POLICY,
        )
        self.assertTrue(noncritical["audit_pass"])
        self.assertEqual(noncritical["tolerated_audit_warnings"], [{
            "severity": "NOT_COMPLIANT", "category": "unrelated category"
        }])

        critical = compact_experiment(
            experiment(audits={"NOT_COMPLIANT": [{"category": "Poor Library Complexity"}]}),
            "ENCSR1",
            POLICY,
        )
        self.assertFalse(critical["audit_pass"])
        self.assertEqual(critical["critical_not_compliant_audits"], ["Poor Library Complexity"])

        error = compact_experiment(
            experiment(
                audits={"ERROR": [{"category": "any error is fatal"}]},
                treatments=[{"treatment_term_name": "interferon gamma"}],
                modifications=[{"method": "CRISPR"}],
                synchronization={"term_name": "G1 arrest"},
            ),
            "ENCSR1",
            POLICY,
        )
        self.assertFalse(error["audit_pass"])
        self.assertEqual(error["treatments"], ["interferon gamma"])
        self.assertTrue(error["has_modifications"])
        self.assertEqual(error["synchronizations"], ["G1 arrest"])

    def test_baseline_profile_requires_unperturbed_primary_cell(self):
        profile = selection_profile()
        clean = compact_experiment(experiment(accession="ENCSR0"), "ENCSR0", POLICY)
        curated = profile_curation(profile, {"ENCSR0": clean})
        self.assertTrue(curated["baseline_eligible"])

        treated = compact_experiment(
            experiment(
                accession="ENCSR0",
                treatments=[{"treatment_term_name": "IL-4"}],
            ),
            "ENCSR0",
            POLICY,
        )
        curated = profile_curation(profile, {"ENCSR0": treated})
        self.assertFalse(curated["baseline_eligible"])
        self.assertIn("treated", curated["exclusion_reasons"])

        adjacent = selection_profile()
        adjacent["metadata"]["cell_slims"] = ["endothelial cell"]
        adjacent["lineage"] = "Other hematopoietic/immune"
        curated = profile_curation(adjacent, {"ENCSR0": clean})
        self.assertFalse(curated["baseline_eligible"])
        self.assertEqual(curated["immune_scope"], "immune_adjacent")
        self.assertIn(
            "not_core_immune_or_hematopoietic_cell", curated["exclusion_reasons"]
        )

    def test_one_deterministic_profile_per_context_and_donor(self):
        profiles = []
        for index, tier in enumerate(("accessibility_only", "partial_classification")):
            row = selection_profile(index=index, donor="ENCDO1", tier=tier)
            compact = compact_experiment(
                experiment(accession=f"ENCSR{index}", donor="ENCDO1"),
                f"ENCSR{index}",
                POLICY,
            )
            profiles.append(profile_curation(row, {f"ENCSR{index}": compact}))
        contexts = choose_contexts(profiles)
        self.assertEqual(len(contexts), 1)
        self.assertEqual(contexts[0]["donor_count"], 1)
        self.assertEqual(contexts[0]["member_profile_indices"], [1])
        self.assertEqual(contexts[0]["members"][0]["duplicate_profile_indices"], [0])

    def test_consensus_preserves_class_and_evidence_completeness(self):
        members = [
            {"profile_index": 0, "donor_accession": "D1", "evidence_tier": "partial_classification"},
            {"profile_index": 1, "donor_accession": "D2", "evidence_tier": "full_classification"},
        ]
        context = {"members": members}
        enhancer = summarize_context_codes(bytes([2, 3]), context)
        self.assertEqual(enhancer["status"], "replicated_enhancer_like")
        self.assertEqual(enhancer["classification_capable_donors"], 2)
        self.assertEqual(enhancer["exact_class_counts"], {"dELS": 1, "pELS": 1})

        mixed = summarize_context_codes(bytes([1, 2]), context)
        self.assertEqual(mixed["status"], "mixed_specific_classes")
        self.assertTrue(mixed["discordant_specific_classes"])

        accessibility_context = {
            "members": [
                {**members[0], "evidence_tier": "accessibility_only"},
                {**members[1], "evidence_tier": "accessibility_only"},
            ]
        }
        accessibility = summarize_context_codes(bytes([7, 7]), accessibility_context)
        self.assertEqual(accessibility["status"], "replicated_accessibility_only")
        self.assertEqual(accessibility["donors_classified"], 0)

        unavailable = summarize_context_codes(bytes([0, 0]), accessibility_context)
        self.assertEqual(unavailable["status"], "no_classifying_evidence_in_context")
        self.assertEqual(unavailable["classification_evidence_status"], "unavailable")

        inactive = summarize_context_codes(bytes([0, 0]), context)
        self.assertEqual(inactive["status"], "not_detected")

    def test_context_hierarchy_marks_nesting_and_shared_donors(self):
        ontology = {
            "terms": {
                "CL:1": {"name": "immune cell", "parents": []},
                "CL:2": {"name": "T cell", "parents": ["CL:1"]},
                "CL:3": {"name": "CD4 T cell", "parents": ["CL:2"]},
            }
        }
        contexts = [
            {"context_id": "cl_1", "ontology_id": "CL:1", "members": [{"donor_accession": "D1"}]},
            {"context_id": "cl_2", "ontology_id": "CL:2", "members": [{"donor_accession": "D1"}]},
            {"context_id": "cl_3", "ontology_id": "CL:3", "members": [{"donor_accession": "D2"}]},
        ]
        annotate_context_hierarchy(contexts, ontology)
        self.assertEqual(contexts[2]["direct_parent_context_ids"], ["cl_2"])
        self.assertEqual(contexts[2]["ancestor_context_ids"], ["cl_1", "cl_2"])
        self.assertTrue(contexts[0]["is_summary_parent"])
        self.assertEqual(
            contexts[0]["related_contexts_sharing_donors"],
            [{"context_id": "cl_2", "shared_donor_count": 1}],
        )

    def test_end_to_end_curation_artifacts(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            selection_path = root / "selection.json"
            policy_path = root / "policy.json"
            metadata_path = root / "metadata.json"
            matrix_path = root / "immune.u8"
            prepared_path = root / "prepared.json"
            output = root / "output"
            ontology_path = root / "cl.obo"
            selection = {
                "registry": "SCREEN Registry V4",
                "assembly": "GRCh38",
                "immune_biosamples": [selection_profile()],
            }
            selection_path.write_text(json.dumps(selection))
            policy_path.write_text(json.dumps({
                "policy_id": "test-policy",
                **POLICY,
            }))
            compact = compact_experiment(
                experiment(accession="ENCSR0"), "ENCSR0", POLICY
            )
            metadata_path.write_text(json.dumps({
                "complete": True,
                "selection_sha256": sha256_file(selection_path),
                "policy_sha256": sha256_file(policy_path),
                "experiments": {"ENCSR0": compact},
            }))
            matrix_path.write_bytes(bytes([2]))
            prepared_path.write_text(json.dumps({
                "immune_matrix": {
                    "path": str(matrix_path),
                    "shape": [1, 1],
                    "size": 1,
                    "sha256": sha256_file(matrix_path),
                }
            }))
            ontology_path.write_text(
                "format-version: 1.2\n"
                "data-version: test-release\n\n"
                "[Term]\n"
                "id: CL:0001\n"
                "name: test immune cell\n"
            )
            prepare_curation(argparse.Namespace(
                selection=selection_path,
                prepared_manifest=prepared_path,
                experiment_metadata=metadata_path,
                policy=policy_path,
                cell_ontology=ontology_path,
                output_dir=output,
                force=False,
            ))
            manifest = json.loads(
                (output / "screen.registry-v4.immune-contexts.json").read_text()
            )
            self.assertEqual(manifest["counts"]["baseline_contexts"], 1)
            with contextlib.closing(sqlite3.connect(
                output / "screen.registry-v4.immune-contexts.sqlite3"
            )) as connection:
                self.assertEqual(
                    connection.execute("SELECT count(*) FROM context_member").fetchone()[0],
                    1,
                )


if __name__ == "__main__":
    unittest.main()
