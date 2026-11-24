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

## Tunnel helper reconciliation

The current-baseline audit additionally reconciles MTU search, peer fallback
and the device stack. The audit workflow runs a scoped race-detector check
of these three reviewed files with locked dependencies. This is not a full
WireGuard/AWG migration or a test of the combined upstream dependency set.

The audit also verifies the complete reviewed transport packages (including
cleanly merged tests) against the selected WireGuard-go and AWG revisions.
WireGuard-go advances from `e678616` to `32f4b53` only after an ancestry check;
AWG already uses `a2ae165`. Other dependencies remain locked for this scoped
check. Tailscale requires separate reconciliation: its candidate and fork
histories diverge, so it is not automatically replaced. This does not certify
the complete upstream candidate or authorize promotion of stable pins.

### Transport test follow-up

Run 45442636110 confirmed 109 reviewed conflicts but exposed an AWG test
constructor mismatch and a WireGuard Start/Close data race. The candidate
now serializes lifecycle initialization and shutdown, initializes the pause
manager once, avoids logging private IPC keys on error, and adjusts the
cleanly merged AWG test to use the live path. Clean compatibility adjustments
are reported separately from conflict counts. Full transport CI must pass
before this migration stage is marked complete.

### Tailscale compatibility probe

The transport audit at run 45443273791 passed including WireGuard and AWG
race tests. The Tailscale candidate uses original revision `4138b20` instead
of locked `1f5488c`. The probe restores upstream caller APIs for Taildrop,
Tailcat and the TUN filter, records the exact caller patch and dependency
history, and tests with other dependencies locked. This is a compatibility
probe only: remaining fork-only behavior and dependency combinations must
be reviewed before the Tailscale gitlink is resolved or stable pins change.
