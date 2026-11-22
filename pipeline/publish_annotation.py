"""Publish a staged annotation and its legacy name, with the pointer last."""
import fcntl
import os
from pathlib import Path
import shutil
import sys
from tempfile import TemporaryDirectory


def link_or_copy(source: Path, destination: Path) -> None:
    try:
        os.link(source, destination, follow_symlinks=False)
    except OSError:
        shutil.copy2(source, destination, follow_symlinks=False)


def publish(final: Path, requested: Path) -> None:
    # The runner creates staging under the output directory on the same volume.
    staged = final.parent
    legacy = requested.with_name(final.name)
    outputs = list(dict.fromkeys((legacy, requested)))
    compressed = final.name.endswith(".vcf.gz")
    index = Path(str(final) + ".tbi")
    if not final.stat().st_size or (compressed and not index.stat().st_size):
        raise ValueError("Cannot publish an empty annotation or index")

    with TemporaryDirectory(dir=staged) as temporary:
        work = Path(temporary)
        sidecar = Path(str(requested) + ".deliverable")
        pointer = work / "pointer"
        pointer.write_text(final.name + "\n", encoding="utf-8")
        # Prepare the names before touching any previous run. The two VCFs
        # and their indexes share storage on filesystems with hard links.
        replacements = {}
        indexes = [Path(str(target) + suffix) for target in outputs for suffix in (".tbi", ".csi")]
        for target in outputs:
            for suffix in (("", ".tbi") if compressed else ("",)):
                source = Path(str(final) + suffix)
                destination = Path(str(target) + suffix)
                if target != legacy:
                    alias = work / destination.name
                    link_or_copy(source, alias)
                    source = alias
                replacements[destination] = source
        # Reports keep their established names, including the matched VCF's
        # QC and manifest. Drop obsolete reports when a rerun omits a stage.
        audits = ("_warnings.txt", "_summary.html", ".loftee_ptc50.audit.json", ".haplotype.audit.json")
        reports = (".annotation_qc.json", ".annotation_qc.html", ".run_manifest.json")
        artifacts = [Path(str(requested) + suffix) for suffix in audits]
        artifacts.extend(Path(str(base) + suffix) for base in outputs for suffix in reports)
        for target in artifacts:
            source = staged / target.name
            if source.is_file():
                replacements[target] = source
        replacements[sidecar] = pointer

        # Serialize only the short publication step for runs sharing a folder.
        directory = os.open(requested.parent, os.O_RDONLY)
        try:
            fcntl.flock(directory, fcntl.LOCK_EX)
            paths = [sidecar, *indexes, *artifacts, *outputs]
            backups = {}
            for i, path in enumerate(paths):
                if path.is_symlink() or path.is_file():
                    backups[path] = work / f"previous-{i}"
                    link_or_copy(path, backups[path])
                elif path.exists():
                    raise ValueError(f"Expected a file at {path}")

            changed = set()
            try:
                # Remove old indexes before changing data. A reader can see
                # an absent index during publication, never a stale CSI.
                for path in [sidecar, *indexes, *artifacts]:
                    if path in backups:
                        path.unlink()
                        changed.add(path)
                for path, source in replacements.items():
                    os.replace(source, path)
                    changed.add(path)
            except BaseException:
                for path in changed:
                    # Restored below in data/index/pointer order.
                    path.unlink(missing_ok=True)
                for path in reversed(paths):
                    if path in backups and path in changed:
                        os.replace(backups[path], path)
                raise
        finally:
            os.close(directory)


if __name__ == "__main__":
    publish(Path(sys.argv[1]), Path(sys.argv[2]))
