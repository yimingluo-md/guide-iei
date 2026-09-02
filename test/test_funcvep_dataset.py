#!/usr/bin/env python3
import binascii
import gzip
import hashlib
import json
import struct
import subprocess
import sys
import tempfile
import unittest
import zipfile
import zlib
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.funcvep_dataset import (  # noqa: E402
    ARCHIVE_NAME,
    MEMBER_NAME,
    OUTPUT_HEADER,
    PINNED_RELEASE,
    SOURCE_HEADER,
    build_manifest,
    prepare_archive,
    publish_bundle,
)
from pipeline.indexed_scores import (  # noqa: E402
    ManifestError,
    validate_manifest,
    validate_manifest_registry_contract,
)
from pipeline.predictor_registry import load_registry  # noqa: E402


def source_row(
    identifier, gene, cti="0.1", cte="0.2", sp="0.3",
    clin_cti="0.4", clin_cte="0.5", clin_sp="0.6",
):
    return "\t".join([
        identifier, gene, cti, cte, sp, clin_cti, clin_cte, clin_sp,
    ])


def write_fixture_archive(directory: Path, rows, header=SOURCE_HEADER, name=ARCHIVE_NAME):
    archive = directory / name
    member_data = ("\t".join(header) + "\n" + "\n".join(rows) + "\n").encode()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as handle:
        handle.writestr(MEMBER_NAME, member_data)
    with zipfile.ZipFile(archive) as handle:
        info = handle.getinfo(MEMBER_NAME)
    pin = replace(
        PINNED_RELEASE,
        archive_size=archive.stat().st_size,
        archive_md5=hashlib.md5(archive.read_bytes()).hexdigest(),
        member_size=info.file_size,
        member_crc32=info.CRC,
        source_header=tuple(header),
    )
    return archive, pin


def bgzf_bytes(content: bytes) -> bytes:
    compressor = zlib.compressobj(level=6, wbits=-15)
    compressed = compressor.compress(content) + compressor.flush()
    fixed = struct.pack("<BBBBLBBH", 31, 139, 8, 4, 0, 0, 255, 6)
    block_size = len(fixed) + 6 + len(compressed) + 8
    extra = b"BC" + struct.pack("<HH", 2, block_size - 1)
    trailer = struct.pack("<LL", binascii.crc32(content) & 0xFFFFFFFF, len(content))
    eof = bytes.fromhex(
        "1f8b08040000000000ff0600424302001b0003000000000000000000"
    )
    return fixed + extra + compressed + trailer + eof


def write_tiny_index(path: Path, contigs):
    names = ("\0".join(contigs) + "\0").encode()
    # The parser needs only the standard TBI header and names. htslib creates
    # the complete bin/linear-index body in production.
    content = b"TBI\x01" + struct.pack(
        "<8i", len(contigs), 0, 1, 2, 2, ord("#"), 0, len(names)
    ) + names
    with gzip.open(path, "wb") as handle:
        handle.write(content)


class FuncVEPPreparationTests(unittest.TestCase):
    def test_release_pin_cli_is_the_download_script_source_of_truth(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [sys.executable, str(root / "pipeline" / "funcvep_dataset.py"), "release-pin"],
            check=True,
            capture_output=True,
            text=True,
        )
        payload = json.loads(result.stdout)
        self.assertEqual(payload["archive_name"], PINNED_RELEASE.archive_name)
        self.assertEqual(payload["archive_url"], PINNED_RELEASE.archive_url)
        self.assertEqual(payload["archive_md5"], PINNED_RELEASE.archive_md5)
        script_text = (root / "scripts" / "download_funcvep.sh").read_text()
        self.assertNotIn(PINNED_RELEASE.archive_md5, script_text)
        self.assertNotIn(PINNED_RELEASE.record_id, script_text)

    def test_download_script_parses_acknowledgement_before_the_config_argument(self):
        root = Path(__file__).resolve().parents[1]
        missing = Path(tempfile.gettempdir()) / "guide-iei-missing-funcvep-config.yaml"
        result = subprocess.run(
            [
                "bash", str(root / "scripts" / "download_funcvep.sh"),
                "--acknowledge-license", str(missing),
            ],
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("annotation config not found", result.stderr)
        self.assertNotIn("terms must be reviewed", result.stderr)

    def test_prepares_sorted_funcvep_only_table_and_preserves_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive, pin = write_fixture_archive(
                root,
                [
                    source_row("X-7-T-C", "ENSG00000000003"),
                    source_row("1-20-G-A", "ENSG00000000002"),
                    source_row("1-3-A-T", "ENSG00000000001.9"),
                    source_row("1-3-A-T", "ENSG00000000002", "0.7", "0.8", "0.9"),
                ],
            )
            output = root / "work" / "scores.tsv"
            stats_path = root / "work" / "stats.json"
            stats = prepare_archive(
                archive,
                output,
                stats_path,
                root / "work",
                pin=pin,
                check_space=False,
            )

            self.assertTrue(archive.is_file(), "the user-supplied ZIP must be preserved")
            self.assertEqual(stats["row_count"], 4)
            self.assertEqual(stats["excluded_columns"], [
                "ClinVEP_CTI", "ClinVEP_CTE", "ClinVEP_SP"
            ])
            self.assertNotIn("path", stats["archive"])
            self.assertFalse(list((root / "work" / "partitions").glob("*.tsv")))
            self.assertFalse(list((root / "work" / "sorted").glob("*.tsv")))
            self.assertEqual(
                output.read_text().splitlines(),
                [
                    OUTPUT_HEADER.rstrip("\n"),
                    "1\t3\tA\tT\tENSG00000000001\t0.1\t0.2\t0.3",
                    "1\t3\tA\tT\tENSG00000000002\t0.7\t0.8\t0.9",
                    "1\t20\tG\tA\tENSG00000000002\t0.1\t0.2\t0.3",
                    "X\t7\tT\tC\tENSG00000000003\t0.1\t0.2\t0.3",
                ],
            )

    def test_rejects_duplicate_key_after_gene_version_normalization(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive, pin = write_fixture_archive(
                root,
                [
                    source_row("1-3-A-T", "ENSG00000000001.1"),
                    source_row("1-3-A-T", "ENSG00000000001.2"),
                ],
            )
            with self.assertRaisesRegex(ValueError, "duplicate FuncVEP allele/gene"):
                prepare_archive(
                    archive,
                    root / "work" / "scores.tsv",
                    root / "work" / "stats.json",
                    root / "work",
                    pin=pin,
                    check_space=False,
                )

    def test_accepts_browser_renamed_archive_after_content_verification(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive, pin = write_fixture_archive(
                root, [source_row("1-3-A-T", "ENSG00000000001")]
            )
            renamed = archive.with_name("scores.zip")
            archive.rename(renamed)
            stats = prepare_archive(
                renamed,
                root / "work" / "scores.tsv",
                root / "work" / "stats.json",
                root / "work",
                pin=pin,
                check_space=False,
            )
            self.assertEqual(stats["archive"]["md5"], pin.archive_md5)
            self.assertNotIn("path", stats["archive"])

    def test_preserves_missing_funcvep_scores_and_ignores_discarded_clinvep_values(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive, pin = write_fixture_archive(root, [
                source_row(
                    "1-3-A-T", "ENSG00000000001", ".", "NA", "",
                    "not-a-number", "outside-range", "anything",
                ),
            ])
            output = root / "work" / "scores.tsv"
            stats = prepare_archive(
                archive,
                output,
                root / "work" / "stats.json",
                root / "work",
                pin=pin,
                check_space=False,
            )
            self.assertEqual(
                output.read_text().splitlines()[1],
                "1\t3\tA\tT\tENSG00000000001\t.\t.\t.",
            )
            self.assertEqual(stats["missing_scores"], {
                "FuncVEP_CTI": 1, "FuncVEP_CTE": 1, "FuncVEP_SP": 1,
            })

    def test_rejects_unrecognized_source_header(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bad_header = [*SOURCE_HEADER]
            bad_header[1] = "gene"
            archive, pin = write_fixture_archive(
                root,
                [source_row("1-3-A-T", "ENSG00000000001")],
                header=bad_header,
            )
            # The release pin still declares the real official header.
            pin = replace(pin, source_header=tuple(SOURCE_HEADER))
            with self.assertRaisesRegex(ValueError, "header mismatch"):
                prepare_archive(
                    archive,
                    root / "work" / "scores.tsv",
                    root / "work" / "stats.json",
                    root / "work",
                    pin=pin,
                    check_space=False,
                )

    def test_builds_manifest_and_transactionally_replaces_bundle(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive, pin = write_fixture_archive(root, [
                source_row("10-9-C-G", "ENSG00000000003"),
                source_row("2-6-G-A", "ENSG00000000002"),
                source_row("1-3-A-T", "ENSG00000000001"),
            ])
            work = root / "work"
            plain = work / "scores.tsv"
            stats_path = work / "stats.json"
            prepare_archive(archive, plain, stats_path, work, pin=pin, check_space=False)

            staged_score = work / "scores.tsv.gz"
            staged_score.write_bytes(bgzf_bytes(plain.read_bytes()))
            staged_index = Path(str(staged_score) + ".tbi")
            write_tiny_index(staged_index, ["1", "2", "10"])
            manifest = build_manifest(
                archive,
                staged_score,
                staged_index,
                stats_path,
                installed_score_name="funcvep_scores.grch38.tsv.gz",
                license_acknowledged=True,
            )
            self.assertEqual(manifest["match"]["required"], ["allele", "ensembl_gene_id"])
            self.assertEqual(
                [item["id"] for item in manifest["outputs"]],
                ["FuncVEP_CTI", "FuncVEP_CTE", "FuncVEP_SP"],
            )
            self.assertEqual(
                {
                    item["id"]: item["binary_classification"]["threshold"]
                    for item in manifest["outputs"]
                },
                {
                    "FuncVEP_CTI": 0.419606448098318,
                    "FuncVEP_CTE": 0.519261866786599,
                    "FuncVEP_SP": 0.440940891937106,
                },
            )
            self.assertTrue(all(
                item["binary_classification"]["positive_label"] == "Damaging"
                and item["binary_classification"]["negative_label"] == "Neutral"
                and "Supplementary Table 13"
                in item["binary_classification"]["threshold_set"]
                for item in manifest["outputs"]
            ))
            registry = load_registry()
            self.assertIs(
                validate_manifest_registry_contract(
                    manifest,
                    [registry.predictor("funcvep")],
                    resource=registry.resource("funcvep"),
                    annotator=registry.annotator("funcvep"),
                ),
                manifest,
            )
            self.assertTrue(manifest["source"]["archive"]["preserved"])
            self.assertEqual(manifest["source"]["kind"], "user_supplied_official_archive")
            self.assertNotIn("path", manifest["source"]["archive"])
            self.assertEqual(
                json.loads(stats_path.read_text())["contig_order"],
                ["1", "2", "10"],
            )

            staged_manifest = work / "manifest.json"
            staged_manifest.write_text(json.dumps(manifest))
            target_score = root / "managed" / "funcvep_scores.grch38.tsv.gz"
            target_manifest = root / "managed" / "funcvep.manifest.json"
            target_score.parent.mkdir()
            target_score.write_bytes(b"old data")
            Path(str(target_score) + ".tbi").write_bytes(b"old index")
            target_manifest.write_text("old manifest")

            publish_bundle(
                staged_score,
                staged_index,
                staged_manifest,
                target_score,
                target_manifest,
            )
            self.assertTrue(archive.exists())
            self.assertEqual(target_score.read_bytes(), bgzf_bytes(plain.read_bytes()))
            self.assertEqual(json.loads(target_manifest.read_text())["resource"]["id"], "funcvep")
            self.assertFalse(list(target_score.parent.glob("*.previous.*")))

    def test_manifest_contract_accepts_transcript_and_protein_dimensions(self):
        payload = {
            "manifest_schema": "guide-iei.indexed-scores/v1",
            "resource": {"id": "future", "name": "Future", "release": "1"},
            "assembly": "GRCh38",
            "table": {
                "columns": [
                    "chrom", "position", "reference", "alternate",
                    "ensembl_transcript_id", "protein_change", "score",
                ]
            },
            "match": {
                "required": ["allele", "ensembl_transcript_id", "protein_change"],
                "dimensions": {
                    "allele": {
                        "chrom": "chrom", "position": "position",
                        "reference": "reference", "alternate": "alternate",
                    },
                    "ensembl_transcript_id": {"column": "ensembl_transcript_id"},
                    "protein_change": {"column": "protein_change"},
                },
            },
            "outputs": [{
                "id": "Future_score", "column": "score", "type": "number",
                "description": "Future score",
            }],
            "provenance": {
                "match": "Future_match",
                "match_status": "Future_match_status",
                "source_target": "Future_source_target",
                "allele_available": "Future_allele_available",
            },
        }
        self.assertIs(validate_manifest(payload), payload)
        payload["match"]["required"].append("unsupported_dimension")
        with self.assertRaisesRegex(ManifestError, "unsupported match"):
            validate_manifest(payload)


if __name__ == "__main__":
    unittest.main()
