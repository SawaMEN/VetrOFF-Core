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
        # A modify/delete conflict may already contain the selected version in
        # merge-tree's provisional tree. It is resolved even with zero diff.
        changed = set(os.fsdecode(p) for p in delta.split(b"\0") if p)
        assert changed <= set(resolutions), ("unexpected changes", changed - set(resolutions))
        for path, content in resolutions.items():
            assert git("show", tree + ":" + path) == content
        for path in sorted(set(resolutions) - changed):
            print(f"Reviewed unchanged provisional entry: {component}/{path}")
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
# Preserve fork protocol identifiers and the new upstream lazy-outbound type.
path = "constant/proxy.go"
old, own, new = (checked_blob(data[rev], path, sha) for rev, sha in zip(
    ("base", "patched_tree", "upstream"),
    ("0c5bff59e2e2cd6ea1eb7dbf28096d23e786762c",
     "b888846a0fd0054db5a7d0195a172d7d8462342e",
     "fb48cade69783e82a11fccf6d78910834067f288")))
assert own.count(b'TypeHInvalidConfig = "hinvalid"') == 1
assert new.count(b'TypeHLazy          = "hlazy"') == 1
candidate = own.replace(b'\tTypeXray           = "xray"', b'\tTypeHLazy          = "hlazy" // H: lazy outbound placeholder\n\tTypeXray           = "xray"', 1)
candidate = candidate.replace(b'\tcase TypeDNSTT:', b'\tcase TypeSudoku:\n\t\treturn "Sudoku"\n\tcase TypeCall:\n\t\treturn "Call"\n\tcase TypeDNSTT:', 1)
assert b'TypeHLazy' in candidate
assert candidate.count(b'URLTestModeFailover = "failover"') == 1
assert b'TypeVetrOFF' in candidate and b'TypeSmartDNSPool' in candidate
resolutions[path] = subprocess.check_output(["gofmt"], input=candidate)

# The fork keeps readiness-only implementations compatible. The upstream
# IsWarming helper is additive and can use the same shared Status enum.
path = "adapter/h_status.go"
old, own, new = (checked_blob(data[rev], path, sha) for rev, sha in zip(
    ("base", "patched_tree", "upstream"),
    ("0b09a80400f6542696e6c89e7f59480c68362ecf",
     "5e788b1830f6ab50d61469f75d0657ed0631b8c3",
     "30346206ba9ceb7cb6d661f8f997480f82d3609c")))
interface = checked_blob(data["patched_tree"], "adapter/outbound.go", "6e6c3987e8575fd84c062bcf17ccc472221b8d5b")
assert b"IsReady() bool" in interface and b"func OutboundStatus(" in own
assert git("rev-parse", data["tentative_tree"] + ":adapter/outbound.go").decode().strip() == "6e6c3987e8575fd84c062bcf17ccc472221b8d5b"
marker = b"// IsWarming reports"
assert marker not in own and new.count(marker) == 1
candidate = own.rstrip() + b"\n\n" + new[new.index(marker):]
resolutions[path] = subprocess.check_output(["gofmt"], input=candidate)
path = "adapter/h_display.go"
old, own, new = (checked_blob(data[rev], path, sha) for rev, sha in zip(
    ("base", "patched_tree", "upstream"),
    ("cd547ceb5c229990d217d75c3ed26916cab20a1a",
     "f9dd9baf357997cbb9114daca0ae1fe4b3716121",
     "c2ac8efb5a69732a9f58a95ef9f5495a72389927")))
assert new.replace(b"outbound.Status()", b"OutboundStatus(outbound)") == own
resolutions[path] = own

path = "adapter/h_display_test.go"
old, own, new = (checked_blob(data[rev], path, sha) for rev, sha in zip(
    ("base", "patched_tree", "upstream"),
    ("337fa79a4709594e736dbee599d09563335c61bd",
     "bf3c11837955284b19611213b39f82ec8137eb6e",
     "7149c640703f57e45c2471deea927d515fcdd53c")))
start = own.index(b"// A v5 outbound only needs")
end = own.index(b"type hNamedOutbound", start)
assert own[:start] + own[end:] == new
resolutions[path] = own
# Extend the fork's URL test history with upstream's LastGood field.
path = "adapter/experimental.go"
old, own, new = (checked_blob(data[rev], path, sha) for rev, sha in zip(
    ("base", "patched_tree", "upstream"),
    ("43289e266137dfcf7b384a8b86bf0842ee6c473f",
     "e54ef007aabe87a709266c8bc601fe410d7058c1",
     "b53dffb2afa2fe9bd2ef2a7a5c69df59876e460b")))
marker = b"\t// LastGood: the last delay"
start = new.index(marker)
end = new.index(b"\n}", start)
addition = new[start:end]
assert b'LastGood uint16' in addition
assert (new[:start] + new[end+1:]).replace(b"//H", b"// H") == own.replace(b"//H", b"// H")
needle = b'\tError string '
assert own.count(needle) == 1
end = own.index(b"\n}", own.index(needle))
resolutions[path] = subprocess.check_output(["gofmt"], input=own[:end] + b"\n" + addition + own[end:])

# Keep the legacy pool_tolerance JSON key alongside current options.
path = "option/balancer.go"
old, own, new = (checked_blob(data[rev], path, sha) for rev, sha in zip(
    ("base", "patched_tree", "upstream"),
    ("ea2ddd4e3fa83b09d0b6af299a99199c8bb75d41",
     "d12654d85b7c9fb6f75b2aa83b66e36a27bcac71",
     "fc79821dd9b8c86c5782a36ccef8c0bce81cb6fe")))
assert b'PoolTolerance uint16' in own and b'PoolTolerance' not in new
needle = b'\tPool int '
assert new.count(needle) == 1
end = new.index(b"\n", new.index(needle))
candidate = new[:end] + b'\n\tPoolTolerance uint16 \x60json:"pool_tolerance,omitempty"\x60 // H: legacy alias when tolerance is unset' + new[end:]
resolutions[path] = subprocess.check_output(["gofmt"], input=candidate)

# Preserve fork configuration field as well as the new upstream field.
path = "option/psiphon.go"
old, own, new = (checked_blob(data[rev], path, sha) for rev, sha in zip(
    ("base", "patched_tree", "upstream"),
    ("309d106fdb5b7ee4bb4bfd3202c45bfce50e158f",
     "ac2f3f6eb4a5f153933a80867b2ca30633f45093",
     "d8c9d14a1ebb3ad0c32c4eee2d76d8045c6bf0d3")))
assert b'ConduitPairingID' in own and b'ConduitPairingID' not in new
needle = b'\tPropagationChannelID'
assert new.count(needle) == 1
candidate = new.replace(needle, b'\tConduitPairingID string \x60json:"conduit_pairing_id,omitempty"\x60\n' + needle, 1)
resolutions[path] = subprocess.check_output(["gofmt"], input=candidate)

# Keep the fork test fixture shape and lower bounds on asynchronous counts.
path = "common/monitoring/h_selected_test.go"
old, own, new = (checked_blob(data[rev], path, sha) for rev, sha in zip(
    ("base", "patched_tree", "upstream"),
    ("be56f71f407cf274f2591d6d4a903df8442d0030",
     "8d93973415da80e08bef81d5b18db7a7c6605282",
     "377e1435d3637d586ecd5827143856d6fe3588b3")))
candidate = new.replace(
    b'&fakeGroup{fakeOutbound: fakeOutbound{tag: "select"},',
    b'&fakeGroup{tag: "select",',
).replace(b"dials.Load() == 1", b"dials.Load() >= 1").replace(
    b"dials.Load() == 2", b"dials.Load() >= 2")
assert candidate == own
resolutions[path] = own
# Monitoring changes are one coordinated group: preserve the fork's
# synchronization and immutable snapshots while incorporating upstream scheduling.
path = "common/monitoring/h_registry.go"
old, own, new = (checked_blob(data[rev], path, sha) for rev, sha in zip(
    ("base", "patched_tree", "upstream"),
    ("3a07d1dc2d3c989c1af2d20ce8f1a03bbdc93212",
     "152fb846992bbba858e6391786777f32e61763f6",
     "de3fbc729477b4352aedf24b73c3e516980b592f")))
assert own.count(b"if m.started.Load() {") == 2
assert new.count(b"if m.started {") == 2
candidate = new.replace(b"if m.started {", b"if m.started.Load() {")
assert b"m.keepTopReload(registry)" in candidate and b"func (m *OutboundMonitoring) earlyObserver(" in candidate
resolutions[path] = subprocess.check_output(["gofmt"], input=candidate)

path = "common/monitoring/h_join.go"
# This file was independently added by both sides.
assert subprocess.run(["git", "-C", str(repo), "cat-file", "-e", data["base"] + ":" + path], capture_output=True).returncode != 0
own = checked_blob(data["patched_tree"], path, "7f19c29d4ad0b2a4af2565ac0496fa2efe001444")
new = checked_blob(data["upstream"], path, "a7e0c273ffdb99c7ef70b6c72070dda1353408bd")
marker = b"// finishRunningTest publishes"
assert own.count(marker) == 1 and marker not in new
assert own[:own.index(marker)].rstrip() == new.replace(
    b"\t// keep top asked for this test after the other one had told it its result: it is the answer\n\t// to this one too\n\tm.keepTopNoted(task.outboundTag)\n", b"").rstrip()
resolutions[path] = subprocess.check_output(["gofmt"], input=new.rstrip() + b"\n\n" + own[own.index(marker):])

path = "common/monitoring/h_settle.go"
old, own, new = (checked_blob(data[rev], path, sha) for rev, sha in zip(
    ("base", "patched_tree", "upstream"),
    ("7f56ff7fd435e7ddd10cb65c168e6ebfe2496696",
     "29050e753b9cecd97854ddf89f873938c61877b2",
     "18e33dc2c41798780d29e7e72aedef60b45acf4d")))
needle = b"\tm.handResult(task, outcome)\n"
assert new.count(needle) == 1
assert b"m.finishRunningTest(state, task.testDone)" in own
candidate = new.replace(needle,
    b"\tm.finishRunningTest(state, task.testDone) // H: the next stage must start a fresh test\n" + needle)
needle = b"\t\tm.history.StoreURLTestHistory(task.outboundTag, &state.history)"
assert candidate.count(needle) == 1
candidate = candidate.replace(needle,
    b"\t\tsnapshot := state.history // H: storage readers own an immutable snapshot\n\t\tm.history.StoreURLTestHistory(task.outboundTag, &snapshot)")
assert b"func (m *OutboundMonitoring) abandon(" in candidate and b"m.keepTopNoted(task.outboundTag)" in candidate
resolutions[path] = subprocess.check_output(["gofmt"], input=candidate)

path = "common/monitoring/h_staged.go"
old, own, new = (checked_blob(data[rev], path, sha) for rev, sha in zip(
    ("base", "patched_tree", "upstream"),
    ("77fb7ca007d74d24b695b05e4e532aaddb0427fc",
     "a61ca080f55b33f86191826da2e0b0e2adad009f",
     "b1c01a291fed315b1154b45efcac269432395fc5")))
addition = b"\tif factor := m.timeoutFactor(out); factor != 1 {\n\t\tmarks = scaleMarks(marks, factor)\n\t}\n"
marker = b"// scaleMarks is marks"
assert new.count(addition) == 1 and new.count(marker) == 1
assert new[:new.index(marker)].replace(addition, b"").rstrip() == own.rstrip()
resolutions[path] = new
# Coordinate the main monitor with the previously reviewed registry, join,
# settle and staged implementations. The upstream file is the base for the
# candidate, including lazy outbounds and early group subscriptions.
path = "common/monitoring/outbound_monitoring.go"
old, own, new = (checked_blob(data[rev], path, sha) for rev, sha in zip(
    ("base", "patched_tree", "upstream"),
    ("d27cc83028faec8b05140900291132a5ae583d46",
     "05914a8b8712a9fd23e29a62f6319f792c0f3a5e",
     "3b933447bf2de2776d299eaf0c3f01c2c1b21be1")))
def replace_once(blob, before, after):
    assert blob.count(before) == 1, (path, before)
    return blob.replace(before, after, 1)
def function_blob(blob, signature):
    start = blob.index(signature)
    end = blob.index(b"\n}\n", start) + 3
    return blob[start:end]
candidate = new
# Atomic started remains the contract for the fork's registry and tests.
candidate = replace_once(candidate, b"\tstarted          bool\n", b"\tstarted          atomic.Bool\n")
candidate = replace_once(candidate, b"\t\tstarted:         false,\n", b"")
candidate = replace_once(candidate, b"if !m.started {", b"if !m.started.Load() || m.closed {")
candidate = replace_once(candidate, b"m.started = true", b"m.started.Store(true)")
candidate = replace_once(candidate, b"m.closed = true   //H", b"m.closed = true   //H\n\t\tm.started.Store(false)")
# Cancel before joining workers, preserving the upstream closed guard.
candidate = replace_once(candidate, b"\t\tm.access.Unlock() //H\n\t\tm.stopTimerWorkers()", b"\t\tm.access.Unlock() //H\n\t\tm.cancel()\n\t\tm.stopTimerWorkers()")
candidate = replace_once(candidate, b"\t\tm.cancel()\n\t\tm.workerWG.Wait()", b"\t\tm.workerWG.Wait()")
candidate = replace_once(candidate, b"if !m.started || m.closed {", b"if !m.started.Load() || m.closed || m.ctx.Err() != nil {")
# Keep each execution's completion channel rather than closing a later run.
start = candidate.index(b"\tstate.testDone = make(chan struct{}) //H")
end = candidate.index(b"\tif task.waits < dependencyWaits", start)
fork_start = own.index(b"\ttask.testDone = make(chan struct{})")
fork_end = own.index(b"\tif task.waits < dependencyWaits", fork_start)
candidate = candidate[:start] + own[fork_start:fork_end] + candidate[end:]
candidate = replace_once(candidate, b"type testTask struct {\n", b"type testTask struct {\n\ttestDone chan struct{} // H: completion belongs to this execution\n")
# Snapshot history under its lock and keep the fork's IP provider interface;
# preserve the upstream timeout scaling and lazy-outbound acquisition.
candidate = replace_once(candidate, b"\tidx := m.currentLinkIndex.Load()\n\tfactor :=", b"\tout.mu.Lock()\n\tprevious := out.history\n\tout.mu.Unlock()\n\tidx := m.currentLinkIndex.Load()\n\tfactor :=")
candidate = replace_once(candidate, b"(out.history.IPv6 == nil || out.history.IPv6FromCache)", b"(previous.IPv6 == nil || previous.IPv6FromCache)")
candidate = replace_once(candidate, b"if out.history.IpInfo == nil || out.history.IpInfoFromCache", b"if previous.IpInfo == nil || previous.IpInfoFromCache")
start = own.index(b"\t\tvar newip *ipinfo.IpInfo")
end = own.index(b"\t\tif err == nil {", start)
candidate = replace_once(candidate, b"\t\tnewip, t, err := ipinfo.GetIpInfo(m.logger, ctx, out.outbound, m.ipInfoURL)\n", own[start:end])
candidate = replace_once(candidate, b"mergeIpInfo(out.history.IpInfo, newip)", b"mergeIpInfo(previous.IpInfo, newip)")
# Retain queue rollback on saturation, and immutable history snapshots.
signature = b"func (m *OutboundMonitoring) enqueueTask("
candidate = replace_once(candidate, function_blob(candidate, signature), function_blob(own, signature))
candidate = replace_once(candidate, b"m.history.StoreURLTestHistory(outcome.outboundTag, &state.history)", b"snapshot := state.history // H: immutable history snapshot\n\tm.history.StoreURLTestHistory(outcome.outboundTag, &snapshot)")
candidate = replace_once(candidate, b"return &state.history", b"return &snapshot")
# Keep the upstream cycle ordering and LastGood behavior, as they are
# coordinated with its added cycleOrder implementation and cache semantics.
assert b"return cycleOrder(tags, delays)" in candidate
assert b"state.history.LastGood = outcome.history.Delay" in candidate
assert b"m.abandon(task, errNotRegistered)" in candidate
assert b"m.keepTop.options = options.KeepTop" in candidate
assert b"adapter.AcquireOutbound(out.outbound)" in candidate
resolutions[path] = subprocess.check_output(["gofmt"], input=candidate)

# Preserve existing JSON key while accepting the new upstream spelling.
path = "option/monitoring.go"
old, own, new = (checked_blob(data[rev], path, sha) for rev, sha in zip(
    ("base", "patched_tree", "upstream"),
    ("2c87d244f56196c077753db369b18acdb46ae26d",
     "6828eaea76501f9bfbec1dddd62de4a65bbd4a13",
     "91ccb9d81257250daffe4417e60795055223b397")))
assert b'json:"ip_info_url,omitempty"' in own
assert b'json:"ipinfo_url,omitempty"' in new
candidate = replace_once(new, b'json:"ipinfo_url,omitempty"', b'json:"ip_info_url,omitempty"')
needle = b"\tWorkers"
candidate = replace_once(candidate, needle, b'\tLegacyIpInfoURL string \x60json:"ipinfo_url,omitempty"\x60 // H: upstream spelling\n' + needle)
assert b"KeepTop *KeepTopOptions" in candidate
resolutions[path] = subprocess.check_output(["gofmt"], input=candidate)
path = "common/monitoring/outbound_monitoring.go"
candidate = replace_once(resolutions[path], b"\tonDemand := onDemandTimeouts(options.OnDemandTimeouts)", b"\tif options.IpInfoURL == \"\" {\n\t\toptions.IpInfoURL = options.LegacyIpInfoURL\n\t}\n\tonDemand := onDemandTimeouts(options.OnDemandTimeouts)")
resolutions[path] = subprocess.check_output(["gofmt"], input=candidate)
# Adapt upstream monitoring regressions to the retained fork test fixtures.
import re
for path, hashes in (
    ("common/monitoring/h_cycle_test.go", (None, "0e802f6177aa208433069f8b7a27a889eb47a224", "862ee9106ae2e130e1b3f7d8375180e028f5a88b")),
    ("common/monitoring/h_registry_test.go", (None, "9baf4b8ac59ecddf39b75161a87d508527201493", "5da0e5a17e5bdbbe4daf5e64dbde24e8b8351234")),
    ("common/monitoring/h_ipv6_test.go", ("b2ab2d58a5c8b6fcbc01453f49b29d3918780ceb", "bf793619443349c0c1fa8602192175c3c3f858be", "d37b1c66348c476b3b90dd2b7867af2cada70239")),
):
    if hashes[0] is None:
        assert subprocess.run(["git", "-C", str(repo), "cat-file", "-e", data["base"] + ":" + path], capture_output=True).returncode != 0
    else:
        checked_blob(data["base"], path, hashes[0])
    own = checked_blob(data["patched_tree"], path, hashes[1])
    new = checked_blob(data["upstream"], path, hashes[2])
    candidate = re.sub(rb'&fakeGroup\{fakeOutbound: fakeOutbound\{tag: ("[^"]+")\},', rb'&fakeGroup{tag: \1,', new)
    candidate = candidate.replace(b"m.started = true", b"m.started.Store(true)")
    candidate = candidate.replace(b"destination.IsFqdn()", b"destination.IsDomain()")
    if path.endswith("h_registry_test.go"):
        marker = b"// A group subscribes when it starts."
        assert candidate.count(marker) == 1
        assert candidate[:candidate.index(marker)].replace(b"\n\t\"github.com/stretchr/testify/require\"", b"\n\n\t\"github.com/stretchr/testify/require\"").rstrip() == own.rstrip()
        assert b"func TestH_SubscribeBeforeTheGroupIsRegistered(" in candidate
    else:
        assert candidate == own
    resolutions[path] = subprocess.check_output(["gofmt"], input=candidate)
# DNS pool files were independently added to both source branches.
def added_pair(path, fork_sha, upstream_sha):
    assert subprocess.run(["git", "-C", str(repo), "cat-file", "-e",
                           data["base"] + ":" + path], capture_output=True).returncode != 0
    return (checked_blob(data["patched_tree"], path, fork_sha),
            checked_blob(data["upstream"], path, upstream_sha))

def normalize_imports(blob):
    start = blob.index(b"import (\n") + len(b"import (\n")
    end = blob.index(b"\n)", start)
    imports = sorted(line.strip() for line in blob[start:end].splitlines() if line.strip())
    return blob[:start] + b"\n".join(imports) + blob[end:]

path = "common/dnspool/resolvers.go"
own, new = added_pair(path, "03b5c3dfbee1044f24fabd644762c43935a4e907",
                      "6ef2e415230cba7214f48db4a10eba2aad913984")
assert normalize_imports(own) == normalize_imports(new)
resolutions[path] = new

# Upstream consolidated the exact same bounded score arithmetic into rating.
path = "common/dnspool/history.go"
own, new = added_pair(path, "52880d6d98b4cb51276c0b3c9a591c5d4b231d94",
                      "1dfe3ddfd44cd8f392ca3de7d2d3a583d025d30e")
rating = checked_blob(data["upstream"], "hiddify/rating/rating.go",
                      "b9d78e0211a69baf8165db6933da2f926bb8016c")
assert b"Min Score = -10" in rating and b"Max Score = 10" in rating
assert b"if delta > 0 && s < 0 {" in rating
assert b"return Clamp(int(s) + delta)" in rating
assert b"return Score(min(max(value, int(Min)), int(Max)))" in rating
assert b"score := int(rating.Score(h.ResolverRate[address]).Add(delta))" in new
# The cache format, locking, sorting and all other functions stay the same.
for signature in (b"func NewHistory(", b"func LoadHistory(", b"func (h *History) Save(",
                  b"func (h *History) Score(", b"func (h *History) Sort("):
    assert function_blob(own, signature) == function_blob(new, signature)
resolutions[path] = new

# Keep the fork's bounded retries for UDP/TCP port collisions on Windows and
# other platforms. Verify the remainder is exactly the upstream implementation.
path = "common/dnspool/pool.go"
own, new = added_pair(path, "dab47b6d587a355580f54f5523509e4ee35e4ac0",
                      "b4954717fb5365b5c41b7e827855352ad54a9b2d")
start = own.index(b"// The resolver library reserves a UDP port")
end = own.index(b"// Addr is the local", start)
plain = own[:start] + own[end:]
plain = replace_once(plain,
    b"startLocalWithRetry(managerOptions(logger, tag, options), multidns.StartLocal)",
    b"multidns.StartLocal(managerOptions(logger, tag, options))")
for imp in (b'\t"errors"\n', b'\t"runtime"\n', b'\t"syscall"\n'):
    plain = replace_once(plain, imp, b"")
assert normalize_imports(plain) == normalize_imports(new)
resolutions[path] = own

# Retain the removed legacy service options because existing fork profiles
# still use them. The service and its registration will be reconciled separately.
path = "option/smart_dns_pool.go"
old = checked_blob(data["base"], path, "d8de3e1d9b22a904d499a090c6858bb5623ed1de")
own = checked_blob(data["patched_tree"], path, "400805d62bd313388a4861a5f19b271955e5535f")
assert subprocess.run(["git", "-C", str(repo), "cat-file", "-e",
                       data["upstream"] + ":" + path], capture_output=True).returncode != 0
assert b"type SmartDNSPoolServiceOptions struct" in own
assert b"type SmartDNSPoolUpstreamOptions struct" in own
resolutions[path] = own
# Preserve regression tests for local UDP/TCP port collisions and all common
# upstream tests. The fork fixtures bind TCP first and retry shared ports.
path = "common/dnspool/h_dnspool_fill_test.go"
own, new = added_pair(path, "fa2ae6642b3c8a8dc8c33ce738c7dfbee482bcd9",
                      "a254bd89dfd3fc9ffbe66f504b96d17095b685c1")
assert normalize_imports(own) == normalize_imports(new)
resolutions[path] = new

path = "common/dnspool/h_dnspool_test.go"
own, new = added_pair(path, "719f51cc0bb92e1f274d9f85faaf1c0197b36276",
                      "14cebed96ba60fcce52e60e4663dc13bcdeb186e")
start = own.index(b"\t// Bind TCP first:")
end = own.index(b"\tudp := &dns.Server", start)
new_start = new.index(b'\tpc, err := net.ListenPacket("udp", "127.0.0.1:0")')
new_end = new.index(b"\tudp := &dns.Server", new_start)
plain = own[:start] + new[new_start:new_end] + own[end:]
plain = plain[:plain.index(b"func TestH_DnspoolLocalBindRetry(")].rstrip() + b"\n"
for imp in (b'\t"errors"\n', b'\t"fmt"\n', b'\t"runtime"\n', b'\t"syscall"\n'):
    plain = replace_once(plain, imp, b"")
assert normalize_imports(plain) == normalize_imports(new)
resolutions[path] = own

path = "common/monitoring/h_settle_test.go"
old, own, new = (checked_blob(data[rev], path, sha) for rev, sha in zip(
    ("base", "patched_tree", "upstream"),
    ("ac509b513c7d58ab68ae0bfe640934cad4bd528a",
     "b61df673bea0f13739b37fa35517ebc1a1dfe046",
     "6c061c5d6f6057a02d84085c81bd08977b62eb0d")))
# Restore the upstream equivalent for verification only; retain the fork's
# synchronized checks and wait for completion in the actual resolution.
plain = own.replace(
    b'\trequire.Eventually(t, func() bool {\n\t\tstate := m.reg().outbounds["stuck"]\n\t\tstate.mu.Lock()\n\t\tdefer state.mu.Unlock()\n\t\treturn !state.testing\n\t}, time.Second, 10*time.Millisecond,',
    b'\trequire.Eventually(t, func() bool { return !m.reg().outbounds["stuck"].testing }, time.Second, 10*time.Millisecond,')
start = plain.index(b"\t// An answer is published before all bookkeeping")
end = plain.index(b"\twaitForProbeToSettle(1)\n", start) + len(b"\twaitForProbeToSettle(1)\n")
plain = plain[:start] + plain[end:]
plain = replace_once(plain, b"\twaitForProbeToSettle(2)\n", b"")
adapted = re.sub(rb'&fakeGroup\{fakeOutbound: fakeOutbound\{tag: ("[^"]+")\},', rb'&fakeGroup{tag: \1,', new)
assert normalize_imports(plain) == normalize_imports(adapted)
resolutions[path] = own

path = "dns/transport/multiplexer_test.go"
old, own, new = (checked_blob(data[rev], path, sha) for rev, sha in zip(
    ("base", "patched_tree", "upstream"),
    ("9ab3ab8b5f88a7100553ccb3345d9baccebcd0f2",
     "de752071e258a5b3671f4149f6ea8d2ee68af23d",
     "a65119144b509fdfef8b9b946568078341b29030")))
candidate = replace_once(new,
    b"\tfor i := 0; i < queries; i++ {\n\t\twg.Add(1)\n\t\tgo func() {\n\t\t\tdefer wg.Done()\n",
    b"\tfor range queries {\n\t\twg.Go(func() {\n")
candidate = replace_once(candidate, b"\t\t\terrs <- exchangeErr\n\t\t}()\n",
                         b"\t\t\terrs <- exchangeErr\n\t\t})\n")
assert normalize_imports(candidate) == normalize_imports(own)
resolutions[path] = own
# Keep the existing Smart DNS service and its tests as a coordinated unit.
# Upstream deleted the service; VetrOFF retains existing profile compatibility.
for path, hashes in (
    ("service/smart_dns_pool/service.go", ("c87ece2aeec1355c6b4cb56462bfe0c7a419dd2c", "762781565cb6284b3ec01d89239ac7c541780797")),
    ("service/smart_dns_pool/service_test.go", ("5357324d3eb0f83519d14db5b9dfc9962df00840", "61661738872f957dee893048b803eda6815521f5")),
    ("service/smart_dns_pool/h_service_test.go", ("9ca249babc4c550284072d3359a0ea8f4261d674", "d03bad6cfcb3cfb91b9586976328b7996235cb4a")),
):
    checked_blob(data["base"], path, hashes[0])
    own = checked_blob(data["patched_tree"], path, hashes[1])
    assert subprocess.run(["git", "-C", str(repo), "cat-file", "-e",
                           data["upstream"] + ":" + path], capture_output=True).returncode != 0
    assert b"package smart_dns_pool" in own
    resolutions[path] = own

# Adopt the upstream generic query API while preserving the fork's existing
# DNS option struct layout. Confirm that the complete original Probe body
# is preserved aside from the new query parameter.
path = "common/dnspool/probe.go"
own, new = added_pair(path, "6f33954062ab25aa90571194fb9e07047572dfbf",
                      "eaeb5d156f98084ff11ef6c6612794995ad94713")
start = new.index(b"\t\t\tRemoteTLSDNSServerOptions: option.RemoteTLSDNSServerOptions{")
end = new.index(b"\t\t\tPath: u.Path,", start)
candidate = new[:start] + b"\t\t\tRawLocalDNSServerOptions: local,\n\t\t\tServer:                   u.Hostname(), ServerPort: port,\n" + new[end:]
candidate = replace_once(candidate,
    b"DNSServerAddressOptions:  option.DNSServerAddressOptions{Server: host, ServerPort: uint16(n)},",
    b"Server:                   host, ServerPort: uint16(n),")
signature = b"func Probe("
body_start = candidate.index(b"\n\treturn Exchange(", candidate.index(signature))
body_end = candidate.index(b"\n\tconfig, err := multidns.ParseResolverURL", body_start)
plain = candidate[:body_start] + candidate[body_end:]
plain = replace_once(plain, b"return trans.Exchange(queryCtx, query)",
                     b"return trans.Exchange(queryCtx, BuildQuery(name, qtype))")
assert normalize_imports(plain) == normalize_imports(own)
resolutions[path] = subprocess.check_output(["gofmt"], input=candidate)

# IPv6 fallback source and tests: same behavior, existing metadata API.
path = "route/h_ipv6_fallback.go"
own, new = added_pair(path, "83c5121f666c1784dba2703b1659ac470a69f34c",
                      "ad31936802565a4c5ddfc417234208bee9ec007d")
assert replace_once(new, b"var source net.Conn = remote", b"source := remote") == own
resolutions[path] = own

path = "route/h_ipv6_fallback_test.go"
own, new = added_pair(path, "11dc081522f4ad2413ce2b5b95b896a0e7b91603",
                      "5d18596c64d16950f6607029da3e76a481cffd42")
candidate = new.replace(b"destination.IsFqdn()", b"destination.IsDomain()")
candidate = candidate.replace(b"(string, error, []bool, <-chan error)", b"(string, []bool, <-chan error, error)")
candidate = candidate.replace(b"return string(answer[:n]), err, observations.all(), closed",
                              b"return string(answer[:n]), observations.all(), closed, err")
candidate = re.sub(rb"answer, err, observed, (closed|_) (:=|=)", rb"answer, observed, \1, err \2", candidate)
assert candidate == own
resolutions[path] = own

# ECH test fixtures use the fork's miekg/dns struct definitions.
path = "common/tls/h_ech_resolver_test.go"
own, new = added_pair(path, "81233c91d3ca976db5a24779a60da2e7db9d2105",
                      "70e96c34b84ced841d9bc27690b7966108a7aea5")
candidate = replace_once(new, b"record := &mDNS.HTTPS{SVCB: mDNS.SVCB{", b"record := &mDNS.HTTPS{")
candidate = replace_once(candidate, b"\t}}\n\treturn &mDNS.Msg{MsgHdr: mDNS.MsgHdr{Response: true, Rcode: mDNS.RcodeSuccess},",
                         b"\t}\n\treturn &mDNS.Msg{Response: true, Rcode: mDNS.RcodeSuccess,")
assert candidate == own
resolutions[path] = own
# The DNS router retains direct Close support and the fork's DNS message
# layout. Incorporate upstream timeout propagation and cache invalidation.
path = "dns/router.go"
old, own, new = (checked_blob(data[rev], path, sha) for rev, sha in zip(
    ("base", "patched_tree", "upstream"),
    ("11d7655a358f499cf19b5701969a2a788ba4a2ca",
     "456cdd63dcc601186354d9cdb71c178ae67b5826",
     "97e777025d636bbbd2011b02d49ef070dbd78e0a")))
candidate = own
needle = b"\t\tDisableCache:      options.DNSClientOptions.DisableCache,"
addition = b"\t\tTimeout:           time.Duration(options.DNSClientOptions.Timeout),\n"
assert addition + needle in new
candidate = replace_once(candidate, needle, addition + needle)
needle = b"\t\t\t\tif action.ClientSubnet.IsValid() {"
addition = b"\t\t\t\tif action.Timeout > 0 {\n\t\t\t\t\toptions.Timeout = action.Timeout\n\t\t\t\t}\n"
assert candidate.count(needle) == 2 and new.count(addition + needle) == 2
candidate = candidate.replace(needle, addition + needle)
needle = b"\tif routeOptions.ClientSubnet.IsValid() {"
addition = b"\tif routeOptions.Timeout > 0 {\n\t\toptions.Timeout = routeOptions.Timeout\n\t}\n"
assert addition + needle in new
candidate = replace_once(candidate, needle, addition + needle)
needle = b'\t\t\t} else if R.IsRejected(err) {\n\t\t\t\tr.logger.DebugContext(ctx, "lookup rejected for ", domain)\n'
addition = b'\t\t\t} else if errors.Is(err, ErrNotCached) {\n\t\t\t\tr.logger.DebugContext(ctx, "cache-only lookup missed for ", domain)\n'
assert needle + addition in new
candidate = replace_once(candidate, needle, needle + addition)
signature = b"func (r *Router) ClearCache()"
cache = function_blob(new, signature)
assert b"r.dnsReverseMapping.Purge()" in cache
cache = replace_once(cache,
    b"\tservice.FromContext[adapter.NetworkManager](r.ctx).FlushSystemDNSCache()",
    b"\tif networkManager := service.FromContext[adapter.NetworkManager](r.ctx); networkManager != nil {\n\t\tnetworkManager.FlushSystemDNSCache()\n\t}")
candidate = replace_once(candidate, function_blob(own, signature), cache)
assert b"scope.Add(r.Close)" in candidate
assert b"func (r *Router) Close() error" in candidate
resolutions[path] = subprocess.check_output(["gofmt"], input=candidate)

# Keep every fork protocol and the legacy smart DNS service registration.
# Register upstream lazy placeholders and fallback groups additively, with
# the existing Sudoku registration unchanged to avoid duplicate registration.
path = "include/registry.go"
old, own, new = (checked_blob(data[rev], path, sha) for rev, sha in zip(
    ("base", "patched_tree", "upstream"),
    ("47686ce6075f5139b1c6af02938ed2bb4e03065b",
     "f66c35b8cef673df1ee898e7baf2d7d9eee0d8f6",
     "c2158fecd9de07edaa4e94c0dc1a16cdf360c985")))
candidate = own
for anchor, addition in (
    (b"\thinvalid.RegisterOutbound(registry)\n", b"\toutbound.RegisterLazy(registry)"),
    (b"\tbalancer.RegisterLoadBalance(registry)\n", b"\tgroup.RegisterFallback(registry)"),
    (b"\tregisterBondOutbound(registry) // H\n", b"\tregisterCallOutbound(registry)"),
    (b"\thinvalid.RegisterEndpoint(registry)\n", b"\tendpoint.Register[option.HLazyOptions](registry, C.TypeHLazy, outbound.NewLazyEndpoint)"),
):
    assert addition in new and addition not in candidate
    candidate = replace_once(candidate, anchor, anchor + addition + b"\n")
for name in (b"sudoku", b"openflux", b"olcrtc", b"vetroff", b"fptn", b"pingtunnel"):
    assert candidate.count(name + b".RegisterOutbound(registry)") == 1
assert candidate.count(b"smartdnspool.RegisterService(registry)") == 1
resolutions[path] = subprocess.check_output(["gofmt"], input=candidate)

# Identical logic on both sides: only the spacing of H marker comments differs.
for path, hashes in (
    ("option/route.go", ("2d7c732e6910ae114a7c288a378e83d6255158c6", "7c8e53e7727ec9ec94811e6b5209fcf0eda2d37f", "3b0fbbdf54232dfb1e99985dc5e8e5984cd71f2d")),
    ("common/tls/ech.go", ("5c7109b89e5145fcd3835f9a57026ace1b1a7699", "fb8dfa19e6dc657fcf897268f114bcb593442227", "4c2890c6a9b8bf5fe3254fa6d5ec9331138508c3")),
):
    old, own, new = (checked_blob(data[rev], path, sha) for rev, sha in zip(
        ("base", "patched_tree", "upstream"), hashes))
    assert new.replace(b"//H", b"// H") == own
    resolutions[path] = own
# Review the complete balancer strategy family together. The official
# version has the same selection semantics; retain the fork's consistent
# receivers, legacy alias, and removal of unused state.
def balancer_code(blob):
    # These hash-guarded files have been reviewed for comment-only differences.
    # Normalize line comments and spacing, not code identifiers or literals.
    return b"\n".join(line for raw in blob.splitlines()
                      if (line := raw.split(b"//", 1)[0].strip()))

for file, hashes in (
    ("consistent_hashing.go", ("6659e154be26daac2f8add98895e0277d0bf3e87", "7998e7c30edc57da784f91283b609be5741b3e7e", "1693296906c6f8047bde00f9474863b30f08be07")),
    ("lowest_delay.go", ("bc586d1880b0f037287c557a4444e19d8726c718", "2cfc1643bda06593bf567dd34564da82b92bba65", "e4f4b8c4158c69c9597ecf9ff54703091277fb4b")),
    ("roundrobin.go", ("bd03fe069a9dfbe39ad60173005b671acb5346ee", "99cfd50a8a76af60867349c18d9d87873b0bf3f2", "95cc3e50ba4c91a850211cfd007df4be35b12916")),
    ("sticky_session.go", ("3c4af4f453a0c48a6996a0dfc951ae19fb72231d", "082540552670edeac0cd9d9cd04912db8698eefb", "1ec9bdc247cfd858131f9b8f317f35c82fe04228")),
    ("h_pool.go", ("cede9f6e859af1e3c450c6f6034d2dd363522acb", "96674a3da9be3ee66f460226e3acdc7cba83fe68", "db340e545eb3b7b03760cc7995e11798306db7f6")),
    ("h_stable.go", ("c57afd07e4110d46992da384548fb8d1f5fc4446", "71695b1de20068533f4609a6babe38b4a0726f5e", "b6fbba2a553d92dbfa7f0866571c005713ee9a64")),
    ("balancer.go", ("d69741578c6d84def1536448aba1a3d5bcd23d4b", "a76f2a4909403c720fadf3a405cb78c0670f2735", "635a6d40faf549e6c0d7d74399bb0fe2808a7592")),
):
    path = "protocol/group/balancer/" + file
    old, own, new = (checked_blob(data[rev], path, sha) for rev, sha in zip(
        ("base", "patched_tree", "upstream"), hashes))
    candidate = new
    if file == "consistent_hashing.go":
        signature = b"func (g *ConsistentHashing) Select("
        body = function_blob(new, signature)
        renamed = body.replace(b"(g *ConsistentHashing)", b"(s *ConsistentHashing)").replace(b"g.", b"s.")
        candidate = replace_once(candidate, body, renamed)
    elif file == "lowest_delay.go":
        candidate = replace_once(candidate, b"\tudpOutbounds     []adapter.Outbound\n", b"")
        assert b"udpOutbounds" not in candidate
    elif file == "h_pool.go":
        alias = b"\tif options.Tolerance == 0 {\n\t\toptions.Tolerance = options.PoolTolerance\n\t} // H: legacy alias\n"
        assert alias in own and b"PoolTolerance" not in new
        candidate = replace_once(candidate, b"\tsize := options.Pool\n", alias + b"\tsize := options.Pool\n")
    elif file == "balancer.go":
        for field in (b"\tlink                         string\n",
                      b"\tinterval                     time.Duration\n",
                      b"\tidleTimeout                  time.Duration\n"):
            candidate = replace_once(candidate, field, b"")
    assert balancer_code(candidate) == balancer_code(own), path
    resolutions[path] = own

# Keep the native uTLS snapshots and the shared name used by upstream helpers.
path = "common/tls/h_utls_xray_names.go"
own, new = added_pair(path, "0755cefbb731e38299cf48b86b0489597c7b2823",
                      "2ca6a71f642d671bb9566e49f7fd5e15ef026bc4")
start = new.index(b"// hChromeFingerprint is")
end = new.index(b"// xrayFingerprints", start)
candidate = new[:start] + b"// hChromeFingerprint is the canonical Chrome snapshot of native uTLS.\nvar hChromeFingerprint = utls.HelloChrome_155\n\n" + new[end:]
plain = new[:start] + new[end:]
plain = replace_once(plain, b'\t"hellochrome_155":                  utls.HelloChrome_155,\n', b"")
plain = replace_once(plain, b'\t"hellochrome_auto":                 hChromeFingerprint,', b'\t"hellochrome_auto":                 utls.HelloChrome_Auto,')
assert plain == own
resolutions[path] = subprocess.check_output(["gofmt"], input=candidate)

path = "common/tls/h_utls_xray_names_test.go"
own, new = added_pair(path, "b0f802247b98643ec025213e864d08e69c07aaf9",
                      "b00e57187932358a711487c30277b957be1f697e")
start = new.index(b'\tid, err = uTLSClientHelloID("hellochrome_auto")')
end = new.index(b'\t_, err = uTLSClientHelloID("hellochrome_999")', start)
plain = new[:start] + new[end:]
assert plain.replace(b'utls.HelloChrome_155, id, "sing-box', b'utls.HelloChrome_Auto, id, "sing-box') == own
resolutions[path] = new

path = "common/tls/h_utls_fingerprint_test.go"
old, own, new = (checked_blob(data[rev], path, sha) for rev, sha in zip(
    ("base", "patched_tree", "upstream"),
    ("dc2a93a1b40be1de2fe9128c93cd1d1abea34eca",
     "6e3bc6f61896307f35fa78aa7886627e22ed782b",
     "106053cb7cca34ca0ecf6be6bd101d298c0441e5")))
assert balancer_code(own) == balancer_code(new)
resolutions[path] = own

# REALITY: retain the fork's ML-DSA-65 verification, clock source and version.
# Integrate the official configuration lifecycle without removing verification.
path = "common/tls/reality_client.go"
old, own, new = (checked_blob(data[rev], path, sha) for rev, sha in zip(
    ("base", "patched_tree", "upstream"),
    ("5fe0949be2cccfed45d7a65cf0f1f004afc821ca",
     "be24834b13cbc6d5223972c99882e72f29db34cd",
     "195cf59d3779d9e5d53433ee4a2f077c65fec004")))
candidate = own
candidate = replace_once(candidate, b"\tmldsa65Verify *mldsa65.PublicKey\n}\n\nfunc NewRealityClient",
    b"\tmldsa65Verify *mldsa65.PublicKey\n\tauto *realityKeyShareAuto\n\tlogger logger.ContextLogger\n}\n\nfunc NewRealityClient")
start = new.index(b"\tvar classical bool")
end = new.index(b"\tvar config Config", start)
own_start = candidate.index(b"\tvar classical bool")
own_end = candidate.index(b"\tverify, err := parseRealityMLDSAPublicKey", own_start)
candidate = candidate[:own_start] + new[start:end] + candidate[own_end:]
candidate = replace_once(candidate,
    b"&RealityClientConfig{ctx, uClient.(*UTLSClientConfig), publicKey, shortID, classical, verify}",
    b"&RealityClientConfig{ctx: ctx, uClient: uClient.(*UTLSClientConfig), publicKey: publicKey, shortID: shortID, classical: classical, mldsa65Verify: verify, auto: auto, logger: logger}")
signature = b"func (e *RealityClientConfig) ClientHandshake(ctx context.Context, conn net.Conn) (aTLS.Conn, error) {"
start = new.index(signature)
end = new.index(b"\n\tverifier :=", start)
candidate = replace_once(candidate, signature, new[start:end])
candidate = replace_once(candidate,
    b"realityClientHelloSpec(e.uClient.id, uConfig, e.classical)",
    b"realityClientHelloSpec(e.uClient.id, uConfig, classical)")
assert b"return nil, errRealityVerificationFailed" in new
candidate = replace_once(candidate, b'return nil, E.New("reality verification failed")',
                         b"return nil, errRealityVerificationFailed")
candidate = replace_once(candidate, b"\t\te.mldsa65Verify,\n\t}\n}",
    b"\t\te.mldsa65Verify,\n\t\te.auto,\n\t\te.logger,\n\t}\n}")
assert b"mldsa65.Verify(" in candidate and b"len(certs) == 0" in candidate
assert b"[3]byte{26, 11, 9}" in candidate
assert b"uint32(nowTime.Unix())" in candidate
resolutions[path] = subprocess.check_output(["gofmt"], input=candidate)

# Retain all of the fork's TLS fields and legacy JSON compatibility.
# The newly supported key_share value is reflected in the option documentation.
path = "option/tls.go"
old, own, new = (checked_blob(data[rev], path, sha) for rev, sha in zip(
    ("base", "patched_tree", "upstream"),
    ("ee1e4ef7014960c940a3f6698dceb5fb3318dfaa",
     "55a7ec3977b563319dc54367f5203e33cfe62318",
     "96a49a4dbbe4d03d2e22c4921f5284995260447c")))
assert b'case "auto":' in resolutions["common/tls/reality_client.go"]
candidate = replace_once(own, b'// H: "", "hybrid" or "classical"',
                         b'// H: "", "hybrid", "classical" or "auto"')
for field in (b"Mldsa65Seed", b"Mldsa65Verify", b"LimitFallbackUpload", b"LimitFallbackDownload", b"TLSFragment"):
    assert field in candidate
resolutions[path] = subprocess.check_output(["gofmt"], input=candidate)
# Reconcile the uTLS caller with the new ECH helper and padding API.
# Preserve the fork's cipher-suite validation and native fingerprint aliases.
path = "common/tls/utls_client.go"
old, own, new = (checked_blob(data[rev], path, sha) for rev, sha in zip(
    ("base", "patched_tree", "upstream"),
    ("94f31726853dcafdb4e4af9d0165865920b9a730",
     "b71ae6c8ccd658dabbae5a418fdc5a09e919f039",
     "b5a51672ada4128acb7d9ffe2addb8295372d2ab")))
helper = checked_blob(data["upstream"], "common/tls/h_utls_ech.go",
                      "17b05e748b82335dcd814dcbfd97748f0331be04")
padding = checked_blob(data["upstream"], "common/tls/h_tls_tricks_utls.go",
                       "34452543bba1f683d954f6332bf9bbfb1c62597b")
for name, sha in (
    ("common/tls/h_utls_ech.go", "17b05e748b82335dcd814dcbfd97748f0331be04"),
    ("common/tls/h_tls_tricks_utls.go", "34452543bba1f683d954f6332bf9bbfb1c62597b"),
):
    checked_blob(data["tentative_tree"], name, sha)
assert b"func (c *UTLSClientConfig) hUClient(" in helper
assert b"applyTLSTricksPadding(&spec, c.tricks)" in helper
assert b"func applyTLSTricksPadding(spec *utls.ClientHelloSpec, tricks *tlsTricks)" in padding
native_common = checked_blob(data["patched_tree"], "replace/utls-native/common.go",
                             "68974e2f863f50518456c30241beaf2f88b53d70")
native_names = checked_blob(data["patched_tree"], "replace/utls-native/u_common.go",
                            "a3254582db15c31b4a10f7d78a1db649a102a40c")
native_ech = checked_blob(data["patched_tree"], "replace/utls-native/u_ech.go",
                          "f2576e4293fd559b803b12ec6dba8549ffd0bd46")
assert b"ECHAccepted bool" in native_common
assert b"HelloChrome_Auto        = HelloChrome_155" in native_names
assert b"type EncryptedClientHelloExtension interface" in native_ech
assert b"func BoringGREASEECH()" in native_ech

candidate = own
signature = b"func (c *UTLSClientConfig) Client(conn net.Conn)"
candidate = replace_once(candidate, function_blob(own, signature), function_blob(new, signature))
needle = b"\t\tTLSUnique:                   state.TLSUnique,\n"
addition = b"\t\tECHAccepted:                 state.ECHAccepted,\n"
candidate = replace_once(candidate, needle, needle + addition)
needle = b"\t\tconfig, err = parseECHClientConfig(ctx, config.(ECHCapableConfig), options)"
addition = b"\t\tconfig.(*UTLSClientConfig).id = hECHClientHelloID(logger, options.UTLS.Fingerprint, id)\n"
candidate = replace_once(candidate, needle, addition + needle)
assert b"resolveCipherSuites(options.CipherSuites)" in candidate
assert b"Explicitly enabled legacy TLS cipher suites (weaker security)" in candidate
assert b'"chrome_133", "hellochrome_133"' in candidate
assert b'case "randomizednoalpn":' in candidate
assert b"tf.NewConn(" in candidate
resolutions[path] = subprocess.check_output(["gofmt"], input=candidate)
# XDrive is already enabled in the fork's standard build. Preserve its
# storage routing, template compatibility and explicit connection lifecycle.
path = "include/h_xdrive.go"
own, new = added_pair(path, "8525e397c862488fe7a19fde0d7a6805825515bc",
                      "c9e5da7e866a69ef09542ba01dfd79f41b80399d")
assert replace_once(new, b"//go:build with_xdrive\n\n", b"") == own
resolutions[path] = own

path = "option/h_v2ray_xdrive.go"
own, new = added_pair(path, "a26762ce86c7e33c2140d2d18913856e27951f97",
                      "c028ed574d32db49d696e434443feafb694cd4c5")
plain = own[:own.index(b"// UnmarshalJSON also accepts")].rstrip() + b"\n"
plain = replace_once(plain, b'\t"bytes"\n\n', b"")
plain = re.sub(rb"(?m)^\t(?:FrontAddress|FrontPort|StorageDialer)\s+.*\n", b"", plain)
plain = replace_once(plain, b"SessionTtlSeconds", b"SessionTTLSeconds")
assert subprocess.check_output(["gofmt"], input=plain) == subprocess.check_output(["gofmt"], input=new)
resolutions[path] = own

path = "transport/v2rayxdrive/config.go"
own, new = added_pair(path, "619cd9d142a5b9bb2cfafb4ea1f9923e2b8c2554",
                      "163da14b75ad01153d44edc8af0ffb50c6e35aa1")
plain = replace_once(own, b"\tif options.FrontingAddress == \"\" {\n\t\toptions.FrontingAddress, options.FrontingPort = options.FrontAddress, options.FrontPort // H: legacy fronting names\n\t}\n", b"")
plain = replace_once(plain, b"options.SessionTtlSeconds", b"options.SessionTTLSeconds")
assert plain == new
resolutions[path] = own

path = "transport/v2rayxdrive/transport.go"
own, new = added_pair(path, "0aab39bf6635461c481e6c7403e0f62c4c556780",
                      "47a76fd150558f3d9c75e248f1ee3d6a95dfcbd2")
for signature in (b"func init()", b"func streamSettings(", b"func (s *Server) Network(", b"func (s *Server) Serve(", b"func (s *Server) newConnection(", b"func (s *Server) ServePacket("):
    assert function_blob(own, signature) == function_blob(new, signature)
assert b"context.AfterFunc(c.ctx, cancel)" in own
assert b"return c.closeConnections()" in own
assert b"dialer.New(ctx, options.StorageDialer, true)" in own
assert b"s.cancel()" in own
resolutions[path] = own

# XHTTP: preserve bounded allocation for uploads, including small post sizes,
# and the fork's explicit handling of clearing a deadline.
path = "transport/v2rayxhttp/writer.go"
old, own, new = (checked_blob(data[rev], path, sha) for rev, sha in zip(
    ("base", "patched_tree", "upstream"),
    ("785f8e548ceee6ea962c3e6bdf65a32457f3d763",
     "923a396333bf564ade2f97964a75a37dade2116a",
     "a803be53f952d2f6024c0245129f5998f1359fa2")))
assert own.split(b"func (w uploadWriter) Write(")[0] == new.split(b"func (w uploadWriter) Write(")[0]
assert b"end := written + min(piece, len(data)-written)" in own
assert b"return written, err" in own
assert b"w.WriteMultiBuffer(buf.MultiBuffer{buffer})" in own
resolutions[path] = own

path = "transport/v2rayxhttp/conn.go"
old, own, new = (checked_blob(data[rev], path, sha) for rev, sha in zip(
    ("base", "patched_tree", "upstream"),
    ("1d28adcac43a717424b3b26af7a45e7721ae6007",
     "717161bbeda32caed485364dd1ab33284930b328",
     "6000a435df09749e5144ada7fd1c5c09b3ab8513")))
candidate = own
for signature in (b"func (c *splitConn) Write(", b"func (c *splitConn) Read(", b"func (c *splitConn) Close("):
    candidate = replace_once(candidate, function_blob(own, signature), function_blob(new, signature))
# Close also tolerates a missing writer on a partially constructed stream.
candidate = replace_once(candidate, b"\terr := c.writer.Close()\n", b"\tvar err error\n\tif c.writer != nil {\n\t\terr = c.writer.Close()\n\t}\n")
assert b"hWrapH3(baderror.WrapH2(err))" in candidate
assert b"if t.IsZero()" in candidate
resolutions[path] = subprocess.check_output(["gofmt"], input=candidate)

path = "transport/v2rayxhttp/http.go"
old, own, new = (checked_blob(data[rev], path, sha) for rev, sha in zip(
    ("base", "patched_tree", "upstream"),
    ("03a47da71c1abf8fee76ea1d4b398ef3f33041a5",
     "26054f8a7fcca714c0dc02743b566d486d0b3e81",
     "e398209aa08a5f57a1d1f7a28e3c68cd69437c96")))
assert balancer_code(new) == balancer_code(own)
resolutions[path] = own

path = "transport/v2rayxhttp/h_post_bytes_test.go"
own, new = added_pair(path, "fdcd705074bd87c1b839ad3e83beb20ef6ce4331",
                      "60629b661d0f4b836e9e304c7b40be99764e9285")
assert replace_once(new, b"the range is raised and the server works.", b"the limit is kept and the server works.") == own
resolutions[path] = own
# Coordinate XHTTP stream lifetime across the dialer, reader and tests.
# Retain the fork's direct status validation for download-only streams, but
# detach the established stream from the dial context and synchronize addresses.
path = "transport/v2rayxhttp/dialer.go"
old, own, new = (checked_blob(data[rev], path, sha) for rev, sha in zip(
    ("base", "patched_tree", "upstream"),
    ("0dba3b4518b6d27447baaa757930b8c9a21ab454",
     "c6b29ae3a8a2d084cd540cf2495c8db650c30275",
     "ea9a32d036dcce11e24636e207dc6f3d17beb327")))
candidate = new
# Keep WaitReadCloser in its dedicated source file.
start = candidate.index(b"// WaitReadCloser is the download stream")
end = candidate.index(b"func ApplyMetaToRequest", start)
candidate = candidate[:start] + candidate[end:]
start = candidate.index(b"\treader := &WaitReadCloser{")
end = candidate.index(b"\n}\n", start)
candidate = candidate[:start] + b'''	reader := &WaitReadCloser{Wait: make(chan struct{}), cancel: cancelRequest}
	resultCh := make(chan error, 1)
	go func() {
		resp, requestErr := c.client.Do(req)
		if requestErr != nil {
			if !uploadOnly {
				c.closed.Store(true)
			}
			reader.closeWithError(requestErr)
			resultCh <- requestErr
			return
		}
		if resp.StatusCode != http.StatusOK {
			statusErr := E.New("bad status code: ", resp.Status)
			reader.closeWithError(statusErr)
			resp.Body.Close()
			resultCh <- statusErr
			return
		}
		if uploadOnly {
			io.Copy(io.Discard, resp.Body)
			resp.Body.Close()
			reader.Close()
		} else {
			reader.Set(resp.Body)
		}
		resultCh <- nil
	}()
	// A download-only stream must be accepted by the server before returning.
	// Uploads may need data from the caller before a response can arrive.
	if body == nil && !uploadOnly {
		select {
		case err = <-resultCh:
		case <-ctx.Done():
			err = ctx.Err()
		}
	} else {
		select {
		case err = <-resultCh:
		case <-gotConn.Wait():
		case <-ctx.Done():
			err = ctx.Err()
		}
	}
	if err == nil {
		err = ctx.Err()
	}
	if err != nil {
		reader.Close()
		return nil, nil, nil, err
	}
	addrs := connAddrs.Load()
	if addrs == nil {
		reader.Close()
		return nil, nil, nil, E.New("xhttp connection has no addresses")
	}
	return reader, addrs[0], addrs[1], nil''' + candidate[end:]
assert b"connAddrs.CompareAndSwap" in candidate
assert b"context.WithCancel(context.WithoutCancel(ctx))" in candidate
assert b"c.hTakeWarm(ctx)" in candidate
assert b"c.closed.Load() || c.hClosed.Load()" in candidate
resolutions[path] = subprocess.check_output(["gofmt"], input=candidate)

path = "transport/v2rayxhttp/h_wait_read_closer.go"
own, new = added_pair(path, "c0b1b71b9ece3e5189129ba45b90b25fcd8ce62c",
                      "753778e2bcacc6792b881761a65abce69ab53614")
candidate = replace_once(own, b"\tctx  context.Context\n",
                         b"\tctx context.Context\n\tcancel context.CancelFunc\n\terr error\n")
candidate = replace_once(candidate, b"\tselect {\n\tcase <-w.ctx.Done():\n\t\treturn 0, w.ctx.Err()\n\tcase <-w.Wait:\n\t}\n",
                         b"\tif w.ctx == nil {\n\t\t<-w.Wait\n\t} else {\n\t\tselect {\n\t\tcase <-w.ctx.Done():\n\t\t\treturn 0, w.ctx.Err()\n\t\tcase <-w.Wait:\n\t\t}\n\t}\n")
candidate = replace_once(candidate, b"\trc, closed := w.ReadCloser, w.closed\n",
                         b"\trc, closed, streamErr := w.ReadCloser, w.closed, w.err\n")
candidate = replace_once(candidate, b"\tif rc == nil || closed {\n\t\treturn 0, io.ErrClosedPipe\n",
                         b"\tif rc == nil || closed {\n\t\tif streamErr != nil {\n\t\t\treturn 0, streamErr\n\t\t}\n\t\treturn 0, io.ErrClosedPipe\n")
candidate = replace_once(candidate, b"\tw.access.Unlock()\n\tif rc != nil {\n\t\treturn rc.Close()\n",
                         b"\tw.access.Unlock()\n\tif w.cancel != nil {\n\t\tw.cancel()\n\t}\n\tif rc != nil {\n\t\treturn rc.Close()\n")
candidate += b"\n" + new.split(b"\n\n", 1)[1]
assert b"if rc == nil {" in candidate
assert b"if w.closed || w.ReadCloser != nil {" in candidate
resolutions[path] = subprocess.check_output(["gofmt"], input=candidate)

path = "transport/v2rayxhttp/h_dialer_error_test.go"
old, own, new = (checked_blob(data[rev], path, sha) for rev, sha in zip(
    ("base", "patched_tree", "upstream"),
    ("461785ae46e243d52165794c3d2c0c19d61f2bef",
     "830ffe0663d2183e9a5fa72023a4751f74eb9ce1",
     "77da82a46753054a685970aa121273cca82ed87e")))
marker = b"// A download stream the server rejects"
assert new[:new.index(marker)].rstrip() == own[:own.index(b"// A request may finish")].rstrip()
test = new[new.index(marker):]
test = replace_once(test,
    b"\trequire.NoError(t, err)\n\tdefer reader.Close()\n\t_, err = reader.Read(make([]byte, 16))\n\trequire.ErrorContains(t, err, \"400\")",
    b"\trequire.ErrorContains(t, err, \"400\")\n\trequire.Nil(t, reader)")
# The status is now surfaced directly by Dial, rather than by the first read.
resolutions[path] = subprocess.check_output(["gofmt"], input=own + b"\n" + test)
# Preserve recursive download validation and the XDrive schema entry.
path = "option/v2ray_transport.go"
old, own, new = (checked_blob(data[rev], path, sha) for rev, sha in zip(
    ("base", "patched_tree", "upstream"),
    ("6a4ea98c85a954c74bfbfb1995e614226fd1b55a",
     "3fd724c1d823f9a78d37bf0db272e590651121ce",
     "905a6ecd63d3862f8aa95026d04a9a7a3c4d77bb")))
assert b"func (c *V2RayXHTTPOptions) ResolveDownload()" in own
assert b"download nesting exceeds 8 or contains a cycle" in own
assert b"stream-one cannot use a separate download" in own
assert b"mkcp,kcp,xdrive" in own
resolutions[path] = own

# Use the upstream XMUX lifecycle and the fork's validated download endpoint.
path = "transport/v2rayxhttp/client.go"
old, own, new = (checked_blob(data[rev], path, sha) for rev, sha in zip(
    ("base", "patched_tree", "upstream"),
    ("8a74ad0e1047fd8c42f5b347d6447649eba02e03",
     "cc06e22b38a26900def736d57dd865c071e4a0dc",
     "76b1e6cc4b0b56d818e41870dfb99968fea540b0")))
checked_blob(data["tentative_tree"], "transport/v2rayxhttp/mux.go",
             "96bcf78e0e11a897e4d5c24ba60082df4666e988")
checked_blob(data["tentative_tree"], "transport/v2rayxhttp/h_alpn.go",
             "f9b1497790fca3a4d24f146124d71f9f29b9faa3")
candidate = new
start = candidate.index(b"\tif options.Mode ==")
end = candidate.index(b"\ttlsConfig = hDefaultALPN", start)
own_start = own.index(b"\tif options.Mode ==")
own_end = own.index(b"\tdest := serverAddr", own_start)
candidate = candidate[:start] + own[own_start:own_end] + candidate[end:]
candidate = replace_once(candidate, b"\tif options.Download != nil {\n\t\toptions2 := options.Download\n",
                         b"\tif download != nil {\n\t\toptions2 := download\n")
candidate = replace_once(candidate,
    b"\t\t\tdialer2, ok = service.FromContext[adapter.OutboundManager](ctx).Outbound(options2.Detour)",
    b"\t\t\tmanager := service.FromContext[adapter.OutboundManager](ctx)\n\t\t\tif manager == nil {\n\t\t\t\treturn nil, E.New(\"download detour requires outbound manager\")\n\t\t\t}\n\t\t\tdialer2, ok = manager.Outbound(options2.Detour)")
# An upload-only stream owns a request cancellation handle too.
candidate = replace_once(candidate,
    b"\t\t_, _, _, err = httpClient.OpenStream(ctx, requestURL.String(), sessionId, reader, true)",
    b"\t\tvar uploadReader io.ReadCloser\n\t\tuploadReader, _, _, err = httpClient.OpenStream(ctx, requestURL.String(), sessionId, reader, true)")
needle = b"\t\treturn &conn, nil\n\t}\n\tscMaxEachPostBytes"
candidate = replace_once(candidate, needle,
    b"\t\tpreviousClose := conn.onClose\n\t\tconn.onClose = func() {\n\t\t\tif uploadReader != nil {\n\t\t\t\tuploadReader.Close()\n\t\t\t}\n\t\t\tpreviousClose()\n\t\t}\n" + needle)
# Closing the connection must cancel its own upload loop, not the initial dial.
candidate = replace_once(candidate,
    b"\tctx = context.WithoutCancel(ctx) //H: the uploads belong to the conn, which outlives the dial context",
    b"\tctx, cancelUploads := context.WithCancel(context.WithoutCancel(ctx))\n\tpreviousClose := conn.onClose\n\tconn.onClose = func() {\n\t\tcancelUploads()\n\t\tpreviousClose()\n\t}")
candidate = replace_once(candidate, b"\tgo func() {\n\t\tvar seq int64",
                         b"\tgo func() {\n\t\tdefer cancelUploads()\n\t\tvar seq int64")
assert b"DoneRunning()" in candidate
assert b"c.getHTTPClient2 != nil" in candidate
assert b"xmuxManager.CloseIdleConnections()" in candidate
assert b"context.AfterFunc(quicConn.Context()" in candidate
assert b"options.ResolveDownload()" in candidate
resolutions[path] = subprocess.check_output(["gofmt"], input=candidate)
# MASQUE: merge timeout and idempotent shutdown with the fork's explicit
# UDP/ICMP forwarder lifecycle.
path = "transport/masque/device_stack.go"
old, own, new = (checked_blob(data[rev], path, sha) for rev, sha in zip(
    ("base", "patched_tree", "upstream"),
    ("58e110383673dccc37327eb72b217e5ebe694c01",
     "c7ebfe187c49ff239a4c77602214cf27eb9ed07e",
     "7aba16841a495b8ebd53df2d39e015f09a890825")))
candidate = own
candidate = replace_once(candidate, b'\t"os"\n', b'\t"os"\n\t"sync"\n')
candidate = replace_once(candidate, b'\t"github.com/sagernet/sing-box/log"\n',
                         b'\tC "github.com/sagernet/sing-box/constant"\n\t"github.com/sagernet/sing-box/log"\n')
candidate = replace_once(candidate, b"\tudpForwarder   *tun.UDPForwarder", b"\tcloseOnce sync.Once\n\tudpForwarder   *tun.UDPForwarder")
for signature in (b"func (w *stackDevice) DialContext(", b"func (w *stackDevice) ListenPacket(", b"func (ep *wireEndpoint) WritePackets("):
    candidate = replace_once(candidate, function_blob(own, signature), function_blob(new, signature))
signature = b"func (w *stackDevice) Close() error"
body = function_blob(own, signature)
start = body.index(b"\n") + 1
end = body.rindex(b"\treturn nil")
cleanup = body[start:end]
body2 = body[:start] + b"\tw.closeOnce.Do(func() {\n" + cleanup + b"\t})\n" + body[end:]
candidate = replace_once(candidate, body, body2)
assert b"w.udpForwarder.Start()" in candidate and b"w.udpForwarder.Close()" in candidate
assert b"w.icmpForwarder.Close()" in candidate
assert b"context.WithTimeout(ctx, C.TCPTimeout)" in candidate
assert b"packetBuffer.DecRef() // the reference taken" in candidate
resolutions[path] = subprocess.check_output(["gofmt"], input=candidate)

path = "transport/masque/tunnel.go"
old, own, new = (checked_blob(data[rev], path, sha) for rev, sha in zip(
    ("base", "patched_tree", "upstream"),
    ("bf4f820fd856b69b2a08be3ceceb224226128e0f",
     "518e8de65f11d1ccdfd5b525a22b07ba0520da8f",
     "be6b01e9bc22fbcfff71c58ff797d18db4a9e27f")))
plain = new
for old_msg, own_msg in (
    (b"Error writing to IP connection: %v, continuing...", b"write to IP connection: %v"),
    (b"Error writing ICMP to TUN device: %v, continuing...", b"write ICMP to TUN device: %v"),
    (b"Error reading from IP connection: %v, continuine...", b"read from IP connection: %v"),
):
    plain = replace_once(plain, old_msg, own_msg)
assert balancer_code(normalize_imports(plain)) == balancer_code(normalize_imports(own))
resolutions[path] = own

path = "protocol/masque/h_config_test.go"
old, own, new = (checked_blob(data[rev], path, sha) for rev, sha in zip(
    ("base", "patched_tree", "upstream"),
    ("24e0b6dd4b17ed43ce64340c35dbdfe618b46341",
     "e835d541777ec50f2764406b500b9984cf712b22",
     "08050447bcd72ba924d7df08ca9eb525e3ebb0a4")))
assert normalize_imports(own) == normalize_imports(new)
resolutions[path] = own
# WireGuard uses the official single-flight, cancelable dial path. Keep the
# fork's pause-aware retry behavior and detach the socket before closing it.
path = "transport/wireguard/client_bind.go"
old, own, new = (checked_blob(data[rev], path, sha) for rev, sha in zip(
    ("base", "patched_tree", "upstream"),
    ("4d2deb69984c1176ac1431e6e10e2cbd2037beb5",
     "bb0276dd75c24e611edbf19d380c97325b1756fb",
     "8a56ff7c2f23c4962f3d8aece4086837fe6c0ceb")))
helper = checked_blob(data["tentative_tree"], "transport/wireguard/h_client_bind_dial.go",
                      "1662838facb37e1501e9cdf150aeac0c7bc11fff")
assert b"time.AfterFunc(C.TCPTimeout, cancelDial)" in helper
assert b"func (c *ClientBind) hAbort()" in helper
candidate = new
start = own.index(b"const (\n\tclientBindPausePollInterval")
end = own.index(b"type ClientBind", start)
candidate = replace_once(candidate, b"type ClientBind struct", own[start:end] + b"type ClientBind struct")
for signature in (b"func (c *ClientBind) receive(", b"func (c *ClientBind) Close("):
    candidate = replace_once(candidate, function_blob(new, signature), function_blob(own, signature))
start = own.index(b"func (c *ClientBind) waitActive()")
end = own.index(b"type wireConn struct", start)
candidate = replace_once(candidate, b"type wireConn struct", own[start:end] + b"type wireConn struct")
# Snapshot the opening channel under the mutex when polling pause state.
candidate = replace_once(candidate, b"\treturn !isDone(c.done)\n",
                         b"\tc.connAccess.Lock()\n\tdefer c.connAccess.Unlock()\n\treturn !isDone(c.done)\n")
candidate = replace_once(candidate, b"\ttimer := time.NewTimer(delay)\n",
                         b"\tc.connAccess.Lock()\n\topened := c.done\n\tc.connAccess.Unlock()\n\ttimer := time.NewTimer(delay)\n")
candidate = replace_once(candidate, b"\tcase <-c.done:\n\t\treturn false\n",
                         b"\tcase <-opened:\n\t\treturn false\n")
# Cancel the connection context before invoking a potentially blocking Close.
candidate = replace_once(candidate, b"\tw.PacketConn.Close()\n\tclose(w.done)\n",
                         b"\tclose(w.done)\n")
candidate = replace_once(candidate, b"\t\tw.cancel() //H\n\t} //H\n\treturn nil\n",
                         b"\t\tw.cancel() //H\n\t} //H\n\treturn w.PacketConn.Close()\n")
assert b"return c.hConnect()" in candidate
assert b"if c.aborted" in candidate
assert b"func (c *ClientBind) waitAfterFailure()" in candidate
resolutions[path] = subprocess.check_output(["gofmt"], input=candidate)

# AWG's deferred socket opening, endpoint changes and device lifecycle are
# tightly coupled. Adopt the reviewed upstream implementation as one unit.
for file, hashes in (
    ("bind.go", ("13be5bfa685895d19c314d4050642787b9083a36", "7af1e9548823029e00954f3d557c21e70ca3edd5", "4c3c6ea83e56a0e2a78a1b87a88aead1449e8030")),
    ("h_bind_later.go", ("2b8bc88fa564b319cca3f263a6560f32c3605a4d", "ff9dfe6d9d8db58579dcb2bca9ca882a0be13cfb", "387ccee6666fcb487e52b61a3ec733bbd64587a9")),
    ("h_path.go", ("621ae5eb5c0c52582d2a934b2b479af74cdc4dfb", "41bfea6f45a92a6d63082f9e02bc5a914d984351", "fcafcc6545713ac349255cd486518e7f48133ca6")),
    ("device.go", ("734a6b7546aa0acb689ae19509a24ca519cdd160", "d163192613ca7b5def224bc4d83a34ad80fb9a7d", "9db4d7b5232a6fd3ceeba6fe9350286c80dec593")),
):
    path = "transport/awg/" + file
    old, own, new = (checked_blob(data[rev], path, sha) for rev, sha in zip(
        ("base", "patched_tree", "upstream"), hashes))
    candidate = new
    if file == "bind.go":
        for signature in (b"func (b *bind_adapter) receive(", b"func (b *bind_adapter) ParseEndpoint(", b"func (b *bind_adapter) SetMark("):
            assert function_blob(own, signature) == function_blob(new, signature)
        # Retain compatibility with old internal callers that omit peer options.
        signature = b"func newBind(ctx context.Context, dial N.Dialer, peer netip.AddrPort, reserved *Reserved) conn.Bind {"
        candidate = replace_once(candidate, signature,
            b"func newBind(ctx context.Context, dial N.Dialer, peerOptions ...any) conn.Bind {\n\tvar peer netip.AddrPort\n\tvar reserved *Reserved\n\tif len(peerOptions) > 0 {\n\t\tpeer, _ = peerOptions[0].(netip.AddrPort)\n\t}\n\tif len(peerOptions) > 1 {\n\t\treserved, _ = peerOptions[1].(*Reserved)\n\t}")
        assert b"func (b *bind_adapter) abort()" in candidate
    elif file == "h_bind_later.go":
        assert b"context.WithCancel(b.ctx)" in candidate
        assert b"func (b *bind_adapter) receiveLater(" in candidate
        assert b"func (b *bind_adapter) closeLater()" in candidate
    elif file == "h_path.go":
        candidate = replace_once(candidate, b'for _, line := range strings.Split(ipcConfig, "\\n") {',
                                 b'for line := range strings.SplitSeq(ipcConfig, "\\n") {')
        assert b"func (p *devicePath) useTun(" in candidate
        assert b"func (p *devicePath) setAddresses(" in candidate
        assert b"func (p *devicePath) track(" in candidate
    elif file == "device.go":
        checked_blob(data["tentative_tree"], "transport/awg/h_listen.go",
                     "31847aca274c7a9d0dec315a6b6090401ac54ff9")
        # Keep the existing public closed error when the device is absent.
        candidate = replace_once(candidate, b'return "", os.ErrInvalid', b'return "", net.ErrClosed')
        # A failed initialization must also release a newly created tunnel.
        candidate = replace_once(candidate, b"\tif d.awgDevice != nil {\n\t\td.awgDevice.Close()\n\t}\n\treturn nil\n",
                                 b"\tif d.awgDevice != nil {\n\t\td.awgDevice.Close()\n\t} else if d.tun != nil {\n\t\treturn d.tun.Close()\n\t}\n\treturn nil\n")
        assert b"bind.abort()" in candidate
        assert b"d.path.useTun(tun)" in candidate
    resolutions[path] = subprocess.check_output(["gofmt"], input=candidate)
# WireGuard endpoint: keep on-demand startup, pause wake notification,
# and the fork's idempotent shutdown, integrating upstream MTU tracking.
path = "transport/wireguard/endpoint.go"
old, own, new = (checked_blob(data[rev], path, sha) for rev, sha in zip(
    ("base", "patched_tree", "upstream"),
    ("2bb2a12c3b212c998b4d03f604af06bbacc6e9e5",
     "ef1449d97f5effd414327780d6721dc271677010",
     "b80223b11ee9b64ee93d78ec76ccb40bab9f5f47")))
candidate = own
# The fork deliberately removed the upstream track method from mtuProber;
# the three-way merge preserved that removal. Adapt only the TCP dial branch,
# using the existing interrupt group without adding a duplicate wrapper.
mtu = git("show", data["tentative_tree"] + ":transport/wireguard/h_mtu_discovery.go")
assert b"tcp        *interrupt.Group" in mtu
assert b"p.tcp.Interrupt(true)" in mtu
assert b"func (p *mtuProber) track(" not in mtu
candidate = replace_once(candidate, b"\t\treturn e.tunDevice.DialContext(dialCtx, network, destination)",
    b"\t\tconnection, err := e.tunDevice.DialContext(dialCtx, network, destination)\n\t\tif err != nil || e.mtuProbe == nil {\n\t\t\treturn connection, err\n\t\t}\n\t\treturn e.mtuProbe.tcp.NewConn(connection, true), nil")
# Stop the bind permanently before waiting for any device operation.
candidate = replace_once(candidate, b"\t\tif e.bind != nil {\n\t\t\t_ = e.bind.Close()\n\t\t} // H: release stalled sends before waiting for device locks",
                         b"\t\tif clientBind, ok := e.bind.(*ClientBind); ok {\n\t\t\tclientBind.hAbort()\n\t\t} else if e.bind != nil {\n\t\t\t_ = e.bind.Close()\n\t\t}")
# Discovery and fallback workers can call into the device; stop them outside
# stateAccess so their shutdown cannot wait on a lock held by Close.
candidate = replace_once(candidate, b"\t\te.stateAccess.Lock()\n\t\tdefer e.stateAccess.Unlock()\n\t\tif e.mtuProbe != nil {",
                         b"\t\tif e.mtuProbe != nil {")
candidate = replace_once(candidate, b"\t\twgDevice := e.device.Swap(nil)",
                         b"\t\te.stateAccess.Lock()\n\t\tdefer e.stateAccess.Unlock()\n\t\twgDevice := e.device.Swap(nil)")
# Refresh path information after BindUpdate without holding stateAccess.
signature = b"func (e *Endpoint) BindUpdate() error"
body = function_blob(candidate, signature)
body2 = replace_once(body, b"\te.stateAccess.Lock()\n\tdefer e.stateAccess.Unlock()\n",
                     b"\te.pathChanged()\n\te.stateAccess.Lock()\n\tdefer e.stateAccess.Unlock()\n")
candidate = replace_once(candidate, body, body2)
assert b"func (e *Endpoint) waitNetworkActive(" in candidate
assert b"func (e *Endpoint) ensureDeviceStarted(" in candidate
assert b"e.closeOnce.Do(" in candidate
assert b"e.tunDevice.SetDevice(wgDevice, nil)" in candidate
resolutions[path] = subprocess.check_output(["gofmt"], input=candidate)
# WARP diagnostics and configuration validation. Explicit registration
# detours remain strict; only automatic selection may try other outbounds.
path = "protocol/warp/endpoint_warp.go"
old, own, new = (checked_blob(data[rev], path, sha) for rev, sha in zip(
    ("base", "patched_tree", "upstream"),
    ("31df5021ca8cdf7017968e28b893fc37ce162f45",
     "8d8246f4f4d2e0bd7706722d09269381704a41e7",
     "453fe3f9b9e67d87bf791919c2aaa298fe653817")))
candidate = new
for signature in (b"func warpPeerEndpoint(", b"func warpInterfaceAddresses(", b"func withWARPAPI("):
    candidate = replace_once(candidate, function_blob(new, signature), function_blob(own, signature))
# Preserve the explicit detour policy while taking the new automatic ordering.
candidate = replace_once(candidate, b"for _, outbound := range manager.Outbounds() {",
                         b"for _, outbound := range registerDetours(ctx, manager.Outbounds()) {")
candidate = replace_once(candidate, b"if err := call(outbound); err == nil {",
                         b"if err := registerThrough(outbound, call); err == nil {")
candidate = replace_once(candidate, b"return inner.Status()", b"return adapter.OutboundStatus(inner)")
# Keep conditional MASQUE state; builds without MASQUE need no link state.
candidate = re.sub(rb"(?m)^\tlinkRefused\s+atomic.Bool[^\n]*", b"\tmasqueLinkState", candidate)
assert b"masqueLinkState" in candidate and b"linkRefused   atomic.Bool" not in candidate
assert b"config == nil || len(config.Peers) == 0" in candidate
assert b"net.SplitHostPort(endpoint.Host)" in candidate
assert b"return call(outbound)" in candidate
assert b"w.attemptError.Set(err)" in candidate
resolutions[path] = subprocess.check_output(["gofmt"], input=candidate)

path = "protocol/warp/h_status.go"
own, new = added_pair(path, "719c191c6338a07b0b2e3c2c9f0b5c94286ca785",
                      "14bf21f3cbcf855de851690e699deb7e8f9536ff")
assert function_blob(own, b"func (w *WARPEndpoint) StatusError()") == function_blob(new, b"func (w *WARPEndpoint) StatusError()")
resolutions[path] = new

path = "protocol/warp/h_link_device.go"
own, new = added_pair(path, "90f531f439a07913d97676fd657694f5caf91555",
                      "ebf01e1c5f7a46db0351135aee5f69905d2fc137")
plain = replace_once(own, b' "sync/atomic"\n', b"")
plain = plain[:plain.index(b"type masqueLinkState struct")].rstrip() + b"\n"
assert plain == new
resolutions[path] = subprocess.check_output(["gofmt"], input=own)
# WARP session ownership and failover are a coupled change. Keep the
# conditional MASQUE capability interface from the fork.
path = "protocol/warp/h_mode.go"
old, own, new = (checked_blob(data[rev], path, sha) for rev, sha in zip(
    ("base", "patched_tree", "upstream"),
    ("da93a833fd7932e924d9b14c4969cf51bdab5f18",
     "b20826a3360eccf34325468c4ccf88a5a10a9c51",
     "0ec8a961fa9672f968252b42fd94ee0e838a0339")))
assert new.count(b"if masqueIncluded {") == 3
candidate = new.replace(b"if masqueIncluded {", b"if w.masqueLinkState.included() {")
for signature in (b"func warpMode(", b"func (w *WARPEndpoint) activeMode(", b"func (w *WARPEndpoint) clearFailedModes(", b"func (w *WARPEndpoint) modeFailed(", b"func modeKind(", b"func (w *WARPEndpoint) currentProfile("):
    assert balancer_code(function_blob(own, signature)) == balancer_code(function_blob(new, signature)), signature
assert b"<-previous.finished" in candidate
assert b"session.finished" in candidate
assert b"w.publish(done," in candidate
assert b"s.cancel()" in candidate
# Do not start work for a session canceled while waiting for its predecessor.
candidate = replace_once(candidate, b"\t\t<-previous.finished\n\t}\n\tmode := w.mode",
    b"\t\t<-previous.finished\n\t}\n\tif session.ctx.Err() != nil {\n\t\treturn\n\t}\n\tmode := w.mode")
resolutions[path] = subprocess.check_output(["gofmt"], input=candidate)

path = "protocol/warp/h_failover.go"
old, own, new = (checked_blob(data[rev], path, sha) for rev, sha in zip(
    ("base", "patched_tree", "upstream"),
    ("2f318fc87f08b1a20acbbfa249d1845cb5ebd929",
     "ebe375dae0a3b56c8b7f3d70cf0cdb01a2f2c774",
     "076c363b904a935b7eeee86db28092bd7914b156")))
for signature in (b"func (w *WARPEndpoint) scannerEnabled(", b"func (f *warpFailover) currentNetwork(", b"func (w *WARPEndpoint) probeNoise(", b"func (w *WARPEndpoint) probe(", b"func (f *warpFailover) currentNoise(", b"func (w *WARPEndpoint) currentFailover(", b"func peerStatus("):
    assert balancer_code(function_blob(own, signature)) == balancer_code(function_blob(new, signature)), signature
assert b"func (w *WARPEndpoint) connect(session *warpSession" in new
assert b"w.publish(done," in new
assert b"session.ctx" in new
resolutions[path] = subprocess.check_output(["gofmt"], input=new)

path = "protocol/warp/endpoint_warp_masque_stub.go"
old, own, new = (checked_blob(data[rev], path, sha) for rev, sha in zip(
    ("base", "patched_tree", "upstream"),
    ("6113190b3301fa7ab6d4abc86b34c17232e3f3d7",
     "77c8e92ae2945bb44f8904fb1303e13ed6bc1b7c",
     "d1c9420ec8cbf334a2ae154c0353ba6cb1842a90")))
candidate = replace_once(own, b"connectMASQUE(done <-chan struct{}, profile *warpProfile)",
                         b"connectMASQUE(session *warpSession, profile *warpProfile)")
assert b"func (*masqueLinkState) included() bool { return false }" in candidate
assert b"type masqueLinkState struct{}" in candidate
resolutions[path] = subprocess.check_output(["gofmt"], input=candidate)

# The nonconflicting MASQUE implementation must expose the same new signature
# and retain the fork's build-dependent capability method.
masque = git("show", data["tentative_tree"] + ":protocol/warp/endpoint_warp_masque.go")
assert b"connectMASQUE(session *warpSession" in masque
assert b"func (*masqueLinkState) included() bool" in masque
# WARP tests: preserve strict explicit detour and IPv6 regression coverage.
# The newer incomplete-profile tests supersede the two older no-panic cases.
path = "protocol/warp/h_endpoint_warp_test.go"
old, own, new = (checked_blob(data[rev], path, sha) for rev, sha in zip(
    ("base", "patched_tree", "upstream"),
    ("f4ed3357db664b11e606a4b5b970b25c4df3b9dd",
     "d3fdd5d39c154b86c20d645a69f81e8d213b37c6",
     "08f31da11a8d1e420b3caf4681d8e5e584b9abd3")))
candidate = replace_once(new,
    function_blob(new, b"func TestH_GetWarpProfileFallsBackToAllOutbounds("),
    function_blob(own, b"func TestH_GetWarpProfileHonorsExplicitDetour("))
candidate += b"\n" + function_blob(own, b"func TestH_WARPIPv6PeerAddress(") + b"\n"
assert b"func TestH_WARPIncompleteProfileIsNotUsed(" in candidate
assert b"func TestH_WARPIncompleteRegisteredDeviceFails(" in candidate
assert b"func TestH_WARPIncompleteSavedDeviceIsRetired(" in candidate
resolutions[path] = subprocess.check_output(["gofmt"], input=candidate)

path = "h_warp_detour_live_test.go"
old, own, new = (checked_blob(data[rev], path, sha) for rev, sha in zip(
    ("base", "patched_tree", "upstream"),
    ("0a360189a14613cb8bead9df805f38e8c149b06a",
     "5680b7f5d843a74e88ea0e25a1342cef6c202052",
     "600434fdb5f56e9585baf76dc4e5726cd408362d")))
plain = replace_once(new, b"b.warp().Status()", b"adapter.OutboundStatus(b.warp())")
plain = replace_once(plain, b'const hWARPLivePassword = "warp-live-password"\nconst hWARPLiveUUID = "b831381d-6324-4d53-ad4f-8cda48b30811"',
    b'const (\n\thWARPLivePassword = "warp-live-password"\n\thWARPLiveUUID = "b831381d-6324-4d53-ad4f-8cda48b30811"\n)')
plain = replace_once(plain,
    b'func() bool { return instance.ready() && instance.displayType() == "WARP \xc2\xb7 MASQUE" && instance.traceOK() },',
    b'func() bool {\n\t\t\t\t\treturn instance.ready() && instance.displayType() == "WARP \xc2\xb7 MASQUE" && instance.traceOK()\n\t\t\t\t},')
assert b"".join(balancer_code(plain).split()) == b"".join(balancer_code(own).split())
resolutions[path] = own

# AWG endpoint: deferred DNS and bind lifecycle, while retaining the fork's
# boolean MTU setting, key handling, and secret-free logging.
path = "protocol/awg/endpoint.go"
old, own, new = (checked_blob(data[rev], path, sha) for rev, sha in zip(
    ("base", "patched_tree", "upstream"),
    ("7c447636d9def8220a0398d2cc4df107e6c30b91",
     "ca0c51ff0dcd9c1872f94397f690f8b7a911a21e",
     "097cbf18968f6dafc5a1dccabf1a383dd4ae78af")))
opts = git("show", data["tentative_tree"] + ":option/awg.go")
assert re.search(rb"MTUDiscovery\s+bool\s+", opts)
candidate = replace_once(new, b'logger.Debug("AWG IPC config:\\n", ipc)', b'logger.Debug("AWG IPC configuration prepared")')
candidate = replace_once(candidate, b"options.MTUDiscovery == nil || *options.MTUDiscovery", b"options.MTUDiscovery")
# Preserve support for the existing base64 key format and also accept hex.
needle = b'\t// AmneziaWG 3.x\n\tfor _, setting := range []struct{ key, value string }{\n\t\t{"header_protection_key", awg.HeaderProtectionKey},'
replacement = b'''\t// AmneziaWG 3.x
	if awg.HeaderProtectionKey != "" {
		key, err := hex.DecodeString(awg.HeaderProtectionKey)
		if err != nil || len(key) != 32 {
			key, err = base64.StdEncoding.DecodeString(awg.HeaderProtectionKey)
		}
		if err != nil || len(key) != 32 {
			return "", E.New("invalid AWG header protection key")
		}
		s += "\\nheader_protection_key=" + hex.EncodeToString(key)
	}
	for _, setting := range []struct{ key, value string }{'''
candidate = replace_once(candidate, needle, replacement)
assert b"deferDomainPeers(options)" in candidate
assert b"o.startResolvingPeers()" in candidate
assert b"peerUsers.Lookup(source.Addr)" in candidate
assert b'logger.Debug("AWG IPC configuration prepared")' in candidate
resolutions[path] = subprocess.check_output(["gofmt"], input=candidate)
# Retain the existing base64 key regression test. The fork's option fields
# remain strings, so number-form JSON is not assumed to be supported.
path = "protocol/awg/h_endpoint_test.go"
old, own, new = (checked_blob(data[rev], path, sha) for rev, sha in zip(
    ("base", "patched_tree", "upstream"),
    ("1953ccb757dd7604e754c755084d566196a2c4c4",
     "fe9df075f319b38f7e34ad053ac1b0063a996027",
     "a0be732144e9920cbadf691d153cd02de2e0bd5e")))
candidate = new
if not b"type AwgRange string" in opts:
    candidate = replace_once(candidate, b'"rekey_after_time": 120', b'"rekey_after_time": "120"')
    candidate = replace_once(candidate, b'"reject_after_time": 180, "keepalive_timeout": 10', b'"reject_after_time": "180", "keepalive_timeout": "10"')
# Keep the actual device acceptance tests active; the merged dependency
# must prove compatibility rather than hide failures with the old skip.
candidate += b"\n" + function_blob(own, b"func TestH_GenIpcConfigAWG3AcceptedByDevice(") + b"\n"
assert b"func TestH_GenIpcConfigS3S4AcceptedByDevice(" in candidate
assert b"func TestH_PeerAddresses(" in candidate
assert b"func TestH_EndpointListenPortConflictsWithDetour(" in candidate
assert b"t.Skip(" not in candidate
resolutions[path] = subprocess.check_output(["gofmt"], input=candidate)
# Compile-correct embedded ServerOptions literals, with the same tests
# and exact protocol configuration as in the fork.
path = "protocol/shadowsocks/h_method_test.go"
own, new = added_pair(path, "13087820c1f7f04219d9eb347d8b4d2cabfb0020",
                      "9e5c3d622d746793294553cd6d861dde35800215")
plain = replace_once(own, b'\t\t\t\tServer:     "127.0.0.1",\n\t\t\t\tServerPort: 1,',
                     b'\t\t\t\tServerOptions: option.ServerOptions{Server: "127.0.0.1", ServerPort: 1},')
assert subprocess.check_output(["gofmt"], input=plain) == subprocess.check_output(["gofmt"], input=new)
assert b"\n\tServerOptions\n" in git("show", data["tentative_tree"] + ":option/shadowsocks.go")
resolutions[path] = new

path = "protocol/mieru/h_close_test.go"
own, new = added_pair(path, "f8ec83e2d35e5679155d47cc47087e8f9f6737f9",
                      "6a76b810326fcec8467e919a6dce081307b8cdcf")
plain = replace_once(own, b'\t\t\tServer:       "127.0.0.1",',
                     b'\t\t\tServerOptions: option.ServerOptions{Server: "127.0.0.1"},')
assert subprocess.check_output(["gofmt"], input=plain) == subprocess.check_output(["gofmt"], input=new)
assert b"\n\tServerOptions\n" in git("show", data["tentative_tree"] + ":option/mieru.go")
resolutions[path] = new

path = "protocol/mieru/h_resolver_test.go"
own, new = added_pair(path, "ed9a9596cc5ad74919fc2d002f23ad26a8ac3629",
                      "23c9728cae5f91d0bea05fd3ed161ddd253d55e1")
assert normalize_imports(own) == normalize_imports(new)
resolutions[path] = own

path = "protocol/psiphon/h_notice_test.go"
old, own, new = (checked_blob(data[rev], path, sha) for rev, sha in zip(
    ("base", "patched_tree", "upstream"),
    ("7fc6fc8121e0d484351f9288b70d22541530b753",
     "aba0b2b6370b7ff34cd6f4d7e88ff47b07fc4c00",
     "3e3cdc9b26dacff938751d4dc0595e3c5e3a3fed")))
plain = new.replace(b"map[string]interface{}", b"map[string]any")
plain = replace_once(plain, b"with the last instance gone, a notice goes nowhere",
                     b"the writer stays installed, but no instances remain registered")
assert normalize_imports(plain) == normalize_imports(own)
resolutions[path] = own
# Preserve Mieru's optional MTU and validation. Both revisions otherwise
# differ only in pointer construction, comments and error capitalization.
path = "protocol/mieru/outbound.go"
old, own, new = (checked_blob(data[rev], path, sha) for rev, sha in zip(
    ("base", "patched_tree", "upstream"),
    ("927a01ab0d7595cc297d52cb9513abc2a85ce127",
     "e9f2e613be31e7c10632160ec2e76f2cbeec0cb9",
     "fc630385258c0cb0d156e07f7857258e46abe3c0")))
plain = replace_once(new, b'\t"google.golang.org/protobuf/proto"\n', b"")
plain = plain.replace(b"proto.String(", b"new(")
plain = replace_once(plain, b"Transport of Server Port is not defined!", b"transport of Server Port is not defined")
fork_plain = replace_once(own, b"\tif options.MTU != 0 {\n\t\tconfig.Profile.Mtu = new(options.MTU)\n\t}\n", b"")
fork_plain = replace_once(fork_plain, b'\tif options.MTU != 0 && (options.MTU < 1280 || options.MTU > 1500) {\n\t\treturn fmt.Errorf("mieru MTU must be between 1280 and 1500")\n\t}\n', b"")
assert balancer_code(normalize_imports(plain)) == balancer_code(normalize_imports(fork_plain))
assert re.search(rb"MTU\s+int32\s+", git("show", data["tentative_tree"] + ":option/mieru.go"))
resolutions[path] = own

# Preserve the public Close method, plus upstream scope lifecycle and
# read-ahead/batching. The same cleanup can be called by either entrypoint.
path = "protocol/shadowsocks/outbound.go"
old, own, new = (checked_blob(data[rev], path, sha) for rev, sha in zip(
    ("base", "patched_tree", "upstream"),
    ("700206fe2fa5c7e8e9894557ec7d7e82df1fc79a",
     "9e537810b1de5415c7e4ae9956ad4526a838f665",
     "8476570865042ef685df46b9f9aab10f8ec82738")))
candidate = new
assert b"scope.Add(h.multiplexDialer.Close)" in candidate
assert b"readahead.New(outConn)" in candidate
assert b"hBatch(h.method," in candidate
assert b"wholepacket.Wrap(h.method.DialPacketConn(outConn))" in candidate
assert b"dialShadowsocks2022(ctx, h.method" in candidate
# Upstream lifecycle owns cleanup. Keep explicit Close compatibility by
# routing both calls through the same sync.Once-protected operation.
candidate = replace_once(candidate, b'\t"context"\n', b'\t"context"\n\t"sync"\n')
candidate = replace_once(candidate, b"type Outbound struct {\n", b"type Outbound struct {\n\tcloseOnce sync.Once\n\tcloseErr error\n")
candidate = replace_once(candidate, b"scope.Add(h.multiplexDialer.Close)", b"scope.Add(h.Close)")
candidate += b'''
func (h *Outbound) Close() error {
	h.closeOnce.Do(func() {
		h.closeErr = common.Close(common.PtrOrNil(h.multiplexDialer))
	})
	return h.closeErr
}
'''
resolutions[path] = subprocess.check_output(["gofmt"], input=candidate)
# gRPC: preserve the fork's cancellable wait for the shared connection
# and the close-versus-publication race fix.
path = "transport/v2raygrpc/client.go"
old, own, new = (checked_blob(data[rev], path, sha) for rev, sha in zip(
    ("base", "patched_tree", "upstream"),
    ("31226df41cdf2cda5209d6072c14f8d59992e38b",
     "303a665161cc1f136a8226c5a251963d1c5b6dfb",
     "6f772cb355b2c7540f1f22faf24198df794247cf")))
for signature in (b"func (c *Client) DialContext(", b"func (c *Client) Close("):
    assert balancer_code(function_blob(own, signature)) == balancer_code(function_blob(new, signature))
assert b"case <-ctx.Done():" in own and b"case <-c.ctx.Done():" in own
assert b"c.conn.CompareAndSwap(conn, nil)" in own
candidate = replace_once(own, b"\tconn := c.conn.Load()\n",
                         b"\tif c.ctx.Err() != nil {\n\t\treturn nil, net.ErrClosed\n\t}\n\tif ctx.Err() != nil {\n\t\treturn nil, context.Cause(ctx)\n\t}\n\tconn := c.conn.Load()\n")
resolutions[path] = subprocess.check_output(["gofmt"], input=candidate)

# SSH: retain legacy entrypoints while incorporating upstream session
# detachment, multiplex context and UDP capability reporting.
path = "protocol/ssh/outbound.go"
old, own, new = (checked_blob(data[rev], path, sha) for rev, sha in zip(
    ("base", "patched_tree", "upstream"),
    ("e745d674a79df9f1660aba056d64d6fe6951ab00",
     "16ff59e0c4cb07069f101ac40cb8f470f9d430b1",
     "67f91080ee719c9607feddc26a08306f4f2955c7")))
candidate = new
needle = b"\tscope.Add(func() error {\n\t\ts.closeOnce.Do(func() { close(s.done) })\n\t\ts.clientAccess.Lock()\n\t\tclientConn := s.clientConn\n\t\ts.clientAccess.Unlock()\n\t\treturn common.Close(clientConn)\n\t})"
candidate = replace_once(candidate, needle, b"\tscope.Add(s.Close)")
candidate += b'''
func (s *Outbound) Close() error {
	s.closeOnce.Do(func() { close(s.done) })
	s.clientAccess.Lock()
	clientConn := s.clientConn
	s.client = nil
	s.clientConn = nil
	s.ready.Store(false)
	s.clientAccess.Unlock()
	return common.Close(clientConn)
}
'''
candidate += b"\n" + function_blob(own, b"func (s *Outbound) IsReady(") + b"\n"
display = b"func (s *Outbound) DisplayType() string { return C.ProxyDisplayName(s.Type()) }"
assert display in own
assert b"func (s *Outbound) DisplayType(" not in candidate
candidate += b"\n" + display + b"\n"
assert b"adapter.ContextForMultiplexSession(ctx)" in candidate
assert b"options.UDPOverTCP == nil || !options.UDPOverTCP.Enabled" in candidate
resolutions[path] = subprocess.check_output(["gofmt"], input=candidate)

# Same test code; only import grouping differs.
for path, own_sha, new_sha in (
    ("protocol/hiddify/gooserelay/h_status_test.go", "3b48e55cd1cc3c7ec582513b3741ca863625cf0f", "19a64d4f35b29dda722def85f097f551a303fb71"),
    ("protocol/hiddify/soar/h_soar_test.go", "880bf2c68d36108dd03e5b4fcd7763964ee729bf", "7f20f4d0496801a70a85e4d40794dba5a749dd88"),
):
    own, new = added_pair(path, own_sha, new_sha)
    assert normalize_imports(own) == normalize_imports(new)
    resolutions[path] = own
# DNSTT: keep the fork's formatting and receiver names. Verify that every
# other line is the same, including the resolver and tunnel behavior.
for file, hashes in (
    ("tester.go", ("e88be215e2be26f4af98fbdedaeb42c1e118dc18", "157619dbb04a201fd0a6217da046554833df9362", "f0b9a082f18dec3c91f0289f68743b7a0f18c5fb")),
    ("tools.go", ("4f3bbe70949657fa263841ab04cfe7ece5118b87", "83edf9ded29037494119731809d26b7f4d31ec4d", "8c0136ada29c7c06c924f0bdf1ca82f686c29029")),
    ("dnstt_func.go", ("f2dc3c58991c43ec98622476010d8ae56dbf0780", "602483d509c0d168e1d4c10d92160bc092c89546", "ef1f261cfb0b6cf53d3b08344a70765f89b2308b")),
):
    path = "protocol/hiddify/dnstt/" + file
    old, own, new = (checked_blob(data[rev], path, sha) for rev, sha in zip(
        ("base", "patched_tree", "upstream"), hashes))
    plain = new
    if file == "dnstt_func.go":
        plain = re.sub(rb"\bc\.", b"h.", plain.replace(b"(c *Outbound)", b"(h *Outbound)"))
    assert [l for l in normalize_imports(plain).splitlines() if l.strip()] == [l for l in normalize_imports(own).splitlines() if l.strip()], path
    resolutions[path] = own

path = "protocol/hiddify/dnstt/h_dnstt_test.go"
old, own, new = (checked_blob(data[rev], path, sha) for rev, sha in zip(
    ("base", "patched_tree", "upstream"),
    ("da0cc0a1e982feb567dfcc6e02e1b783af383992",
     "be60fdeb310b6783154abb417176cbc5667245d7",
     "14d129cf4f002c09a5d3fef0c0b7f25d301676c5")))
# Extend the existing test without comparing independently randomized
# map iteration order. The built-in membership check remains intact.
candidate = replace_once(own, b"\t\tseen := map[string]bool{}\n\t\tfor _, r := range resolvers[:len(resolvers)-1] {",
    b"\t\tseen := map[string]bool{}\n\t\tseenAddresses := map[string]bool{}\n\t\tfor _, r := range resolvers[:len(resolvers)-1] {\n\t\t\trequire.False(t, seenAddresses[r.Resolver.ResolverAddr], r.Resolver.ResolverAddr)\n\t\t\tseenAddresses[r.Resolver.ResolverAddr] = true")
assert b"require.Len(t, seen, len(builtin))" in candidate
assert b"dnspool.TierDiscovery" in candidate
resolutions[path] = subprocess.check_output(["gofmt"], input=candidate)

path = "protocol/hiddify/soar/outbound.go"
own, new = added_pair(path, "ff490beebbb1706e090c0f3838bce8671efa0d0b",
                      "5021d94df4bb5214777c59810f23f662e0e78d2a")
plain = replace_once(new, b'for _, label := range strings.Split(zone, ".") {',
                     b'for label := range strings.SplitSeq(zone, ".") {')
assert [l for l in normalize_imports(plain).splitlines() if l.strip()] == [l for l in normalize_imports(own).splitlines() if l.strip()]
resolutions[path] = own

# MTU search: the two implementations differ only in the redundant initial
# high bound. Keep the fork's simpler declaration and all upstream behavior.
path = "hiddify/mtusearch/mtusearch.go"
old, own, new = (checked_blob(data[rev], path, sha) for rev, sha in zip(
    ("base", "patched_tree", "upstream"),
    ("8fa13ca0ce5fcd5006ed0f72ea92086fd6bad8c4",
     "74c7b27337785fcc4174c952bf5fea1575830342",
     "5e262529c7c30c519748c051c1a021742d7075da")))
plain = replace_once(new, b"low, high := 0, len(sizes)", b"low := 0")
plain = replace_once(plain, b"\thigh = len(sizes) - 1", b"\thigh := len(sizes) - 1")
assert plain == own
resolutions[path] = own

# Peer fallback: retain per-instance timing from the fork, and upstream's
# separate move lock so Close does not wait on a blocked device operation.
path = "hiddify/peerfallback/peerfallback.go"
old, own, new = (checked_blob(data[rev], path, sha) for rev, sha in zip(
    ("base", "patched_tree", "upstream"),
    ("4683906b0ac95192e064f99e83b9c757221da221",
     "d6e3f31f864454c30bbc9b104a4518bba1816d33",
     "7324c8e37cdb3f4d3bf70c85a5858a2bcb7dfbdb")))
candidate = replace_once(new, b"\toptions Options\n",
    b"\toptions Options\n\twait time.Duration\n\tinterval time.Duration\n")
candidate = replace_once(candidate, b"&Fallback{options: options, since: time.Now()}",
    b"&Fallback{options: options, since: time.Now(), wait: Wait, interval: Interval}")
candidate = replace_once(candidate, b"time.NewTicker(Interval)", b"time.NewTicker(f.interval)")
candidate = replace_once(candidate, b"time.Since(f.since) < Wait", b"time.Since(f.since) < f.wait")
assert b"moveAccess sync.Mutex" in candidate
assert b"f.closed = true" in candidate
assert b"f.access.Unlock()\n\tif cancel != nil" in candidate
resolutions[path] = subprocess.check_output(["gofmt"], input=candidate)

# The fork's MemoryTun API and stack-only port semantics are retained by
# the clean merge of device.go. Restore upstream's construction cleanup and
# error logging without reintroducing incompatible front/rear-space fields.
path = "transport/device/stack.go"
old, own, new = (checked_blob(data[rev], path, sha) for rev, sha in zip(
    ("base", "patched_tree", "upstream"),
    ("2c3cc6d10c58f526595a7671b62989b3365f12a8",
     "8e62858ced6d7a77bea1d09b89c24ec27c36504a",
     "1cbc604d6bf103a64b349b500587ab2e14d72d43")))
device_api = checked_blob(data["tentative_tree"], "transport/device/device.go",
                          "ef8c72619251033f4161580c4bc5268e75cf24b0")
assert b"PacketHeadroom" in device_api and b"PacketFrontHeadroom" not in device_api
for signature in (b"func (d *stackDevice) UpdateConfiguration(",
                  b"func (d *stackDevice) DialContext(",
                  b"func (d *stackDevice) ListenPacket(",
                  b"func (d *stackDevice) bindAddressLocked("):
    assert function_blob(own, signature) == function_blob(new, signature)
candidate = replace_once(own, b"\t\t\t_ = device.writeOutbound(packets)\n",
    b"\t\t\tif err := device.writeOutbound(packets); err != nil {\n\t\t\t\toptions.Logger.Debug(E.Cause(err, \"write packet batch\"))\n\t\t\t}\n")
candidate = replace_once(candidate, b"device.stack, err = newStack(options, device.memoryTun)\n\tif err != nil {\n\t\treturn nil, err",
    b"device.stack, err = newStack(options, device.memoryTun)\n\tif err != nil {\n\t\tdevice.memoryTun.Close()\n\t\treturn nil, err")
resolutions[path] = subprocess.check_output(["gofmt"], input=candidate)

apply_reviewed(data, resolutions)
