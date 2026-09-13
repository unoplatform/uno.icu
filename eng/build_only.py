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

ROOT = p.ROOT
OUT = ROOT / "artifacts/build-only"
VARIANTS = {"data": ("",), "wasm": ("st", "mt", "st,simd", "mt,simd"),
            "windows": ("x64", "arm64"), "macos": ("x86_64", "arm64")}
BUILD_KEYS = ("GITHUB_REPOSITORY", "GITHUB_SHA", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT",
              "GITHUB_WORKFLOW_REF", "GITHUB_JOB", "RUNNER_OS", "RUNNER_ARCH",
              "ImageOS", "ImageVersion", "WORKFLOW_SHA", "DISPATCH_OPERATION")


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


def matrix_rows():
    rows = [("source", "", ""), ("data", "", ""), ("macos-universal", "", "")]
    rows += [("wasm", v, version) for version in p.EMSCRIPTEN_VERSIONS for v in VARIANTS["wasm"]]
    rows += [(kind, v, "") for kind in ("windows", "macos") for v in VARIANTS[kind]]
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


def assemble(incoming):
    build = Build("staging")
    build.source()
    rows = matrix_rows()
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
    if os.environ["BUILD_SCOPE"] != "build-only":
        raise ValueError("Build-only commands cannot enter a release scope")
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
            macos_build(build, archive)
        build.finish()
    elif args.command == "merge-macos" and os.environ["BUILD_TARGET"] in ("all", "macos"):
        merge_macos(args.incoming)
    elif args.command == "assemble" and os.environ["BUILD_TARGET"] == "all":
        assemble(args.incoming)
    else:
        raise ValueError("Aggregation exceeds authorized resource group")


if __name__ == "__main__":
    main()
