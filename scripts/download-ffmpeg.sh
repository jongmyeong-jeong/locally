#!/bin/bash
# macOS arm64 ffmpeg static binary를 다운로드한다.
# 빌드 전 실행: bash scripts/download-ffmpeg.sh
set -euo pipefail

DEST="bin/ffmpeg"
URL="https://evermeet.cx/ffmpeg/get/zip"

if [ -f "$DEST" ]; then
  echo "[OK] ffmpeg already exists at $DEST"
  exit 0
fi

echo "[INFO] Downloading ffmpeg static binary..."
mkdir -p bin
TMPDIR=$(mktemp -d)
trap 'rm -rf "$TMPDIR"' EXIT

curl -L --fail --retry 3 -o "$TMPDIR/ffmpeg.zip" "$URL"
unzip -o "$TMPDIR/ffmpeg.zip" -d "$TMPDIR"
mv "$TMPDIR/ffmpeg" "$DEST"
chmod +x "$DEST"

# Ad-hoc code sign for Gatekeeper compatibility
codesign --sign - --force "$DEST" 2>/dev/null || true

echo "[OK] ffmpeg downloaded to $DEST"
file "$DEST"
