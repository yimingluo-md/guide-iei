#!/bin/bash
# GUIDE-IEI workbench window. Opened by GUIDE-IEI.app (or by double-click).
# Keep this window open while using GUIDE-IEI; closing it stops the
# workbench. The first launch prepares the environment automatically.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "${HERE}/../.." && pwd)"
clear
echo "GUIDE-IEI is starting. Keep this window open while you work;"
echo "closing it stops the workbench. Your browser opens automatically."
echo
exec bash "${ROOT}/scripts/start_workbench.sh" --bootstrap
