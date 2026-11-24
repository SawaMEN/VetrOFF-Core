#!/usr/bin/env python3
"""Verify exported integration evidence without changing any source or pin."""
import hashlib
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest

MODULES = ("hiddify-core", "hiddify-sing-box", "hiddify-sing-box-test")
TESTS = (
    "TestH_TUICUDPRelayMode", "TestH_ShadowsocksXrayMethodNames",
    "TestH_ShadowsocksXrayMethodNamesMatchCiphers",
    "TestH_Shadowsocks2022ConcurrentFirstPayload",
    "TestH_Shadowsocks2022HandshakeFailure",
    "TestH_Shadowsocks2022HandshakeCancellation",
    "TestH_ShadowsocksLegacyKeepsEarlyHandshake",
    "TestH_RealityShortIDTooLong", "TestH_UTLSFingerprintVersions",
    "TestH_MTUProbeEchoRequest", "TestH_MTUProbeFilter",
    "TestH_MTUProbeAnswerTooBig", "TestH_AwgReservedBytes",
)
PINS = {
    "replace/MasterDnsVPN": "a6b693698ee8246ca415a994bb6da40d66dddba7",
    "replace/sing": "b40ea077780dbc1babef7d035d4b0fea8d9ad6f2",
    "replace/soar": "e18587bc9fb62f326124d1f3eb0ceb31708bd077",
    "replace/tailscale": "4138b20dfb9f5852d043abd8bf9da6c29d5ee53b",
}
SOURCE_PINS = {
    "github.com/amnezia-vpn/amneziawg-go": {
        "path": "github.com/hiddifydeveloper/amneziawg-go",
        "version": "v0.0.0-20261001141816-70b344f2f78b",
    },
    "github.com/sagernet/sing-openvpn": {
        "path": "github.com/sagernet/sing-openvpn",
        "version": "v0.0.0-20260903200517-e060dda5b1f1",
    },
}
REQUIRED = {"conduit_pairing_id", "fallback_address", "final_mask",
            "storage_dialer", "smart_pool", "keep_top", "mtu_discovery"}


def check(condition, message):
    if not condition:
        raise ValueError(message)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(root, filename):
    return json.loads((root / filename).read_text())


def require_pass(root, filename, names):
    log = (root / filename).read_text()
    for name in names:
        check(re.search(r"(?m)^\s*--- PASS: " + re.escape(name) + r" \(", log),
              f"{filename}: missing PASS for {name}")


def schema_contract(schema):
    check(isinstance(schema, dict), "schema is not an object")
    names, refs = set(), set()

    def walk(node):
        if isinstance(node, dict):
            properties = node.get("properties", {})
            if isinstance(properties, dict):
                names.update(properties)
            if isinstance(node.get("$ref"), str):
                refs.add(node["$ref"])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(schema)
    check(REQUIRED <= names, "missing schema properties: " + str(sorted(REQUIRED - names)))
    for ref in refs:
        if not ref.startswith("#/"):
            continue
        node = schema
        for segment in ref[2:].split("/"):
            token = segment.replace("~1", "/").replace("~0", "~")
            if isinstance(node, list):
                check(token.isdigit() and int(token) < len(node), f"broken ref {ref}")
                node = node[int(token)]
            else:
                check(isinstance(node, dict) and token in node, f"broken ref {ref}")
                node = node[token]
    return {"properties": len(names), "references": len(refs)}


def verify(root):
    p = load(root, "combined-candidate-provenance.json")
    for key in ("core_tree", "sing_box_tree", "ray2sing_patched_tree"):
        check(re.fullmatch(r"[0-9a-f]{40}", p[key]), f"invalid tree: {key}")
    for path, revision in PINS.items():
        check(p["candidate_dependency_pins"].get(path) == revision, f"unexpected pin {path}")
    overlay = p["materialized_dependency_overlays"]["replace/MasterDnsVPN"]
    required = p["required_dependency_overlays"]["replace/MasterDnsVPN"]
    check(required["base"] == PINS["replace/MasterDnsVPN"] and required["required_for_promotion"],
          "missing required MasterDNS overlay policy")
    check(overlay["patch"] == "combined-masterdns-readiness.patch", "unexpected overlay patch")
    check((root / overlay["patch"]).stat().st_size > 0, "empty overlay")
    check(re.fullmatch(r"[0-9a-f]{40}", overlay["tree"]) and
          overlay["tree"] == (root / "combined-masterdns-tree.txt").read_text().strip(),
          "overlay tree mismatch")
    callers = p["tailscale_caller_adjustments"]
    check(len(callers) == 6 and len({c["path"] for c in callers}) == 6,
          "incomplete Tailscale caller evidence")
    check(all(re.fullmatch(r"[0-9a-f]{40}", c["candidate_blob"]) for c in callers),
          "invalid caller blob")

    graph = load(root, "combined-effective-dependencies.json")
    check(graph["errors"] == {}, "dependency graph errors")
    check(graph["reviewed_cross_module_pins"] == SOURCE_PINS, "reviewed source pins missing")
    check(set(graph["module_counts"]) == set(MODULES) and
          all(n > 0 for n in graph["module_counts"].values()), "incomplete module graphs")
    check(not set(SOURCE_PINS) & set(graph["shared_source_differences"]),
          "reviewed source pins still diverge")
    for module in MODULES:
        for filename in ("go.mod", "go.sum"):
            name = f"combined-effective-{module}.{filename}"
            check(sha(root / name) == graph["manifest_sha256"][module][filename],
                  f"manifest digest mismatch: {name}")
    require_pass(root, "combined-core-tests.log",
                 ("TestSetInboundIPv4OnlyKeepsPlatformRoutePolicy", "TestShouldEnableIPv6", "TestHotReload"))
    require_pass(root, "combined-core-warp-mtu-policy.log", ("TestVetrOFFWarpMTUDiscoveryPolicy",))
    for context in ("core", "sing"):
        require_pass(root, f"combined-{context}-network-regressions.log", TESTS)
    schema = schema_contract(load(root, "combined-schema.json"))
    schema["sha256"] = sha(root / "combined-schema.json")
    return {
        "status": "evidence complete; not authorization to promote",
        "network_pass_markers": len(TESTS) * 2,
        "schema": schema,
        "remaining_conflicts": {"core": p["unresolved_core"], "sing": p["unresolved_sing_box"]},
        "remaining_source_differences": sorted(graph["shared_source_differences"]),
        "masterdns_overlay_sha256": sha(root / overlay["patch"]),
        "stable_promotion": False,
    }


class EvidenceTests(unittest.TestCase):
    def test_schema(self):
        properties = {name: {} for name in REQUIRED}
        schema = {"properties": properties, "$ref": "#/properties/keep_top"}
        self.assertEqual(schema_contract(schema), {"properties": 7, "references": 1})
        schema["$ref"] = "#/properties/missing"
        with self.assertRaises(ValueError):
            schema_contract(schema)
        del schema["$ref"]
        del properties["mtu_discovery"]
        with self.assertRaises(ValueError):
            schema_contract(schema)

    def test_pass_markers(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "log").write_text("--- PASS: TestFoo (0.00s)\n")
            require_pass(root, "log", ("TestFoo",))
            with self.assertRaises(ValueError):
                require_pass(root, "log", ("TestFooBar",))
            (root / "log").write_text("--- SKIP: TestFoo (0.00s)\n")
            with self.assertRaises(ValueError):
                require_pass(root, "log", ("TestFoo",))

    def test_digest(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "file"
            path.write_bytes(b"candidate")
            first = sha(path)
            path.write_bytes(b"modified")
            self.assertNotEqual(first, sha(path))


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        unittest.main(argv=[sys.argv[0]])
    elif len(sys.argv) == 2:
        try:
            print(json.dumps(verify(Path(sys.argv[1])), indent=2, sort_keys=True))
        except (ValueError, KeyError, OSError, TypeError) as exc:
            sys.exit(f"Candidate evidence incomplete: {exc}")
    else:
        sys.exit("Usage: verify-candidate-evidence.py REPORT_DIR | --self-test")
