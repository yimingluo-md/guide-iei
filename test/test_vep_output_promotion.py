#!/usr/bin/env python3
"""Exercise final publication through the runner with a stand-in VEP and real HTS tools."""
from __future__ import annotations

import gzip
import json
import os
import pathlib
import shutil
import sqlite3
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]

FAKE_DOCKER = r'''#!/usr/bin/env python3
"""Stand-in for the container engine: answers `image inspect`, the perl
file-access probe (writes the .container markers), and a `sh -c "vep ..."`
invocation (writes a VCF without a CSQ header to the -o path)."""
import os, re, subprocess, sys
argv = sys.argv[1:]
if argv[:2] == ["image", "inspect"]:
    sys.exit(0)
if argv[:1] != ["run"]:
    sys.exit(0)
mounts = []
for i, a in enumerate(argv):
    if a == "-v" and i + 1 < len(argv):
        host, cont, *_ = argv[i + 1].split(":")
        mounts.append((cont, host))
mounts.sort(key=lambda m: -len(m[0]))
def to_host(path):
    for cont, host in mounts:
        if path == cont or path.startswith(cont + "/"):
            return host + path[len(cont):]
    raise SystemExit("unmapped container path: " + path)
if "--entrypoint" in argv and argv[argv.index("--entrypoint") + 1] == "perl":
    tail = argv[argv.index("-e") + 2:]
    for j in range(0, len(tail), 4):
        index, mode, target, _digest = tail[j:j + 4]
        if mode == "write":
            with open(to_host(target) + ".container", "wb") as fh:
                fh.write(b"container-write-ok\n")
        print(f"{index}:OK")
    sys.exit(0)
if "-c" in argv:
    cmd = argv[argv.index("-c") + 1]
    match = re.search(r"(?:^|\s)'?-o'?\s+'?([^'\s]+)'?", cmd)
    out = to_host(match.group(1))
    body = b"##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n1\t1\t.\tA\tG\t.\tPASS\t.\n"
    if os.environ.get("FAKE_VALID_VEP"):
        body = (b'##fileformat=VCFv4.2\n##contig=<ID=1,length=248956422>\n'
                b'##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n'
                b'##INFO=<ID=CSQ,Number=.,Type=String,Description="Format: Allele|Consequence|SYMBOL|Protein_position|Amino_acids|Feature">\n'
                b'#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\n'
                b'1\t100\t.\tA\tG\t.\tPASS\tCSQ=G|missense_variant|GENE|20|A/T|ENST1\tGT\t0/1\n')
    with open(out, "wb") as fh:
        if out.endswith(".gz"):
            subprocess.run(["bgzip", "-c"], input=body, stdout=fh, check=True)
        else:
            fh.write(body)
    sys.exit(0)
sys.exit(0)
'''


def _ensure_fixtures():
    """The same throwaway reference files test_dry_run.sh creates."""
    for rel in (
        "test/fixtures/vep_cache/homo_sapiens/113_GRCh38",
        "test/fixtures/fasta", "test/fixtures/loftee", "test/fixtures/custom",
        "test/fixtures/clinvar",
    ):
        (ROOT / rel).mkdir(parents=True, exist_ok=True)
    for rel in (
        "test/fixtures/fasta/genome.fa.gz",
        "test/fixtures/loftee/human_ancestor.fa.gz", "test/fixtures/loftee/loftee.sql",
        "test/fixtures/loftee/gerp.bw", "test/fixtures/custom/repeatmasker.bed.gz",
        "test/fixtures/clinvar/clinvar_latest.GRCh38.vcf.gz",
        # The access probe needs one real file inside the cache directory.
        "test/fixtures/vep_cache/homo_sapiens/113_GRCh38/info.txt",
    ):
        path = ROOT / rel
        if not path.exists():
            path.touch()


@unittest.skipUnless(
    shutil.which("bcftools") and shutil.which("bgzip") and shutil.which("tabix"),
    "bcftools/bgzip/tabix are needed to reach the VEP step natively",
)
class VepOutputPromotionTests(unittest.TestCase):
    def setUp(self):
        _ensure_fixtures()
        self.temp = tempfile.TemporaryDirectory()
        self.dir = pathlib.Path(self.temp.name)
        self.bin = self.dir / "bin"
        self.bin.mkdir()
        fake = self.bin / "docker"
        fake.write_text(FAKE_DOCKER)
        fake.chmod(0o755)
        self.out_dir = self.dir / "out dir"
        self.out_dir.mkdir()
        self.output = self.out_dir / "sample.vep.vcf.gz"
        self.previous = b"previous-deliverable-bytes"
        self.output.write_bytes(self.previous)
        self.sidecar = pathlib.Path(str(self.output) + ".deliverable")
        self.sidecar.write_text("sample.vep.vcf.gz\n")

    def tearDown(self):
        self.temp.cleanup()

    def _run(self, config="test/test.config.yaml", valid=False):
        env = os.environ.copy()
        env["PATH"] = str(self.bin) + os.pathsep + env["PATH"]
        if valid:
            env["FAKE_VALID_VEP"] = "1"
        return subprocess.run(
            [
                "bash", str(ROOT / "scripts/run_annotation.sh"),
                "-i", "test/sample.mini.vcf", "-o", str(self.output),
                "-c", str(config), "--no-clinvar", "--all-variants",
            ],
            cwd=ROOT, env=env, text=True, capture_output=True, timeout=600,
        )

    def _config(self, compression, match):
        import yaml
        config = yaml.safe_load((ROOT / "test/test.config.yaml").read_text().replace(
            "test/fixtures/", str(ROOT / "test/fixtures") + "/"))
        config["output"]["compress"] = compression
        catalog = self.dir / "catalog.tsv"
        catalog.write_text("GENE\t20\tA\tT\tENST1\n")
        config["clinvar"]["protein_match_catalog"] = str(catalog)
        config["post_processing"]["clinical_protein_match"] = {"enabled": match, "clingen": False}
        database = self.dir / "assertions.db"
        with sqlite3.connect(database) as db:
            db.execute("CREATE TABLE IF NOT EXISTS assertions(chrom,pos,ref,alt,active,uuid,caid,assertion,"
                       "disease,mondo_id,mode_of_inheritance,expert_panel,approval_date)")
            db.execute("INSERT INTO assertions VALUES ('1',100,'A','G',1,'id','ca','test','d','m','i','p','date')")
        config["clingen_erepo"] = {"enabled": match, "required": True, "database": str(database)}
        config_path = self.dir / "config.yaml"
        config_path.write_text(yaml.safe_dump(config))
        return config_path

    def test_requested_and_legacy_names_have_all_final_annotations(self):
        for mode, extension in (("none", ".vcf"), ("bgzip", ".vcf.gz")):
            with self.subTest(mode=mode):
                self.output = self.out_dir / ("requested" + extension)
                legacy = self.out_dir / ("requested.aamatch" + extension)
                for path in (self.output, legacy):
                    path.write_text("prior result")
                    for idx in (".tbi", ".csi"):
                        pathlib.Path(str(path) + idx).write_text("stale index")
                result = self._run(self._config(mode, True), valid=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.output.read_bytes(), legacy.read_bytes())
                pointer = pathlib.Path(str(self.output) + ".deliverable")
                self.assertEqual(pointer.read_text(), legacy.name + "\n")
                for path in (self.output, legacy):
                    opener = gzip.open if mode == "bgzip" else open
                    with opener(path, "rt") as fh:
                        content = fh.read()
                    records = [line for line in content.splitlines() if not line.startswith("#")]
                    self.assertEqual(len(records), 1)
                    self.assertIn("ClinVar_path_aa_match=1", records[0])
                    self.assertIn("ClinGen_ERepo=", records[0])
                    self.assertEqual(content.count("##GUIDE_IEI_notice="), 1)
                    self.assertFalse(pathlib.Path(str(path) + ".csi").exists())
                    if mode == "bgzip":
                        indexed = subprocess.check_output(["tabix", path, "1:100-100"], text=True)
                        self.assertEqual(indexed.splitlines(), records)
                    else:
                        self.assertFalse(pathlib.Path(str(path) + ".tbi").exists())
                for suffix, field in ((".run_manifest.json", "output"), (".annotation_qc.json", "input_vcf")):
                    report = json.loads(pathlib.Path(str(legacy) + suffix).read_text())
                    reported = report[field] if field == "input_vcf" else report[field]["path"]
                    self.assertEqual(pathlib.Path(reported).resolve(), legacy.resolve())

    def test_notice_only_run_and_failed_reindex_preserve_previous_publication(self):
        config = self._config("bgzip", False)
        result = self._run(config, valid=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        with gzip.open(self.output, "rt") as fh:
            self.assertIn("##GUIDE_IEI_notice=", fh.read())
        self.assertEqual(self.sidecar.read_text(), self.output.name + "\n")
        index = pathlib.Path(str(self.output) + ".tbi")
        previous = [p.read_bytes() for p in (self.output, index, self.sidecar)]
        shim = self.bin / "tabix"
        shim.write_text(f'#!/bin/sh\ncase "$*" in *final-to-index*) echo "EOF marker is absent" >&2;; esac\n'
                        f'exec "{shutil.which("tabix")}" "$@"\n')
        shim.chmod(0o755)
        result = self._run(config, valid=True)
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertIn("final VCF indexing failed", result.stderr)
        self.assertEqual([p.read_bytes() for p in (self.output, index, self.sidecar)], previous)
        self.assertFalse([p for p in self.out_dir.iterdir() if "notice." in p.name or "guide-iei-work." in p.name])

    def test_failed_notice_rewrite_cleans_partial_file_and_index(self):
        shim = self.bin / "bcftools"
        shim.write_text(
            '#!/bin/bash\n'
            'if [[ "$1" == annotate && "$2" == -h ]]; then\n'
            '  while [[ "$1" != -o ]]; do shift; done\n'
            '  printf partial > "$2"; printf partial > "$2.tbi"; exit 1\n'
            'fi\n'
            f'exec "{shutil.which("bcftools")}" "$@"\n'
        )
        shim.chmod(0o755)
        result = self._run(self._config("bgzip", False), valid=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("could not add the research-use notice", result.stderr)
        self.assertTrue(subprocess.check_output(["tabix", self.output, "1:100-100"]))
        self.assertFalse([p for p in self.out_dir.iterdir() if ".notice." in p.name or "guide-iei-work." in p.name])

    def test_unvalidated_vep_output_is_never_promoted(self):
        result = self._run()
        self.assertNotEqual(result.returncode, 0, result.stderr)
        # The pre-filter census (one pass) supplies both totals (audit M18).
        self.assertIn("input pre-filter (FILTER=PASS-or-unfiltered): 3 -> 3 variants", result.stderr)
        self.assertIn("VEP writing to ", result.stderr)
        self.assertIn("required annotation validation failed", result.stderr)
        self.assertIn("was not promoted", result.stderr)
        # The previous deliverable and its pointer are intact...
        self.assertEqual(self.output.read_bytes(), self.previous)
        self.assertTrue(self.sidecar.exists())
        # ...and the temporary output (plus VEP sidecars) is gone.
        leftovers = [p.name for p in self.out_dir.iterdir() if "vep-tmp" in p.name]
        self.assertEqual(leftovers, [])
        scratch = [p.name for p in self.out_dir.iterdir() if p.name.startswith(".guide-iei-work.")]
        self.assertEqual(scratch, [])


if __name__ == "__main__":
    unittest.main()
