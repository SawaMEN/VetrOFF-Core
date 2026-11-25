#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/_common.sh"
need go
need make
if [[ "${1:-}" != "--reuse-sources" ]]; then
  bash "$ROOT/scripts/prepare.sh"
fi
verify_prepared_sources
cd "$WORK"
export CGO_ENABLED=0 GOWORK=off GOTOOLCHAIN=local
tags=$(cli_tags)
report="$ROOT/.build/v5-regressions"
mkdir -p "$report"
pattern='^(TestH_HotReloadLogLevel|TestH_BalancerKeepsConnectionsThroughATentativeMiss|TestH_MonitoringMissOfACachedMemberIsSeenByTheGroups|TestH_IPv6UnavailableItem|TestH_IPv6UnavailableItemOnlyRoutedVia|TestH_SetMemoryLimitKeepsTheOOMKillersLimit|TestH_PsiphonActiveTunnelReachesScanner|TestH_StatusDeepSearchNotifies|TestH_StoreLastWay|TestH_TryLastWay|TestH_Better|TestH_Search|TestH_SearchLimit|TestH_StatusDeepSearch|TestH_BalancerInterruptsOnlyDeadMember|TestH_HotReloadInbounds|TestH_MASQUELastWay|TestH_MASQUESearchNoise|TestH_MASQUENoiseCandidates|TestH_PsiphonRemembersWay|TestH_PsiphonUseLastWay|TestH_PsiphonSearch|TestH_PsiphonDirectDetour|TestH_PsiphonSearchKeepsTrying|TestH_WARPLastWay|TestH_WARPLastTunnel)$'
go test -mod=readonly -count=1 -timeout=8m -tags "$tags" -ldflags '-checklinkname=0' -run "$pattern" -v github.com/sagernet/sing-box github.com/sagernet/sing-box/common/monitoring github.com/sagernet/sing-box/protocol/group/balancer github.com/sagernet/sing-box/route/rule github.com/sagernet/sing-box/experimental/libbox github.com/sagernet/sing-box/protocol/psiphon github.com/sagernet/sing-box/hiddify/edgescan github.com/sagernet/sing-box/protocol/masque github.com/sagernet/sing-box/protocol/warp | tee "$report/regressions.log"
for test in TestH_HotReloadLogLevel TestH_BalancerKeepsConnectionsThroughATentativeMiss TestH_MonitoringMissOfACachedMemberIsSeenByTheGroups TestH_IPv6UnavailableItem TestH_IPv6UnavailableItemOnlyRoutedVia TestH_SetMemoryLimitKeepsTheOOMKillersLimit TestH_PsiphonActiveTunnelReachesScanner TestH_StatusDeepSearchNotifies TestH_StoreLastWay TestH_TryLastWay TestH_Better TestH_Search TestH_SearchLimit TestH_StatusDeepSearch TestH_BalancerInterruptsOnlyDeadMember TestH_HotReloadInbounds TestH_MASQUELastWay TestH_MASQUESearchNoise TestH_MASQUENoiseCandidates TestH_PsiphonRemembersWay TestH_PsiphonUseLastWay TestH_PsiphonSearch TestH_PsiphonDirectDetour TestH_PsiphonSearchKeepsTrying TestH_WARPLastWay TestH_WARPLastTunnel; do
  grep -Fq -- "--- PASS: $test " "$report/regressions.log"
done
# Compile the WARP package without MASQUE as well: the last-mode
# guard must use the correct implementation in both build variants.
stub_tags=$(printf '%s' "$tags" | tr ', ' '\n\n' | grep -vx 'with_masque' | paste -sd, -)
go test -mod=readonly -count=1 -timeout=5m -tags "$stub_tags" -ldflags '-checklinkname=0' -run '^TestH_WARPLastTunnel$' -v github.com/sagernet/sing-box/protocol/warp | tee "$report/warp-stub.log"
grep -Fq -- '--- PASS: TestH_WARPLastTunnel ' "$report/warp-stub.log"
