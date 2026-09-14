"""Apple cross-build recipes and strict ar/Mach-O platform identity checks."""
from dataclasses import dataclass
import os
from pathlib import Path
import json
import posixpath
import re
import shlex
import struct

import provenance as p

MINIMUM = 0x000D0400
CPUS = {"arm64": (0x100000C, 0), "x86_64": (0x1000007, 3)}
PLATFORMS = {"ios": 2, "tvos": 3, "iossim": 7, "tvossim": 8}
HOST_CONFIGS = ("config/icucross.mk", "config/icucross.inc", "icudefs.mk", "Makefile",
                "config/mh-darwin", "config.status", "config.log")
HOST_TOOLS = ("genrb", "genccode", "gencmn", "icupkg", "pkgdata", "gencnval", "genbrk", "gendict")
HOST_LIBRARIES = ("lib/libicuuc.dylib", "lib/libicui18n.dylib", "lib/libicutu.dylib",
                  "stubdata/libicudata.dylib")
LOCAL_OVERRIDES = ("icudefs.local", "Makefile.local", "common/Makefile.local", "data/Makefile.local")
TOOL_VARIABLES = ("CC", "CXX", "AR", "ARFLAGS", "RANLIB", "TOOLBINDIR", "TOOLLIBDIR",
                  "cross_buildroot", "INVOKE", "PKGDATA_INVOKE")
CONFIGURATION_FILES = ("Makefile", "common/Makefile", "data/Makefile", "icudefs.mk",
                       "config/mh-darwin", "data/pkgdataMakefile", "data/icupkg.inc", "data/rules.mk")


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
    flags = f"-arch {variant.arch} -isysroot {shlex.quote(str(sdk))} {variant.minimum_flag}=13.4"
    return [
        "./configure", "--enable-static", "--disable-shared", "--with-data-packaging=static",
        "--disable-tools", "--disable-extras", "--disable-tests", "--disable-samples", "--disable-dyload",
        "--host=arm-apple-darwin", "--build=" + variant.build_triplet, "--with-cross-build=" + str(host),
        "CFLAGS=" + flags, "CXXFLAGS=" + flags + " -c -stdlib=libc++ --std=c++17",
        "LDFLAGS=-stdlib=libc++ -lstdc++",
    ]


def compiler_environment(sdk):
    selected = {}
    for name, tool in (("CC", "clang"), ("CXX", "clang++"), ("AR", "ar"), ("RANLIB", "ranlib")):
        path = sdk["tools"][tool]["path"]
        if not isinstance(path, str) or not path.startswith("/") or any(char in path for char in "\r\n\0$#"):
            raise ValueError("Selected SDK tool must be an unambiguous absolute POSIX path")
        selected[name] = shlex.quote(path)
    # configure contributes 'r', then the pinned mh-darwin appends '-c'.
    # Do not import caller ARFLAGS or replace ICU's normal platform flags.
    selected["ARFLAGS"] = ""
    return selected


def variant_environment(parent, sdk, filter_file):
    excluded = {"MAKEFLAGS", "MFLAGS", "MAKEOVERRIDES", "MAKEFILES", "GNUMAKEFLAGS", "PKGDATA_OPTS", "PKGDATA",
                "INVOKE", "PKGDATA_INVOKE", "TOOLBINDIR", "TOOLLIBDIR", "cross_buildroot",
                "LEAK_CHECKER", "LD_PRELOAD"}
    return {**{key: value for key, value in parent.items() if key not in excluded and not key.startswith("DYLD_")},
            **compiler_environment(sdk), "ICU_DATA_FILTER_FILE": str(filter_file)}


def tool_probe_makefile(include_icudefs):
    prefix = "srcdir=.\ntop_srcdir=.\ntop_builddir=.\ninclude icudefs.mk\n" if include_icudefs else ""
    names = TOOL_VARIABLES if include_icudefs else TOOL_VARIABLES + ("PKGDATA_OPTS", "PKGDATA")
    # Make expands the values without interpolating them into a shell command.
    return (prefix + ".PHONY: __uno_icu_tool_identity\n__uno_icu_tool_identity:\n" +
            "".join(f"\t$(info UNO_ICU_TOOL_{name}=$({name}))\n" for name in names) + "\t@:\n")


def make_probe_values(text, data_scope):
    expected = set(TOOL_VARIABLES) | ({"PKGDATA_OPTS", "PKGDATA"} if data_scope else set())
    values = {}
    for line in text.splitlines():
        if not line.startswith("UNO_ICU_TOOL_"):
            continue
        name, value = line[len("UNO_ICU_TOOL_"):].split("=", 1)
        if name not in expected or name in values:
            raise ValueError("Unexpected/duplicate evaluated ICU tool variable")
        values[name] = value.strip()
    if set(values) != expected:
        raise ValueError("Incomplete evaluated ICU tool configuration")
    return values


def configuration_assignments(text, names):
    values = {}
    for line in text.splitlines():
        match = re.match(r"^\s*([A-Za-z_][A-Za-z_0-9]*)\s*([:+?]?=)\s*(.*?)\s*$", line)
        if match and match[1] in names:
            if match[1] in values or match[2] != "=":
                raise ValueError("Ambiguous generated ICU tool assignment")
            values[match[1]] = match[3]
    if set(values) != set(names):
        raise ValueError("Missing generated ICU tool assignment")
    return values


def verify_tool_configuration(source, sdk, host_source, root_output, data_output):
    reject_local_overrides(source)
    host_source = str(host_source).rstrip("/")
    selected = {name: sdk["tools"][tool]["path"] for name, tool in
                (("CC", "clang"), ("CXX", "clang++"), ("AR", "ar"), ("RANLIB", "ranlib"))}
    declared = configuration_assignments((source / "icudefs.mk").read_text(encoding="utf-8"),
                                         ("CC", "CXX", "AR", "RANLIB", "ARFLAGS"))
    evaluated = make_probe_values(root_output, False)
    data_evaluated = make_probe_values(data_output, True)
    pkgdata = configuration_assignments((source / "data/icupkg.inc").read_text(encoding="utf-8"),
                                       ("AR", "ARFLAGS", "RANLIB", "COMPILE"))
    for config in (declared, evaluated, data_evaluated, pkgdata):
        for name, path in selected.items():
            if name in config and shlex.split(config[name]) != [path]:
                raise ValueError("Generated ICU configuration substitutes/ambiguously quotes " + name)
    if (shlex.split(declared["ARFLAGS"]) != ["r"] or
            any(shlex.split(config["ARFLAGS"]) != ["r", "-c"] for config in (evaluated, data_evaluated, pkgdata))):
        raise ValueError("Generated ICU archive flags differ from the normal Darwin recipe")
    if not shlex.split(pkgdata["COMPILE"]) or shlex.split(pkgdata["COMPILE"])[0] != selected["CC"]:
        raise ValueError("pkgdata compiler does not use the selected SDK compiler")
    for config in (evaluated, data_evaluated):
        if (config["cross_buildroot"] != host_source or config["TOOLBINDIR"] != host_source + "/bin" or
                config["TOOLLIBDIR"] != host_source + "/lib"):
            raise ValueError("Generated configuration substitutes the native host build")
        for name, folders in (("INVOKE", ("lib", "stubdata", "tools/ctestfw")),
                              ("PKGDATA_INVOKE", ("stubdata", "tools/ctestfw", "lib"))):
            expected = "DYLD_LIBRARY_PATH=" + ":".join(host_source + "/" + folder for folder in folders) + ":$DYLD_LIBRARY_PATH"
            if shlex.split(config[name]) != [expected]:
                raise ValueError("Generated host invocation changes the recorded library search closure")
    if shlex.split(data_evaluated["PKGDATA_OPTS"]) != ["-O", "../data/icupkg.inc"]:
        raise ValueError("pkgdata does not select the verified target configuration")
    command = shlex.split(data_evaluated["PKGDATA"])
    if (not command or command[0] != host_source + "/bin/pkgdata" or command.count("-O") != 1 or
            command[command.index("-O") + 1:command.index("-O") + 2] != ["../data/icupkg.inc"]):
        raise ValueError("Generated pkgdata invocation substitutes tool/configuration")
    rules = (source / "data/rules.mk").read_text(encoding="utf-8")
    used = set(re.findall(r"\$\(TOOLBINDIR\)/([A-Za-z0-9_]+)", rules))
    if not used or not used <= set(HOST_TOOLS):
        raise ValueError("Generated data rules require an unrecorded host tool")
    return {
        "selectedTools": selected, "archiveFlags": ["r", "-c"], "icudefs": declared,
        "makeEvaluated": evaluated, "dataMakeEvaluated": data_evaluated, "pkgdata": pkgdata,
        "generatedRuleHostTools": sorted(used),
        "fileSha256": {name: p.sha((source / name).read_bytes()) for name in CONFIGURATION_FILES},
    }


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
    position, versions, dependencies, install_name = 32, [], [], None
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
        if command in (0xC, 0xD, 0x80000018, 0x8000001F, 0x80000023):
            if size < 24:
                raise ValueError("Truncated Mach-O dylib command")
            name_offset = struct.unpack_from("<I", data, position + 8)[0]
            if name_offset < 24 or name_offset >= size:
                raise ValueError("Invalid Mach-O dylib name")
            raw_name = data[position + name_offset:position + size]
            if b"\0" not in raw_name:
                raise ValueError("Unterminated Mach-O dylib name")
            name = raw_name.split(b"\0", 1)[0].decode("utf-8")
            if command == 0xD:
                if install_name is not None:
                    raise ValueError("Duplicate Mach-O install identity")
                install_name = name
            else:
                dependencies.append(name)
        position += size
    if position != end or len(versions) != 1:
        raise ValueError("Exactly one explicit LC_BUILD_VERSION is required")
    actual_platform, minos, sdk = versions[0]
    if actual_platform != platform or (minimum is not None and minos != minimum):
        raise ValueError("Wrong Apple device/simulator platform or deployment minimum")
    return {"architecture": arch, "cpu": cpu, "cpuSubtype": subtype, "fileType": actual_type, "platform": actual_platform,
            "minimumVersion": minos, "sdkVersionInObject": sdk, "sha256": p.sha(data),
            "installName": install_name, "dylibDependencies": dependencies}


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


def selected_host_tools(filter_data):
    filters = json.loads(filter_data)
    features = filters.get("featureFilters", {})
    required = {
        "brkitr_rules": "genbrk", "brkitr_dictionaries": "gendict", "brkitr_tree": "genrb",
        "cnvalias": "gencnval", "locales_tree": "genrb", "ulayout": "icupkg", "uemoji": "icupkg",
    }
    if filters.get("strategy") != "additive" or set(features) != set(required):
        raise ValueError("Review the host tool closure for this changed ICU filter")
    for category, value in features.items():
        if category == "brkitr_rules":
            if value != {"filterType": "regex", "excludelist": ["^.*char.*$"]}:
                raise ValueError("Review the host break-rule closure for this changed filter")
        elif value != "include":
            raise ValueError("Review the selected data generator closure")
    # BUILDRULES.py selects five generators. pkgdata and its normal
    # genccode/gencmn packaging helpers are retained as the packaging tool set.
    return tuple(sorted(set(required.values()) | {"pkgdata", "genccode", "gencmn"}))


def reject_local_overrides(source):
    for name in LOCAL_OVERRIDES:
        if (source / name).exists():
            raise ValueError("Unreviewed local ICU configuration override: " + name)


def host_dependency_bindings(executables, libraries):
    result = {}
    for tool in executables:
        search = ("stubdata", "tools/ctestfw", "lib") if tool == "bin/pkgdata" else ("lib", "stubdata", "tools/ctestfw")
        pending, seen, graph = [tool], set(), {}
        while pending:
            owner = pending.pop()
            if owner in seen:
                continue
            seen.add(owner)
            identity = executables[owner] if owner in executables else libraries[owner]
            edges = {}
            for dependency in identity["dylibDependencies"]:
                if dependency.startswith(("/usr/lib/", "/System/Library/")):
                    edges[dependency] = "system:" + dependency
                    continue
                basename = posixpath.basename(dependency)
                if not re.fullmatch(r"libicu[A-Za-z0-9_.-]+\.dylib", basename):
                    raise ValueError("Unreviewed non-system host library dependency: " + dependency)
                selected = next((folder + "/" + basename for folder in search
                                 if folder + "/" + basename in libraries), None)
                if selected is None:
                    raise ValueError("Missing consumed ICU host library: " + dependency)
                edges[dependency] = selected
                pending.append(selected)
            graph[owner] = edges
        result[tool] = graph
    return result


def host_identity(source, filter_data=None):
    if filter_data is None:
        filter_data = (p.ROOT / "src/cldr_data/filters.json").read_bytes()
    tools = selected_host_tools(filter_data)
    reject_local_overrides(source)
    root = source.resolve()
    required = list(HOST_CONFIGS) + list(HOST_LIBRARIES)
    required += ["bin/" + name for name in tools]
    required += [file.relative_to(source).as_posix() for parent in ("lib", "stubdata", "tools/ctestfw")
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
    bindings = host_dependency_bindings(executable, libraries)
    return {"fileSha256": hashes, "executables": executable, "libraries": libraries,
            "filterSha256": p.sha(filter_data), "dependencyBindings": bindings,
            "qualification": "Selected ICU host generator/packaging/configuration and direct/transitive ICU dylib closure; system-library names are recorded, not a whole-OS proof; no old thin-artifact host-tree claim"}


def verify_host_identity(identity, filter_sha):
    expected_tools = {"bin/" + name for name in HOST_TOOLS}
    required = set(HOST_CONFIGS) | set(HOST_LIBRARIES) | expected_tools
    hashes = identity["fileSha256"]
    if (identity["filterSha256"] != filter_sha or not required <= set(hashes) or
            any(not re.fullmatch("[0-9a-f]{64}", value) for value in hashes.values())):
        raise ValueError("Incomplete retained host configuration/input identity")
    tools, libraries = identity["executables"], identity["libraries"]
    if set(tools) != expected_tools or not set(HOST_LIBRARIES) <= set(libraries):
        raise ValueError("Incomplete retained host tool/library set")
    if (set(hashes) != set(HOST_CONFIGS) | set(tools) | set(libraries) or
            any(not re.fullmatch(r"(lib|stubdata|tools/ctestfw)/[^/]+\.dylib", name) for name in libraries)):
        raise ValueError("Unbound or invalid retained host input path")
    for entries, file_type in ((tools, 2), (libraries, 6)):
        for name, item in entries.items():
            if (item["architecture"] != "arm64" or item["platform"] != 1 or item["fileType"] != file_type or
                    item["sha256"] != hashes[name]):
                raise ValueError("Substituted host binary identity")
    if host_dependency_bindings(tools, libraries) != identity["dependencyBindings"]:
        raise ValueError("Retained host dylib dependency binding mismatch")


def verify_receipt(directory, host_digest, source_lock, filter_sha):
    """Check retained thin/final archive bindings without executing any tool."""
    receipt = json.loads((directory / "apple-build.json").read_text(encoding="utf-8"))
    if (receipt["schemaVersion"] != 1 or receipt["hostArtifactSha256"] != host_digest or
            receipt["minimumDeployment"] != "13.4" or receipt["runtimeTested"] is not False or
            receipt["authenticatedAttestation"] is not False or not receipt.get("hostSource")):
        raise ValueError("Invalid Apple host/build receipt")
    verify_host_identity(receipt["hostIdentity"], filter_sha)
    for name in HOST_CONFIGS:
        if p.sha((directory / "host-config" / name).read_bytes()) != receipt["hostIdentity"]["fileSha256"][name]:
            raise ValueError("Retained host configuration differs from its consumed input")
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
                entry["compilerEnvironment"] != compiler_environment(sdk)):
            raise ValueError("Apple variant source/filter/recipe identity mismatch")
        if not sdk.get("version") or not sdk.get("buildVersion") or not re.fullmatch("[0-9a-f]{64}", sdk["settingsSha256"]):
            raise ValueError("Incomplete Apple SDK identity")
        if set(sdk["tools"]) != {"clang", "clang++", "ar", "ranlib", "lipo"}:
            raise ValueError("Incomplete Apple compiler/tool identity")
        for tool in sdk["tools"].values():
            if not tool["path"] or not re.fullmatch("[0-9a-f]{64}", tool["sha256"]):
                raise ValueError("Invalid Apple compiler/tool identity")
        variant_dir = directory / "variants" / variant.name
        before = verify_tool_configuration(
            variant_dir / "configuration", sdk, receipt["hostSource"],
            (variant_dir / "make-evaluated-before.txt").read_text(encoding="utf-8"),
            (variant_dir / "data-make-evaluated-before.txt").read_text(encoding="utf-8"))
        after = verify_tool_configuration(
            variant_dir / "configuration-final", sdk, receipt["hostSource"],
            (variant_dir / "make-evaluated-after.txt").read_text(encoding="utf-8"),
            (variant_dir / "data-make-evaluated-after.txt").read_text(encoding="utf-8"))
        if before != after or before != entry["toolConfiguration"]:
            raise ValueError("Retained archive tool/configuration binding mismatch")
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
