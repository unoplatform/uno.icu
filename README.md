# Uno ICU

Uno's native ICU packages and WebAssembly shim. NuGet package versions are
packaging versions, **not upstream ICU versions**.

## Source-pinned current-main build preparation

This prospective change is based on current `main`, Uno commit
`f701123f264b9329b159617657120e383df3b943`. It preserves the **77.4-dev.{height}**
version line, Emscripten **3.1.56 and 5.0.6**, the separate **tvOS** package and
device/simulator jobs, corrected repository metadata and .NET 10 tagging SDK.
ICU remains **77.1**, with the Windows static-CRT patch, macOS/iOS/tvOS configure
options, shim, all four WASM thread/SIMD variants **for each toolchain**, and
the existing 62-locale data filter.

The historical 77.2.1 reconstruction and its `77.2.2-provenance` proposal remain
in a separate preserved worktree/commit. They are not the prospective package
version or a reason to discard newer upstream work. See
[upstream reconciliation](docs/UPSTREAM-RECONCILIATION.md).

`eng/source-lock.json` pins ICU commit
`457157a92aa053e632cc7fcfd0e12f8a943b2d11`, the downloaded archive's SHA-256 and
the **entire** upstream LICENSE's SHA-256. All native jobs consume that checked
archive; Docker independently checks its bytes. A changed codeload archive
fails verification, even if it appears to contain equivalent source. Investigate
and review any lock update; do not refresh hashes automatically.

`version.json` is byte-identical to current main's **77.4-dev.{height}** line.
NBGV determines the precise version in CI. `eng/Pack.ps1` requires the current
configured major/minor and rejects old 77.2/77.3 identities and existing output
directories. Never repack/restamp an old archive
or use an old package's native files as replacement build outputs.

### License ownership

Each of the five packages declares a NuGet file license, `LICENSE.txt`.
It contains the complete upstream LICENSE (including third-party notices)
and the separate Uno license. The same exact texts appear separately as
`licenses/ICU-LICENSE.txt` and `licenses/Uno-LICENSE.md`.
`NOTICE.md` explains their scopes and is the package readme. Uno's license does
not relicense ICU or its data. Retaining all upstream notices is deliberate.

NuGet's extensionless-file mapping treats `src="ICU-LICENSE" target="licenses"`
as a **file named `licenses`**, not a directory. The `.txt` destination name is
intentional; its contents remain byte-identical to upstream LICENSE.

The old checked-in `nuget/nuget.exe` is **4.7.1** and rejects `<license>`.
It is preserved, not replaced. `eng/tools-lock.json` pins a separate
NuGet **6.14.0** executable by URL and SHA-256. It is downloaded only into the
owning checkout's `artifacts/tools`; no restored package or global cache is
changed. The normal pack entrypoint and notice contract verify that hash.

## Build and verification

Use a fresh owned checkout per native architecture/build and retain failing
outputs. Python 3 and PowerShell 7 are required for metadata/package work.
The existing Windows, macOS/iOS/tvOS and Linux Docker toolchains remain required for
native compilation. Arrange shared-machine capacity before those builds.

For **build-only hosted validation**, dispatch the already registered `main.yml`
on the reviewed **feature ref** with `operation=build-only`,
`authorize_native=true`, `authorize_release=false`, `expected_sha` and `target`.
It calls the isolated [`build-only.yml` child](docs/BUILD-ONLY.md) with explicit
inputs, `contents: read`, and **no inherited secrets**. No new default-branch
registration or merge is required or authorized for this route.
The child has no connection to signing,
NuGet push, release/tag creation, or production environments. Its source/native
inventories are not attestations. Ordinary push/PR events run only lightweight
contracts; they cannot start native jobs or publication in the workflows in
this revision. Remote default-branch automation must still be reviewed before
publishing a branch/PR; local edits do not disable installed owner automation.

Fast contracts (the provenance/native-entry tests use the standard library;
workflow policy tests additionally require the pinned PyYAML in
`tests/requirements.txt`). Reuse an existing compatible installation, or install
that manifest in an **owned Python virtual environment**, not a shared cache:

```powershell
python -m unittest discover -s tests -v
python eng/provenance.py fetch-source --archive artifacts/icu-source.zip
python eng/provenance.py fetch-tool --path artifacts/tools/nuget-6.14.0.exe
# Windows: small, nonshipping package fixture, NOT a native ICU package build.
python tests/notice_pack.py --archive artifacts/icu-source.zip `
  --nuget artifacts/tools/nuget-6.14.0.exe `
  --work-directory artifacts/notice-contract-unique
```

### Native commands after resource approval

`.github/workflows/main.yml` retains the full release matrix including iOS/tvOS,
separate from its approved `operation=build-only` route. Release jobs require
exactly `release-dev`/`release-prod`, `authorize_release=true`,
`authorize_native=false`, an exact `expected_sha`, and the matching main/release
branch. Mixed native/release authorization is rejected. Its default manual
operation is `contracts`. Automatic release-on-push is intentionally removed.
The build-only matrix excludes iOS/tvOS; it does not relax the five-package release
pack validation or treat an unverified Apple static row as passed.
The build-only manifests remain `runtimeTested=false`: this is stage-one native
construction. Same-run native/data smoke probes are a separate, explicitly
documented next stage, not implicitly covered by a successful build.
[`tests/raw_icu_smoke.py`](docs/RAW-SMOKE.md) can bind the completed source/data/
Windows-x64 or matching macOS-thin row artifacts and run the existing 62-locale,
line-break, script and bidi assertions in a fresh worker. It does not download
artifacts, change the build workflow, create packages, sign, or publish.

For example, the Linux filtered-data job is:

```bash
python3 eng/provenance.py fetch-source --archive icu.zip
cp icu.zip src/cldr_data/icu.zip
cd src/cldr_data
docker build --output type=local,dest=. .
```

One WASM archive (repeat all four true/false combinations for **both 3.1.56 and
5.0.6**; do not omit a toolchain or variant):

```bash
python3 eng/provenance.py fetch-source --archive icu.zip
cp icu.zip src/unoicu/icu.zip
cd src/unoicu
docker build --build-arg EMSCRIPTEN_VERSION=3.1.56 \
  --build-arg IS_MULTITHREADED=false --build-arg IS_SIMD_SUPPORTED=false \
  --output type=local,dest=out/3.1.56/st .
```

Use the workflow's final archive layout
`unoicu.a/<emscripten>/{st,mt,st,simd,mt,simd}/unoicu.a` (the last two names each
contain a comma). Windows requires Visual C++ x64/ARM64 and Windows SDK
10.0.26100.0; run the workflow's `/MT` patch and `common;stubdata` builds in an
initialized Visual Studio build process. macOS needs both architectures,
`lipo`, and Xcode/iOS/tvOS SDKs for the normal Apple cross-builds. No platform is
replaced with a host library.

After **new native builds** are staged by the existing workflow into `nuget/`,
commit reviewed source changes so the metadata has an honest source identity:

```powershell
pwsh -NoProfile -File eng/Pack.ps1 `
  -Version 77.4.0-dev.1 `
  -SourceArchive artifacts/icu-source.zip `
  -OutputDirectory artifacts/packages-unique
```

This is an example local version, not a reservation/publication of that identity.
Use CI's exact NBGV version for an official build. Preparation fails on dirty
or untracked build source and missing matrix members. It writes notices once;
repeat a failed build in a fresh checkout instead of overwriting evidence.

`tests/icu_smoke.py` loads the specified real Windows x64/macOS package in an
isolated directory, verifies its expected package hash and ICU 77.1, checks all
62 selected locales without fallback, exercises eight line-break cases,
six script properties (including a supplementary-plane character) and mixed
LTR/RTL bidi. The workflow runs Windows x64 and both macOS architectures
before signing. Example:

```powershell
python tests/icu_smoke.py --package <new-win-nupkg> `
  --expected-sha256 <computed-package-sha256> `
  --work-directory artifacts/runtime-unique --output artifacts/runtime.json
```

These are selected ICU API/data probes, not full ICU conformance, WASM
execution, Windows ARM64 runtime or physical-input/UI acceptance.

## Consumer/distribution evidence interface (schemaVersion 1)

Package contents:

* `provenance/source.json`: Uno repository/commit, exact package version,
  upstream lock, pack-tool lock, tracked build/filter/shim/input SHA-256 values
  and selected nonsecret build-run identifiers.
* `provenance/Uno.icu-<platform>.payloads.json`: exact shipped input entry names
  and SHA-256 values. Does not attempt to hash itself or NuGet-generated metadata.
* The license/readme files described above.

External `unsigned-packages.json` and `signed-packages.json` include package
SHA-256 plus `entrySha256` for **every non-signature entry**. Signing comparison
requires unchanged entry bytes, package set, version and source commit.
The validator requires signatures in the signed set but does not implement
signature trust verification; the workflow runs `dotnet nuget verify --all`
separately and fails on error.

The signing job attests the final signed **individual nupkgs**, not just a
merged Actions archive. It verifies the retained Sigstore bundle against the
repository, workflow path and source/signer commits with `gh attestation verify`.
`release-evidence` retains source archive, both inventories, verification JSON,
attestation bundle and native smoke results for 90 days. The stable-release
publish path additionally retains these in a GitHub release alongside the
exact signed packages, without clobbering an existing release. The existing
tagging composite cleans untracked files, so packages are downloaded again
after tagging rather than reconstructed.

The main/dev publish path has only Actions retention plus GitHub's attestation
service; before using it as a durable distribution source, owners must archive
its complete `release-evidence` externally or publish an immutable release.
Never describe 90-day retention as indefinite. The build-only path pins Docker image digests and captures installed package and
tool identities. The legacy release defaults still use base tags, and neither
path pins apt repositories or hosted runner images; this path establishes source/build
provenance, **not byte-reproducible native toolchains**.

NuGet.org countersigning may change the final archive hash. Preserve the
attested pre-ingestion signed bytes, verify the downloaded NuGet signatures,
and compare every non-signature entry to the retained signed inventory.
Do not pretend that the pre-ingestion nupkg SHA equals the downloaded SHA.
Any mismatch outside `.signature.p7s` remains a failure.

Signing requires owner-controlled `PackageSign` credentials/OIDC and GitHub
attestation permissions. Production publication requires the normal owner
approval. Adding the workflow is not evidence that it has run.

## Historical 77.2.1 limitation

Issue [#34](https://github.com/unoplatform/uno.icu/issues/34) requests exact
old-payload source provenance and notices. Valid Uno author/NuGet repository
signatures authenticate package content and publishing identity, but cannot
fill empty source metadata. A matching source tag, native version string,
timestamp and aggregate expired-artifact digest remain corroboration only.
Keep the old-byte gate open unless retained owner/build evidence binds those
specific payloads. A new source-pinned build does not retroactively attest them.
