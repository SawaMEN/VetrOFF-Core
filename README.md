# VetrOFF Core

Patch-first build of the **official** [Hiddify Core](https://github.com/hiddify/hiddify-core), [Hiddify sing-box](https://github.com/hiddify/hiddify-sing-box) and [ray2sing](https://github.com/hiddify/ray2sing). Source code is cloned from original maintainers, not from `SawaMEN/hiddify-sing-box`.

## How it works

1. `sources.lock` pins the exact Hiddify Core commit and the *matching* sing-box/ray2sing submodule commits.
2. `scripts/prepare.sh` clones Hiddify Core with its submodules, verifies all three SHAs and applies strictly ordered `git apply --check` patch stacks.
3. `scripts/build.sh` compiles the **integrated** Go CLI as `vetroff-core-OS-ARCH`; ray2sing and sing-box are linked into this executable, not packaged as separate binaries.
4. GitHub Actions builds on pushes and pull requests, offers manual builds, and publishes artifacts to GitHub Releases for tags `v*`. A separate weekly job reports new upstream revisions (it does not change locked versions silently).

## Current patch packs

| Source | Patch | Function |
| --- | --- | --- |
| Hiddify Core | `0001-vetroff-core-version.patch` | CLI branding and compile-time verification of both submodules |
| Hiddify sing-box | `0001-vetroff-flavor.patch` | VetrOFF build marker exported from the native protocol engine |
| ray2sing | `0001-vetroff-flavor.patch` | VetrOFF marker exported from the link/config converter |

All three are **real, independently applied patches**. They currently establish the reproducible build baseline; the second patch series adds the native VetroffProxy SDK outbound plus a validated, redacted link converter. Other fork-specific features still require porting.

## VetroffProxy integration

The `0002-*` patches register `{"type":"vetroff","link":"vetroff://..."}` as a native SDK outbound, parse valid `vetroff://` links in ray2sing, and pin the VetroffProxy SDK version in all Go module graphs. Invalid share links produce redacted errors. VetroffProxy is **not** sourced from the old Hiddify fork at build time; its self-contained SDK is pinned separately.

The `0003-go127-oom-profile.patch` avoids an unexported runtime symbol removed by newer Go toolchains; Windows builds disable the incompatible opt-in TCP Fast Open linkname tag.

## Commands

Requirements: Git, Bash, Make, Go matching the upstream `go.mod`, sha256sum, internet access.

```bash
bash scripts/prepare.sh
bash scripts/build.sh --reuse-sources
./dist/vetroff-core-linux-amd64 version
```

Use `bash scripts/build.sh` to prepare and build in one command. For cross-compile set `GOOS`, `GOARCH` and `CGO_ENABLED`. All targets use the CLI feature tags defined by the **original** Hiddify Core Makefile, with `NAIVE=0` to avoid platform-specific Cronet native libraries.

**Note:** standalone CLI is not the same as a full Android/iOS shared library. GUI apps and native Cronet libraries require separate build jobs; those are not provided here.

## Patch management

Place `git diff` format files in `patches/hiddify-core`, `patches/hiddify-sing-box` or `patches/ray2sing`. Add each filename to that module's `series`, in order. Patch failures stop the workflow; no `-X ours`, `-X theirs` or force-merges are used. Run `bash scripts/prepare.sh` after pin changes. Do **not** copy a new arbitrary sing-box commit without updating the Hiddify Core submodule pins.

For upstream update check: `bash scripts/check-upstream.sh`.

## Licensing

Retain Hiddify and dependency attribution and comply with the original projects' licenses. In particular, `hiddify-core/LICENSE.md` includes additional published terms; review before commercial redistribution.
