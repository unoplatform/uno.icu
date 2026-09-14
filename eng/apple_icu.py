"""Apple cross-build recipes and strict ar/Mach-O platform identity checks."""
from dataclasses import dataclass
import os
from pathlib import Path
import json
import re
import struct

import provenance as p

MINIMUM = 0x000D0400
CPUS = {"arm64": (0x100000C, 0), "x86_64": (0x1000007, 3)}
PLATFORMS = {"ios": 2, "tvos": 3, "iossim": 7, "tvossim": 8}


@dataclass(frozen=True)
class Variant:
    target: str
    arch: str
    sdk: str
    minimum_flag: str
    build_triplet: str

    @property
    def name(self):
        return self.target + "-" + self.arch


VARIANTS = (
    Variant("ios", "arm64", "iphoneos", "-mios-version-min", "arm-apple"),
    Variant("iossim", "arm64", "iphonesimulator", "-mios-simulator-version-min", "arm64-apple"),
    Variant("iossim", "x86_64", "iphonesimulator", "-mios-simulator-version-min", "x86_64-apple"),
    Variant("tvos", "arm64", "appletvos", "-mtvos-version-min", "arm-apple"),
    Variant("tvossim", "arm64", "appletvsimulator", "-mtvos-simulator-version-min", "arm64-apple"),
    Variant("tvossim", "x86_64", "appletvsimulator", "-mtvos-simulator-version-min", "x86_64-apple"),
)


def payload_specs():
    return {
        f"nuget/uno.icu-{'ios' if target.startswith('ios') else 'tvos'}/{target}/{library}.a":
            (platform, ("arm64", "x86_64") if target.endswith("sim") else ("arm64",))
        for target, platform in PLATFORMS.items() for library in ("libicuuc", "libicudata")
    }


def configure_command(variant, sdk, host):
    if variant not in VARIANTS:
        raise ValueError("Unknown Apple cross-build variant")
    flags = f"-arch {variant.arch} -isysroot {sdk} {variant.minimum_flag}=13.4"
    return [
        "./configure", "--enable-static", "--disable-shared", "--with-data-packaging=static",
        "--disable-tools", "--disable-extras", "--disable-tests", "--disable-samples", "--disable-dyload",
        "--host=arm-apple-darwin", "--build=" + variant.build_triplet, "--with-cross-build=" + str(host),
        "CFLAGS=" + flags, "CXXFLAGS=" + flags + " -c -stdlib=libc++ --std=c++17",
        "LDFLAGS=-stdlib=libc++ -lstdc++",
    ]


def macho_identity(data, platform, arch, minimum=MINIMUM, file_type=1):
    if len(data) < 32 or data[:4] != b"\xcf\xfa\xed\xfe":
        raise ValueError("Expected a 64-bit little-endian Mach-O member")
    _, cpu, subtype, actual_type, count, command_bytes, _, _ = struct.unpack_from("<IiiIIIII", data)
    expected_cpu, expected_subtype = CPUS[arch]
    if cpu != expected_cpu or subtype & 0xFFFFFF != expected_subtype or actual_type != file_type:
        raise ValueError("Mach-O CPU/subtype/file type differs from the required target")
    end = 32 + command_bytes
    if end > len(data) or count > command_bytes // 8:
        raise ValueError("Invalid Mach-O load-command bounds")
    position, versions = 32, []
    for _ in range(count):
        if position + 8 > end:
            raise ValueError("Truncated Mach-O load command")
        command, size = struct.unpack_from("<II", data, position)
        if size < 8 or position + size > end or size % 8:
            raise ValueError("Invalid Mach-O load-command size")
        if command in (0x24, 0x25, 0x2F, 0x30):
            raise ValueError("Ambiguous legacy version-min command; require explicit LC_BUILD_VERSION platform")
        if command == 0x32:
            if size < 24:
                raise ValueError("Truncated LC_BUILD_VERSION")
            actual_platform, minos, sdk, tools = struct.unpack_from("<IIII", data, position + 8)
            if size != 24 + tools * 8:
                raise ValueError("Invalid LC_BUILD_VERSION tool count")
            versions.append((actual_platform, minos, sdk))
        position += size
    if position != end or len(versions) != 1:
        raise ValueError("Exactly one explicit LC_BUILD_VERSION is required")
    actual_platform, minos, sdk = versions[0]
    if actual_platform != platform or (minimum is not None and minos != minimum):
        raise ValueError("Wrong Apple device/simulator platform or deployment minimum")
    return {"architecture": arch, "cpu": cpu, "cpuSubtype": subtype, "fileType": actual_type, "platform": actual_platform,
            "minimumVersion": minos, "sdkVersionInObject": sdk, "sha256": p.sha(data)}


def ar_members(data):
    if not data.startswith(b"!<arch>\n"):
        raise ValueError("Expected a self-contained ar archive, not a thin/reference archive")
    offset, strings = 8, b""
    members = []
    while offset < len(data):
        if offset + 60 > len(data):
            raise ValueError("Truncated ar member header")
        header = data[offset:offset + 60]
        if header[58:60] != b"`\n":
            raise ValueError("Invalid ar member header")
        size = int(header[48:58].strip())
        start, end = offset + 60, offset + 60 + size
        if size < 0 or end > len(data):
            raise ValueError("Invalid ar member bounds")
        name = header[:16].decode("ascii").strip()
        payload = data[start:end]
        offset = end + size % 2
        if offset > len(data):
            raise ValueError("Missing ar alignment byte")
        if name == "//":
            strings = payload
            continue
        if name in ("/", "/SYM64/"):
            continue
        if name.startswith("#1/"):
            length = int(name[3:])
            if length <= 0 or length > len(payload):
                raise ValueError("Invalid BSD ar extended name")
            name = payload[:length].rstrip(b"\0").decode("utf-8")
            payload = payload[length:]
        elif name.startswith("/") and name[1:].isdigit():
            index = int(name[1:])
            if index >= len(strings):
                raise ValueError("Invalid GNU ar long-name reference")
            name = strings[index:].split(b"/\n", 1)[0].split(b"\0", 1)[0].decode("utf-8")
        else:
            name = name.rstrip("/")
        if name in ("__.SYMDEF", "__.SYMDEF SORTED", "__.SYMDEF_64", "__.SYMDEF_64 SORTED"):
            continue
        if not name or not payload:
            raise ValueError("Empty/unknown ar member")
        members.append((name, payload))
    if not members:
        raise ValueError("Archive has no native object members")
    return members


def validate_archive(data, platform, architectures):
    if platform not in PLATFORMS.values() or not architectures or set(architectures) - set(CPUS):
        raise ValueError("Unsupported Apple archive target")
    slices = {}
    if data[:4] in (b"\xca\xfe\xba\xbe", b"\xca\xfe\xba\xbf"):
        if len(architectures) != 2 or len(data) < 8:
            raise ValueError("Unexpected universal archive for a thin/device target")
        count = struct.unpack_from(">I", data, 4)[0]
        wide = data[:4] == b"\xca\xfe\xba\xbf"
        descriptor_size = 32 if wide else 20
        table_end = 8 + count * descriptor_size
        if count != len(architectures) or table_end > len(data):
            raise ValueError("Incomplete/invalid universal architecture table")
        ranges = []
        for index in range(count):
            values = struct.unpack_from(">IIQQII" if wide else ">IIIII", data, 8 + index * descriptor_size)
            cpu, subtype, offset, size, alignment = values[:5]
            arch = next((name for name, pair in CPUS.items() if pair == (cpu, subtype & 0xFFFFFF)), None)
            if (arch not in architectures or arch in slices or size <= 0 or offset < table_end or
                    offset + size > len(data) or alignment > 31 or offset % (1 << alignment)):
                raise ValueError("Invalid/substituted universal archive slice")
            if any(offset < end and offset + size > start for start, end in ranges):
                raise ValueError("Overlapping universal archive slices")
            ranges.append((offset, offset + size))
            slices[arch] = data[offset:offset + size]
    else:
        if len(architectures) != 1:
            raise ValueError("Missing universal simulator CPU slice")
        slices[architectures[0]] = data
    if set(slices) != set(architectures):
        raise ValueError("Incomplete archive architecture set")
    details = {}
    for arch, contents in slices.items():
        details[arch] = [{"member": name, **macho_identity(member, platform, arch)}
                         for name, member in ar_members(contents)]
    return {"sha256": p.sha(data), "architectures": list(slices), "platform": platform,
            "minimumDeployment": "13.4", "members": details,
            "sliceSha256": {arch: p.sha(contents) for arch, contents in slices.items()}, "runtimeTested": False}


def host_identity(source):
    root = source.resolve()
    required = ["config/icucross.mk", "config.status", "config.log",
                "lib/libicuuc.dylib", "lib/libicui18n.dylib", "stubdata/libicudata.dylib"]
    tools = ("genrb", "genccode", "gencmn", "icupkg", "pkgdata")
    required += ["bin/" + name for name in tools]
    required += [file.relative_to(source).as_posix() for parent in ("lib", "stubdata")
                 for file in (source / parent).glob("*.dylib")]
    hashes, executable, libraries = {}, {}, {}
    for name in sorted(set(required)):
        path = source / name
        if not path.is_file() or not path.resolve().is_relative_to(root):
            raise ValueError("Missing or external ARM64 host build input: " + name)
        data = path.read_bytes()
        hashes[name] = p.sha(data)
        if name.startswith("bin/"):
            if not os.access(path, os.X_OK):
                raise ValueError("ARM64 host tool is not executable: " + name)
            executable[name] = macho_identity(data, 1, "arm64", minimum=None, file_type=2)
        elif name.endswith(".dylib"):
            libraries[name] = macho_identity(data, 1, "arm64", minimum=None, file_type=6)
    return {"fileSha256": hashes, "executables": executable, "libraries": libraries,
            "qualification": "Actual same-job ARM64 macOS host tools and library/config inputs; no claim that older thin-library artifacts contain this tree"}


def verify_receipt(directory, host_digest, source_lock, filter_sha):
    """Check retained thin/final archive bindings without executing any tool."""
    receipt = json.loads((directory / "apple-build.json").read_text(encoding="utf-8"))
    if (receipt["schemaVersion"] != 1 or receipt["hostArtifactSha256"] != host_digest or
            receipt["minimumDeployment"] != "13.4" or receipt["runtimeTested"] is not False or
            receipt["authenticatedAttestation"] is not False or not receipt.get("hostSource")):
        raise ValueError("Invalid Apple host/build receipt")
    tools = receipt["hostIdentity"]["executables"]
    expected_tools = {"bin/" + name for name in ("genrb", "genccode", "gencmn", "icupkg", "pkgdata")}
    if set(tools) != expected_tools or "config/icucross.mk" not in receipt["hostIdentity"]["fileSha256"]:
        raise ValueError("Incomplete retained ARM64 host identity")
    for name, identity in tools.items():
        if (identity["architecture"] != "arm64" or identity["platform"] != 1 or identity["fileType"] != 2 or
                identity["sha256"] != receipt["hostIdentity"]["fileSha256"][name]):
            raise ValueError("Substituted host tool identity")
    libraries = receipt["hostIdentity"]["libraries"]
    if not {"lib/libicuuc.dylib", "lib/libicui18n.dylib", "stubdata/libicudata.dylib"} <= set(libraries):
        raise ValueError("Incomplete retained host library identity")
    for name, identity in libraries.items():
        if (identity["architecture"] != "arm64" or identity["platform"] != 1 or identity["fileType"] != 6 or
                identity["sha256"] != receipt["hostIdentity"]["fileSha256"][name]):
            raise ValueError("Substituted host library identity")
    variants = receipt["variants"]
    if len(variants) != 6 or {entry["name"] for entry in variants} != {variant.name for variant in VARIANTS}:
        raise ValueError("Incomplete or duplicate Apple cross-build variants")
    by_name = {entry["name"]: entry for entry in variants}
    for variant in VARIANTS:
        entry = by_name[variant.name]
        sdk = receipt["sdkIdentities"][variant.sdk]
        if (entry["target"] != variant.target or entry["architecture"] != variant.arch or
                entry["platform"] != PLATFORMS[variant.target] or entry["sdk"] != variant.sdk or
                entry["sourceArchiveSha256"] != source_lock["archiveSha256"] or
                entry["filterSha256"] != filter_sha or
                entry["recipe"] != configure_command(variant, sdk["path"], receipt["hostSource"]) or
                entry["compilerEnvironment"] != {"CC": sdk["tools"]["clang"]["path"],
                                                  "CXX": sdk["tools"]["clang++"]["path"]}):
            raise ValueError("Apple variant source/filter/recipe identity mismatch")
        if not sdk.get("version") or not sdk.get("buildVersion") or not re.fullmatch("[0-9a-f]{64}", sdk["settingsSha256"]):
            raise ValueError("Incomplete Apple SDK identity")
        if set(sdk["tools"]) != {"clang", "clang++", "ar", "lipo"}:
            raise ValueError("Incomplete Apple compiler/tool identity")
        for tool in sdk["tools"].values():
            if not tool["path"] or not re.fullmatch("[0-9a-f]{64}", tool["sha256"]):
                raise ValueError("Invalid Apple compiler/tool identity")
        if set(entry["archives"]) != {"libicuuc.a", "libicudata.a"}:
            raise ValueError("Incomplete thin archive pair")
        for library, expected in entry["archives"].items():
            actual = validate_archive((directory / "variants" / variant.name / library).read_bytes(),
                                      PLATFORMS[variant.target], (variant.arch,))
            if actual != expected:
                raise ValueError("Retained thin archive identity mismatch")
    expected_merges = {path for path in payload_specs() if "/iossim/" in path or "/tvossim/" in path}
    if set(receipt["simulatorMerges"]) != expected_merges:
        raise ValueError("Incomplete simulator merge records")
    for path, (platform, arches) in payload_specs().items():
        actual = validate_archive((directory / "payload" / path).read_bytes(), platform, arches)
        target, library = Path(path).parts[-2:]
        if len(arches) == 1:
            expected = by_name[target + "-arm64"]["archives"][library]["sha256"]
            if actual["sha256"] != expected:
                raise ValueError("Device payload differs from its built variant")
        else:
            expected = {arch: by_name[target + "-" + arch]["archives"][library]["sha256"] for arch in arches}
            if actual != receipt["simulatorMerges"][path] or actual["sliceSha256"] != expected:
                raise ValueError("Simulator payload does not contain the exact built CPU archives")
    return receipt
