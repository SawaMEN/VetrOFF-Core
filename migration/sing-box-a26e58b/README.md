# Hiddify sing-box migration checkpoint

The audited VetrOFF snapshot is `8195cf4cc09c68836d9799e6ae7eb2da838f4d46`.
The patch base is original Hiddify sing-box
`a26e58b376987a3305b201ec87fbc8918f620ad3`, the gitlink in matching Hiddify
Core. There are 45 unresolved paths.

Recreate the three-way merge from the audited snapshot and stage the gitlinks
and identical original files listed in [dependencies.md](dependencies.md).
In a separate index rooted at the exact original sing-box target, apply
`gzip -dc sing-box-resolutions.patch.gz | git apply --cached --whitespace=nowarn`.
Take the 128 paths in `resolved-paths.txt` from that index. Paths absent from
the patched index should be deleted. Compare the result with
`resolved-index.txt`. Replay was verified against the saved index entries.

This checkpoint reconciles monitor reload and staged tests with the fork's
queue overflow handling and history snapshots. It retains the Xray, VMess
and native uTLS replacements, legacy DNS group ordering with explicit
`error_ttl`, and the VetrOFF XHTTP, VLESS and XDrive transport behavior.

The monitoring, balancer, DNSTT, SSH and Shadowsocks suites passed. Route
tests compiled and two pacing assertions failed. The box-level XDrive check
is currently blocked by the unresolved WireGuard transport. The earlier
local-storage XDrive round trip and transport-level race checks passed.
