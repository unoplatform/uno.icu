# Explicit hosted native validation (no publication)

This entry prepares **new** ICU 77.1 data/libraries for the Windows, macOS and
WASM packages. It cannot close historical 77.2.1 provenance, runtime acceptance,
or a GA gate. It preserves current main's `77.4-dev.{height}`, both Emscripten
toolchains and full iOS/tvOS release matrix; see [reconciliation](UPSTREAM-RECONCILIATION.md).
No old package bytes are inputs. A successful local unit test is
not a protected hosted job identity or attestation.

## Authorization boundary

The approved feature-branch route is the **already registered `main.yml`
dispatcher** (workflow ID `194395219`), with `operation=build-only`. Its
`build_only` job calls `./.github/workflows/build-only.yml` using `workflow_call`,
passing exactly four explicit inputs and `contents: read`, without a `secrets`
mapping or `secrets: inherit`. The local child resolves from the same reviewed
commit; it need not be registered on the default branch.

The child also retains standalone `workflow_dispatch` for repositories where
that entry is already registered. Both paths require:

* `authorize_native`: boolean, default **false**. The owner must separately
  approve source publication, the exact source/workflow SHA, and hosted capacity.
  An input is an acknowledgement, **not** a GitHub environment approval mechanism.
* `authorize_release`: boolean, default **false** for dispatch. It must remain
  false. A mixed release/native grant is rejected by the caller, child and entry.
* `expected_sha`: required full lowercase 40-character commit. The checkout,
  `github.sha` and `github.workflow_sha` must all match, with no local changes.
* `target`: fixed choice `all` (default), `linux`, `windows`, or `macos`.
  No shell text, source override, image override, runner label, or publish input.

Only `unoplatform/uno.icu` branch dispatches are accepted. The entry recognizes
exactly `main.yml@<ref>` with actual dispatch operation `build-only`, or
`build-only.yml@<ref>` without a dispatcher operation. Reusable workflows retain
the caller's GitHub event/workflow context; the event must still be
`workflow_dispatch`, not a fabricated `workflow_call` event.
Forks, tags, PR refs, other callers, cross-ref/SHA identities, automatic events
and release operations fail closed. Checkouts do not
persist credentials. All new actions are pinned to full commits.

Guards use `toJSON` to distinguish typed booleans from strings/numbers, including
the YAML-to-process environment boundary. Neither `"false"` nor `0` is accepted
as boolean false. Release authorization and every sign/publish job require
**exactly** `release-dev` or `release-prod`, release=true and native=false.
`operation=build-only` cannot reach them even with release=true or malformed flags.

The child workflow has `contents: read`, no job permission escalation, no reusable
release workflow calls, no production environment, no secret references, no
OIDC/attestation permissions, no NuGet pack/sign/publish entrypoint, and no
repository mutation commands. GitHub's normal artifact runtime capability is
used only for same-run download/upload. `authorize` installs only
`tests/requirements.txt` in an ephemeral job-local Python virtual environment.
Native jobs use preinstalled hosted toolchains; no .NET SDK is needed.

**This boundary is not immutable against repository writers.** An owner must
review the published workflow/scripts and enforce their own branch/status
policy. Local manifests cannot prove a protected identity. No repository
settings/protections are changed here, and no approval is inferred from WRITE.

## Trigger and permission graph

| Entry/event | Reachable work | Write/secret access |
| --- | --- | --- |
| `build-only.yml`, no manual authorization | No jobs | None |
| `build-only.yml`, authorized exact-SHA branch | Graph below, fixed selected group | `contents: read`, Actions artifact upload only |
| `main.yml`, push/PR to main/release | Provenance/workflow contracts only | `contents: read` |
| `main.yml`, manual default `contracts` | Contracts only | `contents: read` |
| `main.yml`, `build-only`, native=true/release=false | Local build-only child, then graph below | Explicit `contents: read`, no inherited secrets |
| Either entry, mixed native/release grant | No native/sign/publish jobs | None beyond read-only contracts |
| `main.yml`, explicit authorized `release-dev` on main | Existing full native/package/smoke/sign + dev publish | Legacy signing/OIDC/attestation/NuGet access, only after authorization |
| `main.yml`, explicit authorized `release-prod` on release branch | Existing full native/package/smoke/sign + production publish/tag/release | Legacy `PackageSign`/`Production` gates and write permissions |
| Conventional-commits PR workflow | Read-only commit validation | `contents: read`, no token env or persisted checkout credentials |
| Labeler | Manual informational echo only; automatic label action removed | `contents: read` |

```text
registered main dispatch operation=build-only
  -> local build-only child, native=true/release=false
     -> authorize + exact SHA + contracts -> source archive/notices/inventory
  -> data (Linux Docker) -------------------------------+
  -> wasm [3.1.56, 5.0.6] x [st, mt, st,simd, mt,simd] -+
  -> windows [x64, arm64] (Windows 2022 / VS2022) -------+-> staging (all only)
  -> macos [x86_64, arm64] -> universal lipo ------------+
```

`macos` jobs use `macos-15-intel` and `macos-15` respectively, check native host
architecture, and validate each output with `lipo -verify_arch`. Universal merge
uses **only the two exact same-run manifests/paths**, not a wildcard including
cross-build directories. Windows retains `common;stubdata`, static CRT, SDK
10.0.26100.0 and x64/ARM64, with one matrix row at a time. The build-only runner
uses `windows-2022` because the observed `windows-2025` label now selects VS2026,
outside the pinned ICU VS2022/v143 path. See [Windows prerequisites](WINDOWS-PREREQUISITES.md).
WASM retains Emscripten
3.1.56 **and 5.0.6**, each with all four thread/SIMD variants (eight jobs),
at most two concurrently.

Native rows have 90-minute timeouts, no arbitrary sleeps or success fallback.
Failed commands retain logs with nonzero exit codes but produce no success
`artifact.json`. Dependent aggregation does not run after a failed/missing row.
This is not a runtime smoke job: no runtime/UI/physical-input claim is made.

## Candidate commands — **not authorization to execute**

**No new default-branch registration or main merge is required.** Dispatch the
existing registered `main.yml` on the published reviewed feature ref. The
feature version supplies the new inputs and same-commit local child. Do not
request or perform a default-branch merge as a prerequisite for this route.
Parent/owner retains publication and actual dispatch after safety review,
without force-pushing or overwriting newer commits. Existing
`pull_request_target` workflows from the remote **base** and installed Apps
can act before these local policy changes are merged. Audit/approve that
publication consequence separately; this checkout does not control those Apps.

After those approvals and a capacity grant, a parent may execute, substituting
the **published, reviewed** branch and commit (not an unpublished local SHA):

```powershell
gh workflow run main.yml --repo unoplatform/uno.icu `
  --ref <reviewed-branch> `
  -f operation=build-only -f authorize_native=true -f authorize_release=false `
  -f expected_sha=<full-reviewed-commit> -f target=all
```

`target=linux`, `windows`, or `macos` bounds a separate resource grant. Partial
runs retain their row artifacts but **cannot** produce complete three-package
staging or be mixed into another run. `GITHUB_RUN_ID`, attempt, workflow and
source SHA must match for aggregation. Rerun **all** jobs for fresh complete
evidence; failed-job-only reruns with stale successful artifacts fail closed.

Actual candidate job entry commands (from authorized clean hosted checkouts):

```text
python3 eng/build_only.py authorize
python3 eng/build_only.py source
python3 eng/build_only.py build --kind data
python3 eng/build_only.py build --kind wasm --emscripten 3.1.56 --variant st
python3 eng/build_only.py build --kind wasm --emscripten 3.1.56 --variant mt
python3 eng/build_only.py build --kind wasm --emscripten 3.1.56 --variant st,simd
python3 eng/build_only.py build --kind wasm --emscripten 3.1.56 --variant mt,simd
python3 eng/build_only.py build --kind wasm --emscripten 5.0.6 --variant st
python3 eng/build_only.py build --kind wasm --emscripten 5.0.6 --variant mt
python3 eng/build_only.py build --kind wasm --emscripten 5.0.6 --variant st,simd
python3 eng/build_only.py build --kind wasm --emscripten 5.0.6 --variant mt,simd
python eng/build_only.py build --kind windows --variant x64
python eng/build_only.py build --kind windows --variant arm64
python3 eng/build_only.py build --kind macos --variant x86_64
python3 eng/build_only.py build --kind macos --variant arm64
python3 eng/build_only.py merge-macos --incoming artifacts/incoming
python3 eng/build_only.py assemble --incoming artifacts/incoming
```

Do **not** fabricate `GITHUB_*` environment variables to present local commands
as a hosted run. This implementation contains no local authorization shortcut.

### Repository and resource prerequisites

* Parent's explicit approval to publish the reviewed source and use hosted
  resources. Public repository WRITE can permit dispatch, but does not grant
  an org policy exception, permission to merge, or release authorization.
* Actions enabled; owner policy permits the pinned checkout/upload/download
  actions, public dependency downloads and the chosen standard hosted labels.
  No self-hosted runner or shared workstation is selected.
* Linux x64 with an available Linux Docker/BuildKit daemon; public Docker
  registry, Ubuntu apt repositories and GitHub/codeload reachable. No login or
  container registry push. A data/WASM job should reserve roughly 20 GiB disk
  and 8 GiB RAM (planning estimates, **not measured peak usage**).
* Windows 2022 x64: stable Visual Studio 2022 v143, MSBuild, SDK 10.0.26100.0,
  PowerShell 7 and Python 3. The x64 row requires its x86/x64 C++ component;
  the ARM64 row requires its ARM64 C++ component. Both rows remain mandatory.
  Unfiltered instance/component inventory is retained before version/component
  selection. Missing components fail; the job does not install/repair toolchains.
* macOS Intel and ARM64 hosted capacity, Xcode/command-line tools, `unzip`,
  Python 3, make, clang, `lipo`, `otool`. iOS/tvOS SDKs are not needed for this path.
* Budget for 14 native/merge row jobs plus authorization and aggregate staging,
  concurrency above, and artifact storage. Each artifact expires after 90 days;
  owners must retain evidence elsewhere **under separate archival approval**
  before expiry. This workflow never creates a release for indefinite storage.

## Output and evidence contract (schemaVersion 1)

`artifacts/build-only/<row>/artifact.json` is written only after the exact row
payload paths exist and pass native/data header checks. Headers and lipo
architecture checks are **not** API/data runtime acceptance. The manifest records:

* row/variant/`emscriptenVersion`, Uno commit and unchanged ICU source/archive/license lock;
* canonical `gitBlobSha256` plus actual checkout `inputSha256` (Windows EOL
  conversion can differ), image lock, hosted image identifiers, Python/OS and
  nonsecret run/job/workflow identities;
* every archived file's SHA-256 except its own, and input artifact-manifest
  hashes for derived rows; complete source, notices, tool logs and native outputs;
* explicit `authenticatedAttestation: false`, `byteReproducible: false`,
  `runtimeTested: false`, `shippingPackage: false`.

Full upstream ICU LICENSE bytes come from the verified source archive. Uno
license/NOTICE use canonical Git blobs, independent of runner EOL conversion.
Each artifact carries `LICENSE.txt`, `NOTICE.md`, and both separate license
texts. The source artifact includes the full pinned `icu-source.zip`.

Windows retains unfiltered and filtered `vswhere` inventories, the selected
VS instance/version/component identities, MSBuild binlog, exact selected MSVC
tool version/hashes, and selected SDK header/library hashes (not a full SDK
inventory). It also records before/after hashes for the static-CRT project patch (without
changing encoding or line endings of extracted sources). macOS retains clang hash/version, Xcode/SDK versions, configure
logs, lipo and dependency reports. Docker retains the inspected immutable
manifest, Docker/BuildKit versions, installed apt package versions, compiler
and make versions, configure logs and the build output log.

`eng/build-images.json` pins the Linux/amd64 Ubuntu 24.04 manifest and Emscripten
3.1.56 and 5.0.6 Linux/amd64 manifests. Only small registry metadata was read to resolve these pins;
no image layers were pulled locally. Apt repositories and hosted images still
float. Recorded versions and source hashes do **not** prove byte reproducibility.

For `target=all`, `build-only-three-package-staging` retains all input row
bundles (including notices/logs/manifests) and an exact 15-file `payload` tree:

```text
payload/nuget/icudt.dat
payload/nuget/uno.icu-win/libicu/{x64,arm64}/{icuuc77,icudt77}.dll
payload/nuget/uno.icu-macos/libicu/{libicuuc,libicudata}.dylib
payload/nuget/uno.icu-wasm/buildTransitive/native/unoicu.a/{3.1.56,5.0.6}/
  {st,mt,st,simd,mt,simd}/unoicu.a
```

The last two WASM directory names each include a comma. These are the existing
package input shapes, **not nupkgs**. No iOS/tvOS placeholders are created and
`eng/Pack.ps1` still requires the full five-package release matrix. Any future
three-package pack/runtime validation or protected attestation is a separate,
explicitly authorized follow-up; do not waive the missing rows.

## Native smoke is stage two, not a build claim

This route intentionally completes **stage one: build and inventory** first.
Every row's `runtimeTested=false` remains truthful. A successful header/lipo
check is not ICU API/data execution, signing or attestation acceptance.

The next independently reported probe stage must:

1. Verify and select the **same run/attempt/source** data bundle plus the new
   Windows x64 row and each matching macOS thin row; check all recorded hashes.
2. Reuse the assertions in `tests/icu_smoke.py` for ICU 77.1, selected locales,
   line breaking, script properties and bidi, using those exact libraries and
   `icudt.dat` in fresh process-local directories on matching native runners.
3. Record separate probe JSON identifying the input bundle manifests and actual
   library/data hashes. Preserve, rather than rewrite, the build manifests.

The [raw-library/data adapter](RAW-SMOKE.md) now reuses those assertions through
a fresh isolated worker. It is **not wired into these workflows and has not
executed newly built native bytes merely because its unit contracts pass**.
The existing nupkg probe remains supported separately. Do not fabricate packages,
use old payloads, bypass the five-package pack guard, or claim smoke success from
`runtimeTested=false`. Parent-owned native UI/physical AT checks follow coherent
artifacts and are separate again. Self-reported inventories do not close
signature, protected-attestation or historical proof requirements.

## Focused regression procedure

Reuse installed dependencies or an owned venv containing `tests/requirements.txt`:

```powershell
python -m unittest discover -s tests -v
python tests/notice_pack.py --archive <verified-cached-icu-source.zip> `
  --nuget <verified-cached-nuget-6.14.0.exe> --work-directory <new-owned-directory>
actionlint -shellcheck= -pyflakes= .github/workflows/build-only.yml `
  .github/workflows/main.yml .github/workflows/conventional-commits.yml `
  .github/workflows/labeler.yml
git diff --check
```

The policy tests evaluate the **actual YAML guards**, including the complete
600-case operation x native/release boolean x ref x repository x event table,
1,500 malformed-flag cases, caller/ref/SHA mismatches and direct-entry opt-in.
They model Actions truthiness/loose comparison and verify explicit child
permissions/input forwarding with no secret inheritance. This is local policy
coverage, not a hosted workflow execution or protected job identity.
Entry tests also cover
wrong source/run/scope, incomplete/wrong-architecture payloads, and retained
failed-command evidence. The NuGet fixture packs real full notices only and is
explicitly nonshipping. Actionlint validates Actions YAML/contexts/graphs;
disabling its optional ShellCheck/Pyflakes integrations does not validate
native toolchain execution. Keep results separate from native compile, runtime,
physical-input and historical provenance evidence.
