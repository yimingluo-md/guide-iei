"""Prepared AVI install/retry tests; synthetic bytes, no network or compiler."""
import contextlib
import fcntl
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline import avi_dataset as avi
from pipeline import avi_mirror as mirror


class AviMirrorTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name) / 'selected storage' / 'alphagenome-avi'
        self.payload = {'avi.grch38.vcf.gz': b'fixture scores',
                        'avi.grch38.vcf.gz.tbi': b'fixture index',
                        'SOURCE_AND_TERMS.txt': b'notice', 'README.md': b'card'}
        value = {'schema': avi.SCHEMA, 'release': avi.RELEASE, 'assembly': 'GRCh38',
                 'rows': avi.EXPECTED_ROWS, 'positions': avi.EXPECTED_ROWS // 3,
                 'files': {name: {'size': len(data), 'sha256': hashlib.sha256(data).hexdigest(),
                                  'mtime_ns': 1}
                           for name, data in self.payload.items() if name.startswith('avi.')}}
        self.payload['manifest.json'] = json.dumps(value).encode()
        pins = {name: (len(data), hashlib.sha256(data).hexdigest())
                for name, data in self.payload.items()}
        self.calls = []
        self.enterContext(patch.object(mirror, 'FILES', pins))
        self.enterContext(patch.object(mirror, 'SETUP_BYTES', 3 * 1024**3))
        self.enterContext(patch.object(mirror.shutil, 'disk_usage', return_value=type('Disk', (), {'free': 5 * 1024**3})()))
        self.enterContext(patch.object(mirror, 'fetch', side_effect=self.fetch))
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))

    # Compatible with Python versions whose unittest lacks enterContext.
    def enterContext(self, cm):
        result = cm.__enter__()
        self.addCleanup(cm.__exit__, None, None, None)
        return result

    def fetch(self, name, target, start, scale):
        self.calls.append(name)
        target.write_bytes(self.payload[name])

    def test_install_and_verified_retry_preserve_pinned_manifest(self):
        final = mirror.install(self.root)
        self.assertEqual(avi.current_bundle(self.root), final / 'manifest.json')
        self.assertTrue(avi.valid_bundle(final / 'manifest.json', strict=True))
        self.assertEqual((final / 'mirror-manifest.json').read_bytes(), self.payload['manifest.json'])
        installed = json.loads((final / 'manifest.json').read_text())
        self.assertEqual(installed['mirror']['revision'], mirror.MIRROR_REVISION)
        # Polling must not rehash a 75 GB payload because of producer timestamps.
        with patch.object(avi, 'digest', side_effect=AssertionError('poll rehashed')):
            self.assertTrue(avi.valid_bundle(final / 'manifest.json'))
        self.calls.clear()
        self.assertEqual(mirror.install(self.root), final)
        self.assertEqual(self.calls, [])

    def test_interruption_reuses_verified_files_and_retains_partial(self):
        def interrupted(name, target, start, scale):
            if name == 'avi.grch38.vcf.gz.tbi':
                Path(str(target) + '.parallel').write_bytes(b'partial')
                raise OSError('interrupted')
            self.fetch(name, target, start, scale)
        with patch.object(mirror, 'fetch', side_effect=interrupted):
            with self.assertRaises(OSError):
                mirror.install(self.root)
        work = self.root / ('.downloading-' + avi.RELEASE)
        self.assertTrue((work / 'avi.grch38.vcf.gz.tbi.parallel').is_file())
        self.assertFalse((self.root / 'current.json').exists())
        self.calls.clear()
        mirror.install(self.root)
        self.assertNotIn('avi.grch38.vcf.gz', self.calls)
        self.assertTrue(avi.current_bundle(self.root))

    def test_bad_checksum_never_activates_and_can_retry(self):
        with patch.object(mirror, 'fetch', side_effect=lambda name, target, *args: target.write_bytes(b'corrupt')):
            with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                mirror.install(self.root)
        self.assertFalse((self.root / 'current.json').exists())
        self.assertTrue(mirror.install(self.root).is_dir())

    def test_repair_keeps_old_release_until_verified(self):
        final = mirror.install(self.root)
        scores = final / 'avi.grch38.vcf.gz'
        scores.write_bytes(b'old damaged data')
        with patch.object(mirror, 'fetch', side_effect=OSError('offline')):
            with self.assertRaises(OSError):
                mirror.install(self.root)
        self.assertEqual(scores.read_bytes(), b'old damaged data')
        mirror.install(self.root)
        backups = list(final.parent.glob('*.damaged-*'))
        self.assertEqual(len(backups), 1)
        self.assertEqual((backups[0] / scores.name).read_bytes(), b'old damaged data')
        self.assertTrue(avi.valid_bundle(final / 'manifest.json', strict=True))

    def test_insufficient_space_and_shared_install_lock(self):
        with patch.object(mirror.shutil, 'disk_usage', return_value=type('Disk', (), {'free': 0})()):
            with self.assertRaisesRegex(ValueError, 'free'):
                mirror.install(self.root)
        self.assertEqual(self.calls, [])
        with (self.root / '.prepare.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(ValueError, 'Another AVI installation'):
                mirror.install(self.root)

    def test_rename_failure_restores_previous_release(self):
        final = mirror.install(self.root)
        (final / 'avi.grch38.vcf.gz').write_bytes(b'old')
        original = Path.rename
        def fail_publish(path, destination):
            if path.name.startswith('.downloading-'):
                raise OSError('publication failed')
            return original(path, destination)
        with patch.object(Path, 'rename', fail_publish):
            with self.assertRaisesRegex(OSError, 'publication failed'):
                mirror.install(self.root)
        self.assertEqual((final / 'avi.grch38.vcf.gz').read_bytes(), b'old')


if __name__ == '__main__':
    unittest.main()
