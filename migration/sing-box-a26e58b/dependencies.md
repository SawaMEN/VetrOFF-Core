# Nested dependency resolutions for sing-box a26e58b

The Core target uses sing-box a26e58b376987a3305b201ec87fbc8918f620ad3. In the three-way merge, use the following upstream gitlinks. The VetrOFF revisions have no unique non-merge patches for these two dependencies, verified with Git patch equivalence.

| Path | VetrOFF revision | Upstream revision |
| --- | --- | --- |
| replace/MasterDnsVPN | 62e94f1515d4a5d3133789c90d8b224975b77ff9 | a6b693698ee8246ca415a994bb6da40d66dddba7 |
| replace/soar | 71ae9fb0b1ac9d99b8a6f9b5e254b92fe328f179 | e18587bc9fb62f326124d1f3eb0ceb31708bd077 |

`replace/sing` and `replace/tailscale` still have fork-only changes and need reconciliation. With the two gitlinks above staged, 164 paths remain conflicted. This records source reconciliation. Compilation of the updated pair is pending.
