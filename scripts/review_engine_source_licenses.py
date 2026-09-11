#!/usr/bin/env python3
"""Extract source license evidence for CPAN distributions with vague metadata."""
import argparse
import json
from pathlib import Path
import re
import tarfile


def evidence(folder):
    report = json.loads((folder / 'collection.json').read_text())
    for package in report['packages']:
        if package['kind'] != 'cpan' or not set(package['declared_license']) & {'unknown', 'open_source'}:
            continue
        print('\n## ' + package['name'] + ' (' + ', '.join(package['declared_license']) + ')')
        archive = folder / 'cpan' / package['name'] / package['files'][0]['file']
        with tarfile.open(archive) as source:
            candidates = []
            for member in source:
                if not member.isfile() or member.size > 1024 * 1024:
                    continue
                name = Path(member.name).name
                if not (re.match(r'LICENSE|COPYING|COPYRIGHT|README', name, re.I) or name.endswith('.pm')):
                    continue
                text = source.extractfile(member).read().decode('utf-8', errors='replace')
                lines = text.splitlines()
                excerpts = []
                for i, line in enumerate(lines):
                    if re.search(r'license|licence|same terms as perl|permission is hereby|redistribut', line, re.I):
                        excerpt = '\n'.join(lines[max(0, i - 2):i + 13])
                        if excerpt not in excerpts:
                            excerpts.append(excerpt)
                if excerpts:
                    candidates.append((0 if re.match('LICENSE|COPYING|COPYRIGHT', name, re.I) else 1, member.name, excerpts))
            for _, name, excerpts in sorted(candidates)[:2]:
                print('\n' + name + '\n' + '\n…\n'.join(excerpts[:2]))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('folder', type=Path)
    args = parser.parse_args()
    evidence(args.folder)
