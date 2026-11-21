#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/_common.sh"
need go
need make
if [[ "${1:-}" != "--reuse-sources" ]]; then
    bash "$ROOT/scripts/prepare.sh"
fi
assert_sha "$WORK" "$CORE_SHA"
assert_sha "$WORK/hiddify-sing-box" "$SING_SHA"
assert_sha "$WORK/ray2sing" "$RAY_SHA"
cd "$WORK"
export GOTOOLCHAIN=auto
export CGO_ENABLED=0
tags="$(make -s print-CLI_TAGS NAIVE=0)"
GOFLAGS=-mod=mod go test -count=1 -tags "$tags" github.com/sagernet/sing-box/include -run '^TestVetroffOutboundRegistered$'
GOFLAGS=-mod=mod go test -count=1 -tags "$tags" github.com/hiddify/ray2sing/ray2sing -run '^TestVetroff'
echo 'Native Vetroff outbound registration and ray2sing link parsing tested.'
