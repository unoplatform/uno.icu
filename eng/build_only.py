"""Hosted, explicitly authorized native builds. No signing, packing or publishing.

Manifests are self-reported inventories, NOT authenticated attestations.
Only successful, complete rows receive artifact.json; failed command logs remain.
"""
import argparse
import json
import os
from pathlib import Path
import platform
import re
import shutil
import struct
import subprocess
import sys

import provenance as p
import windows_toolchain as windows
import apple_icu as apple

ROOT = p.ROOT
OUT = ROOT / "artifacts/build-only"
VARIANTS = {"data": ("",), "wasm": ("st", "mt", "st,simd", "mt,simd"),
            "windows": ("x64", "arm64"), "macos": ("x86_64", "arm64")}
BUILD_KEYS = ("GITHUB_REPOSITORY", "GITHUB_SHA", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT",
              "GITHUB_WORKFLOW_REF", "GITHUB_JOB", "RUNNER_OS", "RUNNER_ARCH",
              "ImageOS", "ImageVersion", "WORKFLOW_SHA", "DISPATCH_OPERATION",
              "BUILD_SCOPE", "FULL_FIVE", "AUTHORIZE_APPLE")


def authorize(env, commit, dirty):
    if (env.get("GITHUB_ACTIONS") != "true" or
            env.get("GITHUB_EVENT_NAME") != "workflow_dispatch" or
            env.get("GITHUB_REPOSITORY") != "unoplatform/uno.icu"):
        raise ValueError("Explicit owner-repository workflow dispatch required")
    expected = env.get("EXPECTED_SHA", "")
    if (not re.fullmatch("[0-9a-f]{40}", expected) or dirty or
            expected != commit or expected != env.get("GITHUB_SHA") or
            expected != env.get("WORKFLOW_SHA")):
        raise ValueError("Reviewed source/workflow SHA must match a clean checkout")
    scope = env.get("BUILD_SCOPE")
    ref = env.get("GITHUB_REF", "")
    workflow = env.get("GITHUB_WORKFLOW_REF")
    main = f"unoplatform/uno.icu/.github/workflows/main.yml@{ref}"
    direct = f"unoplatform/uno.icu/.github/workflows/build-only.yml@{ref}"
    native, release = env.get("AUTHORIZE_NATIVE"), env.get("AUTHORIZE_RELEASE")
    operation = env.get("DISPATCH_OPERATION", "")
    # Older three-package callers have no Apple grant. Absence cannot authorize
    # expansion; the new full scope requires both explicit typed values.
    apple_grant, full = env.get("AUTHORIZE_APPLE", "false"), env.get("FULL_FIVE", "false")
    if scope != "build-full-five" and (apple_grant != "false" or full != "false"):
        raise ValueError("Apple authorization cannot change an existing operation's resource scope")
    if scope == "build-only":
        # Values cross the YAML/environment boundary via toJSON, preserving
        # boolean type. A string "false", integer 0, missing value or mixed
        # release/native grant is not an authorization.
        if native != "true" or release != "false":
            raise ValueError("Build-only requires native=true and release=false booleans")
        if not ref.startswith("refs/heads/") or env.get("BUILD_TARGET") not in ("all", "linux", "windows", "macos"):
            raise ValueError("Select an approved build-only branch and resource group")
        # A local reusable workflow resolves from the caller's exact commit;
        # GitHub retains the dispatch caller's context, not a new call event.
        if not ((workflow == main and operation == "build-only") or
                (workflow == direct and operation == "")):
            raise ValueError("Unexpected build-only caller/operation identity")
    elif scope == "build-full-five":
        if native != "true" or release != "false" or apple_grant != "true" or full != "true":
            raise ValueError("Full-five requires native=true, release=false and explicit Apple/full-five booleans")
        if not ref.startswith("refs/heads/") or env.get("BUILD_TARGET") != "all":
            raise ValueError("Full-five requires the complete all-target resource group")
        if not ((workflow == main and operation == "build-full-five") or
                (workflow == direct and operation == "")):
            raise ValueError("Unexpected full-five caller/operation identity")
    elif scope == "raw-smoke":
        if native != "true" or release != "false":
            raise ValueError("Raw smoke requires native=true and release=false booleans")
        if not ref.startswith("refs/heads/") or env.get("BUILD_TARGET") != "all":
            raise ValueError("Raw smoke requires the explicit all-host scope on a reviewed branch")
        if workflow != main or operation != "raw-smoke":
            raise ValueError("Unexpected raw-smoke caller/operation identity")
    elif ((scope == "release-dev" and ref == "refs/heads/main") or
          (scope == "release-prod" and ref.startswith("refs/heads/release/"))):
        if native != "false" or release != "true":
            raise ValueError("Release requires release=true and native=false booleans")
        if workflow != main or operation != scope:
            raise ValueError("Unexpected release caller/operation identity")
    else:
        raise ValueError("Release scope/ref mismatch; no implicit publication")


def check_authorization():
    authorize(os.environ, p.git("rev-parse", "HEAD"), p.git("status", "--porcelain"))


def copy_new(source, destination):
    if not source.is_file() or source.stat().st_size == 0:
        raise ValueError(f"Missing/empty output: {source}")
    p.write_new(destination, source.read_bytes())


def file_hashes(directory):
    if directory.is_symlink():
        raise ValueError(f"Symlink not permitted in artifact: {directory}")
    result = {}
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"Symlink not permitted in artifact: {path}")
        if path.is_file() and path.relative_to(directory).as_posix() != "artifact.json":
            result[path.relative_to(directory).as_posix()] = p.sha(path.read_bytes())
    return result


def repository_bytes(name):
    return subprocess.check_output(["git", "-C", str(ROOT), "show", f"HEAD:{name}"])


def source_inputs():
    # Hash Git's normalized blobs, not checkout EOL conversions on Windows.
    return {name: p.sha(repository_bytes(name)) for name in p.git("ls-files").splitlines()}


def expected_payloads(kind, variant="", emscripten=""):
    if kind in VARIANTS and variant not in VARIANTS[kind]:
        raise ValueError("Unexpected matrix variant")
    if (kind == "wasm" and emscripten not in p.EMSCRIPTEN_VERSIONS) or (kind != "wasm" and emscripten):
        raise ValueError("Unexpected Emscripten toolchain")
    if kind == "data":
        return {"nuget/icudt.dat"}
    if kind == "wasm":
        return {f"nuget/uno.icu-wasm/buildTransitive/native/unoicu.a/{emscripten}/{variant}/unoicu.a"}
    if kind == "windows":
        return {f"nuget/uno.icu-win/libicu/{variant}/{name}77.dll" for name in ("icuuc", "icudt")}
    if kind == "macos":
        return {f"libicu-osx-{variant}/{name}.dylib" for name in ("libicuuc", "libicudata")}
    if kind == "macos-universal":
        return {f"nuget/uno.icu-macos/libicu/{name}.dylib" for name in ("libicuuc", "libicudata")}
    if kind == "source":
        return set()
    if kind == "apple":
        return set(apple.payload_specs())
    if kind == "staging-five":
        return expected_payloads("staging") | expected_payloads("apple")
    if kind == "staging":
        return set().union(expected_payloads("data"), expected_payloads("macos-universal"),
                           *(expected_payloads("windows", v) for v in VARIANTS["windows"]),
                           *(expected_payloads("wasm", v, version)
                             for version in p.EMSCRIPTEN_VERSIONS for v in VARIANTS["wasm"]))
    raise ValueError("Unknown build row")


def validate_payloads(bundle, kind, variant, emscripten=""):
    actual = file_hashes(bundle / "payload")
    if set(actual) != expected_payloads(kind, variant, emscripten):
        raise ValueError(f"Incomplete/unexpected payload set for {kind}/{variant}")
    for name in actual:
        data = (bundle / "payload" / name).read_bytes()
        if name in apple.payload_specs():
            platform_id, architectures = apple.payload_specs()[name]
            apple.validate_archive(data, platform_id, architectures)
            continue
        if name.endswith(".a"):
            valid = data.startswith(b"!<arch>\n") and len(data) > 8
        elif name.endswith(".dat"):
            valid = len(data) > 20 and data[2:4] == b"\xda\x27" and data[12:16] == b"CmnD"
        elif name.endswith(".dll"):
            machine = 0xAA64 if "/arm64/" in name else 0x8664
            offset = struct.unpack_from("<I", data, 0x3c)[0] if len(data) >= 64 else len(data)
            valid = (data.startswith(b"MZ") and len(data) >= offset + 6 and
                     data[offset:offset + 4] == b"PE\0\0" and
                     struct.unpack_from("<H", data, offset + 4)[0] == machine)
        else:
            # lipo -verify_arch separately verifies each thin/universal output.
            valid = len(data) > 32 and data[:4] in (b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe",
                                                   b"\xca\xfe\xba\xbf")
        if not valid:
            raise ValueError(f"Invalid native/data file header: {name}")


def row_key(kind, variant="", emscripten=""):
    return "-".join(part for part in (kind, emscripten, variant) if part)


def matrix_rows(full_five=False):
    rows = [("source", "", ""), ("data", "", ""), ("macos-universal", "", "")]
    rows += [("wasm", v, version) for version in p.EMSCRIPTEN_VERSIONS for v in VARIANTS["wasm"]]
    rows += [(kind, v, "") for kind in ("windows", "macos") for v in VARIANTS[kind]]
    if full_five:
        rows.append(("apple", "", ""))
    return rows


class Build:
    def __init__(self, kind, variant="", emscripten=""):
        expected_payloads(kind, variant, emscripten)
        self.kind, self.variant = kind, variant
        self.emscripten = emscripten
        self.directory = OUT / row_key(kind, variant, emscripten)
        self.directory.mkdir(parents=True, exist_ok=False)
        self.sequence = 0

    def run(self, command, cwd=ROOT, env=None):
        """Preserve output and failure code without converting failures into manifests."""
        self.sequence += 1
        prefix = self.directory / "logs" / f"{self.sequence:02}"
        prefix.parent.mkdir(exist_ok=True)
        p.write_new(prefix.with_suffix(".command.json"), p.json_bytes({
            "command": [str(arg) for arg in command], "cwd": str(cwd)}))
        with prefix.with_suffix(".log").open("xb") as log:
            result = subprocess.run([str(arg) for arg in command], cwd=cwd, env=env,
                                    stdout=log, stderr=subprocess.STDOUT, text=True)
        p.write_new(prefix.with_suffix(".json"), p.json_bytes({
            "command": [str(arg) for arg in command], "cwd": str(cwd), "exitCode": result.returncode}))
        # Native Windows tools need not emit UTF-8. Keep the exact log bytes;
        # escape undecodable/display-unrepresentable bytes only for the console.
        output = prefix.with_suffix(".log").read_text(encoding="utf-8", errors="backslashreplace")
        encoding = sys.stdout.encoding or "utf-8"
        print(output.encode(encoding, errors="backslashreplace").decode(encoding), flush=True)
        result.check_returncode()
        return output.strip()

    def source(self):
        archive = ROOT / "artifacts/icu-source.zip"
        p.fetch_source(archive)
        license_bytes = p.verify_source(archive, p.load_lock())
        p.write_new(self.directory / "licenses/ICU-LICENSE.txt", license_bytes)
        uno = repository_bytes("LICENSE.md")
        p.write_new(self.directory / "licenses/Uno-LICENSE.md", uno)
        p.write_new(self.directory / "NOTICE.md", repository_bytes("eng/NOTICE.md"))
        p.write_new(self.directory / "LICENSE.txt", license_bytes + b"\n\n" + uno)
        return archive

    def finish(self, inputs=None):
        check_authorization()
        validate_payloads(self.directory, self.kind, self.variant, self.emscripten)
        manifest = {
            "schemaVersion": 1, "kind": self.kind, "variant": self.variant,
            "emscriptenVersion": self.emscripten,
            "unoCommit": p.git("rev-parse", "HEAD"), "upstream": p.load_lock(),
            "gitBlobSha256": source_inputs(),
            "inputSha256": {name: p.sha((ROOT / name).read_bytes()) for name in p.git("ls-files").splitlines()},
            "build": {key: os.environ.get(key) for key in BUILD_KEYS},
            "host": {"platform": platform.platform(), "python": sys.version},
            "images": json.loads((ROOT / "eng/build-images.json").read_text()),
            "files": file_hashes(self.directory), "inputArtifacts": inputs or {},
            "authenticatedAttestation": False, "byteReproducible": False,
            "runtimeTested": False, "shippingPackage": False,
            "unverifiedRows": ["ios", "iossim", "tvos", "tvossim", "WASM runtime", "Windows ARM64 runtime"],
        }
        p.write_new(self.directory / "artifact.json", p.json_bytes(manifest))

    def payload(self, source, relative):
        copy_new(source, self.directory / "payload" / relative)


def verify_bundle(directory, kind, variant="", emscripten="", env=None):
    env = os.environ if env is None else env
    manifest = json.loads((directory / "artifact.json").read_text())
    if (manifest["schemaVersion"] != 1 or manifest["kind"] != kind or manifest["variant"] != variant or
            manifest["emscriptenVersion"] != emscripten or
            manifest["unoCommit"] != env["GITHUB_SHA"] or manifest["upstream"] != p.load_lock()):
        raise ValueError("Artifact source/row identity mismatch")
    for key in ("GITHUB_REPOSITORY", "GITHUB_SHA", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT",
                "GITHUB_WORKFLOW_REF", "WORKFLOW_SHA"):
        if not env.get(key) or manifest["build"].get(key) != env[key]:
            raise ValueError("Artifact belongs to a different workflow run")
    if manifest["files"] != file_hashes(directory):
        raise ValueError("Artifact inventory mismatch")
    if manifest["gitBlobSha256"] != source_inputs():
        raise ValueError("Artifact build-input identity mismatch")
    validate_payloads(directory, kind, variant, emscripten)
    if kind in ("apple", "staging-five"):
        if any(manifest["build"].get(key) != value for key, value in
               {"BUILD_SCOPE": "build-full-five", "FULL_FIVE": "true", "AUTHORIZE_APPLE": "true"}.items()):
            raise ValueError("Full-five artifact lacks its explicit producer scope")
    if p.sha((directory / "licenses/ICU-LICENSE.txt").read_bytes()) != p.load_lock()["licenseSha256"]:
        raise ValueError("Incomplete upstream license")
    if (directory / "licenses/Uno-LICENSE.md").read_bytes() != repository_bytes("LICENSE.md"):
        raise ValueError("Uno license mismatch")
    if (directory / "NOTICE.md").read_bytes() != repository_bytes("eng/NOTICE.md"):
        raise ValueError("Notice mismatch")
    if (directory / "LICENSE.txt").read_bytes() != (
            (directory / "licenses/ICU-LICENSE.txt").read_bytes() + b"\n\n" + repository_bytes("LICENSE.md")):
        raise ValueError("Combined license mismatch")
    if kind == "source":
        p.verify_source(directory / "icu-source.zip", p.load_lock())
    return p.sha((directory / "artifact.json").read_bytes())


def docker_build(build, archive):
    kind, variant = build.kind, build.variant
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        raise ValueError("Docker builds require a Linux x64 hosted runner")
    context = ROOT / "src" / ("cldr_data" if kind == "data" else "unoicu")
    copy_new(archive, context / "icu.zip")
    images = json.loads((ROOT / "eng/build-images.json").read_text())
    image = (images["wasm"][build.emscripten] if kind == "wasm" else images[kind])["image"]
    if not re.fullmatch(r"docker.io/[\w/.-]+@sha256:[0-9a-f]{64}", image):
        raise ValueError("Immutable Docker image required")
    build.run(["docker", "version"])
    build.run(["docker", "buildx", "version"])
    build.run(["docker", "buildx", "imagetools", "inspect", image, "--raw"])
    output = ROOT / "artifacts/docker-output"
    args = ["docker", "buildx", "build", "--platform", images["platform"], "--progress", "plain",
            "--build-arg", f"BUILD_IMAGE={image}", "--output", f"type=local,dest={output}"]
    if kind == "wasm":
        args += ["--build-arg", f"EMSCRIPTEN_VERSION={build.emscripten}",
                 "--build-arg", f"IS_MULTITHREADED={str(variant.startswith('mt')).lower()}",
                 "--build-arg", f"IS_SIMD_SUPPORTED={str(',simd' in variant).lower()}"]
    build.run(args + [context])
    name = "icudt.dat" if kind == "data" else "unoicu.a"
    build.payload(output / name, next(iter(expected_payloads(kind, variant, build.emscripten))))
    for name in ("packages.txt", "compiler.txt", "make.txt", "config.log"):
        copy_new(output / "build-evidence" / name, build.directory / "tools" / name)


def windows_build(build, archive):
    if platform.system() != "Windows":
        raise ValueError("Windows hosted runner required")
    discovery = build.directory / "tools/vswhere"
    build.run([sys.executable, ROOT / "eng/windows_toolchain.py",
               "--arch", build.variant, "--output", discovery])
    selection = json.loads((discovery / "selected.json").read_text(encoding="utf-8"))
    vs = Path(selection["installation"]["installationPath"])
    msbuild = vs / "MSBuild/Current/Bin/amd64/MSBuild.exe"
    build.run([msbuild, "-version", "-nologo"])
    tools_version = (vs / "VC/Auxiliary/Build/Microsoft.VCToolsVersion.default.txt").read_text().strip()
    tools = vs / "VC/Tools/MSVC" / tools_version / "bin/Hostx64" / build.variant
    p.write_new(build.directory / "tools/msvc.json", p.json_bytes({
        "version": tools_version, "windowsSdk": windows.SDK_VERSION,
        "visualStudio": selection["installation"], "platformToolset": selection["platformToolset"],
        "hostArchitecture": "x64", "msbuildSha256": p.sha(msbuild.read_bytes()),
        "sha256": {name: p.sha((tools / name).read_bytes()) for name in ("cl.exe", "link.exe", "lib.exe")}}))
    p.write_new(build.directory / "tools/windows-sdk.json", p.json_bytes(
        windows.sdk_identity(windows.installed_sdk_root(), build.variant)))
    build.run(["pwsh", "-NoProfile", "-Command",
               "Expand-Archive -LiteralPath artifacts/icu-source.zip -DestinationPath artifacts/icu"])
    source = ROOT / "artifacts/icu" / f'icu-{p.load_lock()["commit"]}/icu4c/source'
    patches = {}
    for project in source.rglob("*.vcxproj"):
        before = project.read_bytes()
        after = before.replace(b"MultiThreadedDLL", b"MultiThreaded")
        project.write_bytes(after)
        patches[project.relative_to(source).as_posix()] = {"before": p.sha(before), "after": p.sha(after)}
    p.write_new(build.directory / "tools/static-crt-patch.json", p.json_bytes(patches))
    env = os.environ.copy()
    env["ICU_DATA_FILTER_FILE"] = str(ROOT / "src/cldr_data/filters.json")
    build.run([msbuild, "allinone/allinone.sln", "/p:Configuration=Release",
               f"/p:Platform={'ARM64' if build.variant == 'arm64' else 'x64'}",
               "/p:SkipUWP=true", "/p:WindowsTargetPlatformVersion=10.0.26100.0",
               "/p:DefaultPlatformToolset=v143",
               f"/p:VCToolsVersion={tools_version}", "/p:PreferredToolArchitecture=x64",
               "/t:common;stubdata", "/m",
               f"/bl:{build.directory / 'logs/native.binlog'}"], cwd=source, env=env)
    binaries = source.parent / ("binARM64" if build.variant == "arm64" else "bin64")
    for name in expected_payloads("windows", build.variant):
        build.payload(binaries / Path(name).name, name)


def macos_build(build, archive):
    if platform.system() != "Darwin" or platform.machine() != build.variant:
        raise ValueError("Native matching macOS architecture required (no emulated row)")
    build.run(["xcodebuild", "-version"])
    build.run(["xcrun", "--show-sdk-version"])
    compiler = Path(build.run(["xcrun", "--find", "clang"]))
    build.run([compiler, "--version"])
    p.write_new(build.directory / "tools/clang.json", p.json_bytes({
        "path": str(compiler), "sha256": p.sha(compiler.read_bytes())}))
    build.run(["unzip", "-q", archive, "-d", ROOT / "artifacts/icu"])
    source = ROOT / "artifacts/icu" / f'icu-{p.load_lock()["commit"]}/icu4c/source'
    env = os.environ.copy()
    env["ICU_DATA_FILTER_FILE"] = str(ROOT / "src/cldr_data/filters.json")
    build.run(["./runConfigureICU", "macOS", "--with-data-packaging=archive"], cwd=source, env=env)
    build.run(["make", "-j", build.run(["sysctl", "-n", "hw.physicalcpu"])], cwd=source, env=env)
    for name in ("config.log", "config.status"):
        copy_new(source / name, build.directory / "tools" / name)
    for name in expected_payloads("macos", build.variant):
        file = Path(name).name
        binary = source / ("stubdata" if file == "libicudata.dylib" else "lib") / file
        build.run(["lipo", binary, "-verify_arch", build.variant])
        build.run(["otool", "-L", binary])
        build.payload(binary, name)
    return source


def build_apple(host_source, archive, host_bundle):
    check_authorization()
    if (os.environ["BUILD_SCOPE"] != "build-full-five" or platform.system() != "Darwin" or
            platform.machine() != "arm64"):
        raise ValueError("Apple cross-builds require the approved full-five ARM64 macOS job")
    expected_host = ROOT / "artifacts/icu" / f'icu-{p.load_lock()["commit"]}/icu4c/source'
    if host_source.resolve() != expected_host.resolve():
        raise ValueError("Use this job's newly built ICU ARM64 host tree")
    host_manifest = verify_bundle(host_bundle, "macos", "arm64")
    host = apple.host_identity(host_source)
    build = Build("apple")
    build.source()
    p.write_new(build.directory / "host-identity.json", p.json_bytes({
        "hostArtifactSha256": host_manifest, "hostSource": str(host_source), "hostIdentity": host,
        "runtimeTested": False, "authenticatedAttestation": False,
    }))
    build.run(["xcodebuild", "-version"])
    workers = build.run(["sysctl", "-n", "hw.physicalcpu"])
    sdk_records, variant_records = {}, []
    for variant in apple.VARIANTS:
        if variant.sdk not in sdk_records:
            sdk_path = Path(build.run(["xcrun", "--sdk", variant.sdk, "--show-sdk-path"]))
            sdk_version = build.run(["xcrun", "--sdk", variant.sdk, "--show-sdk-version"])
            sdk_build = build.run(["xcrun", "--sdk", variant.sdk, "--show-sdk-build-version"])
            settings = sdk_path / "SDKSettings.plist"
            if not sdk_path.is_dir() or not settings.is_file():
                raise ValueError("Required Apple SDK/settings are missing")
            tools = {}
            for tool in ("clang", "clang++", "ar", "lipo"):
                path = Path(build.run(["xcrun", "--sdk", variant.sdk, "--find", tool]))
                tools[tool] = {"path": str(path), "sha256": p.sha(path.read_bytes())}
            build.run([tools["clang"]["path"], "--version"])
            sdk_records[variant.sdk] = {
                "path": str(sdk_path), "version": sdk_version, "buildVersion": sdk_build,
                "settingsSha256": p.sha(settings.read_bytes()), "tools": tools,
                "qualification": "Selected SDK/compiler identity, not a complete SDK-content hash",
            }
            p.write_new(build.directory / "sdk" / (variant.sdk + ".json"), p.json_bytes(sdk_records[variant.sdk]))
        sdk = sdk_records[variant.sdk]
        extraction = ROOT / "artifacts/apple-sources" / variant.name
        if extraction.exists():
            raise FileExistsError("Apple variants require fresh isolated source/intermediate trees")
        build.run(["unzip", "-q", archive, "-d", extraction])
        source = extraction / f'icu-{p.load_lock()["commit"]}/icu4c/source'
        compiler_env = {"CC": sdk["tools"]["clang"]["path"], "CXX": sdk["tools"]["clang++"]["path"]}
        env = {**os.environ, **compiler_env, "ICU_DATA_FILTER_FILE": str(ROOT / "src/cldr_data/filters.json")}
        command = apple.configure_command(variant, Path(sdk["path"]), host_source)
        directory = build.directory / "variants" / variant.name
        p.write_new(directory / "recipe.json", p.json_bytes({
            "name": variant.name, "recipe": command, "compilerEnvironment": compiler_env,
            "sdk": variant.sdk, "hostArtifactSha256": host_manifest,
            "sourceArchiveSha256": p.load_lock()["archiveSha256"],
            "filterSha256": p.sha((ROOT / "src/cldr_data/filters.json").read_bytes()),
        }))
        try:
            build.run(command, cwd=source, env=env)
            build.run(["make", "-j", workers], cwd=source, env=env)
        finally:
            for name in ("config.log", "config.status"):
                if (source / name).is_file():
                    copy_new(source / name, directory / name)
        archive_records = {}
        for library in ("libicuuc.a", "libicudata.a"):
            binary = source / "lib" / library
            identity = apple.validate_archive(binary.read_bytes(), apple.PLATFORMS[variant.target], (variant.arch,))
            copy_new(binary, directory / library)
            archive_records[library] = identity
            if not variant.target.endswith("sim"):
                build.payload(binary, f"nuget/uno.icu-{variant.target}/{variant.target}/{library}")
        variant_records.append({
            "name": variant.name, "target": variant.target, "architecture": variant.arch,
            "platform": apple.PLATFORMS[variant.target], "sdk": variant.sdk, "recipe": command,
            "compilerEnvironment": compiler_env,
            "sourceArchiveSha256": p.load_lock()["archiveSha256"],
            "filterSha256": p.sha((ROOT / "src/cldr_data/filters.json").read_bytes()),
            "archives": archive_records,
        })
        if apple.host_identity(host_source) != host:
            raise ValueError("Cross-build altered the native host tool inputs")
        # Only this successfully archived variant's fresh tree is removed.
        # Failures leave their working tree and retained command/config logs.
        if extraction.resolve().parent != (ROOT / "artifacts/apple-sources").resolve() or extraction.name != variant.name:
            raise ValueError("Unexpected Apple working-tree cleanup scope")
        shutil.rmtree(extraction)
    merged = {}
    for target, sdk_name in (("iossim", "iphonesimulator"), ("tvossim", "appletvsimulator")):
        family = "ios" if target == "iossim" else "tvos"
        for library in ("libicuuc.a", "libicudata.a"):
            relative = f"nuget/uno.icu-{family}/{target}/{library}"
            destination = build.directory / "payload" / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            inputs = [build.directory / "variants" / f"{target}-{arch}" / library for arch in ("arm64", "x86_64")]
            tool = sdk_records[sdk_name]["tools"]["lipo"]["path"]
            build.run([tool, "-create", *inputs, "-output", destination])
            build.run([tool, destination, "-verify_arch", "arm64", "x86_64"])
            merged[relative] = apple.validate_archive(destination.read_bytes(), apple.PLATFORMS[target],
                                                       ("arm64", "x86_64"))
    p.write_new(build.directory / "apple-build.json", p.json_bytes({
        "schemaVersion": 1, "hostArtifactSha256": host_manifest, "hostIdentity": host, "hostSource": str(host_source),
        "sdkIdentities": sdk_records, "variants": variant_records, "simulatorMerges": merged,
        "minimumDeployment": "13.4", "runtimeTested": False, "authenticatedAttestation": False,
    }))
    apple.verify_receipt(build.directory, host_manifest, p.load_lock(),
                         p.sha((ROOT / "src/cldr_data/filters.json").read_bytes()))
    build.finish({"build-only-macos-arm64": host_manifest})


def merge_macos(incoming):
    build = Build("macos-universal")
    build.source()
    parents = {}
    for arch in VARIANTS["macos"]:
        directory = incoming / f"build-only-macos-{arch}"
        parents[directory.name] = verify_bundle(directory, "macos", arch)
    for name in expected_payloads("macos-universal"):
        destination = build.directory / "payload" / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        sources = [incoming / f"build-only-macos-{arch}/payload/libicu-osx-{arch}" / Path(name).name
                   for arch in VARIANTS["macos"]]
        build.run(["lipo", "-create", *sources, "-output", destination])
        build.run(["lipo", destination, "-verify_arch", "x86_64", "arm64"])
    build.finish(parents)


def assemble(incoming, full_five=False):
    build = Build("staging-five" if full_five else "staging")
    build.source()
    rows = matrix_rows(full_five)
    expected = {"build-only-" + row_key(*row) for row in rows}
    if {d.name for d in incoming.iterdir()} != expected:
        raise ValueError("Complete, exact same-run artifact matrix required")
    parents = {}
    for kind, variant, emscripten in rows:
        name = "build-only-" + row_key(kind, variant, emscripten)
        directory = incoming / name
        parents[name] = verify_bundle(directory, kind, variant, emscripten)
        # Keep all per-row logs/manifests/notices, not only their hashes.
        shutil.copytree(directory, build.directory / "rows" / name)
        if kind not in ("source", "macos"):
            for payload in expected_payloads(kind, variant, emscripten):
                build.payload(directory / "payload" / payload, payload)
    if full_five:
        apple_manifest = json.loads((incoming / "build-only-apple/artifact.json").read_text(encoding="utf-8"))
        expected_host = {"build-only-macos-arm64": parents["build-only-macos-arm64"]}
        if apple_manifest["inputArtifacts"] != expected_host:
            raise ValueError("Apple archives do not bind this producer's ARM64 host")
        apple.verify_receipt(incoming / "build-only-apple", expected_host["build-only-macos-arm64"],
                             p.load_lock(), apple_manifest["inputSha256"]["src/cldr_data/filters.json"])
        universal = json.loads((incoming / "build-only-macos-universal/artifact.json").read_text(encoding="utf-8"))
        if universal["inputArtifacts"] != {name: parents[name] for name in
                                          ("build-only-macos-arm64", "build-only-macos-x86_64")}:
            raise ValueError("Full staging universal libraries do not bind their exact native input rows")
    source = incoming / "build-only-source/icu-source.zip"
    p.verify_source(source, p.load_lock())
    copy_new(source, build.directory / "icu-source.zip")
    build.finish(parents)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("authorize", "source", "build", "merge-macos", "assemble"))
    parser.add_argument("--kind", choices=tuple(VARIANTS))
    parser.add_argument("--variant", default="")
    parser.add_argument("--emscripten", choices=p.EMSCRIPTEN_VERSIONS, default="")
    parser.add_argument("--incoming", type=Path)
    args = parser.parse_args()
    check_authorization()
    if args.command == "authorize":
        print("Explicit dispatch inputs and clean source identity checked; not a protected attestation.")
        return
    if os.environ["BUILD_SCOPE"] not in ("build-only", "build-full-five"):
        raise ValueError("Build-only commands cannot enter a release scope")
    full_five = os.environ["BUILD_SCOPE"] == "build-full-five"
    if args.command == "source":
        build = Build("source")
        copy_new(build.source(), build.directory / "icu-source.zip")
        build.finish()
    elif args.command == "build":
        expected_payloads(args.kind, args.variant, args.emscripten)
        group = {"data": "linux", "wasm": "linux", "windows": "windows", "macos": "macos"}[args.kind]
        if os.environ["BUILD_TARGET"] not in ("all", group):
            raise ValueError("Native row exceeds authorized resource group")
        build = Build(args.kind, args.variant, args.emscripten)
        archive = build.source()
        if args.kind in ("data", "wasm"):
            docker_build(build, archive)
        elif args.kind == "windows":
            windows_build(build, archive)
        else:
            host_source = macos_build(build, archive)
        build.finish()
        if full_five and args.kind == "macos" and args.variant == "arm64":
            build_apple(host_source, archive, build.directory)
    elif args.command == "merge-macos" and os.environ["BUILD_TARGET"] in ("all", "macos"):
        merge_macos(args.incoming)
    elif args.command == "assemble" and os.environ["BUILD_TARGET"] == "all":
        assemble(args.incoming, full_five)
    else:
        raise ValueError("Aggregation exceeds authorized resource group")


if __name__ == "__main__":
    main()
