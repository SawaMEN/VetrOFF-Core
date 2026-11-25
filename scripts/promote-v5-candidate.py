#!/usr/bin/env python3
"""Promote only the exact successful reduced 0.0.5 release candidate."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
CORE = "fcbad0011cf3eb757e1ebc180be31c507c87b142"
SING = "d763388ecaa48e6be8ee83946f2eb03fe6a18d01"
RUN = 45556079422
PINS = {
    "hiddify-core": "4e5a9a357003d0a995360586702fd24c53042e3f",
    "hiddify-sing-box": "a26e58b376987a3305b201ec87fbc8918f620ad3",
    "ray2sing": "a722bf1b184d7fcf10248dee45fc099d62646ca8",
}


def require(ok, message):
    if not ok:
        raise ValueError(message)


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args])


def replace(path, before, after):
    file = ROOT / path
    text = file.read_text()
    require(text.count(before) == 1, f"unexpected anchor: {path}: {before}")
    file.write_text(text.replace(before, after, 1))


def append_patch(component, patch, old, new):
    directory = ROOT / "patches" / component
    require((directory / "expected-tree").read_text().strip() == old,
            f"unexpected old tree: {component}")
    name = "0002-verified-v5-rc.patch"
    require(not (directory / name).exists(), f"patch already exists: {component}")
    (directory / name).write_bytes(patch.read_bytes())
    series = directory / "series"
    series.write_text(series.read_text().rstrip() + "\n" + name + "\n")
    (directory / "expected-tree").write_text(new + "\n")


def main(report):
    require((ROOT / "VERSION").read_text().strip() == "0.0.4", "unexpected VERSION")
    require(not git(ROOT, "status", "--porcelain", "--untracked-files=no"),
            "dirty tracked root checkout")
    for path, revision in PINS.items():
        require(git(ROOT, "rev-parse", "HEAD:" + path).decode().strip() == revision,
                f"root pin drift: {path}")
    metadata = json.loads((report / "broad-v5.json").read_text())
    require(metadata["candidate_tree"] == SING and metadata["core_candidate_tree"] == CORE,
            "successful candidate tree mismatch")
    require(metadata["selected_runtime_commits"] == 10 and metadata["deferred_runtime_commits"] == 3
            and len(metadata["upstream_code_paths"]) == 46,
            "candidate scope mismatch")
    spec = importlib.util.spec_from_file_location("broad", ROOT / "scripts/test-broad-v5-update.py")
    broad = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(broad)
    broad.apply(ROOT / ".build/hiddify-core", report)
    append_patch("hiddify-core", report / "broad-core.patch", metadata["core_baseline"], CORE)
    append_patch("hiddify-sing-box", report / "broad-v5.patch", metadata["baseline"], SING)
    lock = {"verified_run": RUN, "verified_commit": "9d5c077fab0f8e4cbc440002ba7ba8356f69201e",
            "version": "0.0.5-rc", "root_pins": PINS, "candidate": metadata}
    (ROOT / "patches/v5-candidate.lock.json").write_text(json.dumps(lock, indent=2, sort_keys=True) + "\n")
    (ROOT / "VERSION").write_text("0.0.5-rc\n")
    replace("scripts/build.sh",
            r'[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]',
            r'[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-rc[0-9]*)?$ ]]')
    replace("scripts/test.sh", "go test -mod=mod ", "go test -mod=readonly ")
    test = ROOT / "scripts/test.sh"
    test.write_text(test.read_text().rstrip() + '\nbash "$ROOT/scripts/test-v5-regressions.sh" --reuse-sources\n')
    replace(".github/workflows/build.yml", '          tag="v$version"\n',
            '          tag="v$version"\n'
            '          if [[ "$version" == *-rc* ]]; then\n'
            '            echo "Release candidate: retain build artifacts, do not publish a stable release"\n'
            '            exit 0\n'
            '          fi\n')
    replace("README.md", "VetrOFF-Core 0.0.4 is", "VetrOFF-Core 0.0.5-rc is")
    replace("README.md", "## Builds\n",
            """The latest published stable release remains 0.0.4. Main builds
the verified reduced 0.0.5-rc candidate from the same official root pins
and the appended Core/sing-box patches. See patches/v5-candidate.lock.json
for exact tested source provenance. Ten runtime commits are included;
three require the unavailable upstream Sing and Soar dependencies.
No native iOS or client integration is implied.

## Builds
""")
    replace("README.md",
            "Subsequent builds of a published version produce Actions artifacts.",
            "Subsequent builds of a published version and release-candidate versions produce Actions artifacts only.")
    for name in ("partial-v5-update.yml", "next-sing-box-audit.yml", "broad-v5-update.yml"):
        (ROOT / ".github/workflows" / name).unlink()
    # Keep immutable historical verification scripts in Git history.
    git(ROOT, "add", "-A", "--", "patches", "VERSION", "README.md",
        "scripts/build.sh", "scripts/test.sh", ".github/workflows")
    print("Prepared exact 0.0.5-rc patch stack; ordinary build gates required before push.")


if __name__ == "__main__":
    main(Path(sys.argv[1]).resolve())
