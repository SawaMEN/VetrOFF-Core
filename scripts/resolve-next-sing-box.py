#!/usr/bin/env python3
"""Resolve the four identified v5 update conflicts without changing release pins."""
import importlib.util
import re
from pathlib import Path
import subprocess
import tempfile

BASELINE = "f76b0611b09245e2f636692487b774c03a18d6c2"
TARGET = "1267fe6aac8d11f3a562e8feecad11d11f48f965"
BLOBS = {
    "adapter/inbound.go": "3aff8ef9f6489e228295bf471d1781bc966f27e5",
    "docs/hiddify/app_considerations.md": "a95258c6c23a7d50c275893a6d071b27735dd38a",
    "protocol/masque/outbound.go": "9cb087ea2c629ddc50065fbafebe06d0b8b34c87",
    "transport/masque/h_connect.go": "9145af6cbb38af340e899f185cbef166bdee8dea",
}


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args])


def require(condition, message):
    if not condition:
        raise ValueError(message)


def once(text, old, new):
    require(text.count(old) == 1, f"unexpected anchor: {old[:100]!r}")
    return text.replace(old, new, 1)


def format_go(text):
    return subprocess.check_output(["gofmt"], input=text.encode()).decode()


def resolve(repo, baseline, tentative, conflicts, report):
    require(baseline == BASELINE, "released baseline drift")
    require(set(conflicts) == set(BLOBS), f"unexpected conflict paths: {conflicts}")
    for path, oid in BLOBS.items():
        require(git(repo, "rev-parse", TARGET + ":" + path).decode().strip() == oid,
                f"upstream blob drift: {path}")

    def original(path):
        return git(repo, "show", baseline + ":" + path).decode()

    files = {}
    path = "adapter/inbound.go"
    text = original(path)
    # Keep all fork metadata, and extend the context used by new route rules.
    pattern = r"(?m)^(\s*RealOutbound\s+string[^\n]*)$"
    text, count = re.subn(pattern, lambda m: m.group(1) +
                         "\n\tIPv6RouteLookahead bool // H: route rule lookahead guard", text)
    require(count == 1, "inbound context anchor drift")
    files[path] = format_go(text).encode()

    path = "protocol/masque/outbound.go"
    text = original(path)
    # Preserve fork peer metadata and packet/lifecycle adaptations.
    require("peer.IsDomain()" in text, "fork peer API missing")
    pattern = r"(?m)^(\s*if _, err := tunnelMode\(options\); err != nil \{[^\n]*\n\s*return nil, err\n\s*\})$"
    text, count = re.subn(pattern, lambda m: m.group(1) +
                         "\n\tif err := validateNoisePresets(options.Scanner); err != nil {\n\t\treturn nil, err\n\t}", text)
    require(count == 1, "constructor anchor drift")
    text = once(text, "\t\ttunnel.SetFailover(scanner.OnConnected, scanner.NextEndpoint)\n",
                "\t\ttunnel.SetFailover(scanner.OnConnected, scanner.NextEndpoint)\n"
                "\t\ttunnel.SetTLSConfigFor(scanner.noises.TLSConfig)\n")
    files[path] = format_go(text).encode()

    path = "transport/masque/h_connect.go"
    text = original(path)
    # Keep the existing tunnel lifecycle and capture the callback under
    # the same mutex as the endpoint, calling it outside the lock.
    text = once(text, '\tE "github.com/sagernet/sing/common/exceptions"\n',
                '\tE "github.com/sagernet/sing/common/exceptions"\n'
                '\t"github.com/sagernet/sing/common/tls"\n')
    text = once(text, "\te.endpointAccess.Unlock()\n\tconnectURI := e.options.ConnectURI\n",
                "\ttlsConfigFor := e.options.TLSConfigFor\n"
                "\ttlsConfig := e.options.TLSConfig\n"
                "\te.endpointAccess.Unlock()\n"
                "\tif tlsConfigFor != nil {\n\t\ttlsConfig = tlsConfigFor(http2)\n\t}\n"
                "\tconnectURI := e.options.ConnectURI\n")
    text = once(text, "connectTunnel(ctx, e.options.Dialer, e.options.TLSConfig,",
                "connectTunnel(ctx, e.options.Dialer, tlsConfig,")
    require("func (e *Tunnel) SetTLSConfigFor(" not in text, "duplicate callback setter")
    text = text.rstrip() + """

// SetTLSConfigFor sets the callback before tunnel startup.
func (e *Tunnel) SetTLSConfigFor(tlsConfigFor func(http2 bool) tls.Config) {
	e.endpointAccess.Lock()
	defer e.endpointAccess.Unlock()
	e.options.TLSConfigFor = tlsConfigFor
}
"""
    files[path] = format_go(text).encode()

    path = "docs/hiddify/app_considerations.md"
    text = original(path)
    require("Next upstream integration" in text, "missing fork integration notes")
    files[path] = (text.rstrip() + """

## Subsequent v5 update (candidate only)

The original project's [integration notes at 1267fe6](https://github.com/hiddify/hiddify-sing-box/blob/1267fe6aac8d11f3a562e8feecad11d11f48f965/docs/hiddify/app_considerations.md)
describe the next target. Review the balancer lifecycle, log-level hot reload,
IPv6 route lookahead, iOS memory integration and scanner changes before
adopting them in the app. The required sing and Soar revisions must be
fetchable before the full candidate can be built. These notes do not
supersede the released fork's integration requirements.
""").encode()

    spec = importlib.util.spec_from_file_location("stage", Path(__file__).with_name("stage-final-conflicts.py"))
    stage = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(stage)
    tree = stage.stage(repo, tentative, files, {})
    for path in files:
        require(git(repo, "show", tree + ":" + path) == files[path], f"staging mismatch: {path}")
        dest = report / "resolved" / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(files[path])
    (report / "reviewed-update.patch").write_bytes(
        git(repo, "diff", "--binary", "--full-index", baseline, tree))
    return tree, sorted(files)
