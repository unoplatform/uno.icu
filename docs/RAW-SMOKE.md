# Raw ICU/data smoke and unsigned input binding

This is a **local follow-up entry**, not a modification to an active hosted
build. `tests/raw_icu_smoke.py` consumes completed, owner-selected build-only
artifacts. It does not discover/download artifacts or observe/dispatch runs.
No workflow invokes it automatically in this change.

Prepared invocations for the already verified producer set are recorded in
[STAGE2-PROBE-CANDIDATES.md](STAGE2-PROBE-CANDIDATES.md). They are not execution
evidence and do not supply the missing Windows native rows.

Supported rows are `windows-x64`, `macos-x86_64`, and `macos-arm64`. macOS
requires a matching **thin** library CPU type, not the universal artifact.
The actual probe process must match the selected row. Windows ARM64 and WASM
execution remain unsupported. No native execution, physical AT or full ICU
conformance is established by the adapter's unit fixtures.

## Required input selection

The owner must review the source/build and supply **all** of:

* Full producer commit, run ID, run attempt and exact known workflow ref.
* Extracted `build-only-source`, `build-only-data`, and the selected native row
  from that same run/attempt/source.
* An independently selected SHA-256 for each row's `artifact.json`. Do not
  automatically trust whatever manifest happens to be beside a DLL.
* A clean committed checkout of the probe code, with the producer's Git commit
  object available locally. A hosted follow-up checkout needs suitable history
  (for example `fetch-depth: 0`); the adapter does not fetch or repair it.

The producer and probe commits can differ. The adapter reads Git **blobs at the
explicit producer commit**, never substitutes the probe checkout's current files
for the older producer inventory. It compares the complete `gitBlobSha256` map,
locked source archive/license and image identities, all row-file hashes, and
complete notices. The source bundle's actual `icu-source.zip` is verified.
The producer's `inputSha256` records platform checkout hashes (including possible
EOL conversion); those are retained declarations, not proof of compiler execution.

Runtime rows must be the original unsigned/unprobed build artifacts:
`authenticatedAttestation=false`, `runtimeTested=false`, `shippingPackage=false`.
Links/junctions, duplicate JSON keys, missing/extra payloads, mismatched source/
run/attempt/workflow, incorrect PE/Mach-O architecture or incomplete notices fail
before native loading. Other rows, including Windows ARM64, are not substituted.

## Candidate invocation on the matching host

Use actual completed artifact paths and owner-selected digests. These placeholders
are **not** a dispatch or a claim that any run has succeeded:

```powershell
python tests/raw_icu_smoke.py `
  --row windows-x64 `
  --producer-commit <full-producer-commit> `
  --producer-run-id <observed-run-id> --producer-run-attempt <observed-attempt> `
  --producer-workflow-ref unoplatform/uno.icu/.github/workflows/main.yml@refs/heads/<reviewed-branch> `
  --source-bundle <downloaded-build-only-source> --source-manifest-sha256 <owner-selected-sha256> `
  --data-bundle <downloaded-build-only-data> --data-manifest-sha256 <owner-selected-sha256> `
  --native-bundle <downloaded-build-only-windows-x64> --native-manifest-sha256 <owner-selected-sha256> `
  --work-directory artifacts/raw-smoke-unique
```

Use `python3` and the corresponding native row on macOS. This entry always
prepares a new private directory and launches `icu_smoke.py` in a **fresh Python
worker** (`-I -B -X utf8`). The worker inherits only basic OS/temp/locale and
processor-architecture variables, not CI/cloud/package credentials, `PYTHONPATH`
or native loader injection variables. This is process/import isolation, **not
an OS sandbox**: execute only owner-approved native code.

The worker reuses the package probe's existing assertions without reducing the
case set: ICU 77.1, all 62 exact locales, eight line-break cases, six script
properties and mixed bidi. The data digest is computed from the actual buffer
registered with ICU. Prepared bytes and binding/notice/manifest copies are
checked before worker launch and after execution. A failing/timed-out worker,
mutated input or contradictory result produces no successful `probe-report.json`.
There is no placeholder package, signing path or success bypass.

## Output contract and renderer evidence

The new work directory contains:

* `input-binding.json`: producer/source/archive identities, owner-selected input
  manifest hashes, selected raw-file hashes and full-notice hashes. Its
  `runtimeTested` remains **false**, describing preparation only.
* `runtime/`: the exact selected two libraries, `icudt.dat`, and producer
  `filters.json`. No DLL or data file is extracted from an older NuGet package.
* `notices/`: complete ICU and Uno texts, combined license and scope NOTICE.
* `evidence/{source,data,native}.artifact.json`: exact selected producer manifests.
* `prepared-inputs.json`, worker command/exit/log and `worker-result.json`.
* `probe-report.json`, **only after successful actual execution**: separate
  producer and probe commits, input-binding digest, case results and hashes for
  every preceding output file. Only this independent runtime report sets
  `runtimeTested=true`. It does not rewrite any producer build manifest.

For renderer release evidence, retain **all three original producer bundles**
alongside this output, including `icu-source.zip` and tool/build logs. The
copied manifests identify those files but do not retain their contents.
The renderer can pin the probe-report digest and require exact equality between
its selected raw dependency bytes and this inventory, while retaining the full
notices. A new package/distribution archive still needs its own final-byte
inventory and any separately required signature/attestation checks; this entry
does not authorize creating or publishing one.

An unsigned manifest plus hashes establishes content consistency against the
owner's selected inputs. It does **not** independently authenticate the producer,
prove a protected job ran, attest native source-to-binary execution, or establish
byte-reproducible toolchains. Private copies and before/after hashes are not
exclusive POSIX writer protection or a hostile same-user-process lifetime proof.
The report deliberately keeps `authenticatedAttestation=false`,
`signatureVerified=false`, `shippingPackage=false` and
`historicalHashBindingEstablished=false`.

Thin macOS success does not establish universal-binary execution. Windows ARM64,
WASM, iOS/tvOS, GUI/physical AT and full conformance remain separate. Nothing here
retroactively binds historical 77.2.1 package hashes.

## Lightweight validation

```powershell
python -m unittest discover -s tests -p test_raw_icu_smoke.py -v
python -m unittest discover -s tests -v
```

Fixtures generate format headers and small source/notice archives; native calls
and worker completion are mocked only in unit orchestration tests. They test
selection, architecture, tampering and failure boundaries, **not** ICU runtime
acceptance. Keep these results separate from future real hosted probe JSON.
