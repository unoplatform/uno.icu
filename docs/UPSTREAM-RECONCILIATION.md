# Prospective main reconciliation

The public candidate starts at upstream
`f701123f264b9329b159617657120e383df3b943`, not the historical package commit
`27a8963ded97286e987b51c942ebd6fa937e89bf`. Those upstream histories diverge
8 ahead / 1 behind, with merge base `bf4479bbceb7843e50f86e0a2d24aa78cc34c75b`.

The preserved historical candidate `dc840a6b590a9e5fc3288007957a3bd97e2f9aaf`
was merged without rewriting it into a fresh current-main-based branch.
The original historical worktree, notices-only fixtures and evidence remain
separate. Its `77.2.2-provenance` proposal must not replace the current version.

## Reviewed conflict resolutions and preservation

| Surface | Resolution |
| --- | --- |
| `version.json` | Keep current main byte-for-byte: `77.4-dev.{height}`. Package validator follows the configured major/minor, rejecting 77.2/77.3. |
| `main.yml` package step | Retain upstream tvOS staging/dependencies and version summary; invoke the pinned notice/provenance pack helper for **five** packages. |
| Tag-release composite | Retain upstream `.NET 10.0.x`; keep pinned supported actions and the credential exception only in the explicitly gated production path. |
| iOS/WASM nuspecs | Retain corrected `unoplatform/uno.icu` project/repository URLs; add commit metadata and complete notices. |
| `1732e3d` Emscripten | Retain release matrix 3.1.56 + 5.0.6. Build-only now requires four thread/SIMD variants for each version, with separate immutable images and artifact identities. |
| `a30094b` tvOS | Retain device, simulator and universal jobs, SDK/deployment flags, package staging and RID selection targets. Add the same pinned source download, notices and package completeness checks; no new tvOS compile/runtime claim. |
| `51b76e8` / `b020e41` summary | Retain the NuGet summary and its single-quoted literal backticks. |

tvOS targets, ICU filter, shim, native configure flags and current NBGV version
are preservation contracts, not best-effort conflict choices. The build-only
three-package staging now has **15 native/data payload files** and **15 input
bundles**. iOS and tvOS remain in the normal full release matrix but are
explicitly unverified/excluded from this bounded hosted build-only scope.

## Why prospective validation is still preparation, not an old rebuild

At reconciliation, the public GitHub release API listed only `77.1.2`, with no
assets; `77.3.2` exists as a tag, not a discovered evidence-bearing release.
Inspected 77.3.2 NuGet nuspecs did not declare complete file licenses or exact
source commits. The latest observed version for **all five packages** was
`77.4.0-dev.1`; none of those nuspecs declared a license or a repository commit.
The exact responses are retained in local evidence. This was a metadata-only
inspection, not per-entry archive/signature/attestation verification. Neither
corrected URLs nor a newer package version establish an authenticated binding
for the historical 77.2.1 payload hashes.

No native package is rebuilt merely to imitate historical bytes. The candidate
prepares a source-pinned, notice-complete validation path on **current main's
semantics**. No package payloads from old or current releases are repacked.
Absence from the public records inspected is not proof that owners lack other
retained evidence. Historical proof remains open.

## Scope of approval and execution

The parent has authorization for an ICU-only public feature branch/PR and
Linux/Windows/macOS **build-only** validation. The parent retains actual
push/PR/dispatch after reviewing this reconciliation and its trigger boundary.
Signing, tags, releases and NuGet publication remain unauthorized.

The approved feature-ref route now dispatches the existing registered
`main.yml` with `operation=build-only`, native=true and release=false; its
read-only caller invokes `build-only.yml` with explicit inputs and no inherited
secrets. **No default-branch merge/registration is needed or authorized.**
Main's full release jobs are retained for compatibility but cannot run from
default push/PR or build-only operations and require separate explicit release
inputs with native=false. This continuation runs
only lightweight contracts, metadata checks, syntax parsers and a real
notices-only NuGet fixture. It does not use the local heavy-build slot or claim
native, physical AT, protected-status, attestation or GA acceptance.
