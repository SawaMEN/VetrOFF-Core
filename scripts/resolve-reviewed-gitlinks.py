#!/usr/bin/env python3
"""Stage reviewed nested gitlinks and paired callers in the diagnostic tree only."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

repo, report = map(Path, sys.argv[1:])
meta_file = report / "hiddify-sing-box-reviewed.json"
meta = json.loads(meta_file.read_text())
assert meta["base"] == "004371d6be2395ee4379054a8cf113f339dbe231"
assert meta["upstream"] == "a26e58b376987a3305b201ec87fbc8918f620ad3"

# Both are add/add conflicts relative to the locked original revision.
# Historical migration/dependencies.md records the sing cached-connection
# regression and the equivalent Soar change. The locked fork gitlinks
# still match those historical inputs; combined run 45488228887 exercised
# these exact upstream targets. MasterDNS is a conditional resolution:
# its staged upstream gitlink is not sufficient without the readiness patch.
# Keep the Tailscale gitlink open pending review of its divergent history.
choices = {
    "replace/MasterDnsVPN": ("62e94f1515d4a5d3133789c90d8b224975b77ff9",
                             "a6b693698ee8246ca415a994bb6da40d66dddba7"),
    "replace/sing": ("e1f78e792da34ce99fe2279729dea3b0a09efa9d",
                     "b40ea077780dbc1babef7d035d4b0fea8d9ad6f2"),
    "replace/soar": ("71ae9fb0b1ac9d99b8a6f9b5e254b92fe328f179",
                     "e18587bc9fb62f326124d1f3eb0ceb31708bd077"),
}


def git(*args, env=None):
    return subprocess.check_output(["git", "-C", str(repo), *args], env=env)


def entry(tree, path):
    raw = git("ls-tree", tree, "--", path).decode().strip()
    if not raw:
        return None
    info, name = raw.split("\t")
    mode, kind, sha = info.split()
    assert name == path and mode == "160000" and kind == "commit", raw
    return sha


assert set(choices) <= set(meta["remaining_conflicts"])
assert not set(choices) & set(meta["reviewed_resolutions"])
previous = meta["reviewed_tree"]
caller_paths = (
    "protocol/tailscale/tailcat_inbound.go", "protocol/tailscale/endpoint.go",
    "protocol/tailscale/system_binding.go", "protocol/tailscale/port.go",
    "protocol/tailscale/tailcat_node.go", "protocol/tailscale/taildrop_send.go",
)
assert not set(caller_paths) & set(meta["remaining_conflicts"])
with tempfile.TemporaryDirectory() as tmp:
    env = dict(os.environ, GIT_INDEX_FILE=str(Path(tmp) / "index"))
    git("read-tree", previous, env=env)
    for path, (old, new) in choices.items():
        assert entry(meta["base"], path) is None, path
        assert entry(meta["patched_tree"], path) == old, path
        assert entry(meta["upstream"], path) == new, path
        assert entry(previous, path) in (old, new), path
        git("update-index", "--add", "--cacheinfo", f"160000,{new},{path}", env=env)
    # Materialize just the six callers, then reuse the already tested
    # hash-guarded compatibility helper without touching the stable checkout.
    dest = Path(tmp) / "callers"
    for path in caller_paths:
        file = dest / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(git("show", previous + ":" + path))
    subprocess.run([sys.executable, str(Path(__file__).with_name("prepare-tailscale-callers.py")),
                    str(repo), str(report), meta["upstream"], str(dest)], check=True)
    caller_changes = json.loads((report / "tailscale-caller-adjustments.json").read_text())
    assert {entry["path"] for entry in caller_changes} == set(caller_paths)
    for adjustment in caller_changes:
        path = adjustment["path"]
        sha = subprocess.check_output(["git", "-C", str(repo), "hash-object", "-w",
                                       str(dest / path)]).decode().strip()
        assert sha == adjustment["candidate_blob"], path
        git("update-index", "--cacheinfo", f"100644,{sha},{path}", env=env)
    tree = git("write-tree", env=env).decode().strip()

changed = set(os.fsdecode(p) for p in git(
    "diff", "--name-only", "-z", previous, tree).split(b"\0") if p)
assert changed <= set(choices) | set(caller_paths), changed
for adjustment in caller_changes:
    path = adjustment["path"]
    assert git("rev-parse", tree + ":" + path).decode().strip() == adjustment["candidate_blob"]
    output = report / "hiddify-sing-box-resolved" / path
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(git("show", tree + ":" + path))
for path, (_, new) in choices.items():
    assert entry(tree, path) == new, path
    print(f"Reviewed candidate gitlink: {path} -> {new}")

meta["reviewed_tree"] = tree
meta["reviewed_resolutions"] = sorted(set(meta["reviewed_resolutions"]) | set(choices))
meta["remaining_conflicts"] = sorted(set(meta["remaining_conflicts"]) - set(choices))
meta["reviewed_gitlinks"] = {path: {"fork": old, "upstream": new}
                             for path, (old, new) in choices.items()}
meta["clean_adjustments"] = sorted(set(meta["clean_adjustments"]) |
                                   (set(caller_paths) - set(meta["conflicts"])))
meta["tailscale_caller_adjustments"] = caller_changes
meta["required_dependency_overlays"] = {
    "replace/MasterDnsVPN": {
        "base": choices["replace/MasterDnsVPN"][1],
        "script": "scripts/fix-masterdns-session-readiness.py",
        "required_for_promotion": True,
    }
}
assert set(meta["reviewed_resolutions"]) | set(meta["remaining_conflicts"]) == set(meta["conflicts"])
(report / "hiddify-sing-box-reviewed-resolutions.patch").write_bytes(
    git("diff", "--binary", "--full-index", meta["tentative_tree"], tree))
meta_file.write_text(json.dumps(meta, indent=2) + "\n")
print(f"sing-box: {len(meta['reviewed_resolutions'])} reviewed resolutions, "
      f"{len(meta['remaining_conflicts'])} unresolved paths (including manifest/schema gates)")
