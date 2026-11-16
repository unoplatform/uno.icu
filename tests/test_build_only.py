"""Negative/positive non-native fixtures for the hosted build entry and inventory."""
import copy
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "eng"))
import build_only as b
import provenance as p


def authorized(scope="build-only", ref="refs/heads/dev/reviewed"):
    return {
        "GITHUB_ACTIONS": "true", "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_REPOSITORY": "unoplatform/uno.icu",
        "AUTHORIZE_NATIVE": "true" if scope == "build-only" else "false",
        "AUTHORIZE_RELEASE": "false" if scope == "build-only" else "true",
        "DISPATCH_OPERATION": "" if scope == "build-only" else scope,
        "GITHUB_SHA": "a" * 40, "EXPECTED_SHA": "a" * 40, "WORKFLOW_SHA": "a" * 40,
        "BUILD_SCOPE": scope, "BUILD_TARGET": "all", "GITHUB_REF": ref,
        "GITHUB_WORKFLOW_REF": f"unoplatform/uno.icu/.github/workflows/{'build-only.yml' if scope == 'build-only' else 'main.yml'}@{ref}",
        "GITHUB_RUN_ID": "fixture-not-a-run", "GITHUB_RUN_ATTEMPT": "1",
    }


class Authorization(unittest.TestCase):
    def test_explicit_build_branch_and_release_scopes(self):
        for env in (authorized(), authorized("release-dev", "refs/heads/main"),
                    authorized("release-prod", "refs/heads/release/77.2")):
            b.authorize(env, "a" * 40, "")

    def test_default_push_pr_fork_and_unreviewed_sources_are_rejected(self):
        bad = {
            "AUTHORIZE_NATIVE": ("false", "", "True"),
            "GITHUB_ACTIONS": ("false", ""),
            "GITHUB_EVENT_NAME": ("push", "pull_request", "pull_request_target", "workflow_run"),
            "GITHUB_REPOSITORY": ("someone/uno.icu",),
            "GITHUB_SHA": ("b" * 40,),
            "WORKFLOW_SHA": ("b" * 40,),
            "EXPECTED_SHA": ("a" * 7, "b" * 40, "$(echo injection)"),
            "GITHUB_REF": ("refs/tags/test", "refs/pull/1/merge"),
            "GITHUB_WORKFLOW_REF": ("unoplatform/uno.icu/.github/workflows/main.yml@refs/heads/main",),
            "BUILD_SCOPE": ("release-prod", "release-dev", ""),
            "BUILD_TARGET": ("ios", "self-hosted", "linux; echo injection"),
        }
        for key, values in bad.items():
            for value in values:
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    env = authorized()
                    env[key] = value
                    b.authorize(env, "a" * 40, "")
        with self.assertRaisesRegex(ValueError, "clean checkout"):
            b.authorize(authorized(), "a" * 40, " M eng/build_only.py")

    def test_release_channel_must_match_ref(self):
        for scope, ref in (("release-dev", "refs/heads/release/77.2"),
                           ("release-prod", "refs/heads/main"),
                           ("release-prod", "refs/heads/dev/test")):
            with self.subTest(scope=scope, ref=ref), self.assertRaises(ValueError):
                b.authorize(authorized(scope, ref), "a" * 40, "")


class PayloadContracts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_complete_three_package_shape_excludes_ios(self):
        entries = b.expected_payloads("staging")
        self.assertEqual(15, len(entries))  # 1 data + 8 WASM + 4 PE + 2 universal dylibs
        self.assertFalse(any("ios" in name for name in entries))
        self.assertEqual(8, len([n for n in entries if n.endswith("/unoicu.a")]))

    def test_missing_empty_and_extra_payloads_rejected(self):
        with self.assertRaisesRegex(ValueError, "payload set"):
            b.validate_payloads(self.root, "wasm", "st", "3.1.56")
        name = next(iter(b.expected_payloads("wasm", "st", "3.1.56")))
        file = self.root / "payload" / name
        p.write_new(file, b"")
        with self.assertRaisesRegex(ValueError, "header"):
            b.validate_payloads(self.root, "wasm", "st", "3.1.56")
        file.write_bytes(b"!<arch>\nfixture-not-native")
        b.validate_payloads(self.root, "wasm", "st", "3.1.56")
        p.write_new(self.root / "payload/extra.a", b"!<arch>\nfixture-not-native")
        with self.assertRaisesRegex(ValueError, "payload set"):
            b.validate_payloads(self.root, "wasm", "st", "3.1.56")

    def test_arm64_cannot_be_filled_with_x64_pe(self):
        data = bytearray(128)
        data[:2] = b"MZ"
        struct.pack_into("<I", data, 0x3c, 64)
        data[64:68] = b"PE\0\0"
        struct.pack_into("<H", data, 68, 0x8664)
        for name in b.expected_payloads("windows", "arm64"):
            p.write_new(self.root / "payload" / name, data)
        with self.assertRaisesRegex(ValueError, "header"):
            b.validate_payloads(self.root, "windows", "arm64")

    def test_reject_unknown_matrix_variants(self):
        for kind, variant in (("wasm", "st-nosimd"), ("windows", "x86"), ("macos", "universal")):
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                b.expected_payloads(kind, variant)

    def test_failed_command_retains_evidence_without_success_manifest(self):
        with patch.object(b, "OUT", self.root):
            build = b.Build("data")
            with self.assertRaises(b.subprocess.CalledProcessError):
                build.run([sys.executable, "-c", "raise SystemExit(7)"])
            self.assertEqual(7, json.loads((build.directory / "logs/01.json").read_text())["exitCode"])
            self.assertFalse((build.directory / "artifact.json").exists())
            with self.assertRaises(FileExistsError):
                b.Build("data")

    def test_native_log_bytes_are_retained_without_assuming_utf8(self):
        with patch.object(b, "OUT", self.root):
            build = b.Build("data")
            build.run([sys.executable, "-c", "import sys; sys.stdout.buffer.write(bytes([255]))"])
            self.assertEqual(b"\xff", (build.directory / "logs/01.log").read_bytes())
            self.assertEqual(0, json.loads((build.directory / "logs/01.json").read_text())["exitCode"])

    def test_inventory_does_not_exempt_nested_artifact_json(self):
        p.write_new(self.root / "artifact.json", b"top-level inventory")
        p.write_new(self.root / "rows/one/artifact.json", b"input inventory")
        self.assertEqual({"rows/one/artifact.json"}, set(b.file_hashes(self.root)))

    def test_wrong_row_commit_run_and_modified_artifact_rejected(self):
        env = authorized()
        manifest = {
            "schemaVersion": 1, "kind": "source", "variant": "", "unoCommit": "a" * 40,
            "emscriptenVersion": "",
            "upstream": p.load_lock(), "build": env, "files": {},
        }
        for field, value in (("unoCommit", "b" * 40), ("kind", "data"),
                             ("build", {**env, "GITHUB_RUN_ID": "different-run"})):
            changed = copy.deepcopy(manifest)
            changed[field] = value
            (self.root / "artifact.json").write_bytes(p.json_bytes(changed))
            with self.subTest(field=field), self.assertRaises(ValueError):
                b.verify_bundle(self.root, "source", env=env)
        (self.root / "artifact.json").write_bytes(p.json_bytes(manifest))
        p.write_new(self.root / "uninventoried-file", b"changed")
        with self.assertRaisesRegex(ValueError, "inventory mismatch"):
            b.verify_bundle(self.root, "source", env=env)

    def test_full_non_native_fixture_assembly_and_tamper_detection(self):
        # Only unittest-generated format headers, never shipping binaries or
        # a test-only success flag in the production entry.
        repo = self.root / "repo"
        repo.mkdir()
        p.write_new(repo / "tracked.txt", b"fixture input")
        p.write_new(repo / "eng/build-images.json", b"{}")
        archive = repo / "artifacts/icu-source.zip"
        archive.parent.mkdir()
        license_bytes = b"fixture ICU notice, not native source"
        lock = copy.deepcopy(p.load_lock())
        with zipfile.ZipFile(archive, "w") as source:
            source.writestr(f'icu-{lock["commit"]}/LICENSE', license_bytes)
        lock["licenseSha256"] = p.sha(license_bytes)
        lock["archiveSha256"] = p.sha(archive.read_bytes())
        canonical = {"LICENSE.md": b"fixture Uno notice", "eng/NOTICE.md": b"fixture scope notice"}
        incoming = self.root / "incoming"
        incoming.mkdir()
        rows = b.matrix_rows()
        with (patch.object(b, "ROOT", repo), patch.object(b, "OUT", self.root / "out"),
              patch.object(p, "load_lock", return_value=lock),
              patch.object(p, "git", side_effect=lambda *args: (
                  "a" * 40 if args[0] == "rev-parse" else "" if args[0] == "status" else "tracked.txt")),
              patch.object(b, "repository_bytes", side_effect=lambda name: canonical[name]),
              patch.object(b, "source_inputs", return_value={"tracked.txt": p.sha(b"fixture input")}),
              patch.dict(b.os.environ, authorized(), clear=True)):
            for kind, variant, emscripten in rows:
                row = b.Build(kind, variant, emscripten)
                row.source()
                if kind == "source":
                    b.copy_new(archive, row.directory / "icu-source.zip")
                for name in b.expected_payloads(kind, variant, emscripten):
                    if name.endswith(".a"):
                        data = b"!<arch>\nfixture-not-native"
                    elif name.endswith(".dat"):
                        data = bytearray(24)
                        data[2:4], data[12:16] = b"\xda\x27", b"CmnD"
                    elif name.endswith(".dll"):
                        data = bytearray(128)
                        data[:2] = b"MZ"
                        struct.pack_into("<I", data, 0x3c, 64)
                        data[64:68] = b"PE\0\0"
                        struct.pack_into("<H", data, 68, 0xAA64 if variant == "arm64" else 0x8664)
                    else:
                        data = b"\xcf\xfa\xed\xfe" + bytes(40)
                    p.write_new(row.directory / "payload" / name, data)
                row.finish()
                b.shutil.copytree(row.directory, incoming / ("build-only-" + row.directory.name))
            b.assemble(incoming)
            staging = self.root / "out/staging"
            b.verify_bundle(staging, "staging")
            manifest = json.loads((staging / "artifact.json").read_text())
            self.assertEqual(15, len(b.file_hashes(staging / "payload")))
            self.assertEqual(15, len(manifest["inputArtifacts"]))
            self.assertFalse(manifest["authenticatedAttestation"])
            self.assertFalse(manifest["runtimeTested"])
            self.assertTrue((staging / "rows/build-only-macos-arm64/artifact.json").is_file())
            # Rehashing an altered claim cannot replace the expected Git input identity.
            row = incoming / "build-only-data"
            altered = json.loads((row / "artifact.json").read_text())
            altered["gitBlobSha256"]["tracked.txt"] = "b" * 64
            (row / "artifact.json").write_bytes(p.json_bytes(altered))
            with self.assertRaisesRegex(ValueError, "build-input identity"):
                b.verify_bundle(row, "data")


if __name__ == "__main__":
    unittest.main()
