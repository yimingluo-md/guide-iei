#!/usr/bin/env python3
import io
import json
import tempfile
import unittest
from unittest.mock import patch
import unittest.mock
import zipfile
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from local_service.software_update import (
    DOWNLOAD_URL_PREFIX,
    MANIFEST_NAME,
    RELEASE_API_URL,
    SoftwareUpdater,
    parse_version,
)


def build_archive(files: dict[str, bytes], executables: set[str] = frozenset()) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        for name, content in files.items():
            info = zipfile.ZipInfo(name)
            mode = 0o755 if name in executables else 0o644
            info.external_attr = mode << 16
            bundle.writestr(info, content)
    return buffer.getvalue()


class FakeGitHub:
    """Injectable fetcher serving one release."""

    def __init__(self, version: str, files: dict[str, bytes],
                 executables: set[str] = frozenset(),
                 corrupt_sums: bool = False):
        manifest = "\n".join(
            name for name in files if name != MANIFEST_NAME
        ) + "\n"
        payload = {**files, MANIFEST_NAME: manifest.encode()}
        self.archive = build_archive(payload, executables)
        zip_name = f"guide-iei-{version}.zip"
        import hashlib
        digest = hashlib.sha256(self.archive).hexdigest()
        if corrupt_sums:
            digest = "0" * 64
        self.sums = f"{digest}  {zip_name}\n".encode()
        self.release = json.dumps({
            "tag_name": f"v{version}",
            "published_at": "2026-08-23T00:00:00Z",
            "body": "Release notes.",
            "assets": [
                {"name": zip_name,
                 "browser_download_url": f"{DOWNLOAD_URL_PREFIX}v{version}/{zip_name}"},
                {"name": "sha256sums.txt",
                 "browser_download_url": f"{DOWNLOAD_URL_PREFIX}v{version}/sha256sums.txt"},
            ],
        }).encode()

    def __call__(self, url: str, timeout: int = 60) -> bytes:
        if url == RELEASE_API_URL:
            return self.release
        if url.endswith(".zip"):
            return self.archive
        if url.endswith("sha256sums.txt"):
            return self.sums
        raise AssertionError(f"unexpected fetch: {url}")


class SoftwareUpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.repo = base / "repo"
        self.state = base / "state"
        (self.repo / "config").mkdir(parents=True)
        (self.repo / "scripts").mkdir()
        (self.repo / "webui").mkdir()
        (self.repo / "references").mkdir()
        (self.repo / "VERSION").write_text("0.5.0\n")
        (self.repo / "scripts" / "run.sh").write_text("old script\n")
        (self.repo / "config" / "annotation.config.yaml").write_text(
            "# my clinic's cutoffs\nspliceai: 0.3\n"
        )
        (self.repo / "webui" / "package.json").write_text('{"old": true}\n')
        (self.repo / "references" / "huge.dataset").write_text("110GB stand-in\n")

    def tearDown(self):
        self.temp.cleanup()

    def release_files(self, **overrides) -> dict[str, bytes]:
        files = {
            "VERSION": b"0.6.0\n",
            "scripts/run.sh": b"new script\n",
            "scripts/added.sh": b"brand new\n",
            "config/annotation.config.yaml": b"spliceai: 0.5\nnewkey: 1\n",
            "webui/package.json": b'{"old": true}\n',
        }
        files.update({k: v for k, v in overrides.items() if v is not None})
        for key, value in overrides.items():
            if value is None:
                files.pop(key, None)
        return files

    def updater(self, github) -> SoftwareUpdater:
        return SoftwareUpdater(self.repo, self.state, fetch=github)

    def test_version_parsing_orders_releases(self):
        self.assertGreater(parse_version("0.10.0"), parse_version("0.9.9"))
        self.assertGreater(parse_version("v1.0.0"), parse_version("0.99.0"))
        self.assertEqual(parse_version("garbage"), (0,))

    def test_check_reports_availability_and_survives_offline(self):
        github = FakeGitHub("0.6.0", self.release_files())
        result = self.updater(github).check()
        self.assertTrue(result["ok"] and result["update_available"])
        self.assertEqual(result["latest_version"], "0.6.0")

        (self.repo / "VERSION").write_text("0.6.0\n")
        self.assertFalse(self.updater(github).check()["update_available"])
        (self.repo / "VERSION").write_text("0.7.0\n")
        self.assertFalse(self.updater(github).check()["update_available"])

        def offline(url, timeout=60):
            raise OSError("no network")

        result = SoftwareUpdater(self.repo, self.state, fetch=offline).check()
        self.assertFalse(result["ok"])
        self.assertIn("release lookup failed", result["error"])

    def test_install_swaps_manifested_files_and_nothing_else(self):
        github = FakeGitHub("0.6.0", self.release_files(),
                            executables={"scripts/run.sh"})
        summary = self.updater(github).install()
        self.assertTrue(summary["ok"] and summary["restart_required"])
        self.assertEqual(summary["installed_version"], "0.6.0")
        self.assertEqual((self.repo / "VERSION").read_text(), "0.6.0\n")
        self.assertEqual((self.repo / "scripts" / "run.sh").read_text(), "new script\n")
        self.assertTrue((self.repo / "scripts" / "run.sh").stat().st_mode & 0o100)
        self.assertTrue((self.repo / "scripts" / "added.sh").is_file())
        # Datasets and any non-manifested files are untouched.
        self.assertEqual(
            (self.repo / "references" / "huge.dataset").read_text(),
            "110GB stand-in\n",
        )
        # First install has no prior manifest: nothing is deleted.
        self.assertEqual(summary["files_removed"], [])

    def test_status_reports_when_only_a_full_relaunch_finishes_the_update(self):
        """Audit repro (M21): the in-app restart restarts the Python service
        only; a release that changed webui/ (or its dependencies) needs the
        launcher's rebuild / npm ci, i.e. a complete close-and-relaunch. The
        status must say so, so the UI never offers a restart that would keep
        serving the previous interface."""
        updater = self.updater(FakeGitHub("0.6.0", self.release_files()))
        self.assertFalse(updater.status()["full_relaunch_required"])
        # A release whose only interface change is source (no dependency
        # change) — the case the Restart button used to accept.
        github = FakeGitHub(
            "0.6.0", self.release_files(**{"webui/app/page.tsx": b"new ui\n"})
        )
        summary = self.updater(github).install()
        self.assertTrue(summary["web_build_required"])
        self.assertFalse(summary["dependencies_changed"])
        status = self.updater(github).status()
        self.assertTrue(status["restart_pending"])
        self.assertTrue(status["full_relaunch_required"])
        relaunch = self.updater(github).full_relaunch_required()
        self.assertEqual(
            relaunch,
            {"required": True, "web_build_required": True, "dependencies_updated": False},
        )
        # The launcher consumes the flag on a full start; once it is gone an
        # in-app restart is acceptable again.
        (self.repo / "webui" / ".build-required").unlink()
        self.assertFalse(self.updater(github).status()["full_relaunch_required"])

    def test_rollback_reports_web_build_required_like_install(self):
        github = FakeGitHub(
            "0.6.0", self.release_files(**{"webui/app/page.tsx": b"new ui\n"})
        )
        updater = self.updater(github)
        updater.install()
        (self.repo / "webui" / ".build-required").unlink(missing_ok=True)
        result = updater.rollback()
        self.assertEqual(result["restored_version"], "0.5.0")
        self.assertTrue(result["web_build_required"])
        self.assertTrue((self.repo / "webui" / ".build-required").is_file())

    def test_extraction_is_bounded_and_streamed(self):
        """Audit repro (M20): the 500 MB download cap bounded compressed
        bytes only, and each member was read whole into memory. Oversized
        members, a total beyond the extraction limit, and a member whose
        header under-declares its size are refused before/while writing."""
        import zlib
        from local_service import software_update as module

        # 1. A member that declares more than the per-file limit is refused
        #    before anything is written.
        oversized = {"scripts/big.bin": b"x" * 10}
        archive = build_archive(oversized)
        with patch.object(module, "MAX_MEMBER_BYTES", 5):
            with tempfile.TemporaryDirectory() as directory:
                with self.assertRaisesRegex(ValueError, "per-file limit"):
                    SoftwareUpdater._extract_archive(archive, Path(directory))
                self.assertEqual(list(Path(directory).iterdir()), [])

        # 2. Declared sizes that sum past the extraction limit are refused
        #    up front.
        many = {f"scripts/f{i}.txt": b"0123456789" for i in range(10)}
        archive = build_archive(many)
        with patch.object(module, "MAX_EXTRACTED_BYTES", 50):
            with tempfile.TemporaryDirectory() as directory:
                with self.assertRaisesRegex(ValueError, "extracted bytes"):
                    SoftwareUpdater._extract_archive(archive, Path(directory))
                self.assertEqual(list(Path(directory).iterdir()), [])

        # 3. A header that LIES about the uncompressed size is never trusted:
        #    zipfile stops at the declared size and fails its CRC, and the
        #    streamed byte count bounds the other direction (an entry whose
        #    declared size is honest but large is case 1). Either way the
        #    archive is refused, never silently extracted.
        payload = b"A" * 4096
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
            info = zipfile.ZipInfo("scripts/liar.txt")
            info.external_attr = 0o644 << 16
            bundle.writestr(info, payload)
        raw = bytearray(buffer.getvalue())
        # Rewrite the central-directory and local-header uncompressed sizes
        # to a small value so the declared total looks harmless.
        with zipfile.ZipFile(io.BytesIO(bytes(raw))) as check:
            self.assertEqual(check.infolist()[0].file_size, 4096)
        lying = self._with_declared_size(bytes(raw), 16)
        with zipfile.ZipFile(io.BytesIO(lying)) as check:
            self.assertEqual(check.infolist()[0].file_size, 16)
        with patch.object(module, "MAX_MEMBER_BYTES", 1024), \
                patch.object(module, "_EXTRACT_CHUNK", 256):
            with tempfile.TemporaryDirectory() as directory:
                with self.assertRaises((ValueError, zipfile.BadZipFile)):
                    SoftwareUpdater._extract_archive(lying, Path(directory))
        # The streamed guard itself: a reader that keeps producing bytes past
        # the declared size (simulated) trips the per-member limit mid-copy.
        class EndlessMember(io.RawIOBase):
            def read(self, size=-1):
                return b"Z" * (size if size and size > 0 else 4096)

        archive = build_archive({"scripts/ok.txt": b"fine"})
        with patch.object(module, "MAX_MEMBER_BYTES", 1000), \
                patch.object(module, "_EXTRACT_CHUNK", 256), \
                patch.object(zipfile.ZipFile, "open", return_value=EndlessMember()):
            with tempfile.TemporaryDirectory() as directory:
                with self.assertRaisesRegex(ValueError, "while extracting"):
                    SoftwareUpdater._extract_archive(archive, Path(directory))
                # At most the limit plus one chunk reached the disk.
                target = Path(directory) / "scripts" / "ok.txt"
                self.assertLessEqual(target.stat().st_size, 1000)

        # 4. A well-formed archive still extracts, chunked, with modes kept.
        archive = build_archive({"scripts/run.sh": b"#!/bin/sh\n" * 300}, {"scripts/run.sh"})
        with patch.object(module, "_EXTRACT_CHUNK", 64):
            with tempfile.TemporaryDirectory() as directory:
                SoftwareUpdater._extract_archive(archive, Path(directory))
                target = Path(directory) / "scripts" / "run.sh"
                self.assertEqual(target.read_bytes(), b"#!/bin/sh\n" * 300)
                self.assertTrue(target.stat().st_mode & 0o100)

    @staticmethod
    def _with_declared_size(archive: bytes, declared: int) -> bytes:
        """Patch every uncompressed-size field of a one-member zip."""
        import struct
        data = bytearray(archive)
        # Local file header: signature PK\x03\x04, uncompressed size at +22.
        local = data.find(b"PK\x03\x04")
        struct.pack_into("<I", data, local + 22, declared)
        # Central directory header: PK\x01\x02, uncompressed size at +24.
        central = data.find(b"PK\x01\x02")
        struct.pack_into("<I", data, central + 24, declared)
        return bytes(data)

    def test_user_config_is_never_overwritten(self):
        github = FakeGitHub("0.6.0", self.release_files())
        summary = self.updater(github).install()
        config = self.repo / "config" / "annotation.config.yaml"
        self.assertEqual(
            config.read_text(), "# my clinic's cutoffs\nspliceai: 0.3\n"
        )
        self.assertEqual(
            (config.with_name(config.name + ".new")).read_text(),
            "spliceai: 0.5\nnewkey: 1\n",
        )
        self.assertEqual(
            summary["config_review_needed"], ["config/annotation.config.yaml"]
        )
        # An identical config produces no .new file and no review flag.
        (self.repo / "VERSION").write_text("0.5.0\n")
        config.write_text("spliceai: 0.5\nnewkey: 1\n")
        config.with_name(config.name + ".new").unlink()
        summary = self.updater(github).install()
        self.assertEqual(summary["config_review_needed"], [])
        self.assertFalse(config.with_name(config.name + ".new").exists())

    def test_second_update_deletes_only_previously_owned_files(self):
        github = FakeGitHub("0.6.0", self.release_files())
        self.updater(github).install()
        stray = self.repo / "scripts" / "user_note.txt"
        stray.write_text("mine\n")
        second = FakeGitHub("0.7.0", self.release_files(
            VERSION=b"0.7.0\n", **{"scripts/added.sh": None}
        ))
        summary = self.updater(second).install()
        self.assertEqual(summary["files_removed"], ["scripts/added.sh"])
        self.assertFalse((self.repo / "scripts" / "added.sh").exists())
        self.assertTrue(stray.is_file())

    def test_checksum_mismatch_touches_nothing(self):
        github = FakeGitHub("0.6.0", self.release_files(), corrupt_sums=True)
        with self.assertRaisesRegex(ValueError, "sha256"):
            self.updater(github).install()
        self.assertEqual((self.repo / "VERSION").read_text(), "0.5.0\n")
        self.assertEqual((self.repo / "scripts" / "run.sh").read_text(), "old script\n")

    def test_traversal_paths_are_refused(self):
        files = self.release_files()
        files["../escape.sh"] = b"evil\n"
        github = FakeGitHub("0.6.0", files)
        with self.assertRaisesRegex(ValueError, "unsafe path"):
            self.updater(github).install()
        self.assertFalse((Path(self.temp.name) / "escape.sh").exists())
        self.assertEqual((self.repo / "VERSION").read_text(), "0.5.0\n")

    def test_dependency_change_writes_the_install_flag(self):
        github = FakeGitHub("0.6.0", self.release_files())
        summary = self.updater(github).install()
        self.assertFalse(summary["dependencies_changed"])
        self.assertFalse((self.repo / "webui" / ".dependencies-updated").exists())
        (self.repo / "VERSION").write_text("0.5.0\n")
        bumped = FakeGitHub("0.6.1", self.release_files(
            VERSION=b"0.6.1\n",
            **{"webui/package.json": b'{"new": true}\n'},
        ))
        summary = self.updater(bumped).install()
        self.assertTrue(summary["dependencies_changed"])
        self.assertTrue((self.repo / "webui" / ".dependencies-updated").is_file())
        self.assertTrue(summary["web_build_required"])
        self.assertTrue((self.repo / "webui" / ".build-required").is_file())

    def test_web_source_change_requires_a_fresh_production_build(self):
        github = FakeGitHub("0.6.0", self.release_files(
            **{"webui/app/page.tsx": b"export default function Page() { return null }\n"}
        ))
        summary = self.updater(github).install()
        self.assertTrue(summary["web_build_required"])
        self.assertTrue((self.repo / "webui" / ".build-required").is_file())

    def test_predictor_plugin_change_requires_a_container_rebuild(self):
        github = FakeGitHub("0.6.0", self.release_files(
            **{"docker/IndexedScores.pm": b"new indexed predictor adapter\n"}
        ))
        summary = self.updater(github).install()
        self.assertTrue(summary["container_changed"])

    def test_rollback_of_a_predictor_plugin_change_requires_a_container_rebuild(self):
        github = FakeGitHub("0.6.0", self.release_files(
            **{"docker/IndexedScores.pm": b"new indexed predictor adapter\n"}
        ))
        updater = self.updater(github)
        updater.install()
        result = updater.rollback()
        self.assertTrue(result["container_changed"])

    def test_rollback_restores_the_previous_version(self):
        github = FakeGitHub("0.6.0", self.release_files())
        updater = self.updater(github)
        updater.install()
        self.assertTrue(updater.status()["rollback_available"])
        result = updater.rollback()
        self.assertEqual(result["restored_version"], "0.5.0")
        self.assertEqual((self.repo / "VERSION").read_text(), "0.5.0\n")
        self.assertEqual((self.repo / "scripts" / "run.sh").read_text(), "old script\n")
        self.assertFalse((self.repo / "scripts" / "added.sh").exists())
        self.assertFalse(updater.status()["rollback_available"])
        with self.assertRaisesRegex(ValueError, "no previous version"):
            updater.rollback()

    def test_wsl_origin_mirror_receives_the_update(self):
        origin = Path(self.temp.name) / "windows-folder"
        (origin / "scripts").mkdir(parents=True)
        (origin / "VERSION").write_text("0.5.0\n")
        (origin / "scripts" / "run.sh").write_text("old script\n")
        (self.repo / ".wsl-origin").write_text(str(origin) + "\n")
        github = FakeGitHub("0.6.0", self.release_files())
        summary = self.updater(github).install()
        self.assertTrue(summary["wsl_origin_synced"])
        self.assertEqual((origin / "VERSION").read_text(), "0.6.0\n")
        self.assertEqual((origin / "scripts" / "run.sh").read_text(), "new script\n")

    def test_install_refuses_outside_a_guide_iei_folder(self):
        (self.repo / "VERSION").unlink()
        github = FakeGitHub("0.6.0", self.release_files())
        with self.assertRaisesRegex(ValueError, "VERSION is missing"):
            self.updater(github).install()

    def test_rollback_works_when_the_release_added_no_files(self):
        """The likeliest release shape — a bugfix touching only existing
        files — previously crashed rollback with 'empty manifest'."""
        github = FakeGitHub("0.6.0", self.release_files(
            **{"scripts/added.sh": None}
        ))
        updater = self.updater(github)
        updater.install()
        result = updater.rollback()
        self.assertEqual(result["restored_version"], "0.5.0")
        self.assertEqual((self.repo / "VERSION").read_text(), "0.5.0\n")
        self.assertEqual((self.repo / "scripts" / "run.sh").read_text(), "old script\n")
        self.assertFalse(updater.status()["rollback_available"])

    def test_rollback_keeps_config_edits_made_after_install(self):
        """The .new-review flow invites merging keys into the user config
        AFTER install; rollback must never revert those edits, and the
        stale .new file must not survive."""
        github = FakeGitHub("0.6.0", self.release_files())
        updater = self.updater(github)
        updater.install()
        config = self.repo / "config" / "annotation.config.yaml"
        config.write_text("# merged after review\nspliceai: 0.5\nnewkey: 1\n")
        updater.rollback()
        self.assertEqual(
            config.read_text(), "# merged after review\nspliceai: 0.5\nnewkey: 1\n"
        )
        self.assertFalse(config.with_name(config.name + ".new").exists())

    def test_rollback_of_a_dependency_change_writes_the_install_flag(self):
        github = FakeGitHub("0.6.0", self.release_files(
            **{"webui/package.json": b'{"new": true}\n'}
        ))
        updater = self.updater(github)
        summary = updater.install()
        self.assertTrue(summary["dependencies_changed"])
        (self.repo / "webui" / ".dependencies-updated").unlink()  # consumed
        (self.repo / "webui" / ".build-required").unlink()  # consumed
        result = updater.rollback()
        self.assertTrue(result["dependencies_changed"])
        self.assertTrue((self.repo / "webui" / ".dependencies-updated").is_file())
        self.assertTrue((self.repo / "webui" / ".build-required").is_file())

    def test_rollback_mirrors_to_the_wsl_origin(self):
        origin = Path(self.temp.name) / "windows-folder"
        (origin / "scripts").mkdir(parents=True)
        (origin / "VERSION").write_text("0.5.0\n")
        (origin / "scripts" / "run.sh").write_text("old script\n")
        (self.repo / ".wsl-origin").write_text(str(origin) + "\n")
        github = FakeGitHub("0.6.0", self.release_files())
        updater = self.updater(github)
        updater.install()
        self.assertEqual((origin / "VERSION").read_text(), "0.6.0\n")
        result = updater.rollback()
        self.assertTrue(result["wsl_origin_synced"])
        self.assertEqual((origin / "VERSION").read_text(), "0.5.0\n")
        self.assertEqual((origin / "scripts" / "run.sh").read_text(), "old script\n")
        self.assertFalse((origin / "scripts" / "added.sh").exists())

    def test_case_only_rename_does_not_delete_the_new_file(self):
        """On the case-insensitive filesystems clinicians run, deleting the
        old spelling of a case-only rename deletes the file the install
        just wrote."""
        first = FakeGitHub("0.6.0", self.release_files(
            **{"docs/Guide.md": b"guide v1\n"}
        ))
        self.updater(first).install()
        second = FakeGitHub("0.7.0", self.release_files(
            VERSION=b"0.7.0\n",
            **{"docs/Guide.md": None, "docs/guide.md": b"guide v2\n"},
        ))
        summary = self.updater(second).install()
        self.assertNotIn("docs/Guide.md", summary["files_removed"])
        # The manifest-named file must carry the new content. On a
        # case-insensitive filesystem (the clinician default) both
        # spellings are one file — the deletion skip is what keeps it
        # alive; on a case-sensitive one the old spelling lingers as a
        # harmless stale copy (the documented cost of the skip), so only
        # the new spelling's content is asserted.
        self.assertEqual(
            (self.repo / "docs" / "guide.md").read_text(), "guide v2\n"
        )

    def test_interrupted_install_is_detected_and_repairable(self):
        """A crash mid-swap can leave the tree claiming the NEW version;
        the sentinel keeps install available as a repair."""
        github = FakeGitHub("0.6.0", self.release_files())
        updater = self.updater(github)
        # Simulate the crash state: sentinel written, VERSION already new.
        updater.updates_dir.mkdir(parents=True, exist_ok=True)
        updater._sentinel.write_text("0.6.0\n")
        (self.repo / "VERSION").write_text("0.6.0\n")
        self.assertTrue(updater.status()["incomplete_update"])
        summary = updater.install()  # same version — allowed as repair
        self.assertTrue(summary["repaired"])
        self.assertEqual((self.repo / "scripts" / "run.sh").read_text(), "new script\n")
        self.assertFalse(updater.status()["incomplete_update"])

    def test_repair_preserves_the_original_rollback_snapshot(self):
        updater = self.updater(FakeGitHub("0.6.0", self.release_files()))
        real = updater._replace_file
        def interrupted(content, destination, mode=None):
            if destination == self.repo.resolve() / "scripts/added.sh":
                raise OSError("synthetic interrupted update")
            return real(content, destination, mode)
        with unittest.mock.patch.object(updater, "_replace_file", side_effect=interrupted):
            with self.assertRaisesRegex(OSError, "synthetic"):
                updater.install()
        self.assertEqual((self.repo / "scripts/run.sh").read_text(), "new script\n")
        updater.install()
        updater.rollback()
        self.assertEqual((self.repo / "VERSION").read_text(), "0.5.0\n")
        self.assertEqual((self.repo / "scripts/run.sh").read_text(), "old script\n")
        self.assertFalse((self.repo / "scripts/added.sh").exists())

    def test_incomplete_rollback_is_rejected_before_any_files_are_restored(self):
        updater = self.updater(FakeGitHub("0.6.0", self.release_files()))
        updater.install()
        (updater.rollback_dir / "files/scripts/run.sh").unlink()
        with self.assertRaisesRegex(ValueError, "snapshot.*incomplete"):
            updater.rollback()
        self.assertEqual((self.repo / "VERSION").read_text(), "0.6.0\n")
        self.assertEqual((self.repo / "scripts/run.sh").read_text(), "new script\n")

    def test_version_file_is_applied_last(self):
        """A crash before completion must leave the OLD version on disk so
        check() keeps offering the update."""
        from local_service import software_update as module
        github = FakeGitHub("0.6.0", self.release_files())
        updater = self.updater(github)
        real = module.SoftwareUpdater._replace_file
        def exploding(content, destination, mode=None):
            if destination.name == "run.sh":
                raise OSError("simulated crash")
            real(content, destination, mode)
        with unittest.mock.patch.object(
            module.SoftwareUpdater, "_replace_file", staticmethod(exploding)
        ):
            with self.assertRaises(OSError):
                updater.install()
        self.assertEqual((self.repo / "VERSION").read_text(), "0.5.0\n")
        self.assertTrue(updater.status()["incomplete_update"])

    def test_release_shipping_local_state_files_is_refused(self):
        files = self.release_files()
        files[".wsl-origin"] = str(Path(self.temp.name) / "victim").encode()
        github = FakeGitHub("0.6.0", files)
        with self.assertRaisesRegex(ValueError, "local machine state"):
            self.updater(github).install()
        self.assertEqual((self.repo / "VERSION").read_text(), "0.5.0\n")

    def test_directory_at_a_manifest_path_is_refused_before_any_change(self):
        (self.repo / "scripts" / "added.sh").mkdir()
        github = FakeGitHub("0.6.0", self.release_files())
        with self.assertRaisesRegex(ValueError, "folder sits where"):
            self.updater(github).install()
        self.assertEqual((self.repo / "VERSION").read_text(), "0.5.0\n")
        self.assertEqual((self.repo / "scripts" / "run.sh").read_text(), "old script\n")

    def test_restart_pending_lifecycle(self):
        github = FakeGitHub("0.6.0", self.release_files())
        updater = self.updater(github)
        self.assertFalse(updater.status()["restart_pending"])
        updater.install()
        self.assertTrue(updater.status()["restart_pending"])
        updater.clear_restart_pending()
        self.assertFalse(updater.status()["restart_pending"])

    def test_dropped_package_directories_are_pruned(self):
        github = FakeGitHub("0.6.0", self.release_files(
            **{"tools/legacy/old.py": b"module\n"}
        ))
        self.updater(github).install()
        second = FakeGitHub("0.7.0", self.release_files(
            VERSION=b"0.7.0\n", **{"tools/legacy/old.py": None}
        ))
        self.updater(second).install()
        self.assertFalse((self.repo / "tools").exists())

    def test_foreign_asset_hosts_are_refused(self):
        github = FakeGitHub("0.6.0", self.release_files())
        release = json.loads(github.release)
        release["assets"][0]["browser_download_url"] = "https://evil.example/x.zip"
        github.release = json.dumps(release).encode()
        with self.assertRaisesRegex(ValueError, "project's own release location"):
            self.updater(github).install()


if __name__ == "__main__":
    unittest.main()
