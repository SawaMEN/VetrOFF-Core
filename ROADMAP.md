# VetrOFF Core roadmap

- [x] Pin original Hiddify Core and both Git submodules at matching revisions.
- [x] Implement fail-closed 3-module patch application.
- [x] Apply initial separate patches to all three projects.
- [x] Add GitHub Actions matrix, build artifacts, tagged releases, and weekly upstream comparison.
- [ ] Confirm first successful complete GitHub Actions matrix; fix platform-specific upstream issues if any.
- [ ] Migrate native VetroffProxy SDK outbound, option schema, registration and tests from SawaMEN fork into a separate sing-box patch.
- [ ] Add VetroffProxy link conversion and regression tests to ray2sing.
- [ ] Integrate Vetroff runtime options and relevant user-facing commands in Hiddify Core.
- [ ] Migrate other fork-only protocol/routing modifications incrementally with dependency and compatibility tests.
- [ ] Add native Cronet/Naive shared-library builds and platform-specific packaging.
