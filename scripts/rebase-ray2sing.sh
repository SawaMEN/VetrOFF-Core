#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
[[ $(git status --porcelain) == "" ]] || { echo "Start from a clean checkout" >&2; exit 1; }
old=$(git rev-parse HEAD:ray2sing)
new=${1:?Usage: rebase-ray2sing.sh NEW_UPSTREAM_SHA}
[[ "$new" =~ ^[0-9a-f]{40}$ && "$new" != "$old" ]] || { echo "Invalid or unchanged upstream SHA" >&2; exit 1; }
dir="$ROOT/patches/ray2sing"
tmp=$(mktemp -d)
trap 'rm -rf -- "$tmp"' EXIT
git init -q "$tmp/source"
git -C "$tmp/source" remote add origin https://github.com/hiddify/ray2sing.git
git -C "$tmp/source" fetch --no-tags --depth=64 origin "$new"
git -C "$tmp/source" fetch --no-tags --depth=1 origin "$old"
git -C "$tmp/source" merge-base --is-ancestor "$old" "$new" || { echo "Not a verified fast-forward upstream update" >&2; exit 1; }
git -C "$tmp/source" checkout -q --detach "$old"
while IFS= read -r patch || [[ -n "$patch" ]]; do
  [[ -z "$patch" || "$patch" == \#* ]] && continue
  [[ "$patch" =~ ^[A-Za-z0-9_.-]+\.patch$ ]] || exit 1
  git -C "$tmp/source" apply --index --whitespace=nowarn "$dir/$patch"
done < "$dir/series"
old_tree=$(git -C "$tmp/source" write-tree)
[[ "$old_tree" == "$(< "$dir/expected-tree")" ]] || { echo "Original fork tree mismatch" >&2; exit 1; }
git -C "$tmp/source" config user.name "VetrOFF maintenance"
git -C "$tmp/source" config user.email "maintenance@example.invalid"
ours=$(printf "%s\n" "Audited VetrOFF ray2sing tree" | git -C "$tmp/source" commit-tree "$old_tree" -p "$old")
if ! merged=$(git -C "$tmp/source" merge-tree --write-tree --merge-base="$old" "$new" "$ours"); then
  printf "%s\n" "$merged" >&2
  echo "Manual conflict resolution required; original patch and pin unchanged" >&2
  exit 1
fi
[[ "$merged" =~ ^[0-9a-f]{40}$ ]] || { echo "Invalid merged tree" >&2; exit 1; }
git -C "$tmp/source" diff --binary --full-index --no-renames "$new" "$merged" > "$tmp/rebased.patch"
git -C "$tmp/source" reset --hard -q "$new"
if [[ -s "$tmp/rebased.patch" ]]; then
  git -C "$tmp/source" apply --index --whitespace=nowarn "$tmp/rebased.patch"
fi
[[ $(git -C "$tmp/source" write-tree) == "$merged" ]] || { echo "Recreated tree mismatch" >&2; exit 1; }
# For this update the existing personal-fork changes must remain nonempty.
[[ -s "$tmp/rebased.patch" ]] || { echo "Unexpected empty fork delta; review manually" >&2; exit 1; }
[[ "$(< "$dir/series")" == "0001-fork-baseline.patch" ]] || { echo "Unexpected series structure" >&2; exit 1; }
cp "$tmp/rebased.patch" "$dir/0001-fork-baseline.patch"
printf "%s\n" "$merged" > "$dir/expected-tree"
git update-index --cacheinfo "160000,$new,ray2sing"
printf "ray2sing old=%s\nnew=%s\noriginal-patched-tree=%s\nmerged-tree=%s\n" "$old" "$new" "$old_tree" "$merged"
git -C "$tmp/source" diff --stat "$old_tree" "$merged"
