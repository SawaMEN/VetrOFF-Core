#!/usr/bin/env python3
"""Prepare dependency manifest candidates. Never update the stable sources.

All three inputs are reviewed blob IDs. Preserve requirements from both active
sides until a successful combined build and go mod tidy. Resolve competing
versions explicitly, never by text conflict markers or an implicit downgrade.
"""
import json
import os
from pathlib import Path
import subprocess
import sys

root, report = map(Path, sys.argv[1:])
inputs = {
    "hiddify-core": (
        "f715fb41217158b6b60adbdffae5f9d2f8fe9729",
        "b67fff708e1f161e83590ceb86594edb9825aeb5",
        "6ae98ae0aba885de89425c8617c25818aaf43e5d",
    ),
    "hiddify-sing-box": (
        "ee1aa0bce83b007acb8219951da698acd9bc0cb3",
        "6f87f1070cadf0f41efc6ec03b3baf4f8cf0fadd",
        "92e82bce4f7825e13e8a24478dbe5ced3e0f1f37",
    ),
}
# Keep the fork's vendored Xray version and its paired Reality revision for
# now; revisit the pair when reviewing the corresponding API and TLS files.
decisions = {
    "github.com/klauspost/cpuid/v2": "v2.4.2",
    "github.com/xtls/xray-core": "v1.260327.1-0.20261031132019-a4a3dff5b20c",
    "github.com/xtls/reality": "v0.0.0-20261014101318-83f621788ca8",
    "golang.org/x/net": "v0.62.0",
    "google.golang.org/grpc": "v1.85.0",
    "github.com/klauspost/compress": "v1.20.3",
    "google.golang.org/genproto/googleapis/rpc": "v0.0.0-20260817212433-ac3dfec99bb1",
    "github.com/gorilla/websocket": "v1.5.4-0.20250319132907-e064f32e3674",
}

def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args])

def parse(content):
    result = {"require": {}, "replace": {}, "directives": {}, "comments": []}
    block = None
    for raw in content.decode().splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("//"):
            if line not in result["comments"]:
                result["comments"].append(line)
            continue
        if line == ")":
            assert block
            block = None
            continue
        if line.endswith(" ("):
            assert block is None and line[:-2] in ("require", "replace")
            block = line[:-2]
            continue
        parts = line.split()
        kind = block or parts.pop(0)
        if kind == "require":
            assert len(parts) in (2, 4) and (len(parts) == 2 or parts[2:] == ["//", "indirect"]), raw
            assert parts[0] not in result[kind]
            result[kind][parts[0]] = (parts[1], len(parts) == 4)
        elif kind == "replace":
            i = parts.index("=>")
            key, target = " ".join(parts[:i]), " ".join(parts[i+1:])
            assert key not in result[kind] and target
            result[kind][key] = target
        else:
            assert kind in ("module", "go", "godebug") and kind not in result["directives"], raw
            result["directives"][kind] = " ".join(parts)
    assert block is None
    return result

def merge(component):
    repo = root if component == "hiddify-core" else root / component
    meta = json.loads((report / (component + ".json")).read_text())
    docs = []
    for rev, expected in zip(("base", "patched_tree", "upstream"), inputs[component]):
        actual = git(repo, "rev-parse", meta[rev]+":go.mod").decode().strip()
        assert actual == expected, (component, rev, actual)
        docs.append(parse(git(repo, "cat-file", "blob", actual)))
    base, fork, upstream = docs
    merged = {}
    reviewed = []
    for key in sorted(set(fork["require"]) | set(upstream["require"])):
        a, b, c = (doc["require"].get(key) for doc in docs)
        if b is None or c is None:
            chosen = b or c
        elif b[0] == c[0]:
            chosen = (b[0], b[1] and c[1])
        elif a and b[0] == a[0]:
            chosen = (c[0], b[1] and c[1])
        elif a and c[0] == a[0]:
            chosen = (b[0], b[1] and c[1])
        else:
            assert key in decisions, ("Unreviewed version conflict", key, a, b, c)
            assert decisions[key] in (b[0], c[0]), key
            chosen = (decisions[key], b[1] and c[1])
            reviewed.append({"module":key,"base":a,"fork":b,"upstream":c,"selected":chosen})
        merged[key] = chosen
    replacements = {}
    for key in sorted(set(fork["replace"]) | set(upstream["replace"])):
        a, b, c = (doc["replace"].get(key) for doc in docs)
        assert b is None or c is None or b == c or b == a or c == a, ("Unreviewed replacement conflict", key)
        # Preserve every active fork replacement, including native/vendored modules.
        # If a shared replacement was changed only by upstream, take its change.
        replacements[key] = (c if b == a and c is not None else b) or c
    directives = dict(fork["directives"])
    assert directives["module"] == upstream["directives"]["module"]
    assert directives["godebug"] == upstream["directives"]["godebug"] == "multipathtcp=0"
    assert directives["go"] == "1.27.3"
    lines = [f'module {directives["module"]}', "", f'go {directives["go"]}', ""]
    comments = list(dict.fromkeys(fork["comments"] + upstream["comments"]))
    lines += comments + ["", f'godebug {directives["godebug"]}', ""]
    for indirect in (False, True):
        entries = [f"\t{key} {version}" + (" // indirect" if flag else "")
                   for key, (version, flag) in merged.items() if flag == indirect]
        if entries:
            lines += ["require (", *entries, ")", ""]
    for key, value in replacements.items():
        lines.append(f"replace {key} => {value}")
    candidate = ("\n".join(lines) + "\n").encode()
    validated = parse(candidate)
    assert validated["require"] == merged and validated["replace"] == replacements
    assert set(fork["require"]) | set(upstream["require"]) == set(merged)
    assert set(fork["replace"]) | set(upstream["replace"]) == set(replacements)
    for key in ("github.com/xtls/xray-core", "github.com/metacubex/utls", "github.com/sagernet/sing-vmess"):
        assert replacements[key] == fork["replace"][key]
    assert "github.com/SawaMEN/VetroffProxy" in merged
    candidate_file = report / (component + "-go.mod.candidate")
    candidate_file.write_bytes(candidate)
    # Validate with Go's actual module parser, without toolchain downloads.
    env = dict(os.environ, GOTOOLCHAIN="local", GOPROXY="off", GOSUMDB="off")
    result = subprocess.check_output(["go", "mod", "edit", "-json", str(candidate_file)], env=env)
    go = json.loads(result)
    assert go["Go"] == directives["go"]
    actual_require = {x["Path"]:(x["Version"],x["Indirect"]) for x in go["Require"]}
    assert actual_require == merged
    summary = {"component":component,"requirements":len(merged),"replacements":len(replacements),
               "reviewed_version_choices":reviewed,
               "status":"candidate; combined build and dependency API review still required"}
    (report / (component + "-dependency-candidate.json")).write_text(json.dumps(summary, indent=2)+"\n")
    print(f'{component}: dependency candidate validated, {len(merged)} requirements, '
          f'{len(replacements)} replacements, {len(reviewed)} explicit version decisions')
    for choice in reviewed:
        print(f'  {choice["module"]}: {choice["selected"][0]}')

for component in inputs:
    merge(component)
