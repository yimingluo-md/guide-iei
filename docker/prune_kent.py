#!/usr/bin/env python
"""Remove unused legacy Kent build artifacts, preserving the linked source subset.

Runs only in the disposable image build stage (Python 2.7 or 3). Fail closed
if proprietary-directory symbols appear in a retained ELF object. The final
Docker stage must COPY this filesystem from scratch so deleted lower layers
are not distributed. This is a narrowly scoped build audit, not legal advice.
"""
from __future__ import print_function
import glob
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess

KENT = '/opt/vep/src/kent-335_base/src'
DEST = '/usr/share/doc/guide-iei-kent'


def output(args):
    return subprocess.check_output(args).decode('utf-8')


def symbols(path, dynamic=False):
    args = ['nm', '--defined-only', '-g']
    if dynamic:
        args.append('-D')
    args.append(path)
    process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout, stderr = process.communicate()
    if process.returncode:
        raise RuntimeError('Unable to inspect ELF symbols: ' + path)
    return set(line.split()[-1] for line in stdout.decode('utf-8').splitlines() if len(line.split()) == 3)


def digest(path):
    value = hashlib.sha256()
    with open(path, 'rb') as stream:
        while True:
            block = stream.read(1024 * 1024)
            if not block:
                break
            value.update(block)
    return value.hexdigest()


def remove_unused_math_cdf():
    # Only the upstream Carol plugin imports this old module. GUIDE-IEI does
    # not expose Carol; dbNSFP's precomputed scores do not use this plugin.
    # Its bundled DCDFLIB includes ACM code with noncommercial conditions.
    # Check other Perl consumers before removing the exact installed files.
    for root in ('/plugins', '/opt/vep', '/usr/local/share/perl', '/usr/local/lib'):
        for directory, dirs, names in os.walk(root):
            dirs[:] = [name for name in dirs if name not in ('.git', '.meta')]
            for name in names:
                if not name.endswith('.pm'):
                    continue
                path = os.path.join(directory, name)
                if path == '/plugins/Carol.pm' or path.endswith('/Math/CDF.pm'):
                    continue
                with open(path, 'rb') as stream:
                    if re.search(br'(?:use|require)\s+Math::CDF', stream.read()):
                        raise RuntimeError('Unexpected Math::CDF consumer: ' + path)
    exact = ['/plugins/Carol.pm']
    for pattern in ('/usr/local/lib/*/perl/*/Math/CDF.pm',
                    '/usr/local/lib/*/perl/*/auto/Math/CDF',
                    '/usr/local/share/perl/*/*/.meta/Math-CDF-0.1',
                    '/usr/local/share/man/man3/Math::CDF.3pm',
                    '/usr/local/man/man3/Math::CDF.3pm'):
        exact.extend(glob.glob(pattern))
    removed = []
    for path in exact:
        if not os.path.lexists(path):
            continue
        if os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path)
        else:
            os.unlink(path)
        removed.append(path)
    if not any(path.endswith('CDF.pm') for path in removed):
        raise RuntimeError('Expected Math::CDF installation not found')
    return removed


def main():
    if os.path.exists(DEST):
        raise RuntimeError('Refusing to overwrite a prior Kent audit')
    proprietary = glob.glob(KENT + '/jkOwnLib/*.o')
    if not proprietary:
        raise RuntimeError('Expected original jkOwnLib objects for symbol comparison')
    restricted = set()
    for path in proprietary:
        restricted.update(symbols(path))
    if not restricted:
        raise RuntimeError('No jkOwnLib symbols found; cannot validate removal')
    candidates = glob.glob('/usr/local/lib/*/perl/*/auto/Bio/DB/BigFile/BigFile.so')
    if len(candidates) != 1:
        raise RuntimeError('Expected exactly one Bio::DB::BigFile shared library')
    bigfile = candidates[0]
    linked = symbols(bigfile, dynamic=True)
    if not linked:
        raise RuntimeError('BigFile has no exported symbols')
    removed_math = remove_unused_math_cdf()
    checked = []
    for root in ('/usr/local', '/plugins', '/opt/vep'):
        for directory, dirs, names in os.walk(root):
            dirs[:] = [name for name in dirs if name not in ('.git', 'kent-335_base')]
            for name in names:
                path = os.path.join(directory, name)
                if os.path.islink(path) or name.endswith(('.o', '.a')):
                    continue
                with open(path, 'rb') as stream:
                    if stream.read(4) != b'\x7fELF':
                        continue
                found = symbols(path, dynamic=True) | symbols(path)
                overlap = found & restricted
                if overlap:
                    raise RuntimeError('Retained binary contains jkOwnLib symbols: ' + path + ': ' + ', '.join(sorted(overlap)))
                checked.append({'path': path, 'sha256': digest(path), 'symbol_count': len(found)})
    os.makedirs(DEST + '/source')
    objects = []
    sources = set()
    for path in sorted(glob.glob(KENT + '/lib/*.o') + glob.glob(KENT + '/lib/font/*.o')):
        if not (symbols(path) & linked):
            continue
        source = path[:-2] + '.c'
        if not os.path.isfile(source):
            raise RuntimeError('Missing matching Kent source: ' + source)
        objects.append(os.path.relpath(path, KENT))
        dependencies = output(['gcc', '-MM', '-DUSE_SSL', '-D_FILE_OFFSET_BITS=64',
                               '-D_LARGEFILE_SOURCE', '-D_GNU_SOURCE', '-I' + KENT + '/inc', source])
        for dep in shlex.split(dependencies.replace('\\\n', ' ').split(':', 1)[1]):
            dep = os.path.realpath(dep)
            if not dep.startswith(KENT + '/'):
                raise RuntimeError('Unexpected non-system build dependency: ' + dep)
            sources.add(dep)
    if not objects:
        raise RuntimeError('No linked Kent objects identified')
    sources.add(KENT + '/lib/README')
    files = []
    for source in sorted(sources):
        with open(source, 'rb') as stream:
            content = stream.read()
        # Do not redistribute a source subset containing the old restrictive
        # graphics/alignment headers under a general-library assumption.
        if re.search(br'non[- ]commercial|commercial.{0,60}agreement|personal, academic', content, re.I):
            raise RuntimeError('Review required for linked source terms: ' + source)
        relative = os.path.relpath(source, KENT)
        destination = DEST + '/source/' + relative
        parent = os.path.dirname(destination)
        if not os.path.isdir(parent):
            os.makedirs(parent)
        shutil.copy2(source, destination)
        files.append({'path': relative, 'sha256': digest(destination)})
    shutil.copy2(KENT + '/lib/README', DEST + '/LICENSE-KENT-LIB.txt')
    makefile = 'CC ?= gcc\nCFLAGS = -O -g -fPIC -DUSE_SSL -D_FILE_OFFSET_BITS=64 -D_LARGEFILE_SOURCE -D_GNU_SOURCE -Iinc\n'
    makefile += 'OBJECTS = ' + ' '.join(objects) + '\nall: lib/$(shell uname -m)/jkweb.a\n'
    makefile += 'lib/$(shell uname -m)/jkweb.a: $(OBJECTS)\n\tmkdir -p $(@D)\n\tar rcs $@ $(OBJECTS)\n'
    with open(DEST + '/source/Makefile', 'w') as stream:
        stream.write(makefile)
    report = {'schema_version': 1, 'kent_version': '335_base',
              'removed': '/opt/vep/src/kent-335_base',
              'jkOwnLib_symbol_count': len(restricted), 'jkOwnLib_symbols_in_retained_ELF': [],
              'checked_ELF': checked, 'bigfile': bigfile, 'bigfile_sha256': digest(bigfile),
              'linked_objects': objects, 'preserved_sources': files,
              'unused_math_cdf_and_carol_removed': removed_math,
              'limitations': ['Symbol comparison is supporting evidence; also verify actual BigWig queries and final image layers.',
                              'The generated subset Makefile is a GUIDE-IEI build aid; the runtime BigFile binary is unchanged.']}
    with open(DEST + '/audit.json', 'w') as stream:
        json.dump(report, stream, indent=2, sort_keys=True)
        stream.write('\n')
    # Exact validated build tree, inside an ephemeral Docker build only.
    shutil.rmtree('/opt/vep/src/kent-335_base')
    print('Kent cleanup: %d ELF files checked, %d linked objects preserved; jkOwnLib removed' % (len(checked), len(objects)))


if __name__ == '__main__':
    main()
