"""Install the pinned, preprocessed public AVI bundle without local conversion."""
from __future__ import annotations

import argparse
import fcntl
import json
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline.avi_dataset import RELEASE, atomic_json, digest, valid_bundle

MIRROR_REVISION = 'b1e9bf7d332b8e68515a639e0e6d91bed43abce5'
MIRROR_URL = f'https://huggingface.co/datasets/luoyiming1991/alphagenome-avi-grch38/resolve/{MIRROR_REVISION}'
# Independently pinned metadata and payloads; never trust a moving branch.
FILES = {
    'manifest.json': (1730, 'e4295fbdc4d825ed84a4e340e92f3bced9cb2ca30f489157b407679ba643dc95'),
    'SOURCE_AND_TERMS.txt': (403, '8728ce71c70e6eeddfcb84a4bb82e330f3b48f206ac0e586b5cda6001e0ab139'),
    'README.md': (5398, '78c6e8379aff254749e3f2709096e3873a43e22eab7593bdbd88dd3dd2a47446'),
    'avi.grch38.vcf.gz': (75764802676, 'a34a9b48b6dbb3c769d86014e7f28062ef65420c5add7e88f30dfa50d5756b75'),
    'avi.grch38.vcf.gz.tbi': (2798486, '26d20b2855a478ec5b44bba8969a893453292bd432e6f969b01dd7a82334bbae'),
}
SETUP_BYTES = 80 * 1024**3


def pinned_file(path: Path, name: str) -> bool:
    size, sha = FILES[name]
    return path.is_file() and path.stat().st_size == size and digest(path) == sha


def fetch(name: str, target: Path, start: float, scale: float) -> None:
    subprocess.run([
        sys.executable, str(Path(__file__).resolve().parents[1] / 'scripts/parallel_fetch.py'),
        f'{MIRROR_URL}/{name}', str(target), '--connections', '8', '--chunk-mib', '128',
        '--progress-start', str(start), '--progress-scale', str(scale),
    ], check=True)


def install(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    # Share the preparation lock with the maintainer-only source converter.
    with (root / '.prepare.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('Another AVI installation is running at this destination') from None
        return _install_locked(root)


def _install_locked(root: Path) -> Path:
    final = root / 'releases' / RELEASE
    print('0%  Verifying existing AlphaGenome AVI files', flush=True)
    manifest_source = final / 'mirror-manifest.json'
    if not manifest_source.is_file():
        manifest_source = final / 'manifest.json'  # Previously prepared local release.
    if all(pinned_file(manifest_source if name == 'manifest.json' else final / name, name)
           for name in FILES) and valid_bundle(final / 'manifest.json'):
        atomic_json(root / 'current.json', {'release': RELEASE})
        print('100%  AlphaGenome AVI already installed and verified', flush=True)
        return final

    work = root / ('.downloading-' + RELEASE)
    work.mkdir(exist_ok=True)
    # Sparse range downloads reserve logical size before writing. Credit only
    # allocated blocks, not st_size, to avoid understating remaining disk needs.
    allocated = sum(min(path.stat().st_size, path.stat().st_blocks * 512)
                    for name in FILES for path in (work / name, work / (name + '.parallel'))
                    if path.is_file())
    if shutil.disk_usage(root).free < max(2 * 1024**3, SETUP_BYTES - allocated):
        raise ValueError('AVI download needs up to 80 GiB free in Annotation datasets storage; choose a larger location or free space and retry')
    total = sum(size for size, _ in FILES.values())
    done = 0
    for name, (size, _) in FILES.items():
        target = work / name
        start, scale = 90 * done / total, 90 * size / total
        if not pinned_file(target, name):
            if target.exists():
                print(f'{start:.1f}%  Discarding checksum-invalid download: {name}', flush=True)
                target.unlink()  # Only our failed staging artifact, never installed data.
            print(f'{start:.1f}%  Downloading prepared AVI: {name}', flush=True)
            fetch(name, target, start, scale)
            print(f'{start + scale:.1f}%  Verifying SHA-256: {name}', flush=True)
            if not pinned_file(target, name):
                target.unlink(missing_ok=True)
                raise ValueError(f'AVI checksum mismatch for {name}; invalid download discarded. Retry Download / resume.')
        done += size
    print('95%  Validating and activating prepared AlphaGenome AVI', flush=True)
    value = json.loads((work / 'manifest.json').read_text(encoding='utf-8'))
    shutil.copyfile(work / 'manifest.json', work / 'mirror-manifest.json')
    # Keep the original pinned manifest intact; local mtime receipts make
    # readiness polling inexpensive without skipping full job-start hashing.
    for name, record in value['files'].items():
        if name not in FILES or (record['size'], record['sha256']) != FILES[name]:
            raise ValueError('AVI mirror manifest disagrees with the pinned payload')
        record['mtime_ns'] = (work / name).stat().st_mtime_ns
    value['mirror'] = {'url': MIRROR_URL, 'revision': MIRROR_REVISION,
                       'manifest_sha256': FILES['manifest.json'][1]}
    atomic_json(work / 'manifest.json', value)
    if not valid_bundle(work / 'manifest.json'):
        raise ValueError('Downloaded AVI bundle failed validation; installation not activated')
    final.parent.mkdir(parents=True, exist_ok=True)
    backup = final.with_name(RELEASE + '.damaged-' + uuid.uuid4().hex)
    if final.exists():
        final.rename(backup)
        print(f'95%  Previous incomplete/damaged release preserved at {backup}', flush=True)
    try:
        work.rename(final)
    except OSError:
        if backup.exists():
            backup.rename(final)
        raise
    atomic_json(root / 'current.json', {'release': RELEASE})
    print('100%  AlphaGenome AVI installed and verified; no local conversion needed', flush=True)
    return final


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    args = parser.parse_args()
    try:
        install(args.root.expanduser().resolve())
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f'ERROR: {error}', flush=True)
        raise SystemExit(1)
