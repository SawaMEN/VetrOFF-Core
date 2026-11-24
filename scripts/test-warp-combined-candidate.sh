#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/_common.sh"
need git
need go
need python3
report="$ROOT/.build/upstream-merge-audit"
sing="$WORK/hiddify-sing-box"
# A combined candidate build, separate from the isolated package tests.
# Retain the locked module manifests and nested pins (apart from the
# already-reviewed WireGuard transport test pin). Copy only named packages.
python3 - "$sing" "$report" <<'PY'
import json, subprocess, sys, shutil, tempfile
from pathlib import Path
sing, report = map(Path, sys.argv[1:])
meta = json.loads((report / "hiddify-sing-box-reviewed.json").read_text())
tree = meta["reviewed_tree"]
groups = ("protocol/warp", "protocol/masque", "hiddify/warpscan",
          "hiddify/edgescan", "hiddify/rating", "common/cloudflare",
          "common/iponly", "transport/masque", "transport/device",
          "protocol/awg", "protocol/wireguard")
extra = ("adapter/h_deep_search.go", "adapter/h_detour.go", "adapter/h_status.go",
         "adapter/h_lazy.go", "adapter/experimental.go",
         "option/h_edge_search.go", "option/h_warp_scanner.go")
blocked = [p for p in meta["remaining_conflicts"] if p in extra or any(p.startswith(g + "/") for g in groups)]
assert not blocked, blocked
def git(*args):
    return subprocess.check_output(["git", "-C", str(sing), *args])
with tempfile.TemporaryDirectory() as tmp:
    archive = Path(tmp) / "candidate.tar"
    subprocess.run(["git", "-C", str(sing), "archive", "--format=tar",
                    "-o", str(archive), tree, *groups, *extra], check=True)
    dest = Path(tmp) / "src"
    dest.mkdir()
    subprocess.run(["tar", "-xf", str(archive), "-C", str(dest)], check=True)
    for group in groups:
        if (sing / group).exists():
            shutil.rmtree(sing / group)
        shutil.copytree(dest / group, sing / group)
    for path in extra:
        shutil.copyfile(dest / path, sing / path)
# These dependencies are reviewed, not inferred from the old checkout.
assert "adapter/h_detour.go" in meta["clean_adjustments"]
assert "adapter/experimental.go" in meta["reviewed_resolutions"]
assert b"OutboundStatus(outbound)" in (sing / "adapter/h_detour.go").read_bytes()
assert b"type HeldLazy interface" in (sing / "adapter/h_lazy.go").read_bytes()
assert b"LastGood uint16" in (sing / "adapter/experimental.go").read_bytes()
assert not (sing / "hiddify/warpscan/scanner.go").exists()
assert (sing / "hiddify/warpscan/h_engine.go").exists()
assert b"scanner.Close()" in (sing / "protocol/masque/outbound.go").read_bytes()
(report / "warp-combined-candidate.json").write_text(json.dumps(
    {"tree": tree, "packages": groups, "extra": extra,
     "reviewed_adjustment": "adapter/h_detour.go: OutboundStatus compatibility",
     "tags": ["with_gvisor", "with_masque", "with_awg"]}, indent=2) + "\n")
PY
cp "$ROOT/migration/tests/adapter_detour_compat_test.go" "$sing/adapter/vetroff_detour_compat_test.go"
cd "$sing"
tags=with_gvisor,with_masque,with_awg
CGO_ENABLED=1 go test -race -mod=mod -count=1 -timeout=2m -tags "$tags" -ldflags '-checklinkname=0' \
  -run '^TestVetrOFFPathStatusCompatibility
go list -mod=mod -tags "$tags" -f '{{range .GoFiles}}{{println .}}{{end}}' ./protocol/warp > "$report/warp-combined-files.txt"
grep -Fxq endpoint_warp_masque.go "$report/warp-combined-files.txt"
grep -Fxq endpoint_warp_awg.go "$report/warp-combined-files.txt"
CGO_ENABLED=1 go test -race -mod=mod -count=1 -timeout=5m -tags "$tags" -ldflags '-checklinkname=0' \
  -run '^$' ./protocol/warp ./protocol/masque ./hiddify/warpscan ./hiddify/edgescan ./transport/masque \
  2>&1 | tee "$report/warp-combined-build.log"
CGO_ENABLED=1 go test -race -mod=mod -count=1 -timeout=4m -tags "$tags" -ldflags '-checklinkname=0' \
  -run '^TestH_OutboundDropsPacketsToDomains$' -v ./protocol/masque 2>&1 | tee "$report/warp-combined-packet-test.log"
grep -Fq -- '--- PASS: TestH_OutboundDropsPacketsToDomains' "$report/warp-combined-packet-test.log"
 -v ./adapter 2>&1 | tee "$report/adapter-detour-test.log"
grep -Fq -- '--- PASS: TestVetrOFFPathStatusCompatibility' "$report/adapter-detour-test.log"
go list -mod=mod -tags "$tags" -f '{{range .GoFiles}}{{println .}}{{end}}' ./protocol/warp > "$report/warp-combined-files.txt"
grep -Fxq endpoint_warp_masque.go "$report/warp-combined-files.txt"
grep -Fxq endpoint_warp_awg.go "$report/warp-combined-files.txt"
CGO_ENABLED=1 go test -race -mod=mod -count=1 -timeout=5m -tags "$tags" -ldflags '-checklinkname=0' \
  -run '^$' ./protocol/warp ./protocol/masque ./hiddify/warpscan ./hiddify/edgescan ./transport/masque \
  2>&1 | tee "$report/warp-combined-build.log"
CGO_ENABLED=1 go test -race -mod=mod -count=1 -timeout=4m -tags "$tags" -ldflags '-checklinkname=0' \
  -run '^TestH_OutboundDropsPacketsToDomains$' -v ./protocol/masque 2>&1 | tee "$report/warp-combined-packet-test.log"
grep -Fq -- '--- PASS: TestH_OutboundDropsPacketsToDomains' "$report/warp-combined-packet-test.log"
