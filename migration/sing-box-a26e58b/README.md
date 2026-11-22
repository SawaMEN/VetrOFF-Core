# Hiddify sing-box migration checkpoint

The audited VetrOFF snapshot is `8195cf4cc`. The patch base is original Hiddify
sing-box `a26e58b376987a3305b201ec87fbc8918f620ad3`, the gitlink in matching
Hiddify Core. There are 73 unresolved paths.

Recreate the three-way merge from the audited snapshot and stage the gitlinks
and identical original files listed in [dependencies.md](dependencies.md).
In a separate index rooted at the exact original sing-box target, apply
`gzip -dc sing-box-resolutions.patch.gz | git apply --cached`. Read the 90 paths
in `resolved-paths.txt` from that index and stage them in the merge worktree.
Paths absent from the patched index should be deleted. Compare the result
with `resolved-index.txt`. Patch replay was verified locally.

The candidate uses the VetrOFF Xray, VMess and native uTLS replacements.
A DNS group that explicitly sets `error_ttl` uses time-based failure ordering.
XHTTP retains validation of the configured mode, nested download settings,
and cancellation and error reporting during stream setup. VLESS retains the
VetrOFF XDrive constraints, preconnect, reverse and Vision settings.

Targeted tests passed for XHTTP, XDrive, gRPC, VLESS and MTU search. An XDrive
client and server exchanged data through local storage after cancellation of
the dial context. The full box-level XDrive test is blocked by unresolved
merge conflicts in the monitoring package. The full Core build and release
are pending.
