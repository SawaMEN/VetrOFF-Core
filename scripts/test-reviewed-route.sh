#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/_common.sh"
need git
need go
need python3
report="$ROOT/.build/upstream-merge-audit"
sing="$WORK/hiddify-sing-box"
prefix=network
mode=overlay
if [[ $# -ne 0 ]]; then
  [[ $# -eq 2 && "$1" == --combined ]] || { echo 'Usage: test-reviewed-route.sh [--combined candidate-sing-box]' >&2; exit 2; }
  mode=combined
  sing="$(realpath "$2")"
  prefix=combined-network
  [[ "$sing" == "$ROOT"/.build/combined-candidate.*/hiddify-sing-box ]] || { echo 'Expected disposable combined tree' >&2; exit 2; }
fi
tmp=$(mktemp -d)
cp "$sing/go.mod" "$tmp/go.mod"
cp "$sing/go.sum" "$tmp/go.sum"
trap 'cp "$tmp/go.mod" "$sing/go.mod"; cp "$tmp/go.sum" "$sing/go.sum"; rm -rf "$tmp"' EXIT
args=()
if [[ "$mode" == overlay ]]; then
python3 - "$sing" "$report" "$tmp" <<'PY'
import json, subprocess, sys
from pathlib import Path
sing, report, tmp = map(Path, sys.argv[1:])
meta = json.loads((report / "hiddify-sing-box-reviewed.json").read_text())
paths = ("route/network.go", "route/router.go", "route/conn.go", "route/dns.go", "route/h_network_reset_test.go", "route/h_read_pace.go", "route/h_read_pace_test.go", "route/h_wake_pace.go", "route/h_wake_pace_test.go", "route/h_wake_pace_unix.go", "route/h_wake_pace_unix_test.go", "route/h_wake_pace_other.go", "route/h_path_rtt.go")
assert set(paths[:3]) <= set(meta["reviewed_resolutions"])
assert not set(paths) & set(meta["remaining_conflicts"])
files = {}
for path in paths:
    target = tmp / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(subprocess.check_output(["git", "-C", str(sing), "show", meta["reviewed_tree"] + ":" + path]))
    files[str(sing / path)] = str(target)
(tmp / "overlay.json").write_text(json.dumps({"Replace": files}))
PY
  args=(-overlay "$tmp/overlay.json")
fi
cd "$sing"
failed=0
normal="$report/$prefix-reset-candidate-test.log"
disabled="$report/$prefix-pacing-disabled-candidate-test.log"
HIDDIFY_NO_READ_PACE=0 CGO_ENABLED=1 go test -race -mod=mod "${args[@]}" -count=1 -timeout=3m -ldflags '-checklinkname=0' \
  -run '^TestH_(NetworkResetReachesTheDNSServers|ReadPace|WakePace|PacedRawConn|TrackedDatagramSocket|TrackedSockets|PathRoundTrip|BusyDatagramSocket)' -v ./route 2>&1 | tee "$normal" || failed=1
for test in TestH_NetworkResetReachesTheDNSServers TestH_TrackedDatagramSocketKeepsItsReader TestH_WakePaceNeedsALongPath TestH_ReadPaceLeavesAnExchangeAlone TestH_PacedRawConnWaitsAfterAWakeUp TestH_ReadPaceStopsWhenTheDownloadSlows; do
  if ! grep -Fq -- "--- PASS: $test " "$normal"; then
    echo "Required routing regression did not pass: $test" >&2
    failed=1
  fi
done
# Always run disabled-mode check even when enabled-mode test fails.
HIDDIFY_NO_READ_PACE=1 CGO_ENABLED=1 go test -race -mod=mod "${args[@]}" -count=1 -timeout=3m -ldflags '-checklinkname=0' \
  -run '^TestH_ReadPaceLeavesASlowConnectionAlone$' -v ./route 2>&1 | tee "$disabled" || failed=1
grep -Fq -- '--- PASS: TestH_ReadPaceLeavesASlowConnectionAlone ' "$disabled" || failed=1
exit "$failed"
