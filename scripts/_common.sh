#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
WORK="$ROOT/.build/hiddify-core"
DIST="$ROOT/dist"
# shellcheck source=../sources.lock
source "$ROOT/sources.lock"

need() {
  command -v "$1" >/dev/null || { echo "Missing tool: $1" >&2; exit 1; }
}
assert_sha() {
  local actual
  actual="$(git -C "$1" rev-parse HEAD)"
  if [[ "$actual" != "$2" ]]; then
    echo "Source pin mismatch at $1: actual=$actual expected=$2" >&2
    exit 1
  fi
}
