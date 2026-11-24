#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/_common.sh"
need go
need python3
VETROFF_SOURCE_MODE=locked bash "$ROOT/scripts/prepare.sh"
verify_prepared_sources
tmp=$(mktemp -d "$ROOT/.build/anti-dpi-overlay.XXXXXX")
trap 'rm -rf -- "$tmp"' EXIT
# Work only in a disposable copy. Neither the verified prepared source nor
# the stable patch series / expected-tree hashes are modified.
cp -a "$WORK" "$tmp/core"
sing="$tmp/core/hiddify-sing-box"
candidate="$sing/common/adaptiveconnect"
mkdir "$candidate"
cp "$ROOT"/migration/adaptive-connect/*.go "$candidate/"
cp -a "$ROOT/migration/adaptive-connect/testdata" "$candidate/"
cp "$ROOT/migration/anti-dpi-overlay/selector_adapter.go.txt" "$sing/protocol/group/adaptive_selector.go"
cp "$ROOT/migration/anti-dpi-overlay/selector_adapter_test.go.txt" "$sing/protocol/group/adaptive_selector_test.go"
python3 - "$sing/protocol/group/selector.go" <<'PY'
import pathlib
import sys
p = pathlib.Path(sys.argv[1])
s = p.read_text()
def replace_once(old, new):
    global s
    if s.count(old) != 1:
        raise SystemExit("selector source changed; review overlay: " + old)
    s = s.replace(old, new, 1)
replace_once('"slices"', '"slices"\n\t"sync"')
replace_once('type Selector struct {', 'type Selector struct {\n\tadaptiveSelectionMu sync.Mutex\n\tadaptiveSelectionRevision uint64')
replace_once('func (s *Selector) SelectOutbound(tag string) bool {', '''func (s *Selector) SelectOutbound(tag string) bool {
    s.adaptiveSelectionMu.Lock()
    defer s.adaptiveSelectionMu.Unlock()
    return s.selectOutboundLocked(tag)
}

func (s *Selector) selectOutboundLocked(tag string) bool {''')
replace_once('if s.selected.Swap(detour) == detour {', '''s.adaptiveSelectionRevision++
    if s.selected.Swap(detour) == detour {''')
p.write_text(s)
PY
cd "$sing"
gofmt -w protocol/group/selector.go protocol/group/adaptive_selector*.go common/adaptiveconnect/*.go
export CGO_ENABLED=0
go test -mod=mod -count=1 ./common/adaptiveconnect ./protocol/group -run 'TestAntiDPI|TestController'
