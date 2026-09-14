"""Offline plan/API/transport contracts. No native loading or live GitHub calls."""
import io
import json
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "eng"))
import hosted_raw_smoke as h
import provenance as p


def example():
    return json.loads(h.PLAN_PATH.read_text())


def api_run(plan):
    producer = plan["producer"]
    repo = {"id": producer["repositoryId"], "full_name": producer["repository"]}
    return {"id": producer["runId"], "run_attempt": producer["runAttempt"], "head_sha": producer["commit"],
            "head_branch": producer["branch"], "event": producer["event"], "path": producer["workflowPath"],
            "status": "completed", "conclusion": "success", "repository": repo, "head_repository": repo.copy()}


def api_jobs(plan):
    return {"total_count": len(plan["jobs"]),
            "jobs": [{"name": name, "id": job["id"], "conclusion": job["conclusion"], "status": "completed"}
                     for name, job in plan["jobs"].items()]}


def api_artifact(plan, role):
    item, producer = plan["artifacts"][role], plan["producer"]
    return {"id": item["id"], "name": item["name"], "size_in_bytes": item["zipBytes"],
            "digest": "sha256:" + item["zipSha256"], "expired": False,
            "workflow_run": {"id": producer["runId"], "head_sha": producer["commit"],
                             "head_branch": producer["branch"], "repository_id": producer["repositoryId"],
                             "head_repository_id": producer["repositoryId"]}}


class PlanContracts(unittest.TestCase):
    def test_reviewed_plan_has_exact_producer_five_artifacts_and_complete_jobs(self):
        plan = h.parse_plan(h.PLAN_PATH.read_bytes())
        self.assertEqual(34811569292, plan["producer"]["runId"])
        self.assertEqual("8fd7b5345570bb4b7230e13aa596def2df8fc7ad", plan["producer"]["commit"])
        self.assertEqual(1, plan["producer"]["runAttempt"])
        self.assertEqual(5, len(plan["artifacts"]))
        self.assertEqual(34, len(plan["jobs"]))
        self.assertEqual(17, len(h.SUCCESS_JOBS))
        h.verify_run(plan, api_run(plan))
        h.verify_jobs(plan, api_jobs(plan))
        for role in plan["artifacts"]:
            h.verify_artifact(plan, role, api_artifact(plan, role))

    def test_plan_rejects_unknown_fields_types_paths_and_producer_substitutions(self):
        changes = [
            lambda p: p.update(schemaVersion=True),
            lambda p: p.update(downloadUrl="https://unapproved.example"),
            lambda p: p["producer"].update(repository="other/private"),
            lambda p: p["producer"].update(repositoryId=True),
            lambda p: p["producer"].update(runAttempt=0),
            lambda p: p["producer"].update(commit="short"),
            lambda p: p["producer"].update(branch="../other"),
            lambda p: p["producer"].update(workflowPath=".github/workflows/other.yml"),
            lambda p: p["producer"].update(event="pull_request"),
        ]
        for change in changes:
            plan = example()
            change(plan)
            with self.subTest(change=change), self.assertRaises(ValueError):
                h.parse_plan(p.json_bytes(plan))
        with self.assertRaises(ValueError):
            h.parse_plan(b'{"schemaVersion":1,"schemaVersion":2}')

    def test_plan_rejects_missing_ambiguous_wrong_arch_and_hash_inputs(self):
        changes = [
            lambda p: p["artifacts"].pop("macos-arm64"),
            lambda p: p["artifacts"].update({"windows-arm64": p["artifacts"]["windows-x64"]}),
            lambda p: p["artifacts"]["source"].update(name="../source"),
            lambda p: p["artifacts"]["source"].update(zipBytes=0),
            lambda p: p["artifacts"]["source"].update(id=p["artifacts"]["data"]["id"]),
            lambda p: p["artifacts"]["source"].update(zipSha256="invalid"),
            lambda p: p["artifacts"]["source"].update(manifestSha256="A" * 64),
            lambda p: p["artifacts"]["windows-x64"].update(name="build-only-windows-arm64"),
        ]
        for change in changes:
            plan = example()
            change(plan)
            with self.subTest(change=change), self.assertRaises(ValueError):
                h.parse_plan(p.json_bytes(plan))

    def test_plan_requires_all_success_rows_and_skipped_release_jobs(self):
        for name in ("build_only / windows (arm64)", "build_only / staging", "Sign Package"):
            plan = example()
            plan["jobs"].pop(name)
            with self.assertRaises(ValueError):
                h.parse_plan(p.json_bytes(plan))
        plan = example()
        plan["jobs"]["Sign Package"]["conclusion"] = "success"
        with self.assertRaises(ValueError):
            h.parse_plan(p.json_bytes(plan))

    def test_api_run_rejects_wrong_commit_attempt_branch_event_repo_or_incomplete_status(self):
        plan = example()
        for field, value in (("head_sha", "b" * 40), ("run_attempt", 2), ("head_branch", "main"),
                             ("event", "push"), ("status", "in_progress"), ("conclusion", "failure"),
                             ("head_repository", {"id": 1, "full_name": "other/repo"})):
            with self.subTest(field=field), self.assertRaises(ValueError):
                h.verify_run(plan, {**api_run(plan), field: value})

    def test_api_jobs_reject_missing_duplicate_substituted_failed_and_running_jobs(self):
        plan = example()
        changes = [
            lambda r: r["jobs"].pop(),
            lambda r: r["jobs"].__setitem__(0, r["jobs"][1]),
            lambda r: r["jobs"][0].update(id=1),
            lambda r: r["jobs"][0].update(status="in_progress"),
            lambda r: r["jobs"][0].update(conclusion="failure"),
        ]
        for change in changes:
            response = api_jobs(plan)
            change(response)
            with self.subTest(change=change), self.assertRaises(ValueError):
                h.verify_jobs(plan, response)

    def test_api_artifact_rejects_wrong_id_name_digest_size_run_or_fork(self):
        plan = example()
        changes = [
            lambda r: r.update(id=1), lambda r: r.update(name="build-only-windows-arm64"),
            lambda r: r.update(size_in_bytes=1), lambda r: r.update(digest="sha256:" + "b" * 64),
            lambda r: r.update(expired=True), lambda r: r["workflow_run"].update(id=1),
            lambda r: r["workflow_run"].update(head_sha="b" * 40),
            lambda r: r["workflow_run"].update(head_repository_id=1),
        ]
        for change in changes:
            response = api_artifact(plan, "windows-x64")
            change(response)
            with self.subTest(change=change), self.assertRaises(ValueError):
                h.verify_artifact(plan, "windows-x64", response)


class FakeOpener:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def open(self, request, timeout):
        self.requests.append(request)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return io.BytesIO(response)


def redirect(url):
    return urllib.error.HTTPError("https://api.github.com/fixture", 302, "Found", {"Location": url}, None)


class DownloadContracts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_download_sends_token_only_to_fixed_api_host_not_storage(self):
        data = b"fixture artifact bytes"
        artifact = {"id": 123, "zipBytes": len(data), "zipSha256": p.sha(data)}
        opener = FakeOpener([redirect("https://results.blob.core.windows.net/container/fixture.zip?sig=fixture"), data])
        h.GithubReadOnly("fixture-token", opener).download(artifact, self.root / "artifact.zip")
        self.assertEqual("Bearer fixture-token", opener.requests[0].get_header("Authorization"))
        self.assertEqual("api.github.com", h.urllib.parse.urlsplit(opener.requests[0].full_url).hostname)
        self.assertIsNone(opener.requests[1].get_header("Authorization"))
        self.assertEqual(data, (self.root / "artifact.zip").read_bytes())

    def test_unapproved_redirects_are_rejected_without_forwarding_credentials(self):
        for url in ("http://results.blob.core.windows.net/a", "https://attacker.example/a",
                    "https://results.blob.core.windows.net.attacker.example/a",
                    "https://user:password@results.blob.core.windows.net/a", "https://127.0.0.1/a"):
            opener = FakeOpener([redirect(url)])
            with self.subTest(url=url), self.assertRaises(ValueError):
                h.GithubReadOnly("fixture-token", opener).download(
                    {"id": 1, "zipBytes": 1, "zipSha256": "a" * 64}, self.root / "unused")
            self.assertEqual(1, len(opener.requests))

    def test_secondary_redirect_has_no_token_and_cannot_escape_allowlist(self):
        opener = FakeOpener([redirect("https://results.blob.core.windows.net/a"),
                             redirect("https://attacker.example/a")])
        with self.assertRaises(ValueError):
            h.GithubReadOnly("fixture-token", opener).download(
                {"id": 1, "zipBytes": 1, "zipSha256": "a" * 64}, self.root / "unused")
        self.assertEqual(2, len(opener.requests))
        self.assertIsNone(opener.requests[1].get_header("Authorization"))

    def test_hash_short_length_and_excess_length_fail_without_extraction(self):
        for length, checksum in ((2, p.sha(b"abc")), (4, p.sha(b"abc")), (3, "b" * 64)):
            opener = FakeOpener([redirect("https://results.blob.core.windows.net/a"), b"abc"])
            with self.subTest(length=length), self.assertRaises(ValueError):
                h.GithubReadOnly("fixture-token", opener).download(
                    {"id": 1, "zipBytes": length, "zipSha256": checksum}, self.root / f"{length}.zip")

    def test_metadata_api_does_not_follow_redirects_or_access_other_repositories(self):
        opener = FakeOpener([redirect("https://attacker.example/a")])
        client = h.GithubReadOnly("fixture-token", opener)
        with self.assertRaises(RuntimeError):
            client.get_json("repos/unoplatform/uno.icu/actions/runs/1")
        self.assertEqual(1, len(opener.requests))
        with self.assertRaises(ValueError):
            client.api_request("repos/other/private/actions/runs/1")

    def test_safe_extraction_rejects_paths_links_special_files_and_collisions(self):
        for index, names in enumerate((
            ["../escape"], ["/absolute"], ["C:/drive"], ["nested\\escape"], ["a", "A"],
            ["a//b"], ["a/./b"], ["NUL"], ["nested/COM1.txt"], ["trailing."], ["nul\0suffix"],
        )):
            path = self.root / f"bad-{index}.zip"
            with zipfile.ZipFile(path, "w") as archive:
                for name in names:
                    entry = zipfile.ZipInfo("fixture")
                    entry.filename = name
                    archive.writestr(entry, b"fixture")
            destination = self.root / f"out-{index}"
            with self.subTest(names=names), self.assertRaises(ValueError):
                h.extract_archive(path, destination)
            self.assertFalse(destination.exists())
        for mode in (stat.S_IFLNK, stat.S_IFIFO, stat.S_IFCHR):
            path = self.root / f"special-{mode}.zip"
            entry = zipfile.ZipInfo("special")
            entry.external_attr = (mode | 0o600) << 16
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr(entry, b"fixture")
            with self.assertRaises(ValueError):
                h.extract_archive(path, self.root / f"special-{mode}")

    def test_safe_extraction_preserves_ordinary_files_and_refuses_overwrite(self):
        path = self.root / "valid.zip"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("nested/fixture.bin", b"fixture")
        destination = self.root / "valid"
        h.extract_archive(path, destination)
        self.assertEqual(b"fixture", (destination / "nested/fixture.bin").read_bytes())
        with self.assertRaises(FileExistsError):
            h.extract_archive(path, destination)

    def test_wrong_producer_prevents_any_artifact_download(self):
        plan = example()
        client = unittest.mock.Mock()
        client.get_json.side_effect = [{**api_run(plan), "head_sha": "b" * 40}, api_jobs(plan)]
        with self.assertRaises(ValueError):
            h.download_inputs(plan, "windows-x64", self.root / "output", client)
        self.assertEqual(0, client.download.call_count)
        self.assertFalse((self.root / "output/download-receipt.json").exists())

    def test_unselected_row_metadata_substitution_prevents_all_downloads(self):
        plan = example()
        responses = [api_run(plan), api_jobs(plan)]
        for role in plan["artifacts"]:
            value = api_artifact(plan, role)
            if role == "macos-arm64":
                value["digest"] = "sha256:" + "b" * 64
            responses.append(value)
        client = unittest.mock.Mock()
        client.get_json.side_effect = responses
        with self.assertRaises(ValueError):
            h.download_inputs(plan, "windows-x64", self.root / "output", client)
        self.assertEqual(0, client.download.call_count)
        self.assertFalse((self.root / "output/download-receipt.json").exists())

    def test_run_stage_refuses_download_token_before_any_native_process(self):
        with patch.dict(h.os.environ, {"GH_TOKEN": "fixture-only"}, clear=True):
            with self.assertRaisesRegex(ValueError, "credentials"):
                h.execute_probe(example(), "windows-x64", self.root)

    def test_plan_substitution_prevents_native_execution(self):
        p.write_new(self.root / "input-plan.json", b"{}")
        with patch.dict(h.os.environ, {}, clear=True), patch.object(h.subprocess, "run") as run:
            with self.assertRaisesRegex(ValueError, "substitution"):
                h.execute_probe(example(), "windows-x64", self.root)
            self.assertEqual(0, run.call_count)

    def test_native_failure_cannot_create_hosted_success_receipt(self):
        plan = example()
        p.write_new(self.root / "input-plan.json", p.json_bytes(plan))
        p.write_new(self.root / "producer-metadata.json", b"{}")
        p.write_new(self.root / "download-receipt.json", p.json_bytes({
            "schemaVersion": 1, "row": "windows-x64", "planSha256": p.sha(p.json_bytes(plan)),
            "metadataSha256": p.sha(b"{}"), "runtimeTested": False, "authenticatedAttestation": False}))
        paths = {role: self.root / role for role in ("source", "data", "native")}
        with (patch.dict(h.os.environ, {}, clear=True), patch.object(h, "verify_retained", return_value=paths),
              patch.object(h.subprocess, "run", return_value=subprocess.CompletedProcess([], 7))):
            with self.assertRaises(subprocess.CalledProcessError):
                h.execute_probe(plan, "windows-x64", self.root)
        self.assertFalse((self.root / "hosted-smoke-receipt.json").exists())
        self.assertEqual(7, json.loads((self.root / "probe-exit.json").read_text())["exitCode"])

    def test_changed_original_bundles_after_probe_prevent_finalization(self):
        plan = example()
        p.write_new(self.root / "input-plan.json", p.json_bytes(plan))
        p.write_new(self.root / "producer-metadata.json", b"{}")
        p.write_new(self.root / "download-receipt.json", p.json_bytes({
            "schemaVersion": 1, "row": "windows-x64", "planSha256": p.sha(p.json_bytes(plan)),
            "metadataSha256": p.sha(b"{}"), "runtimeTested": False, "authenticatedAttestation": False}))
        paths = {role: self.root / role for role in ("source", "data", "native")}
        with (patch.dict(h.os.environ, {}, clear=True),
              patch.object(h, "verify_retained", side_effect=[paths, ValueError("retained bundle changed")]),
              patch.object(h.subprocess, "run", return_value=subprocess.CompletedProcess([], 0))):
            with self.assertRaisesRegex(ValueError, "retained bundle"):
                h.execute_probe(plan, "windows-x64", self.root)
        self.assertFalse((self.root / "hosted-smoke-receipt.json").exists())


if __name__ == "__main__":
    unittest.main()
