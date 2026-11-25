#!/usr/bin/env python3
"""Read-only audit of the panel contract against the prepared VetrOFF tree."""
import json
import pathlib
import re
import subprocess
import sys

core, panel, report = map(pathlib.Path, sys.argv[1:])
lock = json.loads((panel / "deploy/hiddify/hiddify-core-target.json").read_text())
build = lock["panel_build"]
checks = []
for item in lock["source_checks"]:
    path = core / item["path"]
    if item["path"] == ".gitmodules":
        checks.append({"path": item["path"], "status": "different-by-design",
                       "reason": "VetrOFF uses official gitlinks plus audited patches"})
        continue
    ok = path.is_file() and item["contains"] in path.read_text(errors="replace")
    checks.append({"path": item["path"], "status": "pass" if ok else "fail",
                   "expected": item["contains"]})
native = [{"path": path, "status": "pass" if (core / path).is_file() else "fail"}
          for path in build["native_source_files"]]


def version_parts(value):
    match = re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)(?:-([^+]+))?(?:\+.*)?", value)
    if not match:
        return None
    # Equal numeric versions: require exact reviewed prerelease or stable.
    return tuple(map(int, match.groups()[:3])), match.group(4)


security = []
for name, expected in sorted(build["security_dependencies"].items()):
    cmd = subprocess.run(["go", "list", "-mod=readonly", "-m", "-json", name],
                         cwd=core, capture_output=True, text=True)
    if cmd.returncode:
        security.append({"module": name, "minimum": expected, "status": "fail",
                         "error": cmd.stderr.strip()})
        continue
    module = json.loads(cmd.stdout)
    replacement = module.get("Replace")
    actual = (replacement or module).get("Version", "")
    have, need = version_parts(actual), version_parts(expected)
    ok = bool(have and need and (have[0] > need[0] or
              (have[0] == need[0] and (have[1] is None or actual == expected))))
    security.append({"module": name, "minimum": expected, "effective": actual,
                     "replacement": replacement, "status": "pass" if ok else "fail"})

# The panel's strict process version parser and updater still consume the
# legacy release/manifest format. A build passing this audit is NOT activated.
result = {"source_checks": checks, "native_files": native, "security_dependencies": security,
          "required_tags": build["tags"], "old_release_identity": lock["target"]["version"],
          "activation_ready": False, "live_traffic_verified": False,
          "remaining_contracts": ["VetrOFF version and source manifest acceptance",
                                  "release downloader and atomic rollback",
                                  "server traffic, statistics and protocol E2E"]}
report.parent.mkdir(parents=True, exist_ok=True)
report.write_text(json.dumps(result, indent=2) + "\n")
failures = [x for x in checks + native + security if x["status"] == "fail"]
print(json.dumps({"source_checks": len(checks), "native_files": len(native),
                  "security_dependencies": len(security), "failures": failures}, indent=2))
if failures:
    raise SystemExit(1)
