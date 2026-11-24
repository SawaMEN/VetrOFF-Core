#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/_common.sh"
need git
need go
need python3
report="$ROOT/.build/upstream-merge-audit"
sing="$WORK/hiddify-sing-box"
python3 - "$sing" "$report" <<'PY'
import json, os, subprocess, sys
from pathlib import Path
sing, report = map(Path, sys.argv[1:])
meta = json.loads((report / "hiddify-sing-box-reviewed.json").read_text())
tree = meta["reviewed_tree"]
def git(*args):
    return subprocess.check_output(["git", "-C", str(sing), *args])
path = "protocol/vless/outbound.go"
assert path in meta["reviewed_resolutions"]
assert not any(p.startswith("protocol/vless/") for p in meta["remaining_conflicts"])
test = "protocol/vless/h_outbound_close_test.go"
assert git("rev-parse", tree + ":" + test).decode().strip() == "8d31e7415ee56742b8696db60c355a10ff6a1d11"
paths = (path, test, "go.mod", "go.sum")
backup = {p: (sing / p).read_bytes() if (sing / p).exists() else None for p in paths}
try:
    for p in (path, test):
        (sing / p).write_bytes(git("show", tree + ":" + p))
    with (report / "vless-candidate-test.log").open("wb") as log:
        result = subprocess.run(["go", "test", "-race", "-mod=mod", "-count=1",
            "-timeout=5m", "-ldflags", "-checklinkname=0",
            "./protocol/vless"], cwd=sing, env=dict(os.environ, CGO_ENABLED="1"),
            stdout=log, stderr=subprocess.STDOUT)
    print((report / "vless-candidate-test.log").read_text(), flush=True)
    result.check_returncode()
finally:
    for p, content in backup.items():
        if content is None:
            (sing / p).unlink(missing_ok=True)
        else:
            (sing / p).write_bytes(content)
PY
