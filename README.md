# VetrOFF-Core

VetrOFF-Core 0.0.4 is a standalone CLI built from [Hiddify Core](https://github.com/hiddify/hiddify-core), [Hiddify sing-box](https://github.com/hiddify/hiddify-sing-box), and [ray2sing](https://github.com/hiddify/ray2sing).

The three original projects appear at the root as Git submodules. Each submodule links to the exact original commit used for stable builds. The `.gitmodules` file records their clone URLs.

| Submodule | Patches | Audited fork snapshot |
| --- | --- | --- |
| [`hiddify-core`](hiddify-core) | [`patches/hiddify-core`](patches/hiddify-core/) | `a5662c60c181090d860f165bffe9734c896bae23` |
| [`hiddify-sing-box`](hiddify-sing-box) | [`patches/hiddify-sing-box`](patches/hiddify-sing-box/) | `45814844d5b80af5b43ac5c17d45c0ed3a42bcd0` |
| [`ray2sing`](ray2sing) | [`patches/ray2sing`](patches/ray2sing/) | `7aac8dcf42b6284cf6993c992db8dcf259a4b98f` |

The stable sing-box commit matches the original Core gitlink. The ray2sing submodule is pinned independently to the official revision `a722bf1`, incorporating two upstream AmneziaVPN / AmneziaWG changes. Its patch recreates the verified three-way merge of the previously audited fork tree and the new original revision. The build reads all three pins from the VetrOFF-Core Git checkout, stages the upstream sources in `.build/`, and checks each patched Git tree against its `expected-tree` file. The audited trees preserve the forks' tracked runtime sources, tests, build definitions and dependency changes. The original workflows and contributor instructions are retained, and Core keeps the original submodule definitions and pointers. The fork's checked-in Core archive is omitted and the VetrOFF CLI version patch is applied.

The 0.0.4 release trees are reproduced from the root pins, regenerated patch series and the separately pinned MasterDNS overlay. The old migration pipeline remains in Git history for reproducibility. Subsequent updates must regenerate the relevant patch series and verify the new source trees before promotion.

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

The workflow now performs a single patch preflight before scheduling platform builds and tests. If an upstream candidate conflicts, the preflight job publishes a Markdown conflict summary and an `upstream-patch-diagnostics` artifact containing component patch logs. The failed candidate cannot be mistaken for a passing upgrade, and the locked source pins remain unchanged.

## Checking personal fork updates

`patches/fork-snapshots.lock` records the three **personal fork revisions that were audited when the patch series were generated**. These revisions are distinct from the three original-project Git submodule SHA pins. On pull requests, scheduled and manually dispatched workflows, `scripts/audit-forks.sh --strict` checks the latest `main` of each personal fork against its audited snapshot and fails the independent audit job if new work needs to be ported.

Run locally with:

```sh
bash scripts/audit-forks.sh --strict
```

When a personal fork advances, review its new commits, regenerate the affected patches, update the corresponding expected source tree, verify all targeted tests and platforms, and only then advance the audited revision in `fork-snapshots.lock`. Do **not** replace the official Git submodules with the forks or update the snapshot SHA without incorporating and verifying its changes.

### Ordered subsystem patches

The original Core and sing-box baseline diffs are split into 16 and 126 ordered subsystem patches respectively. The four later Core patches were preserved during the split; ray2sing remains a separate single-patch series. Each `series` file specifies the application order; the numbered pieces preserve complete Git diff sections, including binary changes. Do not sort or reorder these files independently of `series`: related changes may span more than one subsystem.

The initial split was verified against the original byte streams and all three existing `expected-tree` values. When regenerating an unsplit baseline, `python3 scripts/split-baseline-patches.py` performs the same lossless subdivision (Python 3 is only needed for maintenance). Always run `bash scripts/prepare.sh` and the compatibility tests before accepting a changed patch series. A structural split is not itself a rebase; source pin upgrades are reviewed separately.

Patch files use Git binary diff format with full blob IDs for three-way application. After resolving a candidate conflict against the newer original sources, regenerate the affected patch and update the submodule pointers and corresponding `expected-tree` files.

## Migration checkpoints

Migration development now takes place in `main` under [migration/](migration/README.md).
These are **integration candidates**, not stable release inputs. They are tied
to historical audited snapshots; the latest locked fork changes must be
reconciled before promoting the new Core and sing-box pins.

Run the independent checkpoint replay:

```sh
python3 scripts/verify-migration-checkpoints.py --report .build/migration-checkpoint-report.json
```

The separate upstream merge audit compares the current locked source trees
against the fixed migration target. Its reviewed resolutions and dependency
candidates are diagnostic artifacts, not a successful integrated build.
Both verification workflows can be dispatched manually.

Original project licenses and notices apply to the patched sources and resulting binaries.
