# VetrOFF Core roadmap

- [x] Pin original Hiddify Core and both Git submodules at matching revisions.
- [x] Implement fail-closed 3-module patch application.
- [x] Apply initial separate patches to all three projects.
- [x] Add GitHub Actions matrix, build artifacts, tagged releases, weekly upstream comparison, and VetroffProxy integration tests.
- [ ] Confirm first successful complete GitHub Actions matrix; fix platform-specific upstream issues if any.
- [x] Add native VetroffProxy SDK outbound, option schema, registration and test patches to sing-box; pending CI validation.
- [x] Add VetroffProxy link conversion, redacted errors and regression tests to ray2sing; pending CI validation.
- [ ] Integrate Vetroff runtime options and relevant user-facing commands in Hiddify Core.
- [ ] Migrate other fork-only protocol/routing modifications incrementally with dependency and compatibility tests.
- [ ] Add native Cronet/Naive shared-library builds and platform-specific packaging.
