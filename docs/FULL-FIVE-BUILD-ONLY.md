# Explicit full-five native-input preparation

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
The extractor creates missing parent directories and exclusively creates each
variant directory before invoking `unzip`; existing empty, partial or populated
destinations fail rather than being reused or overwritten. Extraction failures
retain the command, exit code, diagnostic log and any partial tree, without a
success manifest. Real `unzip` fixtures exercise all six fresh nested paths,
pre-existing destination/parent conflicts and failed extraction; no fixture
executes configure or a compiler.
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
preserved: static libraries/data, disabled shared/tools/extras/tests/samples/dyload,
existing host/build triplets, `-stdlib=libc++` and C++17. Deployment flags now
explicitly follow the per-variant policy below: **13.4 for devices and Intel
simulators; 14.0 for ARM64 simulators**.
There are no default-toolchain or missing-SDK success fallbacks.

The first expanded run,
[34835674087](https://github.com/unoplatform/uno.icu/actions/runs/34835674087)
at `bd49e19c7cacc4b2f3af7443f9c3a2e7fe6ad673`, built the native ARM64 host and
discovered Xcode 16.4 / iPhoneOS 18.5 and its compiler/archive tools. It failed
before the first `ios-arm64` configure: `unzip` could not create the destination
because `artifacts/apple-sources` did not exist. The fresh-directory preparation
above corrects that lifecycle failure; this failed run is not retroactively a pass.

The approved retry,
[34851642559](https://github.com/unoplatform/uno.icu/actions/runs/34851642559)
at `16214039a82a7b457e2f928334b060a1e010cae3`, advanced through the iOS ARM64 device
build and then failed validation of `iossim-arm64/libicuuc.a`, after its make
command completed. The simulator C/C++ and pkgdata commands requested 13.4.
The rejected archive was not retained because copying followed validation.
Consequently its exact offending member and actual platform/minimum/SDK fields
are **unknown**; no fixture or other-run archive can fill that evidence gap.
Both retained device archives were independently decoded: all 202 objects are
ARM64, platform 2, minimum 13.4. Common objects report SDK 18.5; the assembly data
object reports SDK 0, which is not a platform/minimum failure.

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
Declared and evaluated `CFLAGS`/`CXXFLAGS` and pkgdata `COMPILE` must each select
the exact variant architecture, SDK and deployment flag. Conflicting target,
sysroot, platform or minimum overrides fail; the data archive cannot silently
use a different target from the common library.
Before/after generated makefiles, configuration hashes and evaluations are
retained; selection or configuration changes fail receipt verification.

Command paths are shell-quoted and parsed as single selected executables.
ICU's pkgdata template can remove quoting from a space-containing archiver path;
an ambiguous generated command is rejected, not silently split or substituted.
The earlier tool checks executed on the first two variants in the retry.
The new target/minimum checks still require qualification on an approved run.
No claim of byte reproducibility, protected attestation or native acceptance
follows from the receipts alone.

## Platform-aware archive acceptance

An ar header or ARM64 CPU is insufficient: iOS/tvOS device and simulator
archives can share the same CPU. `eng/apple_icu.py` checks every non-symbol
object member, including BSD extended/GNU long names, and validates archive
and universal-slice bounds.

Every object must be a matching 64-bit Mach-O object with exactly one explicit
`LC_BUILD_VERSION`, the exact per-architecture minimum and required platform:

| Final directory | Platform ID | Exact deployment minimum |
| --- | --- | --- |
| `ios` | 2, iOS device | ARM64: 13.4 |
| `iossim` | 7, iOS simulator | ARM64: 14.0; x86_64: 13.4 |
| `tvos` | 3, tvOS device | ARM64: 13.4 |
| `tvossim` | 8, tvOS simulator | ARM64: 14.0; x86_64: 13.4 |

The ARM64 simulator floor is defined by Apple/Swift LLVM's
[`Triple::getMinimumSupportedOSVersion`](https://github.com/swiftlang/llvm-project/blob/901f89886dcd5d1eaf07c8504d58c90f37b0cfdf/llvm/lib/TargetParser/Triple.cpp#L2055-L2074).
Its [Darwin driver](https://github.com/swiftlang/llvm-project/blob/901f89886dcd5d1eaf07c8504d58c90f37b0cfdf/clang/lib/Driver/ToolChains/Darwin.cpp#L3521-L3524)
enforces supported minima. This proves the old ARM64 simulator recipe requested
an unsupported minimum, not the missing archive's actual bytes or that this
public source exactly built the hosted Apple clang binary. Device and Intel
simulator 13.4 support is unchanged. Validation accepts neither arbitrary higher
versions nor an ARM64 simulator claiming 13.4.

Ambiguous legacy version-min tags are rejected; platform is not inferred from
CPU alone. Empty/thin-reference/malformed archives, missing or overlapping fat
slices, mixed member platforms and wrong deployment minima fail. Simulator
fat slices must hash to the exact separately validated thin archives.

`apple-build.json` schema 2 records `minimumDeploymentByVariant`; archive
identities record `minimumDeploymentByArchitecture`, including both distinct
simulator slice minima. It binds all six recipes, source archive/filter hashes,
SDK/compiler identities, host manifest/tools and per-member archive identities.
The full assembler revalidates that receipt and all retained thin/final archives,
requires the exact same-run sixteen producer bundle set, and checks host and
universal-input manifest dependencies. All notices and producer evidence are
retained. These inventories are not protected attestations.

Both existing raw archive files are retained byte-for-byte, including zero-length
and truncated files, before validating either one. This retention-only copy does
not relax the normal nonempty payload-copy guard or overwrite existing evidence.
A rejected archive gets an `archive-validation-failure.json` containing its
hash (including the SHA-256 of an empty file), variant, expected target and
validation error. Member-level errors include actual CPU/platform/minimum/SDK
when those fields can be decoded.

If an archive is missing or cannot be read/written, retention still attempts the
sibling and fails before content validation. When the evidence directory is
writable, `archive-retention-failure.json` records each I/O error and hashes/sizes
only for successfully retained files. Missing bytes are not fabricated, and
previous or partially written evidence is not deleted or treated as successful
retention. Rejection prevents that variant's payload promotion and any complete
Apple manifest.
The next failure can therefore be independently decoded without accepting or
rewriting its bytes. Current tests include retained-device metadata controls
and explicit simulator policy fixtures, **not a recovered failing archive**.

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

Both approved expanded attempts were consumed; publishing or executing another
reviewed retry requires fresh parent/user approval. A successful three-host raw-smoke run does
not cover these new Apple archives, universal macOS execution, Windows ARM64/
WASM runtime, physical AT or full GA. The expanded host/SDK/platform evidence
remains missing until actual execution. Unit fixtures test binary formats and
selection/receipt failures; they are not native compilation or runtime proof.
