#!/usr/bin/env python3
"""Probe the actual local container view before expensive annotation work.

Read probes compare a short file fingerprint without printing file contents.
Write probes use uniquely named, disposable markers and verify both directions
of the host/container share. Never modify VM sharing settings automatically.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

try:
    from .build_vep_command import build_vep_command, load_config
except ImportError:
    from build_vep_command import build_vep_command, load_config


class AccessError(ValueError):
    pass


@dataclass(frozen=True)
class AccessPath:
    path: Path
    label: str
    write: bool = False


def _absolute(value, root):
    path = Path(value).expanduser()
    return (path if path.is_absolute() else root / path).resolve()


def collect_paths(config, input_path, output_path, root, *, assembly="GRCh38", all_variants=False):
    """Use the VEP resolver rather than duplicating predictor asset contracts."""
    root = Path(root).resolve()
    result = [AccessPath(Path(input_path).resolve(), "input VCF"),
              AccessPath(Path(output_path).resolve().parent, "output and annotation temporary files", True)]
    plan = build_vep_command(config, str(input_path), str(output_path), base_dir=str(root))
    if plan.errors:
        raise AccessError("\n".join(plan.errors))
    for value in plan.reference_paths:
        path = Path(value)
        # Optional absent references are skipped by the same VEP resolver.
        result.append(AccessPath(path, "annotation reference"))
        for suffix in (".tbi", ".csi", ".fai", ".gzi"):
            index = Path(str(path) + suffix)
            if index.exists():
                result.append(AccessPath(index, "reference index"))

    def add(value, label, *, optional=False, write=False):
        if not value:
            return
        path = _absolute(value, root)
        if not optional or path.exists():
            result.append(AccessPath(path, label, write))

    region = config.get("region") or {}
    if not all_variants and region.get("coding_only", True):
        bed = region.get("custom_bed") or region.get("bed") or "references/regions/coding_splice.padded.bed.gz"
        if _absolute(bed, root).exists() or region.get("custom_bed"):
            add(bed, "region BED")
        else:
            # The runner can build its default BED; its destination must work.
            add(str(_absolute(bed, root).parent), "generated region BED directory", write=True)
    post = config.get("post_processing") or {}
    ptc = post.get("loftee_ptc_50bp") or {}
    if ptc.get("enabled"):
        add(ptc.get("gtf"), "PTC transcript reference", optional=not ptc.get("required"))
    if assembly == "GRCh37":
        conversion = (config.get("liftover") or {}).get("grch37_to_grch38") or {}
        for key in ("source_fasta", "chain"):
            add(conversion.get(key), "GRCh37 liftover " + key)
        source = conversion.get("source_fasta")
        if source:
            for suffix in (".fai", ".gzi"):
                add(str(source) + suffix, "GRCh37 FASTA index")
        target = (config.get("reference") or {}).get("fasta") or {}
        if target.get("path"):
            fasta = _absolute(target["path"], root)
            dictionary = Path(str(fasta).removesuffix(".gz").removesuffix(".fa") + ".dict")
            if not dictionary.exists() or dictionary.stat().st_mtime < fasta.stat().st_mtime:
                add(str(fasta.parent), "generated liftover dictionary directory", write=True)
            else:
                add(str(dictionary), "liftover sequence dictionary")
    # Collapse duplicates, but never lose a write requirement for the same dir.
    return list(dict.fromkeys(result))


def sharing_guidance(runtime, path, write=False):
    folder = path if path.is_dir() else path.parent
    return (
        f"Check that the drive is connected and this folder is permitted: {folder}. "
        f"On macOS with Colima, add this specific folder as a {'writable' if write else 'readable'} mount in "
        "the active Colima profile (colima start --edit), then restart that VM "
        "when no jobs are running. With Docker Desktop, add it under Settings > "
        "Resources > File sharing. With Podman machine, check the VM's shared "
        "volumes. On Linux or a remote Docker context, the path must exist on "
        f"the {runtime} host with suitable permissions. Alternatively choose an "
        "already-shared folder. GUIDE-IEI has not changed your sharing settings."
    )


def _representative(path):
    """One existing file proves a read-only directory isn't an empty VM shadow."""
    if path.is_file():
        return path
    if not path.is_dir():
        raise AccessError(f"Host path is missing or not a regular file/directory: {path}")
    def cannot_walk(exc):
        raise exc

    for directory, _, names in os.walk(path, onerror=cannot_walk):
        for name in sorted(names):
            candidate = Path(directory) / name
            if candidate.is_file():
                return candidate
    raise AccessError(f"Reference directory is empty: {path}. Finish dataset setup before annotating.")


# Only fingerprints/markers cross stdout, never the input or reference bytes.
PROBE_CODE = r'''
use strict;
use warnings;
use Digest::SHA qw(sha256_hex);
use Fcntl qw(O_WRONLY O_CREAT O_EXCL);
$| = 1;
while (@ARGV) {
    my ($key, $mode, $filename, $expected) = splice(@ARGV, 0, 4);
    my $ok = eval {
        open(my $input, '<:raw', $filename) or die 'read failed';
        my $bytes = '';
        defined(read($input, $bytes, 4096)) or die 'read failed';
        close($input) or die 'close failed';
        sha256_hex($bytes) eq $expected or die 'file differs from host';
        if ($mode eq 'write') {
            sysopen(my $output, "$filename.container", O_WRONLY | O_CREAT | O_EXCL, 0644) or die 'write failed';
            print {$output} "container-write-ok\n" or die 'write failed';
            close($output) or die 'close failed';
        }
        1;
    };
    if (!$ok) { print "$key:FAIL\n"; exit 2; }
    print "$key:OK\n";
}
'''


def check_access(paths, runtime, image, *, run=subprocess.run):
    if runtime not in {"docker", "podman", "singularity", "apptainer"}:
        raise AccessError(f"Unsupported container runtime: {runtime}")
    mounts, checks, markers = [], [], []
    try:
        for item in paths:
            original = Path(item.path).resolve()
            try:
                if item.write:
                    original.mkdir(parents=True, exist_ok=True)
                    fd, filename = tempfile.mkstemp(prefix=".guide-iei-access-", dir=original)
                    with os.fdopen(fd, "wb") as handle:
                        handle.write(os.urandom(32))
                    path = Path(filename)
                    markers.append(path)
                    markers.append(Path(str(path) + ".container"))
                    # Marker is random, not user data. Let an image running as
                    # a different UID read it, then test creation of its own file.
                    path.chmod(0o644)
                    directory = original
                else:
                    path = _representative(original)
                    directory = original if original.is_dir() else path.parent
                with path.open("rb") as handle:
                    digest = hashlib.sha256(handle.read(4096)).hexdigest()
            except (OSError, ValueError) as exc:
                raise AccessError(
                    f"Host cannot {'write' if item.write else 'read'} {item.label}: {original}. "
                    "Check drive connection, file permissions, and dataset installation."
                ) from exc
            # Docker's -v separator cannot represent a colon in a host path.
            if any(c in str(directory) for c in (":", "\n", "\r")):
                raise AccessError(f"Container sharing does not support ':' or newlines in this folder name: {directory}")
            mount = f"/guide_access/{len(checks)}"
            target = mount + "/" + path.relative_to(directory).as_posix()
            mounts.append((str(directory), mount, "rw" if item.write else "ro"))
            checks.append((item, path, target, digest))
        command = [runtime]
        if runtime in {"docker", "podman"}:
            command += ["run", "--rm", "--pull=never", "--network=none", "--ulimit", "core=0:0"]
            for host, target, mode in mounts:
                command += ["-v", f"{host}:{target}:{mode}"]
            # Perl is part of VEP's runtime; Python is not present in all
            # supported VEP images. This adds no new container dependency.
            command += ["--entrypoint", "perl", image, "-e", PROBE_CODE]
        else:
            command += ["exec"]
            for host, target, mode in mounts:
                command += ["--bind", f"{host}:{target}:{mode}"]
            command += [image, "perl", "-e", PROBE_CODE]
        for index, (item, _, target, digest) in enumerate(checks):
            command += [str(index), "write" if item.write else "read", target, digest]
        try:
            result = run(command, text=True, capture_output=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise AccessError(
                f"Could not run the {runtime} file-access check. Start the configured container engine "
                "and ensure the GUIDE-IEI image is installed; then retry."
            ) from exc
        confirmed = set(result.stdout.splitlines())
        if result.returncode and not confirmed and any(
            phrase in result.stderr.lower()
            for phrase in ("cannot connect", "is the docker daemon running", "no such image", "unable to find image",
                           "executable file not found", "can't locate digest/sha")
        ):
            raise AccessError(
                "Container engine or GUIDE-IEI image could not run the access probe. "
                "Start the configured engine and repair/rebuild the GUIDE-IEI image; "
                "this is not evidence of a file-sharing problem.\n" + result.stderr.strip()[-1500:]
            )
        if result.returncode and not confirmed:
            # A runtime may refuse a bind mount before the probe starts.
            # Do not blame the first input when a later reference mount failed.
            folders = list(dict.fromkeys(host for host, _, _ in mounts))
            raise AccessError(
                "Container could not start the file-access probe for these folders: "
                + ", ".join(folders)
                + ". Check the container diagnostic to identify the rejected mount. "
                + sharing_guidance(runtime, Path(folders[0]))
                + "\nContainer diagnostic: " + result.stderr.strip()[-1500:]
            )
        for index, (item, path, _, _) in enumerate(checks):
            if f"{index}:OK" not in confirmed:
                detail = result.stderr.strip()[-1500:]
                raise AccessError(
                    f"Container cannot {'read/write' if item.write else 'read'} {item.label}: {item.path}. "
                    f"The host access check passed. {sharing_guidance(runtime, item.path, item.write)}"
                    + (f"\nContainer diagnostic: {detail}" if detail else "")
                )
            if item.write:
                written = Path(str(path) + ".container")
                if not written.is_file() or written.read_bytes() != b"container-write-ok\n":
                    raise AccessError(f"Container writes are not visible on the host for {item.label}: {item.path}. "
                                      + sharing_guidance(runtime, item.path, True))
        if result.returncode:
            raise AccessError(f"Container access probe failed (exit {result.returncode}): {result.stderr.strip()[-1500:]}")
    finally:
        for path in markers:
            path.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--root", required=True)
    parser.add_argument("--assembly", choices=("GRCh37", "GRCh38"), default="GRCh38")
    parser.add_argument("--all-variants", action="store_true")
    args = parser.parse_args()
    try:
        cfg = load_config(args.config)
        paths = collect_paths(cfg, args.input, args.output, args.root,
                              assembly=args.assembly, all_variants=args.all_variants)
        container = cfg.get("container") or {}
        check_access(paths, container.get("runtime") or "docker", container.get("image") or "vep-annotate:latest")
    except (ValueError, OSError) as exc:
        parser.exit(2, f"ERROR {exc}\n")
    print(f"Container access verified for {len(paths)} input/reference/work paths")


if __name__ == "__main__":
    main()
