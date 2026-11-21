# VetrOFF-Core

VetrOFF-Core 0.0.1 is a standalone CLI built from [Hiddify Core](https://github.com/hiddify/hiddify-core), [Hiddify sing-box](https://github.com/hiddify/hiddify-sing-box), and [ray2sing](https://github.com/hiddify/ray2sing).

The three original projects appear at the root as Git submodules. Each submodule links to the exact original commit used for stable builds. The `.gitmodules` file records their clone URLs.

| Submodule | Patches | Audited fork snapshot |
| --- | --- | --- |
| [`hiddify-core`](hiddify-core) | [`patches/hiddify-core`](patches/hiddify-core/) | `a5662c60c181090d860f165bffe9734c896bae23` |
| [`hiddify-sing-box`](hiddify-sing-box) | [`patches/hiddify-sing-box`](patches/hiddify-sing-box/) | `45814844d5b80af5b43ac5c17d45c0ed3a42bcd0` |
| [`ray2sing`](ray2sing) | [`patches/ray2sing`](patches/ray2sing/) | `7aac8dcf42b6284cf6993c992db8dcf259a4b98f` |

The stable sing-box commit matches the original Core gitlink. The stable ray2sing submodule uses the current original commit because its patch recreates the same audited source tree. The build reads all three pins from the VetrOFF-Core Git checkout, stages the upstream sources in `.build/`, and checks each patched Git tree against its `expected-tree` file. The audited trees preserve the forks' tracked runtime sources, tests, build definitions and dependency changes. The original workflows and contributor instructions are retained, and Core keeps the original submodule definitions and pointers. The fork's checked-in Core archive is omitted and the VetrOFF CLI version patch is applied.

## Builds

GitHub Actions builds the CLI for Linux and macOS on AMD64 and ARM64, and for Windows on AMD64. The first successful build and test run on `main` publishes the version in `VERSION` with its binaries, SHA-256 checksums and exact source revisions. Subsequent builds of a published version produce Actions artifacts.

```sh
bash scripts/build.sh
./dist/vetroff-core-linux-amd64 version
bash scripts/test.sh --reuse-sources
```

Local builds need a Git checkout, Bash, Make, Go and `sha256sum`. Run `git submodule update --init` if you also want to inspect the original sources in the project root. Standalone builds use the fork's CLI build tags with Cronet disabled. Mobile libraries, the browser extension and installer packages have separate build requirements.

## Upstream updates

Every Monday GitHub Actions takes the latest original Core commit and uses the sing-box and ray2sing revisions selected by its gitlinks. It applies all three patch series, runs the tests, and attempts the five CLI targets. Successful candidate builds are downloadable from that Actions run. Conflicts stop the candidate and identify the patch and source revision in the log. Stable releases continue to use the reproducible revisions in the three Git submodules.

Patch files use Git binary diff format with full blob IDs for three-way application. After resolving a candidate conflict against the newer original sources, regenerate the affected patch and update the submodule pointers and corresponding `expected-tree` files.

Original project licenses and notices apply to the patched sources and resulting binaries.
