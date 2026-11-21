#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/_common.sh"
need git
mkdir -p "$ROOT/.build"
rm -rf -- "$WORK" "$ROOT/.build/prepared-sources.lock"

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
  if [[ "$actual" != "$base" ]]; then
    git -C "$path" fetch --quiet --depth=1 origin "$base"
    args+=(--3way)
  fi
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ -z "$line" || "$line" == \#* ]] && continue
    [[ "$line" =~ ^[A-Za-z0-9_.-]+\.patch$ ]] || { echo "Invalid patch name: $line" >&2; return 1; }
    patch="$ROOT/patches/$name/$line"
    echo "Applying $name/$line"
    git -C "$path" apply "${args[@]}" "$patch" || {
      echo "Could not apply $name/$line to $actual" >&2
      return 1
    }
  done < "$ROOT/patches/$name/series"
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

apply_series hiddify-core "$WORK" "$core" "$CORE_SHA"
core_tree=$(verify_patched_tree "$WORK" "$core" "$CORE_SHA" "$CORE_TREE")
checkout_source "$WORK/hiddify-sing-box" "$SING_URL" "$sing"
apply_series hiddify-sing-box "$WORK/hiddify-sing-box" "$sing" "$SING_SHA"
sing_tree=$(verify_patched_tree "$WORK/hiddify-sing-box" "$sing" "$SING_SHA" "$SING_TREE")
git -C "$WORK/hiddify-sing-box" -c 'url.https://github.com/.insteadOf=git@github.com:' \
  submodule update --init --recursive --jobs 4
checkout_source "$WORK/ray2sing" "$RAY_URL" "$ray"
apply_series ray2sing "$WORK/ray2sing" "$ray" "$RAY_SHA"
ray_tree=$(verify_patched_tree "$WORK/ray2sing" "$ray" "$RAY_SHA" "$RAY_TREE")

cat > "$ROOT/.build/prepared-sources.lock" <<LOCK
CORE_SHA=$core
SING_SHA=$sing
RAY_SHA=$ray
CORE_TREE=$core_tree
SING_TREE=$sing_tree
RAY_TREE=$ray_tree
LOCK
echo 'Applied all three patch series and verified the source trees for locked revisions.'
