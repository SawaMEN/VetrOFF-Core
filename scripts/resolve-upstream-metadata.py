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
        assert candidate[:candidate.index(marker)].rstrip() == own.rstrip()
        assert b"func TestH_SubscribeBeforeTheGroupIsRegistered(" in candidate
    else:
        assert candidate == own
    resolutions[path] = subprocess.check_output(["gofmt"], input=candidate)
apply_reviewed(data, resolutions)
