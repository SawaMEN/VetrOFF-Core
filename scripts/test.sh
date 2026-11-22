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
export CGO_ENABLED=0
tags=$(cli_tags)
go test -mod=mod -count=1 -tags "$tags" -ldflags '-checklinkname=0' \
  ./v2/config ./v2/config/paneluri ./v2/hcore ./v2/hcore/adminbgservice \
  github.com/hiddify/ray2sing/ray2sing github.com/hiddify/ray2sing/ray2sing_test
