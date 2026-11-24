#!/usr/bin/env python3
"""Record and verify the effective Go graphs of the disposable integration tree."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

MODULES = {
    "hiddify-core": ".",
    "hiddify-sing-box": "hiddify-sing-box",
    "hiddify-sing-box-test": "hiddify-sing-box/test",
}


def json_stream(raw):
    decoder = json.JSONDecoder()
    modules = {}
    raw = raw.lstrip()
    while raw:
        entry, end = decoder.raw_decode(raw)
        path = entry["Path"]
        if path in modules or entry.get("Error"):
            raise ValueError(f"Invalid or duplicate module: {path}")
        modules[path] = entry
        raw = raw[end:].lstrip()
    if not modules or sum(bool(m.get("Main")) for m in modules.values()) != 1:
        raise ValueError("Expected a nonempty graph with exactly one main module")
    return modules


def local_path(path, directory, root):
    target = (directory / path).resolve()
    if not target.is_relative_to(root) or not (target / "go.mod").is_file():
        raise ValueError(f"Invalid local replacement: {path}")
    return target.relative_to(root).as_posix()


def normalize_replacement(entry, directory, root):
    if not entry:
        return None
    if not entry.get("Version"):
        return {"local": local_path(entry.get("Dir") or entry["Path"], directory, root)}
    return {"path": entry["Path"], "version": entry["Version"]}


def verify_graph(graph, manifest, directory, root):
    expected = {
        (entry["Old"]["Path"], entry["Old"].get("Version", "")):
        normalize_replacement(entry["New"], directory, root)
        for entry in manifest.get("Replace", [])
    }
    result = {}
    for path, entry in sorted(graph.items()):
        if entry.get("Main"):
            continue
        version = entry.get("Version", "")
        selected = expected.get((path, version), expected.get((path, "")))
        actual = normalize_replacement(entry.get("Replace"), directory, root)
        if actual != selected:
            raise ValueError(f"{path}@{version}: expected replacement {selected}, got {actual}")
        result[path] = {"version": version, "replace": actual}
    return result


def differences(graphs):
    paths = sorted(set().union(*(set(graph) for graph in graphs.values())))
    result = {}
    for path in paths:
        present = {name: graph[path] for name, graph in graphs.items() if path in graph}
        if len(present) > 1 and len({json.dumps(v, sort_keys=True) for v in present.values()}) > 1:
            result[path] = present
    return result


def collect(root, report):
    root, report = root.resolve(), report.resolve()
    if root.parent != report.parent or not root.name.startswith("combined-candidate."):
        raise ValueError("Expected disposable combined candidate beside audit report")
    graphs, errors, snapshots = {}, {}, {}
    env = dict(os.environ, GOWORK="off", GOTOOLCHAIN="local")
    for name, relative in MODULES.items():
        directory = root / relative
        prefix = report / ("combined-effective-" + name)
        try:
            initial = report / (name + "-go.mod.candidate")
            manifest = json.loads(subprocess.check_output(
                ["go", "mod", "edit", "-json", str(initial)], cwd=directory, env=env))
            proc = subprocess.run(["go", "list", "-mod=mod", "-m", "-json", "all"],
                                  cwd=directory, env=env, capture_output=True, check=False)
            Path(str(prefix) + ".graph.jsonstream").write_bytes(proc.stdout)
            Path(str(prefix) + ".stderr.log").write_bytes(proc.stderr)
            if proc.returncode:
                raise ValueError(f"go list failed with exit {proc.returncode}")
            graphs[name] = verify_graph(json_stream(proc.stdout.decode()), manifest, directory, root)
        except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
            errors[name] = str(exc)
        finally:
            snapshots[name] = {}
            for filename in ("go.mod", "go.sum"):
                source = directory / filename
                if source.is_file():
                    data = source.read_bytes()
                    Path(str(prefix) + "." + filename).write_bytes(data)
                    snapshots[name][filename] = hashlib.sha256(data).hexdigest()
    report_data = {
        "status": "failed" if errors else "replacement checks passed; version differences require review",
        "module_counts": {name: len(graph) for name, graph in graphs.items()},
        "manifest_sha256": snapshots,
        "shared_module_differences": differences(graphs),
        "errors": errors,
    }
    (report / "combined-effective-dependencies.json").write_text(
        json.dumps(report_data, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report_data, indent=2, sort_keys=True))
    return 1 if errors else 0


class GraphTests(unittest.TestCase):
    def test_stream(self):
        self.assertEqual(set(json_stream('{"Path":"main","Main":true}\n{"Path":"dep","Version":"v1.0.0"}')), {"main", "dep"})
        for raw in ('', '{"Path":"dep"}', '{"Path":"main","Main":true} {"Path":"main"}'):
            with self.assertRaises(ValueError):
                json_stream(raw)

    def test_local_replacement(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            directory = root / "module"
            directory.mkdir()
            dependency = root / "dep"
            dependency.mkdir()
            (dependency / "go.mod").touch()
            manifest = {"Replace": [{"Old": {"Path": "dep"}, "New": {"Path": "../dep"}}]}
            graph = {"dep": {"Version": "v1.0.0", "Replace": {"Path": "../dep", "Dir": str(dependency)}}}
            self.assertEqual(verify_graph(graph, manifest, directory, root)["dep"]["replace"], {"local": "dep"})
            with self.assertRaises(ValueError):
                verify_graph({"dep": {"Version": "v1.0.0"}}, manifest, directory, root)
            with self.assertRaises(ValueError):
                local_path("../..", directory, root)

    def test_version_specific_replacement(self):
        root = Path.cwd()
        manifest = {"Replace": [{"Old": {"Path": "dep", "Version": "v1.0.0"},
                                "New": {"Path": "fork", "Version": "v1.1.0"}}]}
        graph = {"dep": {"Version": "v2.0.0"}}
        self.assertIsNone(verify_graph(graph, manifest, root, root)["dep"]["replace"])
        with self.assertRaises(ValueError):
            verify_graph({"dep": {"Version": "v1.0.0"}}, manifest, root, root)

    def test_differences(self):
        graphs = {"core": {"dep": {"version": "v1"}, "only": {}},
                  "sing": {"dep": {"version": "v2"}}}
        self.assertEqual(set(differences(graphs)), {"dep"})


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        unittest.main(argv=[sys.argv[0]])
    elif len(sys.argv) == 3:
        sys.exit(collect(Path(sys.argv[1]), Path(sys.argv[2])))
    else:
        sys.exit("Usage: verify-combined-dependencies.py candidate report | --self-test")
