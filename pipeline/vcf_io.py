"""Read plain VCF and gzip/BGZF text with the same UTF-8 and newline rules."""
import gzip
from pathlib import Path


COMPRESSED_SUFFIXES = (".gz", ".bgz", ".bgzf")
VCF_SUFFIXES = (".vcf.gz", ".vcf.bgz", ".vcf.bgzf", ".vcf")
BGZF_EOF = bytes.fromhex("1f8b08040000000000ff0600424302001b0003000000000000000000")


def check_bgzf_eof(path):
    """Require the end marker only for gzip members with a BGZF BC subfield."""
    with Path(path).open("rb") as stream:
        header = stream.read(12)
        if len(header) < 12 or header[:3] != b"\x1f\x8b\x08" or not header[3] & 4:
            return
        extra = stream.read(int.from_bytes(header[10:12], "little"))
        offset = 0
        while offset + 4 <= len(extra):
            tag = extra[offset:offset + 2]
            size = int.from_bytes(extra[offset + 2:offset + 4], "little")
            offset += 4 + size
            if tag == b"BC" and size == 2:
                stream.seek(-len(BGZF_EOF), 2)
                if stream.read() != BGZF_EOF:
                    raise ValueError(f"Invalid VCF {Path(path).name}: missing BGZF EOF marker (truncated input)")
                return


def validate_output_path(path, compression):
    compression = "none" if compression is None else compression
    suffixes = {"bgzip": ".vcf.gz", "none": ".vcf"}
    if compression not in suffixes:
        raise ValueError("output.compress must be bgzip or none")
    if not str(path).endswith(suffixes[compression]):
        raise ValueError(f"output.format=vcf + compress={compression} requires an output ending in {suffixes[compression]}")


def open_text(path):
    opener = gzip.open if Path(path).suffix.lower() in COMPRESSED_SUFFIXES else open
    return opener(path, "rt", encoding="utf-8")
