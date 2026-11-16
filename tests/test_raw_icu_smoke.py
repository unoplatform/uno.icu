"""Raw-input binding fixtures, not native runtime or hosted acceptance."""
import copy
import json
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import icu_smoke as smoke
import raw_icu_smoke as raw
import provenance as p


class RawBinding(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.commit = "a" * 40
        self.origin = raw.Origin(self.commit, "1234", "1",
                                 "unoplatform/uno.icu/.github/workflows/main.yml@refs/heads/dev/fixture")
        self.license = b"Complete fixture ICU notice\nthird-party fixture terms\n"
        archive = self.root / "source.zip"
        lock = copy.deepcopy(p.load_lock())
        with zipfile.ZipFile(archive, "w") as source:
            source.writestr(f'icu-{lock["commit"]}/LICENSE', self.license)
        lock["archiveSha256"] = p.sha(archive.read_bytes())
        lock["licenseSha256"] = p.sha(self.license)
        self.blobs = {
            "eng/source-lock.json": p.json_bytes(lock),
            "eng/build-images.json": b"{}",
            "LICENSE.md": b"Complete fixture Uno notice\n",
            "eng/NOTICE.md": b"Fixture scopes; no native payload execution\n",
            "src/cldr_data/filters.json": (p.ROOT / "src/cldr_data/filters.json").read_bytes(),
        }
        self.snapshot = raw.ProducerSource(self.commit, self.blobs)
        self.bundles = {}
        self.digests = {}
        for name, kind, variant in (("source", "source", ""), ("data", "data", ""),
                                    ("native", "windows", "x64")):
            directory = self.root / name
            directory.mkdir()
            p.write_new(directory / "licenses/ICU-LICENSE.txt", self.license)
            p.write_new(directory / "licenses/Uno-LICENSE.md", self.blobs["LICENSE.md"])
            p.write_new(directory / "NOTICE.md", self.blobs["eng/NOTICE.md"])
            p.write_new(directory / "LICENSE.txt", self.license + b"\n\n" + self.blobs["LICENSE.md"])
            if name == "source":
                p.write_new(directory / "icu-source.zip", archive.read_bytes())
            for file in raw.b.expected_payloads(kind, variant):
                if file.endswith(".dll"):
                    data = bytearray(128)
                    data[:2] = b"MZ"
                    struct.pack_into("<I", data, 0x3c, 64)
                    data[64:68] = b"PE\0\0"
                    struct.pack_into("<H", data, 68, 0x8664)
                else:
                    data = bytearray(24)
                    data[2:4], data[12:16] = b"\xda\x27", b"CmnD"
                p.write_new(directory / "payload" / file, data)
            manifest = {
                "schemaVersion": 1, "kind": kind, "variant": variant, "emscriptenVersion": "",
                "unoCommit": self.commit, "upstream": lock, "images": {},
                "gitBlobSha256": self.snapshot.hashes, "inputSha256": self.snapshot.hashes,
                "build": self.origin.build_fields(), "files": raw.b.file_hashes(directory),
                "runtimeTested": False, "authenticatedAttestation": False, "shippingPackage": False,
            }
            p.write_new(directory / "artifact.json", p.json_bytes(manifest))
            self.bundles[name] = directory
            self.digests[name] = p.sha((directory / "artifact.json").read_bytes())

    def prepare(self):
        return raw.prepare(self.root / "probe", "windows-x64", self.origin,
                           self.snapshot, self.bundles, self.digests)

    def change_manifest(self, name, change):
        directory = self.bundles[name]
        manifest = json.loads((directory / "artifact.json").read_text())
        change(manifest)
        (directory / "artifact.json").write_bytes(p.json_bytes(manifest))
        self.digests[name] = p.sha((directory / "artifact.json").read_bytes())

    def test_complete_binding_copies_only_selected_bytes_and_full_notices(self):
        binding = self.prepare()
        self.assertEqual(self.commit, binding["producerCommit"])
        self.assertEqual(62, len(json.loads((self.root / "probe/runtime/filters.json").read_text())
                                 ["localeFilter"]["whitelist"]))
        self.assertEqual(smoke.raw_file_names("windows-x64"), set(binding["preparedFiles"]))
        self.assertFalse(binding["runtimeTested"])
        self.assertFalse(binding["authenticatedAttestation"])
        self.assertEqual(self.license, (self.root / "probe/notices/ICU-LICENSE.txt").read_bytes())
        self.assertEqual(self.digests, binding["inputManifestSha256"])
        with self.assertRaises(FileExistsError):
            self.prepare()

    def test_changed_native_or_data_bytes_rejected_before_preparation(self):
        for name in ("native", "data"):
            with self.subTest(name=name):
                file = next(path for path in (self.bundles[name] / "payload").rglob("*") if path.is_file())
                original = file.read_bytes()
                file.write_bytes(original + b"tampered")
                with self.assertRaisesRegex(ValueError, "inventory"):
                    self.prepare()
                file.write_bytes(original)
        self.assertFalse((self.root / "probe").exists())

    def test_wrong_external_manifest_digest_rejected(self):
        self.digests["native"] = "b" * 64
        with self.assertRaisesRegex(ValueError, "manifest SHA"):
            self.prepare()

    def test_run_attempt_commit_and_source_blob_mismatches_rejected(self):
        cases = [
            lambda m: m["build"].update(GITHUB_RUN_ID="other"),
            lambda m: m["build"].update(GITHUB_RUN_ATTEMPT="2"),
            lambda m: m.update(unoCommit="b" * 40),
            lambda m: m["gitBlobSha256"].update({"LICENSE.md": "b" * 64}),
        ]
        path = self.bundles["native"] / "artifact.json"
        original = path.read_bytes()
        for change in cases:
            with self.subTest(change=change):
                self.change_manifest("native", change)
                with self.assertRaises(ValueError):
                    self.prepare()
                path.write_bytes(original)
                self.digests["native"] = p.sha(original)

    def test_truncated_notice_rejected_even_if_inventory_is_rehashed(self):
        path = self.bundles["data"] / "licenses/ICU-LICENSE.txt"
        path.write_bytes(self.license[:10])
        self.change_manifest("data", lambda m: m.update(files=raw.b.file_hashes(self.bundles["data"])))
        with self.assertRaisesRegex(ValueError, "notice"):
            self.prepare()

    def test_wrong_architecture_and_unsupported_rows_rejected(self):
        directory = self.bundles["native"]
        file = next((directory / "payload").rglob("icuuc77.dll"))
        data = bytearray(file.read_bytes())
        struct.pack_into("<H", data, 68, 0xAA64)
        file.write_bytes(data)
        self.change_manifest("native", lambda m: m.update(files=raw.b.file_hashes(directory)))
        with self.assertRaisesRegex(ValueError, "header"):
            self.prepare()
        for row in ("windows-arm64", "wasm", "macos-universal"):
            with self.subTest(row=row), self.assertRaises(ValueError):
                raw.row_identity(row)

    def test_macos_requires_matching_thin_cpu_not_a_fat_or_other_cpu_file(self):
        for row, cpu in (("macos-x86_64", 0x1000007), ("macos-arm64", 0x100000C)):
            data = bytearray(40)
            data[:4] = b"\xcf\xfa\xed\xfe"
            struct.pack_into("<I", data, 4, cpu)
            raw.check_thin_macos(bytes(data), row)
            data[:4] = b"\xca\xfe\xba\xbe"
            with self.assertRaises(ValueError):
                raw.check_thin_macos(bytes(data), row)
        with self.assertRaises(ValueError):
            raw.check_thin_macos(b"\xcf\xfa\xed\xfe" + bytes(36), "macos-arm64")

    def test_both_macos_thin_rows_bind_their_own_libraries_and_same_data(self):
        template = json.loads((self.bundles["native"] / "artifact.json").read_text())
        for row, cpu in (("macos-x86_64", 0x1000007), ("macos-arm64", 0x100000C)):
            kind, variant = raw.row_identity(row)
            directory = self.root / row
            for name in ("LICENSE.txt", "NOTICE.md", "licenses/ICU-LICENSE.txt", "licenses/Uno-LICENSE.md"):
                p.write_new(directory / name, (self.bundles["source"] / name).read_bytes())
            for file in raw.b.expected_payloads(kind, variant):
                data = bytearray(40)
                data[:4] = b"\xcf\xfa\xed\xfe"
                struct.pack_into("<I", data, 4, cpu)
                p.write_new(directory / "payload" / file, data)
            manifest = {**template, "kind": kind, "variant": variant, "files": raw.b.file_hashes(directory)}
            p.write_new(directory / "artifact.json", p.json_bytes(manifest))
            bundles = {**self.bundles, "native": directory}
            digests = {**self.digests, "native": p.sha((directory / "artifact.json").read_bytes())}
            binding = raw.prepare(self.root / ("probe-" + row), row, self.origin, self.snapshot, bundles, digests)
            self.assertEqual(row, binding["row"])
            self.assertEqual(smoke.raw_file_names(row), set(binding["preparedFiles"]))
            self.assertEqual(digests, binding["inputManifestSha256"])

    def test_worker_detects_changes_before_and_after_cases(self):
        self.prepare()
        binding = self.root / "probe/prepared-inputs.json"
        digest = p.sha(binding.read_bytes())
        with patch.object(smoke, "require_raw_host"), patch.object(smoke, "probe_directory") as cases:
            cases.return_value = {"nativeVersion": [77, 1, 0, 0]}
            result = smoke.probe_prepared(binding, digest)
            self.assertEqual(digest, result["preparedInputsSha256"])
            self.assertEqual(62, len(cases.call_args.args[1]))
            def mutate(*args):
                (self.root / "probe/runtime/icudt.dat").write_bytes(b"changed during probe")
                return {}
            cases.side_effect = mutate
            with self.assertRaisesRegex(ValueError, "input hash"):
                smoke.probe_prepared(binding, digest)
            cases.reset_mock()
            with self.assertRaisesRegex(ValueError, "input hash"):
                smoke.probe_prepared(binding, digest)
            cases.assert_not_called()

    def test_wrong_host_and_unverified_binding_never_load_native(self):
        with (patch.object(smoke.sys, "platform", "win32"),
              patch.object(smoke.platform, "machine", return_value="ARM64"),
              self.assertRaises(ValueError)):
            smoke.require_raw_host("windows-x64")
        self.prepare()
        with patch.object(smoke, "probe_directory") as cases, self.assertRaises(ValueError):
            smoke.probe_prepared(self.root / "probe/prepared-inputs.json", "b" * 64)
        cases.assert_not_called()

    def test_duplicate_json_fields_and_fabricated_attestation_claims_rejected(self):
        with self.assertRaises(ValueError):
            raw.read_json(b'{"schemaVersion":1,"schemaVersion":2}')
        self.change_manifest("native", lambda m: m.update(authenticatedAttestation=True))
        with self.assertRaises(ValueError):
            self.prepare()

    def test_worker_failure_retains_logs_and_never_creates_a_pass_report(self):
        binding = self.prepare()
        with patch.object(raw.subprocess, "run", return_value=subprocess.CompletedProcess([], 7)):
            with self.assertRaises(subprocess.CalledProcessError):
                raw.execute(self.root / "probe", binding)
        self.assertEqual(7, json.loads((self.root / "probe/worker-exit.json").read_text())["exitCode"])
        self.assertTrue((self.root / "probe/worker-command.json").exists())
        self.assertFalse((self.root / "probe/probe-report.json").exists())

    def test_worker_output_cannot_substitute_unexecuted_or_different_data(self):
        binding = self.prepare()
        directory = self.root / "probe"
        result = {
            "preparedInputsSha256": p.sha((directory / "prepared-inputs.json").read_bytes()),
            "inputSha256": binding["preparedFiles"], "row": "windows-x64",
            "nativeVersion": [77, 1, 0, 0], "dataSha256": "b" * 64, "cultureCount": 62,
        }
        def worker(*args, **kwargs):
            p.write_new(directory / "worker-result.json", p.json_bytes(result))
            return subprocess.CompletedProcess([], 0)
        with patch.object(raw.subprocess, "run", side_effect=worker):
            with self.assertRaisesRegex(ValueError, "Worker result"):
                raw.execute(directory, binding)
        self.assertFalse((directory / "probe-report.json").exists())

    def worker_result(self, directory, binding, complete=False):
        result = {
            "preparedInputsSha256": p.sha((directory / "prepared-inputs.json").read_bytes()),
            "inputSha256": binding["preparedFiles"], "row": binding["row"],
            "nativeVersion": [77, 1, 0, 0],
            "dataSha256": binding["preparedFiles"]["icudt.dat"], "cultureCount": 62,
        }
        if complete:
            locales = json.loads(self.blobs["src/cldr_data/filters.json"])["localeFilter"]["whitelist"]
            result.update(
                cultures=[{"requested": locale, "actual": locale} for locale in locales],
                lineBreakCases={locale: [0, 1, 2] for locale in ("en", "ar", "he", "hi", "th", "zh_Hans", "ja", "km")},
                scripts={"U+0041": "Latin", "U+0639": "Arabic", "U+05D0": "Hebrew",
                         "U+0915": "Devanagari", "U+4E2D": "Han", "U+10400": "Deseret"},
                bidiDirection=2)
        p.write_new(directory / "worker-result.json", p.json_bytes(result))
        return subprocess.CompletedProcess([], 0)

    def test_prepared_input_replacement_is_rejected_before_worker_launch(self):
        binding = self.prepare()
        directory = self.root / "probe"
        changed = b"unapproved replacement data"
        (directory / "runtime/icudt.dat").write_bytes(changed)
        prepared = json.loads((directory / "prepared-inputs.json").read_text())
        prepared["files"]["icudt.dat"] = p.sha(changed)
        (directory / "prepared-inputs.json").write_bytes(p.json_bytes(prepared))
        with patch.object(raw.subprocess, "run", side_effect=lambda *a, **k: self.worker_result(directory, binding)) as worker:
            with self.assertRaises(ValueError):
                raw.execute(directory, binding)
            # Do not dump process environment values in failing mock diagnostics.
            self.assertEqual(0, worker.call_count)

    def test_worker_cannot_truncate_retained_notices(self):
        binding = self.prepare()
        directory = self.root / "probe"
        def worker(*args, **kwargs):
            (directory / "notices/ICU-LICENSE.txt").write_bytes(b"truncated")
            return self.worker_result(directory, binding)
        with patch.object(raw.subprocess, "run", side_effect=worker), self.assertRaises(ValueError):
            raw.execute(directory, binding)

    def test_worker_cannot_replace_retained_producer_binding(self):
        binding = self.prepare()
        directory = self.root / "probe"
        def worker(*args, **kwargs):
            (directory / "evidence/native.artifact.json").write_bytes(b"{}")
            return self.worker_result(directory, binding)
        with patch.object(raw.subprocess, "run", side_effect=worker), self.assertRaises(ValueError):
            raw.execute(directory, binding)

    def test_worker_cannot_claim_case_count_without_case_results(self):
        binding = self.prepare()
        directory = self.root / "probe"
        with (patch.object(raw.subprocess, "run",
                           side_effect=lambda *a, **k: self.worker_result(directory, binding)),
              self.assertRaisesRegex(ValueError, "case results")):
            raw.execute(directory, binding)

    def test_complete_worker_receipt_is_checked_without_claiming_fixture_execution(self):
        binding = self.prepare()
        directory = self.root / "probe"
        with patch.object(raw.subprocess, "run",
                          side_effect=lambda *a, **k: self.worker_result(directory, binding, complete=True)) as worker:
            result = raw.execute(directory, binding)
        self.assertEqual(1, worker.call_count)
        self.assertEqual(62, len(result["cultures"]))
        self.assertEqual(8, len(result["lineBreakCases"]))
        self.assertEqual(6, len(result["scripts"]))
        self.assertEqual(2, result["bidiDirection"])
        self.assertFalse((directory / "probe-report.json").exists())

    def test_origin_cannot_infer_or_mix_an_unrelated_build_identity(self):
        for commit, run, attempt, workflow in (
            ("short", "1234", "1", self.origin.workflow_ref),
            (self.commit, "", "1", self.origin.workflow_ref),
            (self.commit, "1234", "0", self.origin.workflow_ref),
            (self.commit, "1234", "1", "someone/uno.icu/.github/workflows/main.yml@refs/heads/main"),
            (self.commit, "1234", "1", "unoplatform/uno.icu/.github/workflows/other.yml@refs/heads/main"),
        ):
            with self.assertRaises(ValueError):
                raw.Origin(commit, run, attempt, workflow)

    def test_native_worker_environment_excludes_credentials_and_loader_overrides(self):
        env = {"SystemRoot": "fixture-system", "TEMP": "fixture-temp", "PROCESSOR_ARCHITECTURE": "AMD64",
               "GITHUB_TOKEN": "fixture-only", "AZURE_CLIENT_SECRET": "fixture-only",
               "PYTHONPATH": "fixture-injection", "LD_PRELOAD": "fixture-injection",
               "DYLD_LIBRARY_PATH": "fixture-injection"}
        with patch.dict(raw.os.environ, env, clear=True):
            actual = raw.worker_environment()
        self.assertEqual({"SYSTEMROOT", "TEMP", "PROCESSOR_ARCHITECTURE"}, {key.upper() for key in actual})


if __name__ == "__main__":
    unittest.main()
