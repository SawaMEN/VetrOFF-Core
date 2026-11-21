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
mkdir -p "$DIST"
cd "$WORK"
export GOTOOLCHAIN=auto
export GOOS="${GOOS:-linux}"
export GOARCH="${GOARCH:-amd64}"
export CGO_ENABLED="${CGO_ENABLED:-0}"
tags="$(make -s print-CLI_TAGS NAIVE=0)"
# tfo-go uses private Go netFD symbols on Windows which changed with Go 1.27.
# Excluding this opt-in tag enables the dependency's supported no-TFO fallback.
if [[ "$GOOS" == windows ]]; then
    tags="${tags//tfogo_checklinkname0,/}"
    tags="${tags//,tfogo_checklinkname0/}"
fi
[[ -n "$tags" ]] || { echo 'Upstream CLI tags were empty' >&2; exit 1; }
extension=''
[[ "$GOOS" == windows ]] && extension='.exe'
name="vetroff-core-$GOOS-$GOARCH$extension"
echo "Building $name with tags: $tags (CGO_ENABLED=$CGO_ENABLED)"
GOFLAGS=-mod=mod go build -trimpath -buildvcs=false -tags "$tags" -ldflags '-s -w' -o "$DIST/$name" ./cmd/main
(cd "$DIST" && sha256sum "$name" > "$name.sha256")
cat > "$DIST/$name.sources.txt" <<EOF
Hiddify Core: $CORE_URL @ $CORE_SHA
Hiddify sing-box: $SING_URL @ $SING_SHA
ray2sing: $RAY_URL @ $RAY_SHA
Applied patches: all three series
Build: $GOOS/$GOARCH CGO_ENABLED=$CGO_ENABLED
Cronet/Naive: disabled for standalone CLI
EOF
if [[ "$GOOS/$GOARCH" == linux/amd64 && "$(uname -s)" == Linux ]]; then
  "$DIST/$name" version | tee "$DIST/$name.version.txt"
  grep -q 'sing-box/vetroff-patch-v1 / ray2sing/vetroff-patch-v1' "$DIST/$name.version.txt"
fi
echo "Created $DIST/$name"
