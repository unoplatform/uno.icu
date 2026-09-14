"""Exact deployment policy and retained failure metadata; no compiler or native loading."""
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest

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
