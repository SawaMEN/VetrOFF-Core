#!/usr/bin/env python3
"""Stage two previously reviewed nested gitlinks in the diagnostic tree only."""
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
# these exact upstream targets. MasterDNS needs its additional readiness
# patch and Tailscale needs paired caller verification, so leave both open.
choices = {
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
with tempfile.TemporaryDirectory() as tmp:
    env = dict(os.environ, GIT_INDEX_FILE=str(Path(tmp) / "index"))
    git("read-tree", previous, env=env)
    for path, (old, new) in choices.items():
        assert entry(meta["base"], path) is None, path
        assert entry(meta["patched_tree"], path) == old, path
        assert entry(meta["upstream"], path) == new, path
        assert entry(previous, path) in (old, new), path
        git("update-index", "--add", "--cacheinfo", f"160000,{new},{path}", env=env)
    tree = git("write-tree", env=env).decode().strip()

changed = set(os.fsdecode(p) for p in git(
    "diff", "--name-only", "-z", previous, tree).split(b"\0") if p)
assert changed <= set(choices), changed
for path, (_, new) in choices.items():
    assert entry(tree, path) == new, path
    print(f"Reviewed candidate gitlink: {path} -> {new}")

meta["reviewed_tree"] = tree
meta["reviewed_resolutions"] = sorted(set(meta["reviewed_resolutions"]) | set(choices))
meta["remaining_conflicts"] = sorted(set(meta["remaining_conflicts"]) - set(choices))
meta["reviewed_gitlinks"] = {path: {"fork": old, "upstream": new}
                             for path, (old, new) in choices.items()}
assert set(meta["reviewed_resolutions"]) | set(meta["remaining_conflicts"]) == set(meta["conflicts"])
(report / "hiddify-sing-box-reviewed-resolutions.patch").write_bytes(
    git("diff", "--binary", "--full-index", meta["tentative_tree"], tree))
meta_file.write_text(json.dumps(meta, indent=2) + "\n")
print(f"sing-box: {len(meta['reviewed_resolutions'])} reviewed resolutions, "
      f"{len(meta['remaining_conflicts'])} unresolved paths (including manifest/schema gates)")
