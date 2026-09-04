#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from local_service.cohort_store import (
    PREDICTOR_REGISTRY,
    CohortStore,
    _clingen_assertions_for_alt,
    annotation_from,
    parse_genotype,
)


PREDICTOR_FIELDS = [
    "Allele", "ALLELE_NUM", "Consequence", "IMPACT", "SYMBOL", "Gene",
    "Feature", "HGVSc", "HGVSp", "Protein_position", "Amino_acids", "PICK",
    "CADD_phred", "CADD_raw", "LoGoFunc_prediction", "LoGoFunc_neutral",
    "LoGoFunc_GOF", "LoGoFunc_LOF", "LoGoFunc_allele_available",
    "LoGoFunc_source_transcript", "LoGoFunc_source_HGVSp", "LoGoFunc_match",
    "FuncVEP_CTI", "FuncVEP_CTE", "FuncVEP_SP",
    "FuncVEP_allele_available", "FuncVEP_match", "FuncVEP_match_status",
    "FuncVEP_source_gene",
]


def _csq(**values):
    return "|".join(str(values.get(field, "")) for field in PREDICTOR_FIELDS)


def write_predictor_vcf(path: Path) -> None:
    partial_logofunc = _csq(
        Allele="G", ALLELE_NUM=1, Consequence="missense_variant",
        IMPACT="MODERATE", SYMBOL="GENE1", Gene="ENSG000001",
        Feature="ENST_MANE", HGVSc="c.1A>G", HGVSp="ENSP1:p.Lys1Arg",
        Protein_position=1, Amino_acids="K/R", PICK=1,
        CADD_phred=31, CADD_raw=5.1, LoGoFunc_allele_available=1,
        LoGoFunc_source_transcript="ENST_SOURCE",
        LoGoFunc_source_HGVSp="ENSP1:p.Lys1Arg",
        LoGoFunc_match="allele_only",
        FuncVEP_CTI=0.912, FuncVEP_CTE=0.731, FuncVEP_SP=0.445,
        FuncVEP_allele_available=1, FuncVEP_match="allele_gene",
        FuncVEP_match_status="exact", FuncVEP_source_gene="ENSG000001",
    )
    exact_logofunc = _csq(
        Allele="G", ALLELE_NUM=1, Consequence="missense_variant",
        IMPACT="MODERATE", SYMBOL="GENE1", Gene="ENSG000001",
        Feature="ENST_SOURCE", HGVSc="c.1A>G", HGVSp="ENSP1:p.Lys1Arg",
        Protein_position=1, Amino_acids="K/R", CADD_phred=32, CADD_raw=5.2,
        LoGoFunc_prediction="GOF", LoGoFunc_neutral=0.05,
        LoGoFunc_GOF=0.9, LoGoFunc_LOF=0.05,
        LoGoFunc_allele_available=1,
        LoGoFunc_source_transcript="ENST_SOURCE",
        LoGoFunc_source_HGVSp="ENSP1:p.Lys1Arg",
        LoGoFunc_match="allele_transcript_protein",
        FuncVEP_CTI=0.912, FuncVEP_CTE=0.731, FuncVEP_SP=0.445,
        FuncVEP_allele_available=1, FuncVEP_match="allele_gene",
        FuncVEP_match_status="exact", FuncVEP_source_gene="ENSG000001",
    )
    partial_funcvep = _csq(
        Allele="T", ALLELE_NUM=1, Consequence="missense_variant",
        IMPACT="MODERATE", SYMBOL="GENE2", Gene="ENSG000002",
        Feature="ENST000002", HGVSc="c.2C>T", HGVSp="ENSP2:p.Ala2Val",
        Protein_position=2, Amino_acids="A/V", PICK=1,
        LoGoFunc_allele_available=1,
        LoGoFunc_source_transcript="ENST_MISSING",
        LoGoFunc_source_HGVSp="ENSP9:p.Gly9Asp",
        LoGoFunc_match="allele_only",
        FuncVEP_CTI=0.99, FuncVEP_CTE=0.88, FuncVEP_SP=0.77,
        FuncVEP_allele_available=1, FuncVEP_match="allele_only",
        FuncVEP_match_status="partial", FuncVEP_source_gene="ENSG999999",
    )
    haplotype = (
        "1:100:A:G|P1|ENST_SOURCE|FRAME_RESTORED_CONFIRMED|"
        "1:110:AG:A|ENSP1:p.Lys1Arg"
    )
    clingen_detail = (
        "change|CG-1|1%3A101%3AA%3AT|Pathogenic|GENE1|ENST_SOURCE|"
        "1|K|R|Disease%20one"
    )
    genia_detail = (
        "residue|GENIA-1|1%3A102%3AC%3AT|LP|GENE1|ENST_SOURCE|"
        "1|K|Q|"
    )
    clinvar_detail = (
        "residue|CV-1|1%3A103%3AG%3AA|Likely_pathogenic|GENE1|"
        "ENST_SOURCE|1|K|Q|Disease%20two"
    )
    path.write_text(
        "##fileformat=VCFv4.2\n"
        "##reference=GRCh38\n"
        "##contig=<ID=1,length=248956422>\n"
        '##INFO=<ID=CSQ,Number=.,Type=String,Description="Format: '
        + "|".join(PREDICTOR_FIELDS) + '">\n'
        '##INFO=<ID=ClinVar_path_aa_match,Number=A,Type=Integer,Description="test">\n'
        '##INFO=<ID=ClinVar_path_aa_change_match,Number=A,Type=Integer,Description="test">\n'
        '##INFO=<ID=ClinVar_path_aa_details,Number=A,Type=String,Description="test">\n'
        '##INFO=<ID=ClinGen_path_aa_match,Number=A,Type=Integer,Description="test">\n'
        '##INFO=<ID=ClinGen_path_aa_change_match,Number=A,Type=Integer,Description="test">\n'
        '##INFO=<ID=ClinGen_path_aa_details,Number=A,Type=String,Description="test">\n'
        '##INFO=<ID=GenIA_path_aa_match,Number=A,Type=Integer,Description="test">\n'
        '##INFO=<ID=GenIA_path_aa_change_match,Number=A,Type=Integer,Description="test">\n'
        '##INFO=<ID=GenIA_path_aa_details,Number=A,Type=String,Description="test">\n'
        '##INFO=<ID=IEI_HAPLOTYPE_FRAME,Number=.,Type=String,Description="test">\n'
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tP1\n"
        f"1\t100\trsDual\tA\tG\t99\tPASS\tCSQ={partial_logofunc},{exact_logofunc};"
        f"ClinVar_path_aa_match=1;ClinVar_path_aa_change_match=0;"
        f"ClinVar_path_aa_details={clinvar_detail};"
        f"ClinGen_path_aa_match=1;ClinGen_path_aa_change_match=1;"
        f"ClinGen_path_aa_details={clingen_detail};"
        f"GenIA_path_aa_match=1;GenIA_path_aa_change_match=0;"
        f"GenIA_path_aa_details={genia_detail};"
        f"IEI_HAPLOTYPE_FRAME={haplotype}"
        "\tGT:PS:AD:DP:GQ\t0|1:7:10,10:20:99\n"
        f"1\t200\t.\tC\tT\t99\tPASS\tCSQ={partial_funcvep}"
        "\tGT:AD:DP:GQ\t0/1:10,10:20:99\n",
        encoding="utf-8",
    )


def write_cadd_vcf(path: Path, score: float | None) -> None:
    fields = [
        "Allele", "ALLELE_NUM", "Consequence", "IMPACT", "SYMBOL",
        "Gene", "Feature", "HGVSc", "HGVSp", "CADD_phred",
    ]
    values = [
        "G", "1", "missense_variant", "MODERATE", "GENE1",
        "ENSG000001", "ENST000001", "c.1A>G", "p.Lys1Arg",
        "" if score is None else str(score),
    ]
    path.write_text(
        "##fileformat=VCFv4.2\n"
        "##reference=GRCh38\n"
        "##contig=<ID=1,length=248956422>\n"
        '##INFO=<ID=CSQ,Number=.,Type=String,Description="Format: '
        + "|".join(fields) + '">\n'
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tP1\n"
        f"1\t100\t.\tA\tG\t99\tPASS\tCSQ={'|'.join(values)}"
        "\tGT:AD:DP:GQ\t0/1:10,10:20:99\n",
        encoding="utf-8",
    )


def write_invalid_metric_vcf(path: Path) -> None:
    fields = [
        "Allele", "ALLELE_NUM", "Consequence", "IMPACT", "SYMBOL",
        "Gene", "Feature", "PICK", "SpliceAI_pred_SYMBOL",
        "SpliceAI_pred_DS_AG", "SpliceAI_pred_DP_AG", "REVEL_score",
    ]
    spliceai = [
        "G", "1", "splice_region_variant", "MODERATE", "GENE1",
        "ENSG000001", "ENST000001", "1", "GENE1", "0.5", "137", "",
    ]
    nonfinite = [
        "T", "1", "missense_variant", "MODERATE", "GENE2",
        "ENSG000002", "ENST000002", "1", "", "", "", "nan",
    ]
    path.write_text(
        "##fileformat=VCFv4.2\n"
        "##reference=GRCh38\n"
        "##contig=<ID=1,length=248956422>\n"
        '##INFO=<ID=CSQ,Number=.,Type=String,Description="Format: '
        + "|".join(fields) + '">\n'
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tP1\n"
        f"1\t100\t.\tA\tG\t99\tPASS\tCSQ={'|'.join(spliceai)}"
        "\tGT:AD:DP:GQ\t0/1:10,10:20:99\n"
        f"1\t200\t.\tC\tT\t99\tPASS\tCSQ={'|'.join(nonfinite)}"
        "\tGT:AD:DP:GQ\t0/1:10,10:20:99\n",
        encoding="utf-8",
    )


class PredictorStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.database = self.root / "cohort.sqlite3"
        self.store = CohortStore(self.database)
        with self.store._session() as connection:
            file_id = connection.execute(
                """
                INSERT INTO cohort_files(path, size_bytes, mtime_ns, imported_at)
                VALUES (?, 10, 20, '2026-08-31T00:00:00+00:00')
                """,
                (str(self.root / "source.vcf"),),
            ).lastrowid
            self.variant_id = connection.execute(
                """
                INSERT INTO cohort_variants(
                    variant_key, chrom, pos, ref, alt
                ) VALUES ('1:100:A:G', '1', 100, 'A', 'G')
                """
            ).lastrowid
            self.annotation_id = connection.execute(
                """
                INSERT INTO cohort_annotations(
                    variant_id, gene, gene_id, transcript, hgvsc, hgvsp,
                    consequence, impact, cadd
                ) VALUES (?, 'GENE1', 'ENSG000001', 'ENST000001',
                          'c.1A>G', 'p.Lys1Arg', 'missense_variant',
                          'MODERATE', 21.5)
                """,
                (self.variant_id,),
            ).lastrowid
            connection.execute(
                "INSERT INTO cohort_samples(file_id, name) VALUES (?, 'P1')",
                (file_id,),
            )

    def tearDown(self):
        self.temp.cleanup()

    def release(self, **updates):
        options = {
            "provider": "Ozcelik Lab",
            "resource_id": "funcvep",
            "release_version": "2026.1",
            "assembly": "GRCh38",
            "source_uri": "https://zenodo.org/records/20595206",
            "checksum_algorithm": "sha256",
            "checksum": "abc123",
            "priority": 50,
            "metadata": {"doi": "10.5281/zenodo.20595206"},
        }
        options.update(updates)
        return self.store.upsert_predictor_release(**options)

    def prediction(self, release_id, **updates):
        options = {
            "release_id": release_id,
            "predictor_id": "funcvep",
            "variant_id": self.variant_id,
            "target_scope": "allele_gene",
            "gene_id": "ENSG000001",
            "gene_symbol": "GENE1",
            "match_status": "exact",
            "provenance": {"line": 42},
            "values": {
                "cti": 0.87,
                "cte": 0.76,
                "allele_available": True,
                "match": "allele_gene",
                "match_status": "exact",
                "source_gene": "ENSG000001",
            },
        }
        options.update(updates)
        return self.store.upsert_prediction(**options)

    def test_existing_database_gains_tables_without_touching_legacy_rows(self):
        with self.store._session() as connection:
            connection.executescript(
                """
                DROP TABLE prediction_values;
                DROP TABLE prediction_observations;
                DROP TABLE predictor_releases;
                """
            )
        reopened = CohortStore(self.database)
        with reopened._session() as connection:
            tables = {
                row[0] for row in connection.execute(
                    """
                    SELECT name FROM sqlite_master
                    WHERE type = 'table' AND name LIKE 'predict%'
                    """
                )
            }
            legacy = connection.execute(
                "SELECT gene, transcript, cadd FROM cohort_annotations"
            ).fetchone()
        self.assertEqual(tables, {
            "predictor_releases", "prediction_observations", "prediction_values",
        })
        self.assertEqual(tuple(legacy), ("GENE1", "ENST000001", 21.5))

    def test_release_checksum_is_immutable_identity_and_uri_must_be_public(self):
        initial = self.release()
        same = self.release(metadata={"revision_note": "verified"})
        changed = self.release(checksum="def456")
        self.assertEqual(initial["id"], same["id"])
        self.assertNotEqual(initial["id"], changed["id"])
        with self.store._session() as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM predictor_releases"
                ).fetchone()[0],
                2,
            )
        with self.assertRaisesRegex(ValueError, "credential-free"):
            self.release(source_uri="https://example.org/file?token=secret")
        with self.assertRaisesRegex(ValueError, "private host"):
            self.release(source_uri="https://127.0.0.1/file")
        with self.assertRaisesRegex(ValueError, "path must not contain credentials"):
            self.release(
                source_uri=(
                    "https://data.omim.org/downloads/"
                    "private-account-key/mimTitles.txt"
                )
            )

    def test_boolean_false_partial_withholding_and_incremental_provenance(self):
        release = self.release()
        initial = self.prediction(
            release["id"],
            provenance={"line": 42},
            values={"cti": 0.87, "allele_available": False},
        )
        self.assertIs(initial["provenance"]["allele_available"], False)
        partial = self.prediction(
            release["id"],
            match_status="partial",
            provenance={"reviewed": True},
            values={"cti": 0.99, "match": "allele_only"},
        )
        self.assertEqual(partial["values"], {})
        self.assertEqual(partial["provenance"]["line"], 42)
        self.assertTrue(partial["provenance"]["reviewed"])
        self.assertEqual(partial["provenance"]["match"], "allele_only")
        self.assertIn("cti", partial["provenance"]["withheld_metrics"])
        restored = self.prediction(
            release["id"], values={"cti": 0.93}, match_status="exact"
        )
        self.assertEqual(restored["values"], {"cti": 0.93})
        self.assertNotIn("withheld_metrics", restored["provenance"])

    def test_phase_set_missing_sentinel_and_pid_fallback(self):
        missing = parse_genotype("GT:PS", "0|1:.", 0)
        fallback = parse_genotype("GT:PS:PID", "0|1:.:block1", 0)
        self.assertEqual(missing["phase_set"], "")
        self.assertEqual(fallback["phase_set"], "block1")

    def test_registry_scoped_prediction_uses_typed_values_and_compact_provenance(self):
        release = self.release()
        initial = self.prediction(release["id"])
        self.assertEqual(initial["values"], {"cte": 0.76, "cti": 0.87})
        self.assertEqual(initial["value_types"], {
            "cte": "number", "cti": "number",
        })
        self.assertTrue(initial["provenance"]["allele_available"])
        self.assertEqual(initial["provenance"]["match"], "allele_gene")
        target = json.loads(initial["target_key"])
        self.assertEqual(target, {
            "alternate": "G", "chromosome": "1", "ensembl_gene": "ENSG000001",
            "position": 100, "reference": "A",
        })
        self.assertNotIn("annotation_id", target)

        updated = self.prediction(
            release["id"], values={"cti": 0.91, "cte": None}
        )
        self.assertEqual(initial["id"], updated["id"])
        self.assertEqual(updated["values"], {"cti": 0.91})
        self.assertEqual(
            len(self.store.query_predictions(
                variant_id=self.variant_id, predictor_id="funcvep"
            )),
            1,
        )

    def test_registry_validation_rejects_bad_scope_metric_range_and_target_key(self):
        release = self.release()
        with self.assertRaisesRegex(ValueError, "target_scope"):
            self.prediction(release["id"], target_scope="allele_gene_transcript")
        with self.assertRaisesRegex(ValueError, "unknown metric"):
            self.prediction(release["id"], values={"made_up": 0.5})
        with self.assertRaisesRegex(ValueError, "between"):
            self.prediction(release["id"], values={"cti": 1.5})
        with self.assertRaisesRegex(ValueError, "derived"):
            self.prediction(release["id"], target_key="arbitrary")
        with self.assertRaisesRegex(ValueError, "ensembl_gene must be a string"):
            self.prediction(
                release["id"], target={"ensembl_gene": 123}
            )
        with self.assertRaisesRegex(ValueError, "contradicts variant_id"):
            self.prediction(
                release["id"], target={
                    "chromosome": "2", "ensembl_gene": "ENSG000001",
                }
            )

    def test_generic_numeric_filter_is_predictor_and_metric_aware(self):
        release = self.release()
        prediction = self.prediction(release["id"])
        self.assertEqual(
            [row["id"] for row in self.store.filter_predictions(
                metric="cti", operator=">=", value=0.8,
                predictor_id="funcvep",
            )],
            [prediction["id"]],
        )
        self.assertEqual(
            self.store.filter_predictions(
                metric="cti", operator=">", value=0.9,
                predictor_id="funcvep",
            ),
            [],
        )
        with self.assertRaisesRegex(ValueError, "not filterable"):
            self.store.filter_predictions(
                metric="match", operator="=", value="allele_gene",
                predictor_id="funcvep",
            )

    def test_prediction_serialization_chunks_legacy_sqlite_variable_limits(self):
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        try:
            connection.executescript("""
                CREATE TABLE observations (
                    id INTEGER PRIMARY KEY,
                    provenance_json TEXT NOT NULL,
                    release_metadata_json TEXT NOT NULL
                );
                CREATE TABLE prediction_values (
                    observation_id INTEGER NOT NULL,
                    metric TEXT NOT NULL,
                    value_type TEXT NOT NULL,
                    numeric_value REAL,
                    text_value TEXT,
                    boolean_value INTEGER,
                    unit TEXT
                );
            """)
            count = 1_001
            connection.executemany(
                "INSERT INTO observations VALUES (?, '{}', '{}')",
                ((index,) for index in range(1, count + 1)),
            )
            connection.executemany(
                "INSERT INTO prediction_values VALUES (?, 'cti', 'number', ?, NULL, NULL, NULL)",
                ((index, index / count) for index in range(1, count + 1)),
            )
            if hasattr(connection, "setlimit"):
                connection.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 999)
            rows = connection.execute(
                "SELECT id, provenance_json, release_metadata_json FROM observations"
            ).fetchall()
            serialized = CohortStore._serialize_prediction_observations(
                connection, rows
            )
            self.assertEqual(len(serialized), count)
            self.assertAlmostEqual(serialized[-1]["values"]["cti"], 1.0)
        finally:
            connection.close()

    def test_full_cohort_reset_removes_observations_but_keeps_release(self):
        release = self.release()
        self.prediction(release["id"])
        self.store._reset_cohort_tables()
        self.assertEqual(self.store.query_predictions(), [])
        with self.store._session() as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM predictor_releases"
                ).fetchone()[0],
                1,
            )

    def test_spliceai_uses_plugin_source_gene_not_unrelated_csq_gene(self):
        annotation = annotation_from({
            "Allele": "G",
            "Consequence": "splice_region_variant",
            "IMPACT": "MODERATE",
            "SYMBOL": "WRONG",
            "Gene": "ENSG_WRONG",
            "Feature": "ENST_WRONG",
            "SpliceAI_pred_SYMBOL": "SOURCE",
            "SpliceAI_pred_DS_AG": "0.91",
            "SpliceAI_pred_DP_AG": "4",
        })
        spliceai = next(
            prediction for prediction in annotation["_predictions"]
            if prediction["predictor_id"] == "spliceai"
        )
        self.assertEqual(spliceai["gene_symbol"], "SOURCE")
        self.assertEqual(spliceai["gene_id"], "")
        self.assertEqual(spliceai["target"], {"gene_symbol": "SOURCE"})
        self.assertEqual(
            spliceai["provenance"]["source_gene_symbol"], "SOURCE"
        )

        missing_source = annotation_from({
            "Allele": "G", "Consequence": "splice_region_variant",
            "IMPACT": "MODERATE", "SYMBOL": "WRONG", "Gene": "ENSG_WRONG",
            "Feature": "ENST_WRONG", "SpliceAI_pred_DS_AG": "0.91",
        })
        missing_prediction = next(
            prediction for prediction in missing_source["_predictions"]
            if prediction["predictor_id"] == "spliceai"
        )
        self.assertEqual(missing_prediction["match_status"], "partial")
        self.assertEqual(missing_prediction["values"], {})
        self.assertEqual(
            missing_prediction["provenance"]["missing_dimensions"],
            ["gene_symbol"],
        )

    def test_every_registry_predictor_normalizes_through_shared_path(self):
        base_record = {
            "Allele": "G", "ALLELE_NUM": "1",
            "Consequence": "missense_variant", "IMPACT": "MODERATE",
            "SYMBOL": "GENE1", "Gene": "ENSG000001",
            "Feature": "ENST000001.1", "Protein_position": "12",
            "Amino_acids": "A/V", "HGVSp": "ENSP1:p.Ala12Val",
            "PromoterAI_TSS": "90", "PromoterAI_strand": "+",
            "PromoterAI_source_transcript": "ENST000001.1",
            "PromoterAI_match": "exact_version",
            "LoGoFunc_source_transcript": "ENST000001.1",
            "LoGoFunc_source_HGVSp": "ENSP1:p.Ala12Val",
            "LoGoFunc_match": "allele_transcript_protein",
            "FuncVEP_match": "allele_gene",
            "FuncVEP_match_status": "exact",
            "SpliceAI_pred_SYMBOL": "GENE1",
        }
        for predictor in PREDICTOR_REGISTRY.predictors:
            annotator = PREDICTOR_REGISTRY.annotators_by_id[
                predictor.annotator_id
            ]
            if annotator.match.scope.value == "sample_haplotype":
                continue
            with self.subTest(predictor=predictor.id):
                record = dict(base_record)
                for metric in predictor.metrics:
                    if metric.value_type.value == "boolean":
                        value = "1"
                    elif metric.value_type.value in {"float", "integer"}:
                        if metric.value_range:
                            value_number = sum(metric.value_range) / 2
                        else:
                            value_number = 1
                        if metric.value_type.value == "integer":
                            value_number = int(value_number)
                        value = str(value_number)
                    else:
                        value = "test"
                    record[metric.field] = value
                # These fields carry match identity, not arbitrary category
                # data, so keep their producer contracts explicit.
                record.update({
                    "PromoterAI_match": "exact_version",
                    "PromoterAI_source_transcript": "ENST000001.1",
                    "PromoterAI_strand": "+",
                    "LoGoFunc_match": "allele_transcript_protein",
                    "LoGoFunc_source_transcript": "ENST000001.1",
                    "LoGoFunc_source_HGVSp": "ENSP1:p.Ala12Val",
                    "FuncVEP_match": "allele_gene",
                    "FuncVEP_match_status": "exact",
                    "SpliceAI_pred_SYMBOL": "GENE1",
                })
                annotation = annotation_from(
                    record, predictors=(predictor,)
                )
                self.assertEqual(len(annotation["_predictions"]), 1)
                normalized = annotation["_predictions"][0]
                self.assertEqual(normalized["predictor_id"], predictor.id)
                self.assertEqual(
                    normalized["target_scope"], annotator.match.scope.value
                )
                self.assertEqual(normalized["match_status"], "exact")

    def test_legacy_clingen_tokens_are_filtered_by_leading_alt(self):
        raw = ",".join((
            "G|u1|CA1|Pathogenic|D1|M1|AD|Panel|2026",
            "G|u2|CA2|Pathogenic|D2|M2|AR|Panel|2026",
            "T|u3|CA3|Pathogenic|D3|M3|AD|Panel|2026",
        ))
        self.assertEqual(len(_clingen_assertions_for_alt(
            raw, alt="G", alt_index=0, alts=("G", "T")
        )), 2)
        self.assertEqual(len(_clingen_assertions_for_alt(
            raw, alt="T", alt_index=1, alts=("G", "T")
        )), 1)

        # Two legacy assertions can equal the number of ALT alleles without
        # being Number=A slots; their leading ALT remains authoritative.
        ambiguous_count = ",".join((
            "G|u1|CA1|Pathogenic|D1|M1|AD|Panel|2026",
            "G|u2|CA2|Pathogenic|D2|M2|AR|Panel|2026",
        ))
        self.assertEqual(len(_clingen_assertions_for_alt(
            ambiguous_count, alt="G", alt_index=0, alts=("G", "T")
        )), 2)
        self.assertEqual(_clingen_assertions_for_alt(
            ambiguous_count, alt="T", alt_index=1, alts=("G", "T")
        ), [])


class PredictorImportIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def _import(self, *, legacy: bool) -> CohortStore:
        vcf = self.root / ("legacy.vcf" if legacy else "staged.vcf")
        write_predictor_vcf(vcf)
        database = self.root / ("legacy.sqlite3" if legacy else "staged.sqlite3")
        store = CohortStore(database)
        environment = {"IEI_COHORT_LEGACY_IMPORT": "1"} if legacy else {}
        with patch.dict(os.environ, environment, clear=False):
            if not legacy:
                os.environ.pop("IEI_COHORT_LEGACY_IMPORT", None)
            store.import_vcf(vcf)
        return store

    def _assert_dual_write(self, store: CohortStore) -> None:
        with store._session() as connection:
            source = connection.execute(
                """
                SELECT cadd, logofunc_prediction, logofunc_gof,
                       logofunc_match
                FROM cohort_annotations WHERE transcript = 'ENST_SOURCE'
                """
            ).fetchone()
            annotation_count = connection.execute(
                "SELECT COUNT(*) FROM cohort_annotations"
            ).fetchone()[0]
        self.assertEqual(tuple(source), (32.0, "GOF", 0.9, "allele_transcript_protein"))
        self.assertEqual(annotation_count, 3)

        logofunc = store.query_predictions(predictor_id="logofunc")
        self.assertEqual({row["match_status"] for row in logofunc}, {"exact", "partial"})
        exact = next(row for row in logofunc if row["match_status"] == "exact")
        partial = next(row for row in logofunc if row["match_status"] == "partial")
        self.assertEqual(exact["transcript_id"], "ENST_SOURCE")
        self.assertIsNotNone(exact["annotation_id"])
        self.assertEqual(exact["values"]["prediction"], "GOF")
        self.assertEqual(exact["values"]["gof"], 0.9)
        self.assertEqual(partial["transcript_id"], "ENST_MISSING")
        self.assertEqual(partial["values"], {})
        self.assertTrue(partial["provenance"]["allele_available"])
        self.assertEqual(partial["provenance"]["match"], "allele_only")
        exact_target = json.loads(exact["target_key"])
        self.assertEqual(exact_target["ensembl_transcript"], "ENST_SOURCE")
        self.assertEqual(exact_target["protein_position"], "1")
        self.assertEqual(
            exact_target["amino_acid_change"], "ENSP1:p.Lys1Arg"
        )

        funcvep = store.query_predictions(predictor_id="funcvep")
        exact_func = next(row for row in funcvep if row["match_status"] == "exact")
        partial_func = next(row for row in funcvep if row["match_status"] == "partial")
        self.assertIsNone(exact_func["annotation_id"])
        self.assertEqual(exact_func["values"], {
            "cte": 0.731, "cti": 0.912, "sp": 0.445,
        })
        self.assertEqual(partial_func["values"], {})
        self.assertEqual(partial_func["provenance"]["source_gene"], "ENSG999999")
        self.assertEqual(partial_func["provenance"]["match"], "allele_only")
        self.assertTrue(partial_func["provenance"]["allele_available"])
        self.assertEqual(
            partial_func["provenance"]["withheld_metrics"],
            ["cti", "cte", "sp"],
        )
        self.assertEqual(
            json.loads(partial_func["target_key"])["ensembl_gene"],
            "ENSG999999",
        )
        self.assertIn(
            "ensembl_gene", partial_func["provenance"]["matched_dimensions"]
        )

        filtered = store.filter_predictions(
            predictor_id="cadd_coding", metric="phred", operator=">=", value=32,
        )
        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0]["target_scope"], "allele")
        self.assertEqual(filtered[0]["transcript_id"], "")
        self.assertTrue(filtered[0]["source_file_id"])
        self.assertTrue(filtered[0]["checksum"])
        self.assertNotIn("?", filtered[0]["source_uri"])

        amino_acid = store.query_predictions(predictor_id="clinvar_aa_match")
        self.assertEqual(len(amino_acid), 1)
        self.assertEqual(amino_acid[0]["values"], {
            "change_match": False, "residue_match": True,
        })
        self.assertEqual(
            amino_acid[0]["value_types"], {
                "change_match": "boolean", "residue_match": "boolean",
            }
        )
        self.assertEqual(
            amino_acid[0]["provenance"]["details"],
            "residue|CV-1|1:103:G:A|Likely_pathogenic|GENE1|"
            "ENST_SOURCE|1|K|Q|Disease two",
        )
        self.assertEqual(
            len(store.filter_predictions(
                predictor_id="clinvar_aa_match",
                metric="residue_match",
                operator="=",
                value=True,
            )),
            1,
        )

        clingen_match = store.query_predictions(
            predictor_id="clingen_aa_match"
        )
        self.assertEqual(len(clingen_match), 1)
        self.assertEqual(clingen_match[0]["values"], {
            "change_match": True, "residue_match": True,
        })
        self.assertEqual(
            clingen_match[0]["provenance"]["details"],
            "change|CG-1|1:101:A:T|Pathogenic|GENE1|ENST_SOURCE|"
            "1|K|R|Disease one",
        )
        self.assertEqual(
            len(store.filter_predictions(
                predictor_id="clingen_aa_match",
                metric="change_match",
                operator="=",
                value=True,
            )),
            1,
        )

        genia_match = store.query_predictions(predictor_id="genia_aa_match")
        self.assertEqual(len(genia_match), 1)
        self.assertEqual(genia_match[0]["values"], {
            "change_match": False, "residue_match": True,
        })
        self.assertEqual(
            genia_match[0]["provenance"]["details"],
            "residue|GENIA-1|1:102:C:T|LP|GENE1|ENST_SOURCE|1|K|Q|",
        )

        haplotype = store.query_predictions(predictor_id="haplotype_frame")
        self.assertEqual(len(haplotype), 1)
        self.assertEqual(haplotype[0]["match_status"], "exact")
        self.assertEqual(
            haplotype[0]["values"],
            {"frame_evidence": "FRAME_RESTORED_CONFIRMED"},
        )
        self.assertIsNotNone(haplotype[0]["sample_id"])
        self.assertEqual(json.loads(haplotype[0]["target_key"])["phase_set"], "7")

    def test_default_staged_import_dual_writes_predictions(self):
        self._assert_dual_write(self._import(legacy=False))

    def test_legacy_rowwise_import_dual_writes_predictions(self):
        self._assert_dual_write(self._import(legacy=True))

    def test_absent_protein_match_schema_does_not_store_negative_evidence(self):
        vcf = self.root / "no-protein-match-schema.vcf"
        write_cadd_vcf(vcf, 20)
        store = CohortStore(self.root / "no-protein-match-schema.sqlite3")
        store.import_vcf(vcf)
        for predictor_id in (
            "clinvar_aa_match", "clingen_aa_match", "genia_aa_match",
        ):
            with self.subTest(predictor_id=predictor_id):
                self.assertEqual(
                    store.query_predictions(predictor_id=predictor_id), []
                )

    def test_invalid_metrics_do_not_abort_either_cohort_import_path(self):
        for legacy in (False, True):
            with self.subTest(legacy=legacy):
                vcf = self.root / f"invalid-{'legacy' if legacy else 'staged'}.vcf"
                write_invalid_metric_vcf(vcf)
                store = CohortStore(
                    self.root / f"invalid-{'legacy' if legacy else 'staged'}.sqlite3"
                )
                environment = {"IEI_COHORT_LEGACY_IMPORT": "1"} if legacy else {}
                with patch.dict(os.environ, environment, clear=False):
                    if not legacy:
                        os.environ.pop("IEI_COHORT_LEGACY_IMPORT", None)
                    result = store.import_vcf(vcf)

                self.assertEqual(result["variant_count"], 2)
                spliceai = store.query_predictions(predictor_id="spliceai")
                self.assertEqual(len(spliceai), 1)
                self.assertEqual(
                    spliceai[0]["values"], {"delta_acceptor_gain": 0.5}
                )
                self.assertNotIn(
                    "delta_position_acceptor_gain", spliceai[0]["provenance"]
                )
                self.assertEqual(
                    spliceai[0]["provenance"]["invalid_metrics"],
                    ["delta_position_acceptor_gain"],
                )
                self.assertEqual(
                    store.query_predictions(predictor_id="revel"), []
                )

    def test_forced_reimport_removes_stale_source_predictions(self):
        first = self.root / "first.vcf"
        second = self.root / "second.vcf"
        write_cadd_vcf(first, 35)
        write_cadd_vcf(second, 20)
        store = CohortStore(self.root / "stale.sqlite3")
        store.import_vcf(first)
        store.import_vcf(second)
        before = store.query_predictions(predictor_id="cadd_coding")
        self.assertEqual(sorted(row["values"]["phred"] for row in before), [20, 35])
        self.assertEqual(len({row["release_id"] for row in before}), 2)

        second_source_file_id = next(
            row["source_file_id"] for row in before
            if row["values"]["phred"] == 20
        )
        write_cadd_vcf(first, None)
        store.import_vcf(first, force=True)
        after = store.query_predictions(predictor_id="cadd_coding")
        self.assertEqual([row["values"]["phred"] for row in after], [20])
        self.assertEqual(after[0]["source_file_id"], second_source_file_id)

    def test_multiallelic_missing_csq_match_does_not_borrow_other_allele(self):
        fields = [
            "Allele", "ALLELE_NUM", "Consequence", "IMPACT", "SYMBOL",
            "Gene", "Feature", "CADD_phred",
        ]
        consequence = "|".join([
            "G", "1", "missense_variant", "MODERATE", "WRONG",
            "ENSG_WRONG", "ENST_WRONG", "39",
        ])
        vcf = self.root / "multiallelic.vcf"
        vcf.write_text(
            "##fileformat=VCFv4.2\n##reference=GRCh38\n"
            "##contig=<ID=1,length=248956422>\n"
            '##INFO=<ID=CSQ,Number=.,Type=String,Description="Format: '
            + "|".join(fields) + '">\n'
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tP1\n"
            f"1\t300\t.\tA\tG,T\t99\tPASS\tCSQ={consequence}"
            "\tGT:AD:DP:GQ\t0/2:10,0,10:20:99\n",
            encoding="utf-8",
        )
        store = CohortStore(self.root / "multiallelic.sqlite3")
        store.import_vcf(vcf)
        self.assertEqual(store.query_predictions(predictor_id="cadd_coding"), [])
        with store._session() as connection:
            annotation = connection.execute(
                "SELECT gene, transcript, cadd FROM cohort_annotations"
            ).fetchone()
        self.assertEqual(tuple(annotation), ("—", "", None))

    def test_multiallelic_minimized_indel_alleles_bind_when_unambiguous(self):
        fields = [
            "Allele", "Consequence", "IMPACT", "SYMBOL",
            "Gene", "Feature", "CADD_phred",
        ]
        consequences = [
            "T|inframe_insertion|MODERATE|GENEA|ENSGA|ENSTA|20",
            "G|inframe_insertion|MODERATE|GENEB|ENSGB|ENSTB|30",
        ]
        vcf = self.root / "multiallelic-minimized.vcf"
        vcf.write_text(
            "##fileformat=VCFv4.2\n##reference=GRCh38\n"
            "##contig=<ID=1,length=248956422>\n"
            '##INFO=<ID=CSQ,Number=.,Type=String,Description="Format: '
            + "|".join(fields) + '">\n'
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tP1\n"
            f"1\t310\t.\tA\tAT,AG\t99\tPASS\tCSQ={','.join(consequences)}"
            "\tGT:AD\t1/2:0,10,10\n",
            encoding="utf-8",
        )
        store = CohortStore(self.root / "multiallelic-minimized.sqlite3")
        store.import_vcf(vcf)
        with store._session() as connection:
            rows = connection.execute(
                """
                SELECT cohort_variants.alt, cohort_annotations.gene,
                       cohort_annotations.transcript, cohort_annotations.cadd
                FROM cohort_annotations
                JOIN cohort_variants
                  ON cohort_variants.id = cohort_annotations.variant_id
                ORDER BY cohort_variants.alt
                """
            ).fetchall()
        assert [tuple(row) for row in rows] == [
            ("AG", "GENEB", "ENSTB", 30.0),
            ("AT", "GENEA", "ENSTA", 20.0),
        ]

    def test_ambiguous_minimized_indel_alleles_remain_unannotated(self):
        fields = ["Allele", "Consequence", "IMPACT", "SYMBOL"]
        vcf = self.root / "multiallelic-ambiguous.vcf"
        vcf.write_text(
            "##fileformat=VCFv4.2\n##reference=GRCh38\n"
            "##contig=<ID=1,length=248956422>\n"
            '##INFO=<ID=CSQ,Number=.,Type=String,Description="Format: '
            + "|".join(fields) + '">\n'
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tP1\n"
            "1\t320\t.\tCTT\tC,CT\t99\tPASS\t"
            "CSQ=-|frameshift_variant|HIGH|GENEA,"
            "-|inframe_deletion|MODERATE|GENEB\tGT:AD\t1/2:0,10,10\n",
            encoding="utf-8",
        )
        store = CohortStore(self.root / "multiallelic-ambiguous.sqlite3")
        store.import_vcf(vcf)
        with store._session() as connection:
            rows = connection.execute(
                """
                SELECT cohort_variants.alt, cohort_annotations.gene
                FROM cohort_annotations
                JOIN cohort_variants
                  ON cohort_variants.id = cohort_annotations.variant_id
                ORDER BY cohort_variants.alt
                """
            ).fetchall()
        assert [tuple(row) for row in rows] == [("C", "—"), ("CT", "—")]

    def test_multiallelic_info_predictors_are_selected_per_alt(self):
        fields = [
            "Allele", "ALLELE_NUM", "Consequence", "IMPACT", "SYMBOL",
            "Gene", "Feature", "Protein_position", "HGVSp",
        ]
        consequences = [
            "G|1|missense_variant|MODERATE|GENE1|ENSG1|ENST1|10|p.Ala10Gly",
            "T|2|missense_variant|MODERATE|GENE2|ENSG2|ENST2|20|p.Ala20Val",
        ]
        g_assertion = "G|u1|CA1|Pathogenic|D1|M1|AD|Panel|2026"
        t_assertions = "&".join((
            "T|u2|CA2|Pathogenic|D2|M2|AD|Panel|2026",
            "T|u3|CA3|Likely_pathogenic|D3|M3|AR|Panel|2026",
        ))
        genia_detail = (
            "change|G1|1%3A401%3AA%3AC|P|GENE2|ENST2|20|A|V|"
        )
        vcf = self.root / "allele-info.vcf"
        vcf.write_text(
            "##fileformat=VCFv4.2\n##reference=GRCh38\n"
            "##contig=<ID=1,length=248956422>\n"
            '##INFO=<ID=CSQ,Number=.,Type=String,Description="Format: '
            + "|".join(fields) + '">\n'
            '##INFO=<ID=ClinVar_path_aa_match,Number=A,Type=Integer,Description="test">\n'
            '##INFO=<ID=ClinVar_path_aa_change_match,Number=A,Type=Integer,Description="test">\n'
            '##INFO=<ID=ClinGen_ERepo,Number=A,Type=String,Description="test">\n'
            '##INFO=<ID=ClinGen_ERepo_count,Number=A,Type=Integer,Description="test">\n'
            '##INFO=<ID=GenIA_path_aa_match,Number=A,Type=Integer,Description="test">\n'
            '##INFO=<ID=GenIA_path_aa_change_match,Number=A,Type=Integer,Description="test">\n'
            '##INFO=<ID=GenIA_path_aa_details,Number=A,Type=String,Description="test">\n'
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tP1\n"
            f"1\t400\t.\tA\tG,T\t99\tPASS\tCSQ={','.join(consequences)};"
            "ClinVar_path_aa_match=0,1;"
            "ClinVar_path_aa_change_match=0,0;"
            f"ClinGen_ERepo={g_assertion},{t_assertions};"
            "ClinGen_ERepo_count=1,2;"
            "GenIA_path_aa_match=0,1;"
            "GenIA_path_aa_change_match=0,1;"
            f"GenIA_path_aa_details=.,{genia_detail}"
            "\tGT:AD\t1/2:0,10,10\n",
            encoding="utf-8",
        )
        store = CohortStore(self.root / "allele-info.sqlite3")
        store.import_vcf(vcf)
        amino_acid = store.query_predictions(predictor_id="clinvar_aa_match")
        self.assertEqual(len(amino_acid), 2)
        by_alt = {
            json.loads(observation["target_key"])["alternate"]: observation
            for observation in amino_acid
        }
        self.assertEqual(by_alt["G"]["target_scope"], "allele")
        self.assertEqual(by_alt["G"]["gene_symbol"], "")
        self.assertEqual(by_alt["G"]["values"], {
            "change_match": False, "residue_match": False,
        })
        self.assertEqual(by_alt["T"]["values"], {
            "change_match": False, "residue_match": True,
        })

        clingen = store.query_predictions(
            predictor_id="clingen_erepo_assertions"
        )
        self.assertEqual(
            sorted(observation["values"]["count"] for observation in clingen),
            [1.0, 2.0],
        )
        self.assertEqual(
            sorted(
                len(
                    observation["provenance"]["assertions"]
                    .replace("&", ",")
                    .split(",")
                )
                for observation in clingen
            ),
            [1, 2],
        )
        genia_matches = store.query_predictions(predictor_id="genia_aa_match")
        self.assertEqual(len(genia_matches), 2)
        genia_by_alt = {
            json.loads(observation["target_key"])["alternate"]: observation
            for observation in genia_matches
        }
        self.assertEqual(genia_by_alt["G"]["values"], {
            "change_match": False, "residue_match": False,
        })
        self.assertEqual(genia_by_alt["T"]["values"], {
            "change_match": True, "residue_match": True,
        })
        self.assertNotIn("details", genia_by_alt["G"]["provenance"])
        self.assertEqual(
            genia_by_alt["T"]["provenance"]["details"],
            "change|G1|1:401:A:C|P|GENE2|ENST2|20|A|V|",
        )

    def test_haplotype_without_phase_set_is_partial_provenance(self):
        fields = [
            "Allele", "Consequence", "IMPACT", "SYMBOL", "Gene", "Feature",
        ]
        event = (
            "1:500:A:AT|P1|ENST1|FRAME_RESTORING_POSSIBLE_UNPHASED|"
            "1:510:AG:A|ENSP1:p.X10delins"
        )
        vcf = self.root / "haplotype-no-phase-set.vcf"
        vcf.write_text(
            "##fileformat=VCFv4.2\n##reference=GRCh38\n"
            "##contig=<ID=1,length=248956422>\n"
            '##INFO=<ID=CSQ,Number=.,Type=String,Description="Format: '
            + "|".join(fields) + '">\n'
            '##INFO=<ID=IEI_HAPLOTYPE_FRAME,Number=.,Type=String,Description="test">\n'
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tP1\n"
            "1\t500\t.\tA\tAT\t99\tPASS\t"
            f"CSQ=AT|frameshift_variant|HIGH|GENE1|ENSG1|ENST1;"
            f"IEI_HAPLOTYPE_FRAME={event}\tGT:AD\t0/1:10,10\n",
            encoding="utf-8",
        )
        store = CohortStore(self.root / "haplotype-no-phase-set.sqlite3")
        store.import_vcf(vcf)
        observation = store.query_predictions(
            predictor_id="haplotype_frame"
        )[0]
        self.assertEqual(observation["match_status"], "partial")
        self.assertEqual(observation["values"], {})
        self.assertEqual(
            observation["provenance"]["frame_evidence"],
            "FRAME_RESTORING_POSSIBLE_UNPHASED",
        )
        self.assertEqual(
            observation["provenance"]["missing_dimensions"], ["phase_set"]
        )

    def test_incomplete_promoter_scope_is_not_stored_as_exact(self):
        annotation = annotation_from({
            "Allele": "G", "Consequence": "upstream_gene_variant",
            "IMPACT": "MODIFIER", "SYMBOL": "GENE1", "Gene": "ENSG000001",
            "Feature": "ENST000001", "PromoterAI_score": "0.8",
            "PromoterAI_TSS": "90", "PromoterAI_source_transcript": "ENST000001",
            "PromoterAI_match": "exact_version",
        })
        promoter = next(
            item for item in annotation["_predictions"]
            if item["predictor_id"] == "promoterai"
        )
        self.assertEqual(promoter["match_status"], "partial")
        self.assertEqual(promoter["values"], {})
        self.assertEqual(promoter["provenance"]["missing_dimensions"], ["strand"])

    def test_promoter_uses_source_transcript_and_withholds_mismatched_score(self):
        base = {
            "Allele": "G", "Consequence": "upstream_gene_variant",
            "IMPACT": "MODIFIER", "SYMBOL": "GENE1", "Gene": "ENSG000001",
            "Feature": "ENST_WRONG.2", "PromoterAI_score": "-0.8",
            "PromoterAI_TSS": "90", "PromoterAI_strand": "+",
            "PromoterAI_source_transcript": "ENST_SOURCE.1",
        }
        partial = annotation_from({**base, "PromoterAI_match": "allele_only"})
        partial_prediction = next(
            item for item in partial["_predictions"]
            if item["predictor_id"] == "promoterai"
        )
        self.assertEqual(partial_prediction["match_status"], "partial")
        self.assertEqual(partial_prediction["values"], {})
        self.assertEqual(
            partial_prediction["target"]["ensembl_transcript"],
            "ENST_SOURCE",
        )
        self.assertEqual(
            partial_prediction["provenance"]["withheld_metrics"], ["score"]
        )

        exact = annotation_from({**base, "PromoterAI_match": "stable_id"})
        exact_prediction = next(
            item for item in exact["_predictions"]
            if item["predictor_id"] == "promoterai"
        )
        self.assertEqual(exact_prediction["match_status"], "exact")
        self.assertEqual(exact_prediction["values"], {"score": -0.8})
        self.assertFalse(exact_prediction["bind_annotation"])

        negative_strand = annotation_from({
            **base,
            "Feature": "ENST_SOURCE.2",
            "PromoterAI_strand": "-1",
            "PromoterAI_match": "stable_id",
        })
        negative_prediction = next(
            item for item in negative_strand["_predictions"]
            if item["predictor_id"] == "promoterai"
        )
        self.assertEqual(negative_prediction["match_status"], "exact")
        self.assertEqual(negative_prediction["target"]["strand"], "-")
        self.assertEqual(negative_prediction["provenance"]["strand"], "-")
        self.assertTrue(negative_prediction["bind_annotation"])

        legacy_base = {
            key: value for key, value in base.items()
            if key != "PromoterAI_strand"
        }
        legacy_strand = annotation_from({
            **legacy_base,
            "Feature": "ENST_SOURCE.2",
            "STRAND": "-1",
            "PromoterAI_match": "stable_id",
        })
        legacy_prediction = next(
            item for item in legacy_strand["_predictions"]
            if item["predictor_id"] == "promoterai"
        )
        self.assertEqual(legacy_prediction["match_status"], "exact")
        self.assertEqual(legacy_prediction["target"]["strand"], "-")
        self.assertEqual(legacy_prediction["provenance"]["strand"], "-")
        self.assertEqual(legacy_prediction["values"], {"score": -0.8})

        exact_version = annotation_from({
            **base,
            # VEP's default CSQ Feature is unversioned even though the plugin
            # matched the transcript object's exact version.
            "Feature": "ENST_SOURCE",
            "PromoterAI_strand": "+",
            "PromoterAI_match": "exact_version",
        })
        versioned_prediction = next(
            item for item in exact_version["_predictions"]
            if item["predictor_id"] == "promoterai"
        )
        self.assertEqual(
            versioned_prediction["target"]["ensembl_transcript"],
            "ENST_SOURCE.1",
        )
        self.assertTrue(versioned_prediction["bind_annotation"])

        contradictory_version = annotation_from({
            **base,
            "Feature": "ENST_SOURCE.2",
            "PromoterAI_strand": "+",
            "PromoterAI_match": "exact_version",
        })
        contradictory_prediction = next(
            item for item in contradictory_version["_predictions"]
            if item["predictor_id"] == "promoterai"
        )
        self.assertFalse(contradictory_prediction["bind_annotation"])

        malformed = annotation_from({
            **base,
            "PromoterAI_TSS": "bogus",
            "PromoterAI_strand": "?",
            "PromoterAI_match": "exact_version",
        })
        malformed_prediction = next(
            item for item in malformed["_predictions"]
            if item["predictor_id"] == "promoterai"
        )
        self.assertEqual(malformed_prediction["match_status"], "partial")
        self.assertEqual(malformed_prediction["values"], {})
        self.assertEqual(
            malformed_prediction["provenance"]["invalid_dimensions"],
            ["tss", "strand"],
        )


if __name__ == "__main__":
    unittest.main()
