#!/usr/bin/env python3
"""Build a self-contained Mac app. No publication or user installation.

Build on each target architecture with Node/npm and Xcode Command Line Tools.
--preview allows uncommitted source and an ad-hoc signature for local testing.
Release builds require a clean tree, Developer ID, and a notarytool profile.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import posixpath
import plistlib
import re
import shutil
import subprocess
import tarfile
import tempfile
import zipfile
import uuid

ROOT = Path(__file__).resolve().parents[1]
WHEELS = {
    "arm64": ("https://files.pythonhosted.org/packages/45/9f/3b1c20a0b7a3200524eb0076cc027a970d320bd3a6592873c85c92a08731/PyYAML-6.0.2-cp313-cp313-macosx_11_0_arm64.whl", "50187695423ffe49e2deacb8cd10510bc361faac997de9efef88badc3bb9e2d1"),
    "x86_64": ("https://files.pythonhosted.org/packages/ef/e3/3af305b830494fa85d95f6d95ef7fa73f2ee1cc8ef5b495c7c3269fb835f/PyYAML-6.0.2-cp313-cp313-macosx_10_13_x86_64.whl", "efdca5630322a10774e8e98e1af481aad470dd62c3170801852d752aa7a783ba"),
}
CODE_DIRS = {"local_service", "pipeline", "scripts", "config", "docker", "docs"}
ROOT_FILES = {"VERSION", "LICENSE", "README.md", "requirements.txt"}
# Explicit new runtime files also support local previews before they are committed.
EXTRA_FILES = {"local_service/static_site.py", "local_service/desktop_app.py", "local_service/container_startup.py", "docs/MACOS_APP_RELEASE.md"}


def run(*args, **kwargs):
    print("+", *map(str, args), flush=True)
    return subprocess.run([str(arg) for arg in args], check=True, **kwargs)


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(url, digest, cache):
    target = cache / url.rsplit("/", 1)[-1].replace("%2B", "+")
    if target.is_file() and sha256(target) == digest:
        return target
    part = target.with_suffix(target.suffix + ".part")
    run("curl", "--fail", "--location", "--retry", "3", "--output", part, url)
    if sha256(part) != digest:
        raise ValueError(f"Checksum mismatch: {target.name}; refusing to package")
    part.replace(target)
    return target


def copy_source(destination):
    tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode().split("\0")
    for name in sorted(set(tracked) | EXTRA_FILES):
        if not name:
            continue
        path = Path(name)
        if path.parts[0] not in CODE_DIRS and name not in ROOT_FILES:
            continue
        source = ROOT / path
        if not source.is_file() or source.is_symlink():
            continue
        if path.name.startswith("test_") or path.suffix in {".pyc", ".sqlite3"}:
            continue
        target = destination / path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def is_macho(path):
    if path.is_symlink() or not path.is_file():
        return False
    with path.open("rb") as stream:
        return stream.read(4) in (b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca")


def extract_runtime(archive, destination):
    with tarfile.open(archive) as bundle:
        for member in bundle.getmembers():
            name = posixpath.normpath(member.name)
            if name.startswith(("/", "../")) or name == ".." or member.isdev() or member.isfifo():
                raise ValueError("Unsafe runtime archive member")
            if member.issym() or member.islnk():
                link = posixpath.normpath(posixpath.join(posixpath.dirname(name) if member.issym() else "", member.linkname))
                if link.startswith(("/", "../")) or link == "..":
                    raise ValueError("Unsafe runtime archive link")
        # Compatible with the macOS system Python 3.9/3.10. The archive has
        # already passed its pinned SHA-256 and path/link validation above.
        bundle.extractall(destination)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preview", action="store_true")
    parser.add_argument("--output", type=Path, default=ROOT / "dist/macos-app")
    parser.add_argument("--cache", type=Path, default=ROOT / "dist/macos-build-cache")
    parser.add_argument("--identity", default=os.environ.get("IEI_MAC_SIGN_IDENTITY"))
    parser.add_argument("--notary-profile", default=os.environ.get("IEI_MAC_NOTARY_PROFILE"))
    args = parser.parse_args()
    arch = platform.machine()
    if platform.system() != "Darwin" or arch not in WHEELS:
        parser.error("build natively on an arm64 or x86_64 Mac")
    dirty = subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT).strip()
    if not args.preview and (dirty or not args.identity or not args.notary_profile):
        parser.error("release needs a clean tree, --identity and --notary-profile; use --preview for local testing")
    if args.identity and not args.identity.startswith("Developer ID Application:"):
        parser.error("use a Developer ID Application identity")
    version = (ROOT / "VERSION").read_text().strip()
    stem = f"GUIDE-IEI-macOS-{version}-{arch}" + ("-preview" if args.preview else "")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    for suffix in (".app", ".zip", ".dmg"):
        if (output / (stem + suffix)).exists():
            parser.error(f"{stem + suffix} already exists; choose a new output directory")
    cache = args.cache.resolve()
    cache.mkdir(parents=True, exist_ok=True)
    # Reuse the bootstrapper's audited Python pin rather than duplicate it.
    pins = dict(re.findall(r'^([A-Z0-9_]+)="([^"\n]+)"$', (ROOT / "scripts/setup_environment.sh").read_text(), re.M))
    triple = "aarch64-apple-darwin" if arch == "arm64" else "x86_64-apple-darwin"
    name = f"cpython-{pins['PYTHON_VERSION']}+{pins['PYTHON_BUILD_TAG']}-{triple}-install_only_stripped.tar.gz"
    py_hash = pins["PYTHON_SHA_DARWIN_ARM64" if arch == "arm64" else "PYTHON_SHA_DARWIN_X64"]
    archive = download(f"https://github.com/astral-sh/python-build-standalone/releases/download/{pins['PYTHON_BUILD_TAG']}/{name.replace('+', '%2B')}", py_hash, cache)
    wheel_url, wheel_hash = WHEELS[arch]
    wheel = download(wheel_url, wheel_hash, cache)
    with tempfile.TemporaryDirectory(prefix="guide-iei-app-build-") as temporary:
        stage = Path(temporary)
        app = stage / "GUIDE-IEI.app"
        contents = app / "Contents"
        resources = contents / "Resources"
        application = resources / "application"
        application.mkdir(parents=True)
        (contents / "MacOS").mkdir()
        frameworks = contents / "Frameworks"
        frameworks.mkdir()
        extract_runtime(archive, stage / "runtime")
        framework = frameworks / "Python.framework"
        runtime = framework / "Versions/3.13"
        runtime.parent.mkdir(parents=True)
        shutil.move(stage / "runtime/python", runtime)
        (framework / "Versions/Current").symlink_to("3.13")
        # A real framework envelope keeps Apple's nested-code validation from
        # treating arbitrary CPython support directories as malformed bundles.
        (runtime / "Resources").mkdir()
        (runtime / "Resources/Info.plist").write_bytes(plistlib.dumps({
            "CFBundleIdentifier": "org.guide-iei.python", "CFBundleName": "Python",
            "CFBundleExecutable": "Python", "CFBundlePackageType": "FMWK",
            "CFBundleVersion": pins["PYTHON_VERSION"],
        }))
        (runtime / "lib/libpython3.13.dylib").rename(runtime / "Python")
        (runtime / "lib/libpython3.13.dylib").symlink_to("../Python")
        (framework / "Python").symlink_to("Versions/Current/Python")
        (framework / "Resources").symlink_to("Versions/Current/Resources")
        python = runtime / "bin/python3"
        run(python, "-s", "-B", "-m", "pip", "install", "--no-index", "--no-deps", "--no-compile", wheel)
        run(python, "-s", "-B", "-c", "import yaml,ssl,sqlite3; print('Bundled Python dependency check passed')")
        copy_source(application)

        # Build from a temporary source snapshot. Never overwrite the running
        # developer workbench's .next bundle or package local uploads/state.
        web = stage / "webui"
        web.mkdir()
        shutil.copytree(application / "config", stage / "config")
        for filename in ("package.json", "package-lock.json", "tsconfig.json", "next.config.ts", "next-env.d.ts", "postcss.config.mjs"):
            source = ROOT / "webui" / filename
            if source.is_file():
                shutil.copy2(source, web / filename)
        shutil.copytree(ROOT / "webui/app", web / "app")
        public = web / "public"
        public.mkdir()
        tracked = subprocess.check_output(["git", "ls-files", "-z", "webui/public"], cwd=ROOT).decode().split("\0")
        for name in filter(None, tracked):
            target = web / Path(name).relative_to("webui")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / name, target)
        run("npm", "ci", "--no-fund", "--no-audit", cwd=web)
        env = dict(os.environ, IEI_DESKTOP_EXPORT="1", NEXT_PUBLIC_IEI_SERVICE_URL="", NEXT_TELEMETRY_DISABLED="1")
        run("npm", "run", "build", cwd=web, env=env)
        shutil.copytree(web / "out", application / "webui/site")
        # The backend opens the public gene-knowledge SQLite file here.
        (application / "webui/public").symlink_to("site", target_is_directory=True)
        notices = []
        for license_file in sorted((web / "node_modules").rglob("LICENSE*")):
            if license_file.is_file():
                notices.append(f"\n--- {license_file.relative_to(web / 'node_modules')} ---\n" + license_file.read_text(errors="replace"))
        (resources / "THIRD-PARTY-NOTICES.txt").write_text("Build dependency notices; Python and PyYAML licenses are also retained in Frameworks/Python.\n" + "\n".join(notices))
        metadata = {
            "build_id": uuid.uuid4().hex,
            "version": version, "architecture": arch, "preview": args.preview,
            "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT).decode().strip(),
            "uncommitted_source": bool(dirty), "python_sha256": py_hash,
            "pyyaml_version": "6.0.2", "pyyaml_sha256": wheel_hash,
        }
        (application / "desktop-build.json").write_text(json.dumps(metadata, indent=2) + "\n")
        template = ROOT / "desktop/macos/GUIDE-IEI.app/Contents"
        info = plistlib.loads((template / "Info.plist").read_bytes())
        # Never share Launch Services identity with the source-tree shim.
        info.update(CFBundleIdentifier="org.guide-iei.desktop", CFBundleName="GUIDE-IEI",
                    CFBundleDisplayName="GUIDE-IEI", CFBundleVersion=version,
                    CFBundleShortVersionString=version, LSArchitecturePriority=[arch])
        (contents / "Info.plist").write_bytes(plistlib.dumps(info))
        for icon in (template / "Resources").glob("*.icns"):
            shutil.copy2(icon, resources / icon.name)
        run("xcrun", "clang", "-fobjc-arc", "-Wall", "-Wextra", "-Werror", "-Os", "-arch", arch,
            "-mmacosx-version-min=13.0", "-framework", "Cocoa", ROOT / "desktop/macos/GUIDE-IEI-App.m",
            "-o", contents / "MacOS/GUIDE-IEI")
        identity = args.identity or "-"
        options = ["--force", "--sign", identity, "--options", "runtime"]
        options += ["--timestamp"] if args.identity else ["--timestamp=none"]
        for path in sorted(app.rglob("*"), key=lambda item: len(item.parts), reverse=True):
            if is_macho(path) and path != contents / "MacOS/GUIDE-IEI":
                run("codesign", *options, path)
        run("codesign", *options, framework)
        run("codesign", *options, app)
        run("codesign", "--verify", "--deep", "--strict", app)
        if args.notary_profile:
            submission = stage / "notarize.zip"
            run("ditto", "-c", "-k", "--keepParent", app, submission)
            run("xcrun", "notarytool", "submit", submission, "--keychain-profile", args.notary_profile, "--wait")
            run("xcrun", "stapler", "staple", app)
            run("xcrun", "stapler", "validate", app)
            run("spctl", "--assess", "--type", "execute", "--verbose", app)
        destination = output / (stem + ".app")
        run("ditto", app, destination)
        # Keep the installed app name stable, just like the DMG. The output
        # directory's architecture/version label belongs to the archive only.
        run("ditto", "-c", "-k", "--keepParent", app, output / (stem + ".zip"))
        dmg_root = stage / "dmg"
        dmg_root.mkdir()
        run("ditto", destination, dmg_root / "GUIDE-IEI.app")
        (dmg_root / "Applications").symlink_to("/Applications", target_is_directory=True)
        dmg = output / (stem + ".dmg")
        run("hdiutil", "create", "-volname", "GUIDE-IEI", "-srcfolder", dmg_root, "-format", "UDZO", dmg)
        if args.identity:
            run("codesign", "--sign", args.identity, "--timestamp", dmg)
        if args.notary_profile:
            run("xcrun", "notarytool", "submit", dmg, "--keychain-profile", args.notary_profile, "--wait")
            run("xcrun", "stapler", "staple", dmg)
            run("xcrun", "stapler", "validate", dmg)
        sums = [f"{sha256(output / (stem + ext))}  {stem + ext}" for ext in (".zip", ".dmg")]
        (output / (stem + ".sha256sums.txt")).write_text("\n".join(sums) + "\n")
        print(f"Built {destination}\nPreview: {args.preview}; Developer ID signed: {bool(args.identity)}", flush=True)


if __name__ == "__main__":
    main()
