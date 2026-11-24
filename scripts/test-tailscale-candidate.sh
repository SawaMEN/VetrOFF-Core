#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/_common.sh"
need git
need go
need python3
report="$ROOT/.build/upstream-merge-audit"
sing="$WORK/hiddify-sing-box"
# The earlier tests intentionally alter the disposable checkout. The
# Tailscale probe starts from the verified locked source again.
VETROFF_SOURCE_MODE=locked bash "$ROOT/scripts/prepare.sh"
verify_prepared_sources
# Re-fetch the exact original revision: prepare.sh rebuilt the object store.
target=a26e58b376987a3305b201ec87fbc8918f620ad3
git -C "$sing" fetch --quiet --no-tags --depth=1 origin "$target"
python3 - "$sing" "$report" "$target" <<'PY'
import json, subprocess, sys
from pathlib import Path
sing, report = map(Path, sys.argv[1:3])
target = sys.argv[3]
def git(*args):
    return subprocess.check_output(["git", "-C", str(sing), *args])
# These three callers were changed by the fork to support an older
# Tailscale library. Each reviewed replacement is restricted to the
# exact locked blob and exact upstream blob, not a blanket checkout.
specs = [
    ("protocol/tailscale/port.go", "aa5b3667064375d2ff5a06222e95a3b6f8d5ff53", "fb382504e3cdc626ea0abac7e0920a730465f89d"),
    ("protocol/tailscale/tailcat_node.go", "600da67f8cc10253193703d1cb6bc8915b955e12", "ff77324639337e401920a39d0f90de2f44b408e9"),
    ("protocol/tailscale/taildrop_send.go", "ecf74c949c521faca33b413c050fd94c25197c59", "cc71f49d253da1ff52687c3a1cfa0fbc01ef5062"),
]
changes = []
for path, old, new in specs:
    assert git("rev-parse", ":" + path).decode().strip() == old, path
    assert git("rev-parse", target + ":" + path).decode().strip() == new, path
    own = git("cat-file", "blob", old)
    official = git("cat-file", "blob", new)
    if path.endswith("/port.go"):
        candidate = own.replace(b"t.server.ExportLocalBackend().ExportFilter().Load()", b"t.wgEngine.GetFilter()")
        assert candidate == official
    elif path.endswith("/taildrop_send.go"):
        candidate = own.replace(b"localBackend.ExportSelf()", b"nodeBackend.Self()").replace(b"localBackend.ExportPeers()", b"nodeBackend.Peers()")
        candidate = candidate.replace(b'!peer.Valid() || peer.Hostinfo().OS()', b'!peer.Valid() || !peer.Hostinfo().Valid() || peer.Hostinfo().OS()')
        assert candidate == official
    else:
        # Restore the actual upstream netstack handler and full per-node
        # socket hooks; the old callback adapters were for the old API.
        assert b"netmon.New(bus, node.logf, options.Hooks.Dialer)" in own
        assert b"netmon.New(bus, node.logf, options.Hooks)" in official
        assert b"netStack.Handler = options.Handler" in official
        assert b"options.MemoryPressure" in official
        candidate = official
    (sing / path).write_bytes(candidate)
    changes.append({"path": path, "locked_blob": old, "candidate_blob": new})
(report / "tailscale-caller-adjustments.json").write_text(json.dumps(changes, indent=2) + "\n")
(report / "tailscale-caller-adjustments.patch").write_bytes(git("diff", "--binary", "--full-index", "--", *(p[0] for p in specs)))
PY
# Compare the original dependency histories without merging or modifying
# the stable gitlinks. The target and locked histories have diverged.
ts="$sing/replace/tailscale"
old=1f5488c9848da9d4f1104cc29998535939f23df8
new=4138b20dfb9f5852d043abd8bf9da6c29d5ee53b
assert_sha "$ts" "$old"
git -C "$ts" fetch --no-tags origin "$new"
git -C "$ts" log --left-right --cherry-mark --no-merges --format='%m %H %s' "$new...$old" > "$report/tailscale-commit-audit.txt"
git -C "$ts" diff --stat "$old" "$new" > "$report/tailscale-tree-diff.txt"
# This compatibility probe uses the target dependency only. Other
# dependencies remain locked so any further required API changes are visible.
git -C "$ts" checkout --detach "$new"
assert_sha "$ts" "$new"
cd "$sing"
CGO_ENABLED=1 go test -race -mod=mod -count=1 -timeout=5m -tags with_tailscale -ldflags '-checklinkname=0' \
  ./protocol/tailscale/... 2>&1 | tee "$report/tailscale-candidate-test.log"
