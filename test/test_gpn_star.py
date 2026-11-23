import gzip
import hashlib
import io
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from pipeline import gpn_star as gpn

try:
    import pyarrow as pa
    import pyarrow.parquet as pq
except ImportError:
    pa = pq = None


class GpnStarTest(unittest.TestCase):
    def test_configured_directory_expands_home_and_preserves_other_paths(self):
        with patch.dict(os.environ, {"HOME": "/tmp/gpn-home"}):
            self.assertEqual(gpn.configured_directory("~/scores"), Path("/tmp/gpn-home/scores"))
            self.assertEqual(gpn.configured_directory("/tmp/scores"), Path("/tmp/scores"))
            self.assertEqual(gpn.configured_directory("references/scores"), gpn.ROOT / "references/scores")

    @unittest.skipUnless(pq, "requires optional Parquet reader")
    def test_home_directory_install_and_annotation_package_selection(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"HOME": tmp}), \
                patch.object(gpn, "install_runtime"):
            root = Path(tmp) / "scores"
            cfg = Path(tmp) / "config.yaml"
            cfg.write_text("wgs_review: {gpn_star_v: {enabled: true, directory: '~/scores'}}\n"
                           "region: {bed: custom.bed, padding_bp: 100}\n")
            source = Path(tmp) / "input.vcf"
            source.write_text("##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
                              "1\t100\t.\tA\tG\t.\tPASS\t.\n1\t200\t.\tA\tG\t.\tPASS\t.\n")
            for package, positions in ((gpn.EXOME_PACKAGE, [100]), ("v100", [100, 200])):
                folder = gpn.release_dir(root, package)
                folder.mkdir(parents=True)
                shard = folder / gpn.shard_name("1")
                pq.write_table(pa.table({"pos": positions, "ref": ["A"] * len(positions),
                                         "alt": ["G"] * len(positions),
                                         "llr_calibrated": [-1.0] * len(positions),
                                         "abs_llr_calibrated": [0.5] * len(positions)}), shard)
                pins = {"1": {"size": shard.stat().st_size, "sha256": gpn.sha256(shard)}}
                resource = gpn.EXOME_RESOURCE if package == gpn.EXOME_PACKAGE else "gpn_star_v"
                with patch.object(gpn, "assets", return_value=pins):
                    with patch.object(sys, "argv", ["gpn_star.py", "install", str(cfg), resource]), \
                            patch.object(sys, "stdout", new_callable=io.StringIO):
                        gpn.main()
                    self.assertTrue(gpn.installed(root, package))
                    with patch.object(sys, "argv", ["gpn_star.py", "annotate", str(cfg), str(source)]), \
                            patch.object(sys, "stdout", new_callable=io.StringIO) as out, \
                            patch.object(sys, "stderr", new_callable=io.StringIO) as log:
                        gpn.main()
                    result = out.getvalue()
                    self.assertIn(f"##GPNStar_v100_package={package}", result)
                    records = [line.split("\t")[-1] for line in result.splitlines() if not line.startswith("#")]
                    self.assertIn("GPNv100_LLR=-1.0", records[0])
                    self.assertEqual(records[1] == ".", package == gpn.EXOME_PACKAGE)
                    for text in (result, log.getvalue()):
                        self.assertEqual("custom BED" in text, package == gpn.EXOME_PACKAGE)
                        self.assertEqual("wider padding" in text, package == gpn.EXOME_PACKAGE)

    def test_proxy_declines_resume_and_fresh_transfer_is_verified(self):
        pin = {"size": 4, "sha256": hashlib.sha256(b"good").hexdigest()}
        with tempfile.TemporaryDirectory() as tmp, patch.object(gpn, "install_runtime"), patch.object(
                gpn, "assets", return_value={"21": pin}):
            root = Path(tmp)
            folder = gpn.release_dir(root, "v100")
            folder.mkdir(parents=True)
            part = folder / "llr_chr21.parquet.part"
            for rc, status in ((33, "200"), (22, "416"), (0, "416")):
                part.write_bytes(b"go")
                def transfer(args, **kwargs):
                    if "--continue-at" in args:
                        return subprocess.CompletedProcess(args, rc, status)
                    self.assertFalse(part.exists())
                    part.write_bytes(b"good")
                    return subprocess.CompletedProcess(args, 0)
                with patch.object(gpn.subprocess, "run", side_effect=transfer) as fetch:
                    gpn.install(root, "v100")
                self.assertEqual(fetch.call_count, 2)
                self.assertTrue(gpn.installed(root, "v100"))
                (folder / gpn.shard_name("21")).unlink()
            part.write_bytes(b"go")
            with patch.object(gpn.subprocess, "run", side_effect=subprocess.CalledProcessError(28, ["curl"])) as fetch:
                with self.assertRaises(subprocess.CalledProcessError):
                    gpn.install(root, "v100")
            self.assertEqual(fetch.call_count, 1)
            self.assertEqual(part.read_bytes(), b"go")
            with patch.object(gpn.subprocess, "run", return_value=subprocess.CompletedProcess(["curl"], 22, "403")):
                with self.assertRaises(subprocess.CalledProcessError):
                    gpn.install(root, "v100")
            self.assertEqual(part.read_bytes(), b"go")

    def test_incomplete_reader_replaced_only_after_successful_build(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(gpn, "bundled_reader", return_value=False), \
                patch.object(gpn, "active_reader", return_value=False):
            reader = Path(tmp) / "reader"
            reader.mkdir()
            old = reader / "old"
            old.touch()
            def build(args, check):
                if "--target" in args:
                    self.assertIn("--require-hashes", args)
                    Path(args[args.index("--target") + 1]).mkdir()
            with patch.object(gpn, "runtime_dir", return_value=reader):
                with patch.object(gpn.subprocess, "run", side_effect=subprocess.CalledProcessError(1, [])):
                    with self.assertRaises(subprocess.CalledProcessError):
                        gpn.install_runtime()
                self.assertTrue(old.exists())
                with patch.object(gpn.subprocess, "run", side_effect=build):
                    gpn.install_runtime()
                self.assertTrue(gpn.runtime_ready())
                self.assertFalse(old.exists())

    @unittest.skipUnless(pq, "requires optional Parquet reader")
    def test_active_reader_needs_no_managed_install(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(gpn, "bundled_reader", return_value=False), \
                patch.object(gpn, "runtime_dir", return_value=Path(tmp) / "reader"), \
                patch.object(gpn.subprocess, "run") as install:
            self.assertTrue(gpn.runtime_ready())
            gpn.install_runtime()
            install.assert_not_called()
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_no_models_strips_known_scores_without_parquet_dependency(self):
        header = "##fileformat=VCFv4.2\n##GPNStar_m447_package=old\n##GPNStar_v100_release=old\n"
        cols = "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS\n"
        row = "1\t1\t.\tA\tG\t.\tPASS\t"
        cfg = "{}"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.vcf"
            config = Path(tmp) / "cfg.yaml"
            config.write_text(cfg)
            path.write_text(header + '##INFO=<ID=GPNm447_LLR,Number=A,Type=Float,Description="Old">\n'
                            + cols + row + "GPNm447_LLR=3;OTHER=0;GPNv100_ABSLLR=5\tGT\t0/1\n")
            cli = [sys.executable, gpn.__file__]
            self.assertEqual(subprocess.check_output(cli + ["has-fields", path], text=True).strip(), "yes")
            clean = subprocess.check_output(cli + ["annotate", config, path], text=True)
            self.assertEqual(clean, "##fileformat=VCFv4.2\n" + cols + row + "OTHER=0\tGT\t0/1\n")
            path.write_text(clean)
            self.assertEqual(subprocess.check_output(cli + ["has-fields", path], text=True).strip(), "no")

    def test_repair_rejects_corrupt_shard_and_resumes_verified_chromosome(self):
        pin = {"size": 4, "sha256": hashlib.sha256(b"good").hexdigest()}
        with tempfile.TemporaryDirectory() as tmp, patch.object(gpn, "install_runtime"), patch.object(
                gpn, "assets", return_value={"1": pin, "22": pin}):
            root = Path(tmp)
            folder = gpn.release_dir(root, "m447")
            folder.mkdir(parents=True)
            (folder / gpn.shard_name("1")).write_bytes(b"good")
            part = folder / "llr_chr22.parquet.part"
            part.write_bytes(b"nope")
            with self.assertRaisesRegex(ValueError, "Checksum"):
                gpn.install(root, "m447")
            self.assertFalse(gpn.installed(root, "m447"))
            self.assertFalse(part.exists())
            part.write_bytes(b"good")
            gpn.install(root, "m447")
            self.assertTrue(gpn.installed(root, "m447"))
            self.assertFalse(gpn.installed(root, "v100"))
            (folder / gpn.shard_name("1")).write_bytes(b"bad")
            self.assertFalse(gpn.installed(root, "m447"))

    def test_failed_repair_revokes_previously_verified_receipt(self):
        pin = {"size": 4, "sha256": hashlib.sha256(b"good").hexdigest()}
        with tempfile.TemporaryDirectory() as tmp, patch.object(gpn, "install_runtime"), patch.object(
                gpn, "assets", return_value={"21": pin}):
            root = Path(tmp)
            folder = gpn.release_dir(root, "v100")
            folder.mkdir(parents=True)
            target = folder / gpn.shard_name("21")
            target.write_bytes(b"good")
            gpn.install(root, "v100")
            self.assertTrue(gpn.installed(root, "v100"))
            stamp = target.stat()
            target.write_bytes(b"oops")
            os.utime(target, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
            target.with_suffix(".parquet.part").write_bytes(b"oops")
            with self.assertRaisesRegex(ValueError, "Checksum"):
                gpn.install(root, "v100")
            self.assertFalse(gpn.installed(root, "v100"))
            target.with_suffix(".parquet.part").write_bytes(b"good")
            gpn.install(root, "v100")
            self.assertTrue(gpn.installed(root, "v100"))

    @unittest.skipUnless(pq, "requires optional Parquet reader")
    def test_padded_snvs_use_the_changed_base_and_keep_their_alt_slot(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            pq.write_table(pa.table({"chrom": ["21"], "pos": [100], "ref": ["A"], "alt": ["C"],
                                     "llr_calibrated": [-1.0], "abs_llr_calibrated": [0.5]}),
                           folder / gpn.shard_name("21"))
            header = "##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
            for pos, ref, alts in ((100, "AT", "A,CT,GC"), (99, "TAT", "T,TCT,TGC")):
                row = f"21\t{pos}\t.\t{ref}\t{alts}\t.\tPASS\t"
                original = header + row + ".\n"
                result = io.StringIO()
                gpn.annotate(io.StringIO(original), result, {"v100": folder})
                self.assertEqual(result.getvalue().splitlines()[-1],
                                 row + "GPNv100_LLR=.,-1.0,.;GPNv100_ABSLLR=.,0.5,.")

    @unittest.skipUnless(pq, "requires optional Parquet reader")
    def test_exact_alleles_and_reannotation_across_batch_and_row_group_boundaries(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(gpn, "BATCH", 2):
            dirs = {}
            for model, chrom in (("m447", "22"), ("v100", "X")):
                dirs[model] = Path(tmp) / model
                dirs[model].mkdir()
                for c in ("22", "X"):
                    pos = 100 if c == chrom else 101
                    table = pa.table({"chrom": [c]*3, "pos": [pos]*3, "ref": ["A"]*3, "alt": ["C","G","T"],
                                      "llr_calibrated": pa.array([0.,-2.,1.], type=pa.float32()),
                                      "abs_llr_calibrated": pa.array([-.5,.75,-1.], type=pa.float32())})
                    pq.write_table(table, dirs[model] / gpn.shard_name(c), row_group_size=2)
            header = "##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS\n"
            rows = [f"{c}\t100\t.\t{r}\tT,AG,C\t.\tPASS\tOLD=1\tGT\t1/3\n"
                    for c,r in (("chr22","a"),("X","A"),("MT","A"),("22","G"))]
            result = io.StringIO()
            gpn.annotate(io.StringIO(header + "".join(rows)), result, dirs)
            text = result.getvalue()
            records = [line.split("\t") for line in text.splitlines() if not line.startswith("#")]
            for row,model in zip(records, ("m447","v100")):
                self.assertEqual(row[7], f"OLD=1;GPN{model}_LLR=1.0,.,0.0;GPN{model}_ABSLLR=-1.0,.,-0.5")
                self.assertEqual(row[8:], ["GT", "1/3"])
            self.assertEqual([r[7] for r in records[2:]], ["OLD=1", "OLD=1"])
            rerun = io.StringIO()
            gpn.annotate(io.StringIO(text), rerun, dirs)
            self.assertEqual(rerun.getvalue(), text)
            for only in dirs:
                fresh, repeated = io.StringIO(), io.StringIO()
                gpn.annotate(io.StringIO(header + "".join(rows)), fresh, {only: dirs[only]})
                gpn.annotate(io.StringIO(text), repeated, {only: dirs[only]})
                self.assertEqual(repeated.getvalue(), fresh.getvalue())

    @unittest.skipUnless(pq, "requires optional Parquet reader")
    def test_gap_in_sparse_row_group_preserves_record_and_scores_later_group(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            table = pa.table({
                "chrom": ["21"] * 3, "pos": [100, 200, 300],
                "ref": ["A"] * 3, "alt": ["G"] * 3,
                "llr_calibrated": [-1.0, -2.0, -3.0],
                "abs_llr_calibrated": [0.25, 0.5, 0.75],
            })
            pq.write_table(table, folder / gpn.shard_name("21"), row_group_size=2)
            header = "##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS\n"
            gap = "21\t150\t.\tA\tG\t.\tPASS\tOLD=1\tGT\t0/1"
            hit = "21\t300\t.\tA\tG\t.\tPASS\t.\tGT\t0/1"
            result = io.StringIO()
            gpn.annotate(io.StringIO(header + gap + "\n" + hit + "\n"), result, {"v100": folder})
            records = [line for line in result.getvalue().splitlines() if not line.startswith("#")]
            self.assertEqual(records, [gap, hit.replace("PASS\t.", "PASS\tGPNv100_LLR=-3.0;GPNv100_ABSLLR=0.75")])

    def test_exome_package_never_enables_wgs_and_full_v_serves_both(self):
        with patch.object(gpn, "installed", side_effect=lambda root, model: model == gpn.EXOME_PACKAGE):
            self.assertEqual(gpn.select_package(Path("."), "v100", "exome"), gpn.EXOME_PACKAGE)
            with self.assertRaisesRegex(ValueError, "whole_genome"):
                gpn.select_package(Path("."), "v100", "whole_genome")
        with patch.object(gpn, "installed", return_value=True):
            for scope in ("exome", "whole_genome"):
                self.assertEqual(gpn.select_package(Path("."), "v100", scope), "v100")
            with self.assertRaisesRegex(ValueError, "whole-genome"):
                gpn.select_package(Path("."), "m447", "exome")

    @unittest.skipUnless(pq, "requires optional Parquet reader")
    def test_compact_run_reports_fixed_coverage_and_full_rerun_removes_it(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(gpn, "assets", return_value={}):
            root = Path(tmp)
            cfg = root / "config.yaml"
            cfg.write_text(f"wgs_review: {{gpn_star_v: {{enabled: true, directory: '{root}'}}}}\n"
                           "region: {custom_bed: panel.bed, padding_bp: 20}\n")
            source = root / "data.vcf"
            source.write_text("##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n")
            for package in (gpn.EXOME_PACKAGE, "v100"):
                folder = gpn.release_dir(root, package)
                folder.mkdir(parents=True)
                (folder / "installed.json").write_text(json.dumps({"revision": gpn.REVISION, "model": package}))
                with patch.object(sys, "argv", ["gpn_star.py", "annotate", str(cfg), str(source)]), \
                        patch.object(sys, "path", list(sys.path)), \
                        patch.object(sys, "stdout", new_callable=io.StringIO) as out, \
                        patch.object(sys, "stderr", new_callable=io.StringIO) as log:
                    gpn.main()
                    self.assertIn(f"##GPNStar_v100_package={package}\n", out.getvalue())
                    for output in (out.getvalue(), log.getvalue()):
                        for term in ("custom BED", "8-bp", "genome-wide V"):
                            self.assertEqual(term in output, package == gpn.EXOME_PACKAGE)
                    source.write_text(out.getvalue())

    @unittest.skipUnless(pq, "requires optional Parquet reader")
    def test_reordered_score_columns_and_missing_statistics_preserve_finite_slots(self):
        table = pa.table({"alt": ["G", "C", "T", "G"], "ref": ["A"] * 4,
                          "abs_llr_calibrated": [0., float("-inf"), None, float("nan")],
                          "pos": [10, 10, 10, 11], "llr_calibrated": [float("nan"), -1., float("inf"), None]})
        source = ("##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
                  "1\t10\t.\tA\tC,G,T\t.\tPASS\t.\n1\t11\t.\tA\tG\t.\tPASS\tKEEP\n")
        with tempfile.TemporaryDirectory() as tmp:
            for stats in (True, False):
                pq.write_table(table, Path(tmp) / gpn.shard_name("1"), write_statistics=stats, row_group_size=2)
                out = io.StringIO()
                gpn.annotate(io.StringIO(source), out, {"v100": Path(tmp)})
                self.assertEqual([r.split("\t")[-1] for r in out.getvalue().splitlines()[-2:]],
                                 ["GPNv100_LLR=-1.0,.,.;GPNv100_ABSLLR=.,0.0,.", "KEEP"])

    @unittest.skipUnless(pq, "requires optional Parquet reader")
    def test_builder_verifies_null_nan_and_infinite_scores_without_position_statistics(self):
        from scripts.build_gpn_star_exome import build_chrom, same_rows
        table = pa.table({"llr_calibrated": [float("nan"), None, float("-inf")],
                          "pos": [11, 12, 13]})
        with tempfile.TemporaryDirectory() as tmp:
            source, target = (Path(tmp) / name for name in ("full.parquet", "compact.parquet"))
            pq.write_table(table, source, write_statistics=False)
            receipt = build_chrom(source, target, [(10, 13)], {"sha256": gpn.sha256(source)})
            self.assertEqual(receipt["rows"], 3)
            scores = pq.read_table(target)["llr_calibrated"].to_pylist()
            self.assertTrue(math.isnan(scores[0]))
            self.assertEqual(scores[1:], [None, float("-inf")])
            changed = table.set_column(0, "llr_calibrated", pa.array([None, None, float("-inf")]))
            self.assertFalse(same_rows(table, changed))

    @unittest.skipUnless(pq, "requires optional Parquet reader")
    def test_bed_metadata_and_blank_lines(self):
        from scripts.build_gpn_star_exome import read_regions
        with tempfile.TemporaryDirectory() as tmp, patch.object(gpn, "assets", return_value={"1": {}}):
            for name, opener in (("targets.bed", open), ("targets.bed.gz", gzip.open)):
                bed = Path(tmp) / name
                with opener(bed, "wt") as out:
                    out.write("  # header\ntrack name=exome\n\n1\t10\t12\tgene\n \t\n browser position chr1\n1 14 15\n")
                self.assertEqual(read_regions(bed), {"1": [(10, 12), (14, 15)]})

    @unittest.skipUnless(pq, "requires optional Parquet reader")
    def test_half_open_exome_boundaries_with_split_multiallelic_site(self):
        from scripts.build_gpn_star_exome import selected_groups
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "chr1.parquet"
            table = pa.table({"pos": [9, 10, 11, 11, 11, 12, 13, 15, 16], "score": list(range(9))})
            for stats in (True, False):
                pq.write_table(table, path, row_group_size=4, write_statistics=stats)
                with pq.ParquetFile(path) as chrom:
                    subset = pa.concat_tables(list(selected_groups(chrom, [(10, 12), (14, 15)])))
                self.assertEqual(subset["score"].to_pylist(), [2, 3, 4, 5, 7])
