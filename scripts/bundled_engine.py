#!/usr/bin/env python3
"""Package/load an immutable VEP image; never build or pull at runtime.

The manifest and archive are sealed inside the signed app. Source checkouts
continue to use docker/build.sh. Container data mounts are never exported.
"""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

LABEL = "org.guide-iei.source-fingerprint"
ARCHES = {"arm64": "arm64", "aarch64": "arm64", "x86_64": "amd64", "amd64": "amd64"}


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def fingerprint(root):
    return subprocess.check_output(["bash", str(root / "docker/image_fingerprint.sh")], text=True).strip()


def inspect(runtime, image):
    return json.loads(subprocess.check_output([runtime, "image", "inspect", image], text=True, timeout=30))[0]


def verify_image(info, expected, architecture):
    if info.get("Os") != "linux" or info.get("Architecture") != architecture:
        raise ValueError("Bundled engine architecture does not match the target container runtime")
    if (info.get("Config", {}).get("Labels") or {}).get(LABEL) != expected:
        raise ValueError("Engine does not match this GUIDE-IEI source; rebuild the release engine first")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", info.get("Id", "")):
        raise ValueError("Invalid engine image identity")


def validate_bundle(directory, expected, architecture):
    manifest = json.loads((directory / "manifest.json").read_text())
    if (manifest.get("schema_version") != 1 or manifest.get("source_fingerprint") != expected
            or manifest.get("platform") != "linux/" + architecture
            or manifest.get("archive") != "vep-engine.tar.gz"
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", manifest.get("image_id", ""))):
        raise ValueError("Bundled engine manifest is incompatible; reinstall the correct GUIDE-IEI app")
    archive = directory / manifest["archive"]
    if archive.is_symlink() or archive.stat().st_size != manifest.get("archive_bytes") or digest(archive) != manifest.get("sha256"):
        raise ValueError("Bundled engine archive failed integrity verification; reinstall GUIDE-IEI")
    return manifest


def smoke(runtime, image):
    # No data mounts or network; test the actual packaged executables/plugins.
    command = """set -eu
vep --help >/dev/null
samtools --version >/dev/null
bcftools --version >/dev/null
bgzip --version >/dev/null
tabix --version >/dev/null
bcftools plugin -l | grep -qx liftover
perl -MDBD::SQLite -e 'exit 0'
for plugin in PromoterAI LoGoFunc IndexedScores LoF; do
    perl -I/plugins -c "/plugins/$plugin.pm"
done
"""
    subprocess.run([runtime, "run", "--rm", "--pull=never", "--network=none", "--entrypoint", "sh", image, "-c", command], check=True, timeout=120)


def export_bundle(root, directory, runtime, image, architecture):
    expected = fingerprint(root)
    info = inspect(runtime, image)
    verify_image(info, expected, architecture)
    image_id = info["Id"]
    smoke(runtime, image_id)
    if directory.exists():
        manifest = validate_bundle(directory, expected, architecture)
        if manifest["image_id"] != image_id:
            raise ValueError("Bundle cache belongs to a different image; choose a new cache directory")
        return manifest
    directory.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="engine-export-", dir=directory.parent) as temporary:
        stage = Path(temporary) / "bundle"
        stage.mkdir()
        archive = stage / "vep-engine.tar.gz"
        print("Exporting the prebuilt VEP engine (no patient volumes or databases)…", flush=True)
        process = subprocess.Popen([runtime, "image", "save", image_id], stdout=subprocess.PIPE)
        try:
            with archive.open("wb") as output, gzip.GzipFile(filename="", mode="wb", fileobj=output, mtime=0, compresslevel=6) as compressed:
                shutil.copyfileobj(process.stdout, compressed, 1024 * 1024)
            if process.wait() != 0:
                raise RuntimeError("Docker image export failed")
        finally:
            process.stdout.close()
            if process.poll() is None:
                process.terminate()
                process.wait()
        manifest = {"schema_version": 1, "platform": "linux/" + architecture,
                    "image_id": image_id, "source_fingerprint": expected,
                    "archive": archive.name, "archive_bytes": archive.stat().st_size,
                    "sha256": digest(archive)}
        (stage / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        # Preserve notices available in the image in a convenient companion.
        # Distribution/source obligations still require a release license review.
        with (stage / "IMAGE-NOTICES.txt").open("wb") as output:
            output.write(b"Notices collected from the bundled Linux image. See docs/MACOS_APP_RELEASE.md for release obligations.\n")
            subprocess.run([runtime, "run", "--rm", "--pull=never", "--network=none", "--entrypoint", "sh", image_id, "-c",
                "find /usr/share/doc /opt/vep /plugins -type f \\( -iname copyright -o -iname 'LICENSE*' -o -iname 'COPYING*' -o -iname 'NOTICE*' -o -name UPSTREAM-MODIFICATIONS.txt \\) -exec sh -c 'for f; do printf \"\\n--- %s ---\\n\" \"$f\"; cat \"$f\"; done' sh {} +"],
                stdout=output, check=True, timeout=120)
        stage.rename(directory)
    return manifest


def load_bundle(root, directory, runtime, image):
    if "@" in image:
        raise ValueError("Cannot replace a digest-pinned image; configure a writable image tag")
    engine_arch = subprocess.check_output([runtime, "info", "--format", "{{.Architecture}}"], text=True, timeout=30).strip()
    architecture = ARCHES.get(engine_arch)
    if not architecture:
        raise ValueError("Unsupported container architecture: " + engine_arch)
    print("=== Verifying bundled annotation engine ===", flush=True)
    manifest = validate_bundle(directory, fingerprint(root), architecture)
    print("=== Installing bundled annotation engine ===", flush=True)
    subprocess.run([runtime, "image", "load", "--input", str(directory / manifest["archive"])], check=True)
    info = inspect(runtime, manifest["image_id"])
    verify_image(info, manifest["source_fingerprint"], architecture)
    smoke(runtime, manifest["image_id"])
    # Tag only after verifying the loaded immutable image, leaving the old tag
    # intact if decompression, loading or the smoke test fails.
    subprocess.run([runtime, "image", "tag", manifest["image_id"], image], check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["export", "load"])
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--runtime", default="docker")
    parser.add_argument("--image", default="vep-annotate:latest")
    parser.add_argument("--architecture", choices=["arm64", "amd64"])
    args = parser.parse_args()
    try:
        if args.action == "export":
            if not args.architecture:
                parser.error("export requires --architecture")
            export_bundle(args.root, args.directory, args.runtime, args.image, args.architecture)
        else:
            load_bundle(args.root, args.directory, args.runtime, args.image)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        parser.exit(1, f"Bundled engine: {exc}\n")


if __name__ == "__main__":
    main()
