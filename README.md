# VetrOFF-Core

VetrOFF-Core 0.0.1 is a standalone CLI built from [Hiddify Core](https://github.com/hiddify/hiddify-core), [Hiddify sing-box](https://github.com/hiddify/hiddify-sing-box), and [ray2sing](https://github.com/hiddify/ray2sing).

Each component keeps its `source.lock`, ordered `series`, and patch files in the same directory.

| Component | Original commit | Local patch directory |
| --- | --- | --- |
| Hiddify Core | [`b097daa`](https://github.com/hiddify/hiddify-core/commit/b097daadd89d2b62e30c0f4353f55a973118514c) | [`patches/hiddify-core`](patches/hiddify-core/) |
| Hiddify sing-box | [`004371d`](https://github.com/hiddify/hiddify-sing-box/commit/004371d6be2395ee4379054a8cf113f339dbe231) | [`patches/hiddify-sing-box`](patches/hiddify-sing-box/) |
| ray2sing | [`cddb962`](https://github.com/hiddify/ray2sing/commit/cddb962a5a9d02f4136353e76347e7d909b12e65) | [`patches/ray2sing`](patches/ray2sing/) |

The two dependency commits match the original Core gitlinks. Locked preparation checks the entire Git tree produced by each patch series against its `source.lock`. The audited tree reconstructs the recorded fork snapshot with the original GitHub workflow and contributor instructions. Core additionally keeps the original submodule definitions and pointers, omits the fork's checked-in binary archive, and takes the VetrOFF CLI version patch. The audit covers tracked runtime sources, tests, build definitions and dependency changes. The fork commits are recorded for reference.

## Builds

GitHub Actions builds the CLI for Linux and macOS on AMD64 and ARM64, and for Windows on AMD64. The first successful build and test run on `main` publishes the version in `VERSION` with its binaries, SHA-256 checksums and exact source revisions. Subsequent builds of a published version produce Actions artifacts.

```sh
bash scripts/build.sh
./dist/vetroff-core-linux-amd64 version
bash scripts/test.sh --reuse-sources
```

Local builds need Bash, Git, Make, Go and `sha256sum`. Standalone builds use the fork's CLI build tags with Cronet disabled. Mobile libraries, the browser extension and installer packages have separate build requirements.

## Upstream updates

Every Monday GitHub Actions takes the latest original Core commit and uses the sing-box and ray2sing revisions selected by its gitlinks. It applies all three patch series, runs the tests, and attempts the five CLI targets. Successful candidate builds are downloadable from that Actions run. Conflicts stop the candidate and identify the patch and source revision in the log. Stable releases continue to use the reproducible revisions in the three `source.lock` files.

Patch files use Git binary diff format with full blob IDs for three-way application. After resolving a candidate conflict against the newer original sources, regenerate the affected patch and update the commit and patched tree IDs in the corresponding locks.

Original project licenses and notices apply to the patched sources and resulting binaries.
