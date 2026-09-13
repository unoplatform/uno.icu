"""Read-only VS2022 discovery with retained component diagnostics. No installation."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess

import provenance as p

VERSION_RANGE = "[17.0,18.0)"
SDK_VERSION = "10.0.26100.0"


def requirements(arch):
    components = {"x64": "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
                  "arm64": "Microsoft.VisualStudio.Component.VC.Tools.ARM64"}
    if arch not in components:
        raise ValueError("Only x64 and arm64 native targets are supported")
    return [components[arch]]


def sanitize_inventory(instances):
    if not isinstance(instances, list):
        raise ValueError("vswhere must return an instance array")
    result = []
    for instance in instances:
        summary = {key: instance.get(key) for key in (
            "instanceId", "installationPath", "installationVersion", "productId",
            "isComplete", "isLaunchable", "isPrerelease")}
        summary["components"] = [
            {key: package[key] for key in ("id", "version", "type") if key in package}
            for package in instance.get("packages", []) if package.get("type") == "Component"]
        result.append(summary)
    return result


def select_instance(instances, arch):
    required = requirements(arch)
    if not instances:
        raise ValueError(f"No matching stable Visual Studio 2022 {arch} installation ({required}); "
                         "see unfiltered-inventory.json and use the documented windows-2022 runner")
    if len(instances) != 1:
        raise ValueError("Expected one latest matching Visual Studio 2022 installation")
    instance = instances[0]
    version = instance.get("installationVersion", "")
    if not isinstance(version, str) or not re.fullmatch(r"17\.\d+\.\d+\.\d+", version):
        raise ValueError("Only the explicitly supported Visual Studio 2022 / v143 toolset is accepted")
    if (instance.get("isComplete") is not True or instance.get("isLaunchable") is not True or
            instance.get("isPrerelease") is not False or not instance.get("installationPath")):
        raise ValueError("A complete, launchable, non-preview installation is required")
    installed = {component["id"] for component in instance["components"]}
    if not set(required) <= installed:
        raise ValueError(f"Missing target-specific C++ components: {required}")
    return instance


def discover(vswhere, arch, output):
    required = requirements(arch)
    output.mkdir(parents=True, exist_ok=False)
    p.write_new(output / "vswhere-tool.json", p.json_bytes({
        "path": str(vswhere), "sha256": p.sha(vswhere.read_bytes())}))

    def query(name, filters):
        command = [str(vswhere), *filters, "-products", "*", "-format", "json", "-utf8", "-include", "packages"]
        result = subprocess.run(command, capture_output=True, encoding="utf-8-sig")
        p.write_new(output / (name + "-command.json"), p.json_bytes({
            "command": command, "exitCode": result.returncode, "stderr": result.stderr}))
        result.check_returncode()
        inventory = sanitize_inventory(json.loads(result.stdout))
        p.write_new(output / (name + "-inventory.json"), p.json_bytes(inventory))
        # Only whitelisted instance fields and component IDs/versions are logged.
        # Do not dump environment variables or arbitrary installer properties.
        print(json.dumps({"query": name, "instances": inventory}), flush=True)
        return inventory

    # Deliberately before any version/component filter, including on failure.
    query("unfiltered", ["-all", "-prerelease"])
    filtered = query("filtered", ["-latest", "-version", VERSION_RANGE, "-requires", *required])
    selected = {
        "schemaVersion": 1, "targetArchitecture": arch, "versionRange": VERSION_RANGE,
        "requiredComponents": required, "platformToolset": "v143",
        "installation": select_instance(filtered, arch),
    }
    p.write_new(output / "selected.json", p.json_bytes(selected))
    return selected


def sdk_identity_files(arch):
    requirements(arch)
    return [
        f"Include/{SDK_VERSION}/shared/sdkddkver.h",
        f"Include/{SDK_VERSION}/um/Windows.h",
        f"Include/{SDK_VERSION}/ucrt/stdio.h",
        f"Lib/{SDK_VERSION}/um/{arch}/kernel32.lib",
        f"Lib/{SDK_VERSION}/ucrt/{arch}/libucrt.lib",
    ]


def sdk_identity(root, arch):
    hashes = {}
    for name in sdk_identity_files(arch):
        file = root / name
        if not file.is_file():
            raise ValueError(f"Required selected SDK identity file is missing: {file}")
        hashes[name] = p.sha(file.read_bytes())
    return {"version": SDK_VERSION, "registryRoot": str(root), "architecture": arch, "sha256": hashes,
            "qualification": "Selected SDK header/library identity files, not a hash of the entire SDK; native binlog records resolved build inputs"}


def installed_sdk_root():
    # Match the installed Windows Kits root; read-only registry access.
    import winreg
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows Kits\Installed Roots",
                       0, winreg.KEY_READ | winreg.KEY_WOW64_32KEY) as key:
        value, _ = winreg.QueryValueEx(key, "KitsRoot10")
    return Path(value)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arch", choices=("x64", "arm64"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--vswhere", type=Path)
    args = parser.parse_args()
    if os.name != "nt":
        parser.error("Real vswhere discovery requires Windows; unit fixtures are separate")
    vswhere = args.vswhere or Path(os.environ["ProgramFiles(x86)"]) / "Microsoft Visual Studio/Installer/vswhere.exe"
    discover(vswhere, args.arch, args.output)


if __name__ == "__main__":
    main()
