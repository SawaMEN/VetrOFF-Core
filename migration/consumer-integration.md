# Consumer migration: VetrOFF-Core 0.0.5-rc

The existing 0.0.4 stable release is unchanged. The verified RC is now in
the ordinary patch stack (a34bbb9). The CI promotion's final push was
rejected because the workflow token lacked workflow-write permission;
the exact tested commit was subsequently fast-forwarded through the
authorized repository connection.

Consumers are pinned for the initial compatibility assessment:
3x-ui 7186c696b756c81e63715aa6b7dc30ea02d14f7c and native hiddify-app
159bb746be8d759b85217eaebf480f62336062de.

- Panel: audit native source anchors, all required security module
  versions, compile with the actual panel tag list (including Naive
  and Cronet), run the panel protocol tests and config validation.
  The ordinary CLI disables Naive and does not satisfy the legacy
  version/revision/target.json parser. Do not rename it as a replacement.
  The new provider must eventually support exact VetrOFF tree provenance,
  matching Cronet, staged config checks, statistics and traffic tests,
  atomic install, and rollback. Do not change the Xray backend.
- Android: build AAR and ARM64 root companion with the existing native
  API, then compile an isolated debug APK against those exact files.
  The production client currently downloads the legacy stable release;
  do not repoint it until the archive contract, API and runtime pass.
- macOS: bind a native ARM64 XCFramework and link the Swift application
  and Network Extension, including existing Swift smoke tests. No
  separate CLI process, no signed DMG publication in this audit.

All three jobs are non-deploying. No application repository default,
release channel, production installation or credentials are changed.
The debug APK is not a signed production upgrade. Actual devices and
real server traffic are a separate final gate. Sing and Soar's three
upstream changes remain deferred.
