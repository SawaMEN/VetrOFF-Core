#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/_common.sh"
need git
need python3
core=$1
[[ "$core" =~ ^[0-9a-f]{40}$ ]] || exit 2
report="$ROOT/.build/upstream-merge-audit"
mkdir -p "$report"
VETROFF_SOURCE_MODE=locked bash "$ROOT/scripts/prepare.sh"
verify_prepared_sources

audit_merge() {
  local name=$1 path=$2 base=$3 target=$4 expected=$5
  local patched ours status=0
  patched=$(git -C "$path" write-tree)
  [[ "$patched" == "$expected" ]] || { echo "Unverified patched tree: $name" >&2; return 2; }
  git -C "$path" fetch --quiet --no-tags --depth=1 origin "$target"
  ours=$(GIT_AUTHOR_NAME="VetrOFF audit" GIT_AUTHOR_EMAIL=audit@example.invalid \
    GIT_COMMITTER_NAME="VetrOFF audit" GIT_COMMITTER_EMAIL=audit@example.invalid \
    git -C "$path" commit-tree "$patched" -p "$base" -m "Verified patched source tree")
  git -C "$path" merge-tree --write-tree --name-only --no-messages -z \
    --merge-base="$base" "$target" "$ours" \
    > "$report/$name.nul" 2> "$report/$name.stderr" || status=$?
  if (( status > 1 )); then cat "$report/$name.stderr" >&2; return "$status"; fi
  python3 - "$report" "$name" "$base" "$target" "$patched" "$status" <<'PY'
from pathlib import Path
from collections import Counter
import json, sys, os
report, name, base, target, patched, status = sys.argv[1:]
root = Path(report)
fields = (root / (name + ".nul")).read_bytes().split(b"\0")
tree = fields.pop(0).decode("ascii")
if len(tree) != 40 or any(c not in "0123456789abcdef" for c in tree):
    raise SystemExit("Invalid merge result")
paths = sorted(set(os.fsdecode(p) for p in fields if p))
if (status == "0") != (not paths):
    raise SystemExit("Merge status does not match conflict paths")
data = dict(component=name, base=base, upstream=target, patched_tree=patched,
            tentative_tree=tree, conflicts=paths,
            by_directory=dict(Counter(p.split("/")[0] for p in paths)))
(root / (name + ".json")).write_text(json.dumps(data, ensure_ascii=True, indent=2)+"\n")
print(f"{name}: {len(paths)} conflicting paths; upstream {target}")
for directory, count in sorted(data["by_directory"].items(), key=lambda p: (-p[1],p[0])):
    print(f"  {directory}: {count}")
PY
}
audit_merge hiddify-core "$WORK" "$CORE_SHA" "$core" "$CORE_TREE"
sing=$(git -C "$WORK" rev-parse "$core:hiddify-sing-box")
audit_merge hiddify-sing-box "$WORK/hiddify-sing-box" "$SING_SHA" "$sing" "$SING_TREE"
python3 "$ROOT/scripts/resolve-upstream-metadata.py" "$WORK" "$report"
echo "Read-only diagnostic complete; no stable patch files or gitlinks modified."
