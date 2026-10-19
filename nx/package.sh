#!/usr/bin/env bash
set -euo pipefail
REPO=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
OUT=${1:?用法: ./nx/package.sh /tmp/gmsl-camera-acceptance-nx.tar.gz}
python3 - "$REPO" "$OUT" <<'PY'
from pathlib import Path
import sys
root,out=map(lambda s:Path(s).resolve(),sys.argv[1:])
if out == root or root in out.parents or out.exists():
    raise SystemExit('输出必须在仓库外且不能覆盖已有文件')
PY
(cd "$REPO/nx/bundles" && shasum -a 256 -c SHA256SUMS)
COPYFILE_DISABLE=1 tar --exclude='__pycache__' --exclude='*.pyc' -czf "$OUT" -C "$REPO" \
  README.md DRIVER_AND_FILES.md camera_acceptance.py run.sh profiles manifests nx docs
shasum -a 256 "$OUT"
