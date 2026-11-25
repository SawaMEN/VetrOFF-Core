#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
WORK="$ROOT/.build/hiddify-core"
DIST="$ROOT/dist"
submodule_commit() {
  local mode kind sha path
  read -r mode kind sha path < <(git -C "$ROOT" ls-tree HEAD -- "$1")
  if [[ "$mode" != 160000 || "$kind" != commit || "$path" != "$1" ]]; then
    echo "Missing Git submodule $1 in the VetrOFF-Core checkout" >&2
    return 1
  fi
  printf '%s\n' "$sha"
}

submodule_url() {
  git config --file "$ROOT/.gitmodules" --get "submodule.$1.url"
}

CORE_URL=$(submodule_url hiddify-core)
SING_URL=$(submodule_url hiddify-sing-box)
RAY_URL=$(submodule_url ray2sing)
CORE_SHA=$(submodule_commit hiddify-core)
SING_SHA=$(submodule_commit hiddify-sing-box)
RAY_SHA=$(submodule_commit ray2sing)
CORE_TREE=$(< "$ROOT/patches/hiddify-core/expected-tree")
SING_TREE=$(< "$ROOT/patches/hiddify-sing-box/expected-tree")
RAY_TREE=$(< "$ROOT/patches/ray2sing/expected-tree")

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
  assert_sha "$WORK/hiddify-sing-box/replace/MasterDnsVPN" "$MASTERDNS_SHA"
  [[ "$(git -C "$WORK/hiddify-sing-box/replace/MasterDnsVPN" write-tree)" == "$MASTERDNS_TREE" ]]
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
