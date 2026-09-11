#!/usr/bin/env python3
"""Collect exact-version dependency sources; download completeness is not legal clearance.

Output is a maintainer-only companion directory, never patient data or an app
runtime dependency. Re-running verifies cached bytes and retries failed items.
Ubuntu source components are checked against their .dsc SHA-256 declarations.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import re
import subprocess
import time
from urllib.parse import urlencode, urlparse, unquote
from urllib.request import Request, urlopen


def sha256(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def get_json(url):
    for attempt in range(4):
        try:
            with urlopen(Request(url, headers={'User-Agent': 'GUIDE-IEI-source-collector/1'}), timeout=90) as stream:
                return json.load(stream)
        except OSError:
            if attempt == 3:
                raise
            time.sleep(2 ** attempt)


def safe_name(name):
    if not name or name in ('.', '..') or not re.fullmatch(r'[A-Za-z0-9_.+~:-]+', name):
        raise ValueError('Unsafe source filename: ' + name)
    return name


def download(url, folder, expected=None):
    if urlparse(url).scheme != 'https':
        raise ValueError('Source downloads require HTTPS')
    name = safe_name(unquote(urlparse(url).path.rsplit('/', 1)[-1]))
    path = folder / name
    folder.mkdir(parents=True, exist_ok=True)
    receipt = folder / (name + '.download.json')
    if path.is_file() and receipt.is_file():
        old = json.loads(receipt.read_text())
        actual = sha256(path)
        if old.get('url') == url and old.get('sha256') == actual and (expected is None or expected == actual):
            return old
    partial = folder / (name + '.part')
    subprocess.run(['curl', '--fail', '--location', '--proto', '=https', '--proto-redir', '=https',
                    '--retry', '3', '--connect-timeout', '30', '--max-time', '1800',
                    '--silent', '--show-error', '--output', str(partial), url], check=True)
    actual = sha256(partial)
    if expected and expected != actual:
        raise ValueError('Source checksum mismatch: ' + name)
    partial.replace(path)
    result = {'url': url, 'file': name, 'bytes': path.stat().st_size, 'sha256': actual,
              'upstream_sha256_verified': expected is not None}
    receipt.write_text(json.dumps(result, indent=2) + '\n')
    return result


def dsc_checksums(text):
    result = {}
    active = False
    for line in text.splitlines():
        if line == 'Checksums-Sha256:':
            active = True
        elif active and line.startswith(' '):
            digest, size, name = line.split()
            if not re.fullmatch(r'[a-f0-9]{64}', digest) or int(size) < 0:
                raise ValueError('Invalid .dsc checksum')
            result[safe_name(name)] = (digest, int(size))
        elif active:
            break
    if not result:
        raise ValueError('No SHA-256 components in .dsc')
    return result


def ubuntu_source(row, output):
    name, version = row['name'], row['version']
    folder = output / 'ubuntu' / safe_name(name) / safe_name(version)
    query = urlencode({'ws.op': 'getPublishedSources', 'source_name': name,
                       'version': version, 'exact_match': 'true'})
    data = get_json('https://api.launchpad.net/1.0/ubuntu/+archive/primary?' + query)
    entries = data['entries']
    matches = [item for item in entries if item['source_package_name'] == name
               and item['source_package_version'] == version]
    if not matches:
        raise ValueError('Exact Ubuntu source version unavailable: ' + name + '=' + version)
    # Prefer the series shipped in the image, but source/version identity is
    # verified independently because identical source can be copied to pockets.
    matches.sort(key=lambda item: not item['distro_series_link'].endswith('/jammy'))
    urls = get_json(matches[0]['self_link'] + '?ws.op=sourceFileUrls')
    urls = {unquote(urlparse(url).path.rsplit('/', 1)[-1]): url for url in urls}
    descriptors = [key for key in urls if key.endswith('.dsc')]
    if len(descriptors) != 1:
        raise ValueError('Expected one .dsc for ' + name)
    descriptor = download(urls[descriptors[0]], folder)
    text = (folder / descriptors[0]).read_text()
    if not re.search(r'^Source: ' + re.escape(name) + r'$', text, re.M) or not re.search(r'^Version: ' + re.escape(version) + r'$', text, re.M):
        raise ValueError('.dsc source/version mismatch')
    files = [descriptor]
    for filename, (digest, size) in dsc_checksums(text).items():
        if filename not in urls:
            raise ValueError('Missing .dsc component URL: ' + filename)
        item = download(urls[filename], folder, digest)
        if item['bytes'] != size:
            raise ValueError('.dsc component size mismatch')
        files.append(item)
    return {'kind': 'ubuntu', 'name': name, 'version': version,
            'publishing_record': matches[0]['self_link'], 'files': files}


def cpan_source(row, output):
    path = row['cpan_path']
    if not re.fullmatch(r'[A-Za-z0-9_+./~-]+', path) or '..' in Path(path).parts:
        raise ValueError('Invalid CPAN distribution path')
    folder = output / 'cpan' / safe_name(row['distribution'])
    url = 'https://cpan.metacpan.org/authors/id/' + path
    try:
        item = download(url, folder)
    except subprocess.CalledProcessError:
        item = download('https://backpan.perl.org/authors/id/' + path, folder)
    return {'kind': 'cpan', 'name': row['distribution'], 'declared_license': row['license'], 'files': [item]}


def python_source(row, output):
    data = get_json('https://pypi.org/pypi/' + row['name'] + '/' + row['version'] + '/json')
    sources = [item for item in data['urls'] if item['packagetype'] == 'sdist']
    if not sources:
        raise ValueError('No Python source distribution: ' + row['name'])
    item = sources[0]
    return {'kind': 'python', 'name': row['name'], 'version': row['version'],
            'files': [download(item['url'], output / 'python' / safe_name(row['name'] + '-' + row['version']), item['digests']['sha256'])]}


def collect(inventory, python_packages, output, workers):
    output.mkdir(parents=True, exist_ok=True)
    tasks = [(ubuntu_source, row) for row in inventory['source_packages']]
    tasks += [(cpan_source, row) for row in inventory['cpan_distributions']]
    tasks += [(python_source, row) for row in python_packages]
    results, failures = [], []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(fn, row, output): row for fn, row in tasks}
        for future in as_completed(futures):
            row = futures[future]
            try:
                result = future.result()
                results.append(result)
                print('Downloaded ' + result['kind'] + ': ' + result['name'], flush=True)
            except Exception as exc:
                failures.append({'request': row, 'error': str(exc)})
                print('UNRESOLVED: ' + str(exc), flush=True)
            report = {'schema_version': 1, 'image_id': inventory['image_id'],
                      'status': 'incomplete' if failures or len(results) != len(tasks) else 'package_downloads_complete_not_clearance',
                      'expected_packages': len(tasks), 'completed_packages': len(results),
                      'packages': sorted(results, key=lambda item: (item['kind'], item['name'])), 'failures': failures,
                      'limitations': ['Native source-built components and local build/patch materials must also be included.',
                                      'CPAN license metadata is not a substitute for reviewing source license texts.',
                                      'Downloads alone do not establish redistribution clearance.']}
            temporary = output / 'collection.json.tmp'
            temporary.write_text(json.dumps(report, indent=2) + '\n')
            temporary.replace(output / 'collection.json')
    return not failures


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inventory', type=Path, required=True)
    parser.add_argument('--python-packages', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--workers', type=int, default=4, choices=range(1, 9))
    args = parser.parse_args()
    raise SystemExit(0 if collect(json.loads(args.inventory.read_text()),
        json.loads(args.python_packages.read_text()), args.output.resolve(), args.workers) else 1)
