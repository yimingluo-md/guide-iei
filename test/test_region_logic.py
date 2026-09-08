#!/usr/bin/env python3
"""Integration test for CDS plus true exon-junction region construction."""
import gzip
import os
import pathlib
import subprocess
import tempfile


ROOT = pathlib.Path(__file__).resolve().parents[1]
BGZF_EOF = bytes.fromhex("1f8b08040000000000ff0600424302001b0003000000000000000000")

GTF_LINES = [
    '1\ttest\texon\t100\t300\t.\t+\t.\tgene_id "G"; transcript_id "T"; gene_biotype "protein_coding";',
    '1\ttest\tCDS\t200\t300\t.\t+\t0\tgene_id "G"; transcript_id "T"; gene_biotype "protein_coding";',
    '1\ttest\texon\t400\t600\t.\t+\t.\tgene_id "G"; transcript_id "T"; gene_biotype "protein_coding";',
    '1\ttest\tCDS\t400\t500\t.\t+\t0\tgene_id "G"; transcript_id "T"; gene_biotype "protein_coding";',
]


def _fake_hts_bin(tmp_path, *, complete=True):
    """bgzip/tabix stand-ins.

    The fake bgzip writes a gzip member and, like the real tool, terminates the
    stream with the 28-byte BGZF EOF block (omitted when ``complete`` is
    False, which mimics a bgzip interrupted at a block boundary).
    """
    fake_bin = tmp_path / ("bin-complete" if complete else "bin-truncated")
    fake_bin.mkdir(parents=True)
    eof = BGZF_EOF.hex() if complete else ""
    (fake_bin / "bgzip").write_text(
        "#!/usr/bin/env python3\nimport gzip,os,shutil,sys\n"
        "src=sys.argv[-1]\n"
        "with open(src,'rb') as i,gzip.open(src+'.gz','wb') as o: shutil.copyfileobj(i,o)\n"
        f"open(src+'.gz','ab').write(bytes.fromhex('{eof}'))\n"
        "os.unlink(src)\n"
    )
    (fake_bin / "tabix").write_text(
        "#!/usr/bin/env python3\nimport pathlib,sys\npathlib.Path(sys.argv[-1]+'.tbi').write_bytes(b'TBI')\n"
    )
    os.chmod(fake_bin / "bgzip", 0o755)
    os.chmod(fake_bin / "tabix", 0o755)
    return fake_bin


def _prepare(tmp_path, *, required_contigs="1", gtf_lines=GTF_LINES):
    region_dir = tmp_path / "regions"
    region_dir.mkdir(exist_ok=True)
    bed = region_dir / "coding_splice.padded.bed.gz"
    raw = region_dir / "Homo_sapiens.GRCh38.113.gtf.gz"
    with gzip.open(raw, "wt") as fh:
        fh.write("\n".join(gtf_lines) + "\n")
    config = tmp_path / "config.yaml"
    config.write_text(
        "container:\n  vep_image_tag: release_113.4\n"
        "reference:\n  assembly: GRCh38\n  fasta:\n    path: unused.fa.gz\n"
        f"region:\n  padding_bp: 8\n  bed: {bed}\n  custom_bed: ''\n"
        f"  required_contigs: '{required_contigs}'\n"
    )
    return bed, config


def _build(config, fake_bin, *extra):
    env = os.environ.copy()
    env["PATH"] = str(fake_bin) + os.pathsep + env["PATH"]
    return subprocess.run(
        ["bash", str(ROOT / "scripts/build_coding_bed.sh"), str(config), *extra],
        env=env,
        cwd=ROOT,
        capture_output=True,
        text=True,
    )


def test_utr_separated_splice_boundaries_are_retained(tmp_path):
    bed, config = _prepare(tmp_path)
    result = _build(config, _fake_hts_bin(tmp_path))
    assert result.returncode == 0, result.stderr
    with gzip.open(bed, "rt") as fh:
        intervals = {tuple(line.rstrip().split("\t")) for line in fh}
    # These windows are around exon edges separated from CDS by 100 bp of UTR.
    assert ("1", "91", "107") in intervals
    assert ("1", "592", "608") in intervals
    # Published atomically: the final file carries the BGZF EOF marker, its
    # index sits beside it, and no publish temp is left behind.
    assert bed.read_bytes().endswith(BGZF_EOF)
    assert (bed.parent / (bed.name + ".tbi")).exists()
    assert not [p for p in bed.parent.iterdir() if ".publish." in p.name]


def test_bed_missing_a_required_contig_is_not_published(tmp_path):
    # The GTF covers contig 1 only; requiring 1 and 2 must abort before the
    # final name exists (audit M12: a truncated GTF otherwise yields a region
    # file that silently excludes whole chromosomes from every run).
    bed, config = _prepare(tmp_path, required_contigs="1 2")
    result = _build(config, _fake_hts_bin(tmp_path))
    assert result.returncode != 0
    assert "would lack contig(s): 2" in result.stderr, result.stderr
    assert not bed.exists()
    assert not (bed.parent / (bed.name + ".tbi")).exists()


def test_incomplete_bgzf_output_is_not_published(tmp_path):
    # A bgzip that stops at a block boundary yields a valid gzip with no EOF
    # marker; it must never be renamed onto the final path.
    bed, config = _prepare(tmp_path)
    result = _build(config, _fake_hts_bin(tmp_path, complete=False))
    assert result.returncode != 0
    assert "incomplete BGZF stream" in result.stderr, result.stderr
    assert not bed.exists()
    assert not [p for p in bed.parent.iterdir() if ".publish." in p.name]


def test_incomplete_published_bed_is_rebuilt(tmp_path):
    # An earlier interrupted publish left a marker-less file under the final
    # name: a bare -s test kept it forever; the builder must now rebuild it.
    bed, config = _prepare(tmp_path)
    plain = gzip.compress(b"1\t0\t10\n")
    bed.write_bytes(plain)
    (bed.parent / (bed.name + ".tbi")).touch()
    result = _build(config, _fake_hts_bin(tmp_path))
    assert result.returncode == 0, result.stderr
    assert "present but incomplete" in result.stderr, result.stderr
    assert bed.read_bytes().endswith(BGZF_EOF)
    with gzip.open(bed, "rt") as fh:
        assert ("1", "91", "107") in {tuple(line.rstrip().split("\t")) for line in fh}
    # A complete file is left alone.
    again = _build(config, _fake_hts_bin(tmp_path / "again"))
    assert again.returncode == 0, again.stderr
    assert "already present" in again.stderr, again.stderr


if __name__ == "__main__":
    tests = [
        test_utr_separated_splice_boundaries_are_retained,
        test_bed_missing_a_required_contig_is_not_published,
        test_incomplete_bgzf_output_is_not_published,
        test_incomplete_published_bed_is_rebuilt,
    ]
    for test in tests:
        with tempfile.TemporaryDirectory() as td:
            test(pathlib.Path(td))
        print(f"PASS  {test.__name__}")
