#!/usr/bin/env python3
"""Publication regressions independent of host HTS tools/container availability."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
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
