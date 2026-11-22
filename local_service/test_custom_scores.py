"""Selected-column and context matching regressions using synthetic tables."""
import gzip
import io
import json
import sqlite3
from contextlib import closing
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from local_service import custom_scores
from local_service.workbench_service import WorkbenchRequestHandler


class CustomScoresTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.work = Path(tmp.name)
        self.root = self.work / "custom-scores"
        self.file = self.work / "scores.tsv.gz"
        self.cols = ["#CHROM", "pos", "ref", "alt", "strand", "gene_id", "transcript_id",
                     *[f"detail_{n}" for n in range(78)], "overall"]
        self.plus = ["chr1", "11", "AC", "AT", "+", "GENE1", "ENST11.3", *(["9"] * 78), "0"]
        self.minus = [*self.plus]
        self.minus[4], self.minus[-1] = "-", "0.75"
        self.write(self.file, self.cols, [self.plus, self.minus])
        self.spec = {"name": "Synthetic", "mapping": custom_scores.inspect([self.file])["mapping"],
                     "scores": [{"column": "overall", "label": "Effect"}]}
        self.query = {"chrom": "1", "pos": 12, "ref": "C", "alt": "T", "strand": "+",
                      "gene": "GENE1", "transcript": "ENST11", "versionedTranscript": "ENST11.3"}

    @staticmethod
    def write(path, cols, rows):
        with gzip.open(path, "wt", encoding="utf-8") as f:
            for row in [cols, *rows]:
                f.write("\t".join(row) + "\n")

    def install(self, paths=None):
        return custom_scores.install(self.root, paths or [self.file], self.spec, lambda *_: None)

    def matches(self, **kwargs):
        return custom_scores.datasets(self.root, {**self.query, **kwargs})[0]["matches"]

    def test_wide_shards_reordered_projection_minimal_allele_and_zero(self):
        other = self.work / "more.tsv.gz"
        self.write(other, self.cols[::-1], [["."] + self.plus[1:-1][::-1] + ["chr2"]])
        self.assertEqual(self.install([self.file, other])["rows"], 3)
        self.assertEqual(self.matches()[0]["values"], ["0"])
        self.assertEqual(self.matches(chrom="2")[0]["values"], [None])
        self.assertEqual(self.matches(alt="G"), [])
        self.assertEqual(self.matches(gene="GENE2"), [])
        self.assertEqual(self.matches(versionedTranscript="ENST11.4"), [])
        self.assertEqual(self.matches(versionedTranscript=None)[0]["unresolved"], ["transcript"])
        both = self.matches(strand=None)
        self.assertEqual([(r["context"]["strand"], r["values"], r["unresolved"]) for r in both],
                         [("+", ["0"], ["strand"]), ("-", ["0.75"], ["strand"])])

    def test_unsorted_bins_skipped_alleles_and_removing_broken_index(self):
        far = [*self.plus]
        far[1] = "3011"
        excluded = [[*self.plus[:3], alt, *self.plus[4:]] for alt in ("AC", "*", "-")]
        self.write(self.file, self.cols, [self.plus, far] * 100 + excluded)
        result = self.install()
        self.assertEqual((result["rows"], result["skipped_rows"]), (200, 3))
        index = self.root / (result["id"] + ".sqlite3")
        with closing(sqlite3.connect(index)) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM blocks").fetchone()[0], 2)
        index.write_bytes(b"unreadable")
        self.assertIn("error", custom_scores.datasets(self.root)[0])
        custom_scores.remove(self.root, result["id"])
        self.assertEqual(custom_scores.datasets(self.root), [])
        self.assertTrue(self.file.is_file())

    def test_atomic_replacement_and_required_fields(self):
        self.install()
        with self.assertRaisesRegex(ValueError, "confirm replacement"):
            self.install()
        self.spec["replace"] = True
        self.write(self.file, self.cols, [self.plus] * 4 + [["bad"]])
        with self.assertRaisesRegex(ValueError, "data row 5"):
            self.install()
        self.assertEqual(self.matches()[0]["values"], ["0"])
        self.assertFalse(list(self.root.glob("*.partial")))
        self.spec["scores"] = []
        with self.assertRaisesRegex(ValueError, "at least one"):
            self.install()

    def test_refseq_versions_case_gene_id_and_optional_dash_convention(self):
        self.plus[4:7] = ["-", "Gene1", "NM_003998.4"]
        self.minus[4:7] = ["-1", "Gene1", "NM_003998"]
        self.write(self.file, self.cols, [self.plus, self.minus])
        self.spec["dash_is_missing_strand"] = True
        self.install()
        query = dict(transcript="NM_003998", versionedTranscript=None)
        found = self.matches(**query)
        self.assertEqual([(r["values"], r["unresolved"]) for r in found], [(["0"], ["strand", "transcript"])])
        found = self.matches(**query, gene="ENSG123", geneId="ENSG123", strand="-")
        self.assertEqual([r["unresolved"] for r in found], [["strand", "gene", "transcript"], ["gene"]])
        self.assertEqual(self.matches(transcript="NM_003998", versionedTranscript="NM_003998.5"), [])
        self.assertEqual(self.matches(transcript="NM_003998", versionedTranscript="NM_003998.4")[0]["unresolved"], ["strand"])
        self.assertEqual(self.matches(**query, gene="OTHER", geneId="ENSG123"), [])

    def test_new_columns_replace_the_old_projection_only_after_commit(self):
        self.install()
        self.spec.update(replace=True, scores=[
            {"column": "detail_77", "label": "Detail"}, {"column": "overall", "label": "Summary"}
        ])
        def check_previous_index(message, _):
            if message.startswith("Indexing"):
                self.assertEqual(self.matches()[0]["values"], ["0"])
                with self.assertRaisesRegex(ValueError, "in progress"):
                    self.install()
        custom_scores.install(self.root, [self.file], self.spec, check_previous_index)
        self.assertEqual(self.matches()[0]["values"], ["9", "0"])
        self.assertEqual([s["label"] for s in custom_scores.datasets(self.root)[0]["scores"]], ["Detail", "Summary"])

    def test_duplicate_and_mismatched_files(self):
        with self.assertRaisesRegex(ValueError, "twice"):
            custom_scores.source_paths([str(self.file)] * 2)
        other = self.work / "other.gz"
        self.write(other, self.cols[:-1], [self.plus[:-1]])
        with self.assertRaisesRegex(ValueError, "different set"):
            custom_scores.inspect([self.file, other])

    def test_http_import_and_lookup_contract(self):
        service = Mock(annotation_root=self.work)
        service._start_internal_resource_job.side_effect = lambda _, operation, runner: runner(lambda *_: None)
        def call(path, body):
            handler = object.__new__(WorkbenchRequestHandler)
            handler.service = service
            handler.path = path
            handler.command = "POST"
            raw = json.dumps(body).encode()
            handler.headers = {"Host": "localhost", "Content-Length": str(len(raw))}
            handler.rfile = io.BytesIO(raw)
            handler._json = Mock()
            handler.do_POST()
            return handler._json.call_args.args
        preview, = call("/api/custom-scores/inspect", {"paths": [str(self.file)]})
        self.assertEqual(preview["examples"][0][-1], "0")
        result, status = call("/api/resource-preparations/custom-scores", dict(self.spec, paths=[str(self.file)]))
        self.assertEqual((result["rows"], status), (2, 202))
        response, = call("/api/custom-scores/lookup", self.query)
        self.assertEqual(response[0]["matches"][0]["values"], ["0"])
        removed, = call("/api/custom-scores/remove", {"id": result["id"]})
        self.assertEqual(removed["id"], result["id"])
        self.assertEqual(call("/api/custom-scores/lookup", self.query), ([],))


    def test_unreadable_index_does_not_hide_healthy_results(self):
        good = self.install()
        broken = self.root / "broken.sqlite3"
        broken.write_bytes(b"damaged database")
        for filename in ("._x.sqlite3", ".hidden.sqlite3"):
            (self.root / filename).write_bytes(b"sidecar")
        for query in (None, self.query):
            by_id = {d["id"]: d for d in custom_scores.datasets(self.root, query)}
            self.assertEqual(set(by_id), {good["id"], "broken"})
            self.assertIn(broken.name, by_id["broken"]["error"])
            self.assertEqual(by_id[good["id"]]["rows"], 2)
            if query:
                self.assertEqual(by_id[good["id"]]["matches"][0]["values"], ["0"])

        # An index may have readable metadata but a damaged score block.
        with closing(sqlite3.connect(self.root / (good["id"] + ".sqlite3"))) as db:
            db.execute("UPDATE blocks SET payload=?", (b"bad block",))
            db.commit()
        entries = custom_scores.datasets(self.root, self.query)
        damaged = next(entry for entry in entries if entry["id"] == good["id"])
        self.assertIn("Synthetic", damaged["error"])
        self.assertEqual(damaged["scores"], [])
