# Upstream migration

The migration target is Core `4e5a9a3` and its sing-box gitlink `a26e58b`.
The stable CLI still builds from the root gitlinks and patch series.
Do not point a stable build at this directory.

Two complementary checkpoints are available:

- [Core checkpoint](core-4e5a9a3/README.md): all historical Core conflicts resolved, 148 staged paths verified.
- [sing-box checkpoint](sing-box-a26e58b/README.md): 128 staged paths verified, 45 pending paths. This is a partial patch against the original target, not a complete combined tree.

The independent upstream-merge-audit workflow uses the **current stable**
source trees. It reported 38 Core and 65 sing-box unresolved paths after
reviewed resolutions on 2026-11-24. These counts refer to a different baseline
and must not be added to the historical checkpoint counts.

Next: reconcile the saved historical resolutions with the current locked
fork snapshots, resolve WireGuard/AWG and Tailscale, resolve remaining
conflicts, check dependency compatibility and routing tests, and run the
complete build matrix before updating any stable gitlinks or expected trees.

Checkpoint replay checks both saved Git tree IDs and selected index entries.
The sing-box tree ID refers only to the partial replay, not the full merge.
The verification does not compile the candidate.
