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

Run 45444977114 confirmed the WireGuard/AWG tests and initial Tailscale
caller checks, then failed compiling the endpoint (TunDevice renamed to Tun,
ExportIPStack now returns sing-tun.Go). The probe adapts the endpoint without
replacing its fork-specific HTTP transport or lifecycle. It also initializes
the exported engine used by filtering/packet I/O, restores the full system
binding/default-route hook, and explicitly enables both with_tailscale and
with_tailcat build tags. These changes are not yet an approved dependency
migration; compilation and runtime regressions still gate promotion.

Run 45445885849 passed the transport race tests and stable build, but the
explicit Tailcat tag uncovered an inbound lifecycle mismatch. The probe now
restores the exact upstream scoped Tailcat inbound lifecycle (including
cleanup registration before node startup) and asserts the expected Tailcat
source files are included by the build tags. The outbound legacy lifecycle
is retained; it uses the existing outbound adapter interface. No stable
source pin has changed. Candidate runtime tests still gate promotion.

### Documentation reconciliation

Four documentation conflicts (Soar and FinalMask, English and Persian) now
restore upstream project attribution while retaining the complete locked
compatibility text. Soar is byte-identical to upstream after insertion of
credits. FinalMask retains its original wording and formatting; duplicate
related sections can be cleaned once the associated transport behavior is
fully verified. This is documentation-only and does not change protocols.

### Sequential DNS reconciliation

The migration candidate combines the new upstream rating table and detour
readiness check with the fork's `sequentialState` and readiness contract.
An explicitly positive `error_ttl` retains time-based ordering; omitting it
selects the upstream rating policy (no penalty when every server fails).
This is a deliberate compatibility decision, not a stable behavior change.
Existing tests are retained and eight upstream rating, detour, and deadline
tests are added. A scoped race test checks the DNS transport and rating
packages before the disposable checkout is rebuilt for Tailscale.

### MasterDNS resolver admission

The reviewed MasterDNS caller verifies actual tunnel MTU probes after the
ordinary resolver pretest. The locked dependency and target have seven
file differences despite divergent histories. The scoped CI imports only
the four reviewed library/probe/test files, preserving the locked example
configs and documentation. Both the library and outbound package are tested
with the race detector. The nested dependency gitlink remains unresolved
until its full migration and compatibility are confirmed. No stable pin or
release code changes in this stage.

### MasterDNS and WARP follow-up

Run 45453921715 verified 117 reviewed resolutions and passed DNS, transport,
and MasterDnsVPN library race tests. The outbound probe failed to compile
because its test overlay omitted the already-reviewed `common/dnspool/probe.go`.
The overlay now includes that file explicitly. For WARP, the candidate uses
upstream host classification with every locked range retained, and records
the obsolete scanner as a reviewed deletion (the fork's sole change there
was equivalent WaitGroup syntax). The actual scanning implementation now
lives in edgescan. Scoped tests cover the shared engine and WARP host
classification, not the complete WARP endpoint migration. The original
stable dependency pins and source patch series are unchanged.

### MASQUE packet and scanner lifecycle reconciliation

Run 45454746442 passed all scoped checks, including MasterDNS, edgescan,
WARP classification and Tailscale. The MASQUE outbound resolution restores
upstream scanner shutdown and synchronized scanner publication, and the
IP-only UDP writer that avoids a netstack panic on domain destinations.
The locked peer metadata API (`IsDomain`) remains unchanged. A reverse
transformation asserts that all other original outbound behavior survives.
The isolated regression overlays only the packet method and upstream test
on the locked scanner. Full scanner lifecycle and WARP endpoint integration
still require a combined candidate build; the stable pins are unchanged.

### Combined WARP candidate and MasterDNS docs

Run 45458222414 verified 120 resolutions and passed the earlier scoped
checks; MASQUE's packet test failed at device creation because the test
was built without `with_gvisor`. The test now explicitly enables it.
The combined candidate gate copies the complete reviewed WARP, MASQUE,
edge scanner and supporting packages and compiles with gVisor, MASQUE
and AWG enabled. The adapter detour helper uses a probe-only readiness
compatibility adjustment; remaining API mismatches must be addressed
before integration. The compile-only pass is followed by the actual UDP
regression test. The two MasterDNS translations now document the previously
verified resolver admission behavior and restore attribution. No stable
source pins are changed.

### WARP dependency completeness

The combined WARP probe now includes the upstream HeldLazy interface and
reviewed URLTestHistory.LastGood field, both required by detour handling.
The PathStatus adaptation is a reviewed clean-tree adjustment rather than
a temporary test edit. A regression test checks legacy IsReady outbounds
and detailed Status outbounds in a single path. The Tailscale gate starts
from its own locked source checkout and runs independently of failures in
preceding package probes once audit and Go setup have succeeded. Two smart
DNS pool documentation conflicts restore attribution, preserving all other
bytes. Stable pins remain unchanged; combined WARP tests are still pending.

### MasterDNS session readiness race

Audit 45461276725 verified 124/166 sing-box conflicts and the independent
Tailscale probe passed. The repeated MasterDnsVPN TCP library test exposed
the same sessionReady race previously seen in SOCKS5. The candidate patch
replaces the readiness flag with atomic.Bool across all six affected files
(including tests), and publishes true after session initialization rather
than before compression policy application. Both source revisions and the
complete reference inventory are checked before writing. CI repeats the
library tests three times and exercises internal session/runtime tests.
The WARP/MASQUE checks now run after successful DNS checks even when the
independent MasterDNS test fails. This is a candidate-only patch, not a
claim that every concurrent session field is safe; stable pins unchanged.
