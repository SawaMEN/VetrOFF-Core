# Adaptive Connect — VetrOFF-Core integration candidate

Target: the VetrOFF-Core sing-box runtime, not VetrOFF Client or the standalone
Hiddify Core upstream. 3x-ui remains the configuration/diagnostics producer.
This candidate is deliberately outside the stable patch series until the
upstream migration and tests are complete.

## Implemented

- Transport-independent selector ported from [3x-ui Anti-DPI](https://github.com/SawaMEN/3x-ui/tree/Anti-DPI/internal/adaptiveconnect).
- Explicit opt-in, consecutive failure/recovery thresholds, freshness,
  cooldown, optional failback and confirmed active state.
- Network generation isolation; stale probe completions cannot affect a new
  network. Manual mode suppresses automatic decisions.
- Deterministic tests with no external connections or dependencies.

Run: `bash scripts/test-adaptive-connect.sh`.

## Runtime integration contract

The existing sing-box patch series already has URLTest failover, monitoring,
selector, and Xray-compatible balancer support. Review the migration result
before introducing a second scheduler. Reuse completed end-to-end health
measurements rather than TCP connect success. The adapter must provide only
eligible tunnel outbounds and reject direct, block and hidden transport tags.
Separate TCP/UDP eligibility where appropriate. A canceled probe is not a
failure. Serialize network changes, selection and tunnel activation; verify
the new active connection before calling ConfirmActive. Never treat a
recommendation as an executed switch. Preserve manual selection and do not
fall back to direct. On interface changes reset the generation and discard
in-flight results. No telemetry is transmitted.

## Promotion checklist

- [x] Independent selection policy and tests.
- [x] Network generation and manual selection guards.
- [ ] Connect to reviewed URLTest/Selector runtime and platform lifecycle.
- [ ] Agree versioned opt-in configuration with 3x-ui; retain ordinary subscriptions.
- [ ] End-to-end transfer probes and integration tests on reviewed source trees.
- [ ] Client-side Siberian/DPI diagnostic correlation, with explicit user consent.
- [ ] Validate mobile/desktop builds and real network behavior before promoting
  the candidate into ordered patches and regenerating expected-tree hashes.

No stable gitlink, patch series, expected tree, or release version changes.
The candidate does not yet select real traffic or implement packet manipulation.

## Runtime adapter candidate

The new Runtime accepts typed completed probe events and filters resolved tunnel candidates by TCP/UDP support. Canceled and skipped tests never count as failures. A selector-owner adapter must implement SwitchVerified as a compare-and-activate transaction, keeping the old selection on errors. Rejected activation invalidates the candidate's health streak. Manual resume starts a new generation. The tests use an in-memory selector double, not a real tunnel. An independent CI workflow runs the package race tests on Anti-DPI without fetching upstream or releasing binaries.

The remaining integration work is to wire this adapter into actual URLTest result delivery and the Selector owner, coordinate lifecycle events, and validate the real switch transaction. The current migration candidate is not part of a production binary.

## Portable configuration v1

The standalone policy uses seven required fields, including version and explicit enabled. See [default fixture](testdata/policy-v1.json). Failure/recovery thresholds are 1..10, cooldown 0..300 seconds and maximum observation age 5..600 seconds. Unknown versions, fields, duplicates, null values and trailing data fail closed. The local user must independently opt in; importing an enabled policy does not activate routing. The adapter accepts no endpoint addresses, credentials, commands or direct fallback from this policy. A compatible 3x-ui UI can export this document separately; normal subscription payloads are unchanged. Actual import into the running app/core remains pending.

## Client Siberian-style diagnostic candidate

The diagnostic runner uses an explicit platform-provided DiagnosticPath for both DNS and TCP, distinguishes client-physical and client-tunnel reports, and rejects an unknown path or changed network generation. No default dialer/resolver, implicit direct fallback, certificate bypass, automatic run or telemetry is provided. It requires local consent and two owned test hostnames with valid certificates sharing exactly one public IP; one bounded run per minute. Baselines must succeed before a small comparison burst. TLS timeouts with a successful control are a lead, not proof of censorship. Other errors or partial results are inconclusive. No evidence from this diagnostic is fed directly into the Adaptive Connect selector. The report is local, versioned JSON and omits raw error messages.

This is a standard Go TLS comparison inspired by [Siberian Check](https://github.com/iddserg/siberian-check), not the upstream implementation or uTLS fingerprint test. Actual platform-bound adapters, fingerprint support and runtime integration remain pending. The 3x-ui Anti-DPI branch now imports and compares the original client report format. Unit tests use injected paths; they do not verify mobile network routing.

## Layered connectivity diagnostic candidate

CheckConnectivity uses the same explicitly bound DiagnosticPath and shared manual-run cooldown as the Siberian comparison. It resolves IPv4/IPv6 separately, rejects unsafe or mismatched answers, and checks up to two distinct public addresses per family. Each address is pinned for a TCP connection, verified TLS handshake and a single HTTPS HEAD request to the owned hostname on port 443. Redirects are not followed, no response body is downloaded, and no credentials are sent. A final HTTP status (including 403 or 503) proves a response, not application health. Certificate failures, absent DNS answers, timeouts and untested stages remain distinct. Partial reports carry complete=false, and the result never changes routing. The versioned connectivity report is distinct from the Siberian report. The 3x-ui Anti-DPI branch imports connectivity reports; platform binding and full DPI-CH-specific checks remain pending. Tests cover injected failures, address limits, path changes and an isolated local TLS server; they do not contact third parties.

## Adaptive MUX candidate

The locked sing-box source has outbound multiplex options for h2mux, smux, yamux and rmux. VLESS's constructor can clear Flow when mux is enabled; the new policy conservatively excludes nonempty Flow (including Vision), transport-level multiplexing, special modes, unreviewed protocols and unverified server compatibility. VMess, Trojan, Shadowsocks and VLESS without flow are eligible only after actual adapter validation. The core must supply two preconfigured, otherwise equivalent tunnel outbounds and a local opaque pair identity derived from effective options, not a subscription-provided string. No new mux protocol or scheduler is introduced. The policy uses the existing Runtime and verified switch transaction, is TCP-only, and requires both Adaptive Connect and Adaptive MUX opt-in plus local consent. The default preference is non-multiplexed; failback retains the Adaptive Connect policy. Existing streams must not be reconfigured in place. The controller must not compete with another controller for the same selector. Actual profile construction, server compatibility validation and runtime wiring are pending. Tests use fake switchers and do not establish real transport compatibility.

## Connection budget candidate

The existing sing-box group Pool balances outbound slots; sing-mux and XHTTP XMUX already own physical transport connection reuse. Do not duplicate those pools. The new PoolBudget accounts for logical connection opens and lifetimes across profiles and network generations. It is disabled by default and requires local consent. Defaults are 64 total reservations, 32 per profile and 8 pending opens (candidate values, not measured production recommendations). A nonblocking reservation is acquired before a dial and committed only on success. On errors or connection close, release exactly once; duplicate releases are harmless. Old-generation pending opens cannot commit and continue consuming the budget until actually cleaned up. Active connections remain counted across network changes. Budget exhaustion is local admission control, not network filtering or a reason to retry through another route. No new sockets, background workers, queues or physical-connection limits are implemented. The adapter must wire this to actual dial/close hooks, including the failure cleanup, share one budget across selectors, and preserve the transport's own limits. The tests check resource limits, concurrency, stale generation and opt-in; actual transport integration remains pending.

## Local availability history candidate

AvailabilityHistory stores only bounded successful/failed end-to-end observations in memory. It requires explicit opt-in and is disabled by default. Defaults: 24-hour retention, 128 network/profile/transport series, 32 observations per series; oldest series are evicted when full. The adapter supplies opaque process-local network and effective-profile identities (derive with a random local secret, not raw SSID, IP, credential or subscription tag). TCP and UDP remain separate. The RecordWithHistory adapter records only accepted Runtime samples; canceled, skipped, duplicate and stale-generation observations are rejected. The owner must serialize identity binding with network lifecycle events. History can suggest a stable order for existing probes using a smoothed success fraction and a minimum of three samples, but never marks a candidate healthy, bypasses fresh probes, skips probes or changes live routing. Clear discards history; no disk persistence or telemetry. Tests cover bounds, retention, opt-in, concurrency and network isolation. Platform identity derivation and actual probe worker integration remain pending.

## Unified controller candidate

Controller composes one runtime per independently routed TCP/UDP selector, one shared optional PoolBudget and one optional AvailabilityHistory. The existing Config v1 remains unchanged; ControllerConfig is an internal composition, not a subscription schema. All features require Adaptive Connect opt-in plus their own optional consent. A validated MUX pair replaces the ordinary runtime for its binding rather than competing with it. The controller requires an initial network reset and complete active-route snapshot, prevalidates resets, serializes lifecycle events and rejects foreign or stale probe tickets (including after manual resume). It never imports historical data as fresh health. Pool reservations survive path changes until real cleanup; Close stops new admissions and clears history, but the transport owner must cancel/close real I/O. Switch callbacks must not re-enter the controller, and separate bindings must correspond to independent selectors. The candidate does not yet create actual platform bindings, derive real profile/network identities, or register in a production core. The owner must serialize physical path changes and actual manual selection with controller calls. Tests exercise composed behavior using fakes, including independent routes, shared budgets, failed reset and stale callbacks.

## Prepared-tree selector bridge

The experimental overlay in migration/anti-dpi-overlay integrates a Switcher with the actual locked, patched sing-box Selector source. It uses the existing selection path with a mutex and revision guard against intervening manual selections (including ABA). The first bridge is intentionally restricted to a plain started selector with TCP-only reviewed leaf outbounds, no dependencies and no hidden/direct/block/group members. An owner must supply a verified HTTPS 204 endpoint it controls. A bounded GET via the chosen outbound, normal TLS verification and exact response status are required; cached URLTest latency alone is insufficient. Probe exposes an outcome for the existing worker; no scheduler or automatic binding is added. Existing SelectOutbound callers retain normal behavior with serialized selection changes. The overlay runs in a disposable copy of the locked prepared tree and does not alter stable patches or tree locks. Tests exercise the actual Selector with injected fake outbounds and verifier; real tunnel, platform events, URLTest delivery, configuration attachment and shared TCP/UDP selection remain pending. Run bash scripts/test-anti-dpi-overlay.sh. CI adds a separate integration job; build success is required before promoting this bridge.

### URLTest worker bridge and integration CI repair

The first integration CI run prepared all locked trees successfully but stopped before Go compilation: patched sing-box requires Go >=1.27.3, while the workflow installed Go 1.26.11. The integration job now prepares sources first and reads the Go version from the prepared sing-box go.mod. The explicit WithAdaptiveURLTest context binds a fresh worker batch to a controller generation before testing. Only matching uncached leaf probes, the same resolved outbound object and the exact configured single HTTPS 204 URL use the strict verifier; unrelated, cached and multi-URL tests keep their normal implementation. The worker rejects stale observations from adaptive history and does not treat them as a failed URLTest result. A network owner must cancel old contexts and invoke Tick after completed batches; this is not yet wired into automatic startup or platform callbacks. Rejected activation now preserves the probe timestamp watermark while clearing streaks. Stable source pins remain untouched.
