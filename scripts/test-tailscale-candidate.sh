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
# These callers were changed by the fork to support an older
# Tailscale library. Each reviewed replacement is restricted to the
# exact locked blob and exact upstream blob, not a blanket checkout.
specs = [
    ("protocol/tailscale/tailcat_inbound.go", "3ddef2f8622af9e824a4696b09bc68d0f1370b9a", "2be55c6309f4b67bf2cf9c1c98475ba739bc3f2c"),
    ("protocol/tailscale/endpoint.go", "62d8d7de2522fc2fde9eacbe4aaa144c135dc20a", "b4a699ec062daae920375fbb7d9fc5c292a70d74"),
    ("protocol/tailscale/system_binding.go", "656d4141896a9c512acbf971747f302783a8c40e", "e68185f07d90cbd675e3d37321b42e20e47bb066"),
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
    elif path.endswith("/tailcat_inbound.go"):
        # The stable adapter already uses scoped inbound lifecycles. Restore
        # upstream Start(stage, scope), preserving routing/user/key behavior.
        candidate = own.replace(
            b"Start(stage adapter.StartStage) error",
            b"Start(stage adapter.StartStage, scope *adapter.Scope) error")
        candidate = candidate.replace(b"Context:      i.ctx,", b"Context:      scope.Context(),")
        candidate = candidate.replace(
            b"\terr = node.start()\n\tif err != nil {\n\t\tnode.close()\n\t\treturn err\n\t}",
            b"\tscope.Add(func() error {\n\t\tnode.close()\n\t\treturn nil\n\t})\n\terr = node.start()\n\tif err != nil {\n\t\treturn err\n\t}")
        close = b"func (i *TailcatInbound) Close() error {\n\tif i.node != nil {\n\t\ti.node.close()\n\t\ti.node = nil\n\t}\n\treturn nil\n}\n\n"
        assert candidate.count(close) == 1
        candidate = candidate.replace(close, b"")
        assert candidate == official
    elif path.endswith("/endpoint.go"):
        # Preserve fork HTTP transport, lifecycle and platform behavior;
        # replace only methods whose netstack API changed and initialize
        # the engine used by packet I/O and filtering before publication.
        candidate = own
        def edit(before, after):
            global candidate
            assert candidate.count(before) == 1, before
            candidate = candidate.replace(before, after)
        for imp in (b"github.com/sagernet/gvisor/pkg/tcpip",
                    b"github.com/sagernet/gvisor/pkg/tcpip/adapters/gonet",
                    b"github.com/sagernet/gvisor/pkg/tcpip/header",
                    b"github.com/sagernet/gvisor/pkg/tcpip/stack"):
            edit(b'\t"' + imp + b'"\n', b"")
        edit(b"stack             *stack.Stack", b"stack             *tun.Go")
        edit(b"t.server.TunDevice = wgTunDevice", b"t.server.Tun = wgTunDevice")
        edit(b"\tt.server.ListenPacketFunc = binding.listenPacket\n",
             b"\tt.server.ListenPacketFunc = binding.listenPacket\n\tt.server.DefaultRouteFunc = binding.defaultRoute\n")
        edit(b"\t\tmtu = uint32(tsTUN.DefaultTUNMTU())\n\t\t}\n",
             b"\t\tmtu = uint32(tsTUN.DefaultTUNMTU())\n\t\t}\n\t\tt.systemInterfaceMTU = mtu\n")
        edit(b"\tt.server.ExportLocalBackend().ExportEngine().(wgengine.ExportedUserspaceEngine).SetOnReconfigListener(t.onReconfig)",
             b"\tt.wgEngine = t.server.ExportLocalBackend().ExportEngine().(wgengine.ExportedUserspaceEngine)\n\tt.wgEngine.SetOnReconfigListener(t.onReconfig)")
        # Only the stack dialing/listening bodies differ between the
        # locked and official versions; their guards and DNS logic agree.
        for name, following in ((b"DialContext", b"listenPacketWithAddress"),
                                (b"listenPacketWithAddress", b"ListenPacketWithDestination")):
            start = b"func (t *Endpoint) " + name + b"("
            end = b"func (t *Endpoint) " + following + b"("
            def method(blob):
                i = blob.index(start)
                return blob[i:blob.index(end, i)]
            previous, current = method(candidate), method(official)
            assert b"gonet." in previous and b"t.stack." in current
            edit(previous, current)
        # Keep fork HTTP manager and explicit shutdown logic unchanged.
        assert b"httpClientManager.ResolveTransport" in candidate
        assert b"func (t *Endpoint) Close() error" in candidate
        candidate = subprocess.check_output(["gofmt"], input=candidate)
    elif path.endswith("/system_binding.go"):
        # The complete upstream binding carries default-route information
        # and per-node socket hooks instead of the old reduced interface.
        assert b"AutoRedirectOutputMarkFunc()" in own and b"AutoRedirectOutputMarkFunc()" in official
        assert b"AutoDetectInterfaceControl" in own and b"AutoDetectInterfaceControl" in official
        assert b"NewUDPEgressPool" in own and b"NewUDPEgressPool" in official
        assert b"func (b systemBinding) hooks(" in official
        candidate = official
    else:
        # Restore the actual upstream netstack handler and full per-node
        # socket hooks; the old callback adapters were for the old API.
        assert b"netmon.New(bus, node.logf, options.Hooks.Dialer)" in own
        assert b"netmon.New(bus, node.logf, options.Hooks)" in official
        assert b"netStack.Handler = options.Handler" in official
        assert b"options.MemoryPressure" in official
        candidate = official
    (sing / path).write_bytes(candidate)
    candidate_sha = git("hash-object", "--", path).decode().strip()
    changes.append({"path": path, "locked_blob": old, "upstream_blob": new,
                    "candidate_blob": candidate_sha})
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
# Verify build-tag selection, so Tailcat cannot silently disappear from
# the migration check even if its package still compiles without the tag.
cd "$sing"
go list -mod=mod -tags with_tailscale,with_tailcat -f '{{range .GoFiles}}{{println .}}{{end}}' ./protocol/tailscale > "$report/tailscale-candidate-files.txt"
grep -Fxq tailcat_inbound.go "$report/tailscale-candidate-files.txt"
grep -Fxq tailcat_outbound.go "$report/tailscale-candidate-files.txt"
grep -Fxq tailcat_node.go "$report/tailscale-candidate-files.txt"
CGO_ENABLED=1 go test -race -mod=mod -count=1 -timeout=5m -tags with_tailscale,with_tailcat -ldflags '-checklinkname=0' \
  ./protocol/tailscale/... 2>&1 | tee "$report/tailscale-candidate-test.log"
