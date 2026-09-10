"""Persistent, conservative storage-location registry for the local workbench.

The registry is deliberately tiny and remains in the user's home directory.
It is separate from the relocatable workbench state so the application can
find an external data drive without falling back to a fresh default database.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Literal


StorageKind = Literal["annotation", "data", "temporary"]
STORAGE_KINDS: tuple[StorageKind, ...] = ("annotation", "data", "temporary")
REGISTRY_VERSION = 1
STORAGE_MARKER = ".iei-variant-review-storage.json"


class StorageRegistryError(RuntimeError):
    """The bootstrap exists but cannot safely identify configured storage."""


def default_registry_path() -> Path:
    override = os.environ.get("IEI_WORKBENCH_STORAGE_REGISTRY", "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".iei-variant-review-bootstrap.json"


def _resolved(path: Path | str) -> Path:
    return Path(path).expanduser().resolve(strict=False)


def path_is_dir(path: Path) -> bool:
    """Permission-safe directory check for Python versions before 3.14."""
    try:
        return path.is_dir()
    except OSError:
        return False


def _filesystem_type(path: Path) -> str:
    """Best-effort filesystem type for an existing path or its nearest parent."""
    try:
        probe = path
        while not probe.exists() and probe != probe.parent:
            probe = probe.parent
        if not probe.exists():
            return ""
        if os.name == "posix" and platform.system() == "Darwin":
            result = subprocess.run(
                ["df", "-Y", "-P", str(probe)],
                capture_output=True, text=True, check=False, timeout=5,
            )
            if result.returncode != 0:
                return ""
            lines = [line for line in result.stdout.splitlines() if line.strip()]
            fields = lines[-1].split() if len(lines) >= 2 else []
            return fields[1].casefold() if len(fields) >= 2 else ""
        elif os.name == "posix":
            result = subprocess.run(
                ["stat", "-f", "-c", "%T", str(probe)],
                capture_output=True, text=True, check=False, timeout=5,
            )
        else:
            return ""
        return result.stdout.strip().casefold() if result.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def storage_path_warning(path: Path, kind: StorageKind = "data") -> str:
    components = [part.casefold() for part in path.parts]
    cloud_component = any(
        component == "box"
        or component.startswith("box sync")
        or any(marker in component for marker in (
            "onedrive", "icloud drive", "dropbox", "google drive",
            "sharepoint", "creative cloud",
        ))
        for component in components
    )
    if cloud_component and kind != "annotation":
        return "Cloud-synchronised folders are not suitable for the active SQLite cohort database."
    filesystem = _filesystem_type(path)
    network_types = {
        "nfs", "nfs4", "smbfs", "cifs", "afpfs", "webdav", "fuse.sshfs",
    }
    is_network = (
        str(path).startswith(("//", "\\\\", "/net/"))
        or filesystem in network_types
        or filesystem.startswith(("nfs", "smb", "cifs", "afp", "webdav"))
    )
    if is_network:
        if kind == "annotation":
            return "Network storage is supported for read-only annotation datasets but tabix-heavy VEP annotation may be slower."
        return "Network storage is not recommended for the active SQLite cohort database because locking and latency may be unreliable."
    if filesystem in {"exfat", "msdos", "msdosfs", "vfat"}:
        if kind == "annotation":
            return (
                "This drive is exFAT/FAT formatted. Sustained multi-gigabyte dataset "
                "downloads are unreliable on exFAT under macOS (stalls and device "
                "disconnects have been observed) and sparse files are unsupported. "
                "Reformatting the drive as APFS (macOS) or ext4 (Linux) is strongly "
                "recommended before storing annotation datasets on it."
            )
        return "exFAT/FAT storage is not recommended for the active SQLite cohort database; use APFS, NTFS, or ext4."
    if cloud_component:
        return "Cloud-synchronised annotation storage may be substantially slower than a local SSD."
    return ""


def _windows_path(path: Path) -> str | None:
    """Return the Windows spelling for a mounted WSL drive when available."""
    if not (os.environ.get("WSL_DISTRO_NAME") or os.environ.get("WSL_INTEROP")):
        return None
    parts = path.parts
    if len(parts) < 3 or parts[0] != "/" or parts[1] != "mnt" or len(parts[2]) != 1:
        return None
    drive = parts[2].upper()
    remainder = "\\".join(parts[3:])
    return f"{drive}:\\{remainder}" if remainder else f"{drive}:\\"


class StorageLocationRegistry:
    """Read/write configured roots while retaining safe defaults.

    ``persist=False`` is used by direct service/unit-test construction. The
    normal application passes a persistent home-directory bootstrap path.
    """

    def __init__(
        self,
        pipeline_root: Path,
        default_data_root: Path,
        *,
        registry_path: Path | None = None,
        persist: bool = True,
    ):
        self.pipeline_root = _resolved(pipeline_root)
        self.default_annotation_root = _resolved(
            os.environ.get("IEI_DEFAULT_ANNOTATION_ROOT") or self.pipeline_root / "references"
        )
        self.default_data_root = _resolved(default_data_root)
        self.registry_path = _resolved(registry_path or default_registry_path())
        self.backup_path = self.registry_path.with_name(self.registry_path.name + ".backup")
        self.persist = persist
        self._data = self._load()
        if self.persist and self.registry_path.is_file() and not self.backup_path.exists():
            self._atomic_write(self.backup_path, self._data)

    def _defaults(self) -> dict:
        return {
            "version": REGISTRY_VERSION,
            "revision": 0,
            "annotation_root": str(self.default_annotation_root),
            "data_root": str(self.default_data_root),
            # Empty means "follow data root". This keeps the default simple.
            "temporary_root": "",
            "annotation_storage_id": "",
            "data_storage_id": "",
            "temporary_storage_id": "",
            # This small, user-owned audit trail survives a restart after a
            # successful migration. It deliberately contains paths only, not
            # data or any patient metadata.
            "migrations": [],
        }

    def _load(self) -> dict:
        defaults = self._defaults()
        try:
            registry_exists = self.registry_path.exists()
            backup_exists = self.backup_path.exists()
        except OSError as exc:
            raise StorageRegistryError(
                f"The storage bootstrap location cannot be inspected: {exc}"
            ) from exc
        if not registry_exists and not backup_exists:
            return defaults
        errors: list[str] = []
        valid: list[tuple[Path, dict]] = []
        for candidate, candidate_exists in (
            (self.registry_path, registry_exists),
            (self.backup_path, backup_exists),
        ):
            if not candidate_exists:
                continue
            try:
                raw = json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                errors.append(f"{candidate}: {exc}")
                continue
            if not isinstance(raw, dict) or raw.get("version") != REGISTRY_VERSION:
                errors.append(f"{candidate}: unsupported or missing registry version")
                continue
            for key in ("annotation_root", "data_root", "temporary_root"):
                if not isinstance(raw.get(key), str):
                    raw[key] = defaults[key]
            for key in ("annotation_storage_id", "data_storage_id", "temporary_storage_id"):
                if not isinstance(raw.get(key), str):
                    raw[key] = ""
            if not isinstance(raw.get("migrations"), list):
                raw["migrations"] = []
            if not isinstance(raw.get("revision"), int):
                raw["revision"] = 0
            valid.append((candidate, {**defaults, **raw}))
        if valid:
            _selected_path, loaded = max(
                valid, key=lambda item: int(item[1].get("revision") or 0)
            )
            if self.persist:
                # Heal a missing, stale, or corrupt peer copy immediately.
                self._atomic_write(self.backup_path, loaded)
                self._atomic_write(self.registry_path, loaded)
            return loaded
        raise StorageRegistryError(
            "The storage bootstrap is unreadable or incompatible. The workbench will not "
            "fall back to an empty database. Restore or remove both bootstrap files only "
            f"after locating the real Sample Library. Details: {'; '.join(errors)}"
        )

    @staticmethod
    def _atomic_write(path: Path, value: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.partial")
        payload = json.dumps(value, indent=2, sort_keys=True) + "\n"
        try:
            with temporary.open("w", encoding="utf-8") as handle:
                try:
                    os.fchmod(handle.fileno(), 0o600)
                except (AttributeError, OSError):
                    pass
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            temporary.replace(path)
            try:
                directory_fd = os.open(path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            except OSError:
                pass
        finally:
            temporary.unlink(missing_ok=True)

    def _save(self) -> None:
        if not self.persist:
            return
        self._data["revision"] = int(self._data.get("revision") or 0) + 1
        # Keep two independently atomic copies. A truncated/deleted primary is
        # recovered from the backup instead of silently selecting defaults.
        self._atomic_write(self.backup_path, self._data)
        self._atomic_write(self.registry_path, self._data)

    def root(self, kind: StorageKind) -> Path:
        if kind not in STORAGE_KINDS:
            raise ValueError(f"unsupported storage location: {kind}")
        if kind == "annotation":
            return _resolved(self._data["annotation_root"] or self.default_annotation_root)
        if kind == "data":
            return _resolved(self._data["data_root"] or self.default_data_root)
        configured = self._data.get("temporary_root", "")
        return _resolved(configured) if configured else self.root("data")

    def set_root(
        self, kind: StorageKind, path: Path | str | None, *, storage_id: str | None = None
    ) -> Path:
        if kind not in STORAGE_KINDS:
            raise ValueError(f"unsupported storage location: {kind}")
        key = f"{kind}_root"
        if kind == "temporary" and path is None:
            self._data[key] = ""
            self._data["temporary_storage_id"] = ""
            self._save()
            return self.root(kind)
        if path is None:
            raise ValueError("a storage path is required")
        resolved = _resolved(path)
        self._data[key] = str(resolved)
        if storage_id is not None:
            self._data[f"{kind}_storage_id"] = storage_id
        self._save()
        return resolved

    def storage_id(self, kind: StorageKind) -> str:
        return str(self._data.get(f"{kind}_storage_id") or "")

    @staticmethod
    def marker(path: Path) -> dict | None:
        marker_path = path / STORAGE_MARKER
        try:
            if not marker_path.is_file():
                return None
            value = json.loads(marker_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            hint = ""
            if getattr(exc, "errno", None) == 1:  # EPERM: macOS privacy (TCC) denial
                hint = (
                    " — on macOS this usually means the app that launched the "
                    "workbench lacks permission to read this drive. Grant the "
                    "terminal app Full Disk Access (System Settings > Privacy & "
                    "Security), or approve the Removable Volumes prompt, then "
                    "start the workbench again."
                )
            raise StorageRegistryError(
                f"storage marker is unreadable: {marker_path} ({exc}){hint}"
            ) from exc
        if not isinstance(value, dict):
            raise StorageRegistryError(f"storage marker is invalid: {marker_path}")
        return value

    def write_marker(
        self, path: Path, kind: StorageKind, *, storage_id: str | None = None,
        service: str = "", migration_id: str = "",
    ) -> str:
        identity = storage_id or uuid.uuid4().hex
        value = {"kind": kind, "storage_id": identity}
        if service:
            value["service"] = service
        if migration_id:
            value["migration_id"] = migration_id
        self._atomic_write(path / STORAGE_MARKER, value)
        return identity

    def validate_marker(self, kind: StorageKind, path: Path, *, required: bool) -> dict | None:
        value = self.marker(path)
        if value is None:
            if required:
                raise StorageRegistryError(
                    f"configured {kind} storage is missing its identity marker: {path}. "
                    "Reconnect the expected drive; a blank replacement will not be initialized."
                )
            return None
        if value.get("kind") != kind:
            raise StorageRegistryError(
                f"configured {kind} storage has a {value.get('kind') or 'different'} marker: {path}"
            )
        expected = self.storage_id(kind)
        observed = str(value.get("storage_id") or "")
        if expected and observed != expected:
            raise StorageRegistryError(
                f"configured {kind} storage identity does not match the selected drive: {path}"
            )
        return value

    def ensure_marker_identity(
        self, kind: StorageKind, path: Path, *, required: bool, service: str = ""
    ) -> dict | None:
        value = self.validate_marker(kind, path, required=required)
        if value is None:
            return None
        observed = str(value.get("storage_id") or "")
        if not observed:
            if not os.access(path, os.W_OK):
                if required:
                    raise StorageRegistryError(
                        f"legacy {kind} storage marker cannot be upgraded because the folder is read-only: {path}"
                    )
                return value
            observed = self.write_marker(path, kind, service=service)
            value = self.marker(path)
        if not self.storage_id(kind):
            self.set_root(kind, path, storage_id=observed)
        return value

    def record_migration(self, record: dict) -> None:
        """Retain a compact migration record so the preserved source is visible."""
        entries = [item for item in self._data.get("migrations", []) if isinstance(item, dict)]
        entries = [item for item in entries if item.get("id") != record.get("id")]
        entries.insert(0, dict(record))
        self._data["migrations"] = entries[:20]
        self._save()

    def migration_history(self) -> list[dict]:
        return [dict(item) for item in self._data.get("migrations", []) if isinstance(item, dict)]

    def is_default(self, kind: StorageKind) -> bool:
        if kind == "temporary":
            return not bool(self._data.get("temporary_root"))
        default = self.default_annotation_root if kind == "annotation" else self.default_data_root
        return self.root(kind) == default

    def describe(self, kind: StorageKind, *, active_path: Path | None = None) -> dict:
        root = self.root(kind)
        exists = path_is_dir(root)
        writable = False
        readable = False
        free_bytes = None
        total_bytes = None
        if exists:
            try:
                usage = shutil.disk_usage(root)
                free_bytes = usage.free
                total_bytes = usage.total
                writable = os.access(root, os.W_OK)
                readable = os.access(root, os.R_OK)
            except OSError:
                pass
        active = _resolved(active_path) if active_path else None
        warning = storage_path_warning(root, kind)
        return {
            "id": kind,
            "path": str(root),
            "exists": exists,
            "writable": writable,
            "readable": readable,
            "available": bool(exists and (readable if kind == "annotation" else writable)),
            "free_bytes": free_bytes,
            "total_bytes": total_bytes,
            "warning": warning,
            "uses_default": self.is_default(kind),
            "follows_data_root": kind == "temporary" and not self._data.get("temporary_root"),
            "active_path": str(active) if active else str(root),
            "restart_required": bool(active and active != root),
            "windows_path": _windows_path(root),
        }

    def as_dict(self, *, active_data_root: Path, active_annotation_root: Path, active_temporary_root: Path) -> dict:
        return {
            "registry_path": str(self.registry_path),
            "locations": [
                self.describe("annotation", active_path=active_annotation_root),
                self.describe("data", active_path=active_data_root),
                self.describe("temporary", active_path=active_temporary_root),
            ],
        }
