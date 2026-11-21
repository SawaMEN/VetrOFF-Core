#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/_common.sh"
need git
mkdir -p "$ROOT/.build"
rm -rf -- "$WORK"
git clone --filter=blob:none --no-checkout --depth 1 --branch main "$CORE_URL" "$WORK"
git -C "$WORK" fetch --depth 1 origin "$CORE_SHA"
git -C "$WORK" -c advice.detachedHead=false checkout --detach "$CORE_SHA"
# ray2sing's upstream gitlink uses an SSH URL. Rewrite it to public HTTPS.
git -C "$WORK" -c 'url.https://github.com/.insteadOf=git@github.com:' \
  submodule update --init --recursive --jobs 4
assert_sha "$WORK" "$CORE_SHA"
assert_sha "$WORK/hiddify-sing-box" "$SING_SHA"
assert_sha "$WORK/ray2sing" "$RAY_SHA"

apply_series() {
  local name="$1" path="$2" line patch series="$ROOT/patches/$1/series"
  [[ -f "$series" ]] || { echo "Missing patch series $series" >&2; exit 1; }
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%%#*}"
    line="${line//[[:space:]]/}"
    [[ -z "$line" ]] && continue
    [[ "$line" =~ ^[A-Za-z0-9_.-]+\.patch$ ]] || { echo "Unsafe patch name: $line" >&2; exit 1; }
    patch="$ROOT/patches/$name/$line"
    [[ -f "$patch" ]] || { echo "Missing patch file: $patch" >&2; exit 1; }
    echo "Applying $name/$line"
    git -C "$path" apply --check --whitespace=error "$patch"
    git -C "$path" apply --whitespace=error "$patch"
  done < "$series"
}
apply_series hiddify-sing-box "$WORK/hiddify-sing-box"
apply_series ray2sing "$WORK/ray2sing"
apply_series hiddify-core "$WORK"
grep -q VetroffCoreFlavor "$WORK/hiddify-sing-box/constant/vetroff_core.go"
grep -q VetroffCoreFlavor "$WORK/ray2sing/ray2sing/vetroff_core.go"
grep -q VetroffCoreFlavor "$WORK/cmd/cmd_version.go"
echo 'All 3 official source revisions verified; all 3 patch stacks applied.'
