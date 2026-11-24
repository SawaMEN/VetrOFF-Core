#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/_common.sh"
need git
need go
need python3
need make
report="$ROOT/.build/upstream-merge-audit"
candidate=$(mktemp -d "$ROOT/.build/combined-candidate.XXXXXX")
trap 'rm -rf -- "$candidate"' EXIT
verify_prepared_sources

# Materialize the audited trees separately, not over the stable worktree.
# Explicitly allow only documentation, generated schema and manifest/gitlink
# conflicts at this point. Any new unresolved source code blocks the probe.
python3 - "$WORK" "$report" "$candidate" "$RAY_TREE" <<'PY'
import json
import os
from pathlib import Path
import subprocess
import sys

work, report, target = map(Path, sys.argv[1:4])
ray_tree = sys.argv[4]
core = json.loads((report / "hiddify-core-reviewed.json").read_text())
sing = json.loads((report / "hiddify-sing-box-reviewed.json").read_text())
links = {
    "replace/MasterDnsVPN": "a6b693698ee8246ca415a994bb6da40d66dddba7",
    "replace/sing": "b40ea077780dbc1babef7d035d4b0fea8d9ad6f2",
    "replace/soar": "e18587bc9fb62f326124d1f3eb0ceb31708bd077",
    "replace/tailscale": "4138b20dfb9f5852d043abd8bf9da6c29d5ee53b",
}
assert set(core["remaining_conflicts"]) <= {"go.mod"}
allowed = {"README.md", "docs/schema.json", "go.mod", "test/go.mod"} | set(links)
assert all(p in allowed or p.startswith("docs/") for p in sing["remaining_conflicts"]), sing["remaining_conflicts"]

def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args])

def archive(repo, tree, destination):
    destination.mkdir(parents=True, exist_ok=True)
    with subprocess.Popen(["git", "-C", str(repo), "archive", tree], stdout=subprocess.PIPE) as proc:
        subprocess.run(["tar", "-xf", "-", "-C", str(destination)], stdin=proc.stdout, check=True)
        proc.stdout.close()
        assert proc.wait() == 0

sing_src = work / "hiddify-sing-box"
sing_dest = target / "hiddify-sing-box"
archive(work, core["reviewed_tree"], target)
archive(sing_src, sing["reviewed_tree"], sing_dest)
# ray2sing is the verified patched revision, not yet a new release pin.
archive(work / "ray2sing", ray_tree, target / "ray2sing")
pins = {}
for line in git(sing_src, "ls-tree", "-r", sing["reviewed_tree"]).decode().splitlines():
    info, name = line.split("\t", 1)
    mode, kind, sha = info.split()
    if mode != "160000":
        continue
    assert kind == "commit" and name.startswith("replace/"), name
    if name in links:
        sha = links[name]
        assert git(sing_src, "rev-parse", sing["upstream"] + ":" + name).decode().strip() == sha
    pins[name] = sha
assert set(links) <= set(pins)
for name, sha in sorted(pins.items()):
    destination = sing_dest / name
    destination.mkdir(parents=True, exist_ok=True)
    url = subprocess.check_output(["git", "config", "-f", str(sing_dest / ".gitmodules"), "--get",
                                   "submodule." + name + ".url"], text=True).strip()
    assert url.startswith("https://github.com/"), (name, url)
    subprocess.run(["git", "init", "--quiet", str(destination)], check=True)
    git(destination, "remote", "add", "origin", url)
    git(destination, "fetch", "--quiet", "--no-tags", "--depth=1", "origin", sha)
    git(destination, "checkout", "--quiet", "--detach", sha)
    assert git(destination, "rev-parse", "HEAD").decode().strip() == sha
    subprocess.run(["git", "-C", str(destination), "-c", "url.https://github.com/.insteadOf=git@github.com:",
                    "submodule", "update", "--init", "--recursive", "--jobs", "4"], check=True)
for module, directory in (("hiddify-core", target), ("hiddify-sing-box", sing_dest), ("hiddify-sing-box-test", sing_dest / "test")):
    manifest = (report / (module + "-go.mod.candidate")).read_bytes()
    (directory / "go.mod").write_bytes(manifest)
    env = dict(os.environ, GOTOOLCHAIN="local", GOPROXY="off", GOSUMDB="off")
    parsed = json.loads(subprocess.check_output(["go", "mod", "edit", "-json"], cwd=directory, env=env))
    for replacement in parsed.get("Replace", []):
        path = replacement["New"]["Path"]
        if path.startswith("."):
            local = (directory / path).resolve()
            assert local.is_relative_to(target.resolve()), path
            assert (local / "go.mod").is_file(), (module, path)
summary = {"core_tree": core["reviewed_tree"], "sing_box_tree": sing["reviewed_tree"],
           "ray2sing_patched_tree": ray_tree, "candidate_dependency_pins": pins,
           "unresolved_core": core["remaining_conflicts"], "unresolved_sing_box": sing["remaining_conflicts"],
           "status": "experimental combined probe; not a release"}
(report / "combined-candidate-provenance.json").write_text(json.dumps(summary, indent=2) + "\n")
PY

export CGO_ENABLED=0
cd "$candidate"
tags=$(cli_tags)
printf '%s\n' "$tags" > "$report/combined-candidate-tags.txt"
# Do not suppress compile or module failures. Keep each independent probe's
# log and attempt the other probes so one failure does not hide the rest.
failed=0
probe() {
  local name=$1
  shift
  if ! "$@" 2>&1 | tee "$report/combined-$name.log"; then
    failed=1
    echo "Combined candidate failed: $name" >&2
  fi
}
probe core-modules go list -mod=mod -m all
probe core-cli go build -mod=mod -trimpath -buildvcs=false -tags "$tags" -ldflags '-checklinkname=0' -o "$candidate/vetroff-core-probe" ./cmd/main
probe core-tests go test -mod=mod -count=1 -timeout=6m -tags "$tags" -ldflags '-checklinkname=0' ./v2/config ./v2/hcore ./v2/hcore/adminbgservice
cd "$candidate/hiddify-sing-box"
probe sing-modules go list -mod=mod -m all
probe sing-tests go test -mod=mod -count=1 -timeout=6m -tags "$tags,with_xdrive" -ldflags '-checklinkname=0' \
  -run '^TestH_(NetworkResetReachesTheDNSServers|Psiphon|XDrive|EveryPeerBridgeHasTheSessionsHandlers)' \
  ./route ./protocol/psiphon ./transport/call/telemost ./transport/v2rayxdrive .
# The integration test module must use exactly the reviewed root
# replacements. Compile it without executing tests that require containers,
# remote services or privileged network operations.
cd "$candidate/hiddify-sing-box/test"
probe test-modules go list -mod=mod -m all
probe test-compile go test -mod=mod -exec /bin/true -run '^$' -tags "$tags" -ldflags '-checklinkname=0' ./...
cd "$candidate/hiddify-sing-box"
# The checked-in schema is generated output. Regenerate from the actual
# candidate's registries, not by merging JSON conflict markers. Validate
# local references and the presence of the fork compatibility options.
schema="$report/combined-schema.json"
probe schema-generate go run -mod=mod -tags "$tags,with_xdrive" -ldflags '-checklinkname=0' ./cmd/sing-box schema -o "$schema"
if [[ -s "$schema" ]]; then
  probe schema-validate python3 - "$schema" <<'PY'
import json, sys
from pathlib import Path
schema = json.loads(Path(sys.argv[1]).read_text())
assert isinstance(schema, dict)
names, refs = set(), set()
def walk(value):
    if isinstance(value, dict):
        props = value.get("properties", {})
        if isinstance(props, dict):
            names.update(props)
        if isinstance(value.get("$ref"), str):
            refs.add(value["$ref"])
        for child in value.values():
            walk(child)
    elif isinstance(value, list):
        for child in value:
            walk(child)
walk(schema)
required = {"conduit_pairing_id", "fallback_address", "final_mask",
            "storage_dialer", "smart_pool", "keep_top"}
assert required <= names, ("missing compatibility fields", sorted(required - names))
for ref in refs:
    if not ref.startswith("#/"):
        continue
    node = schema
    for part in ref[2:].split("/"):
        node = node[part.replace("~1", "/").replace("~0", "~")]
print(f"Schema validated: {len(names)} property names, {len(refs)} references")
PY
else
  failed=1
  echo "Schema generation did not produce a JSON artifact" >&2
fi
exit "$failed"
