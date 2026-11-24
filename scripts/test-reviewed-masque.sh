#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/_common.sh"
need git
need go
need python3
report="$ROOT/.build/upstream-merge-audit"
sing="$WORK/hiddify-sing-box"
# Exercise just the reviewed packet path on the locked MASQUE scanner.
# Full scanner lifecycle changes require the separate WARP migration.
python3 - "$sing" "$report" <<'PY'
import json, subprocess, sys
from pathlib import Path
sing, report = map(Path, sys.argv[1:])
meta = json.loads((report / "hiddify-sing-box-reviewed.json").read_text())
tree = meta["reviewed_tree"]
path = "protocol/masque/outbound.go"
assert path in meta["reviewed_resolutions"]
def git(*args):
    return subprocess.check_output(["git", "-C", str(sing), *args])
own = (sing / path).read_bytes()
assert git("hash-object", "--", path).decode().strip() == "c486e4016f816bf6b826e761e5a13a4f39f6962b"
new = git("show", tree + ":" + path)
signature = b"func (w *Outbound) ListenPacketWithDestination("
def method(blob):
    i = blob.index(signature)
    return blob[i:blob.index(b"\n}\n", i)+3]
assert new.count(b"iponly.NewPacketConn(") == 3
candidate = own.replace(method(own), method(new), 1)
candidate = candidate.replace(b'\t"github.com/sagernet/sing-box/common/dialer"\n',
    b'\t"github.com/sagernet/sing-box/common/dialer"\n\t"github.com/sagernet/sing-box/common/iponly"\n', 1)
(sing / path).write_bytes(subprocess.check_output(["gofmt"], input=candidate))
test = "protocol/masque/h_packet_test.go"
assert git("rev-parse", tree + ":" + test).decode().strip() == "8f2df80bbc3c84948ae912964920b234a1e0e39d"
assert git("rev-parse", tree + ":common/iponly/conn.go").decode().strip() == "86e9e99b32cd1d682e64fcb1ff0c1160d1aa4520"
(sing / test).write_bytes(git("show", tree + ":" + test))
(report / "masque-packet-candidate.patch").write_bytes(git("diff", "--binary", "--full-index", "--", path))
PY
cd "$sing"
CGO_ENABLED=1 go test -race -mod=mod -count=1 -timeout=4m -tags with_gvisor -ldflags '-checklinkname=0' \
  ./protocol/masque -run '^TestH_OutboundDropsPacketsToDomains$' -v 2>&1 | tee "$report/masque-packet-candidate-test.log"
grep -Fq -- '--- PASS: TestH_OutboundDropsPacketsToDomains' "$report/masque-packet-candidate-test.log"
