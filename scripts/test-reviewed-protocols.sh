#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/_common.sh"
need git
need go
need python3
report="$ROOT/.build/upstream-merge-audit"
sing="$WORK/hiddify-sing-box"
# These packages are independent of the WARP candidate. Restore each
# touched directory afterwards so no test overlay contaminates another.
python3 - "$sing" "$report" <<'PY'
import json, subprocess, sys, shutil, tempfile, os
from pathlib import Path
sing, report = map(Path, sys.argv[1:])
meta = json.loads((report / "hiddify-sing-box-reviewed.json").read_text())
tree = meta["reviewed_tree"]
def git(*args):
    return subprocess.check_output(["git", "-C", str(sing), *args])
groups = ("protocol/hiddify/dnstt", "protocol/shadowsocks", "transport/simple-obfs", "protocol/ssh",
          "hiddify/readahead", "common/wholepacket", "hiddify/finalmask", "protocol/tunnel", "transport/v2rayxdrive", "protocol/psiphon", "common/proxybridge", "hiddify/edgescan", "transport/call", "protocol/call")
extra = ("option/shadowsocks.go", "option/dnstt.go", "option/ssh.go", "option/psiphon.go", "option/h_psiphon_scanner.go", "option/h_edge_search.go", "option/h_call.go")
# Some option filenames differ by revision: only include paths that exist.
extra = tuple(p for p in extra if git("ls-tree", tree, "--", p))
blocked = [p for p in meta["remaining_conflicts"] if p in extra or any(p.startswith(g + "/") for g in groups)]
assert not blocked, blocked
assert set(("protocol/hiddify/dnstt/outbound.go", "protocol/shadowsocks/inbound.go")) <= set(meta["reviewed_resolutions"])
with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    archive = tmp / "candidate.tar"
    subprocess.run(["git", "-C", str(sing), "archive", "--format=tar", "-o", str(archive),
                    tree, *groups, *extra], check=True)
    dest = tmp / "src"
    dest.mkdir()
    subprocess.run(["tar", "-xf", str(archive), "-C", str(dest)], check=True)
    backup = tmp / "backup"
    touched = []
    manifests = {p: (sing / p).read_bytes() for p in ("go.mod", "go.sum")}
    try:
        for path in (*groups, *extra):
            original = sing / path
            saved = backup / path
            saved.parent.mkdir(parents=True, exist_ok=True)
            if original.exists():
                original.rename(saved)
            touched.append(path)
            if (dest / path).is_dir():
                shutil.copytree(dest / path, original)
            else:
                shutil.copyfile(dest / path, original)
        env = dict(os.environ, CGO_ENABLED="1")
        failed = []
        for name, package in (("readahead", "./hiddify/readahead"), ("wholepacket", "./common/wholepacket"), ("dnstt", "./protocol/hiddify/dnstt"), ("shadowsocks", "./protocol/shadowsocks"), ("ssh", "./protocol/ssh"), ("finalmask", "./hiddify/finalmask"), ("tunnel-server", "./protocol/tunnel"), ("xdrive-lifecycle", "./transport/v2rayxdrive"), ("psiphon", "./protocol/psiphon"), ("telemost", "./transport/call/telemost"), ("call-relay", "./transport/call/tunnel"), ("call-config", "./transport/call"), ("call-retry", "./protocol/call")):
            with (report / (name + "-candidate-test.log")).open("wb") as log:
                result = subprocess.run(["go", "test", "-race", "-mod=mod", "-count=1",
                    "-timeout=5m", "-ldflags", "-checklinkname=0", package], cwd=sing, env=env,
                    stdout=log, stderr=subprocess.STDOUT)
            print((report / (name + "-candidate-test.log")).read_text(), flush=True)
            if result.returncode:
                failed.append(name)
        if failed:
            raise SystemExit("candidate tests failed: " + ", ".join(failed))
    finally:
        for path in reversed(touched):
            original = sing / path
            saved = backup / path
            if original.is_dir():
                shutil.rmtree(original)
            elif original.exists():
                original.unlink()
            if saved.exists():
                saved.rename(original)
        for path, content in manifests.items():
            (sing / path).write_bytes(content)
PY
