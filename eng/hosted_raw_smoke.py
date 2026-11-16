"""Read-only hosted raw-smoke follow-up for an explicitly reviewed producer plan."""
import argparse
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import zipfile

import build_only as b
import provenance as p

sys.path.insert(0, str(p.ROOT / "tests"))
import raw_icu_smoke as raw

PLAN_PATH = p.ROOT / "eng/raw-smoke-plan.json"
ARTIFACT_ROLES = ("source", "data", "windows-x64", "macos-x86_64", "macos-arm64")
ROWS = ("windows-x64", "macos-x86_64", "macos-arm64")
SUCCESS_JOBS = {
    "provenance_contracts", "build_only / authorize", "build_only / data",
    "build_only / windows (x64)", "build_only / windows (arm64)",
    "build_only / macos (x86_64, macos-15-intel)", "build_only / macos (arm64, macos-15)",
    "build_only / macos_universal", "build_only / staging",
    *(f"build_only / wasm ({version}, {variant})" for version in ("3.1.56", "5.0.6")
      for variant in ("st", "mt", "st,simd", "mt,simd")),
}
SKIPPED_JOBS = {
    "release_authorization", "build_unoicu", "build_icudt", "build_libicu_windows", "build_libicu_osx",
    "build_libicu_ios", "build_libicu_iossim", "build_libicu_iossim_universal", "build_libicu_osx_universal",
    "build_libicu_tvos", "build_libicu_tvossim", "build_libicu_tvossim_universal",
    "package", "macos_smoke", "Sign Package", "Publish Dev", "Publish Production",
}


def exact_keys(value, keys, label):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ValueError("Unexpected/missing " + label + " fields")


def positive_integer(value):
    return type(value) is int and value > 0


def parse_plan(data):
    plan = raw.read_json(data)
    exact_keys(plan, ("schemaVersion", "producer", "artifacts", "jobs"), "plan")
    if type(plan["schemaVersion"]) is not int or plan["schemaVersion"] != 1:
        raise ValueError("Unsupported plan schema")
    producer = plan["producer"]
    exact_keys(producer, ("repository", "repositoryId", "commit", "runId", "runAttempt", "branch",
                          "workflowPath", "event"), "producer")
    if (producer["repository"] != "unoplatform/uno.icu" or producer["workflowPath"] != ".github/workflows/main.yml" or
            producer["event"] != "workflow_dispatch" or
            not all(positive_integer(producer[key]) for key in ("repositoryId", "runId", "runAttempt")) or
            not isinstance(producer["commit"], str) or not re.fullmatch("[0-9a-f]{40}", producer["commit"]) or
            not isinstance(producer["branch"], str) or
            not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", producer["branch"]) or
            any(part in producer["branch"] for part in ("..", "//", ".lock")) or
            producer["branch"].endswith(("/", "."))):
        raise ValueError("Invalid producer identity")
    exact_keys(plan["artifacts"], ARTIFACT_ROLES, "artifact roles")
    ids = set()
    for role, artifact in plan["artifacts"].items():
        exact_keys(artifact, ("id", "name", "zipBytes", "zipSha256", "manifestSha256"), "artifact")
        if (not positive_integer(artifact["id"]) or artifact["id"] in ids or
                artifact["name"] != "build-only-" + role or not positive_integer(artifact["zipBytes"]) or
                not raw.digest_valid(artifact["zipSha256"]) or not raw.digest_valid(artifact["manifestSha256"])):
            raise ValueError("Invalid/ambiguous artifact identity")
        ids.add(artifact["id"])
    jobs = plan["jobs"]
    if (not isinstance(jobs, dict) or not SUCCESS_JOBS | SKIPPED_JOBS <= set(jobs) or
            set(jobs) - SUCCESS_JOBS - SKIPPED_JOBS - {"raw_smoke"}):
        raise ValueError("Plan must bind the complete known producer job graph")
    job_ids = set()
    for name, job in jobs.items():
        exact_keys(job, ("id", "conclusion"), "job")
        expected = "success" if name in SUCCESS_JOBS else "skipped"
        if not positive_integer(job["id"]) or job["id"] in job_ids or job["conclusion"] != expected:
            raise ValueError("Invalid producer job identity/conclusion")
        job_ids.add(job["id"])
    return plan


def origin(plan):
    producer = plan["producer"]
    return raw.Origin(producer["commit"], str(producer["runId"]), str(producer["runAttempt"]),
                      f'{producer["repository"]}/{producer["workflowPath"]}@refs/heads/{producer["branch"]}')


def verify_run(plan, result):
    producer = plan["producer"]
    expected = {"id": producer["runId"], "run_attempt": producer["runAttempt"], "head_sha": producer["commit"],
                "head_branch": producer["branch"], "path": producer["workflowPath"], "event": producer["event"],
                "status": "completed", "conclusion": "success"}
    if any(result.get(key) != value for key, value in expected.items()):
        raise ValueError("Producer run/attempt/source/event is not the completed approved execution")
    for key in ("repository", "head_repository"):
        repository = result.get(key, {})
        if repository.get("id") != producer["repositoryId"] or repository.get("full_name") != producer["repository"]:
            raise ValueError("Producer repository mismatch")


def verify_jobs(plan, response):
    jobs = response.get("jobs", [])
    if response.get("total_count") != len(plan["jobs"]) or len(jobs) != len(plan["jobs"]):
        raise ValueError("Incomplete producer job results")
    seen = set()
    for job in jobs:
        name = job.get("name")
        if name not in plan["jobs"] or name in seen:
            raise ValueError("Unexpected/duplicate producer job")
        expected = plan["jobs"][name]
        if (job.get("id") != expected["id"] or job.get("status") != "completed" or
                job.get("conclusion") != expected["conclusion"]):
            raise ValueError("Producer job did not complete as approved: " + name)
        seen.add(name)


def verify_artifact(plan, role, actual):
    expected, producer = plan["artifacts"][role], plan["producer"]
    if any(actual.get(key) != value for key, value in {
            "id": expected["id"], "name": expected["name"], "size_in_bytes": expected["zipBytes"],
            "digest": "sha256:" + expected["zipSha256"]}.items()) or actual.get("expired") is not False:
        raise ValueError("Artifact ID/name/size/digest mismatch: " + role)
    run = actual.get("workflow_run", {})
    for key, value in {"id": producer["runId"], "head_sha": producer["commit"], "head_branch": producer["branch"],
                       "repository_id": producer["repositoryId"], "head_repository_id": producer["repositoryId"]}.items():
        if run.get(key) != value:
            raise ValueError("Artifact belongs to another producer: " + role)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def artifact_location(url):
    parsed = urllib.parse.urlsplit(url)
    host = parsed.hostname or ""
    allowed = (host.endswith(".blob.core.windows.net") or host == "objects.githubusercontent.com" or
               host.endswith(".actions.githubusercontent.com"))
    if (parsed.scheme != "https" or parsed.username or parsed.password or parsed.fragment or
            parsed.port not in (None, 443) or not allowed):
        raise ValueError("Unapproved artifact redirect host/scheme")
    return url


class GithubReadOnly:
    def __init__(self, token, opener=None):
        if not token:
            raise ValueError("The artifact-download step requires its scoped GH_TOKEN")
        self.token = token
        self.opener = opener or urllib.request.build_opener(NoRedirect())

    def api_request(self, path):
        if not path.startswith("repos/unoplatform/uno.icu/") or ".." in path:
            raise ValueError("Only this repository's GitHub API is permitted")
        return urllib.request.Request("https://api.github.com/" + path, headers={
            "Authorization": "Bearer " + self.token, "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "uno-icu-raw-smoke"})

    def get_json(self, path):
        try:
            with self.opener.open(self.api_request(path), timeout=60) as response:
                return raw.read_json(response.read())
        except urllib.error.HTTPError as error:
            code = error.code
            error.close()
            raise RuntimeError(f"Read-only GitHub metadata request failed: HTTP {code}") from None

    def download(self, artifact, destination):
        # The credential is sent only to api.github.com. No automatic redirect
        # carries it: storage requests are constructed afresh without auth.
        path = f"repos/unoplatform/uno.icu/actions/artifacts/{artifact['id']}/zip"
        try:
            response = self.opener.open(self.api_request(path), timeout=60)
        except urllib.error.HTTPError as error:
            code, location = error.code, error.headers.get("Location")
            error.close()
            if code != 302 or not location:
                raise RuntimeError(f"Artifact redirect request failed: HTTP {code}") from None
        else:
            response.close()
            raise ValueError("Expected the documented GitHub artifact redirect")
        location = artifact_location(location)
        for _ in range(3):
            request = urllib.request.Request(location, headers={"User-Agent": "uno-icu-raw-smoke"})
            try:
                response = self.opener.open(request, timeout=120)
                break
            except urllib.error.HTTPError as error:
                code, target = error.code, error.headers.get("Location")
                error.close()
                if code not in (301, 302, 303, 307, 308) or not target:
                    raise RuntimeError(f"Artifact storage request failed: HTTP {code}") from None
                location = artifact_location(urllib.parse.urljoin(location, target))
        else:
            raise ValueError("Artifact redirect limit exceeded")
        checksum, count = hashlib.sha256(), 0
        with response, destination.open("xb") as stream:
            for block in iter(lambda: response.read(1024 * 1024), b""):
                count += len(block)
                if count > artifact["zipBytes"]:
                    raise ValueError("Artifact exceeds approved archive length")
                checksum.update(block)
                stream.write(block)
        if count != artifact["zipBytes"] or checksum.hexdigest() != artifact["zipSha256"]:
            raise ValueError("Downloaded artifact SHA-256/length mismatch")


def file_digest(path):
    checksum = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            checksum.update(block)
    return checksum.hexdigest()


def extract_archive(archive, directory):
    with zipfile.ZipFile(archive) as zipped:
        names = set()
        for entry in zipped.infolist():
            # ZipInfo normalizes backslashes on Windows and truncates NULs.
            # Inspect the original name before accepting a portable file path.
            name = entry.orig_filename
            path = PurePosixPath(name)
            kind = stat.S_IFMT(entry.external_attr >> 16)
            reserved = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$",
                        *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
            if (not name or name != entry.filename or path.is_absolute() or
                    name != path.as_posix() + ("/" if entry.is_dir() else "") or
                    any(part.endswith((" ", ".")) or part.split(".")[0].upper() in reserved for part in path.parts) or
                    any(x in (".", "..") for x in name.split("/")) or
                    "\\" in name or ":" in name or "\0" in name or name.casefold() in names or
                    kind not in (0, stat.S_IFREG, stat.S_IFDIR) or
                    (kind == stat.S_IFDIR and not entry.is_dir()) or (kind == stat.S_IFREG and entry.is_dir())):
                raise ValueError("Unsafe/nonordinary/colliding artifact ZIP entry")
            names.add(name.casefold())
        directory.mkdir(parents=True, exist_ok=False)
        for entry in zipped.infolist():
            target = directory.joinpath(*PurePosixPath(entry.filename).parts)
            if entry.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                p.write_new(target, zipped.read(entry))


def row_paths(plan, row, output):
    if row not in ROWS:
        raise ValueError("Unsupported raw-smoke architecture")
    return {role: output / "producer/bundles" / plan["artifacts"][key]["name"]
            for role, key in (("source", "source"), ("data", "data"), ("native", row))}


def verify_retained(plan, row, output):
    metadata = raw.read_json((output / "producer-metadata.json").read_bytes())
    exact_keys(metadata, ("run", "jobs", "artifacts"), "retained API metadata")
    verify_run(plan, metadata["run"])
    verify_jobs(plan, metadata["jobs"])
    exact_keys(metadata["artifacts"], ARTIFACT_ROLES, "retained artifact metadata")
    for role in ARTIFACT_ROLES:
        verify_artifact(plan, role, metadata["artifacts"][role])
    source = raw.ProducerSource(plan["producer"]["commit"], raw.git_snapshot(plan["producer"]["commit"]))
    paths = row_paths(plan, row, output)
    for role, key in (("source", "source"), ("data", "data"), ("native", row)):
        artifact = plan["artifacts"][key]
        archive = output / "producer/archives" / (artifact["name"] + ".zip")
        if archive.stat().st_size != artifact["zipBytes"] or file_digest(archive) != artifact["zipSha256"]:
            raise ValueError("Retained original archive changed")
        kind, variant = (role, "") if role != "native" else raw.row_identity(row)
        raw.verify_bundle(paths[role], artifact["manifestSha256"], kind, variant, origin(plan), source)
        if kind == "macos":
            for name in b.expected_payloads(kind, variant):
                raw.check_thin_macos((paths[role] / "payload" / name).read_bytes(), row)
    return paths


def download_inputs(plan, row, output, client):
    row_paths(plan, row, output)
    output.mkdir(parents=True, exist_ok=False)
    producer = plan["producer"]
    prefix = f"repos/unoplatform/uno.icu/actions/runs/{producer['runId']}/attempts/{producer['runAttempt']}"
    metadata = {"run": client.get_json(prefix), "jobs": client.get_json(prefix + "/jobs?per_page=100"), "artifacts": {}}
    verify_run(plan, metadata["run"])
    verify_jobs(plan, metadata["jobs"])
    for role, artifact in plan["artifacts"].items():
        actual = client.get_json(f"repos/unoplatform/uno.icu/actions/artifacts/{artifact['id']}")
        verify_artifact(plan, role, actual)
        metadata["artifacts"][role] = actual
    p.write_new(output / "producer-metadata.json", p.json_bytes(metadata))
    p.write_new(output / "input-plan.json", p.json_bytes(plan))
    paths = row_paths(plan, row, output)
    for role, key in (("source", "source"), ("data", "data"), ("native", row)):
        artifact = plan["artifacts"][key]
        archive = output / "producer/archives" / (artifact["name"] + ".zip")
        archive.parent.mkdir(parents=True, exist_ok=True)
        client.download(artifact, archive)
        # Independent verification also applies to alternate/mock transports.
        if archive.stat().st_size != artifact["zipBytes"] or file_digest(archive) != artifact["zipSha256"]:
            raise ValueError("Artifact transport returned unapproved bytes")
        extract_archive(archive, paths[role])
    verify_retained(plan, row, output)
    p.write_new(output / "download-receipt.json", p.json_bytes({
        "schemaVersion": 1, "row": row, "planSha256": p.sha(p.json_bytes(plan)),
        "metadataSha256": file_digest(output / "producer-metadata.json"),
        "runtimeTested": False, "authenticatedAttestation": False}))


def execute_probe(plan, row, output):
    if os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN"):
        raise ValueError("Native probe step must not receive GitHub download credentials")
    if (output / "input-plan.json").read_bytes() != p.json_bytes(plan):
        raise ValueError("Retained plan substitution")
    receipt = raw.read_json((output / "download-receipt.json").read_bytes())
    exact_keys(receipt, ("schemaVersion", "row", "planSha256", "metadataSha256",
                         "runtimeTested", "authenticatedAttestation"), "download receipt")
    if (type(receipt["schemaVersion"]) is not int or receipt["schemaVersion"] != 1 or
            receipt["row"] != row or receipt["planSha256"] != p.sha(p.json_bytes(plan)) or
            receipt["metadataSha256"] != file_digest(output / "producer-metadata.json") or
            receipt["runtimeTested"] is not False or receipt["authenticatedAttestation"] is not False):
        raise ValueError("Download receipt mismatch")
    paths = verify_retained(plan, row, output)
    producer = plan["producer"]
    command = [sys.executable, p.ROOT / "tests/raw_icu_smoke.py", "--row", row,
               "--producer-commit", producer["commit"], "--producer-run-id", str(producer["runId"]),
               "--producer-run-attempt", str(producer["runAttempt"]), "--producer-workflow-ref", origin(plan).workflow_ref]
    for role, key in (("source", "source"), ("data", "data"), ("native", row)):
        command += ["--" + role + "-bundle", paths[role],
                    "--" + role + "-manifest-sha256", plan["artifacts"][key]["manifestSha256"]]
    command += ["--work-directory", output / "probe"]
    p.write_new(output / "probe-command.json", p.json_bytes({"command": [str(x) for x in command]}))
    with (output / "probe-launch.log").open("xb") as log:
        result = subprocess.run([str(x) for x in command], stdout=log, stderr=subprocess.STDOUT, timeout=180)
    p.write_new(output / "probe-exit.json", p.json_bytes({"exitCode": result.returncode}))
    result.check_returncode()
    verify_retained(plan, row, output)
    probe = raw.read_json((output / "probe/probe-report.json").read_bytes())
    if (probe["status"] != "passed" or probe["runtimeTested"] is not True or
            probe["producerCommit"] != producer["commit"] or probe["probeCommit"] != p.git("rev-parse", "HEAD")):
        raise ValueError("Raw probe result/source mismatch")
    b.check_authorization()
    p.write_new(output / "hosted-smoke-receipt.json", p.json_bytes({
        "schemaVersion": 1, "row": row, "planSha256": p.sha(p.json_bytes(plan)),
        "probeReportSha256": file_digest(output / "probe/probe-report.json"),
        "probeRun": {key: os.environ.get(key) for key in b.BUILD_KEYS},
        "producerCommit": producer["commit"], "runtimeTested": True,
        "authenticatedAttestation": False, "signatureVerified": False, "shippingPackage": False}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("authorize", "download", "run"))
    parser.add_argument("--row", choices=ROWS, required=True)
    args = parser.parse_args()
    b.check_authorization()
    if os.environ.get("BUILD_SCOPE") != "raw-smoke":
        raise ValueError("This entry only accepts the raw-smoke operation")
    raw.smoke.require_raw_host(args.row)
    plan = parse_plan(PLAN_PATH.read_bytes())
    output = p.ROOT / "artifacts/raw-smoke" / args.row
    if args.command == "authorize":
        # The producer must be an available commit, not an inferred current HEAD.
        raw.git_snapshot(plan["producer"]["commit"])
        print("Raw-smoke authorization, host and reviewed input-plan schema checked")
    elif args.command == "download":
        download_inputs(plan, args.row, output, GithubReadOnly(os.environ.get("GH_TOKEN")))
    else:
        execute_probe(plan, args.row, output)
    b.check_authorization()


if __name__ == "__main__":
    main()
