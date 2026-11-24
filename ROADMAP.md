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
- [ ] Integrate with the existing URLTest/Selector and platform network lifecycle.
- [ ] Define versioned opt-in 3x-ui configuration and diagnostics interface.
- [ ] Validate candidate builds and real network behavior before promotion.
