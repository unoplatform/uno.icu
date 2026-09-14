"""Full-five opt-in and Apple archive platform fixtures; no native compilation."""
import importlib
import itertools
import json
import shlex
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "eng"))
import build_only as b
import provenance as p
from test_dispatch_route import context, environment, REFS
from workflow_expression import Expression

ROOT = p.ROOT


def apple():
    return importlib.import_module("apple_icu")


def object_file(platform_id, arch="arm64", minimum=None):
    if minimum is None:
        minimum = 0x000E0000 if platform_id in (7, 8) and arch == "arm64" else 0x000D0400
    cpu, subtype = (0x100000C, 0) if arch == "arm64" else (0x1000007, 3)
    header = struct.pack("<IiiIIIII", 0xFEEDFACF, cpu, subtype, 1, 1, 24, 0, 0)
    return header + struct.pack("<IIIIII", 0x32, 24, platform_id, minimum, 0, 0)


def archive(data, name="fixture.o"):
    name_bytes = name.encode()
    payload = name_bytes + data
    header = (f"#1/{len(name_bytes)}".ljust(16) + "0".ljust(12) + "0".ljust(6) +
              "0".ljust(6) + "100644".ljust(8) + str(len(payload)).ljust(10) + "`\n").encode()
    return b"!<arch>\n" + header + payload + (b"\n" if len(payload) % 2 else b"")


def universal(platform_id, payloads=None):
    payloads = payloads or {arch: archive(object_file(platform_id, arch)) for arch in ("arm64", "x86_64")}
    slices = [(0x100000C, 0, payloads["arm64"]), (0x1000007, 3, payloads["x86_64"])]
    offset = 48
    descriptors, data = [], b""
    for cpu, subtype, payload in slices:
        descriptors.append(struct.pack(">IIIII", cpu, subtype, offset, len(payload), 0))
        data += payload
        offset += len(payload)
    return struct.pack(">II", 0xCAFEBABE, 2) + b"".join(descriptors) + data


def apple_fixture(directory, host_hash, lock, filter_hash):
    from test_apple_tool_binding import configuration_fixture
    a = apple()
    host_binary = bytearray(object_file(1))
    struct.pack_into("<I", host_binary, 12, 2)
    identity = a.macho_identity(bytes(host_binary), 1, "arm64", minimum=None, file_type=2)
    host_files = {name: p.sha(b"fixture host config") for name in a.HOST_CONFIGS}
    for name in a.HOST_CONFIGS:
        p.write_new(directory / "host-config" / name, b"fixture host config")
    tools = {}
    for name in a.HOST_TOOLS:
        host_files["bin/" + name] = identity["sha256"]
        tools["bin/" + name] = identity
    struct.pack_into("<I", host_binary, 12, 6)
    lib_identity = a.macho_identity(bytes(host_binary), 1, "arm64", minimum=None, file_type=6)
    libraries = {}
    for name in a.HOST_LIBRARIES:
        libraries[name] = lib_identity
        host_files[name] = lib_identity["sha256"]
    receipt = {
        "schemaVersion": 2, "hostArtifactSha256": host_hash, "hostSource": "/fixture/host/source",
        "hostIdentity": {"fileSha256": host_files, "executables": tools, "libraries": libraries,
                         "filterSha256": filter_hash, "dependencyBindings": a.host_dependency_bindings(tools, libraries)},
        "sdkIdentities": {}, "variants": [], "simulatorMerges": {},
        "minimumDeploymentByVariant": {row.name: "14.0" if row.name in ("iossim-arm64", "tvossim-arm64") else "13.4"
                                        for row in a.VARIANTS},
        "runtimeTested": False, "authenticatedAttestation": False,
    }
    for row in a.VARIANTS:
        receipt["sdkIdentities"][row.sdk] = {
            "path": "/fixture/" + row.sdk, "version": "fixture", "buildVersion": "fixture",
            "settingsSha256": "a" * 64,
            "tools": {name: {"path": "/fixture/" + name, "sha256": "b" * 64}
                      for name in ("clang", "clang++", "ar", "ranlib", "lipo")},
        }
        sdk = receipt["sdkIdentities"][row.sdk]
        config = directory / "variants" / row.name / "configuration"
        root_values, data_values = configuration_fixture(config, sdk, receipt["hostSource"], row)
        b.shutil.copytree(config, config.with_name("configuration-final"))
        for phase in ("before", "after"):
            p.write_new(config.parent / f"make-evaluated-{phase}.txt", root_values.encode())
            p.write_new(config.parent / f"data-make-evaluated-{phase}.txt", data_values.encode())
        tool_configuration = a.verify_variant_configuration(config, sdk, receipt["hostSource"], root_values, data_values, row)
        archives = {}
        for library in ("libicuuc.a", "libicudata.a"):
            data = archive(object_file(a.PLATFORMS[row.target], row.arch) + row.name.encode(), library + ".o")
            p.write_new(directory / "variants" / row.name / library, data)
            archives[library] = a.validate_archive(data, a.PLATFORMS[row.target], (row.arch,))
            if not row.target.endswith("sim"):
                p.write_new(directory / f"payload/nuget/uno.icu-{row.target}/{row.target}" / library, data)
        receipt["variants"].append({
            "name": row.name, "target": row.target, "architecture": row.arch, "platform": a.PLATFORMS[row.target],
            "minimumDeployment": receipt["minimumDeploymentByVariant"][row.name],
            "sdk": row.sdk, "recipe": a.configure_command(row, "/fixture/" + row.sdk, receipt["hostSource"]),
            "compilerEnvironment": a.compiler_environment(sdk), "toolConfiguration": tool_configuration,
            "sourceArchiveSha256": lock["archiveSha256"], "filterSha256": filter_hash, "archives": archives,
        })
    for target in ("iossim", "tvossim"):
        family = "ios" if target == "iossim" else "tvos"
        for library in ("libicuuc.a", "libicudata.a"):
            data = universal(a.PLATFORMS[target], {arch: (directory / "variants" / (target + "-" + arch) / library).read_bytes()
                                                  for arch in ("arm64", "x86_64")})
            name = f"nuget/uno.icu-{family}/{target}/{library}"
            p.write_new(directory / "payload" / name, data)
            receipt["simulatorMerges"][name] = a.validate_archive(data, a.PLATFORMS[target], ("arm64", "x86_64"))
    p.write_new(directory / "apple-build.json", p.json_bytes(receipt))
    return receipt


class FullFiveContracts(unittest.TestCase):
    def test_separate_main_operation_and_default_three_scope(self):
        main = yaml.load((ROOT / ".github/workflows/main.yml").read_text(), Loader=yaml.BaseLoader)
        child = yaml.load((ROOT / ".github/workflows/build-only.yml").read_text(), Loader=yaml.BaseLoader)
        self.assertIn("build-full-five", main["on"]["workflow_dispatch"]["inputs"]["operation"]["options"])
        self.assertEqual("false", main["on"]["workflow_dispatch"]["inputs"]["authorize_apple"]["default"])
        job = main["jobs"]["build_full_five"]
        self.assertEqual({"contents": "read"}, job["permissions"])
        self.assertNotIn("secrets", job)
        self.assertEqual("true", job["with"]["full_five"])
        for trigger in ("workflow_dispatch", "workflow_call"):
            self.assertEqual("false", child["on"][trigger]["inputs"]["full_five"]["default"])
            self.assertEqual("false", child["on"][trigger]["inputs"]["authorize_apple"]["default"])

    def test_full_scope_boolean_ref_event_table_and_no_release_reachability(self):
        main = yaml.load((ROOT / ".github/workflows/main.yml").read_text(), Loader=yaml.BaseLoader)
        jobs = main["jobs"]
        full = Expression(jobs.get("build_full_five", {}).get("if", "${{ false }}"))
        blocked = [Expression(jobs[name]["if"]) for name in
                   ("build_only", "raw_smoke", "release_authorization", "sign", "publish_dev", "publish_prod")]
        mismatches = []
        for native, release, grant, ref, repo, event, target in itertools.product(
                (True, False), (True, False), (True, False), REFS,
                ("unoplatform/uno.icu", "someone/uno.icu"), ("workflow_dispatch", "push", "pull_request"),
                ("all", "windows")):
            values = context("build-full-five", native, release, ref, repo, event)
            values.update({"inputs.authorize_apple": grant, "inputs.target": target})
            expected = (native and not release and grant and ref.startswith("refs/heads/") and
                        repo == "unoplatform/uno.icu" and event == "workflow_dispatch" and target == "all")
            if full.evaluate(values) != expected or any(guard.evaluate(values) for guard in blocked):
                mismatches.append(values)
            env = environment(values)
            env.update(AUTHORIZE_APPLE=json.dumps(grant), FULL_FIVE="true")
            try:
                b.authorize(env, "a" * 40, "")
                permitted = True
            except ValueError:
                permitted = False
            if permitted != expected:
                mismatches.append(("entry", values))
        self.assertEqual([], mismatches, repr(mismatches[:5]))

    def test_malformed_apple_grants_and_partial_full_scope_rejected(self):
        env = environment(context("build-full-five"))
        env.update(AUTHORIZE_APPLE="true", FULL_FIVE="true")
        b.authorize(env, "a" * 40, "")
        for key in ("AUTHORIZE_APPLE", "FULL_FIVE"):
            for value in ('"true"', '"false"', "", "null", "0", "1", "false"):
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    b.authorize({**env, key: value}, "a" * 40, "")
        for scope in ("build-only", "raw-smoke", "release-dev", "release-prod"):
            bad = environment(context(scope))
            bad.update(AUTHORIZE_APPLE="true", FULL_FIVE="true")
            with self.assertRaises(ValueError):
                b.authorize(bad, "a" * 40, "")

    def test_child_scope_truth_table_and_malformed_flags_stay_closed(self):
        child = yaml.load((ROOT / ".github/workflows/build-only.yml").read_text(), Loader=yaml.BaseLoader)
        guard = Expression(child["jobs"]["authorize"]["if"])
        for full, grant, native, release, target in itertools.product(
                (False, True, "true", "false", None, 0, 1),
                (False, True, "true", "false", None, 0, 1),
                (False, True), (False, True), ("all", "windows")):
            values = context(native=native, release=release)
            values.update({"inputs.full_five": full, "inputs.authorize_apple": grant, "inputs.target": target})
            expected = (native is True and release is False and
                        ((full is False and grant is False) or (full is True and grant is True and target == "all")))
            self.assertEqual(expected, guard.evaluate(values))

    def test_apple_grant_cannot_start_old_routes_or_any_release(self):
        main = yaml.load((ROOT / ".github/workflows/main.yml").read_text(), Loader=yaml.BaseLoader)
        guards = [Expression(main["jobs"][name]["if"]) for name in
                  ("build_only", "raw_smoke", "release_authorization", "sign", "publish_dev", "publish_prod")]
        for operation, native, release, grant, ref in itertools.product(
                ("build-only", "raw-smoke", "release-dev", "release-prod"),
                (False, True), (False, True), (True, "true", "", None, 0, 1), REFS):
            values = context(operation, native, release, ref)
            values["inputs.authorize_apple"] = grant
            self.assertFalse(any(guard.evaluate(values) for guard in guards))

    def test_exact_23_payloads_and_full_producer_set_without_weakening_pack(self):
        self.assertEqual(15, len(b.expected_payloads("staging")))
        self.assertEqual(23, len(b.expected_payloads("staging-five")))
        self.assertEqual(8, len(b.expected_payloads("apple")))
        self.assertEqual(15, len(b.matrix_rows()))
        self.assertEqual(16, len(b.matrix_rows(full_five=True)))
        self.assertEqual(5, len(p.PACKAGE_IDS))
        self.assertIn("Uno.icu-tvos", p.PACKAGE_IDS)

    def test_valid_device_and_universal_simulator_archive_platforms(self):
        a = apple()
        for platform_id in (2, 3):
            result = a.validate_archive(archive(object_file(platform_id)), platform_id, ("arm64",))
            self.assertEqual(["arm64"], result["architectures"])
        for platform_id in (7, 8):
            result = a.validate_archive(universal(platform_id), platform_id, ("arm64", "x86_64"))
            self.assertEqual({"arm64", "x86_64"}, set(result["architectures"]))

    def test_same_arm64_cpu_cannot_substitute_ios_tvos_or_simulator(self):
        a = apple()
        for actual, expected in ((2, 3), (3, 2), (2, 7), (7, 2), (3, 8), (8, 3), (7, 8)):
            with self.subTest(actual=actual, expected=expected), self.assertRaises(ValueError):
                a.validate_archive(archive(object_file(actual)), expected, ("arm64",))

    def test_missing_or_wrong_load_command_and_deployment_are_rejected(self):
        a = apple()
        no_platform = struct.pack("<IiiIIIII", 0xFEEDFACF, 0x100000C, 0, 1, 0, 0, 0, 0)
        legacy = struct.pack("<IiiIIIII", 0xFEEDFACF, 0x100000C, 0, 1, 1, 16, 0, 0)
        legacy += struct.pack("<IIII", 0x25, 16, 0x000D0400, 0)
        for obj in (no_platform, legacy, object_file(2, minimum=0x000E0000)):
            with self.assertRaises(ValueError):
                a.validate_archive(archive(obj), 2, ("arm64",))

    def test_archive_bounds_unknown_members_and_missing_slice_fail_closed(self):
        a = apple()
        for data in (b"!<arch>\n", b"!<thin>\n", archive(b"not Mach-O"),
                     archive(object_file(2))[:-5], universal(7)[:-8]):
            with self.assertRaises(ValueError):
                a.validate_archive(data, 7, ("arm64", "x86_64"))
        with self.assertRaises(ValueError):
            a.validate_archive(archive(object_file(7)), 7, ("arm64", "x86_64"))

    def test_overlapping_or_duplicate_fat_architectures_are_rejected(self):
        for duplicate in (True, False):
            data = bytearray(universal(7))
            if duplicate:
                struct.pack_into(">II", data, 8 + 20, 0x100000C, 0)
            else:
                struct.pack_into(">I", data, 8 + 20 + 8, 48)
            with self.assertRaises(ValueError):
                apple().validate_archive(bytes(data), 7, ("arm64", "x86_64"))

    def test_all_object_members_must_match_not_only_first_member(self):
        a = apple()
        mixed = archive(object_file(2)) + archive(object_file(3), "other.o")[8:]
        with self.assertRaises(ValueError):
            a.validate_archive(mixed, 2, ("arm64",))

    def test_six_isolated_recipes_preserve_sdk_and_minimum_flags(self):
        a = apple()
        self.assertEqual(6, len(a.VARIANTS))
        self.assertEqual({"iphoneos", "iphonesimulator", "appletvos", "appletvsimulator"},
                         {row.sdk for row in a.VARIANTS})
        for row in a.VARIANTS:
            sdk, host = Path(r"Q:\owned\sdk"), Path(r"Q:\owned\host\source")
            command = a.configure_command(row, sdk, host)
            self.assertIn("--with-data-packaging=static", command)
            self.assertIn("--disable-shared", command)
            self.assertIn("--with-cross-build=" + str(host), command)
            minimum = "14.0" if row.name in ("iossim-arm64", "tvossim-arm64") else "13.4"
            self.assertTrue(any(row.minimum_flag + "=" + minimum in value for value in command))
            self.assertIn(f"CFLAGS=-arch {row.arch} -isysroot {shlex.quote(str(sdk))} {row.minimum_flag}={minimum}", command)

    def test_missing_host_build_tree_or_tool_prevents_cross_build(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError, "host"):
                apple().host_identity(Path(temp))

    def test_full_receipt_checks_every_variant_and_exact_simulator_slices(self):
        a = apple()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            receipt = apple_fixture(root, "a" * 64, p.load_lock(), "c" * 64)
            a.verify_receipt(root, "a" * 64, p.load_lock(), "c" * 64)
            with self.assertRaises(ValueError):
                a.verify_receipt(root, "b" * 64, p.load_lock(), "c" * 64)
            # Correct platform/CPU but different object bytes are not the built inputs.
            file = root / "payload/nuget/uno.icu-ios/iossim/libicuuc.a"
            file.write_bytes(universal(7))
            receipt["simulatorMerges"]["nuget/uno.icu-ios/iossim/libicuuc.a"] = a.validate_archive(
                file.read_bytes(), 7, ("arm64", "x86_64"))
            (root / "apple-build.json").write_bytes(p.json_bytes(receipt))
            with self.assertRaisesRegex(ValueError, "exact built"):
                a.verify_receipt(root, "a" * 64, p.load_lock(), "c" * 64)

    def test_full_receipt_rejects_missing_thin_archive_and_variant(self):
        a = apple()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            receipt = apple_fixture(root, "a" * 64, p.load_lock(), "c" * 64)
            original = (root / "apple-build.json").read_bytes()
            receipt["variants"].pop()
            (root / "apple-build.json").write_bytes(p.json_bytes(receipt))
            with self.assertRaisesRegex(ValueError, "variants"):
                a.verify_receipt(root, "a" * 64, p.load_lock(), "c" * 64)
            (root / "apple-build.json").write_bytes(original)
            file = root / "variants/ios-arm64/libicudata.a"
            file.unlink()
            with self.assertRaises((ValueError, FileNotFoundError)):
                a.verify_receipt(root, "a" * 64, p.load_lock(), "c" * 64)

    def test_host_identity_requires_real_format_tools_and_rejects_missing_one(self):
        a = apple()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in a.HOST_CONFIGS:
                p.write_new(root / name, b"fixture host config")
            obj = bytearray(object_file(1))
            struct.pack_into("<I", obj, 12, 2)
            for name in a.HOST_TOOLS:
                p.write_new(root / "bin" / name, obj)
            struct.pack_into("<I", obj, 12, 6)
            for name in a.HOST_LIBRARIES:
                p.write_new(root / name, obj)
            with patch.object(a.os, "access", return_value=True):
                self.assertEqual(8, len(a.host_identity(root)["executables"]))
                (root / "bin/pkgdata").unlink()
                with self.assertRaisesRegex(ValueError, "host"):
                    a.host_identity(root)

    def test_full_assembly_requires_16_same_run_inputs_and_retains_23_payloads(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = root / "repo"
            p.write_new(repo / "eng/build-images.json", b"{}")
            filter_bytes = b'{"fixture":"not runtime data"}'
            p.write_new(repo / "src/cldr_data/filters.json", filter_bytes)
            names = ("eng/build-images.json", "src/cldr_data/filters.json")
            hashes = {name: p.sha((repo / name).read_bytes()) for name in names}
            source_archive = repo / "artifacts/icu-source.zip"
            source_archive.parent.mkdir()
            license_bytes = b"fixture full license"
            lock = dict(p.load_lock())
            with zipfile.ZipFile(source_archive, "w") as source:
                source.writestr(f'icu-{lock["commit"]}/LICENSE', license_bytes)
            lock.update(archiveSha256=p.sha(source_archive.read_bytes()), licenseSha256=p.sha(license_bytes))
            canonical = {"LICENSE.md": b"fixture Uno license", "eng/NOTICE.md": b"fixture scopes"}
            incoming = root / "incoming"
            incoming.mkdir()
            env = environment(context("build-full-five"))
            env.update(AUTHORIZE_APPLE="true", FULL_FIVE="true", GITHUB_RUN_ID="fixture", GITHUB_RUN_ATTEMPT="1")
            with (patch.object(b, "ROOT", repo), patch.object(b, "OUT", root / "out"),
                  patch.object(p, "load_lock", return_value=lock),
                  patch.object(p, "git", side_effect=lambda *args: (
                      "a" * 40 if args[0] == "rev-parse" else "" if args[0] == "status" else "\n".join(names))),
                  patch.object(b, "repository_bytes", side_effect=lambda name: canonical[name]),
                  patch.object(b, "source_inputs", return_value=hashes),
                  patch.dict(b.os.environ, env, clear=True)):
                rows = sorted(b.matrix_rows(full_five=True),
                              key=lambda row: 2 if row[0] == "apple" else 1 if row[0] == "macos-universal" else 0)
                manifests = {}
                for kind, variant, version in rows:
                    row = b.Build(kind, variant, version)
                    row.source()
                    parents = {}
                    if kind == "source":
                        b.copy_new(source_archive, row.directory / "icu-source.zip")
                    if kind == "apple":
                        parents = {"build-only-macos-arm64": manifests["build-only-macos-arm64"]}
                        apple_fixture(row.directory, parents["build-only-macos-arm64"], lock, p.sha(filter_bytes))
                    else:
                        if kind == "macos-universal":
                            parents = {name: manifests[name] for name in
                                       ("build-only-macos-arm64", "build-only-macos-x86_64")}
                        for name in b.expected_payloads(kind, variant, version):
                            if name.endswith(".a"):
                                data = b"!<arch>\nfixture WASM only"
                            elif name.endswith(".dat"):
                                data = bytearray(24)
                                data[2:4], data[12:16] = b"\xda\x27", b"CmnD"
                            elif name.endswith(".dll"):
                                data = bytearray(128)
                                data[:2], data[64:68] = b"MZ", b"PE\0\0"
                                struct.pack_into("<I", data, 0x3c, 64)
                                struct.pack_into("<H", data, 68, 0xAA64 if variant == "arm64" else 0x8664)
                            else:
                                data = b"\xcf\xfa\xed\xfe" + bytes(40)
                            p.write_new(row.directory / "payload" / name, data)
                    row.finish(parents)
                    name = "build-only-" + row.directory.name
                    b.shutil.copytree(row.directory, incoming / name)
                    manifests[name] = p.sha((row.directory / "artifact.json").read_bytes())
                b.assemble(incoming, full_five=True)
                result = root / "out/staging-five"
                b.verify_bundle(result, "staging-five")
                self.assertEqual(23, len(b.file_hashes(result / "payload")))
                self.assertEqual(16, len(json.loads((result / "artifact.json").read_text())["inputArtifacts"]))
                apple_manifest_path = incoming / "build-only-apple/artifact.json"
                original = apple_manifest_path.read_bytes()
                substituted = json.loads(original)
                substituted["inputArtifacts"]["build-only-macos-arm64"] = "f" * 64
                apple_manifest_path.write_bytes(p.json_bytes(substituted))
                with patch.object(b, "OUT", root / "substituted-out"), self.assertRaisesRegex(ValueError, "ARM64 host"):
                    b.assemble(incoming, full_five=True)
                apple_manifest_path.write_bytes(original)
                # A new assembly cannot accept the old 15-row subset.
                partial = root / "partial"
                partial.mkdir()
                for item in incoming.iterdir():
                    if item.name != "build-only-apple":
                        b.shutil.copytree(item, partial / item.name)
                with patch.object(b, "OUT", root / "partial-out"), self.assertRaisesRegex(ValueError, "matrix"):
                    b.assemble(partial, full_five=True)


if __name__ == "__main__":
    unittest.main()
