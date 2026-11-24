#!/usr/bin/env python3
"""Stage a reproducible 0.0.4 root-pin/patch promotion in an isolated checkout."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
PINS = {
    "hiddify-core": ("4e5a9a357003d0a995360586702fd24c53042e3f", "e4823f0dda24d906354b09c6a919d366424216e7"),
    "hiddify-sing-box": ("a26e58b376987a3305b201ec87fbc8918f620ad3", "f76b0611b09245e2f636692487b774c03a18d6c2"),
    "ray2sing": ("a722bf1b184d7fcf10248dee45fc099d62646ca8", "578ece81ce7f02d4b40b56c821daac0d20ac75d7"),
}
MDNS = ("a6b693698ee8246ca415a994bb6da40d66dddba7", "b63200821135df6286dedc5ba673e5ae8f3862c1")


def require(ok, message):
    if not ok:
        raise ValueError(message)


def git(repo, *args, env=None, data=None):
    return subprocess.check_output(["git", "-C", str(repo), *args], env=env, input=data)


def module(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / file)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def replace_once(text, before, after):
    require(text.count(before) == 1, "unexpected source anchor: " + before[:80])
    return text.replace(before, after, 1)


def write_series(name, raw, revision, tree, splitter):
    directory = ROOT / "patches" / name
    if directory.exists():
        shutil.rmtree(directory)
    directory.mkdir(parents=True)
    parts = splitter.split_diff(raw) if raw else []
    for filename, content in parts:
        require(len(content) < 90 * 1024 * 1024, "patch exceeds GitHub file limit")
        (directory / filename).write_bytes(content)
    (directory / "series").write_text("".join(name + "\n" for name, _ in parts))
    (directory / "expected-tree").write_text(tree + "\n")
    (directory / "base-revision").write_text(revision + "\n")
    require(b"".join((directory / name).read_bytes() for name, _ in parts) == raw,
            "patch series reconstruction mismatch")


def verify_series(repo, name, revision, expected):
    directory = ROOT / "patches" / name
    with tempfile.TemporaryDirectory() as tmp:
        env = dict(os.environ, GIT_INDEX_FILE=str(Path(tmp) / "index"))
        git(repo, "read-tree", revision, env=env)
        for filename in (directory / "series").read_text().splitlines():
            git(repo, "apply", "--cached", "--binary", "--whitespace=nowarn",
                str(directory / filename), env=env)
        require(git(repo, "write-tree", env=env).decode().strip() == expected,
                f"regenerated tree mismatch: {name}")


def main(work, local, evidence):
    require((ROOT / "VERSION").read_text().strip() == "0.0.3", "unexpected promotion base")
    require(not git(ROOT, "status", "--porcelain", "--untracked-files=no"), "dirty tracked checkout")
    checker = module("checker", "verify-candidate-evidence.py")
    checker.verify(evidence)
    replay = module("replay", "replay-final-candidate.py")
    splitter = module("splitter", "split-baseline-patches.py")
    final = json.loads((evidence / "final-candidate-conflicts.json").read_text())
    provenance = json.loads((evidence / "combined-candidate-provenance.json").read_text())
    require(final["integration_passed"] is True, "unverified integration")
    for name, (revision, expected) in PINS.items():
        repo = work if name == "hiddify-core" else work / name
        if name == "ray2sing":
            actual = git(repo, "write-tree").decode().strip()
            require(actual == provenance["ray2sing_patched_tree"], "ray2sing provenance drift")
        else:
            actual = replay.replay(repo, name, local, evidence, final["components"][name]["tree"])
        require(actual == expected, f"release tree drift: {name}")
        require(git(repo, "rev-parse", revision + "^{commit}").decode().strip() == revision,
                f"missing upstream source: {name}")
        raw = git(repo, "diff", "--binary", "--full-index", "--no-renames",
                  "--submodule=short", revision, expected)
        write_series(name, raw, revision, expected, splitter)
        verify_series(repo, name, revision, expected)
        git(ROOT, "update-index", "--cacheinfo", f"160000,{revision},{name}")
    require(git(work, "rev-parse", PINS["hiddify-core"][0] + ":hiddify-sing-box").decode().strip()
            == PINS["hiddify-sing-box"][0], "Core upstream sing-box pin mismatch")
    overlay = provenance["materialized_dependency_overlays"]["replace/MasterDnsVPN"]
    require(overlay["tree"] == MDNS[1], "MasterDNS overlay tree drift")
    require(git(work / "hiddify-sing-box", "rev-parse",
                PINS["hiddify-sing-box"][1] + ":replace/MasterDnsVPN").decode().strip()
            == MDNS[0], "MasterDNS base mismatch")
    raw = (evidence / overlay["patch"]).read_bytes()
    require(raw, "missing MasterDNS patch")
    write_series("masterdns", raw, *MDNS, splitter)
    prepare = ROOT / "scripts/prepare.sh"
    text = prepare.read_text()
    anchor = 'cat > "$ROOT/.build/prepared-sources.lock" <<LOCK'
    text = replace_once(text, anchor, '''# The nested dependency overlay is part of the released source tree.
mdns="$WORK/hiddify-sing-box/replace/MasterDnsVPN"
mdns_base=$(< "$ROOT/patches/masterdns/base-revision")
mdns_expected=$(< "$ROOT/patches/masterdns/expected-tree")
assert_sha "$mdns" "$mdns_base"
apply_series masterdns "$mdns" "$mdns_base" "$mdns_base"
mdns_tree=$(verify_patched_tree "$mdns" "$mdns_base" "$mdns_base" "$mdns_expected")

''' + anchor)
    text = replace_once(text, "RAY_TREE=$ray_tree\nLOCK", "RAY_TREE=$ray_tree\nMASTERDNS_SHA=$mdns_base\nMASTERDNS_TREE=$mdns_tree\nLOCK")
    text = replace_once(text, "Applied all three patch series", "Applied all source and nested dependency patch series")
    prepare.write_text(text)
    common = ROOT / "scripts/_common.sh"
    text = replace_once(common.read_text(), '  assert_sha "$WORK/ray2sing" "$RAY_SHA"\n',
                        '''  assert_sha "$WORK/ray2sing" "$RAY_SHA"
  assert_sha "$WORK/hiddify-sing-box/replace/MasterDnsVPN" "$MASTERDNS_SHA"
  [[ "$(git -C "$WORK/hiddify-sing-box/replace/MasterDnsVPN" write-tree)" == "$MASTERDNS_TREE" ]]
''')
    common.write_text(text)
    build = ROOT / "scripts/build.sh"
    build.write_text(replace_once(build.read_text(), "go build -mod=mod", "go build -mod=readonly"))
    (ROOT / "VERSION").write_text("0.0.4\n")
    readme = ROOT / "README.md"
    text = readme.read_text()
    text = replace_once(text, "VetrOFF-Core 0.0.3 is", "VetrOFF-Core 0.0.4 is")
    text = replace_once(text, "## Builds\n", """The 0.0.4 release trees are reproduced from the root pins, regenerated patch series and the separately pinned MasterDNS overlay. The old migration pipeline remains in Git history for reproducibility. Subsequent updates must regenerate the relevant patch series and verify the new source trees before promotion.

## Builds
""")
    readme.write_text(text)
    # Retire only the old 0.0.4 publication and baseline-specific CI entry
    # points; historical scripts and checkpoints remain reproducible in Git.
    for filename in ("candidate-matrix.yml", "publish-004.yml", "combined-candidate.yml", "upstream-merge-audit.yml"):
        (ROOT / ".github/workflows" / filename).unlink()
    git(ROOT, "add", "-A", "--", "patches", "scripts/prepare.sh", "scripts/_common.sh",
        "scripts/build.sh", "VERSION", "README.md", ".github/workflows")
    print("Prepared unified 0.0.4 patch stack; promotion still requires platform validation.")


if __name__ == "__main__":
    main(*(Path(arg).resolve() for arg in sys.argv[1:]))
