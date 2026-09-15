"""Exact deployment policy and retained failure metadata; no compiler or native loading."""
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "eng"))
import apple_icu as a
import build_only as b
import provenance as p
from test_apple_tool_binding import configuration_fixture, sdk_fixture
from test_full_five import apple_fixture, archive, object_file, universal

EXPECTED = {
    "ios-arm64": "13.4", "iossim-arm64": "14.0", "iossim-x86_64": "13.4",
    "tvos-arm64": "13.4", "tvossim-arm64": "14.0", "tvossim-x86_64": "13.4",
}


class AppleDeployment(unittest.TestCase):
    def test_all_six_recipes_request_the_exact_supported_minimum(self):
        for variant in a.VARIANTS:
            minimum = EXPECTED[variant.name]
            command = a.configure_command(variant, "/sdk", "/host")
            for prefix in ("CFLAGS=", "CXXFLAGS="):
                value = next(arg for arg in command if arg.startswith(prefix))
                with self.subTest(variant=variant.name, prefix=prefix):
                    self.assertIn(variant.minimum_flag + "=" + minimum, value)
                    self.assertIn("-arch " + variant.arch, value)

    def test_simulator_archives_require_14_arm64_and_13_4_intel_slices(self):
        for platform in (7, 8):
            slices = {
                "arm64": archive(object_file(platform, "arm64", 0x000E0000)),
                "x86_64": archive(object_file(platform, "x86_64", 0x000D0400)),
            }
            result = a.validate_archive(universal(platform, slices), platform, ("arm64", "x86_64"))
            self.assertEqual({"arm64": "14.0", "x86_64": "13.4"}, result["minimumDeploymentByArchitecture"])
            self.assertEqual({arch: p.sha(data) for arch, data in slices.items()}, result["sliceSha256"])

    def test_higher_lower_and_wrong_platform_versions_remain_rejected(self):
        for variant in a.VARIANTS:
            expected = 0x000E0000 if EXPECTED[variant.name] == "14.0" else 0x000D0400
            for minimum in (0x000D0000, 0x000D0400, 0x000E0000, 0x000F0000):
                if minimum == expected:
                    continue
                with self.subTest(variant=variant.name, minimum=minimum), self.assertRaises(ValueError):
                    a.validate_archive(archive(object_file(a.PLATFORMS[variant.target], variant.arch, minimum)),
                                       a.PLATFORMS[variant.target], (variant.arch,))
            with self.assertRaises(ValueError):
                a.validate_archive(archive(object_file(1, variant.arch, expected)),
                                   a.PLATFORMS[variant.target], (variant.arch,))

    def test_observed_device_load_commands_keep_exact_fields_and_assembly_sdk_zero(self):
        observations = json.loads((p.ROOT / "tests/fixtures/apple-deployment-observations.json").read_text())
        for row in observations["controls"]:
            command = bytes.fromhex(row["buildVersionHex"])
            header = struct.pack("<IiiIIIII", 0xFEEDFACF, row["cpu"], row["cpuSubtype"],
                                 row["fileType"], 1, len(command), 0, 0)
            result = a.validate_archive(archive(header + command, row["member"]), 2, ("arm64",))
            member = result["members"]["arm64"][0]
            self.assertEqual(row["minimum"], member["minimumVersion"])
            self.assertEqual(row["sdk"], member["sdkVersionInObject"])
            self.assertEqual({"arm64": "13.4"}, result["minimumDeploymentByArchitecture"])
            with self.assertRaises(ValueError) as failure:
                a.validate_archive(archive(header + command, row["member"]), 7, ("arm64",))
            self.assertIn(row["member"], str(failure.exception))
            self.assertIn("platform=2", str(failure.exception))
            self.assertIn("minimum=13.4", str(failure.exception))
            self.assertIn("sdk=", str(failure.exception))

    def test_actual_generated_c_cxx_and_pkgdata_flags_are_reconciled(self):
        for variant in a.VARIANTS:
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                outputs = configuration_fixture(root, sdk_fixture(), variant=variant)
                result = a.verify_variant_configuration(root, sdk_fixture(), "/host/source", *outputs, variant)
                self.assertEqual(EXPECTED[variant.name], result["target"]["minimumDeployment"])
                self.assertEqual(a.PLATFORMS[variant.target], result["target"]["platform"])

    def test_generated_flag_substitution_in_any_compile_surface_fails(self):
        variant = next(row for row in a.VARIANTS if row.name == "iossim-arm64")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            outputs = configuration_fixture(root, sdk_fixture(), variant=variant)
            for file in ("icudefs.mk", "data/icupkg.inc"):
                path = root / file
                original = path.read_bytes()
                for before, after in ((b"-arch arm64", b"-arch x86_64"),
                                      (b"-isysroot /fixture/sdk", b"-isysroot /wrong/sdk"),
                                      (b"-mios-simulator-version-min=14.0", b"-mios-version-min=14.0"),
                                      (b"-mios-simulator-version-min=14.0", b"-mios-simulator-version-min=13.4"),
                                      (b"-arch arm64", b"-arch arm64 --target=arm64-apple-macos14")):
                    self.assertIn(before, original)
                    path.write_bytes(original.replace(before, after))
                    with self.subTest(file=file, after=after), self.assertRaises(ValueError):
                        a.verify_variant_configuration(root, sdk_fixture(), "/host/source", *outputs, variant)
                    path.write_bytes(original)
            path = root / "icudefs.mk"
            original = path.read_text()
            for variable in ("CFLAGS", "CXXFLAGS"):
                path.write_text("\n".join(
                    line.replace("version-min=14.0", "version-min=15.0") if line.startswith(variable + " = ") else line
                    for line in original.splitlines()) + "\n")
                with self.subTest(variable=variable), self.assertRaises(ValueError):
                    a.verify_variant_configuration(root, sdk_fixture(), "/host/source", *outputs, variant)
                path.write_text(original)
                for index in (0, 1):
                    bad = list(outputs)
                    bad[index] = "\n".join(
                        line.replace("version-min=14.0", "version-min=15.0")
                        if line.startswith("UNO_ICU_TOOL_" + variable + "=") else line
                        for line in bad[index].splitlines())
                    with self.subTest(variable=variable, scope=index), self.assertRaises(ValueError):
                        a.verify_variant_configuration(root, sdk_fixture(), "/host/source", *bad, variant)

    def test_both_raw_archives_are_retained_before_first_member_rejection(self):
        variant = next(row for row in a.VARIANTS if row.name == "iossim-arm64")
        for rejected in ("libicuuc.a", "libicudata.a"):
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                source, directory = root / "cross", root / "retained"
                archives = {name: archive(object_file(1 if name == rejected else 7, "arm64", 0x000E0000),
                                          "offending.ao" if name == rejected else "valid.o")
                            for name in ("libicuuc.a", "libicudata.a")}
                for name, data in archives.items():
                    p.write_new(source / "lib" / name, data)
                with self.subTest(rejected=rejected), self.assertRaises(ValueError):
                    b.retain_apple_archives(source, directory, variant)
                for name, data in archives.items():
                    self.assertEqual(data, (directory / name).read_bytes())
                failure = json.loads((directory / "archive-validation-failure.json").read_text())
                self.assertEqual("iossim-arm64", failure["variant"])
                self.assertEqual(rejected, failure["archive"])
                self.assertEqual(p.sha(archives[rejected]), failure["archiveSha256"])
                self.assertIn("offending.ao", failure["error"])
                self.assertFalse((directory / "artifact.json").exists())
                self.assertFalse((directory / "payload").exists())

    def test_empty_common_or_data_archive_is_retained_before_validation(self):
        variant = next(row for row in a.VARIANTS if row.name == "iossim-arm64")
        libraries = ("libicuuc.a", "libicudata.a")
        empty_sha = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        for empty in ((libraries[0],), (libraries[1],), libraries):
            with tempfile.TemporaryDirectory() as temp:
                source, directory = Path(temp) / "cross", Path(temp) / "retained"
                contents = {name: b"" if name in empty else archive(object_file(7, "arm64", 0x000E0000))
                            for name in libraries}
                for name, data in contents.items():
                    p.write_new(source / "lib" / name, data)
                with self.subTest(empty=empty), self.assertRaises(ValueError):
                    b.retain_apple_archives(source, directory, variant)
                for name, data in contents.items():
                    self.assertEqual(data, (directory / name).read_bytes())
                    self.assertEqual(data, (source / "lib" / name).read_bytes())
                failure = json.loads((directory / "archive-validation-failure.json").read_text())
                self.assertEqual(next(name for name in libraries if name in empty), failure["archive"])
                self.assertEqual(empty_sha, failure["archiveSha256"])
                self.assertFalse(failure["runtimeTested"])
                self.assertFalse((directory / "payload").exists())
                self.assertFalse((directory / "artifact.json").exists())
                self.assertFalse((directory / "archive-retention-failure.json").exists())

    def test_truncated_archive_bytes_are_retained_without_repair(self):
        variant = a.VARIANTS[0]
        with tempfile.TemporaryDirectory() as temp:
            source, directory = Path(temp) / "cross", Path(temp) / "retained"
            partial = b"!<arch>\npartial-header"
            other = archive(object_file(2))
            p.write_new(source / "lib/libicuuc.a", partial)
            p.write_new(source / "lib/libicudata.a", other)
            with self.assertRaises(ValueError):
                b.retain_apple_archives(source, directory, variant)
            self.assertEqual(partial, (directory / "libicuuc.a").read_bytes())
            self.assertEqual(other, (directory / "libicudata.a").read_bytes())
            failure = json.loads((directory / "archive-validation-failure.json").read_text())
            self.assertEqual(p.sha(partial), failure["archiveSha256"])
            self.assertIn("Truncated", failure["error"])
            self.assertFalse((directory / "artifact.json").exists())

    def test_missing_archives_retain_available_sibling_and_report_no_fabricated_bytes(self):
        libraries = ("libicuuc.a", "libicudata.a")
        for missing in ((libraries[0],), (libraries[1],), libraries):
            with tempfile.TemporaryDirectory() as temp:
                source, directory = Path(temp) / "cross", Path(temp) / "retained"
                for name in libraries:
                    if name not in missing:
                        p.write_new(source / "lib" / name, b"")
                with self.subTest(missing=missing), self.assertRaises(OSError):
                    b.retain_apple_archives(source, directory, a.VARIANTS[0])
                failure = json.loads((directory / "archive-retention-failure.json").read_text())
                self.assertEqual(set(missing), {item["archive"] for item in failure["errors"]})
                for item in failure["errors"]:
                    self.assertEqual("FileNotFoundError", item["errorType"])
                    self.assertNotIn("archiveSha256", item)
                    self.assertTrue(item["error"])
                self.assertEqual(set(libraries) - set(missing), set(failure["retainedArchives"]))
                for name in libraries:
                    if name in missing:
                        self.assertFalse((directory / name).exists())
                    else:
                        self.assertEqual(b"", (directory / name).read_bytes())
                        self.assertEqual({"bytes": 0, "sha256": p.sha(b"")}, failure["retainedArchives"][name])
                self.assertFalse((directory / "payload").exists())
                self.assertFalse((directory / "artifact.json").exists())
                self.assertFalse((directory / "archive-validation-failure.json").exists())

    def test_retention_never_overwrites_an_existing_destination(self):
        with tempfile.TemporaryDirectory() as temp:
            source, directory = Path(temp) / "cross", Path(temp) / "retained"
            p.write_new(source / "lib/libicuuc.a", b"new common")
            p.write_new(source / "lib/libicudata.a", b"new data")
            p.write_new(directory / "libicuuc.a", b"previous evidence")
            with self.assertRaises(OSError):
                b.retain_apple_archives(source, directory, a.VARIANTS[0])
            self.assertEqual(b"previous evidence", (directory / "libicuuc.a").read_bytes())
            self.assertEqual(b"new data", (directory / "libicudata.a").read_bytes())
            failure = json.loads((directory / "archive-retention-failure.json").read_text())
            self.assertEqual("FileExistsError", failure["errors"][0]["errorType"])
            self.assertEqual({"libicudata.a"}, set(failure["retainedArchives"]))
            self.assertFalse((directory / "artifact.json").exists())

    def test_partial_write_failure_keeps_bytes_and_retains_sibling_without_success(self):
        with tempfile.TemporaryDirectory() as temp:
            source, directory = Path(temp) / "cross", Path(temp) / "retained"
            p.write_new(source / "lib/libicuuc.a", b"complete common")
            p.write_new(source / "lib/libicudata.a", b"complete data")
            write_new = p.write_new

            def fail_common_write(path, data):
                if path == directory / "libicuuc.a":
                    write_new(path, data[:4])
                    raise OSError("fixture interrupted write")
                write_new(path, data)

            with patch.object(p, "write_new", side_effect=fail_common_write), self.assertRaises(OSError):
                b.retain_apple_archives(source, directory, a.VARIANTS[0])
            self.assertEqual(b"comp", (directory / "libicuuc.a").read_bytes())
            self.assertEqual(b"complete data", (directory / "libicudata.a").read_bytes())
            failure = json.loads((directory / "archive-retention-failure.json").read_text())
            self.assertEqual("fixture interrupted write", failure["errors"][0]["error"])
            self.assertEqual({"libicudata.a"}, set(failure["retainedArchives"]))
            self.assertFalse((directory / "archive-validation-failure.json").exists())
            self.assertFalse((directory / "artifact.json").exists())
            self.assertFalse((directory / "payload").exists())

    def test_empty_raw_retention_does_not_relax_normal_payload_copy(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "empty.a"
            p.write_new(source, b"")
            with self.assertRaisesRegex(ValueError, "Missing/empty"):
                b.copy_new(source, root / "ordinary-copy.a")
            self.assertFalse((root / "ordinary-copy.a").exists())
            with patch.object(b, "OUT", root / "build"):
                build = b.Build("apple")
                with self.assertRaisesRegex(ValueError, "Missing/empty"):
                    build.payload(source, "nuget/uno.icu-ios/ios/libicuuc.a")
                self.assertFalse((build.directory / "payload").exists())
                self.assertFalse((build.directory / "artifact.json").exists())

    def test_receipt_rejects_substituted_per_variant_support_minimum(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            receipt = apple_fixture(root, "a" * 64, p.load_lock(), "c" * 64)
            self.assertEqual(EXPECTED, receipt["minimumDeploymentByVariant"])
            receipt["minimumDeploymentByVariant"]["iossim-arm64"] = "13.4"
            (root / "apple-build.json").write_bytes(p.json_bytes(receipt))
            with self.assertRaises(ValueError):
                a.verify_receipt(root, "a" * 64, p.load_lock(), "c" * 64)


if __name__ == "__main__":
    unittest.main()
