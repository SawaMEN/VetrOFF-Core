#!/usr/bin/env python3
"""Verify the exact reviewed Tailscale API sources, without promoting a gitlink."""
import json
from pathlib import Path
import subprocess
import sys

repo, report = map(Path, sys.argv[1:])
target = "4138b20dfb9f5852d043abd8bf9da6c29d5ee53b"

def git(*args):
    return subprocess.check_output(["git", "-C", str(repo), *args])

assert git("rev-parse", "HEAD").decode().strip() == target
# These are the exact target blobs inspected for the five late fork-side
# compatibility commits. Presence is source evidence, not runtime parity.
reviewed = {
    "net/tstun/wrap.go": ("7c4eaa4aedabef1bef236bed5af404a1366ab210", ["inputDevice atomic.Pointer", "returnPath atomic.Pointer", "t.returnPath.Load()"]),
    "net/tstun/input.go": ("bbd662bf78bc65ce903551705b454b4c95e442d6", ["t.inputDevice.Store(dev)", "SetReturnPath(", "state.returnPath.ReturnPackets("]),
    "wgengine/userspace_export.go": ("55e0d34a361072a28df1fe43cecf6bfb4c89a90e", ["SetReturnPath(", "e.tundev.SetReturnPath("]),
    "ipn/ipnlocal/local.go": ("412fe16ddb92cfd1f9d602444c81d747d0cbca04", ["func (b *LocalBackend) NetMapNoPeers()", "func (b *LocalBackend) NodeBackend()"]),
    "ipn/ipnlocal/local_export.go": ("1a7da3b2ab881c9f3616276b01e789a4ac1e010d", ["ExportMagicDNSHosts()"]),
    "tsnet/tsnet.go": ("0f71d0eed90080819425f395d4a9a87930f025f0", ["netmon.Hooks{", "DefaultRoute: s.DefaultRouteFunc", "ns.Handler = s.NetstackHandler", "s.lb.PeerForIP(ip)"]),
    "types/key/disco.go": ("2bbfa64fcb63118a4f3d9afdbb3b9f5ffde191be", ["func DiscoPrivateFromRaw32("]),
    "types/key/node.go": ("da996c59868d0c80f56d92e4f2f019f584127773", ["func (k NodePrivate) Raw32()", "func NodePrivateFromRaw32("]),
    "util/dnsname/dnsname.go": ("dd20c12eeea627d65600a1472b9f2d4013035904", ["func (f FQDN) Parent()"]),
    "wgengine/userspace.go": ("ac1cea0813ca4b2f7e54afeacaec419fa1850d77", ["conf.DERPAppName", "conf.ForceDiscoKey", "conf.OnDERPRecv"]),
    "wgengine/magicsock/magicsock.go": ("d75ed26111d805f32e1015d3f8b617d0cae13843", ["c.discoAtomic.Set(opts.ForceDiscoKey)", "c.onDERPRecv = opts.OnDERPRecv"]),
    "wgengine/magicsock/derp.go": ("8f757a505147aac79fb84825fae257f37e3a1767", ["c.onDERPRecv(regionID, dm.src, b[:n])"]),
    "derp/derpserver/derpserver.go": ("4deeef84bc8cd4874ddddbe6e414e8402624d06f", ["SetVerifyClientKeys(", "s.verifyClientKeys.Contains(clientKey)"]),
}
for path, (sha, markers) in reviewed.items():
    assert git("rev-parse", f"{target}:{path}").decode().strip() == sha, path
    assert git("hash-object", "--", path).decode().strip() == sha, path
    source = (repo / path).read_text()
    assert all(marker in source for marker in markers), path

report.parent.mkdir(parents=True, exist_ok=True)
report.write_text(json.dumps({
    "target": target,
    "reviewed_files": {path: sha for path, (sha, _) in reviewed.items()},
    "status": "source APIs verified; full behavior and divergent history still pending",
    "open_review": ["return-path and route semantics", "network hooks", "MagicDNS behavior",
                    "DERP receive ordering and authorization", "other divergent commits",
                    "combined build and platform tests"],
}, indent=2) + "\n")
print(f"Verified {len(reviewed)} exact Tailscale source blobs")
