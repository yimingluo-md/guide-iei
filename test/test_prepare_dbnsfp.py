"""Exercise the local dbNSFP installer with tiny chromosome shards and real HTS tools."""
import gzip
import hashlib
import os
from pathlib import Path
import shutil
import subprocess

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "prepare_dbnsfp.sh"
pytestmark = pytest.mark.skipif(
    not all(shutil.which(tool) for tool in ("bgzip", "tabix")), reason="requires bgzip and tabix"
)
HEADER = "#chr\tpos(1-based)\thg38_chr\thg38_pos(1-based)"


def install(source, output, consume=False, *, env=None):
    config = output.parent / "test.yaml"
    config.write_text(f"plugins:\n  dbNSFP:\n    path: '{output}'\n")
    args = ["bash", str(SCRIPT), str(source), str(config)]
    if consume:
        args.append("--remove-source-after-success")
    return subprocess.run(args, cwd=output.parent, env={**os.environ, "HTS_VIA_CONTAINER": "0", **(env or {})},
                          text=True, capture_output=True, timeout=30)


def prebuilt(output):
    plain = output.with_suffix("")
    plain.write_text("#chr\tpos(1-based)\n1\t42\n")
    subprocess.run(["bgzip", "-f", str(plain)], check=True)
    subprocess.run(["tabix", "-s1", "-b2", "-e2", str(output)], check=True)


def shard(folder, number, *rows):
    path = folder / f"dbNSFP4_variant.chr{number}.gz"
    with gzip.open(path, "wt") as handle:
        handle.write("\n".join((HEADER, *rows)) + "\n")
    return path


def shim(folder, name, body):
    path = folder / name
    path.write_text("#!/bin/bash\nset -eu\n" + body)
    path.chmod(0o755)


@pytest.mark.parametrize("alias_kind", ["relative", "symlink", "hardlink", "directory_symlink"])
@pytest.mark.parametrize("consume", [False, True])
def test_prebuilt_aliases_preserve_data_and_sidecars(tmp_path, alias_kind, consume):
    output = tmp_path / "dbNSFP5_grch38.gz"
    prebuilt(output)
    index = Path(str(output) + ".tbi")
    digest = Path(str(output) + ".md5")
    digest.write_bytes(hashlib.md5(output.read_bytes()).hexdigest().upper().encode() + b"\r\n")
    if alias_kind == "relative":
        source = "./" + output.name
    elif alias_kind == "directory_symlink":
        folder = tmp_path / "alias"
        folder.symlink_to(tmp_path, target_is_directory=True)
        source = folder / output.name
    else:
        source = tmp_path / "another-name.gz"
        if alias_kind == "symlink":
            source.symlink_to(output)
        else:
            os.link(output, source)
        # An independent data alias can have its own sibling index.
        index.rename(str(source) + ".tbi")
    data_id = output.stat().st_ino
    result = install(source, output, consume)
    assert result.returncode == 0, result.stderr
    assert "MD5 verified" in result.stderr
    assert output.stat().st_ino == data_id and digest.is_file()
    assert os.path.samefile(tmp_path / source, output)
    assert subprocess.check_output(["tabix", str(output), "1:42-42"]) == b"1\t42\n"


@pytest.mark.parametrize("sidecar", [b"", b"invalid\r\n", b"0" * 32 + b"\r\n"])
def test_prebuilt_rejects_invalid_or_mismatched_sidecar(tmp_path, sidecar):
    output = tmp_path / "dbNSFP5_grch38.gz"
    prebuilt(output)
    Path(str(output) + ".md5").write_bytes(sidecar)
    result = install(output, output, consume=True)
    assert result.returncode != 0 and "MD5" in result.stderr
    assert output.is_file() and Path(str(output) + ".tbi").is_file()


def test_legacy_sorts_placed_rows_on_destination_volume(tmp_path):
    source = tmp_path / "chromosomes"
    source.mkdir()
    dest = tmp_path / "external volume"
    dest.mkdir()
    output = dest / "dbNSFP4_grch38.gz"
    prebuilt(output)
    Path(str(output) + ".csi").write_bytes(b"old CSI")
    Path(str(output) + ".md5").write_bytes(b"previous digest")
    one = shard(source, 1, "1\t1\tX\t43", "1\t2\t.\t7", "1\t3\t10\t4000000", "1\t4\t2\t.")
    two = shard(source, 2, "2\t1\t10\t3000000", "2\t2\t2\t1", "2\t3\t1\t0")
    shim(tmp_path, "sort", '[[ "$LC_ALL" == C && "$1" == -T && -d "$2" && "${2%/*}" == "$DEST" ]]\n'
                          'exec "$REAL_SORT" "$@"\n')
    env = {"PATH": f"{tmp_path}:{os.environ['PATH']}", "REAL_SORT": shutil.which("sort"), "DEST": str(dest)}
    result = install(source, output, consume=True, env=env)
    assert result.returncode == 0, result.stderr
    assert gzip.decompress(output.read_bytes()).decode().splitlines() == [
        HEADER, "2\t1\t10\t3000000", "1\t3\t10\t4000000", "2\t2\t2\t1", "1\t1\tX\t43",
    ]
    assert subprocess.check_output(["tabix", str(output), "X:43-43"]) == b"1\t1\tX\t43\n"
    assert not any(p.exists() for p in (one, two, Path(str(output) + ".csi"), Path(str(output) + ".md5")))
    assert not list(dest.glob(".dbnsfp.*"))


@pytest.mark.parametrize("failure", ["unplaced", "truncated_shard", "short_bgzf", "index_error",
                                     "index_warning", "empty_query", "publish_error"])
def test_failed_legacy_replacement_preserves_inputs_and_installed_pair(tmp_path, failure):
    source = tmp_path / "chromosomes"
    source.mkdir()
    one = shard(source, 1, "1\t1\t.\t.")
    two = shard(source, 2, "2\t2\t2\t" + ("." if failure == "unplaced" else "42"))
    if failure == "truncated_shard":
        one.write_bytes(one.read_bytes()[:-3])  # an early shard, followed by a good one
    output = tmp_path / "dbNSFP4_grch38.gz"
    prebuilt(output)
    index = Path(str(output) + ".tbi")
    csi = Path(str(output) + ".csi")
    csi.write_bytes(b"prior CSI")
    digest = Path(str(output) + ".md5")
    digest.write_bytes(b"prior MD5")
    original = {p: p.read_bytes() for p in (one, two, output, index, csi, digest)}
    if failure == "short_bgzf":
        shim(tmp_path, "bgzip", 'echo bad > table.gz\n')
    elif failure.startswith("index_"):
        shim(tmp_path, "tabix", 'echo partial > "${@: -1}.tbi"\n'
             + ("exit 1\n" if failure == "index_error" else "echo '[E::index] bad input' >&2\n"))
    elif failure == "empty_query":
        shim(tmp_path, "tabix", 'if [[ "$1" == -f ]]; then exec "$REAL_TABIX" "$@"; fi\n')
    elif failure == "publish_error":
        # Successful query, then inject a failed rename of the new TBI.
        shim(tmp_path, "tabix", '"$REAL_TABIX" "$@"\nif [[ "$1" != -f ]]; then rm "$1.tbi"; fi\n')
    env = {"PATH": f"{tmp_path}:{os.environ['PATH']}", "REAL_TABIX": shutil.which("tabix")}
    result = install(source, output, consume=True, env=env)
    assert result.returncode != 0
    if failure == "publish_error":
        assert "FileNotFoundError" in result.stderr and "table.gz.tbi" in result.stderr
    assert {p: p.read_bytes() for p in original} == original
    assert not list(tmp_path.glob(".dbnsfp.*"))
