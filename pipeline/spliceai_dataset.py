"""Install and validate the unchanged, chromosome-sharded MANE 1.5 release.

The manifest is published last. A failed installation keeps resumable staging
files and never replaces a working table. No prediction transformation occurs.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
PIN = ROOT / "config/spliceai-mane-v1.5.json"
FORMAT = "mane_v1.5_sharded"
BASE = "https://huggingface.co/datasets/luoyiming1991/spliceai-mane-v1.5-d500-m1-snv/resolve/90445b422a04dafd620c206093020ddbdc7817ba/"
NOTICES = {
    "LICENSE.md": "b1c450715e42435fd3e4f2dec9d0f65ff2340a3ca81436ab1bccfb1403bc7f3d",
    "NOTICE.md": "51a6867c8eae3b0043fd4a594e22310d342a452e73ca1520fb5926731000c063",
}


def full_install_config(configured=None):
    """Release-owned upgrade target, independent of preserved legacy YAML.

    Keep an explicitly configured sharded destination. Legacy and compact
    provider paths must never become destinations for the full release.
    """
    if configured and configured.get("format") == FORMAT and configured.get("snv"):
        return dict(configured)
    return {"enabled": True, "required": True, "format": FORMAT,
            "version": "MANE v1.5 D=500 M=1",
            "snv": "references/spliceai/mane-v1.5-d500-m1/manifest.json"}


def prefer_legacy(block, resolve):
    """Keep an existing v1.4 table usable until the explicit full upgrade."""
    if block.get("format") != FORMAT or not block.get("snv"):
        return
    target = resolve(block["snv"])
    if target and not Path(target).exists() and Path(target).name == "manifest.json":
        old = Path(target).parent.parent / "spliceai_scores.masked.snv.ensembl_mane_v1.4.grch38.vcf.gz"
        if old.is_file() and Path(str(old) + ".tbi").is_file():
            block.pop("format", None)
            block.update(snv=str(old), version="MANE v1.4 (legacy)")


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(8 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def assets(release):
    for row in release["files"]:
        yield row["vcf"], row["vcf_bytes"], row["vcf_sha256"]
        yield row["index"], row["index_bytes"], row["index_sha256"]
    for name, sha in NOTICES.items():
        yield name, None, sha


def validate(path, *, strict=False):
    """Metadata-only by default; only explicit strict checks read payloads.

    Old three-element receipts are compatible: ctime changes on migration,
    chmod and hard-link cleanup without any change in the file contents.
    Metadata is a readiness shortcut, not a cryptographic integrity guarantee.
    """
    path = Path(path)
    try:
        release = json.loads(path.read_text())
        if release != json.loads(PIN.read_text()):
            raise ValueError("release manifest differs from the pinned MANE 1.5 dataset")
        receipt = {} if strict else json.loads((path.parent / "installation.json").read_text())
        for name, size, sha in assets(release):
            file = path.parent / name
            stat = file.stat()
            if file.is_symlink() or not file.is_file() or (size is not None and stat.st_size != size):
                raise ValueError(f"missing or incomplete chromosome asset: {name}")
            signature = [stat.st_size, stat.st_mtime_ns]
            if strict:
                if digest(file) != sha:
                    raise ValueError(f"checksum mismatch: {name}")
                after = file.stat()
                if (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns) != (
                        after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                    raise ValueError(f"file changed during verification: {name}")
            else:
                saved = receipt.get("files", {}).get(name)
                if (not isinstance(saved, list) or len(saved) not in (2, 3)
                        or any(type(value) is not int for value in saved)
                        or saved[:2] != signature):
                    raise ValueError(f"verification required for {name}; run the SpliceAI installation/repair action")
        return release
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise ValueError(f"Full SpliceAI installation needs repair: {exc}") from exc


def _signatures(directory, release):
    return {name: [stat.st_size, stat.st_mtime_ns]
            for name, _, _ in assets(release)
            for stat in [(directory / name).stat()]}


def _write_receipt(directory, signatures):
    """Publish verification metadata atomically; callers hold the install lock."""
    receipt = {"source": BASE, "files": signatures}
    temporary = None
    try:
        # Keep a crash-orphaned temporary file outside the dedicated payload
        # directory so it cannot block a later repair as an unrelated asset.
        with tempfile.NamedTemporaryFile(mode="w", dir=directory.parent,
                                         prefix=f".{directory.name}-receipt-", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(json.dumps(receipt) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, directory / "installation.json")
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def valid(path):
    if not path:
        return False
    try:
        validate(path)
        return True
    except ValueError:
        return False


def install(path, run=subprocess.run):
    import fcntl
    import uuid
    path = Path(path).resolve()
    if path.name != "manifest.json":
        raise ValueError("Full SpliceAI destination must end in manifest.json")
    if path.parent.exists() and any(path.parent.iterdir()):
        allowed = {"manifest.json", "installation.json", "data", *NOTICES,
                   *(n + suffix for n in NOTICES for suffix in (".lock", ".etag"))}
        if not (path.parent / "installation.json").is_file() or any(
                entry.name not in allowed for entry in path.parent.iterdir()):
            raise ValueError("Use a dedicated SpliceAI installation directory; destination contains unrelated files")
    path.parent.parent.mkdir(parents=True, exist_ok=True)
    lock = path.parent.with_name(path.parent.name + ".install.lock")
    with lock.open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("Full SpliceAI installation is already running") from None
        release = json.loads(PIN.read_text())
        # Explicit install/repair, never a request-handler readiness check.
        # Reverify stale metadata once and refresh its receipt without making
        # a second 34 GB installation. Corruption must fall through to repair.
        try:
            signatures = _signatures(path.parent, release)
            validate(path, strict=True)
            if signatures != _signatures(path.parent, release):
                raise ValueError("SpliceAI files changed during verification")
        except (OSError, ValueError):
            pass
        else:
            _write_receipt(path.parent, signatures)
            print("=== Full SpliceAI MANE 1.5 already verified ===", flush=True)
            return
        stage = path.parent.with_name(path.parent.name + ".preparing")
        if stage.is_symlink():
            raise ValueError("SpliceAI staging directory must not be a symbolic link")
        stage.mkdir(parents=True, exist_ok=True)
        # Repair only damaged/missing assets. Hard links avoid another 34 GB
        # copy and keep good existing data available until atomic publication.
        for name, size, sha in assets(release):
            old, target = path.parent / name, stage / name
            if target.parent.is_symlink():
                raise ValueError("SpliceAI staging data directory must not be a symbolic link")
            if not target.exists() and old.is_file() and not old.is_symlink():
                if (size is None or old.stat().st_size == size) and digest(old) == sha:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    os.link(old, target)
        needed = sum(max(0, (size or 0) - ((stage / name).stat().st_size if (stage / name).is_file() else 0))
                     for name, size, _ in assets(release)) + 2 * 1024**3
        if shutil.disk_usage(stage).free < needed:
            raise ValueError(f"Full SpliceAI requires approximately {needed / 1024**3:.1f} GiB additional free space")
        for name, size, sha in assets(release):
            target = stage / name
            if target.parent.is_symlink():
                raise ValueError("SpliceAI staging data directory must not be a symbolic link")
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.is_symlink():
                raise ValueError("SpliceAI download targets must not be symbolic links")
            print(f"=== Full SpliceAI MANE 1.5: {name} ===", flush=True)
            if target.is_file() and (size is None or target.stat().st_size == size) and digest(target) == sha:
                continue
            run([sys.executable, str(ROOT / "scripts/parallel_fetch.py"), BASE + name, str(target),
                 "--sha256", sha, "--connections", "8" if name.endswith(".vcf.gz") else "1"], check=True)
            if (size is not None and target.stat().st_size != size) or digest(target) != sha:
                raise ValueError(f"SpliceAI asset verification failed: {name}")
        _write_receipt(stage, _signatures(stage, release))
        (stage / path.name).write_bytes(PIN.read_bytes())
        validate(stage / path.name)
        if path.parent.exists():
            backup = path.parent.with_name(path.parent.name + ".replaced-" + uuid.uuid4().hex[:12])
            path.parent.rename(backup)
            print(f"Previous SpliceAI directory preserved at {backup}", flush=True)
        stage.rename(path.parent)
        print("=== Full SpliceAI MANE 1.5 ready: 24 chromosomes; original scores unchanged ===", flush=True)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--verify-only", action="store_true",
                        help="Read-only SHA-256 verification of every file; does not download or refresh receipts")
    args = parser.parse_args()
    if args.verify_only:
        validate(args.manifest, strict=True)
        print("Full SpliceAI SHA-256 verification passed")
    else:
        install(args.manifest)
