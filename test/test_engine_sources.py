"""Offline regression checks for maintainer source-package tooling."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
import collect_engine_sources as sources
import package_engine_sources as packaging
import verify_engine_layers as layers


class EngineSourceTests(unittest.TestCase):
    def test_descriptor_requires_sha256_and_rejects_traversal(self):
        digest = 'a' * 64
        self.assertEqual(sources.dsc_checksums('Checksums-Sha256:\n ' + digest + ' 3 x.tar.gz\nFiles:\n'),
                         {'x.tar.gz': (digest, 3)})
        for text in ('Files:\n abc 3 x.tar.gz', 'Checksums-Sha256:\n ' + digest + ' 3 ../x',
                     'Checksums-Sha256:\n no-hash 3 x.tar.gz'):
            with self.assertRaises(ValueError):
                sources.dsc_checksums(text)

    def test_download_rechecks_cached_content_and_upstream_hash(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            expected = hashlib.sha256(b'abc').hexdigest()
            def write(args, **kwargs):
                Path(args[args.index('--output') + 1]).write_bytes(b'abc')
            with patch.object(sources.subprocess, 'run', side_effect=write) as run:
                sources.download('https://example.org/source.tar.gz', directory, expected)
                sources.download('https://example.org/source.tar.gz', directory, expected)
                self.assertEqual(run.call_count, 1)
                (directory / 'source.tar.gz').write_bytes(b'xyz')
                sources.download('https://example.org/source.tar.gz', directory, expected)
                self.assertEqual(run.call_count, 2)
                with self.assertRaisesRegex(ValueError, 'checksum'):
                    sources.download('https://example.org/source.tar.gz', directory, 'b' * 64)

    def test_download_rejects_plain_http(self):
        with self.assertRaises(ValueError):
            sources.download('http://example.org/x.tar.gz', Path('/unused'))

    def test_source_failure_is_persisted_and_never_marked_complete(self):
        inventory = {'image_id': 'sha256:test', 'source_packages': [{'name': 'x', 'version': '1'}],
                     'cpan_distributions': []}
        with tempfile.TemporaryDirectory() as temporary, patch.object(sources, 'ubuntu_source', side_effect=ValueError('missing version')):
            self.assertFalse(sources.collect(inventory, [], Path(temporary), 1))
            report = json.loads((Path(temporary) / 'collection.json').read_text())
            self.assertEqual(report['status'], 'incomplete')
            self.assertEqual(len(report['failures']), 1)

    def test_ubuntu_requires_exact_version_not_nearest_release(self):
        with patch.object(sources, 'get_json', return_value={'entries': []}):
            with self.assertRaisesRegex(ValueError, 'Exact Ubuntu source'):
                sources.ubuntu_source({'name': 'bash', 'version': 'unavailable'}, Path('/unused'))

    def test_runtime_does_not_inherit_deleted_layers(self):
        dockerfile = (ROOT / 'docker/Dockerfile').read_text()
        runtime = dockerfile.split('FROM scratch AS runtime', 1)[1]
        self.assertIn('COPY --from=build / /', runtime)
        self.assertIn('python2 /tmp/guide-iei-prune-kent.py', dockerfile)
        self.assertNotIn('KENT_SRC=', runtime)
        self.assertIn('USER vep', runtime)

    def test_fingerprint_lists_stay_in_sync(self):
        from local_service.workbench_service import CONTAINER_FINGERPRINT_FILES
        script = (ROOT / 'docker/image_fingerprint.sh').read_text()
        import re
        names = re.findall(r'^  "([^"]+)"$', script, re.M)
        self.assertEqual(names, list(CONTAINER_FINGERPRINT_FILES))
        self.assertIn('prune_kent.py', names)
        self.assertIn('UPSTREAM-MODIFICATIONS.txt', names)

    def test_symbol_reader_fails_closed(self):
        spec = importlib.util.spec_from_file_location('prune_kent', ROOT / 'docker/prune_kent.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with patch.object(module.subprocess, 'Popen') as popen:
            popen.return_value.communicate.return_value = (b'', b'broken')
            popen.return_value.returncode = 1
            with self.assertRaisesRegex(RuntimeError, 'Unable to inspect'):
                module.symbols('/fixture/library.so')

    def test_removed_paths_are_rejected_even_in_historical_layers(self):
        for name in ('./opt/vep/src/kent-335_base/src/jkOwnLib/x.c',
                     'plugins/Carol.pm', 'usr/local/lib/perl/Math/CDF.pm',
                     'usr/local/share/perl/.meta/Math-CDF-0.1/install.json'):
            self.assertTrue(layers.forbidden(name))
        self.assertFalse(layers.forbidden('usr/share/doc/guide-iei-kent/audit.json'))

    def test_source_coverage_checks_identity_and_exact_package_set(self):
        inventory = {'image_id': 'same', 'kent_pruned': True,
                     'source_packages': [{'name': 'bash', 'version': '1'}],
                     'cpan_distributions': [], 'python_distributions': []}
        collection = {'image_id': 'same', 'status': 'package_downloads_complete_not_clearance',
                      'failures': [], 'packages': [{'kind': 'ubuntu', 'name': 'bash', 'version': '1'}]}
        native = {'image_id': 'same', 'status': 'native_sources_collected_not_clearance',
                  'source_comparisons': [{'component': 'example'}]}
        layer_report = {'image_id': 'same', 'status': 'no_removed_components_in_any_saved_layer'}
        packaging.validate_coverage(inventory, collection, native, layer_report)
        with self.assertRaisesRegex(ValueError, 'different engine'):
            packaging.validate_coverage(inventory, dict(collection, image_id='old'), native, layer_report)
        with self.assertRaisesRegex(ValueError, 'coverage'):
            packaging.validate_coverage(inventory, dict(collection, packages=[]), native, layer_report)
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            packaging.validate_coverage(inventory, dict(collection, failures=['missing']), native, layer_report)


if __name__ == '__main__':
    unittest.main()
