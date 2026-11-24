#!/usr/bin/env python3
"""Reconstruct the tested final candidate from exact integration evidence."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args])


def require(condition, message):
    if not condition:
        raise ValueError(message)


def load(path):
    return json.loads(path.read_text())


def archive(repo, tree, dest):
    dest.mkdir(parents=True, exist_ok=True)
    with subprocess.Popen(["git", "-C", str(repo), "archive", tree], stdout=subprocess.PIPE) as proc:
        subprocess.run(["tar", "-xf", "-", "-C", str(dest)], stdin=proc.stdout, check=True)
        proc.stdout.close()
        require(proc.wait() == 0, "git archive failed")


def replay(repo, component, local, evidence, expected):
    before = load(local / f"{component}-reviewed.json")
    original = load(evidence / f"{component}-reviewed.json")
    # Provisional merge-tree conflict labels may contain a synthetic commit
    # ID which changes between audit runs. Require exact FINAL tree identity.
    for key in ("base", "upstream", "patched_tree", "conflicts",
                "reviewed_resolutions", "remaining_conflicts"):
        require(before[key] == original[key], f"{component}: source drift in {key}")
    staged = load(local / "staged-candidate-conflicts.json")["components"][component]
    original_staged = load(evidence / "staged-candidate-conflicts.json")["components"][component]
    for key in ("resolved_conflicts", "remaining_conflicts"):
        require(staged[key] == original_staged[key], f"{component}: staged coverage mismatch")
    require(staged["reviewed_tree"] == before["reviewed_tree"], "local staged checkpoint mismatch")
    graph = load(evidence / "combined-effective-dependencies.json")
    modules = (("hiddify-core", ""),) if component == "hiddify-core" else (
        ("hiddify-sing-box", ""), ("hiddify-sing-box-test", "test/"))
    files = {}
    for module, prefix in modules:
        for filename in ("go.mod", "go.sum"):
            content = (evidence / f"combined-effective-{module}.{filename}").read_bytes()
            require(hashlib.sha256(content).hexdigest() == graph["manifest_sha256"][module][filename],
                    f"effective manifest digest mismatch: {module}/{filename}")
            files[prefix + filename] = content
    if component == "hiddify-sing-box":
        schema = (evidence / "combined-schema.json").read_bytes()
        final = load(evidence / "final-candidate-conflicts.json")
        require(hashlib.sha256(schema).hexdigest() == final["schema"]["sha256"], "schema digest mismatch")
        files["docs/schema.json"] = schema
    spec = importlib.util.spec_from_file_location("stage", Path(__file__).with_name("stage-final-conflicts.py"))
    stage = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(stage)
    tree = stage.stage(repo, staged["tree"], files, {})
    require(tree == expected, f"{component}: reconstructed tree mismatch: {tree} != {expected}")
    return tree


def main(work, local, evidence, dest):
    spec = importlib.util.spec_from_file_location("evidence_check", Path(__file__).with_name("verify-candidate-evidence.py"))
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    checker.verify(evidence)
    final = load(evidence / "final-candidate-conflicts.json")
    require(final["integration_passed"] is True and final["stable_promotion"] is False,
            "final candidate did not pass integration")
    p = load(evidence / "combined-candidate-provenance.json")
    components = final["components"]
    for name, count in (("hiddify-core", 44), ("hiddify-sing-box", 166)):
        require(components[name]["remaining_conflicts"] == [], "unresolved conflicts")
        require(len(set(components[name]["resolved_conflicts"])) == count, "incomplete conflict coverage")
    require(not dest.exists(), "destination already exists")
    sing = work / "hiddify-sing-box"
    core_tree = replay(work, "hiddify-core", local, evidence, components["hiddify-core"]["tree"])
    sing_tree = replay(sing, "hiddify-sing-box", local, evidence, components["hiddify-sing-box"]["tree"])
    ray_tree = p["ray2sing_patched_tree"]
    require(git(work / "ray2sing", "write-tree").decode().strip() == ray_tree, "ray2sing tree mismatch")
    archive(work, core_tree, dest)
    archive(sing, sing_tree, dest / "hiddify-sing-box")
    archive(work / "ray2sing", ray_tree, dest / "ray2sing")
    pins = {}
    for line in git(sing, "ls-tree", "-r", sing_tree).decode().splitlines():
        info, path = line.split("\t", 1)
        mode, kind, revision = info.split()
        if mode == "160000":
            require(kind == "commit" and path.startswith("replace/"), "unexpected gitlink")
            pins[path] = revision
    require(pins == p["candidate_dependency_pins"], "nested dependency pin drift")
    for path, revision in sorted(pins.items()):
        target = dest / "hiddify-sing-box" / path
        target.mkdir(parents=True, exist_ok=True)
        url = git(dest / "hiddify-sing-box", "config", "-f",
                  str((dest / "hiddify-sing-box/.gitmodules").resolve()),
                  "--get", "submodule." + path + ".url").decode().strip()
        require(url.startswith("https://github.com/"), "unexpected dependency URL")
        git(target, "init", "-q")
        git(target, "remote", "add", "origin", url)
        git(target, "fetch", "-q", "--no-tags", "--depth=1", "origin", revision)
        git(target, "checkout", "-q", "--detach", revision)
        require(git(target, "rev-parse", "HEAD").decode().strip() == revision, "nested HEAD mismatch")
        git(target, "-c", "url.https://github.com/.insteadOf=git@github.com:",
            "submodule", "update", "--init", "--recursive", "--jobs", "4")
    mdns = dest / "hiddify-sing-box/replace/MasterDnsVPN"
    overlay = p["materialized_dependency_overlays"]["replace/MasterDnsVPN"]
    require(final["materialized_dependency_overlays"] == p["materialized_dependency_overlays"],
            "overlay metadata mismatch")
    git(mdns, "apply", "--index", "--binary", str((evidence / overlay["patch"]).resolve()))
    require(git(mdns, "write-tree").decode().strip() == overlay["tree"], "MasterDNS overlay tree mismatch")
    for module, rel in (("hiddify-core", "."), ("hiddify-sing-box", "hiddify-sing-box"),
                        ("hiddify-sing-box-test", "hiddify-sing-box/test")):
        for filename in ("go.mod", "go.sum"):
            require((dest / rel / filename).read_bytes() ==
                    (evidence / f"combined-effective-{module}.{filename}").read_bytes(),
                    f"manifest mismatch: {module}/{filename}")
    require((dest / "hiddify-sing-box/docs/schema.json").read_bytes() ==
            (evidence / "combined-schema.json").read_bytes(), "schema mismatch")
    print(json.dumps({"core": core_tree, "sing_box": sing_tree, "ray2sing": ray_tree,
                      "nested_pins": pins, "masterdns_tree": overlay["tree"],
                      "stable_promotion": False}, indent=2))


if __name__ == "__main__":
    main(*(Path(arg).resolve() for arg in sys.argv[1:]))
