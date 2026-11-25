#!/usr/bin/env python3
"""Test independently reviewed upstream changes while the full v5 update is blocked."""
import importlib.util
import json
import re
from pathlib import Path
import subprocess
import sys
import tempfile

OLD = "a26e58b376987a3305b201ec87fbc8918f620ad3"
TARGET = "d43f078901de7091b9ea438dbf6a19af97209526"
IPV6_FIX = "d09fbdab2347fb451e58203dc7484ce355d1edcf"
IPV6_PARENT = "0046ea8df720343bc86bcce12053e4d9743d7aef"
IPV6_BLOBS = {
    "adapter/inbound.go": ("b7ce30fc4a6f29500acd2b18d32e281224b4e462",
                           "3aff8ef9f6489e228295bf471d1781bc966f27e5"),
    "route/rule/h_rule_item_ipv6_unavailable.go": ("ac855fdbe432607feae43d83bbe6502f58712cd0",
                                                   "613705e53e8de7fb462a464e0459072d55819a40"),
    "route/rule/h_rule_item_ipv6_unavailable_test.go": ("6d3f32b8ad2f79e2c5f3fc0c8b78ad5bd152cfb0",
                                                        "d0efd92f63c1ae545958b3b94dee436252afbb93"),
}
INDEPENDENT = {
    "543c23eaf3b4ae61186eaaecc63cacc4c492efc9": (
        "0b37d9432dfa7c6c990344381bd5124a83b604ef", {
            "experimental/libbox/export.go": (
                "d516bb79526d4b7851eb43a9d73541842262622f",
                "a2faf65f77b7e9fcf4cc9d49653b5331c465e440"),
            "experimental/libbox/h_oom.go": (
                None, "d503ccc41455b663ccde73a64a0ee98c119da932"),
            "experimental/libbox/h_oom_test.go": (
                None, "a5d9d5fc57a36fd97f9e6d87e62954f794eda5ec"),
        }),
    "7c5ebbaefbf5064868f813ae4352943fb8717af3": (
        "9092bf553f51f291c3859bd7916ae7fc3dac799f", {
            "protocol/psiphon/psiphon.go": (
                "3119778e48125997b0eab0bc7973634bc27237e7",
                "37b8cc48fe97a945aaba0d52dafcf166dc7c7598"),
            "protocol/psiphon/h_last_way_test.go": (
                None, "1be5ca9ea0198c6627483b6505f25fdf5daad1d6"),
        }),
}
BASELINE = "f76b0611b09245e2f636692487b774c03a18d6c2"
GO = {
    "adapter/experimental.go": "bb7137499b2b85b29a84f506089d2e654658e6b6",
    "common/monitoring/h_settle.go": "4d394c1d1dfe7f8a50a6130096b4d6281818f197",
    "common/monitoring/h_settle_test.go": "2a392a4323a411e13856af9bbfd475b328556e40",
    "h_box_hotreload.go": "9b61e661c553d4173599554a737a122231a840e0",
    "h_box_hotreload_test.go": "ea990a4949c81318576d73cbf4794369c00973e5",
    "protocol/group/balancer/h_stable.go": "64c277bed6a65b6da02f0f9d07d41740549c706a",
    "protocol/group/balancer/h_stable_test.go": "df5488270c60326a84c382ca544bc919da687458",
}
DOCS = {"docs/hiddify/app_considerations.md", "docs/hiddify/bugfix/v5.en.md",
        "docs/hiddify/bugfix/v5.fa.md"}


def load_module(name, file):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(file))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def require(condition, message):
    if not condition:
        raise ValueError(message)


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args])


def format_go(data):
    return subprocess.check_output(["gofmt"], input=data)


def merge_go(ours, old, new, path):
    # Formatting-only fork changes must not hide real conflicts.
    # Normalize all three inputs, then require a clean content merge.
    with tempfile.TemporaryDirectory() as tmp:
        paths = [Path(tmp) / name for name in ("fork", "base", "upstream")]
        for p, data in zip(paths, (ours, old, new)):
            p.write_bytes(format_go(data))
        merged = subprocess.run(["git", "merge-file", "-p", *map(str, paths)],
                                capture_output=True, check=False)
        require(merged.returncode == 0, f"unresolved partial update: {path}")
        return format_go(merged.stdout)


# Only the four exact duplicate imports reported by the compiler.
DUPLICATES = {
    "protocol/group/balancer/h_balancer_bugs_test.go": "github.com/sagernet/sing/common",
    "protocol/group/balancer/h_balancer_test.go": "github.com/sagernet/sing-box/adapter/htest",
    "protocol/group/balancer/h_strategy_test.go": "github.com/sagernet/sing/common",
    "common/monitoring/h_monitoring_test.go": "github.com/sagernet/sing-box/adapter/htest",
}


def fix_duplicate(data, path, imported):
    text = data.decode()
    match = re.search(r"(?ms)^import \(\n(.*?)^\)", text)
    require(match is not None, f"missing import block: {path}")
    lines = match.group(1).splitlines(keepends=True)
    exact = [i for i, line in enumerate(lines)
             if line.strip() == '"' + imported + '"']
    require(len(exact) == 2, f"unexpected duplicate import count: {path}")
    del lines[exact[1]]
    return format_go((text[:match.start(1)] + "".join(lines) + text[match.end(1):]).encode())


def main(work, report):
    report.mkdir(parents=True, exist_ok=True)
    repo = work / "hiddify-sing-box"
    source = repo
    baseline = git(repo, "write-tree").decode().strip()
    require(baseline == BASELINE, "unexpected unified baseline")
    require(git(repo, "rev-parse", "HEAD").decode().strip() == OLD, "unexpected root pin")
    git(repo, "fetch", "--no-tags", "--no-recurse-submodules", "--depth=32", "origin", TARGET)
    git(repo, "merge-base", "--is-ancestor", OLD, TARGET)
    require(int(git(repo, "rev-list", "--count", OLD + ".." + TARGET)) == 4,
            "unexpected partial update history")
    changed = set(git(repo, "diff", "--name-only", OLD, TARGET).decode().splitlines())
    require(changed == set(GO) | DOCS, f"unexpected changed paths: {changed}")
    files = {}
    for path, oid in GO.items():
        require(git(repo, "rev-parse", TARGET + ":" + path).decode().strip() == oid,
                f"target blob drift: {path}")
        ours, old, new = (git(repo, "show", ref + ":" + path) for ref in (baseline, OLD, TARGET))
        require((source / path).read_bytes() == ours, f"source archive drift: {path}")
        files[path] = merge_go(ours, old, new, path)
    # Select the later IPv6 fix independently of the unavailable gitlinks.
    git(repo, "fetch", "--no-tags", "--no-recurse-submodules", "--depth=32", "origin", IPV6_FIX)
    require(git(repo, "rev-parse", IPV6_FIX + "^").decode().strip() == IPV6_PARENT, "IPv6 parent drift")
    require(set(git(repo, "diff", "--name-only", IPV6_PARENT, IPV6_FIX).decode().splitlines())
            == set(IPV6_BLOBS), "unexpected IPv6 change paths")
    require(not set(IPV6_BLOBS) & set(files), "overlapping partial changes")
    for path, (before, after) in IPV6_BLOBS.items():
        for ref in (OLD, IPV6_PARENT):
            require(git(repo, "rev-parse", ref + ":" + path).decode().strip() == before,
                    f"IPv6 pre-change blob drift: {path}")
        require(git(repo, "rev-parse", IPV6_FIX + ":" + path).decode().strip() == after,
                f"IPv6 target blob drift: {path}")
        ours, old, new = (git(repo, "show", ref + ":" + path)
                          for ref in (baseline, OLD, IPV6_FIX))
        require((source / path).read_bytes() == ours, f"IPv6 source drift: {path}")
        if path == "adapter/inbound.go":
            text, count = re.subn(r"(?m)^(\s*RealOutbound\s+string[^\n]*)$",
                                  lambda m: m.group(1) +
                                  "\n\tIPv6RouteLookahead bool // H: route rule lookahead guard",
                                  ours.decode())
            require(count == 1, "IPv6 context anchor drift")
            files[path] = format_go(text.encode())
        else:
            files[path] = merge_go(ours, old, new, path)
    # Verify individual upstream edits without including the intervening
    # unavailable submodule updates or replacing unrelated fork files.
    for commit, (parent, blobs) in INDEPENDENT.items():
        git(repo, "fetch", "--no-tags", "--no-recurse-submodules",
            "--depth=32", "origin", commit)
        require(git(repo, "rev-parse", commit + "^").decode().strip() == parent,
                f"parent drift: {commit}")
        require(set(git(repo, "diff", "--name-only", parent, commit).decode().splitlines())
                == set(blobs), f"unexpected change paths: {commit}")
        require(not set(blobs) & set(files), f"overlapping changes: {commit}")
        for path, (before, after) in blobs.items():
            require(git(repo, "rev-parse", commit + ":" + path).decode().strip() == after,
                    f"target blob drift: {path}")
            new = git(repo, "show", commit + ":" + path)
            if before is None:
                for ref in (OLD, parent, baseline):
                    require(not git(repo, "ls-tree", ref, "--", path),
                            f"unexpected existing file: {ref}:{path}")
                require(not (source / path).exists(), f"working file exists: {path}")
                files[path] = format_go(new)
            else:
                for ref in (OLD, parent):
                    require(git(repo, "rev-parse", ref + ":" + path).decode().strip() == before,
                            f"pre-change blob drift: {path}")
                ours, old = (git(repo, "show", ref + ":" + path) for ref in (baseline, OLD))
                require((source / path).read_bytes() == ours, f"source drift: {path}")
                files[path] = merge_go(ours, old, new, path)
    # Do not overwrite fork-specific docs. Record the upstream notes separately.
    path = "docs/hiddify/app_considerations.md"
    ours = git(repo, "show", baseline + ":" + path)
    require((source / path).read_bytes() == ours, "source docs drift")
    files[path] = ours.rstrip() + b"\n\n## Partial upstream update (test candidate)\n\nSee https://github.com/hiddify/hiddify-sing-box/tree/d43f078901de7091b9ea438dbf6a19af97209526 for the four upstream commits and changelogs. Also select the independent IPv6 routing fix d09fbdab2. Also select libbox memory-limit handling 543c23eaf and Psiphon notice delivery 7c5ebbaef. Native iOS service integration and the sing buffer changes are not included. The full v5 update is not included.\n"
    for path, imported in DUPLICATES.items():
        ours = git(repo, "show", baseline + ":" + path)
        require((source / path).read_bytes() == ours, f"test source drift: {path}")
        files[path] = fix_duplicate(ours, path, imported)
    stage = load_module("stage", "stage-final-conflicts.py")
    tree = stage.stage(repo, baseline, files, {})
    for path, content in files.items():
        (source / path).parent.mkdir(parents=True, exist_ok=True)
        (source / path).write_bytes(content)
    (report / "partial-update.patch").write_bytes(git(repo, "diff", "--binary", "--full-index", baseline, tree))
    (report / "partial-update.json").write_text(json.dumps({
        "baseline": baseline, "candidate_tree": tree, "target": TARGET,
        "upstream_commits": 7, "deferred_upstream_commits": 14,
        "additional_upstream_commits": [IPV6_FIX, *INDEPENDENT],
        "changed_paths": sorted(files), "duplicate_imports_fixed": sorted(DUPLICATES),
        "full_update": False,
        "stable_promotion": False,
    }, indent=2) + "\n")
    print("Partial update staged: seven selected upstream commits, 14 deferred; no dependency pin changes")


if __name__ == "__main__":
    main(*(Path(arg).resolve() for arg in sys.argv[1:]))
