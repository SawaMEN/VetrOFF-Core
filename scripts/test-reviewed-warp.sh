#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/_common.sh"
need git
need go
need python3
report="$ROOT/.build/upstream-merge-audit"
sing="$WORK/hiddify-sing-box"
# Test shared edge scanner and WARP classification on locked dependencies.
# The WARP endpoint integration is a separate migration gate.
python3 - "$sing" "$report" <<'PY'
import json, subprocess, sys
from pathlib import Path
sing, report = map(Path, sys.argv[1:])
meta = json.loads((report / "hiddify-sing-box-reviewed.json").read_text())
tree = meta["reviewed_tree"]
deleted = "hiddify/warpscan/scanner.go"
assert deleted in meta["deleted_resolutions"]
assert "common/cloudflare/h_warp_detect.go" in meta["reviewed_resolutions"]
def git(*args):
    return subprocess.check_output(["git", "-C", str(sing), *args])
assert not git("ls-tree", tree, "--", deleted)
paths = ["common/cloudflare/h_warp_detect.go", "option/h_warp_scanner.go",
         "option/h_edge_search.go"]
paths += git("ls-tree", "-r", "--name-only", tree, "hiddify/edgescan").decode().splitlines()
assert not set(paths) & set(meta["remaining_conflicts"])
assert any(p.endswith("/h_scanner_test.go") for p in paths)
for path in paths:
    dest = sing / path
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(git("show", tree + ":" + path))
assert b"EdgeSearchOptions" in (sing / "option/h_warp_scanner.go").read_bytes()
PY
cd "$sing"
CGO_ENABLED=1 go test -race -mod=mod -count=1 -timeout=4m -ldflags '-checklinkname=0' \
  ./hiddify/edgescan 2>&1 | tee "$report/edgescan-candidate-test.log"
# Repeatedly exercise the deterministic rating fixture; require every run
# to pass explicitly rather than silently accepting an unmatched regex.
CGO_ENABLED=1 go test -race -mod=mod -count=30 -timeout=2m -run '^TestH_ScanRatesBlocks$' -v -ldflags '-checklinkname=0' \
  ./hiddify/edgescan 2>&1 | tee "$report/edgescan-block-rating-test.log"
[[ $(grep -Fc -- '--- PASS: TestH_ScanRatesBlocks ' "$report/edgescan-block-rating-test.log") == 30 ]]
CGO_ENABLED=1 go test -race -mod=mod -count=1 -timeout=2m -run '^TestH_IsWARPHost$' -ldflags '-checklinkname=0' \
  ./common/cloudflare 2>&1 | tee "$report/warp-classification-test.log"
