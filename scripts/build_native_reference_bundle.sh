#!/usr/bin/env bash
# Build the redistributable native-reference release payload. The resulting
# tar contains paths relative to the configured annotation root and is intended
# to be unpacked alongside the application during release installation.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${HERE}/.." && pwd)"
ANNOTATION_ROOT="${1:-${ROOT}/references}"
OUTPUT="${2:-${ROOT}/native-reference-bundle.tar}"
MANIFEST="${ROOT}/config/native_reference_bundle.json"

python3 - "${ANNOTATION_ROOT}" "${MANIFEST}" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

root, manifest_path = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
errors = []
for item in manifest["files"]:
    path = root / item["path"]
    if not path.is_file():
        errors.append(f"missing: {path}")
        continue
    size = path.stat().st_size
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            hasher.update(chunk)
    digest = hasher.hexdigest()
    if size != item["bytes"]:
        errors.append(f"size mismatch: {path} ({size} != {item['bytes']})")
    if digest != item["sha256"]:
        errors.append(f"checksum mismatch: {path}")
if errors:
    raise SystemExit("native reference bundle validation failed:\n" + "\n".join(errors))
print(f"validated {len(manifest['files'])} native reference files")
PY

FILE_LIST="$(mktemp)"
trap 'rm -f "${FILE_LIST}"' EXIT
python3 - "${MANIFEST}" > "${FILE_LIST}" <<'PY'
import json
import sys
from pathlib import Path
for item in json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))["files"]:
    print(item["path"])
PY

mkdir -p "$(dirname "${OUTPUT}")"
tar -C "${ANNOTATION_ROOT}" -cf "${OUTPUT}" -T "${FILE_LIST}"
echo "Native reference bundle: ${OUTPUT}"
