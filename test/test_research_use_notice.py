#!/usr/bin/env python3
"""Audit H10: the research-use notice must travel with every output artefact."""
import gzip
import json
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipeline"))

from research_use_notice import RESEARCH_USE_NOTICE, VCF_HEADER_KEY, is_notice_header, vcf_header_line  # noqa: E402


def test_notice_text_is_shared_with_the_workbench_and_the_readme():
    assert RESEARCH_USE_NOTICE.startswith("Research use only.")
    workbench = (ROOT / "webui/app/VariantWorkbench.tsx").read_text(encoding="utf-8")
    assert f'const RESEARCH_USE_NOTICE = "{RESEARCH_USE_NOTICE}";' in workbench, (
        "webui/app/VariantWorkbench.tsx RESEARCH_USE_NOTICE must equal pipeline/research_use_notice.py"
    )
    # Both TSV exports carry the notice as their first row.
    assert workbench.count("researchUseNoticeRow(), headers.join") == 2
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "- **Not a cloud service.**" in readme
    assert "- **Not an automated classification or reporting system.**" in readme
    assert "- **A cloud service.**" not in readme


def test_vcf_header_line_round_trips():
    line = vcf_header_line()
    assert line.startswith(f"##{VCF_HEADER_KEY}=") and line.endswith("\n")
    assert is_notice_header(line)
    assert not is_notice_header("##INFO=<ID=CSQ,Number=.,Type=String,Description=\"x\">")
    printed = subprocess.run(
        [sys.executable, str(ROOT / "pipeline/research_use_notice.py"), "--vcf-header"],
        capture_output=True, text=True, check=True,
    ).stdout
    assert printed == line


def test_protein_match_step_writes_the_notice_once():
    sys.path.insert(0, str(ROOT / "test"))
    import test_aa_match as harness
    with tempfile.TemporaryDirectory() as directory:
        tmp = pathlib.Path(directory)
        catalog = tmp / "catalog.tsv"
        catalog.write_text("BRCA1\t100\tR\tH\tENST0\tRCV1\t1:999:C:T\tPathogenic\t\n")
        loaded = harness.aam.load_reference(str(catalog))
        vin = str(tmp / "in.vcf"); vout = str(tmp / "out.vcf"); vout2 = str(tmp / "out2.vcf")
        harness._write(vin, harness._vcf([harness._csq("missense_variant", "BRCA1", "100")]))
        harness.aam.annotate(vin, vout, loaded)
        # Idempotent: a second pass over annotated output keeps ONE notice line.
        harness.aam.annotate(vout, vout2, loaded)
        for path in (vout, vout2):
            lines = pathlib.Path(path).read_text().splitlines()
            notice_lines = [line for line in lines if is_notice_header(line)]
            assert notice_lines == [vcf_header_line().rstrip("\n")], path
            assert lines.index(notice_lines[0]) < next(i for i, l in enumerate(lines) if l.startswith("#CHROM"))


def test_qc_report_and_manifest_carry_the_notice():
    sys.path.insert(0, str(ROOT / "test"))
    from annotation_qc import render_html  # noqa: E402
    report = {
        "metrics": [], "summary": {"records": 0, "pass_records": 0, "samples": []},
        "overall_status": "PASS", "details": {
            "promoterAI": {"status": "x"}, "logofunc": {"exact_transcript_protein_match_records": 0},
            "funcvep": {"complete_score_records": 0},
        }, "input_vcf": "/x.vcf", "created_utc": "now",
        "research_use_notice": RESEARCH_USE_NOTICE,
    }
    page = render_html(report)
    assert RESEARCH_USE_NOTICE in page
    manifest_source = (ROOT / "pipeline/write_run_manifest.py").read_text()
    assert '"research_use_notice": RESEARCH_USE_NOTICE' in manifest_source
    qc_source = (ROOT / "pipeline/annotation_qc.py").read_text()
    assert '"research_use_notice": RESEARCH_USE_NOTICE' in qc_source
    runner = (ROOT / "scripts/run_annotation.sh").read_text()
    assert "research_use_notice.py" in runner and "GUIDE_IEI_notice" in runner


if __name__ == "__main__":
    names = sorted(name for name in globals() if name.startswith("test_"))
    for name in names:
        globals()[name]()
        print(f"PASS  {name}")
    print(f"{len(names)} tests passed")
