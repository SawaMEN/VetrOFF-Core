#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/_common.sh"
need go
need python3
report="$ROOT/.build/upstream-merge-audit"
sing="$WORK/hiddify-sing-box"
# Overlay whole reviewed packages, including clean merges and new upstream
# tests. Never compile unresolved files or silently fall back to old code.
python3 - "$sing" "$report" <<'PY'
import json, subprocess, sys, tempfile, shutil
from pathlib import Path
sing, report = map(Path, sys.argv[1:])
meta = json.loads((report / "hiddify-sing-box-reviewed.json").read_text())
groups = ("transport/awg", "transport/wireguard", "hiddify/sendqueue",
          "hiddify/mtusearch", "hiddify/peerfallback")
remaining = [p for p in meta["remaining_conflicts"]
             if any(p == g or p.startswith(g + "/") for g in groups)]
if remaining:
    raise SystemExit("Unresolved tunnel package conflicts: " + repr(remaining))
tree = meta["reviewed_tree"]
required = ("transport/awg/bind.go", "transport/awg/device.go",
            "transport/awg/h_bind_later.go", "transport/awg/h_path.go",
            "transport/wireguard/client_bind.go", "transport/wireguard/endpoint.go")
if not set(required) <= set(meta["reviewed_resolutions"]):
    raise SystemExit("Missing reviewed tunnel resolutions")
with tempfile.TemporaryDirectory() as tmp:
    archive = Path(tmp) / "packages.tar"
    subprocess.run(["git", "-C", str(sing), "archive", "--format=tar",
                    "-o", str(archive), tree, *groups], check=True)
    extracted = Path(tmp) / "src"
    extracted.mkdir()
    subprocess.run(["tar", "-xf", str(archive), "-C", str(extracted)], check=True)
    for group in groups:
        if (sing / group).exists():
            shutil.rmtree(sing / group)
        shutil.copytree(extracted / group, sing / group)
# Record the dependency pins from the reviewed tree, not moving branches.
pins = {}
for path in ("replace/amneziawg-go", "replace/wireguard-go", "replace/sing-tun",
             "replace/sing", "replace/gvisor", "replace/tailscale"):
    line = subprocess.check_output(["git", "-C", str(sing), "ls-tree", tree, path], text=True)
    mode, kind, sha = line.split("\t")[0].split()
    if mode != "160000" or kind != "commit":
        raise SystemExit("Invalid candidate dependency: " + path)
    pins[path] = sha
(report / "tunnel-candidate-dependencies.json").write_text(json.dumps(pins, indent=2) + "\n")
PY
# The reviewed WireGuard-go revision is a descendant of the locked revision.
# AWG already uses the reviewed upstream revision. Keep other dependencies
# locked until their own compatibility work is complete.
wg="$sing/replace/wireguard-go"
old=e6786162612a1691bb9c1f1d63eb7141e00f6aac
target=32f4b5369c2dff702384168612f83355a09ec177
[[ "$(git -C "$wg" rev-parse HEAD)" == "$old" ]]
[[ "$(git -C "$sing/replace/amneziawg-go" rev-parse HEAD)" == a2ae165a1b3f062ce9dd3dc3353ebd1c591847a5 ]]
git -C "$wg" fetch --no-tags origin "$target"
git -C "$wg" merge-base --is-ancestor "$old" "$target"
git -C "$wg" checkout --detach "$target"
[[ "$(git -C "$wg" rev-parse HEAD)" == "$target" ]]
cp "$ROOT/migration/tests/peerfallback_close_test.go" "$sing/hiddify/peerfallback/vetroff_close_test.go"
cd "$sing"
CGO_ENABLED=1 go test -race -mod=mod -count=1 -timeout=5m -ldflags '-checklinkname=0' \
  ./hiddify/sendqueue ./hiddify/mtusearch ./hiddify/peerfallback \
  ./transport/awg ./transport/wireguard
