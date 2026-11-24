#!/usr/bin/env python3
"""Materialize the last five conflict choices in disposable Git indexes.

The reviewed source checkpoint remains immutable. A fully resolved candidate
is not a release: nested overlays, tests and platform review still matter.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

TAIL = "replace/tailscale"
TAIL_OLD = "1f5488c9848da9d4f1104cc29998535939f23df8"
TAIL_NEW = "4138b20dfb9f5852d043abd8bf9da6c29d5ee53b"
EXPECTED = {
    "hiddify-core": {"go.mod"},
    "hiddify-sing-box": {"go.mod", "test/go.mod", TAIL, "docs/schema.json"},
}
MODULES = {
    "hiddify-core": ("hiddify-core", ""),
    "hiddify-sing-box": ("hiddify-sing-box", ""),
    "hiddify-sing-box-test": ("hiddify-sing-box", "test/"),
}


def git(repo, *args, env=None, data=None):
    return subprocess.check_output(["git", "-C", str(repo), *args], env=env, input=data)


def read_json(path):
    return json.loads(path.read_text())


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def stage(repo, tree, files, links):
    with tempfile.TemporaryDirectory() as tmp:
        env = dict(os.environ, GIT_INDEX_FILE=str(Path(tmp) / "index"))
        git(repo, "read-tree", tree, env=env)
        entries = {}
        for path, content in files.items():
            require(bool(content), f"empty candidate: {path}")
            oid = git(repo, "hash-object", "-w", "--stdin", data=content).decode().strip()
            entries[path] = ("100644", oid)
        for path, oid in links.items():
            entries[path] = ("160000", oid)
        for path, (mode, oid) in entries.items():
            git(repo, "update-index", "--add", "--cacheinfo", f"{mode},{oid},{path}", env=env)
        result = git(repo, "write-tree", env=env).decode().strip()
    changed = set(os.fsdecode(p) for p in git(repo, "diff", "--name-only", "-z", tree, result).split(b"\0") if p)
    require(changed <= set(entries), f"unexpected tree delta: {changed}")
    for path, (mode, oid) in entries.items():
        raw = git(repo, "ls-tree", result, "--", path).decode().strip()
        require(raw.split("\t", 1)[0].split()[:1] == [mode], f"incorrect mode: {path}")
        require(git(repo, "rev-parse", result + ":" + path).decode().strip() == oid, f"incorrect blob: {path}")
    return result


def sha(data):
    return hashlib.sha256(data).hexdigest()


def validate_mod(content):
    with tempfile.TemporaryDirectory() as tmp:
        manifest = Path(tmp) / "go.mod"
        manifest.write_bytes(content)
        env = dict(os.environ, GOTOOLCHAIN="local", GOWORK="off", GOPROXY="off", GOSUMDB="off")
        result = json.loads(subprocess.check_output(["go", "mod", "edit", "-json", str(manifest)], env=env))
        require(result["Module"]["Path"] and result.get("Go"), "invalid manifest")
        return result


def prepare(work, report):
    repos = {"hiddify-core": work, "hiddify-sing-box": work / "hiddify-sing-box"}
    meta = {name: read_json(report / (name + "-reviewed.json")) for name in repos}
    for name, expected in EXPECTED.items():
        require(set(meta[name]["remaining_conflicts"]) == expected, f"unexpected remaining conflicts: {name}")
        require(len(meta[name]["conflicts"]) == (44 if name == "hiddify-core" else 166), f"changed conflict baseline: {name}")
    sing = repos["hiddify-sing-box"]
    for revision, expected in ((meta["hiddify-sing-box"]["patched_tree"], TAIL_OLD),
                               (meta["hiddify-sing-box"]["upstream"], TAIL_NEW)):
        require(git(sing, "rev-parse", revision + ":" + TAIL).decode().strip() == expected, "Tailscale input drift")
    callers = meta["hiddify-sing-box"]["tailscale_caller_adjustments"]
    require(len(callers) == 6 and len({item["path"] for item in callers}) == 6, "Tailscale callers incomplete")
    for item in callers:
        require(git(sing, "rev-parse", meta["hiddify-sing-box"]["reviewed_tree"] + ":" + item["path"]).decode().strip() ==
                item["candidate_blob"], f"caller drift: {item['path']}")
    files = {name: {} for name in repos}
    for module, (component, prefix) in MODULES.items():
        content = (report / (module + "-go.mod.candidate")).read_bytes()
        validate_mod(content)
        files[component][prefix + "go.mod"] = content
    result = {}
    for name, repo in repos.items():
        links = {TAIL: TAIL_NEW} if name == "hiddify-sing-box" else {}
        resolved = set(files[name]) | set(links)
        tree = stage(repo, meta[name]["reviewed_tree"], files[name], links)
        result[name] = {
            "tree": tree,
            "reviewed_tree": meta[name]["reviewed_tree"],
            "resolved_conflicts": sorted(set(meta[name]["reviewed_resolutions"]) | resolved),
            "remaining_conflicts": sorted(EXPECTED[name] - resolved),
        }
        (report / (name + "-staged-candidate.patch")).write_bytes(
            git(repo, "diff", "--binary", "--full-index", meta[name]["tentative_tree"], tree))
    write_json(report / "staged-candidate-conflicts.json", {
        "status": "209/210 source choices staged; generated schema pending",
        "components": result, "stable_promotion": False,
    })
    print("Staged four remaining choices: Tailscale and three go.mod files; schema pending")


def finalize(work, report, integration_failed):
    repos = {"hiddify-core": work, "hiddify-sing-box": work / "hiddify-sing-box"}
    staged = read_json(report / "staged-candidate-conflicts.json")["components"]
    provenance = read_json(report / "combined-candidate-provenance.json")
    for name, key in (("hiddify-core", "core_tree"), ("hiddify-sing-box", "sing_box_tree")):
        require(staged[name]["tree"] == provenance[key], f"materialized tree mismatch: {name}")
    require(staged["hiddify-core"]["remaining_conflicts"] == [] and
            staged["hiddify-sing-box"]["remaining_conflicts"] == ["docs/schema.json"], "unexpected staged state")
    graph = read_json(report / "combined-effective-dependencies.json")
    require(graph["errors"] == {}, "effective dependency checks failed")
    require(set(graph["module_counts"]) == set(MODULES), "incomplete module graphs")
    files = {name: {} for name in repos}
    for module, (component, prefix) in MODULES.items():
        for filename in ("go.mod", "go.sum"):
            content = (report / f"combined-effective-{module}.{filename}").read_bytes()
            require(sha(content) == graph["manifest_sha256"][module][filename],
                    f"effective manifest digest mismatch: {module}/{filename}")
            if filename == "go.mod":
                validate_mod(content)
            files[component][prefix + filename] = content
    # Reuse the independent schema validator for the generated artifact.
    spec = importlib.util.spec_from_file_location("evidence", Path(__file__).with_name("verify-candidate-evidence.py"))
    evidence = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(evidence)
    schema = (report / "combined-schema.json").read_bytes()
    schema_info = evidence.schema_contract(json.loads(schema))
    files["hiddify-sing-box"]["docs/schema.json"] = schema
    output = {}
    for name, repo in repos.items():
        tree = stage(repo, staged[name]["tree"], files[name], {})
        meta = read_json(report / (name + "-reviewed.json"))
        resolved = set(staged[name]["resolved_conflicts"]) | ({"docs/schema.json"} if name == "hiddify-sing-box" else set())
        require(resolved == set(meta["conflicts"]), f"not all conflicts resolved: {name}")
        output[name] = {"tree": tree, "resolved_conflicts": sorted(resolved), "remaining_conflicts": []}
        (report / (name + "-final-candidate.patch")).write_bytes(
            git(repo, "diff", "--binary", "--full-index", meta["tentative_tree"], tree))
    write_json(report / "final-candidate-conflicts.json", {
        "status": "all 210 source conflict choices materialized",
        "components": output, "schema": {**schema_info, "sha256": sha(schema)},
        "integration_passed": not integration_failed,
        "required_dependency_overlays": provenance["required_dependency_overlays"],
        "materialized_dependency_overlays": provenance["materialized_dependency_overlays"],
        "remaining_shared_source_differences": graph["shared_source_differences"],
        "stable_promotion": False,
    })
    print("All 210 conflict paths have candidate tree entries; stable promotion remains disabled")


if __name__ == "__main__":
    mode, work, report, *rest = sys.argv[1:]
    work, report = Path(work), Path(report)
    if mode == "prepare" and not rest:
        prepare(work, report)
    elif mode == "finalize" and len(rest) == 1 and rest[0] in ("0", "1"):
        finalize(work, report, rest[0] == "1")
    else:
        sys.exit("Usage: stage-final-conflicts.py prepare WORK REPORT | finalize WORK REPORT FAILED")
