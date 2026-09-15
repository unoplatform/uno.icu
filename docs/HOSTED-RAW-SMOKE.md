# Read-only hosted raw smoke, without rebuilding the producer

`main.yml` exposes the separate manual **`operation=raw-smoke`** route. It calls
the local reusable `raw-smoke.yml` from the exact reviewed probe commit. This
does not rebuild source/data/native rows, invoke release jobs, create packages,
sign, attest, tag, publish, or modify repository settings.

The owner-selected example is committed as **`eng/raw-smoke-plan.json`**. It
references completed producer **34811569292 / attempt 1 / commit
`8fd7b5345570bb4b7230e13aa596def2df8fc7ad`**. The probe commit is a separate,
newer identity and must not be substituted for the producer in the plan.

## Explicit dispatch boundary

The caller requires all of the following:

* `workflow_dispatch` in `unoplatform/uno.icu`, on a branch;
* `operation=raw-smoke`, `target=all`, typed native=true and release=false;
* full reviewed `expected_sha` matching `github.sha`, `github.workflow_sha`
  and a clean checkout;
* the known `main.yml` caller and matching actual dispatch operation.

`raw-smoke.yml` has no standalone/automatic trigger and no inherited secrets.
Both caller and child grant only **`contents: read` and `actions: read`**.
The fixed runtime matrix is Windows x64 on `windows-2022`, macOS x86_64 on
`macos-15-intel`, and macOS ARM64 on `macos-15`. The entry also checks the
actual process architecture before downloading or loading libraries.
`target=all` is deliberate: other target values fail closed rather than silently
running a larger or empty set.

Default events and default manual inputs run contracts only. The build-only
caller requires exactly `build-only`; release authorization/sign/publish require
exactly their release operations and native=false. Neither native **build**
jobs nor any legacy release job is reachable from `raw-smoke`.

After review and guarded feature publication, the parent may execute:

```powershell
gh workflow run main.yml --repo unoplatform/uno.icu `
  --ref <reviewed-feature-branch> `
  -f operation=raw-smoke -f authorize_native=true -f authorize_release=false `
  -f expected_sha=<new-reviewed-probe-commit> -f target=all
```

This is a prepared command, not execution authorization or a dispatch performed
by the implementation agent. The registered `main.yml` route needs no new
default-branch merge. Avoid duplicate runs after ambiguous API responses.

## Strict input plan and actual API checks

The plan has no URLs to fetch, private paths or logs. It binds:

* repository/ID, producer commit/run/attempt/branch/workflow/event;
* all expected successful and skipped producer job names, IDs and conclusions;
* exactly source, data, Windows x64 and both macOS thin artifact IDs/names,
  raw ZIP byte lengths/SHA-256 values, and `artifact.json` SHA-256 values.

Unknown fields, wrong types, duplicate IDs/JSON fields, missing matrix rows,
unexpected architecture names, invalid hashes and noncanonical refs are rejected.
All selected values must come from a reviewed, completed producer set. Updating
the plan is a source change requiring review, not an automatic "latest" lookup.

Each probe job obtains the **specific attempt** through GitHub's read-only API,
checks completed/successful source/event/repository identity and the complete
job-result graph, then checks all five exact artifact records. Artifact names
are additional constraints, never ambiguous download selectors.
Expired, failed, incomplete, substituted or mismatched input records fail before
any native library is loaded.

Only the **Download approved producer artifacts** step receives `GH_TOKEN`.
Checkout uses the pinned action's internal read token with credentials not
persisted; the token is not a workflow/job environment variable. The native
execution step rejects a GitHub-token environment, and the raw worker retains
its existing minimal credential-free environment.

The standard-library client constructs only fixed repository API GET URLs.
Automatic redirects are disabled. The API authorization header is sent only
to `api.github.com`; artifact-storage requests are constructed afresh **without
authorization**, including subsequent redirects. Only HTTPS artifact-storage
hosts on the explicit allowlist are accepted. Signed redirect URLs/tokens are
not written to evidence. There is no permissive URL or authentication fallback.

The exact archive length and SHA-256 are checked before extraction. ZIP
validation uses original names (before Python's Windows normalization), rejects
traversal, links/devices, noncanonical names and collisions, and writes only new
ordinary files. All extracted row files, source Git blobs/archive, architecture,
tool identities and complete notices are then verified by the raw adapter.

## Runtime and retention

The probe checkout includes producer Git history (`fetch-depth: 0`). The
download stage retains original archives and extracted bundles, API evidence,
the exact plan and an unsigned download receipt under
`artifacts/raw-smoke/<row>/`.

The subsequent credential-free step revalidates the plan, API records, archive
bytes and extracted bundles before invoking `tests/raw_icu_smoke.py` in a fresh
process. The existing harness executes the 62 exact locales, eight line-break
cases, six script properties and mixed bidi checks. It binds producer/probe
commits separately and rechecks its prepared bytes and notices.
Original producer bundles are checked again after the probe; none are rewritten.

Each job uploads the complete directory as `raw-smoke-<row>-attempt-<attempt>`,
including original producer ZIPs/bundles and the fresh probe report. The
attempt suffix avoids artifact-name reuse on reruns. Failure logs/partial
downloads can be retained, but failed probes or retained-input checks do not create a successful
`hosted-smoke-receipt.json`. Retention is **90 days**, not indefinite; owners
must archive the complete evidence before expiry under their archival policy.

Only actually successful runtime reports set `runtimeTested=true`. Original
build manifests remain false. `authenticatedAttestation`, signature verification
and shipping-package claims remain false. GitHub API/context and checksum
comparisons do not fabricate a protected attestation or historical source-to-
binary proof. Process isolation is not an OS sandbox or hostile-writer lifetime
guarantee; load only the owner-approved native code.

Windows ARM64, WASM, universal macOS, iOS/tvOS, physical AT and full ICU
conformance remain outside this three-row runtime scope.

## Focused validation

The local suite evaluates actual YAML guards over 720 operation/boolean/ref/
repository/event combinations, malformed authorization values and reachable-job
permissions. Additional tests cover the strict plan, wrong producer/jobs/
artifact identities, redirect credential isolation, exact lengths/hashes, unsafe
ZIP names, plan substitution and failed/changed-input finalization.
These are light offline contracts; they do not claim hosted probe execution.
