# Hiddify sing-box migration checkpoint

The VetrOFF source is the audited snapshot `8195cf4cc`, and the original base
for the resulting patch is Hiddify sing-box `a26e58b376987a3305b201ec87fbc8918f620ad3`,
the gitlink in the matching original Core. This checkpoint resolves 67 additional
conflicts and records 8 supporting edits. 85 unresolved paths remain.

Recreate the three-way merge from the audited snapshot. Stage the dependency
gitlinks and identical original files listed in [dependencies.md](dependencies.md).
In a separate index rooted at the exact original sing-box target, apply
`gzip -dc sing-box-resolutions.patch.gz | git apply --cached`. Read the 75 paths
in `resolved-paths.txt` from that index and stage them in the merge worktree.
Paths absent from the patched index should be deleted. Check the resulting
entries against `resolved-index.txt`. These steps were verified locally.

The candidate retains the vendored Xray, VMess and native uTLS replacements in
both Go modules. Legacy DNS group configurations that explicitly set
`error_ttl` still use time-based failure ordering. The default uses the
original's current scoring.

Local checks passed for the adapters, DNS transport and pools, MasterDNS,
Soar, FinalMask, and focused TLS fingerprint and REALITY tests. The broader TLS
suite observed a randomized ECH handshake failure. XHTTP still has merge
conflicts, which block the XDrive integration run. The full Core build and
release are pending.
