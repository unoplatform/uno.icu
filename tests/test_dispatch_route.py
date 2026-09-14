"""Registered-dispatch routing, exhaustive boolean/ref guards and caller identity."""
import itertools
import json
from pathlib import Path
import sys
import unittest

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "eng"))
import build_only as b
from workflow_expression import Expression

ROOT = Path(__file__).resolve().parents[1]
SHA = "a" * 40
OPERATIONS = ("contracts", "build-only", "raw-smoke", "release-dev", "release-prod", "invalid")
REFS = ("refs/heads/dev/reviewed", "refs/heads/main", "refs/heads/release/77.4",
        "refs/tags/77.4.1", "refs/pull/1/merge")


def workflow(name):
    return yaml.load((ROOT / ".github/workflows" / name).read_text(), Loader=yaml.BaseLoader)


def context(operation="build-only", native=True, release=False, ref=REFS[0],
            repository="unoplatform/uno.icu", event="workflow_dispatch"):
    return {"inputs.operation": operation, "inputs.authorize_native": native,
            "inputs.authorize_release": release, "inputs.expected_sha": SHA,
            "github.sha": SHA, "github.workflow_sha": SHA, "github.ref": ref,
            "github.repository": repository, "github.event_name": event, "inputs.target": "all",
            "inputs.authorize_apple": False, "inputs.full_five": False}


def environment(values, caller="main.yml"):
    ref = values["github.ref"]
    return {
        "GITHUB_ACTIONS": "true", "GITHUB_EVENT_NAME": values["github.event_name"],
        "GITHUB_REPOSITORY": values["github.repository"],
        "AUTHORIZE_NATIVE": json.dumps(values["inputs.authorize_native"]),
        "AUTHORIZE_RELEASE": json.dumps(values["inputs.authorize_release"]),
        "EXPECTED_SHA": values["inputs.expected_sha"], "GITHUB_SHA": values["github.sha"],
        "WORKFLOW_SHA": values["github.workflow_sha"], "GITHUB_REF": ref,
        "BUILD_SCOPE": values["inputs.operation"], "BUILD_TARGET": values["inputs.target"],
        "DISPATCH_OPERATION": values["inputs.operation"] if caller == "main.yml" else "",
        "GITHUB_WORKFLOW_REF": f'{values["github.repository"]}/.github/workflows/{caller}@{ref}',
    }


def permitted(values):
    native = (values["inputs.operation"] in ("build-only", "raw-smoke") and
              values["inputs.authorize_native"] is True and values["inputs.authorize_release"] is False and
              values["github.ref"].startswith("refs/heads/") and
              (values["inputs.operation"] != "raw-smoke" or values["inputs.target"] == "all"))
    release = (values["inputs.authorize_native"] is False and values["inputs.authorize_release"] is True and
               ((values["inputs.operation"] == "release-dev" and values["github.ref"] == "refs/heads/main") or
                (values["inputs.operation"] == "release-prod" and values["github.ref"].startswith("refs/heads/release/"))))
    common = (values["github.event_name"] == "workflow_dispatch" and
              values["github.repository"] == "unoplatform/uno.icu" and
              values["inputs.expected_sha"] == values["github.sha"] == values["github.workflow_sha"])
    return common and native, common and release


class DispatchRoute(unittest.TestCase):
    def test_registered_main_calls_only_local_build_child_without_secrets(self):
        main = workflow("main.yml")
        child = workflow("build-only.yml")
        inputs = main["on"]["workflow_dispatch"]["inputs"]
        self.assertIn("build-only", inputs["operation"]["options"])
        self.assertEqual("false", inputs["authorize_native"]["default"])
        self.assertEqual("false", inputs["authorize_release"]["default"])
        job = main["jobs"]["build_only"]
        self.assertEqual("./.github/workflows/build-only.yml", job["uses"])
        self.assertEqual({"contents": "read"}, job["permissions"])
        self.assertNotIn("secrets", job)
        self.assertEqual({"authorize_native", "authorize_release", "expected_sha", "target"}, set(job["with"]))
        call = child["on"]["workflow_call"]
        self.assertNotIn("secrets", call)
        self.assertEqual({"contents": "read"}, child["permissions"])
        for name in ("authorize_native", "authorize_release"):
            self.assertEqual("boolean", call["inputs"][name]["type"])
            self.assertEqual("${{ inputs." + name + " }}", job["with"][name])
        for name in ("expected_sha", "target"):
            self.assertEqual("${{ inputs." + name + " }}", job["with"][name])
        # Preserve boolean type at the environment boundary; string "false" is not false.
        self.assertEqual("${{ toJSON(inputs.authorize_native) }}", child["env"]["AUTHORIZE_NATIVE"])
        self.assertEqual("${{ toJSON(inputs.authorize_release) }}", child["env"]["AUTHORIZE_RELEASE"])
        self.assertEqual("${{ fromJSON(toJSON(github.event.inputs)).operation || '' }}",
                         child["env"]["DISPATCH_OPERATION"])

    def test_complete_operation_boolean_ref_repository_event_truth_table(self):
        jobs = workflow("main.yml")["jobs"]
        names = ("build_only", "raw_smoke", "release_authorization", "sign", "publish_dev", "publish_prod")
        guards = {name: Expression(jobs.get(name, {}).get("if", "${{ false }}")) for name in names}
        mismatches = []
        count = 0
        for args in itertools.product(OPERATIONS, (False, True), (False, True), REFS,
                                      ("unoplatform/uno.icu", "someone/uno.icu"),
                                      ("workflow_dispatch", "push", "pull_request")):
            count += 1
            values = context(*args)
            native, release = permitted(values)
            expected = (native and args[0] == "build-only", native and args[0] == "raw-smoke", release, release,
                        release and args[0] == "release-dev", release and args[0] == "release-prod")
            actual = tuple(guards[name].evaluate(values) for name in names)
            if actual != expected:
                mismatches.append((args, expected, actual))
            env = environment(values)
            try:
                b.authorize(env, SHA, "")
                entry_allowed = True
            except ValueError:
                entry_allowed = False
            if entry_allowed != (native or release):
                mismatches.append(("entry", args, native or release, entry_allowed))
        self.assertEqual(720, count)
        self.assertEqual([], mismatches, repr(mismatches[:8]))

    def test_malformed_authorization_and_sha_never_open_a_route(self):
        jobs = workflow("main.yml")["jobs"]
        guards = [Expression(jobs.get(name, {}).get("if", "${{ false }}")) for name in
                  ("build_only", "raw_smoke", "release_authorization", "sign", "publish_dev", "publish_prod")]
        mismatches = []
        for operation, ref, native, release in itertools.product(
                OPERATIONS, REFS, (True, False, "true", "false", "", None, 0, 1),
                (True, False, "true", "false", "", None, 0, 1)):
            if type(native) is bool and type(release) is bool:
                continue
            values = context(operation, native, release, ref)
            if any(guard.evaluate(values) for guard in guards):
                mismatches.append(values)
            with self.assertRaises(ValueError):
                b.authorize(environment(values), SHA, "")
        self.assertEqual([], mismatches, repr(mismatches[:8]))
        for field in ("inputs.expected_sha", "github.sha", "github.workflow_sha"):
            values = context()
            values[field] = "b" * 40
            self.assertFalse(any(guard.evaluate(values) for guard in guards))
            with self.assertRaises(ValueError):
                b.authorize(environment(values), SHA, "")

    def test_known_caller_and_direct_identity_only(self):
        for caller in ("main.yml", "build-only.yml"):
            env = environment(context(), caller)
            b.authorize(env, SHA, "")
            for key, value in (
                ("GITHUB_WORKFLOW_REF", "unoplatform/uno.icu/.github/workflows/other.yml@" + REFS[0]),
                ("GITHUB_WORKFLOW_REF", "unoplatform/uno.icu/.github/workflows/" + caller + "@refs/heads/other"),
                ("WORKFLOW_SHA", "b" * 40), ("GITHUB_SHA", "b" * 40),
                ("DISPATCH_OPERATION", "release-prod"), ("GITHUB_EVENT_NAME", "workflow_call"),
                ("AUTHORIZE_RELEASE", "true"),
            ):
                changed = {**env, key: value}
                with self.subTest(caller=caller, key=key), self.assertRaises(ValueError):
                    b.authorize(changed, SHA, "")
            with self.assertRaises(ValueError):
                b.authorize(env, SHA, " M eng/build_only.py")

    def test_direct_entry_retains_typed_native_opt_in_and_no_release(self):
        child = workflow("build-only.yml")
        guard = Expression(child["jobs"]["authorize"]["if"])
        for native, release in itertools.product((True, False, "true", "false", None, 0, 1), repeat=2):
            values = context(native=native, release=release)
            self.assertEqual(native is True and release is False, guard.evaluate(values))

    def test_build_only_reachable_jobs_have_read_only_permissions(self):
        main = workflow("main.yml")
        child = workflow("build-only.yml")
        jobs = main["jobs"]
        guards = {name: Expression(job.get("if", "${{ true }}")) for name, job in jobs.items()}
        for native, release, ref, repository, event in itertools.product(
                (True, False), (True, False), REFS,
                ("unoplatform/uno.icu", "someone/uno.icu"),
                ("workflow_dispatch", "push", "pull_request")):
            values = context("build-only", native, release, ref, repository, event)
            # Assume all reachable prerequisites succeed; this is the most
            # permissive DAG, not a claim that a hosted job executed.
            active = set()
            while True:
                before = set(active)
                for name, job in jobs.items():
                    needs = job.get("needs", [])
                    needs = [needs] if isinstance(needs, str) else needs
                    if set(needs) <= active and guards[name].evaluate(values):
                        active.add(name)
                if active == before:
                    break
            expected = {"provenance_contracts"}
            if permitted(values)[0]:
                expected.add("build_only")
            self.assertEqual(expected, active)
            for name in active:
                self.assertEqual({"contents": "read"}, jobs[name].get("permissions", main["permissions"]))
                self.assertNotIn("secrets", jobs[name])
                self.assertNotIn("environment", jobs[name])
        for job in child["jobs"].values():
            self.assertEqual({"contents": "read"}, job.get("permissions", child["permissions"]))
            self.assertNotIn("secrets", job)
            self.assertNotIn("environment", job)

    def test_guard_evaluator_models_actions_coercion_not_python_truthiness(self):
        self.assertTrue(Expression("${{ inputs.authorize_native }}").evaluate(context(native="false")))
        self.assertTrue(Expression("${{ inputs.authorize_native == true }}").evaluate(context(native=1)))
        self.assertFalse(Expression("${{ toJSON(inputs.authorize_native) == 'true' }}").evaluate(context(native=1)))
        self.assertTrue(Expression("${{ startsWith(github.ref, 'REFS/HEADS/') }}").evaluate(context()))

    def test_raw_smoke_reaches_no_build_release_or_write_permissions(self):
        main = workflow("main.yml")
        jobs = main["jobs"]
        guards = {name: Expression(job.get("if", "${{ true }}")) for name, job in jobs.items()}
        for native, release, ref, repository, event in itertools.product(
                (True, False), (True, False), REFS,
                ("unoplatform/uno.icu", "someone/uno.icu"),
                ("workflow_dispatch", "push", "pull_request")):
            values = context("raw-smoke", native, release, ref, repository, event)
            active = set()
            while True:
                before = set(active)
                for name, job in jobs.items():
                    needs = job.get("needs", [])
                    needs = [needs] if isinstance(needs, str) else needs
                    if set(needs) <= active and guards[name].evaluate(values):
                        active.add(name)
                if before == active:
                    break
            self.assertEqual({"provenance_contracts", "raw_smoke"} if permitted(values)[0]
                             else {"provenance_contracts"}, active)
            for name in active:
                permissions = jobs[name].get("permissions", main["permissions"])
                self.assertTrue(all(value == "read" for value in permissions.values()))
                self.assertNotIn("environment", jobs[name])
                self.assertNotIn("secrets", jobs[name])

    def test_raw_smoke_rejects_direct_other_callers_and_ambiguous_target(self):
        values = context("raw-smoke")
        b.authorize(environment(values), SHA, "")
        for caller in ("build-only.yml", "raw-smoke.yml", "other.yml"):
            with self.subTest(caller=caller), self.assertRaises(ValueError):
                b.authorize(environment(values, caller), SHA, "")
        guard = Expression(workflow("main.yml")["jobs"]["raw_smoke"]["if"])
        for target in ("linux", "windows", "macos", "", None):
            changed = {**values, "inputs.target": target}
            self.assertFalse(guard.evaluate(changed))
            with self.assertRaises(ValueError):
                b.authorize(environment(changed), SHA, "")

    def test_raw_smoke_workflow_has_fixed_hosts_and_step_scoped_download_token(self):
        main = workflow("main.yml")["jobs"]["raw_smoke"]
        self.assertEqual("./.github/workflows/raw-smoke.yml", main["uses"])
        self.assertEqual({"contents": "read", "actions": "read"}, main["permissions"])
        self.assertNotIn("secrets", main)
        child = workflow("raw-smoke.yml")
        self.assertEqual({"workflow_call"}, set(child["on"]))
        self.assertNotIn("secrets", child["on"]["workflow_call"])
        self.assertEqual({"contents": "read", "actions": "read"}, child["permissions"])
        job = child["jobs"]["probe"]
        matrix = job["strategy"]["matrix"]["include"]
        self.assertEqual({("windows-x64", "windows-2022"), ("macos-x86_64", "macos-15-intel"),
                          ("macos-arm64", "macos-15")}, {(x["row"], x["runner"]) for x in matrix})
        token_steps = [step for step in job["steps"] if "GH_TOKEN" in step.get("env", {})]
        self.assertEqual(["Download approved producer artifacts"], [step["name"] for step in token_steps])
        self.assertNotIn("GH_TOKEN", child["env"])
        for step in job["steps"]:
            if step.get("uses", "").startswith("actions/checkout@"):
                self.assertEqual("false", step["with"]["persist-credentials"])
                self.assertEqual("0", step["with"]["fetch-depth"])
            if "uses" in step:
                self.assertRegex(step["uses"], r"^actions/[\w-]+@[0-9a-f]{40}$")


if __name__ == "__main__":
    unittest.main()
