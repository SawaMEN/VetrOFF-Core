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

def checksum_union(data, hashes, path="go.sum"):
    def records(blob):
        result = {}
        for line in blob.splitlines():
            module, version, digest = line.split()
            assert digest.startswith(b"h1:"), "Unsupported checksum"
            key = (module, version)
            assert key not in result, f"Duplicate checksum: {key!r}"
            result[key] = digest
        return result

    original, fork, official = (
        records(checked_blob(data[key], path, expected))
        for key, expected in zip(("base", "patched_tree", "upstream"), hashes)
    )
    combined = dict(fork)
    for key, digest in official.items():
        if key in combined and combined[key] != digest:
            raise ValueError(f"Checksum mismatch for {key!r}; manual review required")
        combined[key] = digest
    # Never discard a checksum from either active side. Entries removed by both
    # sides stay absent. Final ordering/pruning belongs to go mod tidy after
    # the go.mod and dependency/API conflicts are resolved.
    assert set(fork) | set(official) == set(combined)
    print(f'{data["component"]}: {path} fork={len(fork)}, upstream={len(official)}, union={len(combined)}')
    return b"".join(b" ".join((*key, combined[key])) + b"\n" for key in sorted(combined))

def apply_reviewed(data, resolutions):
    component = data["component"]
    assert set(resolutions) <= set(data["conflicts"])
    with tempfile.TemporaryDirectory() as tmp:
        env = dict(os.environ, GIT_INDEX_FILE=str(Path(tmp) / "index"))
        git("read-tree", data["tentative_tree"], env=env)
        for path, content in resolutions.items():
            sha = git("hash-object", "-w", "--stdin", payload=content).decode().strip()
            git("update-index", "--cacheinfo", f"100644,{sha},{path}", env=env)
            assert git("show", ":" + path, env=env) == content
            output = report / (component + "-resolved") / path
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(content)
            print(f"Reviewed resolution: {component}/{path} -> {sha}")
        tree = git("write-tree", env=env).decode().strip()
        delta = git("diff", "--name-only", "-z", data["tentative_tree"], tree)
        assert set(os.fsdecode(p) for p in delta.split(b"\0") if p) == set(resolutions)
        (report / (component + "-reviewed-resolutions.patch")).write_bytes(
            git("diff", "--binary", "--full-index", data["tentative_tree"], tree)
        )
    remaining = sorted(set(data["conflicts"]) - set(resolutions))
    data.update(reviewed_tree=tree, reviewed_resolutions=sorted(resolutions),
                remaining_conflicts=remaining)
    (report / (component + "-reviewed.json")).write_text(json.dumps(data, indent=2) + "\n")
    print(f"{component}: {len(resolutions)} reviewed resolutions, {len(remaining)} unresolved paths")
    for path in remaining:
        print("  " + path)

resolutions["go.sum"] = checksum_union(data, (
    "7a9f62770c5505d8e73600a4c82a190bf423cd3f",
    "b8a3aa629a919306b11557a435891361b5bec8fc",
    "a97d6509396b5dea6e670ad6b4e1428a53a78459",
))
# Both sides remove the five-second sleep. Preserve upstream documentation
# and the fork's formatting of non-string panic values.
path = "v2/config/debug.go"
old = checked_blob(base, path, "6e2bbab65fb3b018555cb821e7d20b490e5f4176")
own = checked_blob(patched, path, "7453e371f34c58bf8a61b87d9a2f3ef3134c03d8")
new = checked_blob(upstream, path, "648d11db091e60da729f3fca2a26dda21ede6f89")
assert old.count(b"<-time.After(5 * time.Second)") == 1
assert b"time.After" not in own and b"time.After" not in new
assert own.count(b"%s panic: %v") == 1 and new.count(b"%s panic: %s") == 1
assert own.split(b"func DeferPanicToError")[0] == new.split(b"// DeferPanicToError")[0]
resolutions[path] = new.replace(b"%s panic: %s", b"%s panic: %v", 1)
formatted = subprocess.check_output(["gofmt"], input=resolutions[path])
assert formatted == resolutions[path], "Unexpected formatting in debug.go"
# Core state listeners: retain upstream's last-error replay and burst buffer,
# but keep the fork's explicit handling of stream.Send errors.
path = "v2/hcore/coreinfo.go"
old = checked_blob(base, path, "97031cc888d544491ca0ba44b7b5fea28665f0b4")
own = checked_blob(patched, path, "c05dd5ee805e3ce9b6d73d573065e743c7375a8f")
new = checked_blob(upstream, path, "cb2a67987817098d8c267af821baf156cd5c032e")
assert b"Subscribe(1)" in own and b"Subscribe(32)" in new
assert b"lastCoreInfo.Store(&info)" in new and b"func currentCoreInfo()" in new
assert own.count(b"if err := stream.Send(") == 3  # includes commented example
assert new.count(b"\tstream.Send(currentCoreInfo())") == 1
assert new.count(b"\t\t\tstream.Send(info)") == 1
resolutions[path] = new.replace(
    b"\tstream.Send(currentCoreInfo())",
    b"\tif err := stream.Send(currentCoreInfo()); err != nil {\n\t\treturn err\n\t}",
).replace(
    b"\t\t\tstream.Send(info)",
    b"\t\t\tif err := stream.Send(info); err != nil {\n\t\t\t\treturn err\n\t\t\t}",
)
assert subprocess.check_output(["gofmt"], input=resolutions[path]) == resolutions[path]

path = "platform/docker/docker-compose.yml"
old = checked_blob(base, path, "9a59c45885041a8b0d9a3af3d8d35fe813bc5985")
own = checked_blob(patched, path, "fb5f690f20acf1d8c4f8789936066ad0985a1811")
new = checked_blob(upstream, path, "1e98059fc2340f33e8ff19c24b77890b8778f7f3")
old, own, new = (blob.replace(b"\r\n", b"\n") for blob in (old, own, new))
assert old.replace(b'    command: ["/opt/hiddify.sh"]\n', b"") == own
assert b'command: ["/opt/hiddify.sh"]' not in new
seq = iter(new.splitlines())
assert all(any(candidate == line for candidate in seq) for line in own.splitlines())
resolutions[path] = new
apply_reviewed(data, resolutions)

repo = repo / "hiddify-sing-box"
data = json.loads((report / "hiddify-sing-box.json").read_text())
assert data["base"] == "004371d6be2395ee4379054a8cf113f339dbe231"
assert data["upstream"] == "a26e58b376987a3305b201ec87fbc8918f620ad3"
resolutions = {"go.sum": checksum_union(data, (
    "82eb63f703d7d2a94cae5ad2cfda749e206edadf",
    "46d0ed1b661edf3e8fd4eb3442a4452f77617bf4",
    "2d48f0a740e05e60a6fbb008c0a75526a4e5ea78",
)), "test/go.sum": checksum_union(data, (
    "481125699ae1e1752bb28ba2c7dd198d97da936d",
    "40c5123068c74a41e075116d7f685ef0c3a0af64",
    "40a4cd2213f5ac3ee3866d319baa2c7c426de329",
), "test/go.sum")}


# These two constants have identical code on both sides; only marker-comment
# spacing differs. Keep the fork's gofmt-style comments.
for path, hashes in (
    ("constant/err.go", (
        "4f070c4d6fc270a0c062438c82df13c6c1f050e9",
        "82a7f8cd2ebf5b371542f473579976380052426e",
        "dff7f341798df7637cc39211b3a9344d83c53845",
    )),
    ("constant/v2ray.go", (
        "89d9d7736d6b7db87f3595bf5d11a76cfafbf372",
        "7e87b35d07ec4318080e1fec2810090893e89b28",
        "235b72e4e6416be83e2995f354a5557038dc0e32",
    )),
):
    old, own, new = (checked_blob(data[rev], path, sha)
                     for rev, sha in zip(("base", "patched_tree", "upstream"), hashes))
    assert new.replace(b"//H", b"// H") == own
    resolutions[path] = own

# The desktop D-Bus discovery implementations differ only in the two loops.
# Preserve SplitSeq to avoid allocating intermediate slices.
path = "common/settings/proxy_linux.go"
old = checked_blob(data["base"], path, "b37148720b80c3cc24239c80b7339a97eead254d")
own = checked_blob(data["patched_tree"], path, "96a5ad1732c18e4d0d8b03b4dbb8fe66e24aa446")
new = checked_blob(data["upstream"], path, "28b06aa6de91917492b8fe594061280a12e55e53")
assert new.replace(
    b'for _, line := range strings.Split(string(content), "\\n") {',
    b'for line := range strings.SplitSeq(string(content), "\\n") {',
).replace(
    b'for _, variable := range strings.Split(string(content), "\\x00") {',
    b'for variable := range strings.SplitSeq(string(content), "\\x00") {',
) == own
resolutions[path] = own
apply_reviewed(data, resolutions)
