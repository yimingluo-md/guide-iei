"""AVI conversion regression tests, using synthetic public-format data only."""
import gzip
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pipeline import avi_dataset as avi


class AviTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not shutil.which('c++'):
            raise unittest.SkipTest('C++ compiler unavailable')
        cls.build = tempfile.TemporaryDirectory()
        cls.converter = Path(cls.build.name) / 'convert'
        subprocess.run(['c++', '-O2', '-std=c++17', str(ROOT/'pipeline/avi_convert.cpp'),
                        '-lz', '-o', str(cls.converter)], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.build.cleanup()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def source(self, rows):
        header = b'#CHROM\tPOS\tREF\tALT\traw_score\tPHRED\n'
        data = avi.bgzf(header + rows.encode()) + avi.EOF
        # VCF-preset TBI with a data bin and metadata pseudo-bin.
        index = (b'TBI\x01' + struct.pack('<8i',1,2,1,2,0,35,0,5) + b'chr1\0'
                 + struct.pack('<iIiQQIiQQQQiQ',2,4681,1,len(header),len(data)<<16,
                               37450,2,len(header),len(data)<<16,rows.count('\n'),0,1,len(header)))
        path = self.root/'source.zip'
        with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_STORED) as z:
            z.writestr(avi.MEMBER, data)
            z.writestr(avi.MEMBER+'.tbi', gzip.compress(index))
        return path, avi.inspect_archive(path, pinned=False)

    def convert(self, rows):
        path, info = self.source(rows)
        ref = info['chromosomes'][0]
        out = self.root/'out.gz'
        result = subprocess.run([str(self.converter),str(path),str(info['member_offset']),
                                 str(ref['virtual_start']), 'chr1',str(ref['rows']),str(out),
                                 str(self.root/'stats.json'),str(self.root/'progress')], capture_output=True,text=True)
        return result, out

    def test_grouping_is_lossless_and_alt_ordered(self):
        result, out = self.convert('chr1\t10001\tT\tG\t-0.03720\t1.11839\n'
                                   'chr1\t10001\tT\tA\t-0.03868\t1.06466\n'
                                   'chr1\t10001\tT\tC\t0\t0.00000\n')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(gzip.decompress(out.read_bytes()).decode(),
                         '1\t10001\t.\tT\tA,C,G\t.\t.\traw=-0.03868,0,-0.03720;phred=1.06466,0.00000,1.11839\n')
        self.assertTrue(out.read_bytes().endswith(avi.EOF))
        self.assertEqual(json.loads((self.root/'stats.json').read_text()),{'rows':3,'positions':1})

    def test_invalid_rows_fail(self):
        base = 'chr1\t1\tT\tA\t0\t1\nchr1\t1\tT\tC\t0\t2\nchr1\t1\tT\tG\t0\t3\n'
        for altered in (base.replace('\tG\t','\tA\t'),base.replace('\t2\n','\t-2\n'),
                        base.replace('\t2\n','\tNaN\n'),base.replace('\tC\t','\tCC\t'),
                        base.replace('chr1\t1\tT\tG','chr1\t2\tT\tG'),
                        base.replace('\t2\n','\t0x2\n')):
            with self.subTest(altered=altered):
                result, _ = self.convert(altered)
                self.assertNotEqual(result.returncode,0)

    def test_production_pin_rejects_synthetic_archive(self):
        path, _ = self.source('chr1\t1\tT\tA\t0\t1\n'*3)
        with self.assertRaisesRegex(ValueError,'verified'):
            avi.inspect_archive(path)

    def test_malformed_manifest_does_not_break_poll(self):
        p = self.root/'manifest.json'
        for data in (b'\xff',b'[]',b'null',b'{}'):
            p.write_bytes(data)
            self.assertFalse(avi.valid_bundle(p))

    def test_number_a_contract(self):
        self.assertIn(b'ID=raw,Number=A,Type=Float',avi.vcf_header())
        self.assertIn(b'ID=phred,Number=A,Type=Float',avi.vcf_header())

    def test_container_converter_maps_only_helper_archive_and_outputs(self):
        helper = self.root/'compiler dir/convert'
        archive = self.root/'source dir/source.zip'
        output = self.root/'work dir/chr1.vcf.gz'
        command = [str(helper),str(archive),'100','25','chr1','3',str(output),
                   str(output.parent/'stats.json'),str(output.parent/'progress')]
        self.assertEqual(avi.converter_command(command),command)
        for runtime in ('docker','podman','singularity','apptainer'):
            with patch.dict(os.environ, {'RUNTIME':runtime,'IMAGE':'local-test-image'}):
                result = avi.converter_command(command,True)
            self.assertEqual(result[0],runtime)
            self.assertIn(str(helper.parent.resolve())+':/avi_tool:ro',result)
            self.assertIn(str(archive.parent.resolve())+':/avi_source:ro',result)
            self.assertIn(str(output.parent.resolve())+':/avi_output:rw',result)
            self.assertIn('/avi_tool/convert',result)
            self.assertEqual(result[-8:],['/avi_source/source.zip','100','25','chr1','3',
                                         '/avi_output/chr1.vcf.gz','/avi_output/stats.json','/avi_output/progress'])
            if runtime in ('docker','podman'):
                for flag in ('--network=none','--pull=never','core=0:0'): self.assertIn(flag,result)
            self.assertNotIn('python3',result)
        command[-1] = str(self.root/'elsewhere/progress')
        with self.assertRaisesRegex(ValueError,'share a working directory'):
            avi.converter_command(command,True)

    def test_atomic_bundle_publication_and_verified_retry_preserve_source(self):
        path, identity = self.source('chr1\t1\tT\tA\t0\t1\nchr1\t1\tT\tC\t0\t2\nchr1\t1\tT\tG\t0\t3\n')
        source_before = path.read_bytes()
        root = self.root/'installed'
        original_sleep = avi.time.sleep
        def index(scores):
            Path(str(scores)+'.tbi').write_bytes(b'synthetic index; real Tabix tested with VEP separately')
        with patch.object(avi,'inspect_archive',return_value=identity), patch.object(avi,'EXPECTED_ROWS',3), \
             patch.object(avi.shutil,'disk_usage',return_value=type('Disk',(),{'free':300*1024**3})()), \
             patch.object(avi,'_tabix',side_effect=index), \
             patch.object(avi.time,'sleep',side_effect=lambda _: original_sleep(0.01)), \
             contextlib.redirect_stdout(io.StringIO()):
            final = avi.prepare(path,root,self.converter,1)
            self.assertEqual(avi.current_bundle(root),final/'manifest.json')
            self.assertTrue(avi.valid_bundle(final/'manifest.json',strict=True))
            self.assertEqual(avi.prepare(path,root,self.converter,1),final)
            self.assertFalse(list((root/('.preparing-'+avi.RELEASE)).glob('chr*.vcf.gz')))
            # Damaged data never passes status; repair retains the damaged
            # release for recovery and atomically installs the replacement.
            (final/'avi.grch38.vcf.gz').write_bytes(b'bad')
            self.assertIsNone(avi.current_bundle(root))
            self.assertEqual(avi.prepare(path,root,self.converter,1),final)
            self.assertEqual(len(list(final.parent.glob('*.damaged-*'))),1)
            self.assertTrue(avi.valid_bundle(final/'manifest.json',strict=True))
        self.assertEqual(path.read_bytes(),source_before)

    def test_qc_counts_alleles_not_transcripts_and_excludes_indels(self):
        from pipeline.annotation_qc import build_report
        config = self.root/'config.yaml'
        config.write_text('custom_tracks:\n  AlphaGenomeAVI:\n    enabled: true\n')
        vcf = self.root/'annotated.vcf'
        vcf.write_text('##fileformat=VCFv4.2\n'
            '##INFO=<ID=CSQ,Number=.,Type=String,Description="Format: Allele|ALLELE_NUM|Consequence|AlphaGenomeAVI_raw|AlphaGenomeAVI_phred">\n'
            '#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n'
            '1\t10001\t.\tT\tA,G\t.\tPASS\tCSQ=A|1|intergenic_variant|-0.1|0,A|1|intergenic_variant|-0.1|0,G|2|intergenic_variant||\n'
            '1\t10002\t.\tA\tAT\t.\tPASS\tCSQ=T|1|intergenic_variant||\n'
            'MT\t10\t.\tT\tA\t.\tPASS\tCSQ=A|1|intergenic_variant||\n')
        report = build_report(config,vcf)
        item = next(m for m in report['metrics'] if m['name'].startswith('AlphaGenome AVI'))
        self.assertEqual(item['eligible_records'],2)
        self.assertEqual(item['annotated_records'],1)
        self.assertEqual(item['coverage'],0.5)
        self.assertEqual(item['status'],'WARN')


if __name__ == '__main__':
    unittest.main()
