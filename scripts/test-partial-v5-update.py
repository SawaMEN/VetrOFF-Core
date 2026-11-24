#!/usr/bin/env python3
"""Test the first four upstream commits while the full v5 update is blocked."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile

OLD = "a26e58b376987a3305b201ec87fbc8918f620ad3"
TARGET = "d43f078901de7091b9ea438dbf6a19af97209526"
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


def main(work, local, evidence, source, report):
    report.mkdir(parents=True, exist_ok=True)
    checker = load_module("checker", "verify-candidate-evidence.py")
    checker.verify(evidence)
    final = json.loads((evidence / "final-candidate-conflicts.json").read_text())
    require(final["integration_passed"] is True, "unverified release baseline")
    repo = work / "hiddify-sing-box"
    replay = load_module("replay", "replay-final-candidate.py")
    baseline = replay.replay(repo, "hiddify-sing-box", local, evidence,
                             final["components"]["hiddify-sing-box"]["tree"])
    require(baseline == BASELINE, "unexpected baseline")
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
    # Do not overwrite fork-specific docs. Record the upstream notes separately.
    path = "docs/hiddify/app_considerations.md"
    ours = git(repo, "show", baseline + ":" + path)
    require((source / path).read_bytes() == ours, "source docs drift")
    files[path] = ours.rstrip() + b"\n\n## Partial upstream update (test candidate)\n\nSee https://github.com/hiddify/hiddify-sing-box/tree/d43f078901de7091b9ea438dbf6a19af97209526 for the four upstream commits and changelogs. The full v5 update is not included.\n"
    stage = load_module("stage", "stage-final-conflicts.py")
    tree = stage.stage(repo, baseline, files, {})
    for path, content in files.items():
        (source / path).write_bytes(content)
    (report / "partial-update.patch").write_bytes(git(repo, "diff", "--binary", "--full-index", baseline, tree))
    (report / "partial-update.json").write_text(json.dumps({
        "baseline": baseline, "candidate_tree": tree, "target": TARGET,
        "upstream_commits": 4, "deferred_upstream_commits": 17,
        "changed_paths": sorted(files), "full_update": False,
        "stable_promotion": False,
    }, indent=2) + "\n")
    print("Partial update staged: four upstream commits, 17 deferred; no dependency pin changes")


if __name__ == "__main__":
    main(*(Path(arg).resolve() for arg in sys.argv[1:]))
