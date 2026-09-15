"""Current-main preservation contracts, separate from the historical 77.2 branch."""
from pathlib import Path
import hashlib
import json
import sys
import unittest
import xml.etree.ElementTree as ET

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "eng"))
import build_only as b
import provenance as p


class CurrentMain(unittest.TestCase):
    def test_current_version_is_not_replaced_by_historical_line(self):
        p.validate_version("77.4.0-dev.1")
        p.validate_version("77.4.1")
        with self.assertRaises(ValueError):
            p.validate_version("77.2.2-provenance.1")

    def test_current_package_set_includes_tvos(self):
        self.assertEqual({"Uno.icu-win", "Uno.icu-wasm", "Uno.icu-macos",
                          "Uno.icu-ios", "Uno.icu-tvos"}, set(p.PACKAGE_IDS))

    def test_current_wasm_payloads_include_both_toolchains(self):
        paths = b.expected_payloads("staging")
        expected = {f"nuget/uno.icu-wasm/buildTransitive/native/unoicu.a/{version}/{variant}/unoicu.a"
                    for version in ("3.1.56", "5.0.6") for variant in ("st", "mt", "st,simd", "mt,simd")}
        self.assertEqual(expected, {name for name in paths if name.endswith("/unoicu.a")})

    def test_current_nbgv_and_tag_sdk_preserved(self):
        self.assertEqual("77.4-dev.{height}", json.loads((p.ROOT / "version.json").read_text())["version"])
        tag = yaml.load((p.ROOT / ".github/workflows/actions/tag-release/action.yml").read_text(), Loader=yaml.BaseLoader)
        sdk = next(step for step in tag["runs"]["steps"] if step.get("uses", "").startswith("actions/setup-dotnet@"))
        self.assertEqual("10.0.x", sdk["with"]["dotnet-version"])

    def test_release_keeps_tvos_jobs_artifacts_and_summary(self):
        text = (p.ROOT / ".github/workflows/main.yml").read_text()
        jobs = yaml.load(text, Loader=yaml.BaseLoader)["jobs"]
        self.assertEqual(["3.1.56", "5.0.6"], jobs["build_unoicu"]["strategy"]["matrix"]["EMSCRIPTEN_VERSION"])
        for name in ("build_libicu_tvos", "build_libicu_tvossim_universal"):
            self.assertIn(name, jobs["package"]["needs"])
        self.assertEqual(["arm64", "x86_64"], jobs["build_libicu_tvossim"]["strategy"]["matrix"]["arch"])
        for name, sdk in (("build_libicu_tvos", "AppleTVOS"), ("build_libicu_tvossim", "AppleTVSimulator")):
            script = next(step["run"] for step in jobs[name]["steps"] if step.get("name") == "Build ICU")
            self.assertIn("python eng/provenance.py fetch-source --archive icu.zip", script)
            self.assertIn(sdk + ".platform", script)
            self.assertIn("--with-data-packaging=static", script)
            self.assertEqual("build_libicu_osx", jobs[name]["needs"])
        self.assertIn("libicu_tvossim_universal,libicu_tvos", text)
        self.assertIn("Add NuGet Summary", text)
        self.assertIn("'Package version: `${{ steps.nbgv.outputs.SemVer2 }}`'", text)
        self.assertIn("'tvos'", (p.ROOT / "eng/Pack.ps1").read_text())

    def test_all_current_nuspecs_keep_correct_repository_metadata(self):
        for package in p.PACKAGE_IDS:
            nuspec = p.ROOT / "nuget" / package.lower() / (package.lower() + ".nuspec")
            metadata = ET.parse(nuspec).find("n:metadata", p.NS)
            self.assertEqual("https://github.com/unoplatform/uno.icu",
                             metadata.findtext("n:projectUrl", namespaces=p.NS))
            repo = metadata.find("n:repository", p.NS)
            self.assertEqual(p.REPOSITORY, repo.get("url"))
            self.assertEqual("$RepositoryCommit$", repo.get("commit"))

    def test_tvos_targets_unchanged_from_pinned_upstream(self):
        text = (p.ROOT / "nuget/uno.icu-tvos/buildTransitive/Uno.icu-tvos.targets").read_text()
        # Exact normalized source identity from upstream a30094b/f701123.
        self.assertEqual("d25bfe7ef952b372798c4fa58802bd52f3cb01dd5083840bfd435dce4dd023c4",
                         hashlib.sha256(text.encode()).hexdigest())
        self.assertIn("!$(RuntimeIdentifier.StartsWith('tvossimulator'))", text)
        self.assertIn('tvossim/libicuuc.a', text)
        self.assertIn('tvos/libicuuc.a', text)

    def test_filter_shim_and_version_bytes_preserved_after_eol_normalization(self):
        expected = {
            "version.json": "c63c9dffa50915a718d414cbbaf4bd12b6c3e30fc0ba82c9eb8eae56d1055ddb",
            "src/cldr_data/filters.json": "02f535854acf1cfa857808aa1fe2e7d07f807733b986cfa4611b7898f8472660",
            "src/unoicu/unoicu.c": "e5216fa4c689ba03a9b7888a29a841084fe29d7364dbd3dcbacac6f109753958",
        }
        for name, digest in expected.items():
            with self.subTest(name=name):
                self.assertEqual(digest, hashlib.sha256((p.ROOT / name).read_text().encode()).hexdigest())

    def test_wasm_toolchain_cannot_be_silently_omitted_or_substituted(self):
        entries = {f"buildTransitive/native/unoicu.a/{version}/{variant}/unoicu.a": "fixture"
                   for version in ("3.1.56", "5.0.6") for variant in ("st", "mt", "st,simd", "mt,simd")}
        entries["buildTransitive/icudt.dat"] = "fixture"
        p.validate_native_entries("Uno.icu-wasm", entries)
        del entries["buildTransitive/native/unoicu.a/5.0.6/mt,simd/unoicu.a"]
        with self.assertRaises(ValueError):
            p.validate_native_entries("Uno.icu-wasm", entries)
        for version in ("", "3.1.57", "5.0.6;echo bad"):
            with self.assertRaises(ValueError):
                b.expected_payloads("wasm", "st", version)

    def test_tvos_requires_both_device_and_simulator_without_shared_data(self):
        entries = {f"buildTransitive/{arch}/{name}.a": "fixture"
                   for arch in ("tvos", "tvossim") for name in ("libicuuc", "libicudata")}
        p.validate_native_entries("Uno.icu-tvos", entries)
        del entries["buildTransitive/tvossim/libicudata.a"]
        with self.assertRaises(ValueError):
            p.validate_native_entries("Uno.icu-tvos", entries)


if __name__ == "__main__":
    unittest.main()
