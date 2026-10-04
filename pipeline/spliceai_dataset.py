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

ROOT = Path(__file__).resolve().parents[1]
PIN = ROOT / "config/spliceai-mane-v1.5.json"
FORMAT = "mane_v1.5_sharded"
BASE = "https://huggingface.co/datasets/luoyiming1991/spliceai-mane-v1.5-d500-m1-snv/resolve/90445b422a04dafd620c206093020ddbdc7817ba/"
NOTICES = {
    "LICENSE.md": "b1c450715e42435fd3e4f2dec9d0f65ff2340a3ca81436ab1bccfb1403bc7f3d",
    "NOTICE.md": "51a6867c8eae3b0043fd4a594e22310d342a452e73ca1520fb5926731000c063",
}


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
    """Raise actionable errors; cheap unchanged-file validation for UI polls."""
    path = Path(path)
    try:
        release = json.loads(path.read_text())
        if release != json.loads(PIN.read_text()):
            raise ValueError("release manifest differs from the pinned MANE 1.5 dataset")
        receipt = json.loads((path.parent / "installation.json").read_text())
        for name, size, sha in assets(release):
            file = path.parent / name
            stat = file.stat()
            if file.is_symlink() or not file.is_file() or (size is not None and stat.st_size != size):
                raise ValueError(f"missing or incomplete chromosome asset: {name}")
            signature = [stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns]
            if strict or receipt.get("files", {}).get(name) != signature:
                if digest(file) != sha:
                    raise ValueError(f"checksum mismatch: {name}")
        return release
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise ValueError(f"Full SpliceAI installation needs repair: {exc}") from exc


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
        if valid(path):
            validate(path, strict=True)
            print("=== Full SpliceAI MANE 1.5 already verified ===", flush=True)
            return
        release = json.loads(PIN.read_text())
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
        receipt = {"source": BASE, "files": {name: [(stage / name).stat().st_size,
                    (stage / name).stat().st_mtime_ns, (stage / name).stat().st_ctime_ns]
                    for name, _, _ in assets(release)}}
        (stage / "installation.json").write_text(json.dumps(receipt) + "\n")
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
    args = parser.parse_args()
    install(args.manifest)
