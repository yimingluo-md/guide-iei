#!/usr/bin/env python3
"""Integration test for CDS plus true exon-junction region construction."""
import gzip
import os
import pathlib
import subprocess
import tempfile


ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_utr_separated_splice_boundaries_are_retained(tmp_path):
    region_dir = tmp_path / "regions"
    region_dir.mkdir()
    bed = region_dir / "coding_splice.padded.bed.gz"
    raw = region_dir / "Homo_sapiens.GRCh38.113.gtf.gz"
    gtf = "\n".join([
        '1\ttest\texon\t100\t300\t.\t+\t.\tgene_id "G"; transcript_id "T"; gene_biotype "protein_coding";',
        '1\ttest\tCDS\t200\t300\t.\t+\t0\tgene_id "G"; transcript_id "T"; gene_biotype "protein_coding";',
        '1\ttest\texon\t400\t600\t.\t+\t.\tgene_id "G"; transcript_id "T"; gene_biotype "protein_coding";',
        '1\ttest\tCDS\t400\t500\t.\t+\t0\tgene_id "G"; transcript_id "T"; gene_biotype "protein_coding";',
    ]) + "\n"
    with gzip.open(raw, "wt") as fh:
        fh.write(gtf)

    config = tmp_path / "config.yaml"
    config.write_text(
        "container:\n  vep_image_tag: release_113.4\n"
        "reference:\n  assembly: GRCh38\n  fasta:\n    path: unused.fa.gz\n"
        f"region:\n  padding_bp: 8\n  bed: {bed}\n  custom_bed: ''\n"
    )

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "bgzip").write_text(
        "#!/usr/bin/env python3\nimport gzip,os,shutil,sys\n"
        "src=sys.argv[-1]\n"
        "with open(src,'rb') as i,gzip.open(src+'.gz','wb') as o: shutil.copyfileobj(i,o)\n"
        "os.unlink(src)\n"
    )
    (fake_bin / "tabix").write_text(
        "#!/usr/bin/env python3\nimport pathlib,sys\npathlib.Path(sys.argv[-1]+'.tbi').touch()\n"
    )
    os.chmod(fake_bin / "bgzip", 0o755)
    os.chmod(fake_bin / "tabix", 0o755)
    env = os.environ.copy()
    env["PATH"] = str(fake_bin) + os.pathsep + env["PATH"]
    subprocess.run(
        ["bash", str(ROOT / "scripts/build_coding_bed.sh"), str(config)],
        check=True,
        env=env,
        cwd=ROOT,
    )
    with gzip.open(bed, "rt") as fh:
        intervals = {tuple(line.rstrip().split("\t")) for line in fh}
    # These windows are around exon edges separated from CDS by 100 bp of UTR.
    assert ("1", "91", "107") in intervals
    assert ("1", "592", "608") in intervals


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as td:
        test_utr_separated_splice_boundaries_are_retained(pathlib.Path(td))
    print("PASS  test_utr_separated_splice_boundaries_are_retained")
