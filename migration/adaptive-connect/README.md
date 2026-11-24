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
