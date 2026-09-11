#!/usr/bin/env python3
"""Inspect every saved image layer, not just the merged container filesystem."""
import argparse
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile


def forbidden(path):
    path = path.lstrip('./')
    return (path.startswith('opt/vep/src/kent-335_base/') or path == 'plugins/Carol.pm'
            or '/Math/CDF' in path or '/Math::CDF' in path or '/Math-CDF-0.1/' in path)


def verify(runtime, image):
    info = json.loads(subprocess.check_output([runtime, 'image', 'inspect', image], text=True))[0]
    with tempfile.TemporaryDirectory(prefix='guide-iei-layer-review-') as temporary:
        archive = Path(temporary) / 'engine.tar'
        subprocess.run([runtime, 'image', 'save', '-o', str(archive), info['Id']], check=True)
        with tarfile.open(archive) as saved:
            manifest = json.load(saved.extractfile('manifest.json'))
            if len(manifest) != 1:
                raise ValueError('Expected exactly one saved image')
            checked = []
            for name in manifest[0]['Layers']:
                count = 0
                with tarfile.open(fileobj=saved.extractfile(name), mode='r|*') as layer:
                    for member in layer:
                        if forbidden(member.name):
                            raise ValueError('Removed component remains in distributed layer: ' + member.name)
                        count += 1
                checked.append({'layer': name, 'entries_checked': count})
    return {'image_id': info['Id'], 'status': 'no_removed_components_in_any_saved_layer', 'layers': checked}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', default='docker')
    parser.add_argument('--image', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = verify(args.runtime, args.image)
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(report['status'] + ': ' + report['image_id'])
