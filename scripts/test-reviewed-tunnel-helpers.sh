#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/_common.sh"
need go
need python3
report="$ROOT/.build/upstream-merge-audit"
sing="$WORK/hiddify-sing-box"
# This runs on the audit's disposable locked checkout. Do not use its
# modified worktree for release builds or call prepare.sh again here.
python3 - "$sing" "$report" <<'PY'
from pathlib import Path
import json, subprocess, sys, shutil
sing, report = map(Path, sys.argv[1:])
meta = json.loads((report / "hiddify-sing-box-reviewed.json").read_text())
paths = ["hiddify/mtusearch/mtusearch.go", "hiddify/peerfallback/peerfallback.go", "transport/device/stack.go"]
for path in paths:
    if path not in meta["reviewed_resolutions"]:
        raise SystemExit("Missing reviewed candidate: " + path)
    source = report / "hiddify-sing-box-resolved" / path
    expected = subprocess.check_output(["git", "-C", str(sing), "show", meta["reviewed_tree"] + ":" + path])
    if source.read_bytes() != expected:
        raise SystemExit("Candidate blob mismatch: " + path)
    shutil.copyfile(source, sing / path)
PY
cp "$ROOT/migration/tests/peerfallback_close_test.go" "$sing/hiddify/peerfallback/vetroff_close_test.go"
cd "$sing"
# A scoped regression check with the known locked dependencies, not a
# complete integration build of the unresolved upstream candidate.
CGO_ENABLED=1 go test -race -mod=mod -count=1 -timeout=3m -ldflags '-checklinkname=0' \
  ./hiddify/mtusearch ./hiddify/peerfallback ./transport/device
