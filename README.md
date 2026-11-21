# VetrOFF-Core

VetrOFF-Core 0.0.1 is a standalone CLI built from [Hiddify Core](https://github.com/hiddify/hiddify-core), [Hiddify sing-box](https://github.com/hiddify/hiddify-sing-box), and [ray2sing](https://github.com/hiddify/ray2sing).

`sources.lock` records an original Core commit and its two original gitlinks. The patch series contain the runtime, tests, and dependency changes from the SawaMEN forks. Build jobs fetch the three original repositories and their dependencies. The old forks are unnecessary after the patches are committed. Original project workflows and the old checked-in core binary archive are excluded from the migration.

## Builds

GitHub Actions builds the CLI for Linux and macOS on AMD64 and ARM64, and for Windows on AMD64. The first successful build and test run on `main` publishes the version in `VERSION` with its binaries, SHA-256 checksums and exact source revisions. Subsequent builds of a published version produce Actions artifacts.

```sh
bash scripts/build.sh
./dist/vetroff-core-linux-amd64 version
bash scripts/test.sh --reuse-sources
```

Local builds need Bash, Git, Make, Go and `sha256sum`. Standalone builds use the fork's CLI build tags with Cronet disabled. Mobile libraries, the browser extension and installer packages have separate build requirements.

## Upstream updates

Every Monday GitHub Actions takes the latest original Core commit and uses the sing-box and ray2sing revisions selected by its gitlinks. It applies all three patch series, runs the tests, and attempts the five CLI targets. Successful candidate builds are downloadable from that Actions run. Conflicts stop the candidate and identify the patch and source revision in the log. Stable releases continue to use the reproducible revisions in `sources.lock`.

Patch files use Git binary diff format with full blob IDs for three-way application. After resolving a candidate conflict against the newer original sources, regenerate the affected patch and update the three pins together.

Original project licenses and notices apply to the patched sources and resulting binaries.
