"""Install and query the official GRCh38 GPN-Star M and V Parquet releases."""
from __future__ import annotations

import argparse
from collections import defaultdict
from contextlib import ExitStack
import fcntl
from functools import lru_cache
import hashlib
from itertools import islice, takewhile
import json
import os
import platform
from pathlib import Path
import subprocess
import sys
import sysconfig
import tempfile
import time

if __package__:
    from .vcf_io import open_text
    from .genia_alleles import snv_key
else:
    from vcf_io import open_text
    from genia_alleles import snv_key

ROOT = Path(__file__).resolve().parents[1]
RELEASE = json.loads((ROOT / "config/gpn-star-grch38.json").read_text())
REVISION = RELEASE["revision"]
MODELS = {"gpn_star_m": "m447", "gpn_star_v": "v100"}
URL = f"https://huggingface.co/datasets/songlab/gpn-star-scores/resolve/{REVISION}/data"
EXOME_RESOURCE = "gpn_star_v_exome"
EXOME_PACKAGE = "v100-exome"
PACKAGES = {**MODELS, EXOME_RESOURCE: EXOME_PACKAGE}
EXOME_PIN_PATH = ROOT / "config/gpn-star-v-exome.json"


@lru_cache(maxsize=1)
def exome_pin():
    return json.loads(EXOME_PIN_PATH.read_text())


BATCH = 8192


def runtime_dir():
    state = Path(os.environ.get("IEI_WORKBENCH_STATE_DIR") or Path.home() / ".iei-variant-review")
    return state / "tools" / f"gpn-parquet-1-{sys.implementation.cache_tag}-{sysconfig.get_platform()}-{platform.machine()}"


def bundled_reader():
    return (Path(sys.executable).resolve().parents[1] / "Resources/gpn-parquet-bundled").is_file()


def active_reader():
    try:
        import numpy
        import pyarrow.parquet
    except (ImportError, OSError, ValueError):
        return False
    return True


def runtime_ready():
    return bundled_reader() or (runtime_dir() / ".ready").is_file() or active_reader()


def install_runtime():
    if runtime_ready():
        return
    directory = runtime_dir()
    directory.parent.mkdir(parents=True, exist_ok=True)
    with (directory.parent / ".gpn-runtime.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if runtime_ready():
            return
        # Use a private wheel directory. The host application's Python environment
        # is also used by signed installations and must remain untouched.
        with tempfile.TemporaryDirectory(dir=directory.parent) as tmp:
            target = Path(tmp) / "packages"
            subprocess.run([sys.executable, "-m", "pip", "install", "--only-binary=:all:",
                            "--disable-pip-version-check", "--target", str(target),
                            "--require-hashes", "--no-deps", "-r",
                            str(ROOT / "config/gpn-star-reader-requirements.txt")], check=True)
            subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0,sys.argv[1]); "
                            "import numpy, pyarrow.parquet", str(target)], check=True)
            (target / ".ready").touch()
            # The lock also covers recovery of an incomplete previous installation.
            if directory.exists() or directory.is_symlink():
                directory.rename(Path(tmp) / "incomplete")
            target.rename(directory)


def assets(model):
    if model == EXOME_PACKAGE:
        return exome_pin()["shards"]
    return RELEASE["models"][model]


def release_dir(directory, model):
    if model == EXOME_PACKAGE:
        return directory / REVISION / model / exome_pin()["bed_sha256"]
    return directory / REVISION / model


def shard_name(chrom):
    return f"llr_chr{chrom}.parquet"


def installed(directory, model):
    folder = release_dir(directory, model)
    try:
        receipt = json.loads((folder / "installed.json").read_text())
        if receipt != {"revision": REVISION, "model": model}:
            return False
        verified_at = (folder / "installed.json").stat().st_mtime_ns
        for chrom, pin in assets(model).items():
            stat = (folder / shard_name(chrom)).stat()
            if stat.st_size != pin["size"] or stat.st_mtime_ns > verified_at:
                return False
        return True
    except (ValueError, OSError):
        return False


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(4 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def package_url(model, name):
    if model == EXOME_PACKAGE:
        pin = exome_pin()
        revision = pin["mirror_revision"]
        return f"https://huggingface.co/datasets/{pin['mirror_repo']}/resolve/{revision}/{name}"
    return f"{URL}/gpn-star-hg38-{model}-200m/llr/{name}"


def select_package(directory, model, scope):
    if model == "m447" and scope == "exome":
        raise ValueError("GPN-Star-M requires whole-genome mode")
    if installed(directory, model):
        return model
    if model == "v100" and scope == "exome" and installed(directory, EXOME_PACKAGE):
        return EXOME_PACKAGE
    raise ValueError(f"GPN-Star {model} requires a verified {scope} installation. Check Optional add-ons under Import & QC.")


def install(directory, model):
    print("Preparing the GPN-Star Parquet reader", flush=True)
    folder = release_dir(directory, model)
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / ".install.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        # A repair can discover corruption with the same size and timestamp.
        # Only a complete successful verification may certify the installation.
        (folder / "installed.json").unlink(missing_ok=True)
        install_runtime()
        total = sum(pin["size"] for pin in assets(model).values())
        done = 0
        for chrom, pin in assets(model).items():
            name = shard_name(chrom)
            target = folder / name
            print(f"{100 * done / total:.1f}%  Checking GPN-Star {model} chromosome {chrom}", flush=True)
            if not (target.is_file() and target.stat().st_size == pin["size"] and sha256(target) == pin["sha256"]):
                part = target.with_suffix(".parquet.part")
                if part.exists() and part.stat().st_size > pin["size"]:
                    part.unlink()
                if not part.exists() or part.stat().st_size < pin["size"]:
                    print(f"Downloading {name} ({pin['size'] / 1e9:.2f} GB)", flush=True)
                    url = f"{package_url(model, name)}?download=true&request={time.time_ns()}"
                    curl = ["curl", "--fail", "--location", "--silent", "--show-error", "--retry", "3",
                            "--connect-timeout", "30", "--speed-time", "120", "--speed-limit", "1024"]
                    output = ["--output", str(part), url]
                    resume = part.exists() and part.stat().st_size > 0
                    if resume:
                        # curl versions disagree on the exit code for HTTP 416.
                        # Read the final HTTP status separately from the file.
                        attempt = subprocess.run(
                            curl + ["--continue-at", "-", "--write-out", "%{http_code}"] + output,
                            stdout=subprocess.PIPE, text=True,
                        )
                        if attempt.stdout.strip() == "416" or attempt.returncode == 33:
                            print(f"Server declined resume. Restarting {name} from the beginning.", flush=True)
                            part.unlink(missing_ok=True)
                            resume = False
                        else:
                            attempt.check_returncode()
                    if not resume:
                        subprocess.run(curl + output, check=True)
                if part.stat().st_size != pin["size"] or sha256(part) != pin["sha256"]:
                    part.unlink()
                    raise ValueError(f"Checksum mismatch for {name}. Retry the download to fetch this chromosome again.")
                part.replace(target)
            done += pin["size"]
        marker = folder / "installed.tmp"
        marker.write_text(json.dumps({"revision": REVISION, "model": model}))
        marker.replace(folder / "installed.json")
        print(f"100%  GPN-Star {model} installed", flush=True)


def tags(model):
    return (f"GPN{model}_LLR", f"GPN{model}_ABSLLR")


SCORE_FIELDS = {tag for model in MODELS.values() for tag in tags(model)}
OWNED_HEADERS = tuple(f"##INFO=<ID={tag}," for tag in SCORE_FIELDS) + tuple(
    f"##GPNStar_{model}_{key}=" for model in MODELS.values() for key in ("release", "package", "coverage"))


def exome_coverage():
    pin = exome_pin()
    return (f"The compact GPN-Star-V package covers scored GRCh38 SNVs within the default Ensembl "
            f"{pin['ensembl']} coding and splice regions with {pin['padding_bp']}-bp padding. "
            "Targets in a custom BED or wider padding outside these intervals require genome-wide V scores.")


def chromosome(name):
    return name[3:] if name.startswith("chr") else name


def position_ranges(table):
    """Yield each nonempty row group's one-based position bounds."""
    import pyarrow.compute as pc

    pos_column = table.schema.names.index("pos")
    for i in range(table.num_row_groups):
        stats = table.metadata.row_group(i).column(pos_column).statistics
        if stats is not None and stats.has_min_max:
            yield i, stats.min, stats.max
        else:
            pos = table.read_row_group(i, columns=["pos"])["pos"]
            bounds = pc.min_max(pos).as_py()
            if bounds["min"] is not None:
                yield i, bounds["min"], bounds["max"]


def lookup(table, positions, targets):
    import numpy as np

    for i, first, last in position_ranges(table):
        subset = positions[positions.searchsorted(first):positions.searchsorted(last, side="right")]
        if not len(subset):
            continue
        group = table.read_row_group(i, columns=["pos", "ref", "alt", "llr_calibrated", "abs_llr_calibrated"])
        coords = group["pos"].combine_chunks().to_numpy()
        left, right = coords.searchsorted(subset), coords.searchsorted(subset, side="right")
        ids = [index for lo, hi in zip(left, right) for index in range(lo, hi)]
        if not ids:
            continue
        for pos, ref, alt, llr, magnitude in zip(*(col.to_pylist() for col in group.take(ids).columns)):
            if (ref, alt) in targets[pos]:
                scores = tuple(str(value) if np.isfinite(value) else "."
                               for value in (np.float32(llr), np.float32(magnitude)))
                if scores != (".", "."):
                    yield (pos, ref, alt), scores


def annotate(source, out, directories, packages=None):
    """Write one Number=A slot per ALT. Preserve other INFO fields and sample columns."""
    if directories:
        import numpy as np
        import pyarrow.parquet as pq

    supported = {chrom for model in directories for chrom in assets(model)}
    with ExitStack() as stack:
        tables = {}
        for line in source:
            if line.startswith("#CHROM\t"):
                for model in directories:
                    out.write(f"##GPNStar_{model}_release={REVISION}\n")
                    package = (packages or {}).get(model, model)
                    out.write(f"##GPNStar_{model}_package={package}\n")
                    if package == EXOME_PACKAGE:
                        out.write(f"##GPNStar_{model}_coverage={exome_coverage()}\n")
                    for tag, original in zip(tags(model), ("llr_calibrated", "abs_llr_calibrated")):
                        out.write(f'##INFO=<ID={tag},Number=A,Type=Float,Description="GPN-Star {model} {original}">\n')
                out.write(line)
                break
            if line.startswith(OWNED_HEADERS):
                continue
            if not line.startswith("##"):
                raise ValueError("Missing VCF column header")
            out.write(line)
        else:
            raise ValueError("Missing VCF column header")

        while True:
            # Also bound memory for very wide multi-sample VCFs.
            lines = []
            size = 0
            for line in islice(source, BATCH):
                lines.append(line.rstrip("\r\n").split("\t", 8))
                size += len(line)
                if size >= 16 * 1024**2:
                    break
            if not lines:
                break
            targets = defaultdict(lambda: defaultdict(set))
            row_keys = []
            for chrom, pos, _, ref, alts, *_ in lines:
                chrom = chromosome(chrom)
                keys = [snv_key(int(pos), ref, alt) for alt in alts.split(",")] if chrom in supported else []
                row_keys.append(keys)
                for key in keys:
                    if key is not None:
                        position, before, after = key
                        targets[chrom][position].add((before, after))
            hits = {}
            for chrom, wanted in targets.items():
                positions = np.array(sorted(wanted), dtype=np.int64)
                for model, folder in directories.items():
                    if chrom not in assets(model):
                        continue
                    key = (chrom, model)
                    if key not in tables:
                        tables[key] = stack.enter_context(pq.ParquetFile(folder / shard_name(chrom)))
                    hits[key] = dict(lookup(tables[key], positions, wanted))
            for row, keys in zip(lines, row_keys):
                chrom = chromosome(row[0])
                parts = [s for s in row[7].split(";") if s not in (".", "") and s.partition("=")[0] not in SCORE_FIELDS]
                for model in directories:
                    matches = hits.get((chrom, model))
                    if not matches:
                        continue
                    pairs = [matches.get(key) for key in keys]
                    if any(pairs):
                        for i, tag in enumerate(tags(model)):
                            parts.append(tag + "=" + ",".join(pair[i] if pair else "." for pair in pairs))
                row[7] = ";".join(parts) or "."
                out.write("\t".join(row) + "\n")


def main():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    get = commands.add_parser("install")
    get.add_argument("config", type=Path)
    get.add_argument("resource", choices=PACKAGES)
    run = commands.add_parser("annotate")
    run.add_argument("config", type=Path)
    run.add_argument("input", type=Path)
    run.add_argument("--scope", choices=("exome", "whole_genome"))
    probe = commands.add_parser("has-fields")
    probe.add_argument("input", type=Path)
    args = parser.parse_args()
    if args.command == "has-fields":
        with open_text(args.input) as inp:
            headers = takewhile(lambda line: line.startswith("##"), inp)
            print("yes" if any(line.startswith(OWNED_HEADERS) for line in headers) else "no")
        return
    import yaml
    cfg = yaml.safe_load(args.config.read_text()) or {}
    blocks = cfg.get("wgs_review", {})
    if args.command == "install":
        key = "gpn_star_v" if args.resource == EXOME_RESOURCE else args.resource
        directory = (blocks.get(key) or {}).get("directory")
        if not directory:
            raise ValueError(f"Configure wgs_review.{key}.directory")
        install(ROOT / directory, PACKAGES[args.resource])
        return
    scope = args.scope or ("exome" if cfg.get("region", {}).get("coding_only", True) else "whole_genome")
    folders = {}
    packages = {}
    for key, model in MODELS.items():
        block = blocks.get(key) or {}
        if not block.get("enabled"):
            continue
        if not block.get("directory"):
            raise ValueError(f"Configure wgs_review.{key}.directory")
        directory = ROOT / block["directory"]
        packages[model] = select_package(directory, model, scope)
        folders[model] = release_dir(directory, packages[model])
        if packages[model] == EXOME_PACKAGE:
            print(exome_coverage(), file=sys.stderr)
    if folders and not bundled_reader() and (runtime_dir() / ".ready").is_file():
        sys.path.insert(0, str(runtime_dir()))
    if folders:
        try:
            import pyarrow.parquet
            import numpy
        except ImportError as exc:
            raise ValueError("GPN-Star Parquet reader unavailable. Install or verify the dataset in Import & QC.") from exc
    with open_text(args.input) as inp:
        annotate(inp, sys.stdout, folders, packages)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        sys.exit(f"GPN-Star: {exc}")
