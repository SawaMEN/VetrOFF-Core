#!/usr/bin/env python3
"""Replay and verify saved migration checkpoints without touching stable sources."""
import argparse
import gzip
import json
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
CORE = "4e5a9a357003d0a995360586702fd24c53042e3f"
SING = "a26e58b376987a3305b201ec87fbc8918f620ad3"
OID = re.compile(r"[0-9a-f]{40}")

def git(repo, *args, payload=None):
    return subprocess.run(
        ["git", "-C", str(repo), *args], input=payload,
        stdout=subprocess.PIPE, check=True,
    ).stdout

def paths(file):
    result = file.read_text(encoding="utf-8").splitlines()
    if any(not p or p.startswith("/") or ".." in p.split("/") or "\t" in p for p in result):
        raise ValueError(f"{file}: invalid path")
    if len(set(result)) != len(result):
        raise ValueError(f"{file}: duplicate path")
    return set(result)

def index_entries(raw):
    result = {}
    for entry in raw.split(b"\0"):
        if not entry:
            continue
        metadata, path = entry.split(b"\t", 1)
        mode, sha, stage = metadata.decode("ascii").split()
        path = path.decode("utf-8")
        if stage != "0" or not OID.fullmatch(sha) or path in result:
            raise ValueError(f"Invalid index entry: {entry!r}")
        result[path] = (mode, sha)
    return result

def expected_index(file):
    lines = file.read_bytes().splitlines()
    return index_entries(b"\0".join(lines))

def verify_index(expected, actual, selected):
    if not set(expected) <= selected:
        raise ValueError("Saved index contains an unlisted path")
    observed = {p: actual[p] for p in selected if p in actual}
    if observed != expected:
        mismatch = sorted(p for p in selected if observed.get(p) != expected.get(p))
        raise ValueError(f"Checkpoint index mismatch: {mismatch}")

def prepare(repo, source, revision):
    repo.mkdir()
    git(repo, "init", "--quiet")
    git(repo, "remote", "add", "origin", source)
    git(repo, "fetch", "--quiet", "--no-tags", "--depth=1", "origin", revision)
    if git(repo, "rev-parse", "FETCH_HEAD").decode().strip() != revision:
        raise ValueError("Fetched revision does not match the checkpoint")
    git(repo, "read-tree", revision)

def replay(repo, directory, revision, prefix, list_prefix, complete):
    selected = paths(directory / (list_prefix + "-paths.txt"))
    expected = expected_index(directory / (list_prefix + "-index.txt"))
    pending = paths(directory / "pending-paths.txt")
    if selected & pending:
        raise ValueError("Resolved and pending paths overlap")
    with gzip.open(directory / (prefix + "-resolutions.patch.gz"), "rb") as patch:
        git(repo, "apply", "--cached", "--whitespace=nowarn", payload=patch.read())
    actual = index_entries(git(repo, "ls-files", "--stage", "-z"))
    verify_index(expected, actual, selected)
    tree = git(repo, "write-tree").decode().strip()
    changed = {p.decode("utf-8") for p in git(
        repo, "diff", "--cached", "--name-only", "--no-renames", "-z", revision,
    ).split(b"\0") if p}
    target = (directory / "candidate-tree.txt").read_text().strip()
    if not OID.fullmatch(target) or tree != target:
        raise ValueError(f"Checkpoint tree mismatch: {tree} != {target}")
    if complete and pending:
        raise ValueError("Complete checkpoint still has pending paths")
    if changed - selected:
        raise ValueError(f"Patch modifies unreviewed paths: {sorted(changed - selected)}")
    if not complete:
        identical = paths(directory / "identical-upstream-paths.txt")
        if identical & (selected | pending):
            raise ValueError("Identical-upstream paths overlap other review lists")
        if changed & (pending | identical):
            raise ValueError("Partial patch changes pending or identical-upstream paths")
    return {"upstream": revision, "tree": tree, "verified_paths": len(selected),
            "changed_paths": len(changed), "pending_paths": sorted(pending),
            "complete_checkpoint": complete}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, help="Write a JSON verification report")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="vetroff-checkpoint-") as tmp:
        core = Path(tmp) / "core"
        sing = Path(tmp) / "sing"
        prepare(core, "https://github.com/hiddify/hiddify-core.git", CORE)
        link = index_entries(git(core, "ls-files", "--stage", "-z")).get("hiddify-sing-box")
        if link != ("160000", SING):
            raise ValueError("Core target does not pin the sing-box checkpoint")
        report = {"core": replay(core, ROOT / "migration/core-4e5a9a3", CORE,
                                 "core", "restored", True)}
        prepare(sing, "https://github.com/hiddify/hiddify-sing-box.git", SING)
        report["sing_box"] = replay(sing, ROOT / "migration/sing-box-a26e58b", SING,
                                    "sing-box", "resolved", False)
    report["status"] = "checkpoint replay verified; integrated build and unresolved paths still pending"
    output = json.dumps(report, indent=2) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(output, encoding="utf-8")
    print(output, end="")

if __name__ == "__main__":
    main()
