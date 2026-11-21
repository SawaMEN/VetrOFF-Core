#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/_common.sh"
need git
latest=$(git ls-remote --exit-code "$CORE_URL" refs/heads/main | cut -f1)
[[ "$latest" =~ ^[0-9a-f]{40}$ ]] || { echo 'Could not resolve original Core main' >&2; exit 1; }
printf '%s\n' "$latest"
