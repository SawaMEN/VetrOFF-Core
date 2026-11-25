#!/usr/bin/env python3
"""Build a broad isolated v5 candidate; never replace unavailable gitlinks."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import importlib.util

ROOT = Path(__file__).resolve().parent
BASELINE = "f76b0611b09245e2f636692487b774c03a18d6c2"
TARGET = "1267fe6aac8d11f3a562e8feecad11d11f48f965"
CORE_BASELINE = "e4823f0dda24d906354b09c6a919d366424216e7"
CORE_TEST = "v2/hcore/hotreload_test.go"
CORE_TEST_BLOB = "fae53d4ba78ab40679b3858db49c886011c3fc27"
LOCKED = {
    "replace/sing": "b40ea077780dbc1babef7d035d4b0fea8d9ad6f2",
    "replace/soar": "e18587bc9fb62f326124d1f3eb0ceb31708bd077",
}
BLOCKED_FILES = {
    "protocol/hiddify/soar/outbound.go",
    "protocol/hiddify/soar/h_soar_test.go",
}
APP_DOC = "docs/hiddify/app_considerations.md"


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args])


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    obj = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj


def oid(repo, ref, path):
    return git(repo, "rev-parse", ref + ":" + path).decode().strip()


def changed(repo, a, b):
    return set(git(repo, "diff", "--name-only", "--no-renames", a, b).decode().splitlines())


def links(repo, tree):
    result = {}
    for line in git(repo, "ls-tree", "-r", tree).decode().splitlines():
        meta, path = line.split("\t", 1)
        mode, kind, commit = meta.split()
        if mode == "160000":
            require(kind == "commit", f"invalid gitlink {path}")
            result[path] = commit
    return result


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def core_test_update(work, report):
    require(git(work, "write-tree").decode().strip() == CORE_BASELINE, "Core baseline drift")
    require(oid(work, CORE_BASELINE, CORE_TEST) == CORE_TEST_BLOB, "Core test blob drift")
    old = git(work, "show", CORE_BASELINE + ":" + CORE_TEST)
    require((work / CORE_TEST).read_bytes() == old, "Core test worktree drift")
    start = b"\t// something that cannot be hot reloaded changes (the log level): refused, core unchanged\n"
    end = b"\t// the TUN inbound changes (here: TUN is turned on): refused so the app restarts instead\n"
    require(old.count(start) == old.count(end) == 1, "Core hot reload anchor drift")
    first, last = old.index(start), old.index(end)
    require(first < last, "Core test block order drift")
    replacement = b'''	// A log-level-only reload must preserve the service and all outbounds.
	_, err = ChangeHiddifySettings(&ChangeHiddifySettingsRequest{HiddifySettingsJson: hotReloadSettings(t, newMixedPort, "error")}, false)
	require.NoError(t, err)
	proxyB = runningOutbound(t, "proxy-b")
	proxyC := runningOutbound(t, "proxy-c")
	response, err = HotReload(static.BaseContext, &StartRequest{
		ConfigContent: profile(socks("proxy-a", 1080), socks("proxy-b", 2081), socks("proxy-c", 1082)),
	})
	require.NoError(t, err)
	require.Equal(t, MessageType_EMPTY, response.MessageType)
	require.Same(t, instance, runningTestService(t).Instance(), "log level does not restart Core")
	require.Same(t, proxyA, runningOutbound(t, "proxy-a"))
	require.Same(t, proxyB, runningOutbound(t, "proxy-b"))
	require.Same(t, proxyC, runningOutbound(t, "proxy-c"))
	require.True(t, listening(newMixedPort), "the listener remains open")
	require.Equal(t, "error", log.FormatLevel(instance.Box().LogFactory().Level()))

	// Log level and an outbound removal can also be applied together.
	_, err = ChangeHiddifySettings(&ChangeHiddifySettingsRequest{HiddifySettingsJson: hotReloadSettings(t, newMixedPort, "warn")}, false)
	require.NoError(t, err)
	response, err = HotReload(static.BaseContext, &StartRequest{
		ConfigContent: profile(socks("proxy-a", 1080), socks("proxy-b", 2081)),
	})
	require.NoError(t, err)
	require.Equal(t, MessageType_EMPTY, response.MessageType)
	require.Same(t, instance, runningTestService(t).Instance())
	require.Same(t, proxyA, runningOutbound(t, "proxy-a"))
	require.Same(t, proxyB, runningOutbound(t, "proxy-b"))
	require.Nil(t, runningOutbound(t, "proxy-c"))
	require.Equal(t, "warn", log.FormatLevel(instance.Box().LogFactory().Level()))

'''
    new = old[:first] + replacement + old[last:]
    anchor = b'\t"github.com/sagernet/sing-box/daemon"\n'
    require(new.count(anchor) == 1, "Core import anchor drift")
    new = new.replace(anchor, anchor + b'\t"github.com/sagernet/sing-box/log"\n', 1)
    new = subprocess.check_output(["gofmt"], input=new)
    stage = load("stage_core", "stage-final-conflicts.py")
    tree = stage.stage(work, CORE_BASELINE, {CORE_TEST: new}, {})
    require(changed(work, CORE_BASELINE, tree) == {CORE_TEST}, "Core candidate scope drift")
    patch = report / "broad-core.patch"
    patch.write_bytes(git(work, "diff", "--binary", "--full-index", CORE_BASELINE, tree))
    return {"core_baseline": CORE_BASELINE, "core_candidate_tree": tree,
            "core_patch_sha256": digest(patch), "core_changed_paths": [CORE_TEST]}


def prepare(work, report):
    repo = work / "hiddify-sing-box"
    require(git(repo, "write-tree").decode().strip() == BASELINE, "baseline drift")
    manifest = json.loads((ROOT / "broad-v5-target.json").read_text())
    require(len(manifest) == 63, "manifest drift")
    report.mkdir(parents=True, exist_ok=True)
    audit = load("audit", "audit-next-sing-box.py")
    # The full audit intentionally fails if its required gitlinks cannot
    # be fetched. Only this explicitly reduced candidate may continue.
    result = audit.main(work, report / "full-audit")
    details = json.loads((report / "full-audit/update-audit.json").read_text())
    require(not details["remaining_conflicts"], "unresolved full-source conflicts")
    require(details["upstream_target"] == TARGET and details["baseline_tree"] == BASELINE,
            "audit provenance drift")
    require(result == (1 if details["unavailable_dependencies"] else 0),
            "unexpected full-audit result")
    require(set(details["changed_upstream_paths"]) == set(manifest), "upstream path drift")
    for path, target in manifest.items():
        require(oid(repo, TARGET, path) == target, f"upstream blob drift: {path}")
    full = details["reviewed_tree"]
    require(changed(repo, BASELINE, full) <= set(manifest), "unexpected full merge paths")
    docs = {path for path in manifest if path.startswith("docs/")}
    excluded = docs | BLOCKED_FILES | set(LOCKED)
    require(len(docs) == 13 and len(set(manifest) - excluded) == 46, "scope drift")
    require(set(LOCKED) <= set(manifest), "missing deferred gitlinks")
    original_links = links(repo, BASELINE)
    for path, revision in LOCKED.items():
        require(original_links.get(path) == revision, f"old gitlink drift: {path}")
    files = {path: git(repo, "show", BASELINE + ":" + path)
             for path in docs | BLOCKED_FILES}
    # Keep all previous fork documentation; do not advertise the blocked
    # Soar/buffer changes or native client work as available features.
    files[APP_DOC] = files[APP_DOC].rstrip() + b"""

## Broad v5 candidate (not released)

The candidate includes the available sing-box runtime changes at
1267fe6 with the original Sing and Soar gitlinks retained. The Soar
Abort integration, Sing buffer updates, native iOS OOM service
integration, and Core/client subscription integration are not included.
See the exact upstream changelog for deferred work, not current behavior.
"""
    # The fork exposes MASQUE availability through the build-tagged
    # masqueLinkState method, rather than the upstream package constant.
    warp = "protocol/warp/h_last_way.go"
    text = git(repo, "show", full + ":" + warp)
    require(text.count(b"if !masqueIncluded {") == 1, "WARP last tunnel guard drift")
    for path, expected in (
        ("protocol/warp/endpoint_warp_masque.go", b"func (*masqueLinkState) included() bool { return true }"),
        ("protocol/warp/endpoint_warp_masque_stub.go", b"func (*masqueLinkState) included() bool { return false }"),
    ):
        require(expected in git(repo, "show", full + ":" + path),
                f"fork MASQUE build guard missing: {path}")
    require(b"w.masqueLinkState.included()" in git(repo, "show", full + ":protocol/warp/h_mode.go"),
            "fork MASQUE runtime guard missing")
    files[warp] = subprocess.check_output(
        ["gofmt"], input=text.replace(b"if !masqueIncluded {",
                                    b"if !w.masqueLinkState.included() {", 1))
    partial = load("partial", "test-partial-v5-update.py")
    for path, imported in partial.DUPLICATES.items():
        require(path not in manifest, f"unexpected overlap: {path}")
        files[path] = partial.fix_duplicate(git(repo, "show", BASELINE + ":" + path),
                                            path, imported)
    stage = load("stage", "stage-final-conflicts.py")
    candidate = stage.stage(repo, full, files, LOCKED)
    selected = set(manifest) - excluded
    delta = changed(repo, BASELINE, candidate)
    require(selected <= delta <= selected | {APP_DOC} | set(partial.DUPLICATES),
            f"unexpected reduced delta: {sorted(delta ^ selected)}")
    require(links(repo, candidate) == original_links, "nested pin changed")
    for path in excluded - {APP_DOC}:
        require(oid(repo, candidate, path) == oid(repo, BASELINE, path), f"deferred file changed: {path}")
    patch = report / "broad-v5.patch"
    patch.write_bytes(git(repo, "diff", "--binary", "--full-index", "--no-renames",
                          "--submodule=short", BASELINE, candidate))
    metadata = {
        "baseline": BASELINE, "candidate_tree": candidate, "upstream_target": TARGET,
        "selected_runtime_commits": 10, "deferred_runtime_commits": 3,
        "upstream_documentation_commits": 8,
        "upstream_code_paths": sorted(selected),
        "changed_paths": sorted(delta),
        "fork_compatibility_adaptations": [warp],
        "deferred_paths": sorted(excluded),
        "nested_gitlinks": original_links,
        "patch_sha256": digest(patch),
        "full_update": False, "stable_promotion": False,
    }
    metadata.update(core_test_update(work, report))
    (report / "broad-v5.json").write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n")
    apply(work, report)
    print(json.dumps(metadata, indent=2))


def apply(work, report):
    repo = work / "hiddify-sing-box"
    metadata = json.loads((report / "broad-v5.json").read_text())
    patch = report / "broad-v5.patch"
    require(metadata["baseline"] == BASELINE and metadata["upstream_target"] == TARGET
            and metadata["full_update"] is False and metadata["stable_promotion"] is False,
            "unexpected candidate metadata")
    require(digest(patch) == metadata["patch_sha256"], "patch hash mismatch")
    require(git(repo, "write-tree").decode().strip() == BASELINE, "build baseline drift")
    require(links(repo, BASELINE) == metadata["nested_gitlinks"], "baseline gitlink drift")
    git(repo, "apply", "--index", "--binary", "--whitespace=nowarn", str(patch.resolve()))
    tree = git(repo, "write-tree").decode().strip()
    require(tree == metadata["candidate_tree"], "candidate tree mismatch")
    require(links(repo, tree) == metadata["nested_gitlinks"], "candidate gitlink drift")
    require(changed(repo, BASELINE, tree) == set(metadata["changed_paths"]),
            "candidate file scope drift")
    core_patch = report / "broad-core.patch"
    require(metadata["core_baseline"] == CORE_BASELINE and
            metadata["core_changed_paths"] == [CORE_TEST], "Core metadata drift")
    require(digest(core_patch) == metadata["core_patch_sha256"], "Core patch hash mismatch")
    require(git(work, "write-tree").decode().strip() == CORE_BASELINE, "Core build baseline drift")
    git(work, "apply", "--index", "--binary", "--whitespace=nowarn", str(core_patch.resolve()))
    core_tree = git(work, "write-tree").decode().strip()
    require(core_tree == metadata["core_candidate_tree"], "Core candidate tree mismatch")
    require(changed(work, CORE_BASELINE, core_tree) == {CORE_TEST}, "Core changed path mismatch")
    print(f"Broad candidate verified: {tree}; Core: {core_tree}")


if __name__ == "__main__":
    action, work, report = sys.argv[1:]
    require(action in ("prepare", "apply"), "invalid action")
    {"prepare": prepare, "apply": apply}[action](Path(work).resolve(), Path(report).resolve())
