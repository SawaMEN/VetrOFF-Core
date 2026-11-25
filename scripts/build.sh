#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/_common.sh"
need go
need make
if [[ "${1:-}" != "--reuse-sources" ]]; then
  bash "$ROOT/scripts/prepare.sh"
fi
verify_prepared_sources

version=$(< "$ROOT/VERSION")
[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-rc[0-9]*)?$ ]] || { echo 'Invalid VERSION' >&2; exit 1; }
mkdir -p "$DIST"
cd "$WORK"
export GOOS="${GOOS:-linux}"
export GOARCH="${GOARCH:-amd64}"
export CGO_ENABLED="${CGO_ENABLED:-0}"
tags=$(cli_tags)
extension=''
strip='-s -w'
if [[ "$GOOS" == windows ]]; then
  extension=.exe
  strip=-w
fi
name="vetroff-core-$GOOS-$GOARCH$extension"
ldflags="$strip -checklinkname=0 -X github.com/hiddify/hiddify-core/v2/hcommon/constants.Version=$version"
echo "Building $name with tags: $tags (CGO_ENABLED=$CGO_ENABLED)"
go build -mod=readonly -trimpath -buildvcs=false -tags "$tags" -ldflags "$ldflags" -o "$DIST/$name" ./cmd/main
(cd "$DIST" && sha256sum "$name" > "$name.sha256")
cat > "$DIST/$name.sources.txt" <<PROVENANCE
Version: $version
Hiddify Core: $CORE_URL @ $CORE_SHA
Hiddify sing-box: $SING_URL @ $SING_SHA
ray2sing: $RAY_URL @ $RAY_SHA
Patched trees: core=$CORE_TREE sing-box=$SING_TREE ray2sing=$RAY_TREE
Build: $GOOS/$GOARCH CGO_ENABLED=$CGO_ENABLED
Cronet/Naive: disabled for standalone CLI
PROVENANCE
if [[ "$GOOS/$GOARCH" == linux/amd64 && "$(uname -sm)" == 'Linux x86_64' ]]; then
  "$DIST/$name" version | tee "$DIST/$name.version.txt"
  grep -Fx "VetrOFF-Core $version" "$DIST/$name.version.txt"
fi
