"""Real unzip regressions for Apple source preparation; never configure or compile."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "eng"))
import build_only as b
import provenance as p


def unzip_tool():
    command = shutil.which("unzip")
    if command:
        return Path(command)
    git = shutil.which("git")
    if sys.platform == "win32" and git:
        command = Path(git).resolve().parents[1] / "usr/bin/unzip.exe"
        if command.is_file():
            return command
    raise FileNotFoundError("These extraction regressions require native unzip (Git for Windows includes it)")


class UnzipBuild(b.Build):
    def __init__(self, tool):
        super().__init__("apple")
        self.tool = tool

    def run(self, command, **kwargs):
        if command[0] != "unzip":
            raise AssertionError("Extraction fixture must not execute native build commands")
        return super().run([self.tool, *command[1:]], **kwargs)


class AppleExtraction(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name) / "fresh checkout"
        self.lock = dict(p.load_lock())
        prefix = f'icu-{self.lock["commit"]}/'
        self.entries = {
            prefix + "LICENSE": b"fixture notice; not the production ICU license",
            prefix + "icu4c/source/configure": b"fixture configure; never executed",
            prefix + "icu4c/source/data/BUILDRULES.py": b"fixture data rules; never executed",
        }
        self.archive = self.root / "artifacts/icu-source.zip"
        self.archive.parent.mkdir(parents=True)
        with zipfile.ZipFile(self.archive, "w", compression=zipfile.ZIP_STORED) as archive:
            for name, data in self.entries.items():
                archive.writestr(name, data)
        self.lock.update(archiveSha256=p.sha(self.archive.read_bytes()),
                         licenseSha256=p.sha(self.entries[prefix + "LICENSE"]))
        notices = {name: b.repository_bytes(name) for name in ("LICENSE.md", "eng/NOTICE.md")}
        self.enterContext(patch.object(b, "ROOT", self.root))
        self.enterContext(patch.object(b, "OUT", self.root / "artifacts/build-only"))
        self.enterContext(patch.object(p, "load_lock", return_value=self.lock))
        self.enterContext(patch.object(b, "repository_bytes", side_effect=notices.__getitem__))
        self.build = UnzipBuild(unzip_tool())
        self.assertEqual(self.archive, self.build.source())

    def destination(self, variant):
        return self.root / "artifacts/apple-sources" / variant.name

    def test_actual_unzip_prepares_all_six_fresh_nested_variant_trees(self):
        parent = self.root / "artifacts/apple-sources"
        self.assertFalse(parent.exists())
        for variant in b.apple.VARIANTS:
            with self.subTest(variant=variant.name):
                destination = b.extract_apple_source(self.build, self.archive, variant)
                self.assertEqual(self.destination(variant), destination)
                self.assertEqual(self.entries, {path.relative_to(destination).as_posix(): path.read_bytes()
                                               for path in destination.rglob("*") if path.is_file()})
                log = self.build.directory / "logs" / f"{self.build.sequence:02}.json"
                self.assertEqual(0, json.loads(log.read_text())["exitCode"])
        self.assertEqual({variant.name for variant in b.apple.VARIANTS}, {path.name for path in parent.iterdir()})
        self.assertEqual(self.lock["archiveSha256"], p.sha(self.archive.read_bytes()))
        self.assertFalse((self.build.directory / "artifact.json").exists())
        self.assertFalse((self.build.directory / "payload").exists())

    def test_existing_empty_nonempty_or_file_destination_is_never_reused(self):
        for variant, kind in zip(b.apple.VARIANTS, ("empty", "nonempty", "file")):
            destination = self.destination(variant)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if kind == "file":
                destination.write_bytes(b"existing file")
            else:
                destination.mkdir()
                if kind == "nonempty":
                    (destination / "sentinel").write_bytes(b"existing tree")
            with self.subTest(kind=kind), self.assertRaises(FileExistsError):
                b.extract_apple_source(self.build, self.archive, variant)
            if kind == "file":
                self.assertEqual(b"existing file", destination.read_bytes())
            else:
                self.assertEqual(["sentinel"] if kind == "nonempty" else [],
                                 [path.name for path in destination.iterdir()])
            self.assertEqual(0, self.build.sequence)

    def test_parent_file_conflict_fails_before_unzip_without_overwrite(self):
        parent = self.root / "artifacts/apple-sources"
        parent.write_bytes(b"do not replace")
        with self.assertRaises((FileExistsError, NotADirectoryError)):
            b.extract_apple_source(self.build, self.archive, b.apple.VARIANTS[0])
        self.assertEqual(b"do not replace", parent.read_bytes())
        self.assertEqual(0, self.build.sequence)

    def test_source_verification_rejects_changed_archive_before_extraction(self):
        self.archive.write_bytes(self.archive.read_bytes() + b"changed source archive")
        with self.assertRaisesRegex(ValueError, "source archive SHA"):
            self.build.source()
        self.assertFalse((self.root / "artifacts/apple-sources").exists())
        self.assertEqual(0, self.build.sequence)

    def test_actual_unzip_failure_retains_partial_tree_and_diagnostic_not_success(self):
        contents = self.archive.read_bytes()
        self.archive.write_bytes(contents.replace(b"fixture configure; never executed",
                                                 b"invalid configure; never executed"))
        # A structurally readable fixture with a bad member CRC exercises unzip,
        # independently of the production lock's earlier byte verification.
        with self.assertRaises(subprocess.CalledProcessError):
            b.extract_apple_source(self.build, self.archive, b.apple.VARIANTS[0])
        self.assertNotEqual(0, json.loads((self.build.directory / "logs/01.json").read_text())["exitCode"])
        self.assertTrue((self.build.directory / "logs/01.log").read_bytes())
        self.assertFalse((self.build.directory / "artifact.json").exists())
        self.assertTrue(self.destination(b.apple.VARIANTS[0]).is_dir())
        with self.assertRaises(FileExistsError):
            b.extract_apple_source(self.build, self.archive, b.apple.VARIANTS[0])
        self.assertEqual(1, self.build.sequence)


if __name__ == "__main__":
    unittest.main()
