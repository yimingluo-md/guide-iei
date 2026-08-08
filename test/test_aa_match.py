#!/usr/bin/env python3
"""Unit tests for the ClinVar amino-acid-match post-processing (VCF)."""
import gzip
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "pipeline"))
import clinvar_aa_match as aam          # noqa: E402
import reduce_vep_to_aa_reference as red  # noqa: E402


CSQ_FORMAT = ("Allele|Consequence|SYMBOL|Gene|Feature|BIOTYPE|"
              "Protein_position|Amino_acids|SIFT|CADD_PHRED")


def _csq(cons, symbol, protpos, allele="A"):
    # positional per CSQ_FORMAT
    vals = [allele, cons, symbol, "ENSG0", "ENST0", "protein_coding",
            protpos, "R/H", "deleterious", "25"]
    return "|".join(vals)


def _vcf(records, sample=True):
    """Build a minimal VEP-style VCF text. `records` = list of (info_csq_str,)."""
    h = [
        "##fileformat=VCFv4.2",
        '##INFO=<ID=CSQ,Number=.,Type=String,Description="Consequence '
        f'annotations from Ensembl VEP. Format: {CSQ_FORMAT}">',
    ]
    cols = "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO"
    if sample:
        cols += "\tFORMAT\tSAMPLE1"
    lines = h + [cols]
    for i, csq in enumerate(records, 1):
        row = f"1\t{1000+i}\t.\tC\tA\t.\tPASS\tCSQ={csq}"
        if sample:
            row += "\tGT\t0/1"
        lines.append(row)
    return "\n".join(lines) + "\n"


def _write(path, text):
    op = gzip.open if path.endswith(".gz") else open
    with op(path, "wt") as fh:
        fh.write(text)


def test_parse_csq_format():
    hdr = ['##INFO=<ID=CSQ,Number=.,Type=String,Description="... Format: '
           + CSQ_FORMAT + '">']
    fields = aam.parse_csq_format(hdr)
    assert fields[0] == "Allele"
    assert "SYMBOL" in fields and "Protein_position" in fields and "Consequence" in fields


def test_match_and_nonmatch(tmp_path):
    # reference: BRCA1 residue 100 is a known pathogenic missense
    ref = {("BRCA1", "100")}
    records = [
        _csq("missense_variant", "BRCA1", "100"),   # -> match
        _csq("missense_variant", "BRCA1", "250"),   # residue not in ref -> 0
        _csq("missense_variant", "TP53", "100"),    # symbol mismatch -> 0
        _csq("synonymous_variant", "BRCA1", "100"), # not missense -> 0
        _csq("missense_variant", "BRCA1", "-"),     # no protein pos -> 0
    ]
    vin = str(tmp_path / "in.vcf")
    vout = str(tmp_path / "out.vcf")
    _write(vin, _vcf(records))
    stats = aam.annotate(vin, vout, ref)
    assert stats["records"] == 5
    assert stats["matched"] == 1, stats
    # verify per-record flags + zygosity preserved
    flags, gts = [], []
    for line in open(vout):
        if line.startswith("#"):
            continue
        cols = line.rstrip("\n").split("\t")
        info = cols[7]
        kv = dict(x.split("=", 1) for x in info.split(";") if "=" in x)
        flags.append(kv["ClinVar_path_aa_match"])
        gts.append(cols[9])  # SAMPLE1 GT
    assert flags == ["1", "0", "0", "0", "0"], flags
    assert gts == ["0/1"] * 5, "zygosity/GT must be preserved untouched"


def test_header_injected(tmp_path):
    ref = {("BRCA1", "100")}
    vin = str(tmp_path / "in.vcf"); vout = str(tmp_path / "out.vcf")
    _write(vin, _vcf([_csq("missense_variant", "BRCA1", "100")]))
    aam.annotate(vin, vout, ref, clinvar_release="20260218")
    text = open(vout).read()
    assert '##INFO=<ID=ClinVar_path_aa_match,Number=1,Type=Integer' in text
    assert "20260218" in text  # release stamped in description


def test_multi_transcript_any_match(tmp_path):
    """A variant with several CSQ transcript entries matches if ANY is a hit."""
    ref = {("BRCA1", "100")}
    multi = ",".join([
        _csq("intron_variant", "BRCA1", "-"),
        _csq("missense_variant", "BRCA1", "100"),  # this one hits
    ])
    vin = str(tmp_path / "in.vcf"); vout = str(tmp_path / "out.vcf")
    _write(vin, _vcf([multi]))
    stats = aam.annotate(vin, vout, ref)
    assert stats["matched"] == 1


def test_protein_position_range_form(tmp_path):
    """VEP sometimes emits Protein_position as e.g. '100' — reference must use
    the same string form the sample VCF carries (exact-string match)."""
    ref = {("BRCA1", "100")}
    vin = str(tmp_path / "in.vcf"); vout = str(tmp_path / "out.vcf")
    _write(vin, _vcf([_csq("missense_variant", "BRCA1", "100")]))
    stats = aam.annotate(vin, vout, ref)
    assert stats["matched"] == 1


def test_gzip_roundtrip(tmp_path):
    ref = {("BRCA1", "100")}
    vin = str(tmp_path / "in.vcf.gz"); vout = str(tmp_path / "out.vcf.gz")
    _write(vin, _vcf([_csq("missense_variant", "BRCA1", "100")]))
    aam.annotate(vin, vout, ref)
    with gzip.open(vout, "rt") as fh:
        text = fh.read()
    assert "ClinVar_path_aa_match=1" in text


def test_empty_reference_all_zero(tmp_path):
    """No reference -> every record flagged 0, run still valid."""
    vin = str(tmp_path / "in.vcf"); vout = str(tmp_path / "out.vcf")
    _write(vin, _vcf([_csq("missense_variant", "BRCA1", "100")]))
    stats = aam.annotate(vin, vout, set())
    assert stats["matched"] == 0
    assert "ClinVar_path_aa_match=0" in open(vout).read()


def test_short_record_is_padded_not_crashed(tmp_path):
    """Audit repro (CORE-5): a 7-column sites-only record used to raise
    IndexError on the unconditional cols[7] assignment."""
    vcf_in = str(tmp_path / "in.vcf")
    with open(vcf_in, "w") as fh:
        fh.write(
            "##fileformat=VCFv4.2\n"
            '##INFO=<ID=CSQ,Number=.,Type=String,Description="... Format: '
            + CSQ_FORMAT + '">\n'
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
            "1\t100\t.\tC\tA\t.\tPASS\n"          # 7 columns, INFO missing
        )
    ref_path = str(tmp_path / "ref.tsv")
    with open(ref_path, "w") as fh:
        fh.write("BRCA1\t100\n")
    out = str(tmp_path / "out.vcf")
    rc = aam.main(["--input", vcf_in, "--output", out,
                   "--reference", ref_path, "--clinvar-release", "TEST"])
    assert rc == 0
    record = [l for l in open(out) if not l.startswith("#")][0].rstrip("\n")
    assert record.split("\t")[7] == "ClinVar_path_aa_match=0"


def test_reduce_gz_output_is_real_gzip(tmp_path):
    """Audit repro (CORE-4 twin): a .gz output name used to receive plain text."""
    tab = str(tmp_path / "clinvar.vep.tsv")
    with open(tab, "w") as fh:
        fh.write("#Uploaded_variation\tSYMBOL\tProtein_position\tConsequence\n")
        fh.write("v1\tBRCA1\t100\tmissense_variant\n")
    out = str(tmp_path / "ref.tsv.gz")
    n = red.reduce_tab(tab, out)
    assert n == 1
    with open(out, "rb") as fh:
        assert fh.read(2) == b"\x1f\x8b"
    with gzip.open(out, "rt") as fh:
        assert fh.read() == "BRCA1\t100\n"


def test_reducer(tmp_path):
    """The VEP-tab -> reference reducer keeps missense w/ protein pos, dedups."""
    tab = str(tmp_path / "clinvar.vep.tsv")
    with open(tab, "w") as fh:
        fh.write("#Uploaded_variation\tSYMBOL\tProtein_position\tConsequence\n")
        fh.write("v1\tBRCA1\t100\tmissense_variant\n")
        fh.write("v2\tBRCA1\t100\tmissense_variant\n")   # dup
        fh.write("v3\tTP53\t250\tmissense_variant&NMD\n")  # compound cons, keep
        fh.write("v4\tBRCA1\t-\tmissense_variant\n")       # no pos, drop
        fh.write("v5\tEGFR\t50\tsynonymous_variant\n")     # not missense, drop
    out = str(tmp_path / "ref.tsv")
    n = red.reduce_tab(tab, out)
    lines = [l.strip() for l in open(out)]
    assert n == 2, lines
    assert "BRCA1\t100" in lines
    assert "TP53\t250" in lines
    # round-trips into the matcher's loader
    ref = aam.load_reference(out)
    assert ("BRCA1", "100") in ref and ("TP53", "250") in ref


if __name__ == "__main__":
    import tempfile, pathlib, inspect
    passed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            sig = inspect.signature(fn)
            with tempfile.TemporaryDirectory() as td:
                if sig.parameters:
                    fn(pathlib.Path(td))
                else:
                    fn()
            print(f"PASS  {name}")
            passed += 1
    print(f"\n{passed} tests passed")
