# VetrOFF-Core roadmap

- [x] Fetch three original Hiddify source repositories and build a single CLI.
- [x] Keep the fork changes as patch series in this repository.
- [x] Build upstream candidates automatically and keep stable source pins reproducible.
- [x] Configure desktop builds, regression tests and release publishing (current stable version: 0.0.3).
- [x] Gate upstream candidate builds behind a single patch-application preflight with conflict reports.
- [x] Detect personal fork updates not yet included in the audited patch snapshots.
- [ ] Rebase Core and sing-box patch stacks onto current original upstream revisions without losing features.
- [x] Split the large Core and sing-box baseline patches into ordered subsystem groups and verify unchanged source trees.
- [ ] Add native Cronet, Android and Apple shared-library packages for GUI clients.

## Migration follow-up

- [x] Preserve historical migration checkpoints and automate their replay in main.
- [x] Integrate the independent current-baseline three-way merge audit.
- [ ] Reconcile historical checkpoints with the latest audited fork snapshots.
- [ ] Resolve remaining WireGuard/AWG and Tailscale conflicts.
- [ ] Resolve the other conflicts and review dependencies.
- [ ] Fix failing routing tests and pass the integrated regression suite.
- [ ] Pass the full build matrix and promote verified source pins together.

## Adaptive Connect / Anti-DPI

Client runtime target: VetrOFF-Core (used by the future client), not VetrOFF Client. See [integration candidate](migration/adaptive-connect/README.md). Stable pins are unchanged until reviewed integration and test completion.

- [x] Port the independent selection policy from 3x-ui.
- [x] Add network-generation isolation, manual mode and deterministic tests.
- [x] Add a typed URLTest/Selector adapter candidate and independent race-test workflow.
- [ ] Wire the adapter into the actual URLTest/Selector and platform network lifecycle.
- [x] Define versioned opt-in policy v1 and strict decoder.
- [ ] Wire policy import into the app/core lifecycle.
- [x] Add a bounded client Siberian-style diagnostic candidate with explicit path scope.
- [x] Implement layered DNS/IPv4/IPv6/TCP/TLS/HTTPS connectivity diagnostics candidate.
- [ ] Connect diagnostics to platform-bound DNS and sockets, uTLS profiles and connectivity report import.
- [ ] Review and integrate additional DPI-CH-specific diagnostic tests.
- [x] Add opt-in Adaptive MUX policy for validated preconfigured TCP profiles.
- [ ] Integrate actual profile validation and mux lifecycle into the selector owner.
- [x] Add opt-in logical connection budget candidate with race-safe reservations.
- [ ] Wire connection budget into actual dial/close lifecycle and verify transport resource usage.
- [x] Add bounded opt-in in-memory availability history and probe ordering candidate.
- [ ] Bind process-local network/profile identities and existing probe lifecycle to history.
- [x] Compose Adaptive Connect, MUX, pool and history into one lifecycle controller candidate.
- [x] Add a disposable prepared-tree bridge for the actual plain TCP-only Selector, guarded manual selection and integration CI.
- [x] Add explicit context-bound fresh URLTest leaf worker bridge in the prepared-tree overlay.
- [x] Add explicit post-start Selector owner, real URLTest batch call, InterfaceUpdated reset, manual pause/resume and close hooks in overlay.
- [x] Add explicit local srun policy file and selector/probe flags; invoke the existing worker at bounded CLI cadence in the overlay.
- [ ] Verify CLI compilation and real tunnel switching; bind Hiddify GUI lifecycle, platform notifications and actual transport cleanup.
- [ ] Validate candidate builds and real network behavior before promotion.
