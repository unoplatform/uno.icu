"""Packaging contracts; fixture bytes are NOT native build/runtime acceptance."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "eng"))
import provenance as p


class SourceContracts(unittest.TestCase):
    def test_every_nuspec_declares_distinct_notices_and_source(self):
        for package_id in p.PACKAGE_IDS:
            with self.subTest(package=package_id):
                path = p.ROOT / "nuget" / package_id.lower() / f"{package_id.lower()}.nuspec"
                tree = ET.parse(path)
                metadata = tree.find("n:metadata", p.NS)
                self.assertEqual("LICENSE.txt", metadata.findtext("n:license", namespaces=p.NS))
                self.assertEqual("file", metadata.find("n:license", p.NS).get("type"))
                self.assertEqual("$RepositoryCommit$", metadata.find("n:repository", p.NS).get("commit"))
                self.assertEqual(p.REPOSITORY, metadata.find("n:repository", p.NS).get("url"))
                files = {n.attrib["src"].replace("\\", "/").split("/")[-1]
                         for n in tree.findall("n:files/n:file", p.NS)}
                self.assertTrue({"ICU-LICENSE.txt", "Uno-LICENSE.md", "LICENSE.txt",
                                 "NOTICE.md", "source.json", f"{package_id}.payloads.json"} <= files)

    def test_immutable_version_guard(self):
        for version in ("77.2.1", "77.2.1-fixed", "77.2.2-provenance.1", "77.1.9", "77.3.0", "garbage"):
            with self.subTest(version=version), self.assertRaises(ValueError):
                p.validate_version(version)
        p.validate_version("77.4.0-dev.1")

    def test_source_lock_is_exact_commit_not_version_inference(self):
        lock = p.load_lock()
        self.assertEqual("77.1", lock["upstreamVersion"])
        self.assertEqual("457157a92aa053e632cc7fcfd0e12f8a943b2d11", lock["commit"])
        self.assertIn(lock["commit"], lock["archiveUrl"])
        self.assertEqual(64, len(lock["archiveSha256"]))

    def test_pipeline_pins_all_source_downloads_and_retains_final_hashes(self):
        workflow = (p.ROOT / ".github/workflows/main.yml").read_text()
        self.assertNotIn("archive/refs/tags/release-77-1.zip", workflow)
        self.assertIn("python eng/provenance.py fetch-source --archive icu.zip", workflow)
        self.assertIn("provenance.py inventory", workflow)
        self.assertIn("actions/attest-build-provenance@", workflow)
        self.assertIn("subject-path: artifacts/NuGet/*.nupkg", workflow)
        self.assertIn("signed-packages.json", workflow)
        self.assertIn("dotnet nuget verify", workflow)
        self.assertNotIn("--skip-duplicate", workflow)
        for dockerfile in ("src/unoicu/Dockerfile", "src/cldr_data/Dockerfile"):
            text = (p.ROOT / dockerfile).read_text()
            self.assertNotIn("wget", "\n".join(line for line in text.splitlines() if line.startswith("RUN wget")))
            self.assertIn("COPY icu.zip", text)
            self.assertIn("sha256sum --check --strict", text)
            self.assertIn("457157a92aa053e632cc7fcfd0e12f8a943b2d11", text)

    def test_missing_wasm_variant_rejected(self):
        entries = {f"buildTransitive/native/unoicu.a/3.1.56/{mode}/unoicu.a": "hash"
                   for mode in ("st", "mt", "st,simd")}
        entries["buildTransitive/icudt.dat"] = "hash"
        with self.assertRaises(ValueError):
            p.validate_native_entries("Uno.icu-wasm", entries)

    def test_refuses_overwriting_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "evidence.json"
            p.write_new(path, b"original")
            with self.assertRaises(FileExistsError):
                p.write_new(path, b"replacement")
            self.assertEqual(b"original", path.read_bytes())

    def test_pack_uses_pinned_license_capable_tool(self):
        pack = (p.ROOT / "eng/Pack.ps1").read_text()
        self.assertIn("artifacts/tools/nuget-6.14.0.exe", pack)
        self.assertIn("fetch-tool", pack)
        self.assertNotIn(r".\nuget.exe pack", pack)
        self.assertEqual("6.14.0", p.load_tools()["nuget"]["version"])

    def test_archive_hash_mismatch_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "icu.zip"
            path.write_bytes(b"wrong archive")
            with self.assertRaisesRegex(ValueError, "archive SHA-256"):
                p.verify_source(path, p.load_lock())

    def test_signing_may_change_only_signature(self):
        before = {"packages": [{"id": "Uno.icu-win", "version": "77.2.2",
                                "entrySha256": {"native.dll": "original"}}]}
        after = copy.deepcopy(before["packages"])
        after[0]["sha256"] = "different signed zip"
        p.compare_signing_payloads(after, before)
        after[0]["entrySha256"]["native.dll"] = "altered"
        with self.assertRaisesRegex(ValueError, "non-signature"):
            p.compare_signing_payloads(after, before)

    def test_signing_cannot_drop_package(self):
        with self.assertRaisesRegex(ValueError, "package set"):
            p.compare_signing_payloads([], {"packages": [{"id": "Uno.icu-win"}]})


class ArchiveContracts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "eng").mkdir()
        self.license = b"fixture complete upstream license\nthird party terms\n"
        self.uno = b"fixture separate Uno license\n"
        self.notice = b"fixture scope notice\n"
        self.lock = copy.deepcopy(p.load_lock())
        self.lock["licenseSha256"] = p.sha(self.license)
        (self.root / "eng/source-lock.json").write_bytes(p.json_bytes(self.lock))
        (self.root / "LICENSE.md").write_bytes(self.uno)
        (self.root / "eng/NOTICE.md").write_bytes(self.notice)
        self.version = "77.4.0-dev.1"
        self.commit = "a" * 40
        self.source = {"schemaVersion": 1, "packageVersion": self.version,
                       "unoRepository": {"url": p.REPOSITORY, "commit": self.commit},
                       "upstream": self.lock}
        self.entries = {
            "LICENSE.txt": self.license + self.uno,
            "licenses/ICU-LICENSE.txt": self.license,
            "licenses/Uno-LICENSE.md": self.uno,
            "NOTICE.md": self.notice,
            "provenance/source.json": p.json_bytes(self.source),
            "buildTransitive/icudt.dat": b"fixture data",
        }
        for arch in ("x64", "arm64"):
            for name in ("icuuc", "icudt"):
                self.entries[f"runtimes/win-{arch}/native/{name}77.dll"] = b"fixture only"
        self.inventory = {
            "schemaVersion": 1, "packageId": "Uno.icu-win", "packageVersion": self.version,
            "sha256": {name: p.sha(data) for name, data in self.entries.items()},
        }
        self.entries["provenance/Uno.icu-win.payloads.json"] = p.json_bytes(self.inventory)
        self.nuspec = f"""<package xmlns="{p.NS['n']}"><metadata>
<id>Uno.icu-win</id><version>{self.version}</version>
<license type="file">LICENSE.txt</license>
<repository type="git" url="{p.REPOSITORY}" commit="{self.commit}"/>
</metadata></package>""".encode()

    def inspect(self):
        path = self.root / "fixture.nupkg"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("Uno.icu-win.nuspec", self.nuspec)
            for name, data in self.entries.items():
                archive.writestr(name, data)
        return p.inspect_package(path, self.version, self.commit, self.root)

    def test_valid_inventory(self):
        result = self.inspect()
        self.assertEqual("Uno.icu-win", result["id"])
        self.assertFalse(result["signaturePresent"])
        self.assertEqual(64, len(result["sha256"]))

    def test_nuget_output_namespace_supported(self):
        self.nuspec = self.nuspec.replace(b"2010/07", b"2013/05")
        self.assertEqual("Uno.icu-win", self.inspect()["id"])

    def test_altered_payload_rejected(self):
        self.entries["buildTransitive/icudt.dat"] += b"changed"
        with self.assertRaisesRegex(ValueError, "Payload hash mismatch"):
            self.inspect()

    def test_uninventoried_payload_rejected(self):
        self.entries["extra.dll"] = b"unexpected"
        with self.assertRaisesRegex(ValueError, "Uninventoried"):
            self.inspect()

    def test_missing_notice_rejected(self):
        del self.entries["NOTICE.md"]
        with self.assertRaises(KeyError):
            self.inspect()

    def test_wrong_repository_commit_rejected(self):
        self.nuspec = self.nuspec.replace(self.commit.encode(), b"b" * 40)
        with self.assertRaisesRegex(ValueError, "repository metadata"):
            self.inspect()

    def test_old_identity_rejected(self):
        with self.assertRaises(ValueError):
            p.inspect_package(self.root / "unused", "77.2.1", self.commit, self.root)

    def test_case_colliding_entry_rejected(self):
        self.entries["notice.md"] = b"collision"
        with self.assertRaisesRegex(ValueError, "case-colliding"):
            self.inspect()


if __name__ == "__main__":
    unittest.main()
