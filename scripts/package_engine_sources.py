#!/usr/bin/env python3
"""Package verified companion materials, excluding removed/unrequested sources.

This checks coverage and identity, not legal permission. Publication still
requires the reviewed notices and release checklist.
"""
import argparse
import json
from pathlib import Path
import tarfile
import zipfile

from collect_engine_sources import safe_name, sha256


def package_paths(row):
    if row['kind'] == 'ubuntu':
        return Path('ubuntu') / safe_name(row['name']) / safe_name(row['version'])
    if row['kind'] == 'cpan':
        return Path('cpan') / safe_name(row['name'])
    if row['kind'] == 'python':
        return Path('python') / safe_name(row['name'] + '-' + row['version'])
    raise ValueError('Unknown source package kind')


def validate_coverage(inventory, collection, native, layers):
    image = inventory['image_id']
    if any(item['image_id'] != image for item in (collection, native, layers)):
        raise ValueError('Source materials are for a different engine image')
    if collection.get('status') != 'package_downloads_complete_not_clearance' or collection.get('failures'):
        raise ValueError('Dependency source collection is incomplete')
    if not inventory.get('kent_pruned') or layers.get('status') != 'no_removed_components_in_any_saved_layer':
        raise ValueError('Runtime cleanup/layer audit is missing')
    if native.get('status') != 'native_sources_collected_not_clearance' or not native.get('source_comparisons'):
        raise ValueError('Native source comparison is incomplete')
    expected = {('ubuntu', row['name'], row['version']) for row in inventory['source_packages']}
    expected |= {('cpan', row['distribution'], '') for row in inventory['cpan_distributions']}
    expected |= {('python', row['name'], row['version']) for row in inventory['python_distributions']}
    actual = {(row['kind'], row['name'], row.get('version', '')) for row in collection['packages']}
    if expected != actual or len(actual) != len(collection['packages']):
        raise ValueError('Source package coverage differs from installed dependency inventory')


def package(folder, inventory_path, layers_path, output, version):
    inventory = json.loads(inventory_path.read_text())
    collection = json.loads((folder / 'collection.json').read_text())
    native = json.loads((folder / 'native-collection.json').read_text())
    layers = json.loads(layers_path.read_text())
    validate_coverage(inventory, collection, native, layers)
    selected = set()
    for row in collection['packages']:
        directory = package_paths(row)
        for item in row['files']:
            relative = directory / safe_name(item['file'])
            path = folder / relative
            if path.is_symlink() or sha256(path) != item['sha256'] or path.stat().st_size != item['bytes']:
                raise ValueError('Changed source archive: ' + str(relative))
            selected.add(relative)
            selected.add(Path(str(relative) + '.download.json'))
    for directory in ('build-materials', 'native-upstream'):
        selected.update(path.relative_to(folder) for path in (folder / directory).rglob('*')
                        if path.is_file() and not path.name.endswith(('.part', '.tmp')))
    for name in ('native-build-trees.tar.gz', 'collection.json', 'native-collection.json', 'THIRD-PARTY-SOURCES.md'):
        selected.add(Path(name))
    if sha256(folder / 'native-build-trees.tar.gz') != native['files'][0]['sha256']:
        raise ValueError('Native source archive changed since collection')
    output.mkdir(parents=True, exist_ok=True)
    name = 'GUIDE-IEI-engine-sources-' + safe_name(version) + '-linux-arm64.tar.gz'
    destination = output / name
    if destination.exists():
        raise ValueError('Source package already exists; choose a fresh output directory')
    receipts = []
    for relative in sorted(selected):
        path = folder / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError('Missing or linked source material: ' + str(relative))
        if path.name.endswith('.download.json'):
            receipt = json.loads(path.read_text())
            downloaded = path.parent / safe_name(receipt['file'])
            if downloaded.is_symlink() or sha256(downloaded) != receipt['sha256'] or downloaded.stat().st_size != receipt['bytes']:
                raise ValueError('Changed downloaded source: ' + str(downloaded))
        receipts.append({'path': str(relative), 'bytes': path.stat().st_size, 'sha256': sha256(path)})
    index = {'schema_version': 1, 'image_id': inventory['image_id'],
             'source_fingerprint': inventory['source_fingerprint'], 'files': receipts}
    index_path = output / 'source-file-index.json'
    index_path.write_text(json.dumps(index, indent=2) + '\n')
    with tarfile.open(destination, 'w:gz') as bundle:
        for relative in sorted(selected):
            bundle.add(folder / relative, arcname=str(relative), recursive=False)
        bundle.add(index_path, arcname='source-file-index.json')
        bundle.add(inventory_path, arcname='runtime-inventory.json')
        bundle.add(layers_path, arcname='layer-verification.json')
    manifest = {'schema_version': 1, 'image_id': inventory['image_id'],
                'source_fingerprint': inventory['source_fingerprint'], 'version': version,
                'archive': name, 'bytes': destination.stat().st_size, 'sha256': sha256(destination),
                'package_count': len(collection['packages']), 'status': 'coverage_verified_review_required'}
    (output / 'engine-sources.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print('Packaged ' + str(destination))


def verify_for_app(source_folder, app_archive):
    manifest = json.loads((source_folder / 'engine-sources.json').read_text())
    with zipfile.ZipFile(app_archive) as app:
        build = json.loads(app.read('GUIDE-IEI.app/Contents/Resources/application/desktop-build.json'))
    engine = build['bundled_engine']
    for key in ('image_id', 'source_fingerprint'):
        if manifest[key] != engine[key]:
            raise ValueError('Source companion does not match app engine: ' + key)
    if manifest.get('version') != build['version'] or manifest.get('status') != 'coverage_verified_review_required':
        raise ValueError('Invalid source companion version/status')
    archive = source_folder / safe_name(manifest['archive'])
    if archive.is_symlink() or archive.stat().st_size != manifest['bytes'] or sha256(archive) != manifest['sha256']:
        raise ValueError('Source companion checksum mismatch')
    return archive


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--folder', type=Path, required=True)
    parser.add_argument('--inventory', type=Path)
    parser.add_argument('--layers', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--version')
    parser.add_argument('--verify-app', type=Path)
    args = parser.parse_args()
    if args.verify_app:
        print(verify_for_app(args.folder, args.verify_app))
    else:
        if not all((args.inventory, args.layers, args.output, args.version)):
            parser.error('Packaging requires --inventory, --layers, --output and --version')
        package(args.folder, args.inventory, args.layers, args.output, args.version)
