#!/usr/bin/env python3
"""Generate the synthetic demonstration exome used for manual screenshots.

Every value here is invented. The sample is one fictional individual
(DEMO01); genes are real IEI genes so the screenshots read naturally, but
positions, alleles, scores, and ClinVar entries are fabricated and must
never be interpreted as real variant assertions. Regenerate with:

    python3 scripts/make_demo_vcf.py [output.vcf.gz]
"""
import gzip
import sys
from pathlib import Path

CSQ_FIELDS = (
    "Allele|ALLELE_NUM|Consequence|IMPACT|SYMBOL|Gene|Feature|BIOTYPE|EXON|"
    "HGVSc|HGVSp|MANE_SELECT|MANE_PLUS_CLINICAL|PICK|MAX_AF|MAX_AF_POPS|"
    "CADD_phred|CADD_raw|AlphaMissense_score|AlphaMissense_pred|REVEL_score|"
    "SIFT_score|SIFT_pred|Polyphen2_HDIV_score|Polyphen2_HDIV_pred|"
    "SpliceAI_pred_DS_AG|SpliceAI_pred_DS_AL|SpliceAI_pred_DS_DG|"
    "SpliceAI_pred_DS_DL|ClinVar_CLNSIG|ClinVar_CLNREVSTAT|LoF|LoF_filter|"
    "LoF_flags|LoF_50_BP_RULE_PTC|LoF_50_BP_RULE_original|"
    "LoF_50_BP_RULE_changed|PTC_dist_from_last_exon|Repeat|SegDup"
)
N_FIELDS = len(CSQ_FIELDS.split("|"))


def csq(**kw):
    values = {name: "" for name in CSQ_FIELDS.split("|")}
    for key, value in kw.items():
        assert key in values, key
        values[key] = str(value)
    joined = "|".join(values[name] for name in CSQ_FIELDS.split("|"))
    assert joined.count("|") == N_FIELDS - 1
    return joined


def record(chrom, pos, ref, alt, qual, gt, dp, ad, gq, *annotations):
    info = "CSQ=" + ",".join(annotations)
    return (f"{chrom}\t{pos}\t.\t{ref}\t{alt}\t{qual}\tPASS\t{info}"
            f"\tGT:AD:DP:GQ\t{gt}:{ad}:{dp}:{gq}")


RECORDS = [
    # BTK stop-gained, hemizygous male, pathogenic: the "clear signal" row.
    record("X", 101_356_176, "G", "A", 1620, "1/1", 41, "0,41", 99, csq(
        Allele="A", ALLELE_NUM=1, Consequence="stop_gained", IMPACT="HIGH",
        SYMBOL="BTK", Gene="ENSG00000010671", Feature="ENST00000308731",
        BIOTYPE="protein_coding", EXON="15/19", HGVSc="c.1573C>T",
        HGVSp="p.Arg525Ter", MANE_SELECT="NM_000061.3", PICK=1,
        CADD_phred=41, CADD_raw=7.1,
        ClinVar_CLNSIG="Pathogenic", ClinVar_CLNREVSTAT="reviewed_by_expert_panel",
        LoF="HC", LoF_50_BP_RULE_PTC="PASS", LoF_50_BP_RULE_original="PASS",
        PTC_dist_from_last_exon=812,
    )),
    # NFKB1 frameshift where the PTC-based 50-bp recalculation changed the call.
    record("4", 102_501_331, "TC", "T", 890, "0/1", 35, "18,17", 99, csq(
        Allele="-", ALLELE_NUM=1, Consequence="frameshift_variant", IMPACT="HIGH",
        SYMBOL="NFKB1", Gene="ENSG00000109320", Feature="ENST00000226574",
        BIOTYPE="protein_coding", EXON="21/24", HGVSc="c.2812del",
        HGVSp="p.Leu938fs", MANE_SELECT="NM_003998.4", PICK=1,
        CADD_phred=33, CADD_raw=5.6, LoF="HC",
        LoF_50_BP_RULE_PTC="FAIL", LoF_50_BP_RULE_original="PASS",
        LoF_50_BP_RULE_changed=1, PTC_dist_from_last_exon=38,
    )),
    # DOCK8 stop-gained demoted to LC with an inspectable reason.
    record("9", 342_912, "C", "T", 512, "0/1", 29, "15,14", 96, csq(
        Allele="T", ALLELE_NUM=1, Consequence="stop_gained", IMPACT="HIGH",
        SYMBOL="DOCK8", Gene="ENSG00000107099", Feature="ENST00000432829",
        BIOTYPE="protein_coding", EXON="48/48", HGVSc="c.6019C>T",
        HGVSp="p.Gln2007Ter", MANE_SELECT="NM_203447.4", PICK=1,
        CADD_phred=37, CADD_raw=6.2, LoF="LC", LoF_filter="END_TRUNC",
        LoF_flags="PHYLOCSF_WEAK", PTC_dist_from_last_exon=21,
    )),
    # STAT1 missense with strong predictor support.
    record("2", 190_969_121, "T", "C", 1105, "0/1", 44, "23,21", 99, csq(
        Allele="C", ALLELE_NUM=1, Consequence="missense_variant",
        IMPACT="MODERATE", SYMBOL="STAT1", Gene="ENSG00000115415",
        Feature="ENST00000361099", BIOTYPE="protein_coding", EXON="10/25",
        HGVSc="c.821A>G", HGVSp="p.Gln274Arg", MANE_SELECT="NM_007315.4",
        PICK=1, MAX_AF=0.000004, MAX_AF_POPS="gnomADe_NFE",
        CADD_phred=29.4, CADD_raw=4.9, AlphaMissense_score=0.94,
        AlphaMissense_pred="likely_pathogenic", REVEL_score=0.81,
        SIFT_score=0.01, SIFT_pred="deleterious", Polyphen2_HDIV_score=0.98,
        Polyphen2_HDIV_pred="probably_damaging",
        ClinVar_CLNSIG="Likely_pathogenic",
        ClinVar_CLNREVSTAT="criteria_provided,_multiple_submitters",
    )),
    # TCF3: MANE Select and MANE Plus Clinical consequences for one variant.
    record("19", 1_619_415, "G", "A", 705, "0/1", 38, "20,18", 99,
        csq(
            Allele="A", ALLELE_NUM=1, Consequence="missense_variant",
            IMPACT="MODERATE", SYMBOL="TCF3", Gene="ENSG00000071564",
            Feature="ENST00000262965", BIOTYPE="protein_coding", EXON="18/19",
            HGVSc="c.1663G>A", HGVSp="p.Glu555Lys", MANE_SELECT="NM_003200.5",
            PICK=1, CADD_phred=27.8, CADD_raw=4.4, AlphaMissense_score=0.71,
            AlphaMissense_pred="likely_pathogenic", REVEL_score=0.62,
            SIFT_score=0.02, SIFT_pred="deleterious",
            Polyphen2_HDIV_score=0.93, Polyphen2_HDIV_pred="probably_damaging",
        ),
        csq(
            Allele="A", ALLELE_NUM=1, Consequence="missense_variant",
            IMPACT="MODERATE", SYMBOL="TCF3", Gene="ENSG00000071564",
            Feature="ENST00000588136", BIOTYPE="protein_coding", EXON="17/18",
            HGVSc="c.1627G>A", HGVSp="p.Glu543Lys",
            MANE_PLUS_CLINICAL="NM_001136139.4",
            CADD_phred=27.8, CADD_raw=4.4, AlphaMissense_score=0.69,
            AlphaMissense_pred="likely_pathogenic", REVEL_score=0.62,
            SIFT_score=0.02, SIFT_pred="deleterious",
            Polyphen2_HDIV_score=0.93, Polyphen2_HDIV_pred="probably_damaging",
        )),
    # CYBB essential splice donor with a decisive SpliceAI loss score.
    record("X", 37_782_204, "G", "T", 980, "1/1", 33, "0,33", 99, csq(
        Allele="T", ALLELE_NUM=1, Consequence="splice_donor_variant",
        IMPACT="HIGH", SYMBOL="CYBB", Gene="ENSG00000165168",
        Feature="ENST00000378588", BIOTYPE="protein_coding", EXON="7/13",
        HGVSc="c.742+1G>T", MANE_SELECT="NM_000397.4", PICK=1,
        CADD_phred=34, CADD_raw=5.8, SpliceAI_pred_DS_DL=0.97,
        SpliceAI_pred_DS_AG=0.02, LoF="HC",
    )),
    # WAS deep intronic splice-gain candidate, the SpliceAI showcase.
    record("X", 48_688_302, "A", "G", 640, "1/1", 27, "0,27", 93, csq(
        Allele="G", ALLELE_NUM=1, Consequence="intron_variant",
        IMPACT="MODIFIER", SYMBOL="WAS", Gene="ENSG00000015285",
        Feature="ENST00000376701", BIOTYPE="protein_coding",
        HGVSc="c.273+286A>G", MANE_SELECT="NM_000377.3", PICK=1,
        MAX_AF=0.00001, MAX_AF_POPS="gnomADg_AFR", CADD_phred=14.2,
        CADD_raw=1.8, SpliceAI_pred_DS_AG=0.62, SpliceAI_pred_DS_AL=0.05,
    )),
    # IL10RA missense of uncertain significance, homozygous.
    record("11", 117_986_285, "C", "T", 1310, "1/1", 47, "0,47", 99, csq(
        Allele="T", ALLELE_NUM=1, Consequence="missense_variant",
        IMPACT="MODERATE", SYMBOL="IL10RA", Gene="ENSG00000110324",
        Feature="ENST00000227752", BIOTYPE="protein_coding", EXON="4/7",
        HGVSc="c.517G>A", HGVSp="p.Gly173Arg", MANE_SELECT="NM_001558.4",
        PICK=1, MAX_AF=0.00008, MAX_AF_POPS="gnomADe_SAS",
        CADD_phred=25.1, CADD_raw=4.0, AlphaMissense_score=0.48,
        AlphaMissense_pred="ambiguous", REVEL_score=0.44, SIFT_score=0.06,
        SIFT_pred="tolerated", Polyphen2_HDIV_score=0.72,
        Polyphen2_HDIV_pred="possibly_damaging",
        ClinVar_CLNSIG="Uncertain_significance",
        ClinVar_CLNREVSTAT="criteria_provided,_single_submitter",
    )),
    # RAG1 missense, compound-het style partner kept simple here.
    record("11", 36_573_361, "G", "A", 860, "0/1", 40, "21,19", 99, csq(
        Allele="A", ALLELE_NUM=1, Consequence="missense_variant",
        IMPACT="MODERATE", SYMBOL="RAG1", Gene="ENSG00000166349",
        Feature="ENST00000299440", BIOTYPE="protein_coding", EXON="2/2",
        HGVSc="c.1420C>T", HGVSp="p.Arg474Cys", MANE_SELECT="NM_000448.3",
        PICK=1, MAX_AF=0.0002, MAX_AF_POPS="gnomADe_NFE", CADD_phred=28.3,
        CADD_raw=4.6, AlphaMissense_score=0.83,
        AlphaMissense_pred="likely_pathogenic", REVEL_score=0.75,
        SIFT_score=0.01, SIFT_pred="deleterious", Polyphen2_HDIV_score=0.99,
        Polyphen2_HDIV_pred="probably_damaging",
        ClinVar_CLNSIG="Pathogenic/Likely_pathogenic",
        ClinVar_CLNREVSTAT="criteria_provided,_multiple_submitters",
    )),
    # IKBKG variant flagged by segmental-duplication overlap (pseudogene trap).
    record("X", 154_565_058, "C", "T", 210, "0/1", 58, "36,22", 71, csq(
        Allele="T", ALLELE_NUM=1, Consequence="missense_variant",
        IMPACT="MODERATE", SYMBOL="IKBKG", Gene="ENSG00000269335",
        Feature="ENST00000594239", BIOTYPE="protein_coding", EXON="9/10",
        HGVSc="c.1167C>T", HGVSp="p.Asp389Asn", MANE_SELECT="NM_001099857.5",
        PICK=1, CADD_phred=22.6, CADD_raw=3.4, AlphaMissense_score=0.31,
        AlphaMissense_pred="likely_benign", REVEL_score=0.29, SIFT_score=0.12,
        SIFT_pred="tolerated", Polyphen2_HDIV_score=0.44,
        Polyphen2_HDIV_pred="benign", SegDup=1,
    )),
    # Common TLR3 missense above the frequency ceiling: the filter demo.
    record("4", 186_083_063, "C", "T", 1480, "0/1", 52, "27,25", 99, csq(
        Allele="T", ALLELE_NUM=1, Consequence="missense_variant",
        IMPACT="MODERATE", SYMBOL="TLR3", Gene="ENSG00000164342",
        Feature="ENST00000296795", BIOTYPE="protein_coding", EXON="4/5",
        HGVSc="c.1234C>T", HGVSp="p.Leu412Phe", MANE_SELECT="NM_003265.3",
        PICK=1, MAX_AF=0.24, MAX_AF_POPS="gnomADe_NFE", CADD_phred=21.1,
        CADD_raw=3.1, AlphaMissense_score=0.12,
        AlphaMissense_pred="likely_benign", REVEL_score=0.18, SIFT_score=0.31,
        SIFT_pred="tolerated", Polyphen2_HDIV_score=0.05,
        Polyphen2_HDIV_pred="benign", ClinVar_CLNSIG="Benign",
        ClinVar_CLNREVSTAT="reviewed_by_expert_panel",
    )),
    # CARD11 in-frame deletion, no precomputed SpliceAI (indel safety flag).
    record("7", 2_946_118, "TCTC", "T", 430, "0/1", 31, "17,14", 95, csq(
        Allele="-", ALLELE_NUM=1, Consequence="inframe_deletion",
        IMPACT="MODERATE", SYMBOL="CARD11", Gene="ENSG00000198286",
        Feature="ENST00000396946", BIOTYPE="protein_coding", EXON="6/25",
        HGVSc="c.361_363del", HGVSp="p.Glu121del", MANE_SELECT="NM_032415.7",
        PICK=1, MAX_AF=0.00002, MAX_AF_POPS="gnomADe_EAS", CADD_phred=23.9,
        CADD_raw=3.7,
    ) + ";IEI_UNSCORED_INDEL=SpliceAI_indel"),
]


def main():
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("demo_exome.vep.vcf.gz")
    header = (
        "##fileformat=VCFv4.2\n"
        "##reference=GRCh38\n"
        "##source=GUIDE-IEI synthetic demonstration data — every value invented\n"
        + "".join(
            f"##contig=<ID={contig}>\n"
            for contig in ("2", "4", "7", "9", "11", "19", "X")
        )
        + '##FILTER=<ID=PASS,Description="All filters passed">\n'
        + f'##INFO=<ID=CSQ,Number=.,Type=String,Description="Consequence annotations from Ensembl VEP. Format: {CSQ_FIELDS}">\n'
        + '##INFO=<ID=IEI_UNSCORED_INDEL,Number=.,Type=String,Description="Retained without a precomputed score">\n'
        + '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n'
        + '##FORMAT=<ID=AD,Number=R,Type=Integer,Description="Allelic depths">\n'
        + '##FORMAT=<ID=DP,Number=1,Type=Integer,Description="Read depth">\n'
        + '##FORMAT=<ID=GQ,Number=1,Type=Integer,Description="Genotype quality">\n'
        + "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tDEMO01\n"
    )
    contig_order = {c: i for i, c in enumerate(("2", "4", "7", "9", "11", "19", "X"))}
    ordered = sorted(
        RECORDS,
        key=lambda line: (contig_order[line.split("\t")[0]], int(line.split("\t")[1])),
    )
    body = "\n".join(ordered) + "\n"
    opener = gzip.open if output.suffix == ".gz" else open
    with opener(output, "wt") as handle:
        handle.write(header + body)
    print(f"wrote {output} ({len(RECORDS)} records, sample DEMO01)")


if __name__ == "__main__":
    main()
