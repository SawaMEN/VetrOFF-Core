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

verify_prepared_sources() {
  source "$ROOT/.build/prepared-sources.lock"
  assert_sha "$WORK" "$CORE_SHA"
  assert_sha "$WORK/hiddify-sing-box" "$SING_SHA"
  assert_sha "$WORK/ray2sing" "$RAY_SHA"
}

cli_tags() {
  local tags
  tags="$(make -s print-CLI_TAGS NAIVE=0)"
  if [[ "${GOOS:-linux}" == windows ]]; then
    tags="${tags//tfogo_checklinkname0,/}"
    tags="${tags//,tfogo_checklinkname0/}"
  fi
  [[ -n "$tags" ]] || { echo 'Upstream CLI tags were empty' >&2; return 1; }
  printf '%s\n' "$tags"
}
