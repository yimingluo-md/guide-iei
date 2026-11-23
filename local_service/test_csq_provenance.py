import tempfile
import unittest
from pathlib import Path

from local_service.cohort_store import CohortStore
from pipeline.genia_alleles import csq_allele_index, old_record_context
from local_service.wgs_review import WgsPrefilterOptions, evaluate_record
from local_service.cohort_store import read_vcf_header

FIXTURE = Path(__file__).resolve().parents[1] / 'test/contracts/bcftools-1.24-split-csq.vcf'

class ProvenanceTest(unittest.TestCase):
    def test_declared_provenance_and_missing_or_forged_provenance(self):
        fixture = FIXTURE.read_text()
        for mode in ('declared', 'reannotated', 'absent', 'forged', 'invalid'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                text = fixture
                if mode == 'reannotated':
                    text = text.replace('CSQ=TG|1|SNVGENE|missense_variant,-|2|DELGENE|frameshift_variant;ORIG=1|100|ACG|ATG,A|2',
                                        'CSQ=-|1|DELGENE|frameshift_variant;ORIG=1|100|ACG|ATG,A|2')
                elif mode == 'absent':
                    text = '\n'.join(line for line in text.splitlines() if not line.startswith('##INFO=<ID=ORIG,'))
                    text = text.replace(';ORIG=1|100|ACG|ATG,A|1', '').replace(';ORIG=1|100|ACG|ATG,A|2', '')
                elif mode == 'forged':
                    text = text.replace('Original variant. Format: CHR|POS|REF|ALT|USED_ALT_IDX', 'Untrusted field')
                elif mode == 'invalid':
                    text = text.replace('ORIG=1|100|ACG|ATG,A|1', 'ORIG=1|100|ACG|ATG,A|2')
                path = Path(directory) / 'input.vcf'
                path.write_text(text + '\n')
                store = CohortStore(Path(directory) / 'cohort.sqlite3')
                result = store.import_vcf(path)
                with store._session() as db:
                    rows = {tuple(row) for row in db.execute('SELECT v.pos,v.alt,a.gene FROM cohort_annotations a JOIN cohort_variants v ON v.id=a.variant_id')}
                self.assertIn((100, 'A', 'DELGENE'), rows)
                self.assertEqual((101, 'T', 'SNVGENE') in rows, mode in {'declared', 'reannotated'})
                self.assertNotIn((101, 'T', 'DELGENE'), rows)
                self.assertNotIn((100, 'A', 'SNVGENE'), rows)
                self.assertEqual('could not be assigned' in result['preparation_warning'], mode not in {'declared', 'reannotated'})
                if mode not in {'declared', 'reannotated'}:
                    self.assertIn('reannotate', store.query({'mode': 'gene', 'gene': 'SNVGENE'})['comparability_warning'].lower())

    def test_wgs_score_retention_requires_matching_consequence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.vcf"
            text = FIXTURE.read_text().replace('SYMBOL|Consequence"', 'SYMBOL|Consequence|SpliceAI_pred_DS_AG"')
            text = text.replace("missense_variant,", "missense_variant|0.95,")
            path.write_text(text)
            header = read_vcf_header(path)
            options = WgsPrefilterOptions(noncoding_mode="none")
            snv = next(line for line in text.splitlines() if line.startswith("1\t101\t"))
            sibling = next(line for line in text.splitlines() if line.startswith("1\t100\t"))
            self.assertTrue(evaluate_record(snv, header, options, {})[0])
            self.assertFalse(evaluate_record(sibling, header, options, {})[0])
            lost = snv.replace(";ORIG=1|100|ACG|ATG,A|1", "")
            self.assertFalse(evaluate_record(lost, header, options, {})[0])

    def test_symbolic_and_unverifiable(self):
        self.assertEqual(csq_allele_index({'Allele': 'deletion'}, 'A', ('<DEL>',)), 0)
        self.assertIsNone(csq_allele_index({'Allele': 'deletion'}, 'A', ('<DUP>',)))
        self.assertIsNone(csq_allele_index({'Allele': 'TG', 'ALLELE_NUM': '1'}, 'C', ('T',)))
        self.assertIsNone(old_record_context({'ORIG':'1|100|ACG|ATG,A|1'}, (), '1', 101, 'C', ('T',)))
        self.assertIsNone(old_record_context({'ORIG':'|100|ACG|ATG,A|1'}, ('ORIG',), '1', 101, 'C', ('T',)))

if __name__ == '__main__': unittest.main()
