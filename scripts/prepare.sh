#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/_common.sh"
need git
mkdir -p "$ROOT/.build"
rm -rf -- "$WORK" "$ROOT/.build/prepared-sources.lock"
rm -f -- "$ROOT/.build/"*.patch.log "$ROOT/.build/patch-report.md"

checkout_source() {
  local path=$1 url=$2 sha=$3
  git init --quiet "$path"
  git -C "$path" remote add origin "$url"
  git -C "$path" fetch --quiet --depth=1 origin "$sha"
  git -C "$path" checkout --quiet --detach "$sha"
}

case "${VETROFF_SOURCE_MODE:-locked}" in
  locked)
    core=$CORE_SHA
    ;;
  upstream)
    core=${VETROFF_UPSTREAM_CORE_SHA:-}
    [[ "$core" =~ ^[0-9a-f]{40}$ ]] || { echo 'Provide VETROFF_UPSTREAM_CORE_SHA' >&2; exit 1; }
    ;;
  *)
    echo 'Unknown VETROFF_SOURCE_MODE' >&2
    exit 1
    ;;
esac

checkout_source "$WORK" "$CORE_URL" "$core"
gitlink() {
  git -C "$WORK" rev-parse "HEAD:$1"
}

sing=$(gitlink hiddify-sing-box)
if [[ "${VETROFF_SOURCE_MODE:-locked}" == locked ]]; then
  if [[ "$sing" != "$SING_SHA" ]]; then
    echo 'The sing-box submodule must match the original Core gitlink' >&2
    exit 1
  fi
  ray=$RAY_SHA
else
  ray=$(gitlink ray2sing)
fi

apply_series() {
  local name=$1 path=$2 actual=$3 base=$4 line patch
  local args=(--index --whitespace=nowarn)
  local series="$ROOT/patches/$name/series"
  local log="$ROOT/.build/$name.patch.log"
  : > "$log"
  [[ -f "$series" ]] || { echo "Missing patch series: $series" >&2; return 1; }
  if [[ "$actual" != "$base" ]]; then
    git -C "$path" fetch --quiet --depth=1 origin "$base" || return 1
    args+=(--3way)
  fi
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ -z "$line" || "$line" == \#* ]] && continue
    [[ "$line" =~ ^[A-Za-z0-9_.-]+\.patch$ ]] || { echo "Invalid patch name: $line" >&2; return 1; }
    patch="$ROOT/patches/$name/$line"
    echo "Applying $name/$line"
    if ! git -C "$path" apply "${args[@]}" "$patch" 2>&1 | tee -a "$log"; then
      echo "Could not apply $name/$line to $actual" >&2
      git -C "$path" diff --name-only --diff-filter=U >&2
      return 1
    fi
  done < "$series"
}

verify_patched_tree() {
  local path=$1 source=$2 pinned_source=$3 expected_tree=$4 tree
  tree=$(git -C "$path" write-tree)
  if [[ "$source" == "$pinned_source" && "$tree" != "$expected_tree" ]]; then
    echo "Patched tree mismatch at $path: actual=$tree expected=$expected_tree" >&2
    return 1
  fi
  printf '%s\n' "$tree"
}

# A conflict in one component should not hide conflicts in the others.
failed_components=()
apply_series hiddify-core "$WORK" "$core" "$CORE_SHA" || failed_components+=(hiddify-core)
checkout_source "$WORK/hiddify-sing-box" "$SING_URL" "$sing"
apply_series hiddify-sing-box "$WORK/hiddify-sing-box" "$sing" "$SING_SHA" || failed_components+=(hiddify-sing-box)
checkout_source "$WORK/ray2sing" "$RAY_URL" "$ray"
apply_series ray2sing "$WORK/ray2sing" "$ray" "$RAY_SHA" || failed_components+=(ray2sing)
if (( ${#failed_components[@]} > 0 )); then
  report="$ROOT/.build/patch-report.md"
  {
    echo "# VetrOFF-Core upstream patch failures"
    echo
    echo "Source mode: ${VETROFF_SOURCE_MODE:-locked}"
    echo "Hiddify Core revision: $core"
    echo "Affected components: ${failed_components[*]}"
    echo
    for component in "${failed_components[@]}"; do
      echo "## $component"
      echo
      echo "First 40 patch errors/conflicts (complete log in workflow artifacts):"
      echo '```text'
      awk '/^(error:|fatal:|Applied patch .* with conflicts)/ { print; if (++n == 40) exit }' "$ROOT/.build/$component.patch.log"
      echo '```'
      echo
    done
  } > "$report"
  echo "Source preparation failed: ${failed_components[*]}; report: $report" >&2
  exit 1
fi

core_tree=$(verify_patched_tree "$WORK" "$core" "$CORE_SHA" "$CORE_TREE")
sing_tree=$(verify_patched_tree "$WORK/hiddify-sing-box" "$sing" "$SING_SHA" "$SING_TREE")
ray_tree=$(verify_patched_tree "$WORK/ray2sing" "$ray" "$RAY_SHA" "$RAY_TREE")
git -C "$WORK/hiddify-sing-box" -c 'url.https://github.com/.insteadOf=git@github.com:' \
  submodule update --init --recursive --jobs 4

# The nested dependency overlay is part of the released source tree.
mdns="$WORK/hiddify-sing-box/replace/MasterDnsVPN"
mdns_base=$(< "$ROOT/patches/masterdns/base-revision")
mdns_expected=$(< "$ROOT/patches/masterdns/expected-tree")
assert_sha "$mdns" "$mdns_base"
apply_series masterdns "$mdns" "$mdns_base" "$mdns_base"
mdns_tree=$(verify_patched_tree "$mdns" "$mdns_base" "$mdns_base" "$mdns_expected")

cat > "$ROOT/.build/prepared-sources.lock" <<LOCK
CORE_SHA=$core
SING_SHA=$sing
RAY_SHA=$ray
CORE_TREE=$core_tree
SING_TREE=$sing_tree
RAY_TREE=$ray_tree
MASTERDNS_SHA=$mdns_base
MASTERDNS_TREE=$mdns_tree
LOCK
echo 'Applied all source and nested dependency patch series and verified the source trees for locked revisions.'
