# Nested dependency resolutions for sing-box a26e58b

Core targets sing-box `a26e58b376987a3305b201ec87fbc8918f620ad3`.
Stage these original-project gitlinks in the three-way merge.

| Path | VetrOFF revision | Original revision | Verification |
| --- | --- | --- | --- |
| replace/MasterDnsVPN | `62e94f1515d4a5d3133789c90d8b224975b77ff9` | `a6b693698ee8246ca415a994bb6da40d66dddba7` | No unique non-merge patches in the VetrOFF revision |
| replace/soar | `71ae9fb0b1ac9d99b8a6f9b5e254b92fe328f179` | `e18587bc9fb62f326124d1f3eb0ceb31708bd077` | No unique non-merge patches in the VetrOFF revision |
| replace/sing | `e1f78e792da34ce99fe2279729dea3b0a09efa9d` | `b40ea077780dbc1babef7d035d4b0fea8d9ad6f2` | Original fix `dcc5d99` is an ancestor. The VetrOFF cached-connection regression test has identical content and passes under the race detector. |

In the new `replace/sing`, `go test -race ./common/bufio ./common/canceler ./protocol/socks`
passed for the two packages with tests. The SOCKS package compiled and reported no test files.

The three gitlinks are resolved. Apply the original versions of the 11 paths in
`identical-upstream-paths.txt`. In each, the two Go versions have the same lexemes,
including string literals and implicit semicolons, and differ only in whitespace.
The remaining 85 conflicted paths are listed in `pending-paths.txt`. The other source resolutions are recorded in `sing-box-resolutions.patch.gz`.
`replace/tailscale` remains pending. Some old compatibility shims are superseded by APIs in its
candidate revision (`NodeBackend.Self` and `Peers`, network monitor hooks, and the TUN return
path). The sing-box callers need to be checked against that revision, especially Taildrop,
Tailcat, and the TUN port.
