#!/usr/bin/env python3
"""Publication regressions independent of host HTS tools/container availability."""
import errno
import fcntl
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pipeline.publish_annotation import preflight_output, publish
EOF = bytes.fromhex("1f8b08040000000000ff0600424302001b0003000000000000000000")
SCRIPT = r'''
set -euo pipefail
source "$1"
hts() {
    local tool="$1"; shift
    local file="${@: -1}"
    case "$tool" in
        bgzip) cp "$FIXTURE" "$file.gz"; rm "$file" ;;
        tabix)
            printf '%s\n' "$file" >> "$INDEX_CALLS"
            printf 'new index' > "$file.tbi"
            if [[ "$MODE" == warning ]]; then
                echo '[W::bgzf_read_block] EOF marker is absent. The input is probably truncated' >&2
            elif [[ "$MODE" == error ]]; then
                return 2
            fi
            ;;
        *) return 3 ;;
    esac
}
publish_bgzf "$2/one.vcf" "$2/final.vcf.gz" vcf
if [[ "$MODE" == success ]]; then
    publish_bgzf "$2/two.vcf" "$2/final.vcf.gz" vcf
fi
'''


class IndexedPublicationTests(unittest.TestCase):
    def test_preflight_accepts_a_concurrent_publisher(self):
        with tempfile.TemporaryDirectory() as directory:
            descriptor = os.open(directory, os.O_RDONLY)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                preflight_output(Path(directory) / "result.vcf")
            finally:
                os.close(descriptor)

    def test_unsupported_lock_fails_on_both_entry_paths_before_work(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output_dir = root / "output directory"
            output_dir.mkdir()
            output = output_dir / "result.vcf.gz"
            existing = {output: b"previous data", Path(str(output) + ".tbi"): b"index",
                        Path(str(output) + ".deliverable"): b"pointer"}
            for path, data in existing.items():
                path.write_bytes(data)
            fixture = root / "input.vcf"
            fixture.write_text("##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n")
            shim = root / "sitecustomize.py"
            shim.write_text(
                "import errno, fcntl\n"
                "def flock(fd, operation):\n"
                "    raise OSError(errno.EOPNOTSUPP, 'locking unsupported')\n"
                "fcntl.flock = flock\n"
            )
            env = dict(os.environ, PYTHONPATH=str(root),
                       PATH=str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"])
            config = ROOT / "test/test.config.yaml"
            commands = [
                ["bash", str(ROOT / "scripts/preflight.sh"), str(config), str(fixture), str(output)],
                ["bash", str(ROOT / "scripts/run_annotation.sh"), "-c", str(config),
                 "-i", str(fixture), "-o", str(output)],
            ]
            for command in commands:
                with self.subTest(command=command[1]):
                    result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=15)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("Cannot lock annotation output directory", result.stderr)
                    self.assertIn(str(output_dir), result.stderr)
                    self.assertIn("Choose an output location", result.stderr)
                    self.assertEqual({p: p.read_bytes() for p in existing}, existing)
                    self.assertEqual(set(output_dir.iterdir()), set(existing))

    def test_preflight_reports_unsupported_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "result.vcf"
            with patch("pipeline.publish_annotation.fcntl.flock",
                       side_effect=OSError(errno.EOPNOTSUPP, "locking unsupported")):
                with self.assertRaisesRegex(ValueError, "filesystem that supports directory locking"):
                    preflight_output(output)

    def test_alias_copy_fallback_and_rollback_after_data_replacement(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stage = root / "stage"
            stage.mkdir()
            base = root / "result.vcf.gz"
            legacy = root / "result.aamatch.vcf.gz"
            final = stage / legacy.name
            pointer = Path(str(base) + ".deliverable")
            original = {pointer: b"previous pointer\n"}
            for path in (base, legacy):
                for suffix in ("", ".tbi", ".csi"):
                    dest = Path(str(path) + suffix)
                    original[dest] = dest.name.encode()
            for path, content in original.items():
                path.write_bytes(content)
            replace = os.replace

            def fail_once(source, target):
                if target == pointer and source.name == "pointer":
                    raise OSError("injected pointer replacement failure")
                replace(source, target)

            with patch("os.link", side_effect=OSError("no hard links")):
                final.write_bytes(b"new data")
                Path(str(final) + ".tbi").write_bytes(b"new index")
                with patch("os.replace", side_effect=fail_once), self.assertRaisesRegex(OSError, "injected"):
                    publish(final, base)
                self.assertEqual({p: p.read_bytes() for p in original}, original)
                final.write_bytes(b"new data")
                Path(str(final) + ".tbi").write_bytes(b"new index")
                publish(final, base)
            for path in (base, legacy):
                self.assertEqual(path.read_bytes(), b"new data")
                self.assertEqual(Path(str(path) + ".tbi").read_bytes(), b"new index")
                self.assertFalse(Path(str(path) + ".csi").exists())
            self.assertEqual(pointer.read_text(), legacy.name + "\n")

    def run_case(self, mode):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "fixture.gz").write_bytes(b"synthetic compressed blocks" + EOF)
        (root / "one.vcf").write_text("first synthetic input\n")
        (root / "two.vcf").write_text("second synthetic input\n")
        (root / "final.vcf.gz").write_bytes(b"previous complete file" + EOF)
        (root / "final.vcf.gz.tbi").write_text("previous index")
        env = dict(os.environ, FIXTURE=str(root / "fixture.gz"), MODE=mode,
                   INDEX_CALLS=str(root / "calls"))
        result = subprocess.run(["bash", "-c", SCRIPT, "test", str(ROOT / "scripts/lib.sh"), str(root)],
                                env=env, capture_output=True, text=True, timeout=15)
        return root, result

    def test_repeated_replacements_index_distinct_temporary_names_before_publication(self):
        root, result = self.run_case("success")
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = (root / "calls").read_text().splitlines()
        self.assertEqual(len(set(calls)), 2)
        self.assertTrue(all(name.startswith(".final.vcf.gz.publish.") for name in calls))
        self.assertEqual((root / "final.vcf.gz.tbi").read_text(), "new index")
        self.assertFalse(list(root.glob(".*.publish.*")))

    def test_zero_exit_truncation_warning_and_nonzero_failure_preserve_prior_pair(self):
        for mode in ("warning", "error"):
            with self.subTest(mode=mode):
                root, result = self.run_case(mode)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual((root / "final.vcf.gz").read_bytes(), b"previous complete file" + EOF)
                self.assertEqual((root / "final.vcf.gz.tbi").read_text(), "previous index")
                self.assertFalse(list(root.glob(".*.publish.*")))
                if mode == "warning":
                    self.assertIn("not publishing its index", result.stderr)

    def test_all_annotation_replacement_passes_use_index_before_promotion(self):
        runner = (ROOT / "scripts/run_annotation.sh").read_text()
        for plain, final in (("PTC_TMP", "OUTPUT"), ("HAPLO_TMP", "OUTPUT"),
                             ("MATCH_INPUT", "FINAL"), ("CLINGEN_TMP", "FINAL_OUTPUT"),
                             ("GENIA_TMP", "FINAL_OUTPUT")):
            self.assertIn(f'publish_bgzf "${plain}" "${final}" vcf', runner)
            self.assertNotIn(f'hts bgzip -f "${plain}"', runner)


if __name__ == "__main__":
    unittest.main()
