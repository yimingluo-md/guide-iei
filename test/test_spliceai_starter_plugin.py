import json
from pathlib import Path
import subprocess
import textwrap

ROOT = Path(__file__).resolve().parents[1]


def test_stable_gene_matching_and_overlap_safety(tmp_path):
    stub = tmp_path / "Bio/EnsEMBL/Variation/Utils"
    stub.mkdir(parents=True)
    (stub / "BaseVepTabixPlugin.pm").write_text(textwrap.dedent(r'''
        package Bio::EnsEMBL::Variation::Utils::BaseVepTabixPlugin;
        sub new { my ($class, $config, @args) = @_; bless {args=>\@args}, $class }
        sub params_to_hash { my %p=map {split /=/, $_, 2} @{$_[0]{args}}; return \%p }
        sub expand_left {} sub expand_right {} sub cache_size {} sub add_file {}
        sub get_data { $_[0]{rows} || [] }
        1;
    '''))
    mapping = json.loads((ROOT / "docker/SpliceAI-MANE1.5-gene-map.json").read_text())
    manifest = {"resource": "starter_spliceai", "assembly": "GRCh38",
                "sources": [{"role": role, "sha256": value} for role, value in mapping["source_sha256"].items()]}
    source = tmp_path / "preparation.json"
    source.write_text(json.dumps(manifest))
    script = tmp_path / "check.pl"
    script.write_text(textwrap.dedent(r'''
        use strict; use warnings; use JSON::PP qw(encode_json); use SpliceAIStarter;
        {package Tx; sub get_Gene {die 'offline cache'} }
        {package VF; sub ref_allele_string {$_[0]{ref}} sub strand {1} }
        {package TVA;
          sub transcript { bless {_gene_stable_id=>$_[0]{gene},_gene_symbol=>($_[0]{symbol}||'')}, 'Tx' }
          sub variation_feature { bless {chr=>($_[0]{chr}||'4'),start=>6070055,end=>6070055,ref=>($_[0]{ref}||'A')}, 'VF' }
          sub variation_feature_seq { $_[0]{alt} || 'C' }
        }
        sub query { my %v=@_; return bless {gene=>'ENSG00000284684.1',%v}, 'TVA' }
        my $p=SpliceAIStarter->new({},'snv=fixture.vcf.gz',"manifest=$ARGV[0]");
        my $line="4\t6070055\t.\tA\tC\t.\t.\tSpliceAI=C|AC092442.3|0|0|0.72|0.99|23|-83|-108|2";
        my $row=$p->parse_data($line);
        $p->{rows}=[$row];
        my %r=(missing_symbol=>$p->run(query()), renamed_symbol=>$p->run(query(symbol=>'NEW_NAME')),
          other_gene=>$p->run(query(gene=>'ENSG00000152969',symbol=>'AC092442.3')),
          missing_gene=>$p->run(query(gene=>'',symbol=>'AC092442.3')),
          wrong_alt=>$p->run(query(alt=>'G')), wrong_ref=>$p->run(query(ref=>'G')),
          indel=>$p->run(query(alt=>'AC')), wrong_chrom=>$p->run(query(chr=>'7')));
        $p->{rows}=[$row,$row]; $r{duplicate}=$p->run(query());
        $p->{rows}=[$p->parse_data($line.',C|LOC128125818|0|0|0.72|0.99|23|-83|-108|2')];
        $r{agreeing_aliases}=$p->run(query());
        $p->{rows}=[$p->parse_data($line.',C|JAKMIP1|0|0|0.12|0.19|23|-83|-108|2;OTHER=value')];
        $r{multi_gene}=$p->run(query());
        $r{neighbor}=$p->run(query(gene=>'ENSG00000152969'));
        $p->{rows}=[$p->parse_data($line.',C|LOC128125818|0|0|0.72|0.98|23|-83|-108|2')];
        $r{conflict}=$p->run(query());
        $p->{rows}=[$row,$p->parse_data($line =~ s/0\.99/0.98/r)];
        $r{conflicting_rows}=$p->run(query());
        eval {$p->parse_data($line =~ s/SpliceAI=C/SpliceAI=G/r)};
        $r{bad_allele_rejected}=length($@)>0 ? 1:0;
        print encode_json(\%r);
    '''))
    command = ["perl", f"-I{tmp_path}", f"-I{ROOT / 'docker'}", str(script), str(source)]
    result = subprocess.run(command, capture_output=True, text=True, check=True)
    rows = json.loads(result.stdout)
    for key in ("missing_symbol", "renamed_symbol", "duplicate", "agreeing_aliases", "multi_gene"):
        assert float(rows[key]["SpliceAI_pred_DS_DL"]) == .99, key
        assert rows[key]["SpliceAI_pred_SYMBOL"] == "AC092442.3"
    assert float(rows["neighbor"]["SpliceAI_pred_DS_DL"]) == .19
    for key in ("other_gene", "missing_gene", "wrong_alt", "wrong_ref", "indel", "wrong_chrom", "conflict", "conflicting_rows"):
        assert rows[key] == {}, key
    assert rows["bad_allele_rejected"]
    manifest["sources"][0]["sha256"] = "0" * 64
    source.write_text(json.dumps(manifest))
    rejected = subprocess.run(command, capture_output=True, text=True)
    assert rejected.returncode != 0 and "does not match" in rejected.stderr


def test_bundled_map_is_unambiguous_and_pinned():
    mapping = json.loads((ROOT / "docker/SpliceAI-MANE1.5-gene-map.json").read_text())
    assert len(mapping["genes"]) == 19363
    assert mapping["genes"]["ENSG00000284684"]["transcript"] == "ENST00000636216.1"
    assert "AC092442.3" in mapping["genes"]["ENSG00000284684"]["symbols"]
    assert "RP11-511P7.5" in mapping["genes"]["ENSG00000284691"]["symbols"]
    owners = {}
    for gene, data in mapping["genes"].items():
        for symbol in data["symbols"]:
            key = (data["chrom"], symbol)
            assert key not in owners or owners[key] == gene
            owners[key] = gene
