#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/_common.sh"
need git
need go
need python3
report="$ROOT/.build/upstream-merge-audit"
sing="$WORK/hiddify-sing-box"
mdns="$sing/replace/MasterDnsVPN"
old=62e94f1515d4a5d3133789c90d8b224975b77ff9
target=a6b693698ee8246ca415a994bb6da40d66dddba7
assert_sha "$mdns" "$old"
git -C "$mdns" fetch --no-tags origin "$target"
# Preserve the locked module tree and its config/docs; import only the
# reviewed additive library change and three new probe/test files.
python3 - "$sing" "$mdns" "$report" "$old" "$target" <<'PY'
import json, subprocess, sys
from pathlib import Path
sing, mdns, report = map(Path, sys.argv[1:4])
old, target = sys.argv[4:]
def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args])
meta = json.loads((report / "hiddify-sing-box-reviewed.json").read_text())
tree = meta["reviewed_tree"]
paths = ("protocol/hiddify/masterdns/outbound.go", "protocol/hiddify/masterdns/h_masterdns_test.go")
assert set(paths) <= set(meta["reviewed_resolutions"])
assert not any(p.startswith("protocol/hiddify/masterdns/") for p in meta["remaining_conflicts"])
for path in paths:
    (sing / path).write_bytes(git(sing, "show", tree + ":" + path))
files = {
    "lib/lib.go": ("6b50239c1dc86e0958d1e2010322075641a334eb", "f65f57ddbfe10d93070ff759b84c41b959e08f33"),
    "internal/client/h_tunnel_probe.go": (None, "e595fe03e193aee8c363653bb455c80fc656dc66"),
    "lib/h_tunnel_probe_test.go": (None, "a8348b3f270872c3bca0a0a4b431ab827225001c"),
    "lib/testserver/testserver.go": (None, "2664ff1b2948f8ffa6a73d2486ca29ef8803758a"),
}
for path, (before, after) in files.items():
    assert git(mdns, "rev-parse", target + ":" + path).decode().strip() == after
    if before:
        assert git(mdns, "rev-parse", old + ":" + path).decode().strip() == before
    else:
        assert not git(mdns, "ls-tree", old, "--", path)
    dest = mdns / path
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(git(mdns, "show", target + ":" + path))
# Confirm exactly the seven inspected file differences in the two revisions.
actual = set(git(mdns, "diff", "--name-only", old, target).decode().splitlines())
assert actual == set(files) | {"README.MD", "client_config.toml.simple", "server_config.toml.simple"}, actual
(report / "masterdns-candidate-dependencies.json").write_text(json.dumps(
    {"locked": old, "upstream": target, "overlay": files}, indent=2) + "\n")
# Stage only in the disposable dependency checkout to retain added files
# in the generated patch; this cannot modify the stable root gitlinks.
git(mdns, "add", "--", *files)
(report / "masterdns-library-candidate.patch").write_bytes(
    git(mdns, "diff", "--cached", "--binary", "--full-index", old))
PY
(
  cd "$mdns"
  CGO_ENABLED=1 go test -race -mod=mod -count=1 -timeout=4m ./lib
) 2>&1 | tee "$report/masterdns-library-test.log"
(
  cd "$sing"
  CGO_ENABLED=1 go test -race -mod=mod -count=1 -timeout=5m -ldflags '-checklinkname=0' ./protocol/hiddify/masterdns
) 2>&1 | tee "$report/masterdns-candidate-test.log"
