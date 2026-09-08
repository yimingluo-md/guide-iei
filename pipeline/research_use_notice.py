"""The research-use notice that travels with every artefact GUIDE-IEI produces.

The workbench shows this notice on screen, but a VCF, QC report, run manifest
or TSV that leaves the workstation used to carry nothing (audit H10). Every
writer imports the same text from here so the wording cannot drift.
"""
from __future__ import annotations

RESEARCH_USE_NOTICE = (
    "Research use only. GUIDE-IEI organizes evidence but does not classify "
    "variants or generate diagnostic reports. Confirm clinically actionable "
    "findings in a certified clinical laboratory before patient care."
)

# VCF meta-information key: ##GUIDE_IEI_notice=<text>
VCF_HEADER_KEY = "GUIDE_IEI_notice"


def vcf_header_line() -> str:
    """The ``##GUIDE_IEI_notice=...`` line, newline-terminated."""
    return f"##{VCF_HEADER_KEY}={RESEARCH_USE_NOTICE}\n"


def is_notice_header(line: str) -> bool:
    return line.startswith(f"##{VCF_HEADER_KEY}=")


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--vcf-header", action="store_true",
        help="print the VCF meta-information line instead of the bare text",
    )
    args = parser.parse_args()
    print(vcf_header_line() if args.vcf_header else RESEARCH_USE_NOTICE, end="" if args.vcf_header else "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
