#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$ROOT/models"
mkdir -p "$DEST"

YUNET_URL="https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx"
LM_URL="https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task"
YUNET_SHA="8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"
LM_SHA="64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff"

sha256_of() {
  shasum -a 256 "$1" | awk '{print $1}'
}

fetch() {
  local url="$1" out="$2" sha="$3"
  if [[ -f "$out" ]]; then
    local got
    got="$(sha256_of "$out")"
    if [[ "$got" == "$sha" ]]; then
      echo "ok $(basename "$out")"
      return
    fi
    echo "hash mismatch, re-download $(basename "$out")"
  fi
  curl -L --fail -o "$out" "$url"
  local got
  got="$(sha256_of "$out")"
  if [[ "$got" != "$sha" ]]; then
    echo "sha256 mismatch for $out: got $got want $sha" >&2
    exit 1
  fi
  echo "fetched $(basename "$out")"
}

fetch "$YUNET_URL" "$DEST/face_detection_yunet_2023mar.onnx" "$YUNET_SHA"
fetch "$LM_URL" "$DEST/face_landmarker.task" "$LM_SHA"
echo "models ready in $DEST"
