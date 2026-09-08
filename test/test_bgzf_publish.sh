#!/usr/bin/env bash
# Audit M12: a BGZF file cut at a block boundary is a valid gzip stream
# (`gzip -t` passes, tabix indexes it) but lacks the 28-byte EOF block, and
# `bcftools view -R` then silently omits every region after the cut. The
# shared helpers must recognise such files, and every published BGZF must be
# written atomically so the final name never holds one.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${HERE}/.." && pwd)"
source "${ROOT}/scripts/lib.sh"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
fail() { echo "FAIL: $*" >&2; exit 1; }
file_size() { wc -c < "$1" | tr -d ' '; }
no_publish_temps() { local f; for f in "$1"/.*.publish.*; do [[ -e "$f" ]] && return 1; done; return 0; }

# --- 1. bgzf_complete on hand-built BGZF blocks (no htslib needed) ------------
# One BGZF block per contig, so "cut at a block boundary" is exactly "drop the
# second block". Both files pass gzip -t; only one carries the EOF marker.
python3 - "$WORK" <<'PY'
import binascii, pathlib, struct, sys, zlib
work = pathlib.Path(sys.argv[1])
EOF = bytes.fromhex("1f8b08040000000000ff0600424302001b0003000000000000000000")
def block(content: bytes) -> bytes:
    c = zlib.compressobj(level=6, wbits=-15)
    data = c.compress(content) + c.flush()
    fixed = struct.pack("<BBBBLBBH", 31, 139, 8, 4, 0, 0, 255, 6)
    size = len(fixed) + 6 + len(data) + 8
    extra = b"BC" + struct.pack("<HH", 2, size - 1)
    trailer = struct.pack("<LL", binascii.crc32(content) & 0xFFFFFFFF, len(content))
    return fixed + extra + data + trailer
one = block(b"1\t0\t100\n")
two = block(b"2\t0\t100\n")
(work / "complete.bed.gz").write_bytes(one + two + EOF)
(work / "cut.bed.gz").write_bytes(one)            # second block and EOF lost
(work / "no-eof.bed.gz").write_bytes(one + two)   # only the EOF block lost
PY
for f in complete cut no-eof; do
    gzip -t "$WORK/$f.bed.gz" || fail "$f.bed.gz should be a valid gzip stream"
done
bgzf_complete "$WORK/complete.bed.gz" || fail "complete BGZF rejected"
! bgzf_complete "$WORK/cut.bed.gz" || fail "BGZF cut at a block boundary accepted"
! bgzf_complete "$WORK/no-eof.bed.gz" || fail "BGZF without its EOF block accepted"
: > "$WORK/empty.gz"
! bgzf_complete "$WORK/empty.gz" || fail "empty file accepted"
! bgzf_complete "$WORK/absent.gz" || fail "absent file accepted"
echo "  bgzf_complete distinguishes complete, cut, and marker-less streams"

# --- 2. the guards are wired where the files are published/consumed ---------
grep -F 'publish_bgzf "$MERGED" "$BED" bed' "${ROOT}/scripts/build_coding_bed.sh" >/dev/null \
    || fail "build_coding_bed.sh no longer publishes the coding BED atomically"
grep -F 'coding BED would lack contig(s)' "${ROOT}/scripts/build_coding_bed.sh" >/dev/null \
    || fail "build_coding_bed.sh no longer verifies contig completeness before publishing"
grep -F 'bgzf_complete "$REGION_BED"' "${ROOT}/scripts/run_annotation.sh" >/dev/null \
    || fail "run_annotation.sh no longer requires a complete BGZF region file"
grep -F 'coding BED lacks contig(s)' "${ROOT}/scripts/run_annotation.sh" >/dev/null \
    || fail "run_annotation.sh no longer verifies the region file's contigs"
grep -F 'region.custom_bed is an incomplete BGZF file' "${ROOT}/scripts/run_annotation.sh" >/dev/null \
    || fail "run_annotation.sh no longer checks a custom BED for the EOF marker"
grep -F 'publish_bgzf "${FASTA_PATH%.gz}" "$FASTA_PATH"' "${ROOT}/scripts/download_references.sh" >/dev/null \
    || fail "download_references.sh no longer publishes the reference FASTA atomically"
grep -F 'publish_bgzf "$UNCOMPRESSED_HG19" "$LIFTOVER_SOURCE_FASTA"' "${ROOT}/scripts/download_references.sh" >/dev/null \
    || fail "download_references.sh no longer publishes the hg19 FASTA atomically"
grep -F 'mv -f "${LIFTOVER_CHAIN}.tmp" "$LIFTOVER_CHAIN"' "${ROOT}/scripts/download_references.sh" >/dev/null \
    || fail "download_references.sh no longer publishes the chain through a verified temp file"
# The full chain installation and corruption paths are exercised separately.
python3 "${ROOT}/test/test_reference_chain.py" || fail "chain installation regression failed"
echo "  publication and consumption sites use the guards"

# --- 3. publish_bgzf with the real tools --------------------------------------
if ! command -v bgzip >/dev/null 2>&1 || ! command -v tabix >/dev/null 2>&1; then
    echo "PASS  BGZF completeness guards (publish_bgzf section skipped: bgzip/tabix not on PATH)"
    exit 0
fi
REAL_BGZIP="$(command -v bgzip)"
export RUNTIME=none HTS_VIA_CONTAINER=0

printf '1\t0\t100\n2\t0\t100\n' > "$WORK/plain.bed"
mkdir -p "$WORK/out"
publish_bgzf "$WORK/plain.bed" "$WORK/out/regions.bed.gz" bed || fail "publish_bgzf failed on valid input"
bgzf_complete "$WORK/out/regions.bed.gz" || fail "published file lacks the EOF marker"
[[ -s "$WORK/out/regions.bed.gz.tbi" ]] || fail "published file has no index"
[[ ! -e "$WORK/plain.bed" ]] || fail "plain input should be consumed"
no_publish_temps "$WORK/out" || fail "publish temp left behind"
[[ "$(tabix -l "$WORK/out/regions.bed.gz" | tr '\n' ' ')" == "1 2 " ]] || fail "index does not list both contigs"
echo "  publish_bgzf writes a complete, indexed file and consumes the input"

# A bgzip that stops at a block boundary (here: the real bgzip minus its EOF
# block) must not have its output renamed onto the final name.
mkdir -p "$WORK/bin"
cat > "$WORK/bin/bgzip" <<SH
#!/usr/bin/env bash
"$REAL_BGZIP" "\$@" || exit \$?
out="\${@: -1}.gz"
size=\$(wc -c < "\$out" | tr -d ' ')
head -c "\$(( size - 28 ))" "\$out" > "\$out.short" && mv -f "\$out.short" "\$out"
SH
chmod +x "$WORK/bin/bgzip"
printf '1\t0\t100\n' > "$WORK/plain2.bed"
rc=0
(PATH="$WORK/bin:$PATH" publish_bgzf "$WORK/plain2.bed" "$WORK/out/short.bed.gz" bed) \
    >"$WORK/short.log" 2>&1 || rc=$?
[[ "$rc" != "0" ]] || fail "an incomplete bgzip stream was published"
grep -F 'incomplete BGZF stream' "$WORK/short.log" >/dev/null || fail "no diagnostic for the incomplete stream"
[[ ! -e "$WORK/out/short.bed.gz" && ! -e "$WORK/out/short.bed.gz.tbi" ]] || fail "final name exists after a failed publish"
no_publish_temps "$WORK/out" || fail "publish temp left behind after failure"
echo "  publish_bgzf refuses to publish a marker-less stream and leaves nothing behind"

# --- 4. why gzip -t is not enough (informational reproduction) ----------------
if command -v bcftools >/dev/null 2>&1; then
    cp "$WORK/cut.bed.gz" "$WORK/out/cut.bed.gz"
    tabix -f -p bed "$WORK/out/cut.bed.gz" 2>/dev/null && tabix_rc=0 || tabix_rc=$?
    cat > "$WORK/two.vcf" <<'VCF'
##fileformat=VCFv4.2
##contig=<ID=1,length=1000>
##contig=<ID=2,length=1000>
#CHROM	POS	ID	REF	ALT	QUAL	FILTER	INFO
1	50	.	A	G	.	PASS	.
2	50	.	A	G	.	PASS	.
VCF
    "$REAL_BGZIP" -f "$WORK/two.vcf" && tabix -f -p vcf "$WORK/two.vcf.gz"
    if [[ "$tabix_rc" == "0" ]]; then
        kept="$(bcftools view -H -R "$WORK/out/cut.bed.gz" "$WORK/two.vcf.gz" 2>/dev/null | cut -f1 | tr '\n' ' ' || true)"
        echo "  (bcftools $(bcftools --version | head -1 | awk '{print $2}') -R on the cut file kept contigs: ${kept:-none} — the guard exists because this is not an error)"
    fi
fi

echo "PASS  BGZF completeness guards and atomic publication"
