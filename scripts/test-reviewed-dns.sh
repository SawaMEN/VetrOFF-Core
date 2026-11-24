#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/_common.sh"
need go
need python3
report="$ROOT/.build/upstream-merge-audit"
sing="$WORK/hiddify-sing-box"
# The reviewed DNS changes deliberately retain the locked GroupTransport
# interface. Use the complete upstream rating package from the reviewed tree.
python3 - "$sing" "$report" <<'PY'
import json, subprocess, sys
from pathlib import Path
sing, report = map(Path, sys.argv[1:])
meta = json.loads((report / "hiddify-sing-box-reviewed.json").read_text())
tree = meta["reviewed_tree"]
files = ("dns/transport/h_group_sequential.go", "dns/transport/h_group_test.go")
assert set(files) <= set(meta["reviewed_resolutions"])
assert not any(p.startswith("dns/transport/") or p.startswith("hiddify/rating/") for p in meta["remaining_conflicts"])
def git(*args):
    return subprocess.check_output(["git", "-C", str(sing), *args])
assert git("rev-parse", tree + ":dns/transport/group.go") == git("rev-parse", ":dns/transport/group.go")
for path in files:
    (sing / path).write_bytes(git("show", tree + ":" + path))
for entry in git("ls-tree", "-r", "--name-only", tree, "hiddify/rating").decode().splitlines():
    dest = sing / entry
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(git("show", tree + ":" + entry))
PY
cd "$sing"
CGO_ENABLED=1 go test -race -mod=mod -count=1 -timeout=4m -ldflags '-checklinkname=0' \
  ./hiddify/rating ./dns/transport 2>&1 | tee "$report/dns-candidate-test.log"
