"""Source-pinned ICU packaging contracts. Uses only Python's standard library."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[1]
NS = {"n": "http://schemas.microsoft.com/packaging/2010/07/nuspec.xsd"}
REPOSITORY = "https://github.com/unoplatform/uno.icu.git"
PACKAGE_IDS = ("Uno.icu-win", "Uno.icu-macos", "Uno.icu-wasm", "Uno.icu-ios", "Uno.icu-tvos")
EMSCRIPTEN_VERSIONS = ("3.1.56", "5.0.6")
APPLE_STATIC_IDS = ("Uno.icu-ios", "Uno.icu-tvos")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def load_lock(root=ROOT):
    return json.loads((root / "eng/source-lock.json").read_text(encoding="utf-8"))

def load_tools(root=ROOT):
    return json.loads((root / "eng/tools-lock.json").read_text(encoding="utf-8"))


def fetch_tool(path, root=ROOT):
    tool = load_tools(root)["nuget"]
    if not path.exists():
        with urllib.request.urlopen(tool["url"], timeout=120) as response:
            write_new(path, response.read())
    verify_tool(path, root)


def verify_tool(path, root=ROOT):
    if sha(path.read_bytes()) != load_tools(root)["nuget"]["sha256"]:
        raise ValueError("NuGet pack tool SHA-256 mismatch")


def write_new(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(data)


def json_bytes(value):
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def git(*args, root=ROOT):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def verify_source(archive, lock):
    if sha(archive.read_bytes()) != lock["archiveSha256"]:
        raise ValueError("ICU source archive SHA-256 mismatch")
    with zipfile.ZipFile(archive) as source:
        license_bytes = source.read(f'icu-{lock["commit"]}/LICENSE')
    if sha(license_bytes) != lock["licenseSha256"]:
        raise ValueError("Complete ICU LICENSE SHA-256 mismatch")
    return license_bytes


def fetch_source(archive, root=ROOT):
    lock = load_lock(root)
    if not archive.exists():
        # Never replace a previously downloaded archive, including on failure.
        archive.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(lock["archiveUrl"], timeout=120) as response:
            write_new(archive, response.read())
    verify_source(archive, lock)


def validate_version(version):
    # Follow the prospective checkout's NBGV major/minor, never the historical
    # 77.2 reconstruction line. NBGV supplies the concrete patch/prerelease.
    configured = json.loads((ROOT / "version.json").read_text(encoding="utf-8"))["version"]
    line = re.match(r"(\d+)\.(\d+)(?:[.-]|$)", configured)
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:-[0-9A-Za-z.-]+)?", version)
    if not line or not match or match.groups()[:2] != line.groups():
        raise ValueError("Use the current version.json major/minor and a concrete NBGV package version")


def package_files(nuspec):
    """Resolve NuGet file mappings, rejecting missing inputs and collisions."""
    return package_files_from_tree(ET.parse(nuspec), nuspec)


def prepare(version, archive, root=ROOT):
    validate_version(version)
    # Tracked source identity must be honest; generated native outputs may be untracked.
    if git("status", "--porcelain", "--untracked-files=no", root=root):
        raise ValueError("Commit tracked source changes before preparing release metadata")
    untracked = git("ls-files", "--others", "--exclude-standard", root=root).splitlines()
    if any(name.startswith(("eng/", "src/", "tests/", "nuget/", ".github/")) for name in untracked):
        raise ValueError("Untracked build/packaging source prevents a complete commit identity")
    commit = git("rev-parse", "HEAD", root=root)
    lock = load_lock(root)
    upstream_license = verify_source(archive, lock)
    uno_license = (root / "LICENSE.md").read_bytes()
    generated = root / "nuget/provenance"
    write_new(generated / "ICU-LICENSE.txt", upstream_license)
    write_new(generated / "Uno-LICENSE.md", uno_license)
    write_new(generated / "NOTICE.md", (root / "eng/NOTICE.md").read_bytes())
    write_new(generated / "LICENSE.txt", (
        b"ICU native libraries/data: complete upstream license and notices\n\n"
        + upstream_license
        + b"\n\nUno packaging/build code and WASM shim: separate license\n\n"
        + uno_license
    ))
    inputs = {}
    for name in git("ls-files", root=root).splitlines():
        if name.startswith(("src/", "eng/", "nuget/", ".github/workflows/")) or name in ("LICENSE.md", "version.json"):
            inputs[name] = sha((root / name).read_bytes())
    manifest = {
        "schemaVersion": 1, "packageVersion": version,
        "unoRepository": {"url": REPOSITORY, "commit": commit},
        "upstream": lock, "tools": load_tools(root), "inputSha256": inputs,
        "build": {key: os.environ.get(key) for key in (
            "GITHUB_REPOSITORY", "GITHUB_SHA", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT",
            "GITHUB_WORKFLOW_REF", "RUNNER_OS", "ImageOS", "ImageVersion")},
    }
    if manifest["build"]["GITHUB_SHA"] not in (None, commit):
        raise ValueError("Checked-out commit differs from build commit")
    write_new(generated / "source.json", json_bytes(manifest))
    for package_id in PACKAGE_IDS:
        nuspec = root / "nuget" / package_id.lower() / f"{package_id.lower()}.nuspec"
        # Inventory itself cannot include its own hash. All other shipped inputs do.
        inventory = generated / f"{package_id}.payloads.json"
        tree = ET.parse(nuspec)
        for element in list(tree.find("n:files", NS)):
            if element.attrib["src"].replace("\\", "/").endswith(inventory.name):
                tree.find("n:files", NS).remove(element)
        # Do not write or alter the authoritative nuspec during inventory generation.
        files = package_files_from_tree(tree, nuspec)
        entries = {name: sha(path.read_bytes()) for name, path in files.items()}
        validate_native_entries(package_id, entries)
        write_new(inventory, json_bytes({
            "schemaVersion": 1, "packageId": package_id,
            "packageVersion": version, "sha256": entries,
        }))
    print(f"Prepared notices and source metadata for {version} at {commit}")


def package_files_from_tree(tree, nuspec):
    # Reuse the mapping parser without temporary source files.
    return resolve_files(tree.findall("n:files/n:file", NS), nuspec)


def resolve_files(elements, nuspec):
    files = {}
    for item in elements:
        pattern = item.attrib["src"].replace("\\", "/")
        target = item.attrib["target"].replace("\\", "/")
        if "**" in pattern:
            base = nuspec.parent / pattern.split("**")[0]
            entries = [(p, f"{target}/{p.relative_to(base).as_posix()}")
                       for p in sorted(base.rglob("*")) if p.is_file()]
        else:
            path = nuspec.parent / pattern
            entries = [(path, f"{target}/{path.name}" if target else path.name)]
        if not entries:
            raise ValueError(f"Empty package input: {pattern}")
        for path, entry in entries:
            if not path.is_file() or entry in files:
                raise ValueError(f"Missing or duplicate package input: {entry}")
            files[entry] = path
    return files


def validate_native_entries(package_id, entries):
    if package_id == "Uno.icu-wasm":
        expected = {f"buildTransitive/native/unoicu.a/{version}/{mode}/unoicu.a"
                    for version in EMSCRIPTEN_VERSIONS for mode in ("st", "mt", "st,simd", "mt,simd")}
    elif package_id == "Uno.icu-win":
        expected = {f"runtimes/win-{arch}/native/{name}77.dll"
                    for arch in ("x64", "arm64") for name in ("icuuc", "icudt")}
    elif package_id == "Uno.icu-macos":
        expected = {f"runtimes/osx/native/{name}.dylib" for name in ("libicuuc", "libicudata")}
    elif package_id == "Uno.icu-ios":
        expected = {f"buildTransitive/{arch}/{name}.a"
                    for arch in ("ios", "iossim") for name in ("libicuuc", "libicudata")}
    elif package_id == "Uno.icu-tvos":
        expected = {f"buildTransitive/{arch}/{name}.a"
                    for arch in ("tvos", "tvossim") for name in ("libicuuc", "libicudata")}
    else:
        raise ValueError(f"Unexpected package identity: {package_id}")
    native = {name for name in entries if name.endswith((".dll", ".dylib", ".a"))}
    if native != expected:
        raise ValueError(f"Incomplete/unexpected native matrix for {package_id}: {native}")
    if package_id not in APPLE_STATIC_IDS and "buildTransitive/icudt.dat" not in entries:
        raise ValueError("Missing filtered ICU data")


def inspect_package(path, version, commit, root=ROOT):
    validate_version(version)
    with zipfile.ZipFile(path) as package:
        names = package.namelist()
        if len(names) != len(set(n.lower() for n in names)):
            raise ValueError("Duplicate or case-colliding archive entries")
        nuspecs = [n for n in names if n.endswith(".nuspec")]
        if len(nuspecs) != 1:
            raise ValueError("Expected one nuspec")
        # NuGet pack upgrades the namespace (e.g. 2010/07 -> 2013/05).
        metadata = ET.fromstring(package.read(nuspecs[0])).find("{*}metadata")
        if metadata is None:
            raise ValueError("Missing package metadata")
        package_id = metadata.findtext("{*}id")
        if metadata.findtext("{*}version") != version:
            raise ValueError("Package version mismatch")
        repository = metadata.find("{*}repository")
        if repository is None or repository.get("commit") != commit or repository.get("url") != REPOSITORY:
            raise ValueError("Package repository metadata mismatch")
        license_node = metadata.find("{*}license")
        if license_node is None or license_node.get("type") != "file" or license_node.text != "LICENSE.txt":
            raise ValueError("Missing file license declaration")
        source = json.loads(package.read("provenance/source.json"))
        if source["unoRepository"] != {"url": REPOSITORY, "commit": commit} or source["packageVersion"] != version:
            raise ValueError("Source metadata mismatch")
        if source["upstream"] != load_lock(root):
            raise ValueError("Upstream source lock mismatch")
        inventory_name = f"provenance/{package_id}.payloads.json"
        inventory = json.loads(package.read(inventory_name))
        if inventory["packageId"] != package_id or inventory["packageVersion"] != version:
            raise ValueError("Payload identity mismatch")
        hashes = inventory["sha256"]
        validate_native_entries(package_id, hashes)
        for name, digest in hashes.items():
            if sha(package.read(name)) != digest:
                raise ValueError(f"Payload hash mismatch: {name}")
        for name in names:
            # NuGet-generated OPC metadata and signature are covered by the external nupkg hash.
            if name in hashes or name in (inventory_name, nuspecs[0], "[Content_Types].xml", ".signature.p7s"):
                continue
            if name.startswith(("_rels/", "package/services/metadata/")) or name.endswith("/"):
                continue
            raise ValueError(f"Uninventoried package entry: {name}")
        icu_license = package.read("licenses/ICU-LICENSE.txt")
        uno_license = package.read("licenses/Uno-LICENSE.md")
        if sha(icu_license) != source["upstream"]["licenseSha256"]:
            raise ValueError("Incomplete upstream license")
        if uno_license != (root / "LICENSE.md").read_bytes():
            raise ValueError("Uno license mismatch")
        combined = package.read("LICENSE.txt")
        if icu_license not in combined or uno_license not in combined:
            raise ValueError("Combined license omits a complete license text")
        if package.read("NOTICE.md") != (root / "eng/NOTICE.md").read_bytes():
            raise ValueError("Scope notice mismatch")
        return {
            "id": package_id, "version": version, "file": path.name,
            "sha256": sha(path.read_bytes()), "payloadSha256": hashes,
            "signaturePresent": ".signature.p7s" in names,
            "entrySha256": {name: sha(package.read(name)) for name in names
                            if not name.endswith("/") and name != ".signature.p7s"},
        }


def compare_signing_payloads(packages, baseline):
    old = {item["id"]: item for item in baseline["packages"]}
    if set(old) != {item["id"] for item in packages}:
        raise ValueError("Signing changed the package set")
    for package in packages:
        before = old[package["id"]]
        if before["version"] != package["version"] or before["entrySha256"] != package["entrySha256"]:
            raise ValueError(f"Signing changed non-signature entries: {package['id']}")


def inventory(directory, version, commit, output, phase, baseline=None, root=ROOT):
    packages = [inspect_package(p, version, commit, root) for p in sorted(directory.glob("*.nupkg"))]
    if sorted(p["id"] for p in packages) != sorted(PACKAGE_IDS):
        raise ValueError("Expected exactly the five current-main source-built packages")
    if phase == "signed" and not all(p["signaturePresent"] for p in packages):
        raise ValueError("Signed inventory requires signatures (verify trust separately)")
    if phase == "signed":
        if baseline is None:
            raise ValueError("Signed inventory requires the unsigned build inventory")
        previous = json.loads(baseline.read_text(encoding="utf-8"))
        if previous["phase"] != "unsigned" or previous["unoCommit"] != commit or previous["packageVersion"] != version:
            raise ValueError("Unsigned inventory source/version mismatch")
        compare_signing_payloads(packages, previous)
    data_hashes = {p["payloadSha256"]["buildTransitive/icudt.dat"]
                   for p in packages if p["id"] not in APPLE_STATIC_IDS}
    if len(data_hashes) != 1:
        raise ValueError("Shared ICU data differs between packages")
    write_new(output, json_bytes({
        "schemaVersion": 1, "phase": phase, "unoCommit": commit,
        "packageVersion": version, "packages": packages,
        "signatureTrustVerifiedByThisScript": False,
    }))
    print(f"Verified {len(packages)} {phase} packages; inventory: {output}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    fetch = commands.add_parser("fetch-source")
    fetch.add_argument("--archive", type=Path, required=True)
    tool = commands.add_parser("fetch-tool")
    tool.add_argument("--path", type=Path, required=True)
    prep = commands.add_parser("prepare")
    prep.add_argument("--archive", type=Path, required=True)
    prep.add_argument("--version", required=True)
    check = commands.add_parser("inventory")
    check.add_argument("--directory", type=Path, required=True)
    check.add_argument("--version", required=True)
    check.add_argument("--commit", required=True)
    check.add_argument("--phase", choices=("unsigned", "signed"), required=True)
    check.add_argument("--output", type=Path, required=True)
    check.add_argument("--baseline", type=Path)
    args = parser.parse_args()
    if args.command == "fetch-source":
        fetch_source(args.archive)
    elif args.command == "fetch-tool":
        fetch_tool(args.path)
    elif args.command == "prepare":
        prepare(args.version, args.archive)
    else:
        inventory(args.directory, args.version, args.commit, args.output, args.phase, args.baseline)


if __name__ == "__main__":
    main()
