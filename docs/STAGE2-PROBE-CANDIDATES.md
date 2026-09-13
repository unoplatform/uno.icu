# Same-producer stage-two candidates (not executed)

This local reconciliation retains the Windows prerequisite fix and optional raw
smoke adapter. It does not dispatch a run, alter the existing PR, or interpret
ambiguous dispatch responses as either success or a global GitHub outage.
Parent retains duplicate-run checks and all remote actions.

## Available, already verified producer set

The following source/data/macOS artifacts belong to **run 34746077621, attempt 1,
commit `bacf1dfaed2592cb6ad65612790827e1acb37d96`**:

| Artifact | ID | `artifact.json` SHA-256 |
| --- | --- | --- |
| `build-only-source` | 10314366000 | `8b0a095c59e2a1f6e9ba59021fa24c1acc27297413ac78d598b1835bf80c04fb` |
| `build-only-data` | 10313987538 | `d24f96c62c6374938b371eb542cb4e47fc89297e91bcaa1d8e5d20d64c518678` |
| `build-only-macos-x86_64` | 10314401936 | `ea3f05197e80cc8cd43870733413f05b6a0f43b008e44c5d0b95e4124328dbfe` |
| `build-only-macos-arm64` | 10314147243 | `ca2a9910ab392b6082b3c376df1e1c08f31ff95ad3550053bb3858e8dff29d19` |

These are manifest digests derived only after the artifact ZIP digests matched
the coordinator-retained GitHub metadata. Source, notices, per-file hashes and
the actual recorded run context were verified locally. The data SHA-256 equals
the historical data reference, but this is not historical native-binary proof.
The overall producer run failed its Windows prerequisites and never produced
complete three-package staging.

## Future native invocations

Use a clean, reviewed checkout containing this reconciliation and the producer
Git object. Place the three original extracted producer bundles under
`artifacts/producer-34746077621/`. On a matching **native macOS** process, choose
one row and its digest:

```bash
# Intel:
row=macos-x86_64
native_sha=ea3f05197e80cc8cd43870733413f05b6a0f43b008e44c5d0b95e4124328dbfe
# On ARM64 instead, use:
# row=macos-arm64
# native_sha=ca2a9910ab392b6082b3c376df1e1c08f31ff95ad3550053bb3858e8dff29d19

producer=artifacts/producer-34746077621
python3 tests/raw_icu_smoke.py \
  --row "$row" \
  --producer-commit bacf1dfaed2592cb6ad65612790827e1acb37d96 \
  --producer-run-id 34746077621 --producer-run-attempt 1 \
  --producer-workflow-ref unoplatform/uno.icu/.github/workflows/main.yml@refs/heads/dev/morning4coffe-dev/icu-build-only-main-20260913 \
  --source-bundle "$producer/build-only-source" \
  --source-manifest-sha256 8b0a095c59e2a1f6e9ba59021fa24c1acc27297413ac78d598b1835bf80c04fb \
  --data-bundle "$producer/build-only-data" \
  --data-manifest-sha256 d24f96c62c6374938b371eb542cb4e47fc89297e91bcaa1d8e5d20d64c518678 \
  --native-bundle "$producer/build-only-$row" \
  --native-manifest-sha256 "$native_sha" \
  --work-directory "artifacts/raw-smoke-34746077621-$row"
```

These are prepared commands, **not executed probes**. A locally prepared
`input-binding.json` remains `runtimeTested=false`. Only a successful actual
worker plus all verification checks can create an independent runtime report;
no original build manifest is rewritten. See [RAW-SMOKE.md](RAW-SMOKE.md).

## Windows remains blocked on new evidence

There are no successful Windows native rows in the above producer set. A later
`target=windows` control at a different commit/run does not supply matching
filtered data from that producer. Do not combine its DLLs with this older data
artifact, even when raw data hashes match.

After parent resolves dispatch ambiguity, a bounded Windows control can verify
the prerequisite fix. A fresh complete same-run matrix can then supply
source/data/Windows inputs for raw Windows x64 smoke. Windows ARM64 and WASM
runtime probes remain unsupported, and neither aggregate staging nor historical
provenance is closed by these preparations. No signing, package publication,
protected attestation, physical AT or GA acceptance is claimed.
