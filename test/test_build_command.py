#!/usr/bin/env python3
"""Unit tests for build_vep_command.py — run with `python -m pytest` or directly."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "pipeline"))
from build_vep_command import build_vep_command  # noqa: E402


def _touch(d, name):
    p = os.path.join(d, name)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "w").close()
    return p


def _full_cfg(root):
    """A config with every source enabled and all files present under `root`."""
    j = lambda *p: os.path.join(root, *p)
    for rel in [
        "vep_cache/.ok", "fasta/genome.fa.gz",
        "dbnsfp/dbNSFP5.1a_grch38.gz",
        "loftee/human_ancestor.fa.gz", "loftee/loftee.sql",
        "loftee/gerp.bw", "spliceai/snv.vcf.gz", "spliceai/indel.vcf.gz",
        "custom/rm.bed.gz", "custom/segdup.bed.gz", "custom/promoter.vcf.gz",
        "clinvar/clinvar.vcf.gz", "custom/logofunc.vcf.gz",
    ]:
        _touch(root, rel)
    return {
        "reference": {"species": "homo_sapiens", "assembly": "GRCh38",
                      "vep_cache_dir": j("vep_cache"),
                      "fasta": {"enabled": True, "required": True, "path": j("fasta/genome.fa.gz")}},
        "run": {"fork": 8, "buffer_size": 5000, "force_overwrite": True},
        "output": {"format": "vcf", "compress": "bgzip", "vep_stats": True},
        "core": {"pick": True, "pick_flag": "--pick", "symbol": True, "hgvs": True,
                 "biotype": True, "sift": "p", "polyphen": "p",
                 "af_gnomade": True, "af_gnomadg": True},
        "plugins": {
            "dbNSFP": {"enabled": True, "path": j("dbnsfp/dbNSFP5.1a_grch38.gz"),
                       "columns": ["CADD_phred", "REVEL_score", "AlphaMissense_score",
                                   "SIFT_pred", "Polyphen2_HDIV_pred"]},
            "LoF": {"enabled": True, "loftee_path": "auto",
                    "human_ancestor_fa": j("loftee/human_ancestor.fa.gz"),
                    "conservation_file": j("loftee/loftee.sql"),
                    "gerp_bigwig": j("loftee/gerp.bw")},
            "SpliceAI": {"enabled": True, "snv": j("spliceai/snv.vcf.gz"),
                         "indel": j("spliceai/indel.vcf.gz")},
        },
        "custom_tracks": {
            "RepeatMasker": {"enabled": True, "file": j("custom/rm.bed.gz"),
                             "short_name": "RepeatMasker", "format": "bed"},
            "SegDup": {"enabled": True, "file": j("custom/segdup.bed.gz"),
                       "short_name": "SegDup", "format": "bed"},
            "promoterAI": {"enabled": True, "file": j("custom/promoter.vcf.gz"),
                           "short_name": "promoterAI", "format": "vcf",
                           "type": "exact", "fields": ["promoterAI"]},
            "ClinVar": {"enabled": True, "file": j("clinvar/clinvar.vcf.gz"),
                        "short_name": "ClinVar", "format": "vcf", "type": "exact",
                        "coords": 0, "fields": ["CLNSIG", "CLNSIGCONF", "CLNREVSTAT", "CLNDN"]},
            "LoGoFunc": {"enabled": True, "file": j("custom/logofunc.vcf.gz"),
                         "short_name": "LoGoFunc", "format": "vcf", "type": "exact",
                         "coords": 0, "fields": ["LoGoFunc_GOF", "LoGoFunc_LOF"]},
        },
    }


def test_full_stack_native(tmp_path):
    """Every source present -> every flag emitted, no warnings/errors."""
    cfg = _full_cfg(str(tmp_path))
    plan = build_vep_command(cfg, "in.vcf.gz", "out.vcf.gz", container=False)
    s = plan.command_string()
    assert not plan.errors, plan.errors
    assert not plan.warnings, plan.warnings
    # output format preserves zygosity
    assert "--vcf" in plan.argv and "--tab" not in plan.argv
    assert "--compress_output" in plan.argv
    # core
    for flag in ["--offline", "--cache", "--pick", "--symbol", "--hgvs",
                 "--af_gnomade", "--af_gnomadg"]:
        assert flag in plan.argv, flag
    assert "--sift" in plan.argv and "p" in plan.argv
    # plugins — dbNSFP with file + selected columns
    dbnsfp = [a for a in plan.argv if a.startswith("dbNSFP,")]
    assert dbnsfp, "dbNSFP plugin emitted"
    assert "CADD_phred" in dbnsfp[0] and "REVEL_score" in dbnsfp[0] and "AlphaMissense_score" in dbnsfp[0]
    assert any(a.startswith("LoF,loftee_path:$LOFTEE_DIR") for a in plan.argv)
    assert any(a.startswith("SpliceAI,snv=") and "indel=" in a for a in plan.argv)
    # custom tracks — all 5
    customs = [plan.argv[i + 1] for i, a in enumerate(plan.argv) if a == "--custom"]
    assert len(customs) == 5, customs
    clinvar = [c for c in customs if "short_name=ClinVar" in c][0]
    assert "fields=CLNSIG%CLNSIGCONF%CLNREVSTAT%CLNDN" in clinvar
    assert "type=exact" in clinvar and "coords=0" in clinvar
    return s


def test_core_only(tmp_path):
    """All plugins/customs disabled -> only core flags, no plugin/custom args."""
    cfg = _full_cfg(str(tmp_path))
    for p in cfg["plugins"].values():
        p["enabled"] = False
    for c in cfg["custom_tracks"].values():
        c["enabled"] = False
    plan = build_vep_command(cfg, "in.vcf", "out.vcf", container=False)
    assert "--plugin" not in plan.argv
    assert "--custom" not in plan.argv
    assert "--pick" in plan.argv and "--vcf" in plan.argv
    assert not plan.errors


def test_missing_file_skipped(tmp_path):
    """A missing optional file is skipped with a warning, run still valid."""
    cfg = _full_cfg(str(tmp_path))
    os.remove(cfg["plugins"]["dbNSFP"]["path"])       # delete dbNSFP
    os.remove(cfg["custom_tracks"]["LoGoFunc"]["file"])  # delete LoGoFunc
    plan = build_vep_command(cfg, "in.vcf", "out.vcf", container=False)
    assert not plan.errors
    assert any("dbNSFP" in w for w in plan.warnings)
    assert any("LoGoFunc" in w for w in plan.warnings)
    assert not any(a.startswith("dbNSFP,") for a in plan.argv)
    customs = [plan.argv[i + 1] for i, a in enumerate(plan.argv) if a == "--custom"]
    assert not any("LoGoFunc" in c for c in customs)
    assert len(customs) == 4  # 5 - 1 skipped


def test_required_missing_errors(tmp_path):
    """A missing REQUIRED file becomes an error (would abort the run)."""
    cfg = _full_cfg(str(tmp_path))
    os.remove(cfg["reference"]["fasta"]["path"])  # fasta is required:true
    plan = build_vep_command(cfg, "in.vcf", "out.vcf", container=False)
    assert any("fasta" in e for e in plan.errors)


def test_container_mounts(tmp_path):
    """Container mode maps ref paths under /refs and records bind-mounts."""
    cfg = _full_cfg(str(tmp_path))
    ind = str(tmp_path / "inp"); outd = str(tmp_path / "outp")
    os.makedirs(ind, exist_ok=True); os.makedirs(outd, exist_ok=True)
    plan = build_vep_command(cfg, os.path.join(ind, "in.vcf.gz"),
                             os.path.join(outd, "out.vcf.gz"), container=True)
    assert not plan.errors
    # all absolute args now point under /refs/, /work_in, /work_out
    for a in plan.argv:
        if a.startswith("/"):
            assert a.startswith(("/refs/", "/work_in", "/work_out")), a
    containers = {m.container for m in plan.mounts}
    assert "/work_in" in containers
    assert "/work_out" in containers
    # reference dirs mounted read-only; work_out mounted read-write
    assert any(m.container.startswith("/refs/") and m.mode == "ro" for m in plan.mounts)
    assert any(m.container == "/work_out" and m.mode == "rw" for m in plan.mounts)


def test_shared_io_dir_single_rw_mount(tmp_path):
    """When input and output share a dir, one rw /work_in mount covers both."""
    cfg = _full_cfg(str(tmp_path))
    d = str(tmp_path / "io")
    os.makedirs(d, exist_ok=True)
    plan = build_vep_command(cfg, os.path.join(d, "in.vcf.gz"),
                             os.path.join(d, "out.vcf.gz"), container=True)
    io_mounts = [m for m in plan.mounts if m.container in ("/work_in", "/work_out")]
    assert len(io_mounts) == 1
    assert io_mounts[0].container == "/work_in" and io_mounts[0].mode == "rw"
    # both -i and -o resolve under /work_in
    i_idx = plan.argv.index("-i"); o_idx = plan.argv.index("-o")
    assert plan.argv[i_idx + 1].startswith("/work_in/")
    assert plan.argv[o_idx + 1].startswith("/work_in/")


if __name__ == "__main__":
    import tempfile
    passed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            with tempfile.TemporaryDirectory() as td:
                fn(__import__("pathlib").Path(td))
            print(f"PASS  {name}")
            passed += 1
    print(f"\n{passed} tests passed")
