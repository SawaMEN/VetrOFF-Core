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

### DNSTT and Shadowsocks reconciliation

Audit 45464438820 passed the MasterDNS test stage after the readiness fix.
The DNSTT outbound conflict retains the compact fork implementation: the
upstream-only fields and duration helper have no code references in the
reviewed package, and the remainder is equivalent after normalization.
Shadowsocks single-user inbound adopts the upstream consolidated obfs
server without changing its scoped lifecycle, normalization or service
creation. The new independent package test gate exercises DNSTT and
Shadowsocks with the race detector and restores its temporary overlays.
The complete candidate still requires successful CI and full integration.
Stable pins are unchanged.

### SSH regression coverage

Run 45465371861 verified 126 resolutions and passed MasterDNS again. Its
isolated MASQUE UDP regression passed; DNSTT/Shadowsocks and combined WARP
steps failed (their logs were still unavailable at this review). The SSH
conflict restores all 21 upstream tests, including the 12 fork tests,
with compatible inbound cleanup and a bounded lifetime for the blocked
read fixture. The scoped protocol test now includes SSH. Combined WARP
is attempted independently of the isolated MASQUE result once the shared
edge scanner stage passes. Stable dependencies are not changed.

### Candidate test harness corrections

Run 45465371861 passed DNSTT under the race detector. Shadowsocks failed
before compilation because the overlay omitted the local readahead package;
include it and wholepacket, and test both helpers. The combined WARP gate
failed at shell parsing (a duplicated tail inside a quoted test regex).
Reconstruct the commands with exact quoted regexes and add a bash/Python
syntax preflight before the expensive merge audit. Scoped package overlays
now restore module manifests and only restore directories actually touched.
The prior isolated MASQUE failure was a Go module proxy transport error,
not a protocol failure; the subsequent run passed that regression. No
release source or stable dependency pins were modified.

### Clean-merge semantic compatibility

Run 45466889896 confirmed 127 resolved conflicts, stable build and all
previous scoped checks. The readahead and wholepacket tests passed. The
SSH regressions exposed three missing server-side fixes (network policy,
malformed forward handling, and host-key algorithms); restore the upstream
implementation with the fork's scoped cleanup. Shadowsocks lacked the
shared obfsServer helper because a clean merge retained the fork's older
helper and multi/relay callers; reconcile the coupled package and tests.
The AWG endpoint and helper each defined singlePeer; keep the fork's exact
validation in the endpoint and the upstream helper without duplication.
These are additional clean-tree compatibility adjustments, not new conflict
counts. Candidate verification remains required; stable pins unchanged.

### VLESS and WARP runtime verification

Audit 45468473285 passed all existing gates (127 resolutions, eight
clean-tree adjustments), including Shadowsocks, SSH and combined WARP
compilation. Stable build and checkpoint replay also passed. The combined
WARP gate now executes its package regression tests. VLESS reconciliation
preserves fork XDrive, reverse, testpre and testseed, close-once encryption
and cancellation, and adopts upstream early Vision validation, kTLS
compatibility metadata and scoped cleanup. Explicit Close shares that
cleanup. The outbound is tested with locked fork dependencies and new
upstream close tests. Full candidate integration remains required; stable
pins unchanged.

### FinalMask reconciliation and WARP diagnostics

Audit 45471276316 confirmed 128 conflicts, VLESS race tests and stable
build. WARP compiled; two runtime assertions expected the old generic
address error instead of the fork's more precise IPv4/IPv6 diagnostic.
Keep the validation and update only the two exact expectations. Resolve
six FinalMask paths: preserve compact fragment ranges, domain checks and
modern iteration; adopt the upstream IPv4-only test socket helper and
larger UDP hop test pool. Run the full FinalMask package with the race
detector using the existing locked Xray dependency. No stable pins are
changed; the new candidate still requires CI verification.

### Core packaging and LAN proxy authorization

Audit 45473321070 passed the reviewed-source gate after the FinalMask
import-grouping assertion was corrected. The next Core candidate resolves
five additional conflicts: Docker and OpenWrt package version metadata,
the Docker entrypoint, and both inbound port files. The original package
versions advance to 5.9.15 in the candidate only. The entrypoint keeps
exec-based process handling and the upstream opt-in router argument.
The port configuration keeps the fork's LanSharingPassword Go field
and the regression covering its existing lan-sharing-password JSON key;
all upstream port policy tests remain. The HiddifyOptions struct and
other callers still require reconciliation, so no integrated Core build
is claimed. The expected current-baseline Core conflict count is 33
(previously 38), subject to CI confirmation. Stable pins are unchanged.

### Core option schema and KeepTop configuration

Run 45473904930 passed the five-conflict Core source gate. The next
candidate retains the fork's complete option schema (including the single
LAN password JSON field) and imports eight upstream settings for logging,
KeepTop, delegated system proxy, and router mode. Integer subscription
overrides now use checked reflect.SetInt/SetUint instead of silently
ignoring values whose concrete type is narrower than int64/uint64.
KeepTop's config helpers and tests are a verified extension of the fork's
existing code. The audit runs a separate Core option and inbound test
using Go's overlay facility with locked dependencies; it deliberately
excludes the lazy KeepTop implementation until the paired sing-box and
hcore lifecycle are reconciled. Three additional Core conflict resolutions
are submitted (14/44, expected 30 remaining), pending the new CI gate.
Stable pins and published packages are unchanged.

### KeepTop runtime and HotReload reconciliation

Run 45474731669 passed the merged Core option/port regression gate.
Run 45473904930 also completed all previously scoped sing-box checks
(134 reviewed conflicts, 32 remaining). Adopt the upstream lazy KeepTop
runtime while retaining the fork's multi-format ParseConfig call and its
Naive/Trojan share-link and JSON startup regression. The new runtime tests
replace the old private reload-driven selection tests. HotReload retains
the protected BaseContext read, uses one captured service pointer, and
imports delegated proxy bookkeeping and engine application reporting.
Check the clean connectivity merge for the exact obsolete watcher call.
Three additional Core paths are submitted (17/44, expected 27 remaining).
The start/stop paths, atomic service publication and full lazy monitoring
integration are still pending; this is not an integrated runtime build.
Stable pins are unchanged.

### Start/Stop lifecycle and private profiles

Run 45475637081 passed the reviewed-source gate for KeepTop and HotReload.
The next candidate reconciles Start, service construction and static
state, retaining the separate startup/lifetime contexts, atomic debug
flag and fork settings locks. The running service is published through
an atomic pointer; Stop removes it before closing. The later portability
patch's filepath.IsLocal guards are retained, alongside upstream's
memory-limit restoration and router cleanup. Preserve the native
no-config-persistence entrypoint across initial start, auto-redirect
retry and subsequent hot reload; avoid the full config debug dump in
private mode. Clean log-level callers use the atomic debug API.
Three further Core resolutions are submitted (20/44, expected 24
remaining). Stop and log-level adaptations are clean-tree adjustments.
Other direct service readers, setup/grpc and admin service callers still
need review; full runtime and private-profile tests are not yet complete.
No stable pin changes.

### Authenticated gRPC, setup and status reporting

Run 45476214954 passed the lifecycle reviewed-source gate. Four more
Core conflicts are submitted: grpc_server.go, setup.go, commands.go and
custom.go (24/44, expected 20 remaining). The candidate keeps locked
listener publication, IPv6-safe address validation, local API secrets,
dynamic mTLS trust snapshots, defensive certificate copies, explicit
libbox setup error handling and the fork's memory-limit override. Adopt
upstream certificate reuse, client certificate PEM handling and device
memory hints. A bare PUBLIC KEY is no longer accepted for TLS client
authentication; callers must provide a CERTIFICATE PEM. Use the atomic
service reference for status reads and report the actual shutdown error
once, after closing the service. The updated candidate must still pass
source verification and integrated builds; the generated RPC schemas,
admin service and mobile wrappers are not yet reconciled. Stable pins
remain unchanged.

### Protobuf and elevated service reconciliation

Run 45476707575 passed the reviewed-source gate for gRPC and service
commands. The next candidate replays only the five protobuf source and
generated files from the verified historical Core checkpoint, checks their
exact blob IDs and verifies that the schema contains the union of both
current source inventories (wire numbers, field types, RPC signatures).
This avoids editing generated code by hand. Adopt upstream elevated
service reload/restart and delegated proxy status, retain nonblocking
liveness checks and both regression tests, and fail a new start when the
previous engine could not be closed. Preserve the authenticated privileged
RPC allowlist and the inline-only diagnostics guard, including generic
files. Seven further conflict resolutions are submitted (31/44, expected
13 remaining), plus the service schema clean-tree adjustment if needed.
Schema verification and integrated build are pending; no stable pins
have changed.

### Diagnostics and proxy monitoring reconciliation

Run 45476707575 completed successfully. Run 45478210412 stopped at
protobuf inventory parsing: the checker did not accept explicit json_name
annotations. Commit 60825a8 extends the checker to compare these annotations
without relaxing the field/type/number compatibility assertions. Recheck
45478958857 is pending.
The next candidate combines the bounded fork network probe and IP lookup
with the upstream crash report export. Adopt lazy proxy display conversion,
resting status and duplicate stream suppression while retaining the fork's
extra-security selector entry, endpoint fallback and readiness interface.
Copy group-local display records before setting selection flags. Speed tests
of an auto-selected group now target its representative proxy rather than
repeatedly measuring all members, and bulk tests skip parked proxies.
Five further Core conflicts are submitted (36/44 expected, 8 remaining;
170/210 total expected). The last fully confirmed source audit covered
158/210; the newer 12 resolutions require CI. Runtime and integration
verification remain pending; stable pins are unchanged.

### Native bindings, settings and DNS/chain configuration

Run 45478958857 passed the reviewed-source gate for protobuf and admin
service reconciliation, confirming 31 Core and 134 sing-box resolutions.
Run 45479211273 failed on the speed-test import-block whitespace comparison;
commit efada1a corrects the comparison while still requiring all nonblank
lines of the original tests to be retained.
Four further Core paths are submitted: buildconfighelper.go, mobile.go,
chain.go and dns_group_test.go. Retain validated settings snapshots and
GenerateConfig, synchronize the temporary log-level timer with settings
publication, preserve native Close errors and privacy bindings, add device
memory hints and stopped callbacks. Retain the Android chain-stage and
legacy-chain validation, preserve Psiphon pairing/region normalization,
and combine DNS dependency-order and duplicate-member tests with upstream
transport-order regressions. The expected candidate count is now 40/44
Core and 134/166 sing-box (174/210 total), of which 165 are confirmed by
source audit and 9 require the newer gate. Remaining Core paths expected:
Makefile, docs/hiddifyrpc.md, go.mod, v2/config/builder.go. Full integration
build and dependency promotion remain pending; stable pins unchanged.

### Builder and native build recipe reconciliation

Run 45480059452 passed the reviewed-source gate, confirming 40 Core and
134 sing-box resolutions. The next candidate resolves builder.go, Makefile
and docs/hiddifyrpc.md. The builder retains the fork's serialized snapshots,
privacy policy, adaptive-network settings and resolved chain-stage handling,
while incorporating native IPv6-only host adaptation, router validation,
fake-IP cache persistence and adaptive processor defaults. On native desktop
systems with IPv6 support, the TUN also captures IPv6 in IPv4-only DNS mode;
Android/iOS retain their platform-specific address contract. The updated
regression tests this distinction; traffic and leak testing on actual
platforms remain required. The Makefile candidate retains pinned generators,
Android API 24 and arm64 targets, web RPC builds and non-mutating module
preparation; adopts upstream Windows launchers (plus the legacy CLI filename),
Apple static Cronet tags and Linux library runpath. All old RPC documentation
table-member names were checked against the newer source.
Expected resolution count: 43/44 Core, 134/166 sing-box (177/210 total).
174 are confirmed at source level; three await audit 45480444831.
Core go.mod remains deliberately unresolved until the combined dependency
candidate builds and passes regression tests. No stable pins changed.

### Outbound manager and tunnel server reconciliation

Audit 45480444831 rejected the builder blob ID: the later FakeDNS cache
patch was already applied. Commit 5c547c5 uses the exact patched blob and
asserts the existing StoreFakeIP setting rather than inserting it twice.
The previous audit 45480059452 passed the reviewed-source and Core options
gates (174/210 source conflicts confirmed). The corrected three Core
resolutions and two additional sing-box resolutions are submitted, giving
43/44 Core and 136/166 sing-box expected (179/210, 31 remaining).
The outbound manager candidate retains immutable slices, captured lifecycle
scope and error returns, adds security classification and endpoint dependency
checks, rejects duplicate initial tags, and leaves the default tag unchanged
when lookup fails. The tunnel server candidate releases its mutex before
returning unknown-user errors and closes a connection discarded after a
failed request write. The reviewed-protocol test overlay now includes the
tunnel package and its queue regression under the race detector. The newer
source audit and runtime tests are pending. Stable pins remain unchanged.

### Router and network reset lifecycle

Audits 45480994448 and 45481191194 passed the reviewed-source gate;
43/44 Core and 136/166 sing-box resolutions are now source-confirmed.
The next candidate resolves route/network.go and route/router.go together.
The network manager uses the lifecycle scope context, unregisters interface
callbacks and closes monitors, and forwards network resets to the router's
DNS/HTTP/process caches without recursive resets. The router no longer
initializes a second neighbor resolver, and registers process searcher
cleanup. DNS flush retains the fork monitoring notification in addition
to native inbound cache flushing. Interface dispatch skips canceled scopes.
A separate CI step runs the upstream network reset/DNS group regression
under the race detector, with a temporary overlay and locked dependencies.
Expected count: 43/44 Core and 138/166 sing-box (181/210; 29 remaining).
179 source resolutions confirmed, two pending. route/conn.go remains open:
read/wake pacing changes require dedicated regressions, including the two
previously failing pacing tests. Stable pins remain unchanged.

### Connection tracking and pacing candidate

Audit 45481728109 passed the reviewed-source gate (181/210 confirmed).
The subsequent network-reset workflow edit malformed YAML because a replacement
interpreted the end-anchor in the test regex; commit 35fda3c restores the valid
workflow and the exact test command. The connection-manager candidate adopts
upstream tracked TCP/UDP pacing, server-first handshake handling, TLS wrapper
setup and packet-dial history invalidation while retaining the fork's socket
ownership and IPv6 fallback. The audit verifies the exact upstream pacing
helpers and tests. CI now runs the network reset, TCP/UDP pacing and path RTT
regressions with the race detector, plus an isolated pacing-disabled run.
Expected resolutions: 43/44 Core and 139/166 sing-box (182/210; 28 remaining).
181 source-confirmed; conn.go awaits the new source gate. The previous two
pacing failures are not considered fixed until the regression suite passes;
no throughput or CPU improvement is claimed without measurement. Stable pins
remain unchanged.

### XDrive security regressions and Xray interoperability

Audit 45482278797 passed the reviewed-source gate, confirming 182/210
resolutions. Four additional sing-box paths are submitted: the box-level
XDrive test, Xray interop test and both XDrive documentation translations.
Retain encrypted VLESS and add an explicit plaintext rejection regression,
plus a bounded write deadline and full-write assertion for the local round
trip. Adopt upstream's additional XHTTP interoperability cases and TLS
fingerprint regression. Keep the locked Xray release's existing documented
limits rather than claiming the newer backend defaults. Include attribution.
The scoped protocol gate now includes XDrive transport lifecycle tests under
the race detector. Box-level and real Xray binary interoperability tests still
need an integrated build; the newer Xray/Reality pair remains unpromoted.
Psiphon remains open: the upstream controller restart loop and SOCKS bridge
must be reconciled with the fork's close synchronization, Android DeviceBinder,
Conduit pairing, detour validation and isolated data directories. Preserve
these requirements before adopting the new scanner or changing bootstrap.
Expected count: 43/44 Core and 143/166 sing-box (186/210; 24 remaining).
182 source-confirmed, four await the latest source gate. Stable pins unchanged.

### Psiphon restart and protected bootstrap candidate

Audit 45483083719 confirmed 43/44 Core and 143/166 sing-box source
resolutions (186/210). Its route test failed to compile because the scoped
overlay omitted route/dns.go; dd23efb includes that coordinated file. The
same audit failed TestH_ScanRatesBlocks; WARP regression remains open.
Three Psiphon conflicts are submitted together: controller, outbound and
configuration tests. The candidate separates permanent instance shutdown
from restartable attempts, waits for controller termination before modifying
configuration or closing the datastore, retains per-instance private data
paths and Conduit pairing, validates detours, and skips a forced local proxy
for direct protected Android connections. The authenticated SOCKS bridge
is prepared before config commit when needed. The duplicate notice.go
implementation is removed only in the candidate. The test overlay includes
Psiphon and its scanner and bridge dependencies. No stable pins changed.
Expected: 43/44 Core and 146/166 sing-box, 189/210 (21 remaining).
186 are source-confirmed; the three new Psiphon paths await source and
runtime verification. Android/Conduit real-network testing remains required.

### Telemost session lifecycle and candidate regression coverage

Audit 45484929203 passed the source gate for the three Psiphon paths:
43/44 Core and 146/166 sing-box (189/210) source resolutions confirmed.
Three more paths are submitted: transport/call/telemost/connect.go,
creator.go and h_relay_test.go. The creator returns a session without
waiting for a peer, installs handlers on every new peer bridge, and
closes active connections on shutdown. A canceled creator is not published;
a joiner's early exit now signals failure instead of waiting for the
caller timeout. Existing upstream reconnect handler tests are retained.
The CI overlay covers the call configuration, relay, retry and Telemost
packages together with the other reviewed protocols. Live service testing
and the known upstream TODO about swapping a joiner's replacement tunnel
remain outstanding. Stable pins are unchanged.
Expected: 43/44 Core and 149/166 sing-box (192/210; 18 remaining).
189 source-confirmed; three await the new source gate and runtime tests.

### Combined dependency integration gate

Audit 45485409880 passed the reviewed-source gate for the Telemost paths:
43/44 Core and 149/166 sing-box (192/210) confirmed. The next candidate
resolves sing-box Makefile and schema/generator.go, retaining additional
ACME/Tailscale/CCM/OCM build tags and the fork's prefixable CIDR schema.
The generated JSON schema remains open until regenerated and checked.
A dedicated non-release workflow (45486170730) materializes the two reviewed
trees and the locked patched ray2sing tree into a disposable directory.
It tests the explicit dependency manifest candidates and pins the four
unresolved nested dependencies to the audited upstream revisions for this
probe only. It verifies every local module replacement, records candidate
provenance, and independently checks module graphs, Linux CLI build and
Core/sing-box regression tests. No candidate binary is uploaded as a release.
The dependency choices are provisional, not promoted; all seven manifest
and gitlink conflicts remain open until the combined results are reviewed.
Expected: 43/44 Core and 151/166 sing-box (194/210; 16 remaining).
192 source-confirmed at the last Telemost audit; two await source verification.
Stable release pins and ROADMAP task status remain unchanged.

### Documentation reconciliation and generated schema gate

The source gate in audit 45486050695 confirmed 43/44 Core and 151/166
sing-box conflicts (194/210). Eight documentation paths are submitted:
README, app integration notes, English/Persian bug histories, monitoring
and WARP documentation. Preserve fork branding, licenses, additional
protocols, historical bug reports, native dependency compatibility and
explicit fallback_address. Upstream history is not a claim that the
combined candidate has passed its build matrix. Monitoring and WARP
pages describe the reviewed upstream candidate behavior.
The combined candidate workflow now generates its JSON Schema from the
actual compiled registry and checks local references and required fork
configuration fields. The generated schema conflict remains open until
that artifact passes validation and is reviewed. The earlier combined
probe 45486170730 passed source audit and was running its build when
checked; no successful integrated build is claimed.
Expected: 43/44 Core and 159/166 sing-box (202/210; 8 remaining).
194 source-confirmed; eight await the new source gate. Remaining: three
module manifests, four nested gitlinks and generated JSON schema.
Stable pins and ROADMAP completion remain unchanged.

### First combined-build compatibility corrections

Combined probe 45486170730 reached Go compilation, then was canceled by
its successor. It exposed missing Lazy.IsReady, direct Status calls on
legacy interfaces, and non-atomic reads of the monitor's started flag.
Commit b27c2c6 supplies compatibility adjustments in four clean paths:
Lazy and three monitoring files. The route overlay alone was insufficient:
route/dns.go in the fork had no dnsHijackConcurrency constant. Commit
abb086f restores the named bound in the candidate, retaining 256 slots.
The scoped Psiphon test now uses the exact reviewed tunnel-core replacement
and DeepSearcher interface, with restoration of the original manifests.
The previous documentation audit failed because WARP has two distinct
fallback_address tables. 3e2337b preserves the fork-only row while retaining
the other upstream row. All eight documentation resolutions require a fresh
source gate; 194/210 remain confirmed and 202/210 submitted. The eight
remaining manifest, gitlink and generated schema conflicts remain open.
The combined workflow now runs after resolver changes and does not cancel
a running probe. Full build and runtime tests remain unconfirmed; stable
pins are unchanged.

### Aligned integration-test dependency manifest

Audit 45487262434 and combined probe 45487277566 passed their reviewed
source gates, confirming 202/210 conflicts (43/44 Core, 159/166 sing-box).
The first combined compilation after the Lazy/monitoring/DNS corrections
was still running when checked. The test/go.mod candidate now inherits
all reviewed sing-box root requirements and replacement directives, with
relative paths adjusted to the test module. Both historical test-only
requirement sets are retained; the two test-only OpenTelemetry conflicts
use the upstream 1.45.0 metric/trace pair. The manifest is checked by Go's
module parser. The combined probe also validates the test module's local
replacement paths, lists the module graph and compiles the integration tests
without executing container or live-network operations. All three module
manifests now have explicit candidates, but none is promoted until the
combined build is verified. The four gitlinks and generated schema remain
open. Count remains 202/210 source-confirmed, eight outstanding; stable
pins unchanged. ROADMAP remains 9/16.

### Carry nested dependency fixes into the combined tree

The combined build 45487277566 remains in progress at the time of this
checkpoint; no new compiler results are available yet. The manifest audit
45487671031 passed its source gate, including test/go.mod candidate syntax.
The combined probe previously checked out the raw MasterDNS target without
the race-tested sessionReady fix. Apply that exact reviewed fix, retain the
patch and resulting tree ID in the diagnostics, and rerun library/session
race tests. Extract the six already reviewed Tailscale caller adaptations
into a reusable helper; use the same helper in the scoped and combined
probes. The combined helper rejects an unexpected merged caller instead of
silently overwriting it. The combined probe also tests the exact sing and
Soar targets with local race regressions. The selected dependency targets
remain provisional; no gitlink or stable version is promoted.
202/210 source resolutions confirmed; 8 still outstanding (3 manifests,
4 nested gitlinks and generated schema). ROADMAP 9/16. Running and pending
combined jobs may be superseded by newer commits; inspect the latest run.

### Second integrated compilation corrections

Combined run 45487277566 failed on a duplicate option import in the
cleanly merged balancer/common.go; Core CLI, Core tests and schema
compilation were blocked by it. Psiphon's direct Android bootstrap test
panicked in its fake network manager's unimplemented ProtectFunc, not
in the actual platform binding. Scoped audit 45487671031 additionally
failed the same two historical pacing regressions: exchange pacing did
not disengage by 200 ms and the simulated busy socket never paced.
Commit 873c825 removes the duplicate import, implements the missing test
fake and runs the two timing-sensitive in-memory tests under Go synctest
virtual time. All prior assertions are retained; production pacing is
unchanged. The workflow explicitly requires both tests to pass, not skip.
The new source and test gates are pending; the latest combined dependency
probe 45488228887 was still running when checked. Do not infer a runtime
fix or measured CPU gain until the tests pass. 202/210 original conflicts
remain source-confirmed; eight outstanding; ROADMAP 9/16. Stable pins
unchanged.

### Third integrated regression checkpoint

Scoped audit 45489649503 passed the source gate and the full Psiphon
protocol group. Both formerly failing deterministic regressions passed:
TestH_ReadPaceLeavesAnExchangeAlone and TestH_PacedRawConnWaitsAfterAWakeUp.
The remaining failure was TestH_ReadPaceStopsWhenTheDownloadSlows (2 ms
wait instead of zero). The proposed test correction separates the fast
producer's queued data from the slow phase and uses synctest; production
pacing is unchanged and the assertions are retained/strengthened.
The common routing test script now runs the same race regressions in the
scoped and combined trees, verifies the required PASS markers and executes
both enabled and disabled pacing checks even when one fails.
Combined run 45488228887 passed MasterDNS library/session tests, sing bufio
and canceler tests, Soar and Tailscale tests, but was based on the older
Balancer and Psiphon sources. Its test-module compilation exposed another
failure: Docker v27.3.1 references sockets.DialPipe, removed in the resolved
go-connections v0.7.0. The test-only manifest candidate now selects Docker
v28.5.2, whose client uses its own context-aware dialPipeContext. The new
SDK pairing still requires compilation; no production dependency is changed.
202/210 source resolutions confirmed; eight outstanding. ROADMAP 9/16.
No stable pin or release promotion until full combined gates pass.

### Required regression and integration trigger coverage

The previous completed routing audit (45489649503) failed the slowdown
regression; the replacement deterministic fixture is already in main.
Require its explicit PASS marker in both the scoped and combined route
checks, not only the Go command exit code. The combined protocol probe
also requires explicit PASS markers for network reset, Psiphon lifecycle
and Android bootstrap, Telemost peer handlers and XDrive validation.
A renamed, skipped or build-tag-excluded required test cannot silently
pass the gate. Changes to the shared routing check, common helper or
source preparation now trigger combined candidate CI, which syntax-checks
all shell scripts. These are test-gate changes, not production transport
changes. 202/210 source resolutions confirmed; eight remain outstanding.
ROADMAP 9/16. Full combined verification and stable pin promotion remain
pending.

### Effective dependency graph and successful scoped audit

Audit 45490112715 passed all scoped gates, including the corrected pacing
regressions. The combined probe 45490098451 was still running when checked.
The combined integration now records the actual Go-selected module graphs
for Core, sing-box and the integration-test module, final go.mod/go.sum
snapshots and SHA-256 digests before the temporary tree is removed. It
checks selected replacements against the reviewed input manifests,
including version-specific directives and canonical local paths. Unexpected
replacements or graph errors fail CI. Shared version differences are
reported for review, not automatically downgraded or treated as failures.
Four offline helper self-tests run before the expensive integration.
202/210 original source conflicts confirmed; eight pending. ROADMAP 9/16.
No stable dependency pins or release inputs changed.

### Two reviewed nested gitlinks staged in the current candidate

The current locked fork still pins the same sing and Soar revisions as the
historical migration. The sing cached-connection regression blob is exactly
identical on both sides (419a876), and upstream contains the original race
fix dcc5d99. Soar's only fork-side commit 71ae9fb has the same two added
file blobs as upstream c2af37c, followed by upstream resolver corrections.
Combined run 45488228887 passed the scoped library tests at these targets.
A separate hash-guarded index step now selects b40ea07 and e18587b for the
reviewed candidate, retaining mode 160000 and verifying the tree delta.
The combined build must consume those exact staged gitlinks. This does not
change root submodules, stable patches or any published binary.
Expected after the new source gate: 204/210 (43/44 Core, 161/166 sing-box),
six outstanding: MasterDNS, Tailscale, three go.mod files and schema.
202/210 remain CI-confirmed until this commit's audit passes. ROADMAP 9/16.
The full combined build and cross-platform release gates remain mandatory.

### MasterDNS conditional resolution and staged Tailscale callers

The MasterDNS target a6b6936 has the seven directly compared differences
already covered by the scoped probe; the readiness race remains mandatory
to fix. Stage its upstream gitlink in the diagnostic tree only, with a
required dependency overlay recorded in the review metadata. The combined
probe requires a nonempty generated patch and a distinct patched nested
tree before compilation. It is not a release-ready bare gitlink.
The six previously tested Tailscale caller adaptations now become part of
the reviewed sing-box tree instead of being an unrecorded extra edit in
the combined checkout. Their exact blob IDs are checked in the export.
Tailscale itself remains unresolved: the compare API reports divergent
histories (71 fork-side commits and 1165 target-side commits), so library
compilation alone does not certify preservation of all fork behavior.
The expected candidate count is 205/210 (43/44 Core, 162/166 sing-box),
five outstanding, pending CI. No stable pin or Adaptive Connect overlay
is changed. Final promotion requires a reproducible nested MasterDNS
patch and complete dependency/API review and integrated build tests.

### Restore WireGuard/WARP MTU discovery configuration

Integrated run 45490098451 passed the sing-box protocol regression group
and generated a valid schema (961 property names, 134 references). Core
compilation failed because the clean merge retained the fork's option
structs without MTUDiscovery. Restore the upstream tri-state fields for
WireGuard and WARP, propagate the flag to the already existing WireGuard
transport and convert it explicitly for the fork's boolean AWG option.
WARP's omitted flag follows the upstream enabled default; standalone AWG
behavior remains unchanged. The scoped WARP gate includes the adjusted
option file. Add a Core conversion/JSON regression for unset, true and false
and require its PASS marker. The generated schema must expose mtu_discovery.
Docker test compilation in that older run still used v27.3.1; the current
manifest already proposes v28.5.2. The source gate of run 45493415836
passed for the prior 205/210 candidate; five conflicts remain open.
No stable pins change until the integrated tests pass.

### Independent Tailscale API compatibility gate

Run 45494490133 passed the reviewed-source gate after the MTU changes;
scoped runtime and combined integration results are still pending.
Review of the Tailscale target confirms source-level counterparts for
several late fork backports: TUN input and return-path interfaces,
NetMapNoPeers, MagicDNS export, node backend, per-node network hooks,
raw key conversions, DNS parent names and DERP configuration hooks.
The new independent workflow verifies 13 exact target blobs and runs
raw-key and DNS-name regression tests under the race detector. This
avoids replacing a pending combined integration run while reviewing the
nested library. It does not prove TUN, DERP or routing behavior is
identical; the remaining divergent commits and platform behavior still
require review. Tailscale remains unresolved. 205/210 candidate paths
source-confirmed, five open. Stable pins remain unchanged.

### Tailscale admission and TUN contract verification

Independent run 45494877741 passed the 13 source-blob checks and both
raw-key and DNS-name race regressions. The target DERP admission differs
from the old fork: a configured static allowlist contributes a rejection
for unlisted keys when no other admission mechanism accepts them. Preserve
that fail-closed behavior instead of copying the old early-return loop.
Three new offline regressions cover isolated static admission and key
replacement, return-path headroom bounds and preservation of the active
path after rejection, and input without a device. These tests do not
establish actual packet delivery, combined admission-policy equivalence,
or production network behavior. New tests are pending CI. The newer
scoped audit 45494490133 reported an EdgeScan/WARP failure; its complete
job log was not available yet. No speculative fix or stable pin change.
205/210 source conflicts reviewed; five open. ROADMAP 36/53 including
Adaptive Connect. Tailscale remains pending broader behavioral review.

### Fix deterministic EdgeScan rating fixture and Tailscale workflow YAML

Audit 45494490133 failed only at TestH_ScanRatesBlocks: the randomly
sampled 16 candidates can all land in the same /24, leaving the other
block's score at zero. Explicitly seed one answering and one failing
address, cap the count at two, and assert a single result plus the original
positive/negative scores. The production scanner is unchanged. Repeat the
fixture 30 times under the race detector and require all PASS markers.
Tailscale run 45495820645 had no jobs because the workflow YAML was
corrupted by a previous string insertion; restore the exact anchored
regex and preserve the original artifact upload step. Both fixes require
fresh CI. No stable pins or source conflict counts changed.

### Core integration regression follow-up

Run 45496243142 passed the full scoped migration audit, including the
30-repeat EdgeScan fixture and WARP checks. Run 45496243106 passed all
five independent Tailscale regressions. Combined run 45496243183 built
the CLI, passed the three MTU flag cases, integration-test compilation,
schema validation and effective replacement checks. The remaining failed
probe was core-tests: an older IPv4-only fixture rejected the intended
native IPv6 TUN leak guard, and six hot-reload assertions referenced the
retired StartedService field. Align the older test with the already
reviewed native/mobile address policy and use the atomic service pointer
in the hot-reload test without removing its original assertions.
Require explicit PASS markers for both regressions and the IPv6 preference
unit test. No production policy, stable pins or conflict count changes.
The module graph report still shows shared version/replacement differences
(including AmneziaWG), requiring review before manifest promotion.
New Core test changes are pending CI; 205/210 source conflicts reviewed.

### Reconcile effective AWG v1 and OpenVPN source pins

Combined run 45496243183 recorded 1195, 1034 and 1041 non-main modules
in Core, sing-box and the test main module respectively, with 45 shared
selection differences. Go only honors replace directives in the main
module. The legacy AWG v1 module was unreplaced in Core but used the
reviewed hiddifydeveloper revision in sing-box and tests. Add that exact
replacement in Core (v3 still uses the existing local checkout). Mirror
Core's existing OpenVPN source pin into sing-box and the test module;
these already selected that same revision, so this is explicit locking.
Preserve all original requirements and other replacements. Validate Go's
parsed replacement map as well as requirements. Audit actual source
identity separately from nominal versions, and enforce both reviewed
source pins across all three graphs. Two new verifier unit tests cover
source identity and missing/mismatched pin detection. Other shared source
differences remain diagnostic, not silently upgraded. Candidate only;
new integration results and stable manifest promotion remain pending.

### Exercise network dependency selections in both main modules

Run 45500842915 passed the reviewed-source gate with the new AWG and
OpenVPN pins; full integration is still pending. Add 13 named local
regressions, verified present in both source revisions, to both the Core
and sing-box main-module test contexts. Cover TUIC relay-mode validation,
Shadowsocks naming and handshake concurrency/failure/cancellation,
legacy handshake, Reality short-ID validation, uTLS fingerprints,
WireGuard MTU packet handling and AWG reserved bytes. Require all 13
explicit PASS markers in each graph (26 total). No remote VPN handshake
or live platform traffic is claimed; this tests compatibility of the
selected module graphs, not full protocol interoperability. No further
version bumps are made until this evidence and the remaining Core tests
are available. Stable pins and the 205/210 source review count remain
unchanged.

### Candidate evidence preflight (no automatic promotion)

The combined candidate with the shared-network checks was queued as
run 45501211366. The independent preflight checks a specified successful
combined CI run and exact commit, nested pins, MasterDNS overlay and
caller evidence, six effective manifest hashes, Core/network PASS
markers, generated schema references and SHA-256. It records unresolved
paths and shared source differences. It never modifies sources or
publishes a release. Push CI runs validator unit tests; manually dispatch
Candidate evidence preflight with the successful run ID and exact head
SHA when available. Older incomplete evidence is intentionally rejected.
Stable pins unchanged; 205/210 source paths reviewed, 36/53 roadmap tasks.

### Materialize the final five migration conflict choices

Stage Tailscale at 4138b20dfb9f5852d043abd8bf9da6c29d5ee53b together
with the six reviewed callers and all three parsed candidate manifests in
separate Git indexes. Tailscale scoped audit 45496243142 and independent
library tests 45496243106 passed; platform behavior remains a release gate.
The original 205/210 reviewed-source checkpoint is retained for replay.
The separate staged candidate has 209/210 choices; the last conflict is
generated docs/schema.json. Combined CI now consumes those staged trees,
checks exact manifest bytes and finalizes the generated schema plus the
three effective go.mod/go.sum pairs into final Git trees and binary
patches. The final report requires exact coverage of all 210 conflict
paths, records shared-source discrepancies, MasterDNS overlay and
integration status, and explicitly disables stable promotion. A failed
integration does not turn a materialized source choice into a verified
release. The new pipeline is pending CI; stable pins unchanged.

### 0.0.4 candidate desktop build matrix

Combined run 45500283071 (8cff05d) passed all integration probes; the
later candidate with all five final tree resolutions is not yet fully
verified. Its source-stage audit 45501843695 passed. Introduce a manual
candidate-only build matrix for linux amd64/arm64, windows amd64 and
Darwin amd64/arm64. It accepts a successful final combined run ID and
exact head SHA, reproduces the audited source checkpoint, applies final
binary patches to temporary indexes, compares resulting tree IDs, verifies
all nested gitlinks and the MasterDNS overlay, and checks exact final
manifest/schema bytes. The source archive is checksummed. Matrix builds
use readonly modules and refuse manifest drift; Linux amd64 has a native
version smoke test. Output artifacts are explicitly candidate CLI builds,
not published releases or native GUI libraries. The workflow is untested
until final integration evidence exists. VERSION and stable pins unchanged.

### Automatic candidate matrix after the final integration

The exact final combined integration run 45501843656 at
ce43716bb022c8f042a74490841303e9bc23d92d was still queued when checked.
Enable a workflow_run trigger on completion, restricted to this run ID,
head SHA and successful conclusion. Reuse the existing API identity and
artifact validation before building. Failed or unrelated runs cannot
start the matrix; manual dispatch remains available for later candidates.
No automatic stable release, source promotion, version bump or tag.

### Fix final candidate replay without weakening tree verification

Matrix run 45506043579 failed before compilation: the tentative Core
merge tree differed across otherwise identical audits. Conflict marker
labels can contain the synthetic commit ID. Reconstruct from the locally
staged candidate tree instead of applying a patch against the provisional
merge tree. Require the locked input identities, conflict coverage and
reviewed resolution lists; restore the exact six effective manifest files
and generated schema after validating their recorded hashes. Require the
resulting final Git tree ID to match the successful integration evidence
exactly. The nested checkout, overlay and manifest checks remain intact.
A narrowly scoped push trigger retries the matrix using only the approved
successful integration 45501843656 unless manually dispatched. No source
pin, VERSION or stable release changes. Matrix retry pending.

### Publish 0.0.4 verified desktop binaries

The final combined integration 45501843656 succeeded with all 210
conflicts resolved. Candidate matrix 45506329292 successfully reconstructed
the tested source trees and is compiling the five desktop targets. User
approved publication of 0.0.4. A separate publish workflow is restricted
to that matrix run and its exact SHA. It requires all five successful
build jobs, checks each binary against its artifact checksum, and
publishes five binaries plus a single SHA256SUMS. No TXT provenance or
per-binary checksum assets are included; source tree identities and CI
links are retained in release notes. Create a draft first, verify the six
uploaded assets, then publish as latest. The existing locked-source
release pipeline also uses the six-asset layout in future versions.
The stable patch-stack promotion remains separate; VERSION and locked
0.0.3 source pins are unchanged. No unverified binary is published.
