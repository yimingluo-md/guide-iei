#!/usr/bin/env python3
"""Guard resource preparation against concatenating a failed bgzip retry."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_large_resource_compression_uses_bgzip_managed_output_files():
    for relative in ("scripts/prepare_funcvep.sh", "scripts/prepare_promoterai.sh"):
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert 'hts bgzip -@ 2 -f "${PLAIN_SCORES}"' in source
        assert 'hts bgzip -@ 2 -c "${PLAIN_SCORES}" >' not in source


if __name__ == "__main__":
    tests = [test_large_resource_compression_uses_bgzip_managed_output_files]
    for test in tests:
        test()
        print(f"PASS  {test.__name__}")
    print(f"\n{len(tests)} tests passed")
