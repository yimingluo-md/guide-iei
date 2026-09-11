#!/usr/bin/env python3
"""Collect native build materials from an immutable, Kent-pruned engine."""
import argparse
import json
from pathlib import Path
import subprocess
import tarfile

from collect_engine_sources import download, get_json, sha256

ROOT = Path(__file__).resolve().parents[1]


def collect(runtime, image, output):
    info = json.loads(subprocess.check_output([runtime, 'image', 'inspect', image], text=True))[0]
    image_id = info['Id']
    output.mkdir(parents=True, exist_ok=True)
    def read(*args):
        return subprocess.check_output([runtime, 'run', '--rm', '--pull=never', '--network=none',
                                       '--entrypoint', args[0], image_id, *args[1:]], timeout=120)
    # This also ensures an unpruned image cannot enter the source companion.
    audit = json.loads(read('cat', '/usr/share/doc/guide-iei-kent/audit.json'))
    if audit['jkOwnLib_symbols_in_retained_ELF']:
        raise ValueError('Kent audit contains restricted linked symbols')
    archive = output / 'native-build-trees.tar.gz'
    with archive.open('wb') as stream:
        subprocess.run([runtime, 'run', '--rm', '--pull=never', '--network=none', '--entrypoint', 'tar', image_id,
            '--exclude=.git', '--exclude=*.o', '--exclude=*.pico', '--exclude=*.so', '--exclude=*.a',
            '-czf', '-', '-C', '/', 'opt/vep/src', 'plugins', 'usr/share/doc/guide-iei-kent',
            'usr/share/doc/guide-iei-engine', 'usr/include/mysql/my_config.h'], stdout=stream, check=True)
    files = [{'file': archive.name, 'sha256': sha256(archive), 'bytes': archive.stat().st_size,
              'source': 'Exact source files retained in ' + image_id}]
    materials = output / 'build-materials'
    materials.mkdir(exist_ok=True)
    for relative in ('docker/Dockerfile', 'docker/build.sh', 'docker/image_fingerprint.sh',
                     'docker/prune_kent.py', 'docker/UPSTREAM-MODIFICATIONS.txt',
                     'docker/PromoterAI.pm', 'docker/LoGoFunc.pm', 'docker/IndexedScores.pm',
                     'docker/.dockerignore'):
        destination = materials / Path(relative).name
        destination.write_bytes((ROOT / relative).read_bytes())
    # Recover original layer commands too; a flattened image's own history
    # cannot explain inherited native build steps.
    upstream = get_json('https://api.github.com/repos/Ensembl/ensembl-vep/contents/docker/Dockerfile?ref=release%2F113.4')
    files.append(download(upstream['download_url'], materials / 'upstream-vep'))
    for filename in ('get_dependencies.sh', 'build_c.sh'):
        files.append(download('https://raw.githubusercontent.com/Ensembl/ensembl-vep/release/113.4/travisci/' + filename,
                              materials / 'upstream-vep'))
    pins = [('bcftools', '1.20'), ('htslib', '1.20')]
    module = get_json('https://api.github.com/repos/samtools/htslib/contents/htscodecs?ref=1.20')
    if module.get('type') != 'submodule' or module.get('submodule_git_url') != 'https://github.com/samtools/htscodecs.git':
        raise ValueError('Unexpected HTSlib submodule')
    pins.append(('htscodecs', module['sha']))
    for project, ref in pins:
        files.append(download('https://github.com/samtools/' + project + '/archive/' + ref + '.tar.gz',
                              output / 'native-upstream' / project))
    files.append(download('https://raw.githubusercontent.com/freeseek/score/909d23019e19aeadf3bf6fe1407fd6afc094592a/liftover.c',
                          output / 'native-upstream' / 'liftover'))
    # Upstream removed some .c files and Makefile.PL files after compilation.
    # Restore pinned source archives, and compare surviving source files so a
    # release label is not silently substituted for a different build.
    recovered = [('bioperl/bioperl-ext', '73138e9f26b9cb6321288bff4fe2516e862aa975', 'bioperl-ext'),
                 ('Ensembl/Bio-HTS', 'release/v2.11', 'Bio-HTS'),
                 ('Ensembl/ensembl-xs', '2.3.2', 'ensembl-xs'),
                 ('samtools/htslib', '1.9', 'htslib')]
    comparisons = []
    for repository, ref, directory in recovered:
        folder = output / 'native-upstream' / directory
        item = download('https://github.com/' + repository + '/archive/' + ref + '.tar.gz', folder)
        files.append(item)
        matched = []
        with tarfile.open(folder / item['file']) as upstream_tar, tarfile.open(archive) as actual_tar:
            actual_members = {member.name: member for member in actual_tar if member.isfile()}
            for member in upstream_tar:
                if not member.isfile():
                    continue
                relative = member.name.split('/', 1)[-1]
                if not relative.endswith(('.c', '.h', '.xs', '.pm')):
                    continue
                target = 'opt/vep/src/' + directory + '/' + relative
                if target not in actual_members:
                    continue
                if upstream_tar.extractfile(member).read() != actual_tar.extractfile(actual_members[target]).read():
                    raise ValueError('Recovered native source differs from image: ' + target)
                matched.append(relative)
        if not matched:
            raise ValueError('No source files matched for ' + directory)
        comparisons.append({'component': directory, 'ref': ref, 'matched_files': matched})
    result = {'schema_version': 1, 'image_id': image_id, 'files': files, 'source_comparisons': comparisons, 'htscodecs_commit': module['sha'],
              'status': 'native_sources_collected_not_clearance',
              'notes': ['Native Kent sources and permission notice are the exact linked subset; jkOwnLib is not included.',
                        'Ubuntu sources, CPAN sources, Python sources and these build materials form one companion package.',
                        'Compare upstream build instructions with recorded original-image history before publication.']}
    (output / 'native-collection.json').write_text(json.dumps(result, indent=2) + '\n')
    print('Collected native build materials for ' + image_id)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', default='docker')
    parser.add_argument('--image', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    collect(args.runtime, args.image, args.output.resolve())
