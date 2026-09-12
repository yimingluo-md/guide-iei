#!/usr/bin/env python3
"""Guide publication must not silently publish the rest of docs or stale output."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from stage_guide_site import stage, ROOT
from check_guide_site import verify


class GuideSiteTests(unittest.TestCase):
    def fixture(self, folder):
        root = Path(folder) / "repo"
        (root / "docs/guide").mkdir(parents=True)
        (root / "docs/assets/img").mkdir(parents=True)
        (root / "docs/_config.yml").write_text("title: User Guide\n")
        (root / "docs/guide.md").write_text("---\ntitle: User Guide\n---\n[chapter](guide/01-start.md)\n")
        (root / "docs/guide/01-start.md").write_text(
            '---\ntitle: Start\n---\n# Start\n[other](02-next.md#heading) '
            '[extra](../FAQ.md#question) ![figure](../assets/img/example.png)\n'
            '```text\n[not a link](missing.md)\n```\n')
        (root / "docs/guide/02-next.md").write_text('---\ntitle: Next\n---\n## Heading\n')
        (root / "docs/FAQ.md").write_text("# Question\nUNREVIEWED EXTRA DOC\n")
        (root / "docs/assets/img/example.png").write_bytes(b"synthetic")
        (root / "docs/assets/img/unused.png").write_bytes(b"unused")
        return root

    def test_allowlist_and_link_rewriting(self):
        with tempfile.TemporaryDirectory() as folder:
            root = self.fixture(folder)
            output = Path(folder) / "site"
            stage(root, output)
            self.assertEqual({p.relative_to(output).as_posix() for p in output.rglob('*') if p.is_file()},
                             {'_config.yml', 'index.md', 'guide/01-start.md', 'guide/02-next.md', 'assets/img/example.png'})
            text = (output / 'guide/01-start.md').read_text()
            self.assertIn('(02-next.html#heading)', text)
            self.assertIn('(https://github.com/yimingluo-md/guide-iei/blob/main/docs/FAQ.md#question)', text)
            self.assertIn('(../assets/img/example.png)', text)
            self.assertIn('[not a link](missing.md)', text)
            self.assertIn('permalink: /guide/01-start.html', text)
            self.assertIn('(guide/01-start.html)', (output / 'index.md').read_text())
            self.assertIn('(02-next.md#heading)', (root / 'docs/guide/01-start.md').read_text())

    def test_refuses_existing_output(self):
        with tempfile.TemporaryDirectory() as folder:
            root = self.fixture(folder)
            output = Path(folder) / 'site'
            output.mkdir()
            stale = output / 'private.md'
            stale.write_text('must not be deleted or published')
            with self.assertRaises(FileExistsError):
                stage(root, output)
            self.assertTrue(stale.exists())

    def test_rejects_missing_or_escaping_links(self):
        with tempfile.TemporaryDirectory() as folder:
            root = self.fixture(folder)
            chapter = root / 'docs/guide/01-start.md'
            for link in ('missing.md', '../../../outside.txt'):
                chapter.write_text('---\ntitle: Start\n---\n[x](' + link + ')')
                with self.assertRaises(ValueError):
                    stage(root, Path(folder) / 'site')

    def test_real_guide_stages_only_chapters_and_referenced_images(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / 'site'
            pages = stage(ROOT, output)
            self.assertEqual(len(pages), 1 + len(list((ROOT / 'docs/guide').glob('*.md'))))
            self.assertFalse((output / 'FAQ.md').exists())
            self.assertFalse((output / 'reference.md').exists())
            self.assertNotIn('ACMG/AMP 2015', (output / 'index.md').read_text())

    def test_rendered_verifier_rejects_extra_pages_and_broken_links(self):
        with tempfile.TemporaryDirectory() as folder:
            root = self.fixture(folder)
            output = Path(folder) / 'site'
            (output / 'guide').mkdir(parents=True)
            (output / 'assets/js').mkdir(parents=True)
            (output / 'index.html').write_text('<a href="/guide-iei/guide/01-start.html#start">Start</a>')
            (output / 'guide/01-start.html').write_text('<h1 id="start">Start</h1>')
            (output / 'guide/02-next.html').write_text('<h1>Next</h1>')
            search = output / 'assets/js/search-data.json'
            search.write_text(json.dumps({'0': {'url': 'https://yimingluo-md.github.io/guide-iei/'}}))
            verify(output, root)
            (output / 'FAQ.html').write_text('Not approved for publication')
            with self.assertRaises(ValueError):
                verify(output, root)
            (output / 'FAQ.html').unlink()
            (output / 'index.html').write_text('<a href="guide/01-start.html#missing">bad</a>')
            with self.assertRaises(ValueError):
                verify(output, root)
            (output / 'index.html').write_text('<img src="assets/img/missing.png">')
            with self.assertRaises(ValueError):
                verify(output, root)
            (output / 'index.html').write_text('<h1>Home</h1>')
            search.write_text(json.dumps({'0': {'url': 'https://yimingluo-md.github.io/guide-iei/FAQ.html'}}))
            with self.assertRaises(ValueError):
                verify(output, root)


if __name__ == '__main__':
    unittest.main()
