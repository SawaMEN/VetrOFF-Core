#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/_common.sh"
need go
need python3
reuse=false
build=false
for arg in "$@"; do
  case "$arg" in
    --reuse-sources) reuse=true ;;
    --build-candidate) build=true ;;
    *) echo "Unknown argument: $arg" >&2; exit 2 ;;
  esac
done
if [[ "$build" == true && "$(uname -sm)" != "Linux x86_64" ]]; then
  echo "Experimental candidate build currently requires Linux x86_64" >&2
  exit 2
fi
if [[ "$reuse" != true ]]; then
  VETROFF_SOURCE_MODE=locked bash "$ROOT/scripts/prepare.sh"
fi
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
cp "$ROOT/migration/anti-dpi-overlay/urltest_adapter.go.txt" "$sing/protocol/group/adaptive_urltest.go"
cp "$ROOT/migration/anti-dpi-overlay/urltest_adapter_test.go.txt" "$sing/protocol/group/adaptive_urltest_test.go"
cp "$ROOT/migration/anti-dpi-overlay/selector_lifecycle.go.txt" "$sing/protocol/group/adaptive_lifecycle.go"
cp "$ROOT/migration/anti-dpi-overlay/selector_lifecycle_test.go.txt" "$sing/protocol/group/adaptive_lifecycle_test.go"
cp "$ROOT/migration/anti-dpi-overlay/cli_adaptive.go.txt" "$tmp/core/cmd/adaptive_cli.go"
cp "$ROOT/migration/anti-dpi-overlay/cli_adaptive_test.go.txt" "$tmp/core/cmd/adaptive_cli_test.go"
cp "$ROOT/migration/anti-dpi-overlay/selector_pool.go.txt" "$sing/protocol/group/adaptive_pool.go"
cp "$ROOT/migration/anti-dpi-overlay/selector_pool_test.go.txt" "$sing/protocol/group/adaptive_pool_test.go"
cp "$ROOT/migration/anti-dpi-overlay/cli_mux.go.txt" "$tmp/core/cmd/adaptive_mux.go"
cp "$ROOT/migration/anti-dpi-overlay/cli_mux_test.go.txt" "$tmp/core/cmd/adaptive_mux_test.go"
cp "$ROOT/migration/anti-dpi-overlay/selector_mux.go.txt" "$sing/protocol/group/adaptive_mux.go"
cp "$ROOT/migration/anti-dpi-overlay/selector_mux_test.go.txt" "$sing/protocol/group/adaptive_mux_test.go"
cp "$ROOT/migration/anti-dpi-overlay/selector_history.go.txt" "$sing/protocol/group/adaptive_history.go"
cp "$ROOT/migration/anti-dpi-overlay/selector_history_test.go.txt" "$sing/protocol/group/adaptive_history_test.go"
cp "$ROOT/migration/anti-dpi-overlay/selector_diagnostic.go.txt" "$sing/protocol/group/adaptive_diagnostic.go"
cp "$ROOT/migration/anti-dpi-overlay/selector_diagnostic_test.go.txt" "$sing/protocol/group/adaptive_diagnostic_test.go"
cp "$ROOT/migration/anti-dpi-overlay/selector_physical.go.txt" "$sing/protocol/group/adaptive_physical.go"
cp "$ROOT/migration/anti-dpi-overlay/selector_physical_linux.go.txt" "$sing/protocol/group/adaptive_physical_linux.go"
cp "$ROOT/migration/anti-dpi-overlay/selector_physical_stub.go.txt" "$sing/protocol/group/adaptive_physical_stub.go"
cp "$ROOT/migration/anti-dpi-overlay/selector_physical_test.go.txt" "$sing/protocol/group/adaptive_physical_test.go"
cp "$ROOT/migration/anti-dpi-overlay/cli_diagnostic.go.txt" "$tmp/core/cmd/adaptive_diagnostic.go"
cp "$ROOT/migration/anti-dpi-overlay/cli_diagnostic_test.go.txt" "$tmp/core/cmd/adaptive_diagnostic_test.go"
cp "$ROOT/migration/anti-dpi-overlay/selector_fingerprint.go.txt" "$sing/protocol/group/adaptive_fingerprint.go"
cp "$ROOT/migration/anti-dpi-overlay/selector_fingerprint_test.go.txt" "$sing/protocol/group/adaptive_fingerprint_test.go"
python3 - "$sing/protocol/group/selector.go" "$sing/protocol/group/urltest.go" "$tmp/core/cmd/cmd_sing_run.go" <<'PY'
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
replace_once('type Selector struct {', 'type Selector struct {\n\tadaptiveSelectionMu sync.Mutex\n\tadaptiveSelectionRevision uint64\n\tadaptiveLifecycleMu sync.Mutex\n\tadaptiveOwner *AdaptiveSelectorOwner')
replace_once('func (s *Selector) SelectOutbound(tag string) bool {', '''func (s *Selector) SelectOutbound(tag string) bool {
    s.adaptiveLifecycleMu.Lock()
    defer s.adaptiveLifecycleMu.Unlock()
    if s.adaptiveOwner != nil {
        return s.adaptiveOwner.manualSelect(tag)
    }
    s.adaptiveSelectionMu.Lock()
    defer s.adaptiveSelectionMu.Unlock()
    return s.selectOutboundLocked(tag)
}

func (s *Selector) selectOutboundLocked(tag string) bool {''')
replace_once('if s.selected.Swap(detour) == detour {', '''s.adaptiveSelectionRevision++
    if s.selected.Swap(detour) == detour {''')
replace_once('func (s *Selector) Close() error {', 'func (s *Selector) Close() error {\n\ts.DisableAdaptive()')
replace_once('func (s *Selector) DialContext(ctx context.Context, network string, destination M.Socksaddr) (net.Conn, error) {', '''func (s *Selector) DialContext(ctx context.Context, network string, destination M.Socksaddr) (net.Conn, error) {
    if conn, err, handled := s.adaptiveBudgetDial(ctx, network, destination); handled {
        return conn, err
    }''')
p.write_text(s)
p = pathlib.Path(sys.argv[2])
s = p.read_text()
replace_once('delay, testErr := urlTestLinks(testCtx, links, detour)', '''delay, testErr, handled := adaptiveURLTestProbe(testCtx, links, detour)
                    if !handled {
                        delay, testErr = urlTestLinks(testCtx, links, detour)
                    }''')
replace_once('if testResult.err != nil {\n\t\t\t\t\tif b.ctx.Err() != nil {', '''if testResult.err == errAdaptiveStale {
                    return nil, nil
                }
                if testResult.err != nil {
                    if b.ctx.Err() != nil {''')
p.write_text(s)
p = pathlib.Path(sys.argv[3])
s = p.read_text()
replace_once('func create() (*box.Box, context.CancelFunc, error) {', '''func create() (*box.Box, context.CancelFunc, error) {
    adaptivePolicy, adaptiveEnabled, err := readAdaptiveCLI(adaptivePolicyPath, adaptiveSelectorTag, adaptiveProbeURL, adaptiveInterval)
    if err != nil {
        return nil, nil, err
    }''')
replace_once('ctx, cancel := context.WithCancel(globalCtx)', '''muxPair, err := prepareAdaptiveMux(options.Outbounds, adaptiveSelectorTag, adaptiveMux, adaptiveMuxPrefer)
    if err != nil {
        return nil, nil, err
    }
    if muxPair != nil && !adaptiveEnabled {
        return nil, nil, E.New("adaptive MUX requires enabled local policy")
    }
    ctx, cancel := context.WithCancel(globalCtx)''')
replace_once('return instance, cancel, nil', '''if err := startAdaptiveCLI(ctx, instance, adaptivePolicy, adaptiveEnabled, muxPair); err != nil {
        cancel()
        _ = instance.Close()
        return nil, nil, E.Cause(err, "start Anti-DPI")
    }
    return instance, cancel, nil''')
p.write_text(s)
PY
cd "$sing"
gofmt -w protocol/group/selector.go protocol/group/urltest.go protocol/group/adaptive_selector*.go protocol/group/adaptive_urltest*.go protocol/group/adaptive_lifecycle*.go protocol/group/adaptive_pool*.go protocol/group/adaptive_mux*.go protocol/group/adaptive_history*.go protocol/group/adaptive_diagnostic*.go protocol/group/adaptive_physical*.go protocol/group/adaptive_fingerprint*.go common/adaptiveconnect/*.go
export CGO_ENABLED=0
go test -mod=mod -count=1 -ldflags '-checklinkname=0' ./common/adaptiveconnect ./protocol/group -run 'TestAntiDPI|TestController'

cd "$tmp/core"
gofmt -w cmd/adaptive_cli*.go cmd/adaptive_mux*.go cmd/adaptive_diagnostic*.go cmd/cmd_sing_run.go
need make
tags=$(cli_tags)
go test -mod=mod -count=1 -tags "$tags" -ldflags '-checklinkname=0' ./cmd -run 'TestAntiDPICLI'

# Build only after the same disposable overlay passes integration tests.
# Never invoke the stable release builder or write into its output directory.
if [[ "$build" == true ]]; then
  need sha256sum
  need git
  version=$(< "$ROOT/VERSION")
  [[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo 'Invalid VERSION' >&2; exit 1; }
  revision=$(git -C "$ROOT" rev-parse HEAD)
  short=${revision:0:12}
  candidate_version="$version-anti-dpi.$short"
  out="$ROOT/dist/anti-dpi/$revision"
  mkdir -p "$out"
  name="vetroff-core-anti-dpi-linux-amd64"
  export GOOS=linux GOARCH=amd64 CGO_ENABLED=0
  go build -mod=mod -trimpath -buildvcs=false -tags "$tags" \
    -ldflags "-s -w -checklinkname=0 -X github.com/hiddify/hiddify-core/v2/hcommon/constants.Version=$candidate_version" \
    -o "$out/$name" ./cmd/main
  "$out/$name" version > "$out/$name.version.txt"
  grep -F "$candidate_version" "$out/$name.version.txt"
  "$out/$name" srun --help > "$out/$name.help.txt"
  for flag in anti-dpi-policy anti-dpi-selector anti-dpi-probe-url anti-dpi-interval anti-dpi-pool anti-dpi-mux anti-dpi-history anti-dpi-diagnostic anti-dpi-diagnostic-fingerprint anti-dpi-diagnostic-ip; do
    grep -Eq -- "--$flag([[:space:]]|$)" "$out/$name.help.txt"
  done
  python3 "$ROOT/scripts/smoke-anti-dpi-cli.py" "$out/$name" | tee "$out/$name.smoke.txt"
  cp "$ROOT/scripts/smoke-anti-dpi-cli.py" "$out/smoke-anti-dpi-cli.py"
  cp "$ROOT/migration/adaptive-connect/testdata/policy-v1.json" "$out/adaptive-connect-v1.disabled.json"
  cp "$ROOT/migration/adaptive-connect/README.md" "$out/ANTI-DPI.md"
  cp "$ROOT/migration/adaptive-connect/TESTING-RU.md" "$out/TESTING-RU.md"
  cat > "$out/$name.sources.txt" <<PROVENANCE
Experimental Anti-DPI CLI; not a stable release
Version: $candidate_version
Repository revision: $revision
Hiddify Core: $CORE_URL @ $CORE_SHA
Hiddify sing-box: $SING_URL @ $SING_SHA
ray2sing: $RAY_URL @ $RAY_SHA
Patched baseline trees: core=$CORE_TREE sing-box=$SING_TREE ray2sing=$RAY_TREE
Overlay: migration/anti-dpi-overlay and migration/adaptive-connect at $revision
Build: linux/amd64 CGO_ENABLED=0
CLI tags: $tags
Toolchain: $(go version)
PROVENANCE
  (cd "$out" && sha256sum "$name" adaptive-connect-v1.disabled.json > SHA256SUMS && sha256sum -c SHA256SUMS)
  echo "Experimental artifact prepared: $out"
fi
