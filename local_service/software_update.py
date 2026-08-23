"""Self-update from GitHub Releases, applied through a file manifest.

The code folder a clinician downloaded also holds their annotation datasets
(references/, by default) and a hand-edited config — so a software update
must never be "replace the folder". Every release ships a manifest listing
exactly the files the release owns (the repository's tracked files); an
update replaces those, deletes only files the PREVIOUS release owned that
the new one no longer ships, and touches nothing else.

Design rules:
- The service performs its own release lookup and downloads only from the
  project's own GitHub release URLs — it never fetches a URL the browser
  supplies.
- The downloaded archive must verify against the release's sha256sums
  asset before a single file is touched.
- The user's annotation config is never overwritten: when a release's
  config carries keys the user's copy lacks, the release version is
  written BESIDE it (`.new`) and the new keys are reported, preserving
  every user edit and comment.
- The previous version's files are snapshotted before the swap; rollback
  restores them with one call.
- Nothing here runs automatically: check() and install() are invoked only
  by explicit user actions in the workbench.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path

GITHUB_REPO = "yimingluo-md/guide-iei"
RELEASE_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
DOWNLOAD_URL_PREFIX = f"https://github.com/{GITHUB_REPO}/releases/download/"
MANIFEST_NAME = "release-manifest.txt"
SUMS_ASSET_NAME = "sha256sums.txt"
# Files the user edits by hand: never overwritten, release copy lands
# beside them as <name>.new when it differs.
PRESERVED_USER_FILES = ("config/annotation.config.yaml",)
# Changes to these mean the next start must do extra work; the updater
# reports them so the UI can set expectations honestly.
DEPENDENCY_FILES = ("webui/package.json", "webui/package-lock.json")
CONTAINER_FILES = ("docker/Dockerfile", "docker/build.sh")
MAX_ARCHIVE_BYTES = 500 * 1024 * 1024


def _default_fetch(url: str, timeout: int = 60) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "guide-iei-updater",
            "Accept": "application/octet-stream, application/vnd.github+json",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = response.read(MAX_ARCHIVE_BYTES + 1)
    if len(data) > MAX_ARCHIVE_BYTES:
        raise ValueError("release download exceeds the size limit")
    return data


def parse_version(value: str) -> tuple[int, ...]:
    numbers = re.findall(r"\d+", str(value or ""))
    return tuple(int(number) for number in numbers[:4]) or (0,)


def _safe_relative_path(value: str) -> str:
    """Validate a manifest/archive path: relative, inside the tree."""
    path = str(value).strip()
    if (
        not path
        or path.startswith(("/", "\\"))
        or "\\" in path
        or re.match(r"^[A-Za-z]:", path)
        or any(part in ("..", "") for part in path.split("/"))
    ):
        raise ValueError(f"unsafe path in release payload: {value!r}")
    return path


class SoftwareUpdater:
    def __init__(self, repo_root: Path, state_dir: Path, fetch=None):
        self.repo_root = Path(repo_root).resolve()
        self.updates_dir = Path(state_dir).resolve() / "software-updates"
        self.rollback_dir = self.updates_dir / "rollback"
        self._fetch = fetch or _default_fetch

    # ------------------------------------------------------------------ #
    # inspection
    # ------------------------------------------------------------------ #
    def current_version(self) -> str:
        try:
            return (self.repo_root / "VERSION").read_text().strip() or "unknown"
        except OSError:
            return "unknown"

    def status(self) -> dict:
        rollback_version = None
        version_file = self.rollback_dir / "rollback-version.txt"
        if version_file.is_file():
            rollback_version = version_file.read_text().strip() or None
        return {
            "current_version": self.current_version(),
            "repo": GITHUB_REPO,
            "rollback_available": rollback_version is not None,
            "rollback_version": rollback_version,
        }

    def check(self) -> dict:
        current = self.current_version()
        base = {"ok": False, "current_version": current, "repo": GITHUB_REPO}
        try:
            release = json.loads(self._fetch(RELEASE_API_URL).decode("utf-8"))
        except Exception as error:  # network, JSON — report, never crash
            return {**base, "error": f"release lookup failed: {error}"}
        tag = str(release.get("tag_name") or "")
        latest = tag.lstrip("vV") or "unknown"
        assets = release.get("assets") or []
        zip_asset = next(
            (
                asset for asset in assets
                if str(asset.get("name", "")).startswith("guide-iei-")
                and str(asset.get("name", "")).endswith(".zip")
            ),
            None,
        )
        sums_asset = next(
            (
                asset for asset in assets
                if asset.get("name") == SUMS_ASSET_NAME
            ),
            None,
        )
        result = {
            **base,
            "ok": True,
            "latest_version": latest,
            "tag": tag,
            "published_at": release.get("published_at"),
            "notes": str(release.get("body") or ""),
            "update_available": parse_version(latest) > parse_version(current),
        }
        if zip_asset and sums_asset:
            result["assets"] = {
                "zip_name": zip_asset.get("name"),
                "zip_url": zip_asset.get("browser_download_url"),
                "sums_url": sums_asset.get("browser_download_url"),
            }
        else:
            result["update_available"] = False
            result["error"] = (
                "the latest release does not carry an installable archive "
                "and checksum; update from it manually or wait for a "
                "complete release"
            )
        return result

    # ------------------------------------------------------------------ #
    # install
    # ------------------------------------------------------------------ #
    def install(self) -> dict:
        if not (self.repo_root / "VERSION").is_file():
            raise ValueError(
                "the software folder does not look like a GUIDE-IEI "
                "installation (VERSION is missing); refusing to update it"
            )
        info = self.check()
        if not info.get("ok"):
            raise ValueError(info.get("error") or "release lookup failed")
        if not info.get("update_available"):
            raise ValueError(
                info.get("error")
                or "no newer release is available to install"
            )
        assets = info["assets"]
        zip_url, sums_url = assets["zip_url"], assets["sums_url"]
        for url in (zip_url, sums_url):
            if not str(url).startswith(DOWNLOAD_URL_PREFIX):
                raise ValueError(
                    f"release asset is not hosted at the project's own "
                    f"release location: {url}"
                )

        archive = self._fetch(zip_url)
        digest = hashlib.sha256(archive).hexdigest()
        expected = self._expected_sha256(
            self._fetch(sums_url).decode("utf-8", "replace"),
            str(assets["zip_name"]),
        )
        if digest != expected:
            raise ValueError(
                "the downloaded release archive failed its sha256 check — "
                "refusing to install it (try again; if this repeats, the "
                "release assets are inconsistent)"
            )

        self.updates_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=self.updates_dir) as staging_name:
            staging = Path(staging_name)
            self._extract_archive(archive, staging)
            manifest = self._read_manifest(staging / MANIFEST_NAME)
            missing = [
                path for path in manifest
                if not (staging / path).is_file()
            ]
            if missing:
                raise ValueError(
                    f"release archive is missing {len(missing)} manifested "
                    f"file(s), e.g. {missing[0]!r} — refusing a partial "
                    "install"
                )
            old_manifest = self._installed_manifest()
            summary = self._apply(staging, manifest, old_manifest)

        (self.updates_dir / "installed-manifest.txt").write_text(
            "\n".join(manifest) + "\n"
        )
        summary.update({
            "ok": True,
            "installed_version": info["latest_version"],
            "previous_version": info["current_version"],
            "restart_required": True,
        })
        summary["wsl_origin_synced"] = self._sync_wsl_origin(manifest, old_manifest)
        return summary

    # ------------------------------------------------------------------ #
    # rollback
    # ------------------------------------------------------------------ #
    def rollback(self) -> dict:
        version_file = self.rollback_dir / "rollback-version.txt"
        manifest_file = self.rollback_dir / "rollback-manifest.txt"
        if not (version_file.is_file() and manifest_file.is_file()):
            raise ValueError("no previous version is available to restore")
        restored_version = version_file.read_text().strip()
        for path in self._read_manifest(manifest_file):
            source = self.rollback_dir / "files" / path
            if not source.is_file():
                continue
            self._replace_file(source.read_bytes(), self.repo_root / path,
                               mode=source.stat().st_mode & 0o777)
        added_file = self.rollback_dir / "rollback-added.txt"
        if added_file.is_file():
            for path in self._read_manifest(added_file):
                (self.repo_root / path).unlink(missing_ok=True)
        previous_manifest = self.rollback_dir / "previous-installed-manifest.txt"
        installed = self.updates_dir / "installed-manifest.txt"
        if previous_manifest.is_file():
            shutil.copy2(previous_manifest, installed)
        else:
            installed.unlink(missing_ok=True)
        shutil.rmtree(self.rollback_dir, ignore_errors=True)
        return {
            "ok": True,
            "restored_version": restored_version,
            "restart_required": True,
        }

    # ------------------------------------------------------------------ #
    # internals
    # ------------------------------------------------------------------ #
    @staticmethod
    def _expected_sha256(sums_text: str, asset_name: str) -> str:
        for line in sums_text.splitlines():
            fields = line.split()
            if len(fields) >= 2 and fields[-1].lstrip("*") == asset_name:
                candidate = fields[0].lower()
                if re.fullmatch(r"[0-9a-f]{64}", candidate):
                    return candidate
        raise ValueError(
            f"the release checksum list has no usable entry for {asset_name}"
        )

    @staticmethod
    def _extract_archive(archive: bytes, staging: Path) -> None:
        import io

        with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
            for member in bundle.infolist():
                if member.is_dir():
                    continue
                relative = _safe_relative_path(member.filename)
                target = staging / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(member) as handle:
                    target.write_bytes(handle.read())
                # git archive records POSIX modes; keep executables
                # executable across the update.
                mode = (member.external_attr >> 16) & 0o777
                if mode:
                    os.chmod(target, mode)

    @staticmethod
    def _read_manifest(path: Path) -> list[str]:
        entries = []
        for line in path.read_text().splitlines():
            line = line.strip()
            if line:
                entries.append(_safe_relative_path(line))
        if not entries:
            raise ValueError(f"empty manifest: {path}")
        return entries

    def _installed_manifest(self) -> list[str] | None:
        stored = self.updates_dir / "installed-manifest.txt"
        if stored.is_file():
            return self._read_manifest(stored)
        return None

    @staticmethod
    def _replace_file(content: bytes, destination: Path, mode: int | None = None) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(
            f".{destination.name}.{os.getpid()}.update-tmp"
        )
        temporary.write_bytes(content)
        if mode:
            os.chmod(temporary, mode)
        os.replace(temporary, destination)

    def _apply(self, staging: Path, manifest: list[str],
               old_manifest: list[str] | None) -> dict:
        # Snapshot before touching anything: every file the swap will
        # replace or delete, plus which paths are brand new (so rollback
        # can remove them again).
        shutil.rmtree(self.rollback_dir, ignore_errors=True)
        files_dir = self.rollback_dir / "files"
        touched = list(dict.fromkeys([*manifest, *(old_manifest or [])]))
        snapshotted, added = [], []
        for path in touched:
            existing = self.repo_root / path
            if existing.is_file():
                backup = files_dir / path
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(existing, backup)
                snapshotted.append(path)
            elif path in manifest:
                added.append(path)
        self.rollback_dir.mkdir(parents=True, exist_ok=True)
        (self.rollback_dir / "rollback-manifest.txt").write_text(
            "\n".join(snapshotted) + "\n" if snapshotted else "\n"
        )
        (self.rollback_dir / "rollback-added.txt").write_text(
            "\n".join(added) + "\n" if added else ""
        )
        (self.rollback_dir / "rollback-version.txt").write_text(
            self.current_version() + "\n"
        )
        stored = self.updates_dir / "installed-manifest.txt"
        if stored.is_file():
            shutil.copy2(
                stored, self.rollback_dir / "previous-installed-manifest.txt"
            )

        deps_changed = False
        container_changed = False
        config_review: list[str] = []
        for path in manifest:
            staged = staging / path
            destination = self.repo_root / path
            new_bytes = staged.read_bytes()
            if path in PRESERVED_USER_FILES and destination.is_file():
                if destination.read_bytes() != new_bytes:
                    self._replace_file(
                        new_bytes, destination.with_name(destination.name + ".new")
                    )
                    config_review.append(path)
                continue
            if path in DEPENDENCY_FILES and (
                not destination.is_file()
                or destination.read_bytes() != new_bytes
            ):
                deps_changed = True
            if path in CONTAINER_FILES and (
                not destination.is_file()
                or destination.read_bytes() != new_bytes
            ):
                container_changed = True
            self._replace_file(
                new_bytes, destination, mode=staged.stat().st_mode & 0o777
            )

        removed = []
        if old_manifest is not None:
            preserved = set(PRESERVED_USER_FILES)
            for path in old_manifest:
                if path in manifest or path in preserved:
                    continue
                target = self.repo_root / path
                if target.is_file():
                    target.unlink()
                    removed.append(path)

        if deps_changed:
            # The launcher runs npm install on the next start when this
            # flag exists; the UI warns that the restart takes longer.
            (self.repo_root / "webui" / ".dependencies-updated").write_text("1\n")
        return {
            "config_review_needed": config_review,
            "dependencies_changed": deps_changed,
            "container_changed": container_changed,
            "files_replaced": len(manifest) - len(config_review),
            "files_removed": removed,
        }

    def _sync_wsl_origin(self, manifest: list[str],
                         old_manifest: list[str] | None) -> bool:
        """Mirror the update onto the Windows-side folder the WSL copy came
        from, so a later launcher refresh cannot silently downgrade."""
        origin_file = self.repo_root / ".wsl-origin"
        if not origin_file.is_file():
            return False
        origin = Path(origin_file.read_text().strip())
        if not (origin.is_dir() and (origin / "VERSION").is_file()):
            return False
        try:
            for path in manifest:
                source = self.repo_root / path
                if path in PRESERVED_USER_FILES or not source.is_file():
                    continue
                self._replace_file(
                    source.read_bytes(), origin / path,
                    mode=source.stat().st_mode & 0o777,
                )
            for path in old_manifest or []:
                if path not in manifest and path not in PRESERVED_USER_FILES:
                    (origin / path).unlink(missing_ok=True)
        except OSError:
            return False
        return True
