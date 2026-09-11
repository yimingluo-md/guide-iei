#!/usr/bin/env python3
"""Inventory a prebuilt engine without networks/data mounts; not license clearance."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess


EVIDENCE_PATHS = (
    "/opt/vep/src/ensembl-vep/LICENSE",
    "/opt/vep/src/loftee/LICENSE",
    "/opt/vep/src/Bio-HTS/LICENSE",
    "/opt/vep/src/htslib/LICENSE",
    "/opt/vep/src/kent-335_base/src/lib/README",
    "/opt/vep/src/kent-335_base/src/lib/gifcodes.h",
    "/opt/vep/src/kent-335_base/src/lib/gemfont.h",
    "/opt/vep/src/kent-335_base/src/inc/jointalign.h",
    "/opt/vep/src/kent-335_base/src/jkOwnLib/bandExt.c",
    "/plugins/SpliceAI.pm",
)


def audit(runtime, image):
    info = json.loads(subprocess.check_output([runtime, "image", "inspect", image], text=True))[0]
    image_id = info["Id"]
    def read(*args):
        return subprocess.check_output([runtime, "run", "--rm", "--pull=never", "--network=none",
            "--entrypoint", args[0], image_id, *args[1:]], timeout=120)
    packages = []
    raw = read("dpkg-query", "-W", "-f=${binary:Package}\t${Version}\t${source:Package}\t${source:Version}\n")
    for line in raw.decode().splitlines():
        fields = line.split("\t")
        if len(fields) != 4:
            raise ValueError("Unexpected dpkg inventory row")
        packages.append(dict(zip(("binary", "version", "source", "source_version"), fields)))
    sources = sorted({(row["source"], row["source_version"]) for row in packages})
    # cpanm retains distribution paths and declared licenses, even when the
    # build tarballs have been removed. These declarations need manual review.
    perl = r'''
use JSON::PP; use File::Find;
my @rows;
find(sub {
  return unless $_ eq 'install.json' && $File::Find::name =~ m{/\.meta/};
  open my $f, '<', $_ or die $!; local $/; my $i=decode_json(<$f>); close $f;
  open my $m, '<', 'MYMETA.json' or die $!; my $meta=decode_json(<$m>); close $m;
  push @rows, {distribution=>$i->{dist}, version=>$i->{version},
    cpan_path=>$i->{pathname}, license=>$meta->{license}, metadata=>$File::Find::name};
}, '/usr/local/share/perl', '/usr/local/lib');
print JSON::PP->new->canonical->encode([sort {$a->{distribution} cmp $b->{distribution}} @rows]);
'''
    cpan = json.loads(read("perl", "-e", perl))
    evidence = []
    for path in EVIDENCE_PATHS:
        data = read("cat", path)
        evidence.append({"path": path, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                         "first_lines": data.decode(errors="replace").splitlines()[:8]})
    return {"schema_version": 1, "review_status": "inventory_not_redistribution_clearance",
        "image_id": image_id, "image_created": info.get("Created"), "platform": info["Os"] + "/" + info["Architecture"],
        "source_fingerprint": (info.get("Config", {}).get("Labels") or {}).get("org.guide-iei.source-fingerprint"),
        "os_release": read("cat", "/etc/os-release").decode(),
        "binary_package_count": len(packages), "source_package_count": len(sources),
        "packages": packages, "source_packages": [{"name": name, "version": version} for name, version in sources],
        "cpan_distributions": cpan, "license_evidence": evidence,
        "limitations": ["Does not retrieve corresponding source archives.",
                         "Does not establish permission for legacy Kent jkOwnLib or every dependency.",
                         "Inventories the merged filesystem; docker save also contains historical layers."]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", default="docker")
    parser.add_argument("--image", default="vep-annotate:latest")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.runtime, args.image)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as output:
        json.dump(result, output, indent=2, sort_keys=True)
        output.write("\n")
    print(f"Inventoried {result['binary_package_count']} OS packages, {result['source_package_count']} OS sources, "
          f"{len(result['cpan_distributions'])} CPAN distributions; NOT license clearance: {args.output}")
