"""Windows discovery fixtures; documented image data is not a live install claim."""
import importlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "eng"))


def helper():
    return importlib.import_module("windows_toolchain")


def instance(version="17.14.37614.0", components=("x86.x64", "ARM64")):
    return {
        "instanceId": "documented-image-fixture",
        "installationVersion": version,
        "installationPath": r"C:\Program Files\Microsoft Visual Studio\2022\Enterprise",
        "productId": "Microsoft.VisualStudio.Product.Enterprise",
        "isComplete": True, "isLaunchable": True, "isPrerelease": False,
        "packages": [{"id": "Microsoft.VisualStudio.Component.VC.Tools." + arch,
                      "type": "Component", "version": "17.14.36510.44"} for arch in components],
        "properties": {"unrelatedDiagnostic": "must-not-be-retained"},
    }


class WindowsPrerequisites(unittest.TestCase):
    def test_build_only_uses_documented_vs2022_image_without_changing_release_matrix(self):
        build = yaml.load((ROOT / ".github/workflows/build-only.yml").read_text(), Loader=yaml.BaseLoader)
        main = yaml.load((ROOT / ".github/workflows/main.yml").read_text(), Loader=yaml.BaseLoader)
        self.assertEqual("windows-2022", build["jobs"]["windows"]["runs-on"])
        self.assertEqual(["x64", "arm64"], build["jobs"]["windows"]["strategy"]["matrix"]["arch"])
        self.assertEqual("windows-2025", main["jobs"]["build_libicu_windows"]["runs-on"])

    def test_requirements_are_target_specific_not_x64_and_arm64_for_every_row(self):
        w = helper()
        self.assertEqual(["Microsoft.VisualStudio.Component.VC.Tools.x86.x64"], w.requirements("x64"))
        self.assertEqual(["Microsoft.VisualStudio.Component.VC.Tools.ARM64"], w.requirements("arm64"))
        with self.assertRaises(ValueError):
            w.requirements("x86")

    def test_documented_vs2022_version_is_selected_and_actual_vs2026_is_rejected(self):
        w = helper()
        good = w.sanitize_inventory([instance()])
        self.assertEqual("17.14.37614.0", w.select_instance(good, "x64")["installationVersion"])
        self.assertEqual("17.14.37614.0", w.select_instance(good, "arm64")["installationVersion"])
        with self.assertRaisesRegex(ValueError, "Visual Studio 2022"):
            w.select_instance(w.sanitize_inventory([instance("18.9.12120.119")]), "x64")
        with self.assertRaisesRegex(ValueError, "No matching"):
            w.select_instance([], "x64")

    def test_x64_only_installation_does_not_hide_missing_arm64(self):
        w = helper()
        inventory = w.sanitize_inventory([instance(components=("x86.x64",))])
        self.assertEqual("documented-image-fixture", w.select_instance(inventory, "x64")["instanceId"])
        with self.assertRaisesRegex(ValueError, "ARM64"):
            w.select_instance(inventory, "arm64")

    def test_incomplete_preview_ambiguous_and_malformed_instances_fail_closed(self):
        w = helper()
        for field, value in (("isComplete", False), ("isLaunchable", False), ("isPrerelease", True),
                             ("installationVersion", "not-a-version"), ("installationPath", "")):
            with self.subTest(field=field), self.assertRaises(ValueError):
                w.select_instance(w.sanitize_inventory([{**instance(), field: value}]), "x64")
        with self.assertRaises(ValueError):
            w.select_instance(w.sanitize_inventory([instance(), instance()]), "x64")

    def test_unfiltered_inventory_keeps_component_names_not_arbitrary_properties(self):
        result = helper().sanitize_inventory([instance()])
        self.assertNotIn("properties", result[0])
        self.assertEqual(2, len(result[0]["components"]))
        self.assertNotIn("must-not-be-retained", json.dumps(result))

    def test_empty_filtered_result_still_preserves_unfiltered_diagnostics(self):
        w = helper()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            tool = root / "vswhere.exe"
            tool.write_bytes(b"fixture identity; never executed")
            replies = [
                subprocess.CompletedProcess([], 0, json.dumps([instance("18.9.12120.119")]), ""),
                subprocess.CompletedProcess([], 0, "[]", ""),
            ]
            with patch.object(w.subprocess, "run", side_effect=replies) as run:
                with self.assertRaisesRegex(ValueError, "No matching"):
                    w.discover(tool, "x64", root / "evidence")
            commands = [call.args[0] for call in run.call_args_list]
            self.assertIn("-all", commands[0])
            self.assertIn("-prerelease", commands[0])
            self.assertNotIn("-version", commands[0])
            self.assertNotIn("-requires", commands[0])
            self.assertIn("[17.0,18.0)", commands[1])
            self.assertNotIn("-prerelease", commands[1])
            self.assertNotIn("Microsoft.VisualStudio.Component.VC.Tools.ARM64", commands[1])
            saved = json.loads((root / "evidence/unfiltered-inventory.json").read_text())
            self.assertEqual("18.9.12120.119", saved[0]["installationVersion"])
            self.assertFalse((root / "evidence/selected.json").exists())

    def test_matching_filtered_cli_result_is_recorded_without_installing(self):
        w = helper()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            tool = root / "vswhere.exe"
            tool.write_bytes(b"fixture identity; never executed")
            reply = subprocess.CompletedProcess([], 0, json.dumps([instance()]), "")
            with patch.object(w.subprocess, "run", return_value=reply):
                selected = w.discover(tool, "arm64", root / "evidence")
            self.assertEqual("arm64", selected["targetArchitecture"])
            self.assertEqual("v143", selected["platformToolset"])
            self.assertEqual("17.14.37614.0", selected["installation"]["installationVersion"])
            self.assertTrue((root / "evidence/selected.json").is_file())

    def test_sdk_identity_hashes_selected_architecture_files_and_rejects_missing_files(self):
        w = helper()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for arch in ("x64", "arm64"):
                for name in w.sdk_identity_files(arch):
                    file = root / name
                    file.parent.mkdir(parents=True, exist_ok=True)
                    file.write_bytes(("fixture " + name).encode())
                result = w.sdk_identity(root, arch)
                self.assertEqual(set(w.sdk_identity_files(arch)), set(result["sha256"]))
                self.assertIn(f"Lib/10.0.26100.0/um/{arch}/kernel32.lib", result["sha256"])
            (root / w.sdk_identity_files("arm64")[-1]).unlink()
            with self.assertRaises(ValueError):
                w.sdk_identity(root, "arm64")


if __name__ == "__main__":
    unittest.main()
