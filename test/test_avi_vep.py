"""Opt-in real VEP test: AVI_VEP_INTEGRATION=1 python3 -m unittest discover -s test -p test_avi_vep.py."""
import gzip
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from pipeline import avi_dataset as avi


@unittest.skipUnless(os.environ.get('AVI_VEP_INTEGRATION')=='1','requires local VEP container and GRCh38 cache')
class AviVepTest(unittest.TestCase):
    def test_intergenic_multi_alt_exact_matching(self):
        with tempfile.TemporaryDirectory(prefix='avi-vep-') as d:
            folder = Path(d).resolve()
            source = folder/'avi.vcf.gz'
            source.write_bytes(avi.bgzf(avi.vcf_header()+
                b'1\t10001\t.\tT\tA,C,G\t.\t.\traw=-0.03868,-0.032,-0.0372;phred=1.06466,1.3114,1.11839\n')+avi.EOF)
            (folder/'input.vcf').write_text('##fileformat=VCFv4.2\n##contig=<ID=1>\n'
                '#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n'
                '1\t10001\tmulti\tT\tG,A\t.\t.\t.\n'
                '1\t10001\twrong_ref\tC\tG\t.\t.\t.\n'
                '1\t10002\tmissing\tA\tC\t.\t.\t.\n')
            docker=os.environ.get('AVI_DOCKER','docker')
            base=[docker,'run','--rm','--network','none','--ulimit','core=0',
                  '-v',f'{folder}:/work','-v',f'{ROOT}/references:/refs:ro','vep-annotate:latest']
            subprocess.run(base+['tabix','-p','vcf','/work/avi.vcf.gz'],check=True,capture_output=True,text=True)
            subprocess.run(base+['vep','--offline','--cache','--dir_cache','/refs/vep_cache','--cache_version','113',
                '--fasta','/refs/fasta/Homo_sapiens.GRCh38.dna.primary_assembly.fa.gz','--assembly','GRCh38',
                '--input_file','/work/input.vcf','--output_file','/work/output.vcf','--vcf','--force_overwrite',
                '--no_stats','--allele_number','--distance','0','--custom',
                'file=/work/avi.vcf.gz,short_name=AlphaGenomeAVI,format=vcf,type=exact,coords=0,fields=raw%phred'],
                check=True,capture_output=True,text=True)
            lines=(folder/'output.vcf').read_text().splitlines()
            csq=next(x for x in lines if x.startswith('##INFO=<ID=CSQ,'))
            fields=csq.split('Format: ')[1].rstrip('">').split('|')
            records={line.split('\t')[2]:line.split('\t') for line in lines if not line.startswith('#')}
            parsed={}
            for name,record in records.items():
                info=dict(x.split('=',1) for x in record[7].split(';') if '=' in x)
                parsed[name]=[dict(zip(fields,x.split('|'))) for x in info['CSQ'].split(',')]
            expected={'G':1.11839,'A':1.06466}
            for row in parsed['multi']:
                self.assertEqual(row['Consequence'],'intergenic_variant')
                self.assertAlmostEqual(float(row['AlphaGenomeAVI_phred']),expected[row['Allele']],places=5)
                self.assertLess(float(row['AlphaGenomeAVI_raw']),0)
            self.assertEqual({r['Allele'] for r in parsed['multi']},{'G','A'})
            for name in ('wrong_ref','missing'):
                self.assertTrue(all(not r['AlphaGenomeAVI_phred'] for r in parsed[name]),parsed[name])


if __name__=='__main__':
    unittest.main()
