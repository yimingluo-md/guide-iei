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
- The user's annotation config is never overwritten — not by install and
  not by rollback: when a release's config carries changes, the release
  version is written BESIDE it (`.new`) and reported; rollback keeps
  whatever the user's config says at that moment.
- The previous version's files are snapshotted before the swap; rollback
  restores them with one call, including on releases that added no files.
- A crash mid-install is detected (a sentinel written before the swap):
  the same release can be reinstalled to repair, and rollback restores.
- Local machine-state files (.wsl-origin, the dependency flag) can never
  arrive from a release archive, and the Windows-origin mirror target is
  captured BEFORE any release content lands.
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
# Files the user edits by hand: never overwritten (install writes the
# release copy beside them as .new; rollback leaves them alone).
PRESERVED_USER_FILES = ("config/annotation.config.yaml",)
# Local machine state that must never arrive from a release archive: a
# hostile archive shipping these could redirect the origin mirror or
# force scripted work on the next launch.
FORBIDDEN_RELEASE_PATHS = (
    ".wsl-origin",
    "webui/.dependencies-updated",
    "webui/.build-required",
)
# Changes to these mean the next start must do extra work; the updater
# reports them so the UI can set expectations honestly.
DEPENDENCY_FILES = ("webui/package.json", "webui/package-lock.json")
CONTAINER_FILES = (
    "docker/.dockerignore",
    "docker/Dockerfile",
    "docker/build.sh",
    "docker/image_fingerprint.sh",
    "docker/PromoterAI.pm",
    "docker/LoGoFunc.pm",
    "docker/IndexedScores.pm",
)
MAX_ARCHIVE_BYTES = 500 * 1024 * 1024
# Bounds on what the archive may EXPAND to (audit M20): the download cap above
# bounds compressed bytes only, and every member used to be read whole into
# memory. A release is source code: no single file approaches 64 MiB and the
# tree is well under 1 GiB, so an archive that claims otherwise is malformed
# or hostile and is refused before anything is written.
MAX_MEMBER_BYTES = 64 * 1024 * 1024
MAX_EXTRACTED_BYTES = 1024 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 50_000
_EXTRACT_CHUNK = 1024 * 1024


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
    """Validate a manifest/archive path: relative, inside the tree, and
    never one of the local machine-state files."""
    path = str(value).strip()
    if (
        not path
        or path.startswith(("/", "\\"))
        or "\\" in path
        or re.match(r"^[A-Za-z]:", path)
        or any(part in ("..", "") for part in path.split("/"))
    ):
        raise ValueError(f"unsafe path in release payload: {value!r}")
    if path in FORBIDDEN_RELEASE_PATHS:
        raise ValueError(
            f"release payload carries local machine state ({path!r}); "
            "refusing the release"
        )
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

    @property
    def _sentinel(self) -> Path:
        return self.updates_dir / "update-in-progress.txt"

    @property
    def _restart_pending_file(self) -> Path:
        return self.updates_dir / "restart-pending.txt"

    def clear_restart_pending(self) -> None:
        """Called at service startup: a fresh process IS the restart."""
        self._restart_pending_file.unlink(missing_ok=True)

    def full_relaunch_required(self) -> dict:
        """Which launcher-only steps are pending after an update (audit M21).

        The in-app restart (exit 75) restarts only the Python service: the
        UI server keeps serving the bundle it was started with, and
        ``npm ci`` never runs. When an update changed ``webui/`` or its
        dependencies, only a complete close and relaunch of GUIDE-IEI
        rebuilds/reinstalls the interface; the flag files below are what
        ``start_workbench.sh`` consumes on that relaunch.
        """
        web = (self.repo_root / "webui" / ".build-required").is_file()
        deps = (self.repo_root / "webui" / ".dependencies-updated").is_file()
        return {
            "required": web or deps,
            "web_build_required": web,
            "dependencies_updated": deps,
        }

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
            # A crash mid-swap leaves a mixed tree that self-reports the
            # NEW version; the sentinel is the only honest witness.
            "incomplete_update": self._sentinel.is_file(),
            "restart_pending": self._restart_pending_file.is_file(),
            # True when an in-app restart would NOT finish the update: the
            # interface must be rebuilt or its dependencies reinstalled,
            # which only a full close-and-relaunch does.
            "full_relaunch_required": self.full_relaunch_required()["required"],
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
            "incomplete_update": self._sentinel.is_file(),
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
        repairing = self._sentinel.is_file()
        info = self.check()
        if not info.get("ok"):
            raise ValueError(info.get("error") or "release lookup failed")
        if "assets" not in info:
            raise ValueError(info.get("error") or "no installable release")
        preserve_rollback = repairing and (self.rollback_dir / "rollback-version.txt").is_file()
        if preserve_rollback and self._sentinel.read_text().strip() != info["latest_version"]:
            raise ValueError(
                "a different release became available during the interrupted update; "
                "roll back the incomplete update before installing it"
            )
        # A repair (interrupted earlier install) may reinstall the SAME
        # version — the tree already claims it while old files remain.
        if not info.get("update_available") and not repairing:
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

        # The origin mirror target must come from THIS machine's state as
        # it was before any release content landed, never from the
        # archive being installed.
        origin = self._wsl_origin_target()

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
            wrong_kind = [
                path for path in manifest
                if (self.repo_root / path).is_dir()
            ]
            if wrong_kind:
                raise ValueError(
                    f"a folder sits where the release ships a file "
                    f"({wrong_kind[0]}); move it aside and install again"
                )
            old_manifest = self._installed_manifest()
            self._sentinel.write_text(info["latest_version"] + "\n")
            summary = self._apply(staging, manifest, old_manifest,
                                  preserve_rollback=preserve_rollback)

        (self.updates_dir / "installed-manifest.txt").write_text(
            "\n".join(manifest) + "\n"
        )
        summary.update({
            "ok": True,
            "installed_version": info["latest_version"],
            "previous_version": info["current_version"],
            "restart_required": True,
            "repaired": repairing,
        })
        summary["wsl_origin_synced"] = self._sync_wsl_origin(
            origin, manifest, old_manifest
        )
        self._restart_pending_file.write_text(info["latest_version"] + "\n")
        self._sentinel.unlink(missing_ok=True)
        return summary

    # ------------------------------------------------------------------ #
    # rollback
    # ------------------------------------------------------------------ #
    def rollback(self) -> dict:
        version_file = self.rollback_dir / "rollback-version.txt"
        manifest_file = self.rollback_dir / "rollback-manifest.txt"
        if not (version_file.is_file() and manifest_file.is_file()):
            raise ValueError("no previous version is available to restore")
        origin = self._wsl_origin_target()
        restored_version = version_file.read_text().strip()
        restored = self._read_manifest(manifest_file, allow_empty=True)
        self._validate_rollback_files(restored)
        dependency_before = self._dependency_bytes()
        container_before = self._container_bytes()
        for path in restored:
            source = self.rollback_dir / "files" / path
            if not source.is_file():
                continue
            destination = self.repo_root / path
            if path in PRESERVED_USER_FILES and destination.is_file():
                # The user's config is theirs at every point in time —
                # including edits made AFTER the install (merging keys the
                # .new file suggested). Rollback never reverts it.
                continue
            self._replace_file(source.read_bytes(), destination,
                               mode=source.stat().st_mode & 0o777)
        added = self._read_manifest(
            self.rollback_dir / "rollback-added.txt", allow_empty=True,
            allow_missing=True,
        )
        for path in added:
            target = self.repo_root / path
            try:
                target.unlink(missing_ok=True)
            except OSError:
                # A directory or permission oddity at an added path must
                # not strand the rollback; the file list below reports
                # exactly what was restored.
                continue
        self._prune_empty_dirs(added, self.repo_root)
        # A stale ".new" config from the rolled-back release would invite
        # merging keys for a version no longer installed.
        for path in PRESERVED_USER_FILES:
            preserved = self.repo_root / path
            preserved.with_name(preserved.name + ".new").unlink(missing_ok=True)
        previous_manifest = self.rollback_dir / "previous-installed-manifest.txt"
        installed = self.updates_dir / "installed-manifest.txt"
        if previous_manifest.is_file():
            shutil.copy2(previous_manifest, installed)
        else:
            installed.unlink(missing_ok=True)
        dependencies_changed = dependency_before != self._dependency_bytes()
        container_changed = container_before != self._container_bytes()
        if dependencies_changed:
            (self.repo_root / "webui" / ".dependencies-updated").write_text("1\n")
        # A rollback changes application code even when package manifests are
        # identical. The next start must not serve a stale production build.
        web_changed = any(path.startswith("webui/") for path in [*restored, *added])
        if web_changed:
            (self.repo_root / "webui" / ".build-required").write_text("1\n")
        synced = self._sync_wsl_origin(
            origin, restored, [*restored, *added]
        )
        shutil.rmtree(self.rollback_dir, ignore_errors=True)
        self._sentinel.unlink(missing_ok=True)
        self._restart_pending_file.write_text(restored_version + "\n")
        return {
            "ok": True,
            "restored_version": restored_version,
            "restart_required": True,
            "dependencies_changed": dependencies_changed,
            "web_build_required": web_changed,
            "container_changed": container_changed,
            "wsl_origin_synced": synced,
        }

    # ------------------------------------------------------------------ #
    # internals
    # ------------------------------------------------------------------ #
    def _validate_rollback_files(self, manifest: list[str]) -> None:
        if any(not (self.rollback_dir / "files" / path).is_file() for path in manifest):
            raise ValueError("the rollback snapshot is incomplete; restore a complete software backup")

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
        """Extract the release into ``staging`` with bounded, streamed I/O.

        Audit M20: the declared sizes are checked first (a member above
        MAX_MEMBER_BYTES, a total above MAX_EXTRACTED_BYTES, or an
        implausible member count is refused before any write), and each
        member is then copied in chunks with the ACTUAL byte count enforced
        — a zip entry can lie about its uncompressed size, so the declared
        total is a pre-check, not the guard.
        """
        import io

        with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
            members = [member for member in bundle.infolist() if not member.is_dir()]
            if len(members) > MAX_ARCHIVE_MEMBERS:
                raise ValueError(
                    f"release archive lists {len(members)} files; the limit is "
                    f"{MAX_ARCHIVE_MEMBERS}"
                )
            declared_total = 0
            for member in members:
                if member.file_size > MAX_MEMBER_BYTES:
                    raise ValueError(
                        f"release archive member {member.filename!r} declares "
                        f"{member.file_size} bytes; the per-file limit is "
                        f"{MAX_MEMBER_BYTES}"
                    )
                declared_total += member.file_size
            if declared_total > MAX_EXTRACTED_BYTES:
                raise ValueError(
                    f"release archive declares {declared_total} extracted bytes; "
                    f"the limit is {MAX_EXTRACTED_BYTES}"
                )
            written_total = 0
            for member in members:
                relative = _safe_relative_path(member.filename)
                target = staging / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                written = 0
                with bundle.open(member) as handle, target.open("wb") as output:
                    while True:
                        chunk = handle.read(_EXTRACT_CHUNK)
                        if not chunk:
                            break
                        written += len(chunk)
                        written_total += len(chunk)
                        if written > MAX_MEMBER_BYTES:
                            raise ValueError(
                                f"release archive member {member.filename!r} "
                                f"exceeds the per-file limit of {MAX_MEMBER_BYTES} "
                                "bytes while extracting"
                            )
                        if written_total > MAX_EXTRACTED_BYTES:
                            raise ValueError(
                                "release archive exceeds the extraction limit of "
                                f"{MAX_EXTRACTED_BYTES} bytes while extracting"
                            )
                        output.write(chunk)
                # git archive records POSIX modes; keep executables
                # executable across the update.
                mode = (member.external_attr >> 16) & 0o777
                if mode:
                    os.chmod(target, mode)

    @staticmethod
    def _read_manifest(path: Path, allow_empty: bool = False,
                       allow_missing: bool = False) -> list[str]:
        if allow_missing and not path.is_file():
            return []
        entries = []
        for line in path.read_text().splitlines():
            line = line.strip()
            if line:
                entries.append(_safe_relative_path(line))
        if not entries and not allow_empty:
            raise ValueError(f"empty manifest: {path}")
        return entries

    def _installed_manifest(self) -> list[str] | None:
        stored = self.updates_dir / "installed-manifest.txt"
        if stored.is_file():
            return self._read_manifest(stored)
        return None

    def _dependency_bytes(self) -> tuple[bytes, ...]:
        values = []
        for path in DEPENDENCY_FILES:
            target = self.repo_root / path
            values.append(target.read_bytes() if target.is_file() else b"")
        return tuple(values)

    def _container_bytes(self) -> tuple[bytes, ...]:
        values = []
        for path in CONTAINER_FILES:
            target = self.repo_root / path
            values.append(target.read_bytes() if target.is_file() else b"")
        return tuple(values)

    @staticmethod
    def _replace_file(content: bytes, destination: Path, mode: int | None = None) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(
            f".{destination.name}.{os.getpid()}.update-tmp"
        )
        try:
            temporary.write_bytes(content)
            if mode:
                os.chmod(temporary, mode)
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _prune_empty_dirs(removed_paths: list[str], root: Path) -> None:
        """Remove directories a deletion pass emptied, up to (never
        including) root — a leftover empty Python package directory stays
        importable as a namespace package, shadowing real modules."""
        for path in removed_paths:
            parent = (root / path).parent
            while parent != root and root in parent.parents:
                try:
                    parent.rmdir()
                except OSError:
                    break
                parent = parent.parent

    def _apply(self, staging: Path, manifest: list[str],
               old_manifest: list[str] | None, *, preserve_rollback: bool = False) -> dict:
        # Snapshot before touching anything: every file the swap will
        # replace or delete, plus which paths are brand new (so rollback
        # can remove them again).
        if preserve_rollback:
            # A repair is the continuation of one update. Re-snapshotting the
            # mixed tree would erase the last known complete software version.
            self._validate_rollback_files(self._read_manifest(
                self.rollback_dir / "rollback-manifest.txt", allow_empty=True,
            ))
        else:
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
                "\n".join(snapshotted) + ("\n" if snapshotted else "")
            )
            (self.rollback_dir / "rollback-added.txt").write_text(
                "\n".join(added) + ("\n" if added else "")
            )
            stored = self.updates_dir / "installed-manifest.txt"
            if stored.is_file():
                shutil.copy2(
                    stored, self.rollback_dir / "previous-installed-manifest.txt"
                )
            # Written LAST: its presence is the promise that the snapshot is
            # complete, so a crash during the snapshot never offers a partial
            # rollback.
            (self.rollback_dir / "rollback-version.txt").write_text(
                self.current_version() + "\n"
            )
        deps_changed = False
        web_changed = False
        container_changed = False
        config_review: list[str] = []
        # VERSION is applied LAST: a crash mid-swap must leave a tree that
        # still reports the OLD version, so check() keeps offering the
        # update and a retry repairs instead of "already up to date".
        ordered = sorted(manifest, key=lambda path: path == "VERSION")
        for path in ordered:
            staged = staging / path
            destination = self.repo_root / path
            new_bytes = staged.read_bytes()
            content_changed = (
                not destination.is_file()
                or destination.read_bytes() != new_bytes
            )
            if path.startswith("webui/") and content_changed:
                web_changed = True
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
            # Case-insensitive comparison: on the case-insensitive
            # filesystems clinicians run (APFS, NTFS), deleting the OLD
            # spelling of a case-only rename would delete the file the
            # install just wrote.
            new_lower = {path.lower() for path in manifest}
            for path in old_manifest:
                if path.lower() in new_lower or path in preserved:
                    continue
                target = self.repo_root / path
                if target.is_file():
                    target.unlink()
                    removed.append(path)
                    if path.startswith("webui/"):
                        web_changed = True
                    if path in CONTAINER_FILES:
                        container_changed = True
            self._prune_empty_dirs(removed, self.repo_root)

        if deps_changed:
            # The launcher runs npm ci on the next full start when
            # this flag exists; the UI directs a full relaunch.
            (self.repo_root / "webui" / ".dependencies-updated").write_text("1\n")
        if web_changed:
            # start_workbench.sh rebuilds the production Next.js output before
            # it reopens the browser, so application updates cannot serve old
            # JavaScript from a previous release.
            (self.repo_root / "webui" / ".build-required").write_text("1\n")
        return {
            "config_review_needed": config_review,
            "dependencies_changed": deps_changed,
            "web_build_required": web_changed,
            "container_changed": container_changed,
            "files_replaced": len(manifest) - len(config_review),
            "files_removed": removed,
        }

    def _wsl_origin_target(self) -> Path | None:
        """The Windows-side folder this WSL copy came from, or None.

        Read from the machine's own pre-update state — a release archive
        can never ship this file (FORBIDDEN_RELEASE_PATHS)."""
        origin_file = self.repo_root / ".wsl-origin"
        if not origin_file.is_file():
            return None
        try:
            origin = Path(origin_file.read_text().strip())
        except OSError:
            return None
        if origin.is_dir() and (origin / "VERSION").is_file():
            return origin
        return None

    def _sync_wsl_origin(self, origin: Path | None, manifest: list[str],
                         old_manifest: list[str] | None) -> bool | None:
        """Mirror the applied file set onto the Windows-side folder the WSL
        copy came from, so a later launcher refresh cannot silently swap
        versions in either direction. Returns None when there is no origin,
        False when the mirror could not complete (the UI warns)."""
        if origin is None:
            return None
        try:
            for path in manifest:
                source = self.repo_root / path
                if path in PRESERVED_USER_FILES or not source.is_file():
                    continue
                self._replace_file(
                    source.read_bytes(), origin / path,
                    mode=source.stat().st_mode & 0o777,
                )
            removed = []
            new_lower = {path.lower() for path in manifest}
            for path in old_manifest or []:
                if path.lower() in new_lower or path in PRESERVED_USER_FILES:
                    continue
                (origin / path).unlink(missing_ok=True)
                removed.append(path)
            self._prune_empty_dirs(removed, origin)
        except OSError:
            return False
        return True
