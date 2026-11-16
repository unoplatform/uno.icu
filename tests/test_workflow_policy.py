"""Trigger/permission regressions, not a hosted authorization or attestation."""
from pathlib import Path
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]


def workflow(name):
    return yaml.load((ROOT / ".github/workflows" / name).read_text(), Loader=yaml.BaseLoader)


class WorkflowPolicy(unittest.TestCase):
    def test_legacy_mutations_require_explicit_dispatch(self):
        main = workflow("main.yml")
        self.assertEqual("contracts", main["on"]["workflow_dispatch"]["inputs"]["operation"]["default"])
        for job in ("sign", "publish_dev", "publish_prod"):
            condition = main["jobs"][job]["if"]
            with self.subTest(job=job):
                self.assertIn("github.event_name == 'workflow_dispatch'", condition)
                self.assertIn("inputs.authorize_release", condition)
                self.assertIn("inputs.expected_sha == github.sha", condition)
                self.assertNotIn("github.event_name == 'push'", condition)

    def test_default_events_cannot_start_native_builds(self):
        jobs = workflow("main.yml")["jobs"]
        for job in ("build_unoicu", "build_icudt", "build_libicu_windows", "build_libicu_osx"):
            self.assertIn("release_authorization", jobs[job]["needs"])
        condition = jobs["release_authorization"]["if"]
        self.assertIn("github.event_name == 'workflow_dispatch'", condition)
        self.assertIn("inputs.authorize_release", condition)

    def test_build_only_is_manual_and_read_only(self):
        build = workflow("build-only.yml")
        self.assertEqual({"workflow_dispatch", "workflow_call"}, set(build["on"]))
        self.assertEqual({"contents": "read"}, build["permissions"])
        inputs = build["on"]["workflow_dispatch"]["inputs"]
        self.assertEqual("false", inputs["authorize_native"]["default"])
        self.assertEqual(["all", "linux", "windows", "macos"], inputs["target"]["options"])
        text = (ROOT / ".github/workflows/build-only.yml").read_text()
        for forbidden in ("secrets.", "id-token:", "attestations:", "azure/", "sign code",
                          "nuget push", "gh release", "git push", "environment:"):
            self.assertNotIn(forbidden, text)
        for job in build["jobs"].values():
            self.assertNotIn("permissions", job)
            self.assertNotIn("uses", job)  # No reusable release workflow.
            for step in job.get("steps", []):
                if "uses" in step:
                    self.assertRegex(step["uses"], r"^actions/[\w-]+@[0-9a-f]{40}$")
                if step.get("uses", "").startswith("actions/checkout@"):
                    self.assertEqual("false", step["with"]["persist-credentials"])
                if step.get("uses", "").startswith("actions/upload-artifact@"):
                    self.assertEqual("error", step["with"]["if-no-files-found"])

    def test_pr_auxiliary_workflows_cannot_mutate(self):
        labeler = workflow("labeler.yml")
        self.assertEqual({"workflow_dispatch"}, set(labeler["on"]))
        self.assertEqual({"contents": "read"}, labeler["jobs"]["triage"]["permissions"])
        self.assertNotIn("uses", labeler["jobs"]["triage"]["steps"][0])
        commits = workflow("conventional-commits.yml")
        self.assertEqual({"contents": "read"}, commits["permissions"])
        self.assertNotIn("env", commits)

    def test_build_matrix_is_complete_and_ios_is_isolated(self):
        jobs = workflow("build-only.yml")["jobs"]
        self.assertEqual(["st", "mt", "st,simd", "mt,simd"],
                         jobs["wasm"]["strategy"]["matrix"]["variant"])
        self.assertEqual(["3.1.56", "5.0.6"], jobs["wasm"]["strategy"]["matrix"]["emscripten"])
        self.assertEqual(["x64", "arm64"], jobs["windows"]["strategy"]["matrix"]["arch"])
        mac = jobs["macos"]["strategy"]["matrix"]["include"]
        self.assertEqual({"x86_64", "arm64"}, {row["arch"] for row in mac})
        self.assertEqual(["macos"], jobs["macos_universal"]["needs"])
        self.assertNotIn("ios", " ".join(jobs))
        self.assertNotIn("tvos", " ".join(jobs))
        for name in ("data", "wasm", "windows", "macos"):
            self.assertEqual(["authorize"], jobs[name]["needs"])


if __name__ == "__main__":
    unittest.main()
