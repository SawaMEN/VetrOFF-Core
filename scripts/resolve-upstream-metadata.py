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

def apply_reviewed(data, resolutions, clean_adjustments=None):
    component = data["component"]
    assert set(resolutions) <= set(data["conflicts"])
    clean_adjustments = clean_adjustments or {}
    assert not set(clean_adjustments) & set(data["conflicts"])
    conflict_resolutions = resolutions
    resolutions = {**resolutions, **clean_adjustments}
    with tempfile.TemporaryDirectory() as tmp:
        env = dict(os.environ, GIT_INDEX_FILE=str(Path(tmp) / "index"))
        git("read-tree", data["tentative_tree"], env=env)
        for path, content in resolutions.items():
            if content is None:
                git("update-index", "--force-remove", "--", path, env=env)
                assert not git("ls-files", "--", path, env=env)
                print(f"Reviewed deletion: {component}/{path}")
                continue
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
            if content is None:
                assert not git("ls-tree", tree, "--", path)
            else:
                assert git("show", tree + ":" + path) == content
        for path in sorted(set(resolutions) - changed):
            print(f"Reviewed unchanged provisional entry: {component}/{path}")
        (report / (component + "-reviewed-resolutions.patch")).write_bytes(
            git("diff", "--binary", "--full-index", data["tentative_tree"], tree)
        )
    remaining = sorted(set(data["conflicts"]) - set(conflict_resolutions))
    data.update(reviewed_tree=tree, reviewed_resolutions=sorted(conflict_resolutions),
                clean_adjustments=sorted(clean_adjustments), remaining_conflicts=remaining,
                deleted_resolutions=sorted(path for path, content in resolutions.items() if content is None))
    (report / (component + "-reviewed.json")).write_text(json.dumps(data, indent=2) + "\n")
    print(f"{component}: {len(conflict_resolutions)} reviewed resolutions, {len(remaining)} unresolved paths")
    if clean_adjustments:
        print(f"{component}: {len(clean_adjustments)} additional compatibility adjustments")
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

# Docker and OpenWrt: advance the original package versions only. These
# files describe upstream distribution packages, not VetrOFF stable pins.
for path, hashes, previous, current in (
    ("platform/docker/Dockerfile", (
        "3ad655730d5cf5cedffb33dd3bc1f0cb8800621d",
        "a92386512b0b0e1bf068950b3fa15cfb0cd8b622",
        "3784a54a0dcb0e906001e090c3d32fdde1631659",
    ), b"ENV VERSION=v5.5.5", b"ENV VERSION=v5.9.15"),
    ("platform/wrt/Makefile", (
        "e3b4ba7e29563f5ad221d90ef423975f2b2441a0",
        "eec54b50430002999b767eafb88ee92c76c79be8",
        "bb79055964f979d441551e7628cc6fde737c9a2d",
    ), b"PKG_VERSION:=5.5.5", b"PKG_VERSION:=5.9.15"),
):
    old, own, new = (checked_blob(data[rev], path, sha)
                     for rev, sha in zip(("base", "patched_tree", "upstream"), hashes))
    assert own.count(previous) == 1 and new.count(current) == 1
    assert own.replace(previous, current, 1) == new, path
    resolutions[path] = new

# The fork's only entrypoint changes were exec and the renamed executable.
# Upstream retains both, removes dead comments and adds an opt-in router
# argument. Keep the upstream script without modifying the host firewall.
path = "platform/docker/hiddify.sh"
old, own, new = (checked_blob(data[rev], path, sha)
                 for rev, sha in zip(("base", "patched_tree", "upstream"), (
                     "5b1f805e67a3530967253754e128e8f8c3e575ff",
                     "70ef1efb02d13e3c48cb967a65e4d3b460f2218d",
                     "97bd2df9c5eaff4007f274dd37dc807cdd872eaf")))
assert old.count(b"/hiddify/HiddifyCli run") == 2
assert old.replace(b"/hiddify/HiddifyCli run", b"exec /hiddify/hiddify-core run") == own
assert b'set -- run --config "$CONFIG"' in new
assert b'[ -f "/hiddify/hiddify.json" ] && set -- "$@" -d /hiddify/hiddify.json' in new
assert b'exec /hiddify/hiddify-core "$@"' in new
subprocess.run(["sh", "-n"], input=new, check=True)
resolutions[path] = new

# Preserve the public Go field name used by the fork and its single
# lan-sharing-password JSON key. The options struct and other callers
# still need reconciliation before the complete Core candidate can build.
path = "v2/config/inbound_ports.go"
own = checked_blob(patched, path, "8f9a380e6e3e76c9aee95f04bf3bc622073a4df4")
new = checked_blob(upstream, path, "c2df169ef8e04d61a1bf806be2df222fd6e105c7")
assert new.count(b"hopt.ProxySecret") == 2
assert new.replace(b"hopt.ProxySecret", b"hopt.LanSharingPassword") == own
resolutions[path] = own

path = "v2/config/inbound_ports_test.go"
own = checked_blob(patched, path, "393db9dab2c1a8eed219a39864542314784cfd68")
new = checked_blob(upstream, path, "2f12fa63eea39d80afb5565081a652ff3a72d2ab")
# Keep the fork regression for the embedded JSON field, as well as every
# upstream port-policy test. Retain the fork's existing profile fixture.
start = own.index(b"\n\nfunc TestLANSharingPasswordJSONDoesNotShadowConfiguredSecret(")
end = own.index(b"\nconst ports =", start)
regression = own[start:end]
assert b"opts.LanSharingPassword" in regression and b"localUsers(opts)" in regression
assert new.count(b"Content: oneProxyProfile") == 1
candidate = new.replace(b"Content: oneProxyProfile", b"Content: chainTestProfile")
marker = b"\nconst ports ="
assert candidate.count(marker) == 1
assert candidate.replace(marker, regression + marker, 1) == own
# Add focused regressions to the existing port test file. The real config
# package test uses the merged options and the original locked dependencies.
candidate = own.replace(b'import (\n', b'import (\n\t"encoding/json"\n\t"strings"\n', 1)
candidate += b'''
func TestVetrOFFMergedOptionsJSON(t *testing.T) {
	opts := DefaultHiddifyOptions()
	data := []byte("{\\\"lan-sharing-password\\\":\\\"secret\\\",\\\"local-proxy-auth\\\":true,\\\"wifi-vpn-sharing\\\":true,\\\"keep-top\\\":3,\\\"keep-top-batch\\\":7,\\\"keep-top-quick\\\":true,\\\"keep-top-countries\\\":[\\\"DE\\\"],\\\"keep-top-pinned\\\":[\\\"a\\\"],\\\"delegate-system-proxy\\\":true,\\\"log-level-until\\\":\\\"2026-11-25T00:00:00Z\\\",\\\"log-level-after\\\":\\\"warn\\\"}")
	if err := json.Unmarshal(data, opts); err != nil {
		t.Fatal(err)
	}
	if opts.LanSharingPassword != "secret" || !opts.LocalProxyAuth || !opts.WifiVPNSharing ||
		opts.KeepTop != 3 || opts.KeepTopBatch != 7 || !opts.KeepTopQuick ||
		len(opts.KeepTopCountries) != 1 || opts.KeepTopCountries[0] != "DE" ||
		len(opts.KeepTopPinned) != 1 || opts.KeepTopPinned[0] != "a" ||
		!opts.DelegateSystemProxy || opts.LogLevelUntil == "" || opts.LogLevelAfter != "warn" {
		t.Fatal("merged options lost configured values")
	}
	encoded, err := json.Marshal(opts)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Count(string(encoded), "\\\"lan-sharing-password\\\"") != 1 {
		t.Fatal("LAN password JSON field was duplicated or lost")
	}
	var roundTrip HiddifyOptions
	if err := json.Unmarshal(encoded, &roundTrip); err != nil || roundTrip.LanSharingPassword != "secret" {
		t.Fatal("LAN password failed to round trip")
	}
}

func TestVetrOFFKeepTopNumericOverrides(t *testing.T) {
	opts := GetOverridableHiddifyOptions(map[string][]string{
		"keep-top": {"3"}, "keep-top-batch": {"-1"}, "keep-top-quick": {"true"},
		"mux.max-streams": {"12"},
	})
	if opts.KeepTop != 3 || opts.KeepTopBatch != -1 || !opts.KeepTopQuick || opts.Mux.MaxStreams != 12 {
		t.Fatal("numeric or boolean subscription overrides were lost")
	}
}
'''
resolutions[path] = subprocess.check_output(["gofmt"], input=candidate)

# HiddifyOptions: use the fork's field names and retain every fork JSON
# setting. Import the new upstream settings as exact reviewed blocks.
path = "v2/config/hiddify_option.go"
own = checked_blob(patched, path, "79ef4deb62e0c00f7632d0fd4acb035377945828")
new = checked_blob(upstream, path, "6b47205d0d7751963caac6bf677bf9ca14a1596b")
candidate = own
def core_insert(anchor, payload):
    global candidate
    assert candidate.count(anchor) == 1, anchor
    candidate = candidate.replace(anchor, payload + anchor, 1)

def core_section(start, end):
    i = new.index(start)
    return new[i:new.index(end, i)]

core_insert(b"\tLogFile ", core_section(b"\tLogLevelUntil ", b"\tLogFile "))
start = b"\t// KeepTop, when above 0,"
end = b"\t// KeepTags are the tags"
old_keep = candidate[candidate.index(start):candidate.index(end)]
assert b"\tKeepTop int " in old_keep
candidate = candidate.replace(old_keep, core_section(start, end), 1)
core_insert(b"\tMixedPort ", core_section(b"\t// DelegateSystemProxy ", b"\tMixedPort "))
core_insert(b"}\n\ntype URLTestOptions", core_section(b"\t// RouterMode ", b"}\n\ntype URLTestOptions"))

# Integer overrides were parsed to int64/uint64 and silently ignored for
# Go int/uint16/duration fields. Assign directly only when representable.
for kind, parse, overflow, setter in (
    (b"int", b"parseInt", b"OverflowInt", b"SetInt"),
    (b"uint", b"parseUint", b"OverflowUint", b"SetUint"),
):
    name = kind + b"Val"
    before = b"\t\t\t\t\tif " + name + b", err := " + parse + b"(overrideValue); err == nil {\n\t\t\t\t\t\tparsedValue = reflect.ValueOf(" + name + b")\n\t\t\t\t\t}"
    after = b"\t\t\t\t\tif " + name + b", err := " + parse + b"(overrideValue); err == nil && !field." + overflow + b"(" + name + b") {\n\t\t\t\t\t\tfield." + setter + b"(" + name + b")\n\t\t\t\t\t}"
    assert candidate.count(before) == 1
    candidate = candidate.replace(before, after, 1)

# Verify per-struct field inventories, including the embedded inbound
# settings, so a duplicate JSON key cannot silently shadow the password.
import re
def core_fields(blob):
    result = {}
    for name, body in re.findall(rb"type (\w+) struct \{\n(.*?)\n\}", blob, re.S):
        fields = {}
        for field, typ, tag in re.findall(rb"^\t(\w+)\s+([^" + bytes([96]) + rb"\n]+?)\s+" + bytes([96]) + rb'json:"([^"]+)"', body, re.M):
            key = tag.split(b",")[0]
            assert key not in fields, (name, key)
            fields[key] = (field, typ.strip(), tag)
        result[name] = fields
    return result

fork_fields, upstream_fields, merged_fields = map(core_fields, (own, new, candidate))
assert set(merged_fields) == set(fork_fields) == set(upstream_fields)
for name, fields in fork_fields.items():
    official = dict(upstream_fields[name])
    if name == b"InboundOptions":
        del official[b"lan-sharing-password"] # fork stores it in HiddifyOptions
    assert merged_fields[name] == {**official, **fields}, name
assert candidate.count(b'json:"lan-sharing-password,omitempty"') == 1
resolutions[path] = subprocess.check_output(["gofmt"], input=candidate)

# KeepTop config helper and its tests: the new implementation is a strict
# extension of the locked helper. Verify all old functions and test bodies.
path = "v2/config/keep_top.go"
own = checked_blob(patched, path, "72d0c4e09234dc02c5d9fe560be2914f66da4bbf")
new = checked_blob(upstream, path, "9d755c09f6fcd05b1ca9d2020949c4295bff6e23")
plain = new[:new.index(b"// lazyTypes")] + new[new.index(b"// ChosenProxies"):]
plain = plain.replace(b'\t"time"\n', b"").replace(b'\t"github.com/sagernet/sing/common/json/badoption"\n', b"")
plain = plain[:plain.index(b"// With keep-top")] + own[own.index(b"// With keep-top"):own.index(b"// UserProxyTags")] + plain[plain.index(b"// UserProxyTags"):]
assert plain == own, path
resolutions[path] = new

path = "v2/config/keep_top_test.go"
own = checked_blob(patched, path, "36e89e9475e7e07398722c2555f6b3158ba8b7fd")
new = checked_blob(upstream, path, "67bb41702fee5ecb91f6797db340a400f9c48299")
plain = new[:new.index(b"\nconst keepTopEndpointsProfile")]
for imp in (b"fmt", b"time", b"github.com/sagernet/sing/common/json/badoption"):
    plain = plain.replace(b'\t"' + imp + b'"\n', b"")
assert plain == own, path
resolutions[path] = new

# Core KeepTop delegates selection to the new sing-box lazy monitoring
# implementation. Preserve the fork's multi-format profile parser, which
# fixed startup from raw share links. The old reload-driven selection
# implementation and its private tests are replaced by the new runtime tests.
path = "v2/hcore/keep_top.go"
own = checked_blob(patched, path, "95dbdba8804230cb63cf187932f96e9b7cb7a170")
new = checked_blob(upstream, path, "360e0ac70763c6dd917ecee603df111ff60eaddd")
start = own.index(b"\t// Profiles may contain share URIs")
end = own.index(b"\tif err != nil {", start)
parser = own[start:end]
assert parser.count(b"static.HiddifyOptions") == 1
assert b"config.ParseConfig(" in parser
before = b"\tinput, err := config.ReadSingOptions(ctx, &config.ReadOptions{Content: in.ConfigContent, Path: in.ConfigPath})\n"
assert new.count(before) == 1
candidate = new.replace(before, parser.replace(b"static.HiddifyOptions", b"hopt"), 1)
assert b"takeDelegatedSystemProxy(options)" in candidate
assert b"config.ApplyKeepTop(options, hopt, candidates)" in candidate
assert b"monitor.KeepTopSearching()" in candidate
resolutions[path] = subprocess.check_output(["gofmt"], input=candidate)

path = "v2/hcore/keep_top_test.go"
own = checked_blob(patched, path, "a740db84183773b590e799ca29b67aa88ea1be36")
new = checked_blob(upstream, path, "408f99d0ee39c8b185f9d292dee04485a01d508e")
# The old tests exercise private selection functions that no longer
# exist. Preserve the fork's independently useful parser regression.
regression = own[own.index(b"func TestBuildRunningConfigParsesImportedProfileFormats("):]
assert regression.count(b"built, err := buildRunningConfig(") == 1
regression = regression.replace(b"built, err := buildRunningConfig(", b"built, _, err := buildRunningConfig(", 1)
assert b"naive share URL" in regression and b"trojan share URL" in regression and b"sing-box JSON" in regression
marker = b'\t"github.com/sagernet/sing-box/common/monitoring"\n'
assert new.count(marker) == 1
candidate = new.replace(marker, marker + b'\t"github.com/sagernet/sing-box/experimental/libbox"\n', 1)
candidate += b"\n" + regression
resolutions[path] = subprocess.check_output(["gofmt"], input=candidate)

# Hot reload: keep the fork's protected BaseContext read and the upstream
# delegated system-proxy bookkeeping. Capture the service pointer once,
# instead of looking it up again during validation and application.
path = "v2/hcore/hotreload.go"
own = checked_blob(patched, path, "1a674d4334baef6056b3bf6228f2dcf1709e109f")
new = checked_blob(upstream, path, "b57ba117624e3e4b7f852c0ce382da69039037e1")
guard = b"\tstatic.stateLock.RLock()\n\tbaseCtx := static.BaseContext\n\tstatic.stateLock.RUnlock()\n\treturn HotReload(baseCtx, in)"
assert guard in own
before = b"\treturn HotReload(static.BaseContext, in)"
assert new.count(before) == 1
candidate = new.replace(before, guard, 1)
before = b"\ttunChanged, err := static.startedService.Load().IsTunChanged(ctx, options)"
after = b"\tss := static.startedService.Load()\n\tif ss == nil {\n\t\treturn MessageType_HOT_RELOAD_FAILED, errors.New(\"no running service to hot reload\")\n\t}\n\ttunChanged, err := ss.IsTunChanged(ctx, options)"
assert candidate.count(before) == 1
candidate = candidate.replace(before, after, 1)
before = b"static.startedService.Load().HotReload(serviceContext(ctx), options)"
assert candidate.count(before) == 1
candidate = candidate.replace(before, b"ss.HotReload(serviceContext(ctx), options)", 1)
for marker in (b"libbox.CheckConfigOptions(options)", b"engineApplying(options)",
               b"engineApplied(err == nil)", b"recordDelegatedSystemProxy(delegatedProxy)",
               b"rememberProxyDetails(options)", b"errTunChanged"):
    assert marker in candidate
resolutions[path] = subprocess.check_output(["gofmt"], input=candidate)

# Connectivity's clean textual merge may retain the old watcher call,
# which would invoke the removed reload-driven KeepTop implementation.
# The two upstream/fork versions have the same updateConnectivity return
# contract. Remove only that exact obsolete call block, if present.
path = "v2/hcore/connectivity.go"
own = checked_blob(patched, path, "de989529def18c500af84a1f48d1327f18bf87f2")
new = checked_blob(upstream, path, "07a01b8967eef986d6dbb4916076e49dcba9fd37")
old_call = b"\t\tif state, ok := h.updateConnectivity(ctx); ok {\n\t\t\th.followKeepTop(state)\n\t\t}\n"
new_call = b"\t\th.updateConnectivity(ctx)\n"
assert own.count(old_call) == 1 and new.count(new_call) == 1
clean = git("show", data["tentative_tree"] + ":" + path)
assert clean in (new, new.replace(new_call, old_call, 1)), path
core_clean = {path: new} if clean != new else {}
# Service lifecycle: retain the fork's settings and context locks, atomic
# debug flag and cancellation helpers, but publish the service atomically
# as upstream does. All direct StartedService callers must be migrated.
path = "v2/hcore/static_data.go"
own = checked_blob(patched, path, "b4e9d99d1fb67f20cc7387e5552eb6489bf1ddb7")
new = checked_blob(upstream, path, "e5256b3b1ef64095de599fe3bb5e3d3e255c38bc")
candidate = own
start = new.index(b"\t// startedService is the running service.")
end = new.index(b"\tHiddifyOptions", start)
assert candidate.count(b"\tStartedService *daemon.StartedService\n") == 1
candidate = candidate.replace(b"\tStartedService *daemon.StartedService\n", new[start:end], 1)
before = b"\tstatic.stateLock.Lock()\n\tstatic.StartedService = ss\n\tstatic.stateLock.Unlock()"
assert candidate.count(before) == 1
candidate = candidate.replace(before, b"\tstatic.startedService.Store(ss)", 1)
before = b"\tdebug                     atomic.Bool\n"
assert candidate.count(before) == 1
candidate = candidate.replace(before, before + b"\tsetupDebug bool // initial platform debug setting; guarded by the lifecycle lock\n\tprivateConfig bool // running profile must not be persisted; guarded by the lifecycle lock\n", 1)
resolutions[path] = subprocess.check_output(["gofmt"], input=candidate)

# Keep the fork's separate startup/lifetime contexts and shutdown semantics;
# add the upstream coherent running snapshot and optional stop channel API.
path = "v2/hcore/service.go"
own = checked_blob(patched, path, "e48b2cb9755826c6b395917f75b92023283723c1")
new = checked_blob(upstream, path, "9f2ebe8c243f00d7eeb5608e4355672fdf52c740")
before = b"func NewService(ctx context.Context, options option.Options) (*daemon.StartedService, error) {\n\treturn NewServiceWithStartupContext(ctx, ctx, options)\n}\nfunc NewServiceWithStartupContext(ctx, startupCtx context.Context, options option.Options) (*daemon.StartedService, error) {"
after = b"""// NewService retains two-argument callers and accepts the upstream optional stop channel.
func NewService(ctx context.Context, options option.Options, stop ...<-chan struct{}) (*daemon.StartedService, error) {
	if len(stop) > 1 {
		return nil, errors.New("multiple startup stop channels")
	}
	if len(stop) == 1 {
		return newService(ctx, ctx, options, stop[0])
	}
	return NewServiceWithStartupContext(ctx, ctx, options)
}

func NewServiceWithStartupContext(ctx, startupCtx context.Context, options option.Options) (*daemon.StartedService, error) {
	return newService(ctx, startupCtx, options, stopSignal(startupCtx))
}

func newService(ctx, startupCtx context.Context, options option.Options, stop <-chan struct{}) (*daemon.StartedService, error) {"""
assert own.count(before) == 1
candidate = own.replace(before, after, 1)
before = b"\tif stop := stopSignal(startupCtx); stop != nil {"
assert candidate.count(before) == 1
candidate = candidate.replace(before, b"\tif stop != nil {", 1)
before = b"\th.stateLock.RLock()\n\tss := h.StartedService\n\th.stateLock.RUnlock()"
assert candidate.count(before) == 1
candidate = candidate.replace(before, b"\tss := h.startedService.Load()", 1)
start = new.index(b"// running is the box and context")
end = new.index(b"func (h *HiddifyInstance) Instance()", start)
before = b"func (h *HiddifyInstance) Instance()"
assert candidate.count(before) == 1
candidate = candidate.replace(before, new[start:end] + before, 1)
assert b"StartOrReloadService(startupCtx," in candidate
assert b"errors.Is(err, os.ErrClosed)" in candidate
resolutions[path] = subprocess.check_output(["gofmt"], input=candidate)

# Start: use the actual patched tree, including the later path portability
# patch. Keep its path traversal safeguards and the native no-persistence API.
path = "v2/hcore/start.go"
own = checked_blob(patched, path, "a4a0d026d716c4c56197c5630da8ac05e9d687ee")
new = checked_blob(upstream, path, "f31f60ecaa3d414a6c5baf58649543e7ca4ab061")
candidate = new
def lifecycle_edit(before, after, count=1):
    global candidate
    assert candidate.count(before) == count, (path, before)
    candidate = candidate.replace(before, after)

def lifecycle_section(blob, start, end):
    i = blob.index(start)
    return blob[i:blob.index(end, i)]

start, end = b"// workingRelative", b"func loadLastStartRequestIfNeeded("
lifecycle_edit(lifecycle_section(new, start, end), lifecycle_section(own, start, end))
assert b"filepath.IsLocal(relative)" in candidate
assert b"lastStartRequestDisableMemoryLimit" in candidate
start, end = b"// noConfigPersistenceKey", b"// startServiceLocked"
lifecycle_edit(end, lifecycle_section(own, start, end) + end)
lifecycle_edit(b"\tqueueStartLocked(static.BaseContext, in)", b"\tstatic.stateLock.RLock()\n\tbaseCtx := static.BaseContext\n\tstatic.stateLock.RUnlock()\n\tqueueStartLocked(baseCtx, in)")
lifecycle_edit(b"\tsaveLastStartRequest(in)\n", b"\tprivateConfig := ctx.Value(noConfigPersistenceKey{}) != nil\n\tif !privateConfig {\n\t\tsaveLastStartRequest(in)\n\t}\n")
# The retry path must obey the same policy as the first attempt.
for indent in (b"\t", b"\t\t\t\t"):
    lifecycle_edit(b"\n" + indent + b"config.SaveCurrentConfig(ctx, currentBuildConfigPath, *options)\n",
                   b"\n" + indent + b"if !privateConfig {\n" + indent + b"\tconfig.SaveCurrentConfig(ctx, currentBuildConfigPath, *options)\n" + indent + b"}\n")
lifecycle_edit(b"\tif static.debug {", b"\tif static.debug.Load() && !privateConfig {", 2)
lifecycle_edit(b"NewService(serviceContext(ctx), *options, stopSignal(ctx))",
               b"NewServiceWithStartupContext(serviceContext(ctx), ctx, *options)", 2)
lifecycle_edit(b"\tstatic.startedService.Store(instance)\n", b"\tstatic.privateConfig = privateConfig\n\tstatic.startedService.Store(instance)\n")
# Private profiles may still report their names in memory, but not store
# their raw config or print its credentials into the debug log.
lifecycle_edit(b"\tengineApplied(true)\n", b"\tif privateConfig {\n\t\tcurrentProfileName.Store(in.ConfigName)\n\t}\n\tengineApplied(true)\n")
assert b"config.DropAutoRedirect(options)" in candidate
assert b"config.RouterModeError(err)" in candidate
assert b"restoreRouterHost()" in candidate
resolutions[path] = subprocess.check_output(["gofmt"], input=candidate)

# Stop merged textually; check both exact source versions and preserve
# upstream's atomic unpublication and router cleanup, clearing the
# private-profile flag only after the watcher is stopped.
path = "v2/hcore/stop.go"
own = checked_blob(patched, path, "7dcead91c7c1d157b6cbd0b755010494565b2140")
new = checked_blob(upstream, path, "960f4ba3151c97978ad9a0564dbfdab03633f2ea")
assert b"cacheSpeedTests()" in own and b"cacheSpeedTests()" in new
assert git("show", data["tentative_tree"] + ":" + path) == new, path
before = b"\tss := static.startedService.Swap(nil)\n"
assert new.count(before) == 1
core_clean[path] = new.replace(before, before + b"\tstatic.privateConfig = false\n", 1)

# Hot reload must not write a native private profile to disk, even when
# the reload caller does not carry the original startup context.
path = "v2/hcore/hotreload.go"
candidate = resolutions[path]
before = b'\tsaveLastStartRequest(in)\n\tconfig.SaveCurrentConfig(ctx, filepath.Join(sWorkingPath, "data/current-config.json"), *options)'
assert candidate.count(before) == 1
candidate = candidate.replace(before, b'''\tif static.privateConfig || ctx.Value(noConfigPersistenceKey{}) != nil {
		static.privateConfig = true
		currentProfileName.Store(in.ConfigName)
	} else {
		saveLastStartRequest(in)
		config.SaveCurrentConfig(ctx, filepath.Join(sWorkingPath, "data/current-config.json"), *options)
	}''', 1)
resolutions[path] = subprocess.check_output(["gofmt"], input=candidate)

# New clean log-level files need the retained atomic debug field.
for path, sha, edits in (
    ("v2/hcore/log_level_until.go", "d6acf4941219c4e0e7d83427fed31789b2494454", (
        (b"static.debug = static.setupDebug || level <= LogLevel_DEBUG", b"static.debug.Store(static.setupDebug || level <= LogLevel_DEBUG)"),
    )),
    ("v2/hcore/log_level_until_test.go", "8b50e82a959c1bfc73ef99d5c0ba5f07b82775da", (
        (b"require.True(t, static.debug)", b"require.True(t, static.debug.Load())"),
        (b"require.Equal(t, static.setupDebug, static.debug)", b"require.Equal(t, static.setupDebug, static.debug.Load())"),
    )),
):
    new = checked_blob(upstream, path, sha)
    assert git("show", data["tentative_tree"] + ":" + path) == new, path
    candidate = new
    for before, after in edits:
        lifecycle_edit(before, after)
    core_clean[path] = subprocess.check_output(["gofmt"], input=candidate)

# Setup RPC: the upstream simplification preserves the fork's fix for
# successful calls returning a nil error rather than dereferencing it.
path = "v2/hcore/setup.go"
own = checked_blob(patched, path, "17730fe2995bbf5697554bff738028dff51370eb")
new = checked_blob(upstream, path, "a32ab723b79933127c47f1c6ddbbe671767c88a7")
assert b"if err := Setup(req, nil); err != nil {" in new
assert b"Message: err.Error()" in new
assert b"ResponseCode_OK" in new
resolutions[path] = new

# gRPC: retain the fork's serialization, IPv6-safe listener validation,
# local API secret and dynamic trust snapshots. Adopt upstream's cached
# server identity, proper certificate PEM parsing and platform memory hints.
path = "v2/hcore/grpc_server.go"
own = checked_blob(patched, path, "ac8841dc3a0b22109624daaeb89168b94f549a68")
new = checked_blob(upstream, path, "729a50159c039b5ae78fec0f49a2e018d02f7689")
candidate = own
lifecycle_edit(b'\t"encoding/pem"\n', b'\t"encoding/pem"\n\t"errors"\n')
lifecycle_edit(b'\t"os"\n', b'\t"os"\n\t"path/filepath"\n')
start, end = b"// goroutineSnapshotsOnce", b"// Setup sets the core up"
marker = b"func Setup(params *SetupRequest"
lifecycle_edit(marker, lifecycle_section(new, start, end) + marker)
lifecycle_edit(b"\tstatic.debug.Store(params.Debug)", b"\tstatic.setupDebug = params.Debug\n\tstatic.debug.Store(params.Debug)")
lifecycle_edit(b"\tbudget := int64(0)\n\tif C.IsAndroid {", b"\tbudget := deviceMemoryLimit\n\tif budget == 0 && C.IsAndroid {")
# Keep the actual libbox error check and the Chdir error check.
assert b"if err := libbox.Setup(" in candidate
assert b"if err := os.Chdir(sWorkingPath); err != nil {" in candidate
lifecycle_edit(b"\tsWorkingPath = params.WorkingDir\n", b"\tsWorkingPath = params.WorkingDir\n\tsSetupMode = params.Mode\n\tcleanupLeftovers()\n\tgoroutineSnapshotsOnce.Do(func() {\n\t\tkeepPreviousGoroutineSnapshot(sWorkingPath, sSetupMode)\n\t\tgo writeGoroutineSnapshots(static.BaseContext, sWorkingPath, sSetupMode)\n\t})\n")
# Preserve init-before-publish. Load a stored client certificate before
# the listener can begin accepting connections, while mu is held.
marker = b"\t\t_, err := startGrpcServerByModeLocked(params.Listen, params.Mode, params.Secret)"
lifecycle_edit(marker, b'\t\tif isMutualTLS(params.Mode) {\n\t\t\tif err := trustStoredClientCertificate(params.BasePath); err != nil {\n\t\t\t\tLog(LogLevel_WARNING, LogType_CORE, "no stored client certificate to trust: ", err.Error())\n\t\t\t}\n\t\t}\n' + marker)
start, end = b"\t\ttable := db.GetTable[hcommon.AppSettings]()\n\t\tLog(LogLevel_DEBUG, LogType_CORE, table)", b"\t\t// Load server certificate and private key"
lifecycle_edit(lifecycle_section(own, start, end), b"\t\tif err := loadServerCertificate(); err != nil {\n\t\t\treturn nil, err\n\t\t}\n")
lifecycle_edit(b"\t\t\tClientCAs:    caCertPool.Clone(),", b"\t\t\tMinVersion:   tls.VersionTLS12,\n\t\t\tClientCAs:    caCertPool.Clone(),")
# Validate the stored certificate/key pair before reuse. Do not rotate a
# pinned certificate merely because a database read failed.
loader = lifecycle_section(new, b"// loadServerCertificate", b"// GetGrpcServerPublicKey")
before = b"\t\t\tcertpair = &hutils.CertificatePair{Certificate: pub, PrivateKey: priv}\n\t\t\treturn nil"
assert loader.count(before) == 1
loader = loader.replace(before, b"\t\t\tif _, pairErr := tls.X509KeyPair(pub, priv); pairErr == nil {\n\t\t\t\tcertpair = &hutils.CertificatePair{Certificate: pub, PrivateKey: priv}\n\t\t\t\treturn nil\n\t\t\t}", 1)
marker = b"// GetGrpcServerPublicKey returns the gRPC server's public key."
lifecycle_edit(marker, loader + marker)
lifecycle_edit(lifecycle_section(own, b"// AddGrpcClientPublicKey", b"func CloseGrpcServer("),
               lifecycle_section(new, b"// AddGrpcClientPublicKey", b"func CloseGrpcServer("))
# No raw PUBLIC KEY is sufficient for TLS certificate authentication.
# Keep the fork's per-handshake clone and defensive certificate copy.
for marker in (b"localControlServerOptions(secret)", b'localControlServerOptions("")',
               b"net.SplitHostPort(listenAddressG)", b"tlsConfig.GetConfigForClient",
               b"return append([]byte(nil), certpair.Certificate...)",
               b"startGrpcServerByModeLocked", b"tls.RequireAndVerifyClientCert"):
    assert marker in candidate, marker
resolutions[path] = subprocess.check_output(["gofmt"], input=candidate)

# Commands: use one atomic service reference per status sample and retain
# the fork's traffic availability field. Keep upstream's targeted tests
# of parked outbounds; do not fall back to a stale name from the database.
path = "v2/hcore/commands.go"
own = checked_blob(patched, path, "55dc14d8312c093ab3d1e8236e91fac4253e319e")
new = checked_blob(upstream, path, "341fbd79b1d1a87b24607e5a50079297b20cb561")
candidate = new
lifecycle_edit(b'\t"github.com/hiddify/hiddify-core/v2/db"\n', b"")
before = b"\t\tmessage.DownlinkTotal = status.DownlinkTotal"
assert b"\t\tmessage.TrafficAvailable = status.TrafficAvailable" in own
lifecycle_edit(before, b"\t\tmessage.TrafficAvailable = status.TrafficAvailable\n" + before)
lifecycle_edit(b"\t\tif box := h.Box(); box != nil {", b"\t\tif ins := ss.Instance(); ins != nil && ins.Box() != nil {\n\t\t\tbox := ins.Box()")
start = b"\t\tif message.CurrentProfile == \"\" {"
end = b"\n\t}\n\n\treturn &message"
lifecycle_edit(lifecycle_section(candidate, start, end), b"")
assert b"monitoring.Get(ctx).TestOutbounds(in.Tags" in candidate
resolutions[path] = subprocess.check_output(["gofmt"], input=candidate)

# Error shutdown: publish STOPPED once, after closing the service.
# Also clear the private profile flag and restore router forwarding.
path = "v2/hcore/custom.go"
own = checked_blob(patched, path, "53507901f43f3abbaf1fe9ef86a72145c91f9f40")
new = checked_blob(upstream, path, "0a7adff2687c205ba3a2ab9005737405c615c522")
candidate = new
marker = b"\tresponse := SetCoreStatus(CoreStates_STOPPED, msgType, message)"
lifecycle_edit(marker, b"\trestoreRouterHost()\n\tstatic.privateConfig = false\n" + marker)
assert b"static.startedService.Swap(nil)" in candidate
assert b"return StopAndAlert(state, err.Error()), err" in candidate
resolutions[path] = subprocess.check_output(["gofmt"], input=candidate)

# Replay only the previously generated protobuf files from the verified
# historical checkpoint, never its unrelated implementation changes.
# Confirm both live source schemas are covered, including wire numbers,
# before accepting the generated source as a migration candidate.
import gzip
checkpoint = Path(__file__).resolve().parent.parent / "migration/core-4e5a9a3"
proto_paths = (
    ("v2/hcore/hcore.proto", "73d4d3690fc4eb33206ebbec688c204821914f08", "66aa500a330401ec67310a08f6b3ec4a193a279e", "6a4b20fb8720a92fc3ff8885a6a42a8e87e23a19"),
    ("v2/hcore/hcore_service.proto", "78d90f8255609b6899fc29666e0c508f08621617", "354056882ba9511437d02c52a9385f241981e76b", "54dddbbd07ddfeaef6efd6abe20002280f10f5fc"),
    ("v2/hcore/hcore.pb.go", "4327eb3c86601a4328136d107eb4653fac99f07b", "8b593ba3bcbaffea145fd61652cbb9191d4c0f4d", "a477ebb8d163ce29dad4249e712ff71f45fb1b14"),
    ("v2/hcore/hcore_service.pb.go", "a4399b6a160b771159b2359d720b870dd4b65135", "72ed2601b19d549490f319c7064cbd289b50f5c7", "a6d8338febb10c04c894318d9c73487f720b4b28"),
    ("v2/hcore/hcore_service_grpc.pb.go", "f3fa1fe7e074d4d644d99e554abc04f30b952a0e", "d9e611f652c3b0b6b7fd3d7c11b99c099e7a39de", "e04b363e7a04ca87da6eb0a8ead22e42c16471e9"),
)
with tempfile.TemporaryDirectory() as tmp:
    env = dict(os.environ, GIT_INDEX_FILE=str(Path(tmp) / "proto-index"))
    git("read-tree", upstream, env=env)
    git("apply", "--cached", *(f"--include={row[0]}" for row in proto_paths),
        payload=gzip.decompress((checkpoint / "core-resolutions.patch.gz").read_bytes()), env=env)
    proto_tree = git("write-tree", env=env).decode().strip()

def proto_inventory(blob):
    # These schemas have flat message, enum and service declarations;
    # reject unsupported nested blocks instead of silently overlooking them.
    clean = re.sub(rb"//[^\n]*", b"", blob)
    groups = {}
    for kind, name, body in re.findall(rb"\b(message|enum|service)\s+(\w+)\s*\{([^{}]*)\}", clean, re.S):
        key = (kind, name)
        assert key not in groups, key
        members = {}
        for stmt in body.split(b";"):
            stmt = b" ".join(stmt.split())
            if not stmt:
                continue
            if kind == b"service":
                match = re.fullmatch(rb"rpc\s+(\w+)\s*\(([^)]*)\)\s*returns\s*\(([^)]*)\)", stmt)
                assert match, stmt
                member, request, response = match.groups()
                value = (request.strip(), response.strip())
            else:
                match = re.fullmatch(rb'(.+?)\s*=\s*(\d+)(?:\s*\[\s*json_name\s*=\s*"([^"]+)"\s*\])?', stmt)
                assert match, stmt
                declaration, member, json_name = match.groups()
                # Preserve explicit JSON names as well as wire types and numbers.
                value = (re.sub(rb"\s*([<>,])\s*", rb"\1", declaration), json_name)
            assert member not in members, (key, member)
            members[member] = value
        groups[key] = members
    assert len(groups) == len(re.findall(rb"\b(?:message|enum|service)\s+\w+\s*\{", clean))
    return groups

for path, own_sha, new_sha, checkpoint_sha in proto_paths:
    own = checked_blob(patched, path, own_sha)
    new = checked_blob(upstream, path, new_sha)
    candidate = checked_blob(proto_tree, path, checkpoint_sha)
    if path.endswith(".proto"):
        fork_fields, upstream_fields, merged_fields = map(proto_inventory, (own, new, candidate))
        expected = {key: dict(fields) for key, fields in upstream_fields.items()}
        for key, fields in fork_fields.items():
            union = expected.setdefault(key, {})
            assert all(number not in union or union[number] == value for number, value in fields.items()), key
            union.update(fields)
        assert merged_fields == expected, (path, "checkpoint schema differs from current union")
    else:
        assert candidate.startswith(b"// Code generated by protoc-gen-go")
    if path in data["conflicts"]:
        resolutions[path] = candidate
    elif candidate != git("show", data["tentative_tree"] + ":" + path):
        core_clean[path] = candidate

# The fork's later admin-service patch introduced the same atomic liveness
# flag that upstream now uses. Keep the upstream reload/restart handling
# and delegated proxy status, verify the actual patched fork transitions.
path = "v2/hcore/adminbgservice/admin_bg_service.go"
own = checked_blob(patched, path, "87ea7c35563c14ba1e4838cef839804383e6f649")
new = checked_blob(upstream, path, "e9ef2f2c644cd63df68ea38a48c3d8ff8e5b80a8")
assert own.count(b"s.running.Store(true)") == 2
assert own.count(b"s.running.Store(false)") == 2
assert new.count(b"s.setRunning()") == 4
assert b"s.running.Load()" in own and b"s.running.Load()" in new
assert b"hcore.DelegatedSystemProxy()" in new
assert b"func (s *hiddifyAdminBG) Reload(" in new
assert b"func (s *hiddifyAdminBG) Restart(" in new
candidate = new
# Retain the old running engine if stopping it failed; do not immediately
# create another TUN on top of an unsuccessful teardown.
lifecycle_edit(b"\ts.closeRunning()\n\tif in.ServerPort", b"\tif err := s.closeRunning(); err != nil {\n\t\treturn &AdminBgResponse{Message: err.Error()}, err\n\t}\n\tif in.ServerPort")
lifecycle_edit(b"\ts.closeRunning()\n\toptions, err := fullCoreOptions(in)", b"\tif err := s.closeRunning(); err != nil {\n\t\treturn err\n\t}\n\toptions, err := fullCoreOptions(in)")
resolutions[path] = subprocess.check_output(["gofmt"], input=candidate)

path = "v2/hcore/adminbgservice/status_test.go"
own = checked_blob(patched, path, "95485351395e9a3df701f8ac8949e99b29d73b90")
new = checked_blob(upstream, path, "2e19101c834ddadef5e13e2856a7f9c8adfa0362")
# Both regressions matter: a real state transition and a direct atomic
# status read while the long-running operation lock is held.
assert b"initial status" in own and b"s.running.Store(true)" in own
assert b"s.setRunning()" in new
assert own[:own.index(b"func TestStatus")] == new[:new.index(b"// Status answers")]
candidate = new + b"\n" + own[own.index(b"func TestStatusAnswersDuringOperation("):]
resolutions[path] = subprocess.check_output(["gofmt"], input=candidate)

path = "v2/hcore/adminbg/adminbg.go"
own = checked_blob(patched, path, "58919b183ef5a6a494e9995112b9a1c929011f2c")
new = checked_blob(upstream, path, "2d8499a67f40f30f30ce40cbf74f08bb457a5aed")
# The newer implementation keeps the authenticated method allowlist
# and extends the privileged diagnostics argument guard.
assert lifecycle_section(own, b"func AllowCoreMethod(", b"// AllowCoreRequest") == lifecycle_section(new, b"func AllowCoreMethod(", b"// AllowCoreRequest")
assert b"len(diagnostics.GenericFiles) > 0" in new
assert b"settings[\"allow-connection-from-lan\"] = false" in new
assert b"func Reload(" in new
assert b"func Authenticate(" in new
resolutions[path] = new

# Diagnostics: the two implementations serve distinct RPCs. Retain the
# fork's bounded, cancellable probe and IP lookup alongside upstream's
# report export. Keep service and context from one running snapshot.
path = "v2/hcore/diagnostics.go"
own = checked_blob(patched, path, "f40b90bebfc4f0429046b828f2c1791d2634b9b3")
new = checked_blob(upstream, path, "000af39f5558c4ed3e618cdb9383353f32d80357")
candidate = new
lifecycle_edit(b'\t"io"\n', b'\t"io"\n\t"net"\n\t"net/http"\n\t"net/url"\n')
lifecycle_edit(b'\t"github.com/sagernet/sing-box/experimental/libbox"\n', b'\t"github.com/sagernet/sing-box/adapter"\n\t"github.com/sagernet/sing-box/experimental/libbox"\n\t"github.com/sagernet/sing-box/hiddify/ipinfo"\n\tM "github.com/sagernet/sing/common/metadata"\n\t"google.golang.org/grpc/codes"\n\t"google.golang.org/grpc/status"\n')
probe = own[own.index(b"func selectedOutbound("):]
assert all(name not in new for name in (b"func selectedOutbound(", b"func validateProbeRequest(", b"func readProbeDownload(", b"func (s *CoreService) ProbeConnection(", b"func (s *CoreService) GetCurrentIpInfo("))
candidate = candidate.rstrip() + b"\n\n" + probe
lifecycle_edit(b"\tif instance == nil {\n\t\treturn nil, nil, nil, status.Error(codes.FailedPrecondition, \"VPN is stopped\")\n\t}", b"\tif instance == nil || instance.Box() == nil || instance.Context() == nil {\n\t\treturn nil, nil, nil, status.Error(codes.FailedPrecondition, \"VPN is stopped\")\n\t}")
lifecycle_edit(b"slices.Contains(running, static.CoreState)", b"slices.Contains(running, currentCoreState())")
lifecycle_edit(b"static.CoreState.String()", b"currentCoreState().String()")
marker = b"\tpath, encrypted, err := exportDiagnostics(sWorkingPath, sTempPath, req)"
lifecycle_edit(marker, b'\tif req == nil {\n\t\treturn nil, status.Error(codes.InvalidArgument, "missing diagnostics request")\n\t}\n' + marker)
assert b"downloadProbeGate" in candidate and b"maxProbeDownload" in candidate
assert b"encryptDirectory(" in candidate and b"writeSensitiveFiles(" in candidate
resolutions[path] = subprocess.check_output(["gofmt"], input=candidate)

# Proxy info: retain the readiness-only fork interface, while adopting
# upstream's on-demand conversion, resting status and stream deduplication.
# Keep the fork's explicit extra-security entry in the full selector list.
path = "v2/hcore/proxy_info.go"
own = checked_blob(patched, path, "c0bee6976d82436e46c2d6d9901b83eb4e6ac326")
new = checked_blob(upstream, path, "40b419c46288ec610b681ebd038c1eaffe339dae")
candidate = new
lifecycle_edit(b"Status(detour.Status())", b"Status(adapter.OutboundStatus(detour))")
lifecycle_edit(b'\t"github.com/sagernet/sing-box/common/monitoring"\n', b'\t"github.com/sagernet/sing-box/common/monitoring"\n\tC "github.com/sagernet/sing-box/constant"\n')
lifecycle_edit(b"\t\tif it, loaded := box.Outbound().Outbound(tag); loaded { // an outbound, or an endpoint", b"\t\tit, loaded := box.Outbound().Outbound(tag)\n\t\tif !loaded {\n\t\t\tif endpoint, ok := box.Endpoint().Get(tag); ok {\n\t\t\t\tit, loaded = endpoint, true\n\t\t\t}\n\t\t}\n\t\tif loaded {")
# The same cached info may be used in several groups. Per-group fields
# must not mutate another group's selection or the cached chain display.
lifecycle_edit(b"\t\t\tpinfo.IsSelected = itemTag == selectedTag", b"\t\t\tpinfo = proto.Clone(pinfo).(*OutboundInfo)\n\t\t\tpinfo.IsSelected = itemTag == selectedTag")
marker = b"\t\tgroups.Items = append(groups.Items, &group)"
lifecycle_edit(marker, b'''		if group.Tag == config.OutboundSelectTag && !onlyGroupitems {
			entryInfos := make(map[string]*OutboundInfo)
			addEntry := func(tag string) {
				if config.IsChainExtraSecurityTag(tag) || tag == selectedTag {
					if info := infoOf(tag); info != nil {
						entryInfos[tag] = proto.Clone(info).(*OutboundInfo)
					}
				}
			}
			addEntry(selectedTag)
			for _, out := range box.Outbound().Outbounds() {
				addEntry(out.Tag())
			}
			for _, endpoint := range box.Endpoint().Endpoints() {
				addEntry(endpoint.Tag())
			}
			showEntryHop(&group, entryInfos, func(tag string) string {
				if out, ok := box.Outbound().Outbound(tag); ok {
					return C.ProxyDisplayName(out.Type())
				}
				if ep, ok := box.Endpoint().Get(tag); ok {
					return C.ProxyDisplayName(ep.Type())
				}
				return TrimTagName(tag)
			})
		}

''' + marker)
helper = lifecycle_section(own, b"// showEntryHop", b"func TrimTagName(")
lifecycle_edit(b"func TrimTagName(", helper + b"func TrimTagName(")
lifecycle_edit(b"if config.IsChainExtraSecurityTag(tag) && (entry == nil || tag < entry.Tag)", b"if candidate != nil && config.IsChainExtraSecurityTag(tag) && (entry == nil || tag < entry.Tag)")
assert b"proto.Equal(sent, info)" in candidate
assert b"monitor.KeepTopPicking()" in candidate
assert b"ev.Priority" in candidate
resolutions[path] = subprocess.check_output(["gofmt"], input=candidate)

path = "v2/hcore/proxy_info_test.go"
own = checked_blob(patched, path, "65dbd61400ee1a3d717c8149acd96860e98973f9")
new = checked_blob(upstream, path, "78a372760890d41911b3b083a1c36325ec0f93e9")
# The fork's readiness method is the only source difference.
readiness = [line for line in own.splitlines(keepends=True) if b"func (g *fakeGroup) IsReady()" in line]
assert len(readiness) == 1 and own.replace(readiness[0], b"") == new
resolutions[path] = own

# Speed tests: avoid repeatedly measuring every member of auto-selected
# groups, skip parked proxies for bulk tests, retain direct tagged tests.
path = "v2/hcore/speedtest.go"
own = checked_blob(patched, path, "90017d4d1fcbfe6ad4507970f08e2334ecc8731a")
new = checked_blob(upstream, path, "3d5c8b7b499a8a61568c5c7ff848a6d1b25236a9")
candidate = new
assert b"box, ctx := h.running()" in candidate
assert b"speedTestTargets(outbound, box.Outbound().Outbound, proxyRests" in candidate
lifecycle_edit(b"if tag := member(group.Tag()); tag != group.Tag() {", b"if tag := member(group.Tag()); tag != group.Tag() && !rests(tag) {")
resolutions[path] = subprocess.check_output(["gofmt"], input=candidate)

path = "v2/hcore/speedtest_test.go"
own = checked_blob(patched, path, "e4a69d6444cbee5a533b93f6f9abccf8a287be56")
new = checked_blob(upstream, path, "64ce3047dbefeff62f3a57df20ebaadeba62545a")
marker = b"type speedGroup struct {"
assert marker in new and marker not in own
before = new[:new.index(marker)].rstrip()
# All old speed tests are retained; the only preceding edits are imports.
extra_imports = b'\n\t"github.com/sagernet/sing-box/adapter"\n\t"github.com/sagernet/sing-box/adapter/outbound"\n\tC "github.com/sagernet/sing-box/constant"\n\t"github.com/stretchr/testify/require"'
assert before.count(extra_imports) == 1
assert [line for line in before.replace(extra_imports, b"").splitlines() if line.strip()] == [line for line in own.splitlines() if line.strip()]
resolutions[path] = new

# Configuration RPCs: validate and persist a complete settings snapshot
# before publishing it. Retain the fork's import and GenerateConfig APIs.
# Adopt the upstream temporary log-level scheduler under the settings lock.
path = "v2/hcore/buildconfighelper.go"
own = checked_blob(patched, path, "2186d34c8f521269c57e2387d8c315cba14005db")
new = checked_blob(upstream, path, "a5fa106842f412a9c8257adba1564e58482ab316")
candidate = own
lifecycle_edit(b'\t\terr = recovered\n\t\tLog(LogLevel_ERROR, LogType_CONFIG, recovered.Error())\n\t})\n\n\tpath := in.TempPath', b'\t\tresponse = &ParseResponse{ResponseCode: hcommon.ResponseCode_FAILED, Message: recovered.Error()}\n\t\terr = recovered\n\t\tLog(LogLevel_ERROR, LogType_CONFIG, recovered.Error())\n\t})\n\n\tpath := in.TempPath')
lifecycle_edit(b'\t\terr = recovered\n\t\tLog(LogLevel_ERROR, LogType_CONFIG, recovered.Error())\n\t})\n\tconfig, err := config.ParseBuildConfigBytes', b'\t\tresponse, err = nil, recovered\n\t\tLog(LogLevel_ERROR, LogType_CONFIG, recovered.Error())\n\t})\n\tconfig, err := config.ParseBuildConfigBytes')
start, end = b"\tswitch next.LogLevel {", b"\n\treturn &CoreInfoResponse{}, nil"
lifecycle_edit(lifecycle_section(own, start, end), b"\tapplyLogLevel(next.LogLevel)\n\tscheduleLogLevelEnd(next)\n")
assert b"settingsSnapshot()" in candidate
assert b"if err := db.GetTable[hcommon.AppSettings]().UpdateInsert" in candidate
assert b"func (s *CoreService) GenerateConfig(" in candidate
resolutions[path] = subprocess.check_output(["gofmt"], input=candidate)

# Timer callbacks use the same lock ordering as ChangeHiddifySettings:
# settingsLock, then logLevelEnd. A replaced timer cannot change a newer
# profile. Snapshot reads never race the timer's option updates.
path = "v2/hcore/log_level_until.go"
candidate = core_clean[path]
lifecycle_edit(b"\t\tlogLevelEnd.Lock()\n\t\tdefer logLevelEnd.Unlock()\n", b"\t\tstatic.settingsLock.Lock()\n\t\tdefer static.settingsLock.Unlock()\n\t\tlogLevelEnd.Lock()\n\t\tdefer logLevelEnd.Unlock()\n")
lifecycle_edit(b"\tstatic.debug.Store(static.setupDebug || level <= LogLevel_DEBUG)", b"\tstatic.stateLock.RLock()\n\tsetupDebug := static.setupDebug\n\tstatic.stateLock.RUnlock()\n\tstatic.debug.Store(setupDebug || level <= LogLevel_DEBUG)")
core_clean[path] = subprocess.check_output(["gofmt"], input=candidate)
path = "v2/hcore/grpc_server.go"
candidate = resolutions[path]
lifecycle_edit(b"\tstatic.stateLock.Unlock()\n\tstatic.setupDebug = params.Debug", b"\tstatic.setupDebug = params.Debug\n\tstatic.stateLock.Unlock()")
resolutions[path] = subprocess.check_output(["gofmt"], input=candidate)

# Mobile bindings: add device memory hints and a stopped callback but keep
# the fork's error-returning Close and native privacy methods.
path = "platform/mobile/mobile.go"
own = checked_blob(patched, path, "f429ba1aaaddad7574b77b9c2b6105a8e6bd8f1d")
new = checked_blob(upstream, path, "2d3c89fd3b502b713479f94e6516e4aca0a04cf4")
candidate = own
lifecycle_edit(b"\tMemoryLimit     int64\n", b"\t// MemoryLimit is the platform-provided memory budget in bytes.\n\tMemoryLimit int64\n\t// LowMemory requests the low-memory relay path.\n\tLowMemory bool\n")
lifecycle_edit(b"\treturn hcore.Setup(&hcore.SetupRequest{", b"\thcore.SetDeviceMemory(opt.MemoryLimit, opt.LowMemory)\n\treturn hcore.Setup(&hcore.SetupRequest{")
callback = lifecycle_section(new, b"// CoreStoppedListener", b"func Stop() error")
lifecycle_edit(b"func Stop() error", callback + b"func Stop() error")
assert b"func Close(mode int) error" in candidate
assert b"func ApplyRegionalPrivacy(" in candidate and b"func ApplyDevicePrivacy(" in candidate
resolutions[path] = subprocess.check_output(["gofmt"], input=candidate)

# Preserve Android's resolved chain-stage snapshot and legacy chain
# validation; adopt the new direct DNS resolver on detoured dialers.
# Retain normalization of the existing Psiphon region and pairing fields.
path = "v2/config/chain.go"
own = checked_blob(patched, path, "14b5a08f5c34ea791c5025a5c0f6f816bdf0e766")
new = checked_blob(upstream, path, "c3446c37ff45df63f6ef77dcd72c1966d6642e64")
candidate = new
marker = b"// setChainHop adds"
lifecycle_edit(marker, lifecycle_section(own, b"// Android sends both", marker) + marker)
lifecycle_edit(b"\tregion := psiphon.Region\n", b"\tregion := strings.ToUpper(strings.TrimSpace(psiphon.Region))\n")
lifecycle_edit(b'options := &option.PsiphonOutboundOptions{Config: "hiddify", EgressRegion: region}', b'options := &option.PsiphonOutboundOptions{Config: "hiddify", EgressRegion: region, ConduitPairingID: strings.TrimSpace(psiphon.ConduitPairingID)}')
assert b"dialer.DomainResolver = resolver" in candidate
assert b"opt.ChainStage != nil" in candidate
assert b"ChainSearchOptions" in candidate
resolutions[path] = subprocess.check_output(["gofmt"], input=candidate)

# DNS tests: adopt upstream's remote/direct transport ordering checks and
# keep the fork's dependency-order and no-duplicate member regressions.
path = "v2/config/dns_group_test.go"
own = checked_blob(patched, path, "5bae98e2465dc3c139849a70a47e610e16acd13a")
new = checked_blob(upstream, path, "1ef41ee72d057f4b005f837eadf418dc26ae9472")
candidate = new.rstrip() + b"\n\n" + own[own.index(b"// Remote DoH bootstrap"):].rstrip() + b"\n"
marker = b"\t\tfor i, member := range g.Servers {\n"
lifecycle_edit(marker, b"\t\tseen := make(map[string]bool)\n" + marker + b'\t\t\tif _, found := servers[member]; !found || seen[member] {\n\t\t\t\tt.Fatalf("%s: missing or duplicate member %q", tag, member)\n\t\t\t}\n\t\t\tseen[member] = true\n')
assert b"func TestDNSBootstrapResolverIsDeclaredFirst" in candidate
assert b"func TestBuildConfigRemoteDNSLocal" in candidate
assert b"func TestTCPFirstOrder" in candidate
resolutions[path] = subprocess.check_output(["gofmt"], input=candidate)

# Config builder: preserve all fork privacy and adaptive-network policies,
# serialized build snapshots and chain-stage handling. Integrate the
# upstream router validation, IPv6-only host adaptation, cache persistence
# and adaptive processor defaults without coupling monitoring to a public API.
path = "v2/config/builder.go"
own = checked_blob(patched, path, "58b252981b7d83a1c1f194be3b269990e3febb73")
new = checked_blob(upstream, path, "6a72bfe009c4065ae605ec22a35f364e3c080404")
candidate = own
marker = b"\tinput, err := ReadSingOptions(ctx, inputOpt)"
lifecycle_edit(marker, b'''	// Keep an explicit Android IPv4-only tunnel compatible with the platform
	// interface; the host adaptation applies to native desktop/router modes.
	ipv6Only := !C.IsAndroid && !C.IsIos && !hostHasIPv4()
	hopts = forIPv6OnlyHost(hopts, ipv6Only)
	hopts, err := forRouterMode(hopts)
	if err != nil {
		return nil, err
	}

''' + marker)
lifecycle_edit(b"\tapplyRegionalPrivacy(&options, hopts)\n", b"\tif ipv6Only {\n\t\tuseIPv6Resolvers(&options)\n\t}\n\tapplyRegionalPrivacy(&options, hopts)\n")
assert candidate.count(b"StoreFakeIP: hopt.EnableFakeDNS,") == 1
marker = b"\nfunc setLog(options *option.Options, opt *HiddifyOptions)"
procs = lifecycle_section(new, b"// setProcs lets", marker)
start = candidate.index(b"func setExperimental(")
end = candidate.index(marker, start)
section = candidate[start:end]
assert section.rstrip().endswith(b"}")
section = section.rstrip()[:-1] + b"\tsetProcs(options)\n}\n\n" + procs.rstrip() + b"\n"
candidate = candidate[:start] + section + candidate[end:]
lifecycle_edit(b"AutoRedirect: hopt.AutoRedirect && AutoRedirectAvailable(),", b"AutoRedirect: tunAutoRedirect(hopt),")
# Router mode must not silently turn off its required auto_redirect when
# the fork's per-device routing table is used.
lifecycle_edit(b"opts.AutoRedirect = hopt.WifiVPNSharing", b"opts.AutoRedirect = hopt.WifiVPNSharing || hopt.RouterMode")
# Native auto-routing should capture IPv6 even when DNS is IPv4-only;
# Android/iOS need the TUN addresses to match their platform VPN builder.
lifecycle_edit(b"\t\tif ipv6Enable {\n\t\t\topts.Address = append(opts.Address,", b"\t\tif ipv6Enable || (!C.IsAndroid && !C.IsIos && shouldEnableIPv6()) {\n\t\t\topts.Address = append(opts.Address,")
# The retained sing-box fork uses []string IPCIDR in its raw rule API.
assert b"IPCIDR: []string{" in candidate
for marker in (b"buildConfigMu.Lock()", b"normalizeAppChain(hopts)", b"applyChainStage(ctx,", b"applyHandbookRouting(ctx,", b"applyRegionalPrivacy(&options,", b"hopts.AdaptiveNetwork", b"opts.ExcludeUID", b"proxyListeners(hopt, ipv6Enable)"):
    assert marker in candidate, marker
resolutions[path] = subprocess.check_output(["gofmt"], input=candidate)

# Update the older regression to distinguish the explicit mobile
# interface contract from native desktop IPv6 route capture. The actual
# platform VPN builder is still covered by its own integration tests.
path = "v2/config/builder_tun_ipv6_test.go"
own = checked_blob(patched, path, "123f18989a3d3e04d5a860d725b4dda7132013cb")
assert git("show", data["tentative_tree"] + ":" + path) == own
candidate = own
lifecycle_edit(b"func TestTunIPv4OnlyDoesNotConfigureIPv6WithoutRoot(t *testing.T)", b"func TestTunIPv4OnlyPlatformAddressPolicy(t *testing.T)")
lifecycle_edit(b"\t\tif len(tun.Address) != 1 || !tun.Address[0].Addr().Is4() {\n\t\t\tt.Fatalf(\"IPv4-only mode must configure only an IPv4 TUN address, got %v\", tun.Address)\n\t\t}", b'''		want := 1
		if !C.IsAndroid && !C.IsIos && shouldEnableIPv6() {
			want = 2
		}
		if len(tun.Address) != want || !tun.Address[0].Addr().Is4() || (want == 2 && !tun.Address[1].Addr().Is6()) {
			t.Fatalf("platform TUN policy expected %d addresses, got %v", want, tun.Address)
		}''')
core_clean[path] = subprocess.check_output(["gofmt"], input=candidate)

# Build recipes: newer Windows launchers, Apple static Cronet linkage and
# Linux runpath fix; keep pinned generators, Android API/arch targets,
# native tests, web RPC build and non-mutating module preparation.
path = "Makefile"
own = checked_blob(patched, path, "ba7f7c5e72aba26f3e3c3e359c8a9c6117e23d56")
new = checked_blob(upstream, path, "be37336605dad69802464d86c7c1ed713e860a73")
candidate = new
marker = b"# Standalone CLI binary"
lifecycle_edit(marker, lifecycle_section(own, b"# Test native APIs without", marker) + marker)
lifecycle_edit(lifecycle_section(new, b"CRONET_DIR=./cronet", b"\nios-full:"),
               lifecycle_section(own, b"CRONET_DIR=./cronet", b"\nios-full:"))
lifecycle_edit(b"\tgo mod tidy\n", b"\tgo mod download\n")
# Keep the earlier filename for clients that have not migrated yet.
marker = b'\tif [ ! -f $(BINDIR)/$(LIBNAME).dll -o ! -f $(BINDIR)/$(WIN_CLINAME).exe'
lifecycle_edit(marker, b'\tcp $(BINDIR)/$(WIN_CLINAME).exe $(BINDIR)/$(CLINAME).exe\n' + marker)
candidate = candidate.rstrip() + b"\n\n" + own[own.index(b"# Tested generators:"):].rstrip() + b"\n"
for marker in (b"ANDROID_API ?= 24", b"android-arm64:", b"PROTOC_GEN_GO_VERSION=v1.36.12", b"web-rpc:", b"IOS_ADD_TAGS=with_dhcp,with_low_memory\n", b"DIAGNOSTICS_PUBLIC_KEY", b"-L./lib -l:$(LIBNAME).so"):
    assert marker in candidate, marker
resolutions[path] = candidate

# Generated RPC docs: upstream retains all existing VetrOFF RPC names and
# adds new fields. Verify every prior table entry by its section and name
# rather than requiring unchanged descriptions or table layout.
path = "docs/hiddifyrpc.md"
own = checked_blob(patched, path, "ac81a944e7c60bb187fdfaee50018513f45a540a")
new = checked_blob(upstream, path, "76eecbda28879d20753edaa7d9fc7a5fcf818885")
def rpc_doc_members(blob):
    section = b""
    rows = set()
    for line in blob.splitlines():
        if line.startswith(b'<a name="'):
            section = line
        if line.startswith(b"| ") and b" |" in line:
            name = line.split(b"|")[1].strip()
            if name and name not in (b"Field", b"Name", b"Method", b"-----", b"----"):
                rows.add((section, name))
    return rows
assert rpc_doc_members(own) <= rpc_doc_members(new), sorted(rpc_doc_members(own) - rpc_doc_members(new))
resolutions[path] = new

# go.mod intentionally remains open: the existing guarded dependency
# candidate is valid syntactically but its combined build is not verified.
apply_reviewed(data, resolutions, core_clean)

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
# Start and Close share a lifecycle lock, separate from device stateAccess.
# Start publishes the bind and pause registration before Close reads them.
# Closing the done channel first rejects new work; queued writes cannot
# block Start on the detour (the reviewed ClientBind uses sendqueue).
candidate = replace_once(candidate, b"\tstateAccess    sync.Mutex\n",
    b"\tstateAccess    sync.Mutex\n\tlifecycleAccess sync.Mutex\n")
candidate = replace_once(candidate, b"func (e *Endpoint) Start(postStart bool) error {\n",
    b"func (e *Endpoint) Start(postStart bool) error {\n\te.lifecycleAccess.Lock()\n\tdefer e.lifecycleAccess.Unlock()\n\tif isDone(e.done) {\n\t\treturn net.ErrClosed\n\t}\n\tif e.device.Load() != nil {\n\t\treturn nil\n\t}\n")
# Publish the pause manager once at construction, before any callbacks or
# data-plane goroutines can observe it. Only registration belongs in Start.
candidate = replace_once(candidate, b"\t\tpauseUpdated:   make(chan struct{}),",
    b"\t\tpause:          service.FromContext[pause.Manager](options.Context),\n\t\tpauseUpdated:   make(chan struct{}),")
candidate = replace_once(candidate, b"\te.pause = service.FromContext[pause.Manager](e.options.Context)\n", b"")
candidate = replace_once(candidate, b"\t\tclose(e.done)\n",
    b"\t\tclose(e.done)\n\t\te.lifecycleAccess.Lock()\n\t\tdefer e.lifecycleAccess.Unlock()\n")
# Do not leak the private key or PSKs if device configuration fails.
candidate = replace_once(candidate, b'E.Cause(err, "setup wireguard: \\n", ipcConf.String())',
    b'E.Cause(err, "setup wireguard")')
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
# The fork deliberately retains the more precise interface-address diagnostic.
assert candidate.count(b"no interface address") == 2
candidate = candidate.replace(b"no interface address", b"WARP interface is missing IPv4 and IPv6 addresses")
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


# These transport files have identical code and comments after normalizing
# only the exact marker spelling. Preserve the fork's comment formatting.
for path, hashes in (
    ("transport/call/common/http.go", ("f0d8ffdb036e3cde50bd30d91dbaba5fd1509c94", "8a6fddccc79264600afade85217a37ab99571ce2", "242820077e154c4e91780ad694b32b8c43afb170")),
    ("transport/call/vk/vk_auth.go", ("029bed0d3f801e9439344a032210955aebf3ed18", "1ee660dbc7cb72e33f5401627d6a8227f36769d3", "f209c216fdd92e416fc734218cb2b12d3aac9f38")),
    ("transport/simple-obfs/http.go", ("df38768ec53276505aebc5785c1986bc16a0be5f", "1c70b20a847e7408ec27e109bdeab5e97517ad88", "e0094eadb8bdfc33b075e2e1b88bdd7e04248835")),
    ("transport/simple-obfs/server.go", ("7cf5dfc21d6a3608f13c5722b5e974861e301a04", "cab57217e1e9d39db686d9c4a70c7db2bc3db6fb", "aa17f1b3c12ffc3c90786d66c6fe282b8c776cca")),
    ("transport/v2ray/transport.go", ("7c9a5a4379267fbeef461b99ec4017db1fb92fba", "b9bfdb20ade197227fb22f32163c83b5f2e6e991", "9a0d9303ddce6fbfb5723fc5d107b61cd8a5f7de")),
):
    old, own, new = (checked_blob(data[rev], path, sha) for rev, sha in zip(
        ("base", "patched_tree", "upstream"), hashes))
    assert new.replace(b"//H", b"// H") == own, path
    resolutions[path] = own



# Restore upstream attribution without dropping the fork's compatibility
# notes. Soar differs only by the inserted credits; FinalMask also contains
# duplicated/formatting edits in the locked docs, which are intentionally
# preserved until the transport/configuration work is complete.
for path, old_sha, own_sha, new_sha in (
    ("docs/hiddify/features/protocols/soar.en.md", None, "a08cc6ad469af7e40d7b22e831e89e158ff580f2", "e66d26954579a0cc296a3d5e51a2e2564b541326"),
    ("docs/hiddify/features/protocols/soar.fa.md", None, "d73e9a6979e61ad5f961d867f3eb49ee142e4a6b", "41d951ff58763f7edeb0da915e26db9dead9d407"),
    ("docs/hiddify/features/transports/finalmask.en.md", "bbf58d6fdd3bb7dee4502f4e1098a2cd5569fc4a", "ee235870ed8f0117b5a50053749d5a387b5ecfef", "fdb06331c4852d3221510744ded2c0a58209234c"),
    ("docs/hiddify/features/transports/finalmask.fa.md", "e28458e8dbf948e5a91fa7f0105291c282f238b9", "882a8bae5da521af09c30d100f772444c0d12228", "aad9e5fba29dd3506e7a136df9dcb3aa8549e1eb"),
):
    if old_sha:
        checked_blob(data["base"], path, old_sha)
    else:
        assert not git("ls-tree", data["base"], "--", path)
    own = checked_blob(data["patched_tree"], path, own_sha)
    new = checked_blob(data["upstream"], path, new_sha)
    en = path.endswith(".en.md")
    credit = ("## Sources and credits" if en else "## منبع‌ها و قدردانی").encode()
    related = ("## See also" if en else "## همچنین ببینید").encode()
    assert own.count(credit) == 0 and new.count(credit) == 1
    i = new.index(credit)
    j = new.index(related, i)
    section = new[i:j]
    assert section.endswith(b"\n\n")
    if "/soar." in path:
        assert new[:i] + new[j:] == own
        resolutions[path] = new
    else:
        # The fork repeats the related section. Add credits before its
        # first occurrence only; every original byte remains recoverable.
        first = own.index(related)
        candidate = own[:first] + section + own[first:]
        assert candidate.replace(section, b"", 1) == own
        assert b"https://github.com/XTLS/Xray-core" in section
        assert b"https://github.com/patterniha/Xray-core" in section
        assert b"https://github.com/PoriyaVali/mirage" in section
        resolutions[path] = candidate


# Sequential DNS: retain the fork's explicit error_ttl compatibility
# contract, with upstream's bounded rating for configurations without it.
# The fork uses sequentialState and the IsReady/OutboundStatus contract.
path = "dns/transport/h_group_sequential.go"
old, own, new = (checked_blob(data[rev], path, sha) for rev, sha in zip(
    ("base", "patched_tree", "upstream"),
    ("c4fce524eccff02a8686ebd3ac78c38ab481d9e7",
     "e0efc7438418e6478a7f6fd5b9cf00dc6d8329d7",
     "089562c88c1e0cbe81ed9be8235ae8a2e1134c61")))
group = checked_blob(data["tentative_tree"], "dns/transport/group.go",
                     "cdf49093836226c1af0903b58725ccf56bdb2b47")
assert b"sequentialState *groupSequential" in group
candidate = replace_once(own, b'\t"github.com/sagernet/sing-box/option"\n',
    b'\t"github.com/sagernet/sing-box/hiddify/rating"\n\t"github.com/sagernet/sing-box/option"\n')
candidate = replace_once(candidate, b'\tE "github.com/sagernet/sing/common/exceptions"\n',
    b'\tE "github.com/sagernet/sing/common/exceptions"\n\tN "github.com/sagernet/sing/common/network"\n\t"github.com/sagernet/sing/service"\n')
candidate = replace_once(candidate, b"\terrorTTL      time.Duration\n",
    b"\terrorTTL      time.Duration\n\tlegacyTTL     bool\n\tscores        rating.Table[string]\n")
candidate = replace_once(candidate, b"\t\terrorTTL:      time.Duration(options.ErrorTTL),",
    b"\t\terrorTTL:      time.Duration(options.ErrorTTL),\n\t\tlegacyTTL:     options.ErrorTTL > 0,")
candidate = replace_once(candidate, b"func (s *groupSequential) reset() {\n",
    b"func (s *groupSequential) reset() {\n\ts.scores.Reset()\n")
candidate = replace_once(candidate, b"func (s *groupSequential) order(tags []string) []string {\n",
    b"func (s *groupSequential) order(tags []string) []string {\n\tif !s.legacyTTL {\n\t\treturn s.scores.Sort(tags)\n\t}\n")
candidate = replace_once(candidate, b"func (s *groupSequential) record(tag string, outcome groupOutcome) {\n",
    b"func (s *groupSequential) record(tag string, outcome groupOutcome) {\n\tif !s.legacyTTL {\n\t\treturn\n\t}\n")
i = new.index(b"// detourReady reports")
j = new.index(b"// score is a server", i)
detour = new[i:j]
detour = replace_once(detour, b"\tstatus, _ := adapter.PathStatus(adapter.GroupPath(outbound, network))\n\treturn status == adapter.StatusReady",
    b"\tfor depth := 0; depth <= 16; depth++ {\n\t\tif adapter.OutboundStatus(outbound) != adapter.StatusReady {\n\t\t\treturn false\n\t\t}\n\t\tgroup, ok := outbound.(adapter.OutboundGroup)\n\t\tif !ok {\n\t\t\treturn true\n\t\t}\n\t\toutbound = group.Selected(network)\n\t\tif outbound == nil {\n\t\t\treturn true\n\t\t}\n\t}\n\treturn false // a cycle or excessively deep group cannot be considered ready")
settle = b'''// settle applies the current upstream rating only when some server answered.
func (s *groupSequential) settle(answered string, failed []string) {
	if !s.legacyTTL && answered != "" {
		s.scores.Settle(answered, failed)
	}
}

func (s *groupSequential) score(tag string) int {
	return int(s.scores.Score(tag))
}

'''
marker = b"// budget is how long"
candidate = replace_once(candidate, marker, settle + detour + marker)
start = new.index(b"func (t *GroupTransport) exchangeSequential(")
exchange = new[start:].replace(b"t.sequential.", b"t.sequentialState.")
exchange = replace_once(exchange, b"\t\t\tt.sequentialState.record(tag, failed)",
    b"\t\t\tt.sequentialState.record(tag, groupAccepted)\n\t\t\tt.sequentialState.settle(tag, failed)")
exchange = replace_once(exchange, b"\t\t\t\tfailed = append(failed, tag)",
    b"\t\t\t\tt.sequentialState.record(tag, groupFailed)\n\t\t\t\tfailed = append(failed, tag)")
# Return cancellation before consulting detour status.
exchange = replace_once(exchange, b"\tcollector := groupCollector{transport: t}\n",
    b"\tcollector := groupCollector{transport: t}\n\tif ctx.Err() != nil {\n\t\tt.finishCanceled(ctx, &collector, callback)\n\t\treturn\n\t}\n")
candidate = candidate[:candidate.index(b"func (t *GroupTransport) exchangeSequential(")] + exchange
resolutions[path] = subprocess.check_output(["gofmt"], input=candidate)

# Keep every existing fork test and append the upstream rating/detour
# tests, adapting only the private sequentialState field and method names.
path = "dns/transport/h_group_test.go"
old, own, new = (checked_blob(data[rev], path, sha) for rev, sha in zip(
    ("base", "patched_tree", "upstream"),
    ("ca7d588a2655c4e32e367f2332e44289c10d3125",
     "1a8fe434d1b595a7ec35c40ec093115ad0003e2c",
     "ef300efa9fef48a66115455d38b7bbf79d665ad2")))
i = new.index(b"// References is the member's detour")
j = new.index(b"func (m *hGroupMember) Tag()", i)
helpers = new[i:j]
candidate = replace_once(own, b"\texchange func(ctx context.Context, message *mDNS.Msg) (*mDNS.Msg, error)\n}",
    b"\texchange func(ctx context.Context, message *mDNS.Msg) (*mDNS.Msg, error)\n\tdetour string\n}")
marker = b"func (m *hGroupMember) Tag()"
candidate = replace_once(candidate, marker, helpers + marker)
i = new.index(b"// A server that failed loses a point")
tests = new[i:].replace(b"group.sequential.", b"group.sequentialState.").replace(b"sequential.record(", b"sequential.settle(").replace(b"hErrMember", b"errHMember")
assert b"func TestH_GroupSequentialSharesTheDeadline(" in tests
assert b"func TestH_GroupSequentialScoresOrderTheServers(" not in own
resolutions[path] = subprocess.check_output(["gofmt"], input=candidate.rstrip() + b"\n\n" + tests)


# MasterDNS's plain DNS pretest is insufficient: verify both small tunnel
# MTU probes before admitting a resolver. Both sides added the package.
# Keep the fork's imports and exact remaining behavior.
path = "protocol/hiddify/masterdns/outbound.go"
assert not git("ls-tree", data["base"], "--", path)
own = checked_blob(data["patched_tree"], path, "0aecc9b3179aa130d0aada3f524b2e45f1633c5f")
new = checked_blob(data["upstream"], path, "695de0b8b1f346be79119a2fba18cca3656edfe3")
candidate = replace_once(own, b'\tmdns "masterdnsvpn-go/lib"\n',
    b'\tmDNS "github.com/miekg/dns"\n\tmdns "masterdnsvpn-go/lib"\n')
candidate = replace_once(candidate, b"\t\t\treturn dnspool.CheckAnswer(address, response, err)",
    b"\t\t\tif err := dnspool.CheckAnswer(address, response, err); err != nil {\n\t\t\t\treturn err\n\t\t\t}\n\t\t\treturn h.probeTunnel(ctx, address)")
i = new.index(b"// probeTunnel sends")
j = new.index(b"// orDefault", i)
candidate = replace_once(candidate, b"// orDefault", new[i:j] + b"// orDefault")
# Check all non-import bytes against the exact reviewed upstream version.
assert candidate[candidate.index(b"\nconst ("):] == new[new.index(b"\nconst ("):]
resolutions[path] = subprocess.check_output(["gofmt"], input=candidate)

path = "protocol/hiddify/masterdns/h_masterdns_test.go"
assert not git("ls-tree", data["base"], "--", path)
own = checked_blob(data["patched_tree"], path, "3e7b8de28dcf3d60addd08a5f7cbf4073bc55a95")
new = checked_blob(data["upstream"], path, "2498946d59d66c355c56249e7e5ac525977ece6d")
candidate = replace_once(own, b'\t"net"\n', b'\t"net"\n\t"strings"\n')
candidate = replace_once(candidate, b'\t"github.com/stretchr/testify/require"\n',
    b'\t"github.com/stretchr/testify/require"\n\t"masterdnsvpn-go/lib/testserver"\n')
i = new.index(b"// startTunnelResolver")
j = new.index(b"// With smart_pool", i)
candidate = replace_once(candidate, b"// With smart_pool", new[i:j] + b"// With smart_pool")
candidate = replace_once(candidate, b"// and after start the resolvers that answer the probe join the pool.",
    b"// and after start the resolvers that answer the probe and carry the tunnel join the pool.")
candidate = replace_once(candidate, b"\tanswering := startLocalDNS(t)",
    b"\tanswering, key := startTunnelResolver(t, validOptions().Domains[0], true)")
i = candidate.index(b"func TestH_MasterDNSSmartPool(t ")
candidate = candidate[:i] + replace_once(candidate[i:], b"\toptions := validOptions()\n",
    b"\toptions := validOptions()\n\toptions.Key = key\n")
assert candidate[candidate.index(b"\nfunc validOptions()"):] == new[new.index(b"\nfunc validOptions()"):]
resolutions[path] = subprocess.check_output(["gofmt"], input=candidate)


# WARP host recognition: upstream expands the endpoint classification.
# Confirm the fork's previously recognized ranges remain covered and no
# other code changed. This does not change the scanner's target list.
path = "common/cloudflare/h_warp_detect.go"
own, new = added_pair(path, "70c79b71a67859c5b7a2a63ba21390b3021c9a3a",
                      "133524d1ae2fd45bb6f2f2e169a3cbcb5bf9f19a")
import ipaddress
import re
def warp_networks(blob):
    return [ipaddress.ip_network(prefix.decode()) for prefix in
            re.findall(rb'netip.MustParsePrefix\("([^"]+)"\)', blob)]
old_ranges, new_ranges = warp_networks(own), warp_networks(new)
assert len(old_ranges) == 10 and len(new_ranges) == 15
assert all(any(a.version == b.version and a.subnet_of(b) for b in new_ranges) for a in old_ranges)
marker = b"// IsWARPAddress reports"
assert own[own.index(marker):] == new[new.index(marker):]
resolutions[path] = new

# Old warpscan implementation moved to edgescan. The only fork-specific
# change was the WaitGroup.Go refactor and gofmt. Do not retain a second
# Scan/Request/Result implementation alongside upstream's aliases.
path = "hiddify/warpscan/scanner.go"
old = checked_blob(data["base"], path, "5fcbf86aaaac5baf697729f1429e2c5c1c07566c")
own = checked_blob(data["patched_tree"], path, "53e11750b43d91d85fbac0cf5dc6eff9dc1cceff")
assert not git("ls-tree", data["upstream"], "--", path)
candidate = replace_once(old, b"\t\twait.Add(1)\n\t\tgo func() {\n\t\t\tdefer wait.Done()\n",
    b"\t\twait.Go(func() {\n")
candidate = replace_once(candidate, b"\t\t}()\n\t}\nfeed:", b"\t\t})\n\t}\nfeed:")
assert subprocess.check_output(["gofmt"], input=candidate) == subprocess.check_output(["gofmt"], input=own)
engine = checked_blob(data["upstream"], "hiddify/warpscan/h_engine.go", "35885c10667a93d21064eabf602455c29cd79263")
shared = checked_blob(data["upstream"], "hiddify/edgescan/scanner.go", "801388a62453ad0c295957139a5afe1b018cffb9")
assert b"return edgescan.Scan(ctx, logger, request)" in engine
assert b"wait.Add(1)" in shared and b"defer wait.Done()" in shared
resolutions[path] = None


# MASQUE: retain the locked peer metadata API, but adopt the new scanner
# lifecycle and IP-only packet writer. All other differences were marker
# formatting; the exact fork body is verified before migration.
path = "protocol/masque/outbound.go"
old, own, new = (checked_blob(data[rev], path, sha) for rev, sha in zip(
    ("base", "patched_tree", "upstream"),
    ("4dd454d30afcec8fd0cca20627cc45230b894bdb",
     "c486e4016f816bf6b826e761e5a13a4f39f6962b",
     "59b6993d5bb8fa1157605f800e00a27741796f11")))
candidate = replace_once(new, b"peer.IsFqdn()", b"peer.IsDomain()")
plain = replace_once(candidate, b'\t"github.com/sagernet/sing-box/common/iponly" //H\n', b"")
plain = replace_once(plain, b"\t\tif scanner != nil { //H\n\t\t\tscanner.Close() //H\n\t\t} //H\n", b"")
plain = replace_once(plain, b"\t\tw.tunnelAccess.Lock() //H\n\t\tw.scanner = scanner\n\t\tw.tunnelAccess.Unlock() //H\n", b"\t\tw.scanner = scanner\n")
plain = replace_once(plain, b"\n\tif scanner := w.currentScanner(); scanner != nil { //H: its searches end with it\n\t\tscanner.Close() //H\n\t} //H\n", b"")
signature = b"func (w *Outbound) ListenPacketWithDestination("
plain = replace_once(plain, function_blob(plain, signature), function_blob(own, signature))
plain = replace_once(plain, b"\t\tif scanner := w.currentScanner(); scanner != nil { //H\n\t\t\tscanner.DetectNetwork(true)",
    b"\t\tif w.scanner != nil {\n\t\t\tw.scanner.DetectNetwork()")
def normalized_masque(blob):
    return subprocess.check_output(["gofmt"], input=blob.replace(b"//H", b"// H"))
assert normalized_masque(plain) == normalized_masque(own), path
assert b"iponly.NewPacketConn" in candidate
assert b"scanner.Close()" in candidate
resolutions[path] = normalized_masque(candidate)


# The MasterDNS resolver admission change passed its scoped race tests.
# Both translations only change the description of that check and add credits.
import difflib
for path, own_sha, new_sha, removed, inserted in (
    ("docs/hiddify/features/protocols/masterdns.en.md",
     "14f1cc2a27f3ed74ee068c1015b2b07f12a10972", "298d53a9445db0ac6c6dd9001e44b9a7fdaa61a1", 3, 6),
    ("docs/hiddify/features/protocols/masterdns.fa.md",
     "02a26996c195aecf5052155f0844cd34313daf1b", "99384c14168a6ba7795e171b1a1cb1fb56d26a97", 3, 5),
):
    own, new = added_pair(path, own_sha, new_sha)
    en = path.endswith(".en.md")
    credit = ("## Sources and credits" if en else "## منبع‌ها و قدردانی").encode()
    related = ("## See also" if en else "## همچنین ببینید").encode()
    i = new.index(credit)
    j = new.index(related, i)
    assert credit not in own and b"https://github.com/masterking32/MasterDnsVPN" in new[i:j]
    left, right = own.splitlines(), (new[:i] + new[j:]).splitlines()
    changes = [(a, b, c, d) for op, a, b, c, d in difflib.SequenceMatcher(None, left, right, autojunk=False).get_opcodes() if op != "equal"]
    assert len(changes) == 2, (path, changes)
    assert sum(b-a for a,b,c,d in changes) == removed
    assert sum(d-c for a,b,c,d in changes) == inserted
    assert all(b"pretest_" in b"\n".join(left[a:b]) for a,b,c,d in changes)
    resolutions[path] = new


# Smart DNS pool: the only upstream differences are source attribution.
for path, old_sha, own_sha, new_sha in (
    ("docs/hiddify/features/dns/smart-dns-pool.en.md", "491a4fec73ae2278bffe5e9dc10c41a1e8c589a6", "7f2b7fecccac88c7083568bb1da5d7dbd1e96363", "89040eab40dcac7d5e9ac5327594bbe87f038ec1"),
    ("docs/hiddify/features/dns/smart-dns-pool.fa.md", "7be6943aebd3da302fd7b63637263706be78006b", "1b6b8fd3bd7c89e08dd56970003c80ba83399d90", "a058a63a5d9914921ffa5d46920ad50052ce2b90"),
):
    old, own, new = (checked_blob(data[rev], path, sha) for rev, sha in zip(
        ("base", "patched_tree", "upstream"), (old_sha, own_sha, new_sha)))
    en = path.endswith(".en.md")
    credit = ("## Sources and credits" if en else "## منبع‌ها و قدردانی").encode()
    related = ("## See also" if en else "## همچنین ببینید").encode()
    i = new.index(credit)
    j = new.index(related, i)
    assert new[:i] + new[j:] == own
    resolutions[path] = new

# Clean upstream helper must use the fork's optional detailed status
# contract. Record the fix in the candidate tree, not only in a test.
path = "adapter/h_detour.go"
detour = checked_blob(data["tentative_tree"], path, "25ec7f984b22fa4fbe7f2e369d13f28a1e8af99f")
assert detour == checked_blob(data["upstream"], path, "25ec7f984b22fa4fbe7f2e369d13f28a1e8af99f")
detour = replace_once(detour, b"outbound.Status()", b"OutboundStatus(outbound)")
detour = subprocess.check_output(["gofmt"], input=detour)


# DNSTT: the upstream-only members and duration helper are unused by the
# reviewed package. Retain the fork's smaller structure and consistent
# receiver names; prove the remaining code is identical.
path = "protocol/hiddify/dnstt/outbound.go"
old, own, new = (checked_blob(data[rev], path, sha) for rev, sha in zip(
    ("base", "patched_tree", "upstream"),
    ("d8277dc1fad8d04f45677c3a49aac55e92ed6ee4",
     "b92057d822bc0c98d4c20dce7e9794dc52e653f5",
     "c4113dc4653e00c2904014c2f84a2b937900dcb6")))
plain = replace_once(new, b'\t"time"\n', b"")
plain = replace_once(plain, b'\t"github.com/sagernet/sing/common/json/badoption"\n', b"")
plain = replace_once(plain, function_blob(plain, b"func initDuration("), b"")
for field in (b"dnsRouter", b"resolve", b"tunnel_index"):
    plain, count = re.subn(rb"(?m)^\t" + field + rb"\s+[^\n]+\n", b"", plain)
    assert count == 1, field
plain = re.sub(rb"\b(?:c|w)\.", b"h.", plain.replace(b"(c *Outbound)", b"(h *Outbound)").replace(b"(w *Outbound)", b"(h *Outbound)"))
def dnstt_code(blob):
    return b"".join(normalize_imports(blob).split())
assert dnstt_code(plain) == dnstt_code(own), path
for name in ("dnstt_func.go", "tester.go", "tools.go", "h_dnstt_test.go", "h_registry_race_test.go"):
    other = git("show", data["upstream"] + ":protocol/hiddify/dnstt/" + name)
    code = b"\n".join(l.split(b"//", 1)[0] for l in other.splitlines())
    assert not re.search(rb"\binitDuration\s*\(|\.(?:dnsRouter|resolve|tunnel_index)\b", code), name
resolutions[path] = own

# Shadowsocks: preserve the shared scoped lifecycle and method normalization,
# and use the upstream consolidated obfs server, including host validation
# and fallback handling. The old fork had the same service dispatch after
# wrapping the connection in a mode-specific wrapper.
path = "protocol/shadowsocks/inbound.go"
old, own, new = (checked_blob(data[rev], path, sha) for rev, sha in zip(
    ("base", "patched_tree", "upstream"),
    ("e7d4c36bbbf8591194d3aafe9a4ce532a25bb028",
     "8ddbe5673d03c087dbbc5865ad303109d129c962",
     "01532b86fd78685563bc1f2d1093f23e9e36295f")))
plain = replace_once(new, b'\t"github.com/sagernet/sing-shadowsocks"\n',
    b'\tobfs "github.com/sagernet/sing-box/transport/simple-obfs"\n\tshadowsocks "github.com/sagernet/sing-shadowsocks"\n')
plain = replace_once(plain, b"\tobfs     obfsServer\n", b"\tobfsMode string\n")
i = plain.index(b"\tobfsServer, err := newObfsServer(options)")
j = plain.index(b"\tinbound.router, err = mux.NewRouterWithOptions", i)
a = own.index(b"\tswitch options.ObfsMode {")
b = own.index(b"\tinbound.router, err = mux.NewRouterWithOptions", a)
plain = plain[:i] + own[a:b] + plain[j:]
signature = b"func (h *Inbound) NewConnection("
plain = replace_once(plain, function_blob(plain, signature), function_blob(own, signature))
assert b"".join(plain.replace(b"//H", b"// H").split()) == b"".join(own.split()), path
assert b"scope.Add(h.listener.Close)" in new
assert b"h.obfs.serve(" in new
resolutions[path] = new


# SSH: restore all upstream regression cases without losing the fork's
# existing 12 tests. Compare the common test bodies and fixtures after
# reversing the reviewed API and assertion changes.
path = "protocol/ssh/h_ssh_test.go"
old, own, new = (checked_blob(data[rev], path, sha) for rev, sha in zip(
    ("base", "patched_tree", "upstream"),
    ("ce3b2f086bdf08bc09c3d5ae8c62ae7c55443ba2",
     "c77e328a3f4f6db8a68e9113734bab69973b76e4",
     "fcef4532910fcde76818eaec20260e0dc91a32a4")))
plain = new[:new.index(b"// the outbound connects in the background after start")].rstrip() + b"\n"
for line in (b'\t"crypto/rsa"\n', b'\t"github.com/sagernet/sing-box/common/uot"\n',
             b'\t"github.com/sagernet/sing/common/bufio/deadline"\n',
             b'\tC "github.com/sagernet/sing-box/constant"\n'):
    plain = replace_once(plain, line, b"")
plain = replace_once(plain, function_blob(plain, b"func startInboundOptions("), b"")
plain = replace_once(plain, b"ListenOptions: option.ListenOptions{Listen: &listen, ListenPort: f.port},",
    b"Listen: &listen, ListenPort: f.port,")
plain = replace_once(plain, b"t.Cleanup(func() { f.inbound.Close() })",
    b"t.Cleanup(func() { _ = htest.Close(f.inbound) })")
plain = replace_once(plain, b'ServerOptions: option.ServerOptions{Server: "127.0.0.1", ServerPort: f.port},',
    b'Server: "127.0.0.1", ServerPort: f.port,')
plain = replace_once(plain, b"t.Cleanup(func() { htest.Close(out.(*Outbound)) })",
    b"t.Cleanup(func() { out.(*Outbound).Close() })")
plain = plain.replace(b"adapter.IsReady(out)", b"out.IsReady()")
plain = replace_once(plain, b'require.Equal(t, adapter.StatusConnecting, out.Status())\n\trequire.Equal(t, "SSH", adapter.DisplayType(out), "the status is shown on its own")',
    b"require.NotEmpty(t, out.DisplayType())")
assert b"".join(normalize_imports(plain).split()) == b"".join(normalize_imports(own).split()), path
candidate = replace_once(new, b"t.Cleanup(func() { f.inbound.Close() })",
    b"t.Cleanup(func() { _ = htest.Close(f.inbound) })")
# The deadline regression uses a blocked read; release it on test exit.
candidate = replace_once(candidate, b"\tconn := deadline.NewConn(&channelConn{Channel: hBlockingChannel{}})",
    b"\tdone := make(chan struct{})\n\tdefer close(done)\n\tconn := deadline.NewConn(&channelConn{Channel: hBlockingChannel{done: done}})")
candidate = replace_once(candidate, b"type hBlockingChannel struct{ ssh.Channel }\n\nfunc (hBlockingChannel) Read([]byte) (int, error) { select {} }",
    b"type hBlockingChannel struct {\n\tssh.Channel\n\tdone <-chan struct{}\n}\n\nfunc (c hBlockingChannel) Read([]byte) (int, error) { <-c.done; return 0, io.EOF }")
old_tests = set(re.findall(rb"(?m)^func (Test\w+)\(", own))
new_tests = set(re.findall(rb"(?m)^func (Test\w+)\(", candidate))
assert len(old_tests) == 12 and len(new_tests) == 21 and old_tests <= new_tests
resolutions[path] = subprocess.check_output(["gofmt"], input=candidate)


# Clean textual merges can still retain incompatible fork implementations.
# The exact locked blobs below represent fork simplifications of upstream
# behavior, not unresolved merge-tree conflicts. Restore the corresponding
# upstream implementation together with its regression tests.
compat = {}
for path, own_sha, up_sha in (
    ("protocol/ssh/inbound.go", "6f4984344968c0ad369b6b46df1dcb07eba19615", "c3211f89c72ef2101172f05a02e254fcf613d50c"),
    ("protocol/shadowsocks/h_obfs.go", "9fbe0df7c80bdaaac95ec3093db3ceed206bb49a", "5c40547f9ecd478f779ad0a9eecf950881b17957"),
    ("protocol/shadowsocks/inbound_multi.go", "2c4a74239d054094a3759d852716a10506005ce3", "e2f8a3ea3ea04001f23e7538776ec1e189001daa"),
    ("protocol/shadowsocks/inbound_relay.go", "aa70f6ad2ecedc95c08be78d74bdac7de11e1aae", "04f8220d564f374fa63375d441ce0b93b76645b8"),
    ("protocol/shadowsocks/h_obfs_test.go", "0d7412493fc267ad1a74959e6da9cce138b75e71", "1c4b5d4e8c4ccf82e47effa2db54134aa858f783"),
):
    own = checked_blob(data["patched_tree"], path, own_sha)
    new = checked_blob(data["upstream"], path, up_sha)
    assert checked_blob(data["base"], path, up_sha) == new
    assert checked_blob(data["tentative_tree"], path, own_sha) == own
    if path == "protocol/ssh/inbound.go":
        for marker in (b"restrictHostKey(priv, options.HostKeyAlgorithms)", b"connectionRouter = router",
                       b"continue // only this channel is refused", b"deadline.NewConn(", b"scope.Add(h.Close)"):
            assert marker in new, marker
        # Start-at-stage fixture and callers may begin directly at Start.
        # Retain the fork's cleanup registration after successful start.
        new = replace_once(new, b"\tif stage == adapter.StartStateInitialize {\n\t\tscope.Add(h.Close) // H: closed with the box\n\t}\n", b"")
        old_start = function_blob(own, b"func (h *Inbound) Start(")
        new = replace_once(new, function_blob(new, b"func (h *Inbound) Start("), old_start)
    elif path.endswith("h_obfs_test.go"):
        old_tests = set(re.findall(rb"(?m)^func (Test\w+)\(", own))
        assert old_tests <= set(re.findall(rb"(?m)^func (Test\w+)\(", new))
    elif path.endswith("h_obfs.go"):
        assert b"func markAuthenticated(" in own and b"func markAuthenticated(" in new
        assert b"func newObfsServer(" in new
    else:
        assert b"markAuthenticated(ctx)" in own and b"markAuthenticated(ctx)" in new
        assert b"h.obfs.serve(" in new and b"scope.Add(" in new
    compat[path] = subprocess.check_output(["gofmt"], input=new)

# singlePeer moved into the upstream endpoint. Preserve the fork's exact
# address validation there and remove its duplicate from the helper file.
path = "protocol/awg/h_peer_fallback.go"
own = checked_blob(data["tentative_tree"], path, "6bc3eed178a731945c8684d18bae1fc163049be5")
new = checked_blob(data["upstream"], path, "ffc4e3b33be6b6c0cac2f7af4b4f647188218817")
assert checked_blob(data["base"], path, "ffc4e3b33be6b6c0cac2f7af4b4f647188218817") == new
signature = b"func singlePeer("
legacy = function_blob(own, signature)
assert b"".join(replace_once(own, legacy, b"").split()) == b"".join(new.split())
endpoint = "protocol/awg/endpoint.go"
resolutions[endpoint] = subprocess.check_output(["gofmt"], input=replace_once(
    resolutions[endpoint], function_blob(resolutions[endpoint], signature), legacy))
compat[path] = new


# VLESS: preserve reverse, testpre/testseed and XDrive, plus the fork's
# cancellable close-once encryption handshake. Adopt upstream early
# Vision validation, kTLS compatibility metadata and scoped cleanup.
path = "protocol/vless/outbound.go"
old, own, new = (checked_blob(data[rev], path, sha) for rev, sha in zip(
    ("base", "patched_tree", "upstream"),
    ("e901f7c5d40dc7d749ec5cfab20ab8d23af0f256",
     "930ff4fd7106a68e8f82b3ef6484b45f384e0510",
     "9c975b36e776696c17a2b8429037a80c1104bd3c")))
candidate = own
i = new.index(b"\t\toutbound.tlsConfig, err = tls.NewClientWithOptions(")
j = new.index(b"\n\t\tif err != nil", i)
candidate = replace_once(candidate,
    b"\t\toutbound.tlsConfig, err = tls.NewClient(ctx, logger, options.Server, common.PtrValueOrDefault(options.TLS))",
    new[i:j])
candidate = replace_once(candidate, b"\tmuxOpts := common.PtrValueOrDefault(options.Multiplex)",
    b"\tflow := options.Flow\n\tmuxOpts := common.PtrValueOrDefault(options.Multiplex)")
i = new.index(b"\tif outbound.vision && outbound.tlsConfig == nil && outbound.encryption == nil")
j = new.index(b"\n\toutbound.multiplexDialer", i)
candidate = replace_once(candidate, b"\toutbound.multiplexDialer, err = mux.NewClientWithOptions",
    new[i:j] + b"\n\toutbound.multiplexDialer, err = mux.NewClientWithOptions")
candidate = replace_once(candidate, b"\treverse         *reverseBridge\n",
    b"\treverse         *reverseBridge\n\tcloseOnce sync.Once\n\tcloseErr error\n")
candidate = replace_once(candidate, b"func (h *Outbound) Start(stage adapter.StartStage, _ *adapter.Scope) error {\n",
    b"func (h *Outbound) Start(stage adapter.StartStage, scope *adapter.Scope) error {\n\tif stage == adapter.StartStateInitialize {\n\t\tscope.Add(h.Close)\n\t}\n")
candidate = replace_once(candidate, function_blob(own, b"func (h *Outbound) Close("), b'''func (h *Outbound) Close() error {
	h.closeOnce.Do(func() {
		if h.reverse != nil {
			h.reverse.close()
		}
		if h.preconnect != nil {
			h.preconnect.Close()
		}
		h.closeErr = common.Close(common.PtrOrNil(h.multiplexDialer), h.transport)
	})
	return h.closeErr
}
''')
candidate = replace_once(candidate, b"\treturn conn, baseConn, err\n}",
    b"\tif err != nil {\n\t\t_ = common.Close(conn)\n\t\treturn nil, nil, err\n\t}\n\treturn conn, baseConn, nil\n}")
for marker in (b"SetVisionTestseed(options.Testseed)", b"newPreconnectPool(", b"newReverseBridge(",
               b"XDrive requires VLESS encryption", b"DialEarlyReverseConnWithOptions(",
               b"destination.IsDomain()", b"handshakeOnceConn", b"scope.Add(h.Close)"):
    assert marker in candidate, marker
assert candidate.count(b"context.AfterFunc(ctx, closeRaw)") == 2
resolutions[path] = subprocess.check_output(["gofmt"], input=candidate)


# FinalMask: preserve the fork's compact range parser and modern Go
# iteration, while bringing over the upstream IPv4 test socket fix.
path = "hiddify/finalmask/dialer.go"
old, own, new = (checked_blob(data[rev], path, sha) for rev, sha in zip(
    ("base", "patched_tree", "upstream"),
    ("e55c64742e1c30f08ea97273c67dea31c377356a",
     "ec1233fcd16f3659a90b371893c978eea570c2aa",
     "81af2878a478f333ffc910e8adc8b1a02fb0a882")))
plain = replace_once(new, b"for i := len(m.tcp) - 1; i >= 0; i-- {", b"for _, v := range slices.Backward(m.tcp) {")
plain = replace_once(plain, b"m.tcp[i].WrapConnServer(conn)", b"v.WrapConnServer(conn)")
plain = replace_once(plain, b"destination.IsFqdn()", b"destination.IsDomain()")
assert plain == own
resolutions[path] = own

path = "hiddify/finalmask/h_xdns_legacy.go"
own, new = added_pair(path, "b6308a2cbae093800c4a9725c5a728d823991d14",
                      "b0c5884cecc0b9abad79049f79d918beb2ca86f9")
# The only difference is the fork's blank line between external imports.
# Keep an exact comparison so any other upstream change fails the audit.
plain = replace_once(new,
    b'E "github.com/sagernet/sing/common/exceptions"\n\t"google.golang.org/protobuf/proto"',
    b'E "github.com/sagernet/sing/common/exceptions"\n\n\t"google.golang.org/protobuf/proto"')
assert plain == own, path
resolutions[path] = own

path = "hiddify/finalmask/xray_tls.go"
own, new = added_pair(path, "7070bfe749180c4df93d3cf09c3f04c8d956c474",
                      "86328c3c098d71c0d4c39587aeb8fda446bccd35")
plain = replace_once(new, b'for _, rangeStr := range strings.Split(listStr, ",") {',
                     b'for rangeStr := range strings.SplitSeq(listStr, ",") {')
assert plain == own
resolutions[path] = own

path = "hiddify/finalmask/xray_conf.go"
old, own, new = (checked_blob(data[rev], path, sha) for rev, sha in zip(
    ("base", "patched_tree", "upstream"),
    ("c766f97a89881cc44ac8017241a2d2193fc2317c",
     "f5fa47e690a239d09db5b79b80cba980538342c6",
     "2f2a1c47e1fa9ece4082451105ff742133a9ec6e")))
plain = replace_once(new, b'for _, mode := range strings.Split(c.Mode, ",") {',
                     b'for mode := range strings.SplitSeq(c.Mode, ",") {')
plain = replace_once(plain, b'Lengths  Int32RangeList', b'Lengths  hInt32Ranges')
plain = replace_once(plain, b'Delays   Int32RangeList', b'Delays   hInt32Ranges')
assert b"".join(plain.split()) == b"".join(own.replace(b" // H: accept old compact ranges", b"").replace(b"  // H: accept old compact ranges", b"").split())
helper = checked_blob(data["tentative_tree"], "hiddify/finalmask/h_fragment_ranges.go", "5d15380247c0b4a5645a073905e207250d516ae5")
assert b"func (r *hInt32Ranges) UnmarshalJSON(" in helper
resolutions[path] = own

path = "hiddify/finalmask/finalmask_test.go"
old, own, new = (checked_blob(data[rev], path, sha) for rev, sha in zip(
    ("base", "patched_tree", "upstream"),
    ("6efed322afa1913460822a4c366bb88739af9b20",
     "7ae30bd9d216d5f628efc808d8ed3f41d80f7cee",
     "e81a555b2b0ba36b20e944d99d80cfb885dc4e41")))
i = new.index(b"// listenPacket opens")
j = new.index(b"// wrapTCP", i)
plain = new[:i] + new[j:]
plain = replace_once(plain, b"N.SystemDialer.DialContext, listenPacket)", b"N.SystemDialer.DialContext, N.SystemDialer.ListenPacket)")
plain = replace_once(plain, b"packetConn, err := listenPacket(ctx, destination)", b"packetConn, err := N.SystemDialer.ListenPacket(ctx, destination)")
assert plain == own
resolutions[path] = new

path = "hiddify/finalmask/h_xray_v26_10_test.go"
own, new = added_pair(path, "327d41fed652b6ffbb886b18f14156ad26308d69",
                      "3a5a1ff42c74baedfa9eb43673ac00bf911a3f1d")
plain = replace_once(new, b"return listenPacket(ctx, destination)", b"return N.SystemDialer.ListenPacket(ctx, destination)")
plain = replace_once(plain,
    b"// a hop picks any of the ports, the current one too: with 16 the connection stays on its first\n\t// port through the three hops of the 20 s below once in 4096 runs (once in 27 with three ports)",
    b"// A larger pool makes a repeated random selection vanishingly unlikely.")
plain = replace_once(plain, b"for range 16 {", b"for range 8 {")
assert plain == own
resolutions[path] = new

# Outbound manager: keep the fork's immutable slices and captured scope,
# error returns instead of panic, and add upstream dependency checking
# for endpoints plus per-outbound security classification.
path = "adapter/outbound/manager.go"
own = checked_blob(data["patched_tree"], path, "a56075c31237362da68b492af744061c5f337c58")
new = checked_blob(data["upstream"], path, "46d31bc9d9c62e5bfd598a2861300bc42fb977ed")
candidate = own
lifecycle_edit(b"\tstarted                 bool\n", b"\tstarted                 bool\n\tinsecure map[string]string\n")
lifecycle_edit(b"\t\toutboundByTag: make(map[string]adapter.Outbound),", b"\t\toutboundByTag: make(map[string]adapter.Outbound),\n\t\tinsecure: make(map[string]string),")
before = lifecycle_section(own, b"\t\tvar dependBy []string\n", b"\t\tif len(dependBy)")
lifecycle_edit(before, b"\t\tdependBy := m.dependents(tag)\n")
helper = lifecycle_section(new, b"// dependents lists", b"func (m *Manager) Create(")
helper = helper.replace(b"if common.Contains(outbound.Dependencies(), tag) {", b"if outbound.Tag() != tag && slices.Contains(outbound.Dependencies(), tag) {")
lifecycle_edit(b"func (m *Manager) Create(", helper + b"func (m *Manager) Create(")
lifecycle_edit(b"\tdelete(m.outboundByTag, tag)\n", b"\tdelete(m.outboundByTag, tag)\n\tdelete(m.insecure, tag)\n")
lifecycle_edit(b"\tm.outboundByTag[tag] = outbound\n", b"\tm.outboundByTag[tag] = outbound\n\tm.insecure[tag] = option.InsecureReason(options)\n")
# Reject duplicate configuration entries before creating a second outbound
# when not running. A valid running replacement continues to use hot reload.
lifecycle_edit(b"\toption.MigrateTLSFragment(options) // H", b'''	m.access.RLock()
	_, duplicate := m.outboundByTag[tag]
	started := m.started
	m.access.RUnlock()
	if duplicate && !started {
		return E.New("duplicate outbound tag: ", tag)
	}
	option.MigrateTLSFragment(options) // H''')
# The existing short declaration is valid: scope is new in this function.
# Do not publish a nonexistent default tag on lookup failure.
lifecycle_edit(b"\tm.defaultTag = tag\n\tif tag == \"\" {", b"\tif tag == \"\" {\n\t\tm.defaultTag = tag")
lifecycle_edit(b"\tdefer m.access.Unlock()\n\tif m.defaultOutbound != outbound", b"\tdefer m.access.Unlock()\n\tm.defaultTag = tag\n\tif m.defaultOutbound != outbound")
assert b"append(slices.Clone(m.outbounds), outbound)" in candidate
assert b"adapter.CloseLate(scope, outbound)" in candidate
assert b"m.endpoint.Endpoints()" in candidate
resolutions[path] = subprocess.check_output(["gofmt"], input=candidate)

# Tunnel server: upstream fixes lock leaks on unknown users and preserves
# nonblocking full-queue eviction. Close a discarded connection when the
# initial request write fails, rather than silently leaking it.
path = "protocol/tunnel/server.go"
own = checked_blob(data["patched_tree"], path, "d05fda52a3569fe3f7545b3a633bfcdddc673e64")
new = checked_blob(data["upstream"], path, "9ccb83c2c2a92df7984133c9dfe19c335b308e57")
candidate = new
lifecycle_edit(b"\t\t\t\ts.logger.ErrorContext(ctx, err)\n\t\t\t\tcontinue", b"\t\t\t\ts.logger.ErrorContext(ctx, err)\n\t\t\t\tconn.Close()\n\t\t\t\tcontinue")
assert b"ch, ok = s.conns[tunnelDestination]\n\t\t\ts.mtx.Unlock()" in candidate
assert b"return E.New(\"user \", request.DestinationUUID, \" not found\")" in candidate
assert b"case oldConn := <-ch:" in candidate and b"oldConn.Close()" in candidate
resolutions[path] = subprocess.check_output(["gofmt"], input=candidate)

# Network manager and router are one coordinated lifecycle change:
# upstream routes interface changes through the DNS/HTTP cache reset once,
# registers scope-owned cleanup and avoids opening a second neighbor resolver.
path = "route/network.go"
own = checked_blob(data["patched_tree"], path, "1ae3cedb2f96f857e16eee20b16251505923d809")
new = checked_blob(data["upstream"], path, "ce6a86e7802ee17f2cf4c22cef0e0dcad9424f26")
candidate = new
# Do not notify an already stopped network scope. Early notifications
# still set networkResetPending so post-start can dispatch them.
lifecycle_edit(b"\tif r.startedCtx != nil {\n\t\tr.dispatchInterfaceUpdateLocked()", b"\tif r.startedCtx != nil && r.startedCtx.Err() == nil {\n\t\tr.dispatchInterfaceUpdateLocked()")
lifecycle_edit(b"started := r.startedCtx != nil\n", b"started := r.startedCtx != nil && r.startedCtx.Err() == nil\n")
# Preserve the fork's monitoring notification when the platform explicitly
# flushes DNS, in addition to upstream's native inbound cache flush.
lifecycle_edit(b"\t\t\tflusher.FlushSystemDNSCache()\n\t\t}\n\t}\n}", b"\t\t\tflusher.FlushSystemDNSCache()\n\t\t}\n\t}\n\tr.networkChanged()\n}")
lifecycle_edit(b"\tr.router.ResetNetwork()\n", b"\tif r.router != nil {\n\t\tr.router.ResetNetwork()\n\t}\n")
assert b"r.startedCtx = scope.Context()" in candidate
assert b"r.interfaceMonitor.UnregisterCallback(interfaceUpdateElement)" in candidate
assert b"scope.Add(r.powerListener.Close)" in candidate
assert b"monitor.InterfaceUpdated(ctx)" in candidate
assert b"r.connectionManager.CloseAll()" in candidate
resolutions[path] = subprocess.check_output(["gofmt"], input=candidate)

path = "route/router.go"
own = checked_blob(data["patched_tree"], path, "0aa84c4b5e1779adf1951863f60181ad95dbe563")
new = checked_blob(data["upstream"], path, "5c89a4ae3c13351db1361a182638903d1a4e2904")
# Keep the initialization-stage neighbor resolver (including native
# platform resolution), not the duplicate start-stage initialization.
assert b"r.needFindNeighbor = needFindNeighbor" in own
assert b"r.needFindNeighbor = needFindNeighbor" not in new
assert b"newPlatformNeighborResolver" in new and b"scope.Add(resolver.Close)" in new
assert b"scope.Add(r.processSearcher.Close)" in new
assert b"hSetIPv6Fallback(router.connection, options.IPv6Fallback)" in new
assert b"dnsHijackSlots:" in new
assert b"r.network.ResetNetwork(r.ctx)" not in new
assert b"r.httpClientManager.ResetNetwork()" in new and b"r.dns.ResetNetwork()" in new
resolutions[path] = new

# The connection manager's upstream version retains the fork's tracked
# socket owner, IPv6 fallback, cached handshake counters and splice guard.
# Adopt paced TCP/UDP wrappers, server-first handshake handling and failed
# packet-dial history invalidation as a coordinated candidate. Do not
# remove the runtime pacing opt-out or the regression tests.
path = "route/conn.go"
own = checked_blob(data["patched_tree"], path, "ac53c62b7bdc39c112df8b570ef3b584e28cd21c")
new = checked_blob(data["upstream"], path, "89f23acb67f099fc60f16eb1259b0af5dc076294")
for marker in (b"type socketOwner struct", b"func (o *socketOwner) Attach(", b"func (o *socketOwner) detach(", b"func (m *ConnectionManager) CloseAll(", b"hNewIPv6Connection(", b"metadata.TLSSpoof == \"\"", b"cachedBuffer.Release()", b"readCounters", b"writeCounters", b"m.deleteURLTestHistory(metadata.OutboundChain)"):
    assert marker in own and marker in new, marker
for marker in (b"tracked.hPace.init(conn)", b"tracked.wake.path.init(conn)", b"serverFirst := sniff.Skip(&metadata)", b"tlsspoof.NewConn(", b"c.hSyscallConn(syscallConn)", b"hPacedSyscallConn(syscallConn, &c.wake)", b"return c.stream || pacingDisabled"):
    assert marker in new, marker
assert new.count(b"m.deleteURLTestHistory(metadata.OutboundChain)") > own.count(b"m.deleteURLTestHistory(metadata.OutboundChain)")
for helper_path, sha in (
    ("route/h_read_pace.go", "85acf4679e1dc326cbd468fddf8d42787eaa8b06"),
    ("route/h_wake_pace.go", "710be5ca8e6a800244c2d93d20026feeff864275"),
    ("route/h_read_pace_test.go", "8288929bf1cdcf35650be98e681b1bb682188269"),
    ("route/h_wake_pace_test.go", "51eec59d0726169970a8388d442045ee0b4176bb"),
    ("route/h_wake_pace_unix_test.go", "f8a86faf2cbb06589e3f2b6d4b1767486f97b1d8"),
):
    helper = checked_blob(data["upstream"], helper_path, sha)
    assert git("show", data["tentative_tree"] + ":" + helper_path) == helper, helper_path
    if helper_path == "route/h_read_pace.go":
        assert b"HIDDIFY_NO_READ_PACE" in helper
resolutions[path] = new

# This file merged cleanly at the textual level, but the AWG constructor
# changed. Reconfigure the existing path instead of replacing a live one.
path = "transport/awg/h_path_test.go"
clean = checked_blob(data["tentative_tree"], path, "a837c542cec35985d6572a7a36ea7ddb650f2266")
assert checked_blob(data["base"], path, "0674856118733ea57d638832773ebc51a7d46db8") == checked_blob(data["upstream"], path, "0674856118733ea57d638832773ebc51a7d46db8")
old_setup = b'''	// The helper starts without fallback configured; create the path as it would
	// be created at construction when fallback_address is supplied.
	client.path = newDevicePath(ctx, logger.NOP(), client.tun, DeviceOpts{
		Address:  []netip.Prefix{netip.MustParsePrefix("10.77.0.2/32")},
		MTU:      1408,
		Peer:     dead,
		Fallback: []netip.AddrPort{real},
	})
	client.path.start(client)'''
clean = replace_once(clean, old_setup,
    b"\tclient.path.setAddresses([]netip.AddrPort{dead, real})")
# The revised server bind explicitly listens on its configured port.
clean = replace_once(clean, b"DeviceOpts{Address: []netip.Prefix{serverAddress}, MTU: 1500}",
    b"DeviceOpts{Address: []netip.Prefix{serverAddress}, MTU: 1500, ListenPort: serverPort}")
apply_reviewed(data, resolutions, {**compat, path: subprocess.check_output(["gofmt"], input=clean),
                                  "adapter/h_detour.go": detour})

