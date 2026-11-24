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
python3 "$ROOT/scripts/prepare-tailscale-callers.py" "$sing" "$report" "$target"
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
