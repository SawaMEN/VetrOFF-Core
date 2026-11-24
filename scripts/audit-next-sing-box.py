#!/usr/bin/env python3
"""Audit the next upstream sing-box update against the released 0.0.4 tree."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

OLD = "a26e58b376987a3305b201ec87fbc8918f620ad3"
NEW = "1267fe6aac8d11f3a562e8feecad11d11f48f965"


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def git(repo, *args, env=None):
    return subprocess.check_output(["git", "-C", str(repo), *args], env=env)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def main(work, local, evidence, report):
    checker = module("evidence_check", "verify-candidate-evidence.py")
    checker.verify(evidence)
    replay = module("replay", "replay-final-candidate.py")
    final = json.loads((evidence / "final-candidate-conflicts.json").read_text())
    require(final["integration_passed"] is True, "unverified baseline")
    require(all(not item["remaining_conflicts"] for item in final["components"].values()),
            "baseline has unresolved conflicts")
    repo = work / "hiddify-sing-box"
    baseline = replay.replay(repo, "hiddify-sing-box", local, evidence,
                             final["components"]["hiddify-sing-box"]["tree"])
    reviewed = json.loads((local / "hiddify-sing-box-reviewed.json").read_text())
    require(reviewed["upstream"] == OLD, "unexpected previous upstream revision")
    git(repo, "fetch", "--quiet", "--no-tags", "origin", NEW)
    require(git(repo, "rev-parse", NEW + "^{commit}").decode().strip() == NEW, "new revision mismatch")
    git(repo, "merge-base", "--is-ancestor", OLD, NEW)
    count = int(git(repo, "rev-list", "--count", OLD + ".." + NEW))
    require(count == 21, "unexpected upstream history")
    env = dict(os.environ, GIT_AUTHOR_NAME="VetrOFF update audit",
               GIT_AUTHOR_EMAIL="audit@example.invalid",
               GIT_COMMITTER_NAME="VetrOFF update audit",
               GIT_COMMITTER_EMAIL="audit@example.invalid",
               GIT_AUTHOR_DATE="2026-11-25T00:00:00Z",
               GIT_COMMITTER_DATE="2026-11-25T00:00:00Z")
    ours = git(repo, "commit-tree", baseline, "-p", OLD,
               "-m", "VetrOFF-Core 0.0.4 verified sing-box tree", env=env).decode().strip()
    result = subprocess.run(
        ["git", "-C", str(repo), "merge-tree", "--write-tree", "--name-only",
         "--no-messages", "-z", "--merge-base=" + OLD, NEW, ours],
        capture_output=True, check=False)
    require(result.returncode in (0, 1), result.stderr.decode(errors="replace"))
    fields = result.stdout.split(b"\0")
    tree = fields.pop(0).decode("ascii")
    require(len(tree) == 40 and all(c in "0123456789abcdef" for c in tree), "invalid merge tree")
    conflicts = sorted(set(os.fsdecode(p) for p in fields if p))
    require(bool(conflicts) == (result.returncode == 1), "merge status mismatch")
    changed = sorted(set(os.fsdecode(p) for p in
                         git(repo, "diff", "--name-only", "-z", OLD, NEW).split(b"\0") if p))
    report.mkdir(parents=True, exist_ok=True)
    (report / "upstream-commits.txt").write_bytes(git(repo, "log", "--format=%H %s", OLD + ".." + NEW))
    (report / "upstream-diff.patch").write_bytes(git(repo, "diff", "--binary", "--full-index", OLD, NEW))
    (report / "tentative-update.patch").write_bytes(git(repo, "diff", "--binary", "--full-index", baseline, tree))
    (report / "merge-stderr.log").write_bytes(result.stderr)
    details = {
        "release_baseline": "0.0.4",
        "upstream_before": OLD,
        "upstream_target": NEW,
        "upstream_commits": count,
        "baseline_tree": baseline,
        "tentative_tree": tree,
        "changed_upstream_paths": changed,
        "conflicts": conflicts,
        "conflict_count": len(conflicts),
        "status": "review_required" if conflicts else "clean_merge_tests_required",
        "stable_promotion": False,
    }
    (report / "update-audit.json").write_text(json.dumps(details, indent=2, sort_keys=True) + "\n")
    print(json.dumps(details, indent=2, sort_keys=True))
    # Even a clean merge still needs its own full integration and matrix.
    return 1 if conflicts else 0


if __name__ == "__main__":
    sys.exit(main(*(Path(arg).resolve() for arg in sys.argv[1:])))
