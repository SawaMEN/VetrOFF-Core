#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT/migration/adaptive-connect"
# No upstream checkout, network, or third-party Go dependencies required.
GO111MODULE=off go test -race -count=1 .
