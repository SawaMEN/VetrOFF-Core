#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/_common.sh"
need git
for item in CORE SING RAY; do
  var_url="${item}_URL"; var_sha="${item}_SHA"
  url="${!var_url}"; pinned="${!var_sha}"
  branch=main
  [[ "$item" == SING ]] && branch=v5
  remote="$(git ls-remote "$url" "refs/heads/$branch" | cut -f1)"
  [[ -n "$remote" ]] || { echo "Could not get upstream $item" >&2; exit 1; }
  echo "$item current=$pinned remote=$remote"
done
echo 'Update pins together: hiddify-core gitlinks must match both submodules.'
