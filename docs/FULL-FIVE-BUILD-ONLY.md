# Explicit full-five native-input preparation (unexecuted)

`main.yml` adds **`operation=build-full-five`**, separate from existing
`build-only` and `raw-smoke`. This is native-input preparation only. It never
invokes `Pack.ps1`, signing, NuGet publication, tags, releases or attestation.
No package version is chosen or reserved.

The current three-package default remains unchanged:

| Scope | Required approval | Producer bundles | Unique native/data payloads | Staging artifact |
| --- | --- | --- | --- | --- |
| `build-only` | native=true, release=false, apple=false | 15 | 15 | `build-only-three-package-staging` |
| `build-full-five` | native=true, release=false, **apple=true** | 16 | 23 | `build-only-five-package-staging` |

The full operation requires `target=all`, a full reviewed `expected_sha`, the
known owner-repository branch/workflow identity, a clean checkout and typed
boolean authorization. `authorize_apple` defaults false. The reusable child
also requires `full_five=true`; both child flags default false. Mixed scopes,
partial targets and malformed flags fail closed. Existing build-only, raw-smoke
and release routes reject an Apple grant rather than silently expanding scope.
Permissions remain `contents: read`, without inherited secrets or production
environments. Standalone child dispatch, where registered, requires the same
two explicit full/Apple flags and normal source/native authorization.

## Additional work and required expanded approval

Before publication or dispatch, the parent must obtain/confirm explicit approval
for **the expanded iOS/tvOS hosted native work**, not infer it from the accepted
Windows/macOS runtime probes or the old three-package build grant.

The additional work is performed in the existing **native ARM64 `macos-15`
job**, after its normal ICU host build finishes:

1. Verify that job's same-source/run/attempt ARM64 native manifest and the actual
   newly built host tree. Require both `config/icucross.mk` and
   `config/icucross.inc`, their generating configuration, all selected host
   tools and their ICU dylib dependencies (details below). Hash the files and
   inspect executable/library Mach-O CPU/platform/type.
2. Execute six isolated cross-builds: iOS device ARM64, iOS simulator ARM64 and
   x86_64, tvOS device ARM64, tvOS simulator ARM64 and x86_64.
3. Merge each simulator's two CPU archives with `lipo`, producing two library
   outputs per simulator family.

The host tree is reused **within that job**, never reconstructed from an old
thin-library artifact. Each variant gets a fresh source/intermediate directory.
Successful variant trees are removed only after archives, recipe and config
evidence have been retained; failures retain their working tree and diagnostics.
This bounds peak working trees to the native host plus one cross variant, while
retaining all twelve thin archives and eight final package-input archives.

Only the full-mode ARM64 macOS job receives the extended **180-minute timeout**;
the existing default remains 90 minutes. Other runners/matrices remain unchanged.
The parent approval should cover the existing complete native matrix plus six
Apple cross-compilations, two simulator merge operations, the four Xcode SDKs
and retained static-archive storage. Actual peak RAM/disk/time is **unmeasured**.
There is no local SDK installation or local heavy build in this implementation.

Required SDK selectors are `iphoneos`, `iphonesimulator`, `appletvos` and
`appletvsimulator`. SDK paths/version/build IDs, settings hashes and selected
clang/clang++/ar/ranlib/lipo paths/hashes are retained. `CC`, `CXX`, `AR` and
`RANLIB` explicitly select those recorded paths, overriding inherited values
without changing machine or process `PATH`. Existing configure options are
preserved: static libraries/data,
disabled shared/tools/extras/tests/samples/dyload, existing host/build triplets,
`-stdlib=libc++`, C++17 and the platform-specific **13.4 minimum** flags.
Actual SDK availability and recipe execution remain unverified until an approved
hosted run. There are no default-toolchain or missing-SDK success fallbacks.

## Consumed host inputs and archive-tool selection

The locked ICU 77.1 `data/BUILDRULES.py` and current additive filter select
`gencnval` (converter aliases), `genbrk` (break rules), `gendict` (dictionaries),
`genrb` (resource trees) and `icupkg` (prebuilt data). The host receipt also
requires `pkgdata`, `genccode` and `gencmn` as the normal packaging tool set.
A changed, unsupported filter requires closure review rather than silently
assuming these eight executables suffice.

Both cross configs are consumed by `icudefs.mk`; the snapshot includes them,
`icudefs.mk`, root `Makefile`, `config/mh-darwin`, `config.status` and `config.log`.
These configuration bytes are retained and checked against their receipt.
`libicuuc`, `libicui18n`, **`libicutu`** and stub `libicudata` dylibs are mandatory.
All dylib files in the actual host search directories are hashed/type-checked;
direct and transitive ICU load-command dependencies, including versioned names,
must resolve there. Normal generators search `lib`, `stubdata`, `tools/ctestfw`;
`pkgdata` searches `stubdata`, `tools/ctestfw`, `lib`. Missing required inputs,
external file links, unresolved/non-system non-ICU dependencies, or local
makefile overrides fail preflight. System-library dependency names are recorded,
not presented as a complete OS-content proof. The host snapshot must remain
unchanged after each cross-build.

For each cross variant, inherited archiver/indexer values, archive flags,
make overrides and host-loader overrides cannot change the selected tool set.
An empty incoming `ARFLAGS` preserves ICU's normal generated `r` plus Darwin
`-c` flags. After configure, the builder generates the actual `data/icupkg.inc`,
then uses metadata-only GNU make targets to inspect the effective root and data
tool variables without executing an archiver. It reconciles `icudefs.mk`,
evaluated make variables and pkgdata's actual `AR`/`ARFLAGS`/`RANLIB`/`COMPILE`
configuration with the recorded SDK tools. Host paths, invocation search order,
pkgdata's `-O` configuration and generated data-rule tool names are also checked.
Before/after generated makefiles, configuration hashes and evaluations are
retained; selection or configuration changes fail receipt verification.

Command paths are shell-quoted and parsed as single selected executables.
ICU's pkgdata template can remove quoting from a space-containing archiver path;
an ambiguous generated command is rejected, not silently split or substituted.
These checks are locked-source reasoning and synthetic configuration tests
until an approved Apple run executes the real generated-make/configure path.
No claim of byte reproducibility, protected attestation or native acceptance
follows from the receipts alone.

## Platform-aware archive acceptance

An ar header or ARM64 CPU is insufficient: iOS/tvOS device and simulator
archives can share the same CPU. `eng/apple_icu.py` checks every non-symbol
object member, including BSD extended/GNU long names, and validates archive
and universal-slice bounds.

Every object must be a matching 64-bit Mach-O object with exactly one explicit
`LC_BUILD_VERSION`, minimum **13.4**, and the required platform:

| Final directory | Platform ID | Architectures |
| --- | --- | --- |
| `ios` | 2, iOS device | ARM64 |
| `iossim` | 7, iOS simulator | ARM64 + x86_64 |
| `tvos` | 3, tvOS device | ARM64 |
| `tvossim` | 8, tvOS simulator | ARM64 + x86_64 |

Ambiguous legacy version-min tags are rejected; platform is not inferred from
CPU alone. Empty/thin-reference/malformed archives, missing or overlapping fat
slices, mixed member platforms and wrong deployment minima fail. Simulator
fat slices must hash to the exact separately validated thin archives.

`apple-build.json` binds all six recipes, source archive/filter hashes,
SDK/compiler identities, host manifest/tools and per-member archive identities.
The full assembler revalidates that receipt and all retained thin/final archives,
requires the exact same-run sixteen producer bundle set, and checks host and
universal-input manifest dependencies. All notices and producer evidence are
retained. These inventories are not protected attestations.

## Final input shape and pack guard

The expanded staging adds exactly:

```text
nuget/uno.icu-ios/ios/{libicuuc,libicudata}.a
nuget/uno.icu-ios/iossim/{libicuuc,libicudata}.a
nuget/uno.icu-tvos/tvos/{libicuuc,libicudata}.a
nuget/uno.icu-tvos/tvossim/{libicuuc,libicudata}.a
```

The only ignore-policy additions are the exact generated `tvos/` and `tvossim/`
directories under `nuget/uno.icu-tvos`, matching the existing iOS policy.
No `.a` wildcard or source-directory ignore is added. `Pack.ps1` and its
five-package/source/version guards remain unchanged and fully enforced.
Full staging is still **not a shipping package**, a version reservation, or
permission to pack/publish.

## Candidate dispatch, after expanded approval

```powershell
gh workflow run main.yml --repo unoplatform/uno.icu `
  --ref <reviewed-feature-ref> `
  -f operation=build-full-five -f authorize_native=true -f authorize_release=false `
  -f authorize_apple=true -f target=all -f expected_sha=<new-reviewed-source-commit>
```

This command has not been executed. A successful three-host raw-smoke run does
not cover these new Apple archives, universal macOS execution, Windows ARM64/
WASM runtime, physical AT or full GA. The expanded host/SDK/platform evidence
remains missing until actual execution. Unit fixtures test binary formats and
selection/receipt failures; they are not native compilation or runtime proof.
