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
   newly built host tree. Require `config/icucross.mk`, configuration logs,
   ARM64 macOS `genrb`, `genccode`, `gencmn`, `icupkg`, `pkgdata` executables and
   host libraries. Hash the files and inspect their Mach-O CPU/platform/type.
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
clang/clang++/ar/lipo paths/hashes are retained. `CC`/`CXX` use those recorded
compiler paths. Existing configure options are preserved: static libraries/data,
disabled shared/tools/extras/tests/samples/dyload, existing host/build triplets,
`-stdlib=libc++`, C++17 and the platform-specific **13.4 minimum** flags.
Actual SDK availability and recipe execution remain unverified until an approved
hosted run. There are no default-toolchain or missing-SDK success fallbacks.

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
