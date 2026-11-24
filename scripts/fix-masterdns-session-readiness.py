#!/usr/bin/env python3
"""Apply the reviewed MasterDnsVPN readiness fix to the disposable checkout."""
import re
import subprocess
import sys
from pathlib import Path

root = Path(sys.argv[1])
old, target = sys.argv[2:4]

def git(*args):
    return subprocess.check_output(["git", "-C", str(root), *args])

# Both pinned trees contain the same vulnerable implementation. Verify all
# six originals before applying any changes, including the internal tests.
files = {
    "internal/client/client.go": "0ef11475c7996e721bc004cc58d46f4a9bd5ff95",
    "internal/client/client_utils.go": "1f848e3a21c7920b65fdccc59f6f769ed005543a",
    "internal/client/session.go": "292f6c341d2fb4cb607105652f646ceebf0c710b",
    "internal/client/async_runtime.go": "d76d229d2da8078ecc06c710add22b7056ab9708",
    "internal/client/session_init_test.go": "b6008f971430181c465bbf29734c0d05aa46300e",
    "internal/client/async_runtime_test.go": "4dd4817ff50edc928290ce809c54f070f193f18d",
}
sources = {}
for path, sha in files.items():
    for rev in (old, target):
        assert git("rev-parse", rev + ":" + path).decode().strip() == sha, (rev, path)
    blob = (root / path).read_bytes()
    assert git("hash-object", "--", path).decode().strip() == sha, path
    sources[path] = blob

# Fail closed if a new read/write or struct literal was missed.
refs = set(git("grep", "-l", "-w", "sessionReady", old, "--", "*.go").decode().splitlines())
refs = {ref.split(":", 1)[1] for ref in refs}
assert refs == set(files), (refs - set(files), set(files) - refs)

def replace_once(blob, before, after):
    assert blob.count(before) == 1, before
    return blob.replace(before, after, 1)

for path, blob in sources.items():
    if path.endswith("/client.go"):
        blob, count = re.subn(rb"(?m)^(\tsessionReady\s+)bool$", rb"\1atomic.Bool", blob)
        assert count == 1
    if path.endswith("/session.go"):
        # Publish the accepted session only after all initialization fields
        # and compression policy have been written.
        blob = replace_once(blob, b"\t\tc.sessionReady = true\n", b"")
        blob = replace_once(blob,
            b"\t\tc.clearSessionResetPending()\n\t\treturn nil",
            b"\t\tc.clearSessionResetPending()\n\t\tc.sessionReady.Store(true)\n\t\treturn nil")
    blob = re.sub(rb"\bc\.sessionReady = (true|false)\b", rb"c.sessionReady.Store(\1)", blob)
    blob = re.sub(rb"\bc\.sessionReady\b(?!\.(?:Load|Store)\()", b"c.sessionReady.Load()", blob)
    # Every code reference now uses atomic operations.
    assert not re.search(rb"\bc\.sessionReady\b(?!\.(?:Load|Store)\()", blob)
    (root / path).write_bytes(subprocess.check_output(["gofmt"], input=blob))
git("add", "--", *files)
