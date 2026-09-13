# Build-only Windows prerequisite policy

## Observed failure, not a compile failure

Run `34746077621`, Windows x64 job `103694227546`, selected:

* Image `windows-2025-vs2026`, version `20260907.229.1`.
* Its documented VS installation is **Visual Studio Enterprise 2026
  18.9.12120.119**, not VS2022.
* The old `[17.0,18.0)` query returned `[]` before compilation. It also
  unnecessarily required both x86/x64 and ARM64 components in the x64 row.

The failure log and exact runner documentation are retained by the coordinator.
The image release tag resolves to runner-images commit
`c240f76fa0dd523af7376dbe8480964a3cb0af47`.
[That immutable image description](https://github.com/actions/runner-images/blob/c240f76fa0dd523af7376dbe8480964a3cb0af47/images/windows/Windows2025-VS2026-Readme.md)
confirms VS18. This does not establish any native compile/runtime result.

ICU's pinned `allinone/Build.Windows.PlatformToolset.props` maps VS **17.0**
to **v143**; it does not add a VS18 mapping. Do not remove the version guard
and silently build with a new default compiler while retaining an old identity.

## Selected compatible route

Only `.github/workflows/build-only.yml` changes its Windows runner to
**`windows-2022`**. Current documentation at runner-images commit
`bac22751eb7d886e12c6063685275299469e9e5b` lists:

* [Supported `windows-2022` label](https://github.com/actions/runner-images/blob/bac22751eb7d886e12c6063685275299469e9e5b/README.md).
* [VS Enterprise 2022 17.14.37614.0, both C++ architecture components, and
  Windows SDK 10.0.26100.0](https://github.com/actions/runner-images/blob/bac22751eb7d886e12c6063685275299469e9e5b/images/windows/Windows2022-Readme.md).

The label is not an immutable VM/toolchain digest. Actual run image identifiers,
VS instance/version, compiler hashes and SDK identity files are still recorded;
there is no byte-reproducibility or successful-installation claim from docs.
The normal upstream release workflow's Windows job is unchanged and not validated
by this bounded build-only fix.

## Discovery and failure evidence

`eng/windows_toolchain.py` runs the installed `vswhere` in read-only mode:

1. **First**, `-all -prerelease -products * -format json -utf8 -include packages`,
   without version or component filters.
2. Retain a sanitized `unfiltered-inventory.json`: instance ID/path/version,
   product ID, complete/launchable/preview flags, and component IDs/versions.
   Arbitrary installer properties and process environment values are not logged.
3. Query the latest stable `[17.0,18.0)` instance with the **target-specific**
   required component:
   * x64: `Microsoft.VisualStudio.Component.VC.Tools.x86.x64`
   * ARM64: `Microsoft.VisualStudio.Component.VC.Tools.ARM64`
4. Retain filtered inventory, command/exit records and `vswhere` executable hash.
   Empty, ambiguous, preview, incomplete, wrong-version or missing-component
   results fail; no `selected.json` is written on failure.

The actual files under the selected installation are then read and their hashes recorded:
MSBuild x64 and selected MSVC `Hostx64/<target>/{cl,link,lib}.exe`.
`/p:DefaultPlatformToolset=v143` makes the intended ICU-compatible platform
toolset explicit. The actual `VCToolsVersion` is read and passed to MSBuild.
The Release `common;stubdata`, x64/ARM64, `SkipUWP`, SDK 10.0.26100.0,
preferred x64 host and existing static-CRT patch remain intact.

`tools/windows-sdk.json` records the installed KitsRoot10 registry location and
hashes of selected SDK version headers plus each target's `kernel32.lib` and
static `libucrt.lib`. These are **identity files, not a whole-SDK inventory**.
The existing native binlog remains the record of resolved compile/link inputs.
Nothing modifies Visual Studio, the registry, SDK contents or restored packages.

## Local contracts versus hosted control

```powershell
python -m unittest discover -s tests -p test_windows_prereqs.py -v
# Windows, metadata only; a missing stable C++ instance should fail with inventory:
python eng/windows_toolchain.py --arch x64 --output artifacts/vswhere-check-unique
```

The matching-version fixtures use the documented image version and component
IDs. They do not claim a local VS install. The real CLI can also exercise the
expected-negative path on a workstation with only preview C++ instances.

Parent publishes only after review, then runs the existing registered dispatcher
with `operation=build-only`, native=true/release=false, the new exact source SHA,
and **`target=windows`**. Both Windows architecture rows remain in that bounded
control. Only after that control should a **fresh `target=all` run** establish
complete same-run matrix evidence. Do not mix artifacts from the earlier failed
run, cancel its other native rows, or count contracts as native compilation.
Signing, release/tag creation and NuGet publication remain unauthorized.
