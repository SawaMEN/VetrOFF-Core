#!/usr/bin/env python3
"""Apply individually reviewed, hash-guarded conflict resolutions to a diagnostic tree.

The result is intentionally not an installable source tree until every other
conflict is resolved. Neither the stable source index nor any patch is changed.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import plistlib

repo, report = Path(sys.argv[1]), Path(sys.argv[2])
data = json.loads((report / "hiddify-core.json").read_text())

def git(*args, payload=None, env=None):
    return subprocess.check_output(["git", "-C", str(repo), *args], input=payload, env=env)

def checked_blob(revision, path, expected):
    spec = revision + ":" + path
    actual = git("rev-parse", spec).decode().strip()
    if actual != expected:
        raise ValueError(f"{path}: reviewed blob changed ({actual})")
    return git("cat-file", "blob", actual)

base, upstream, patched = data["base"], data["upstream"], data["patched_tree"]
assert base == "b097daadd89d2b62e30c0f4353f55a973118514c"
assert upstream == "4e5a9a357003d0a995360586702fd24c53042e3f"
resolutions = {}
path = "Info.plist"
old = checked_blob(base, path, "9dd1f260b17cb417997e3fa04bf6fc653bf338e8")
own = checked_blob(patched, path, "b108781d30f7b84fbdf882bc01e692aa65687ff4")
new = checked_blob(upstream, path, "408f02458d1bd393545e15b55be45593e8fc7232")
# Both edits are exclusively bundle-version updates; take the newer version.
for blob, version in ((old, "5.4.0"), (own, "5.5.5"), (new, "5.9.15")):
    parsed = plistlib.loads(blob)
    assert parsed["CFBundleShortVersionString"] == version
    assert parsed["CFBundleVersion"] == version
    del parsed["CFBundleShortVersionString"]
    del parsed["CFBundleVersion"]
    if blob == old:
        baseline = parsed
    else:
        assert parsed == baseline
resolutions[path] = new

path = "HISTORY.md"
old = checked_blob(base, path, "8ceb9f8eb9af1a7904bc1795c48f4e39ccd8e75d")
own = checked_blob(patched, path, "e247afa4d259d52d4b4b3c44847b3d0b9f9f3b8a")
new = checked_blob(upstream, path, "047c53fe9b55f4e1c9fffcabb336c88facb6f668")
# The fork adds a native-compatibility heading plus upstream changelog entries
# already present verbatim in the newer original. Keep the heading and every
# newer entry, without copying duplicate entries.
prefix, sep, rest = own.partition(b"Changelog\n=========\n")
assert sep and prefix.startswith(b"## (unreleased) Native client compatibility\n")
assert new.startswith(sep)
# Verify all nonblank fork additions outside the custom heading are present
# in the updated upstream changelog, including wrapped lines.
marker = b"\nv4.0.0 (2026-02-13)"
def split_history(blob):
    top, separator, tail = blob.partition(marker)
    assert separator
    return [line for line in top.splitlines() if line.strip()], separator + tail

def is_subsequence(earlier, later):
    sequence = iter(later)
    return all(any(candidate == line for candidate in sequence) for line in earlier)

old_top, old_tail = split_history(old)
own_top, own_tail = split_history(sep + rest)
new_top, new_tail = split_history(new)
assert old_tail == own_tail == new_tail, "Historical changelog changed"
assert is_subsequence(old_top, own_top), "Fork removed or reordered older entries"
assert is_subsequence(own_top, new_top), "Upstream omitted fork changelog entries"
resolutions[path] = prefix + new

assert set(resolutions) <= set(data["conflicts"])
with tempfile.TemporaryDirectory() as tmp:
    env = dict(os.environ, GIT_INDEX_FILE=str(Path(tmp) / "index"))
    git("read-tree", data["tentative_tree"], env=env)
    for path, content in resolutions.items():
        sha = git("hash-object", "-w", "--stdin", payload=content).decode().strip()
        git("update-index", "--cacheinfo", f"100644,{sha},{path}", env=env)
        assert git("show", ":" + path, env=env) == content
        (report / ("resolved-" + path)).write_bytes(content)
        print(f"Reviewed resolution: {path} -> {sha}")
    tree = git("write-tree", env=env).decode().strip()
    delta = git("diff", "--name-only", "-z", data["tentative_tree"], tree)
    assert set(os.fsdecode(p) for p in delta.split(b"\0") if p) == set(resolutions)
    (report / "core-metadata-resolutions.patch").write_bytes(
        git("diff", "--binary", "--full-index", data["tentative_tree"], tree)
    )
remaining = sorted(set(data["conflicts"]) - set(resolutions))
data.update(reviewed_tree=tree, reviewed_resolutions=sorted(resolutions), remaining_conflicts=remaining)
(report / "hiddify-core-reviewed.json").write_text(json.dumps(data, indent=2) + "\n")
print(f"Core: {len(resolutions)} reviewed resolutions, {len(remaining)} unresolved paths")
for component in ("hiddify-core", "hiddify-sing-box"):
    current = json.loads((report / (component + ".json")).read_text())
    print(f"Conflict paths for {component}:")
    for path in current["conflicts"]:
        print("  " + path)
