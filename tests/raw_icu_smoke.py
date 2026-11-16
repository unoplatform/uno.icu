"""Bind unsigned same-run build rows, then execute existing ICU cases in a fresh worker.

No downloads, package construction, signing, attestation or workflow dispatch.
Origin fields and manifest digests must be selected by the evidence owner.
"""
import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "eng"))
import build_only as b
import icu_smoke as smoke
import provenance as p

ROWS = {"windows-x64": ("windows", "x64"), "macos-x86_64": ("macos", "x86_64"),
        "macos-arm64": ("macos", "arm64")}


def read_json(data):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON field: " + key)
            result[key] = value
        return result
    result = json.loads(data, object_pairs_hook=unique)
    if not isinstance(result, dict):
        raise ValueError("Expected a JSON object")
    return result


def digest_valid(value):
    return isinstance(value, str) and re.fullmatch("[0-9a-f]{64}", value) is not None


@dataclass(frozen=True)
class Origin:
    commit: str
    run_id: str
    attempt: str
    workflow_ref: str

    def __post_init__(self):
        if not re.fullmatch("[0-9a-f]{40}", self.commit):
            raise ValueError("A complete producer commit is required")
        if not all(re.fullmatch("[1-9][0-9]*", value) for value in (self.run_id, self.attempt)):
            raise ValueError("Explicit producer run ID and attempt are required")
        if not re.fullmatch(r"unoplatform/uno\.icu/\.github/workflows/(main|build-only)\.yml@refs/heads/\S+",
                            self.workflow_ref):
            raise ValueError("Select a known producer workflow and exact branch ref")

    def build_fields(self):
        return {"GITHUB_REPOSITORY": "unoplatform/uno.icu", "GITHUB_SHA": self.commit,
                "WORKFLOW_SHA": self.commit, "GITHUB_RUN_ID": self.run_id,
                "GITHUB_RUN_ATTEMPT": self.attempt, "GITHUB_WORKFLOW_REF": self.workflow_ref}


def git_snapshot(commit):
    """Read an existing Git commit object, not current files or a moving remote."""
    if p.git("rev-parse", f"{commit}^{{commit}}") != commit:
        raise ValueError("Producer commit object is unavailable")
    tree = subprocess.check_output(["git", "-C", str(p.ROOT), "ls-tree", "-r", "-z", commit])
    blobs = {}
    for entry in tree.split(b"\0"):
        if not entry:
            continue
        metadata, name = entry.split(b"\t", 1)
        mode, kind, object_id = metadata.split()
        if mode not in (b"100644", b"100755") or kind != b"blob":
            raise ValueError("Producer source must contain ordinary tracked files")
        blobs[name.decode("utf-8")] = subprocess.check_output(
            ["git", "-C", str(p.ROOT), "cat-file", "blob", object_id.decode("ascii")])
    return blobs


class ProducerSource:
    def __init__(self, commit, blobs):
        self.commit, self.blobs = commit, blobs
        self.hashes = {name: p.sha(data) for name, data in blobs.items()}
        self.lock = read_json(blobs["eng/source-lock.json"])
        self.images = read_json(blobs["eng/build-images.json"])
        if self.lock["upstreamVersion"] != "77.1":
            raise ValueError("These smoke assertions require ICU 77.1")


def row_identity(row):
    if row not in ROWS:
        raise ValueError("Only Windows x64 and macOS thin rows are supported")
    return ROWS[row]


def check_thin_macos(data, row):
    cpu = {"macos-x86_64": 0x1000007, "macos-arm64": 0x100000C}[row]
    if len(data) < 32 or data[:4] != b"\xcf\xfa\xed\xfe" or struct.unpack_from("<I", data, 4)[0] != cpu:
        raise ValueError("Expected a matching thin Mach-O library, not a fat/other-CPU file")


def reject_links(directory):
    for path in (directory, *directory.rglob("*")):
        if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
            raise ValueError("Links/junctions are not accepted as raw build inputs")


def verify_bundle(directory, digest, kind, variant, origin, source):
    reject_links(directory)
    raw = (directory / "artifact.json").read_bytes()
    if not digest_valid(digest) or p.sha(raw) != digest:
        raise ValueError("Owner-selected manifest SHA-256 mismatch")
    manifest = read_json(raw)
    if (manifest["schemaVersion"] != 1 or manifest["kind"] != kind or manifest["variant"] != variant or
            manifest["emscriptenVersion"] != "" or manifest["unoCommit"] != origin.commit or
            source.commit != origin.commit or manifest["upstream"] != source.lock or
            manifest["images"] != source.images):
        raise ValueError("Producer source/row identity mismatch")
    if any(manifest.get(flag) is not False for flag in ("runtimeTested", "authenticatedAttestation", "shippingPackage")):
        raise ValueError("Expected unsigned, unprobed build rows, not a claimed attestation/package")
    if any(manifest["build"].get(key) != value for key, value in origin.build_fields().items()):
        raise ValueError("Producer run/attempt/workflow mismatch")
    if manifest["gitBlobSha256"] != source.hashes:
        raise ValueError("Producer Git source bytes mismatch")
    checkout = manifest["inputSha256"]
    if set(checkout) != set(source.hashes) or not all(digest_valid(value) for value in checkout.values()):
        raise ValueError("Incomplete producer checkout inventory")
    if manifest["files"] != b.file_hashes(directory):
        raise ValueError("Raw build inventory mismatch")
    b.validate_payloads(directory, kind, variant)
    icu = (directory / "licenses/ICU-LICENSE.txt").read_bytes()
    if p.sha(icu) != source.lock["licenseSha256"]:
        raise ValueError("Incomplete ICU notice")
    for name, expected in (
        ("licenses/Uno-LICENSE.md", source.blobs["LICENSE.md"]),
        ("NOTICE.md", source.blobs["eng/NOTICE.md"]),
        ("LICENSE.txt", icu + b"\n\n" + source.blobs["LICENSE.md"]),
    ):
        if (directory / name).read_bytes() != expected:
            raise ValueError("Complete notice mismatch: " + name)
    if kind == "source":
        p.verify_source(directory / "icu-source.zip", source.lock)
    return manifest


def prepare(directory, row, origin, source, bundles, digests):
    kind, variant = row_identity(row)
    if set(bundles) != {"source", "data", "native"} or set(digests) != set(bundles):
        raise ValueError("Exactly source, data and native bundles/digests are required")
    manifests = {
        name: verify_bundle(bundles[name], digests[name], k, v, origin, source)
        for name, k, v in (("source", "source", ""), ("data", "data", ""), ("native", kind, variant))
    }
    selected = {}
    for name, k, v in (("data", "data", ""), ("native", kind, variant)):
        for relative in b.expected_payloads(k, v):
            data = (bundles[name] / "payload" / relative).read_bytes()
            if p.sha(data) != manifests[name]["files"]["payload/" + relative]:
                raise ValueError("Raw input changed during selection")
            if kind == "macos" and name == "native":
                check_thin_macos(data, row)
            selected[Path(relative).name] = data
    selected["filters.json"] = source.blobs["src/cldr_data/filters.json"]
    if set(selected) != smoke.raw_file_names(row):
        raise ValueError("Incomplete selected raw input shape")
    directory.mkdir(parents=True, exist_ok=False, mode=0o700)
    prepared = {name: p.sha(data) for name, data in selected.items()}
    for name, data in selected.items():
        p.write_new(directory / "runtime" / name, data)
    for name in ("source", "data", "native"):
        data = (bundles[name] / "artifact.json").read_bytes()
        if p.sha(data) != digests[name]:
            raise ValueError("Manifest changed during selection")
        p.write_new(directory / "evidence" / (name + ".artifact.json"), data)
    notices = {
        "ICU-LICENSE.txt": (bundles["source"] / "licenses/ICU-LICENSE.txt").read_bytes(),
        "Uno-LICENSE.md": source.blobs["LICENSE.md"],
        "NOTICE.md": source.blobs["eng/NOTICE.md"],
    }
    if p.sha(notices["ICU-LICENSE.txt"]) != source.lock["licenseSha256"]:
        raise ValueError("ICU notice changed during selection")
    notices["LICENSE.txt"] = notices["ICU-LICENSE.txt"] + b"\n\n" + notices["Uno-LICENSE.md"]
    for name, data in notices.items():
        p.write_new(directory / "notices" / name, data)
    binding = {
        "schemaVersion": 1, "row": row, "producerCommit": origin.commit,
        "producer": origin.build_fields(), "upstream": source.lock,
        "gitBlobSha256": source.hashes, "inputManifestSha256": dict(digests),
        "preparedFiles": prepared, "noticeSha256": {name: p.sha(data) for name, data in notices.items()},
        "runtimeTested": False, "authenticatedAttestation": False, "shippingPackage": False,
        "qualification": "Owner-selected unsigned manifests and Git/source archive bytes were compared; producer execution/protected identity is not independently authenticated",
        "sourceArchiveRetention": "Retain the original source bundle containing icu-source.zip alongside this binding",
        "producerBundleRetention": "Retain all three original bundles, including tool/build logs; copied manifests alone do not retain every producer evidence file",
    }
    p.write_new(directory / "input-binding.json", p.json_bytes(binding))
    p.write_new(directory / "prepared-inputs.json", p.json_bytes({
        "schemaVersion": 1, "row": row, "files": prepared}))
    return binding


def hash_files(directory):
    reject_links(directory)
    return {path.relative_to(directory).as_posix(): p.sha(path.read_bytes())
            for path in directory.rglob("*") if path.is_file()}


def verify_prepared(directory, binding):
    reject_links(directory)
    if (directory / "input-binding.json").read_bytes() != p.json_bytes(binding):
        raise ValueError("Prepared source binding changed")
    expected = p.json_bytes({"schemaVersion": 1, "row": binding["row"], "files": binding["preparedFiles"]})
    if (directory / "prepared-inputs.json").read_bytes() != expected:
        raise ValueError("Prepared worker binding changed")
    if hash_files(directory / "notices") != binding["noticeSha256"]:
        raise ValueError("Retained complete notices changed")
    expected_manifests = {name + ".artifact.json": digest for name, digest in binding["inputManifestSha256"].items()}
    if hash_files(directory / "evidence") != expected_manifests:
        raise ValueError("Retained producer manifest changed")
    smoke.check_raw_files(directory / "runtime", binding["row"], binding["preparedFiles"])


def worker_environment():
    # The ICU worker needs no GitHub, signing, cloud or package credentials,
    # Python search overrides, or native loader injection variables.
    allowed = {"SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "TEMP", "TMP", "TMPDIR",
               "LANG", "LC_ALL", "LC_CTYPE", "PROCESSOR_ARCHITECTURE", "PROCESSOR_ARCHITEW6432"}
    return {key: value for key, value in os.environ.items() if key.upper() in allowed}


def verify_case_results(probe, locales):
    expected_scripts = {"U+0041": "Latin", "U+0639": "Arabic", "U+05D0": "Hebrew",
                        "U+0915": "Devanagari", "U+4E2D": "Han", "U+10400": "Deseret"}
    breaks = probe.get("lineBreakCases")
    expected_locales = {"en", "ar", "he", "hi", "th", "zh_Hans", "ja", "km"}
    if (probe.get("cultures") != [{"requested": locale, "actual": locale} for locale in locales] or
            probe.get("scripts") != expected_scripts or probe.get("bidiDirection") != 2 or
            not isinstance(breaks, dict) or set(breaks) != expected_locales):
        raise ValueError("Incomplete or contradictory worker case results")
    for positions in breaks.values():
        if (not isinstance(positions, list) or len(positions) < 3 or positions[0] != 0 or
                any(type(value) is not int for value in positions) or positions != sorted(set(positions))):
            raise ValueError("Incomplete worker line-break case results")


def execute(directory, binding):
    """Always a fresh worker; failure/timeout leaves no successful runtime report."""
    verify_prepared(directory, binding)
    prepared = directory / "prepared-inputs.json"
    prepared_hash = p.sha(prepared.read_bytes())
    output = directory / "worker-result.json"
    if output.exists():
        raise FileExistsError("Use a fresh worker output, not a previous result")
    command = [sys.executable, "-I", "-B", "-X", "utf8", str(Path(__file__).with_name("icu_smoke.py")),
               "--raw-inputs", str(prepared), "--expected-sha256", prepared_hash, "--output", str(output)]
    p.write_new(directory / "worker-command.json", p.json_bytes({"command": command, "timeoutSeconds": 120}))
    env = worker_environment()
    with (directory / "worker.log").open("xb") as log:
        result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, env=env, timeout=120)
    p.write_new(directory / "worker-exit.json", p.json_bytes({"exitCode": result.returncode}))
    result.check_returncode()
    verify_prepared(directory, binding)
    probe = read_json(output.read_bytes())
    if (probe["preparedInputsSha256"] != prepared_hash or probe["inputSha256"] != binding["preparedFiles"] or
            probe["row"] != binding["row"] or probe["nativeVersion"] != [77, 1, 0, 0] or
            probe["dataSha256"] != binding["preparedFiles"]["icudt.dat"] or probe["cultureCount"] != 62):
        raise ValueError("Worker result does not bind the selected raw inputs")
    locales = read_json((directory / "runtime/filters.json").read_bytes())["localeFilter"]["whitelist"]
    verify_case_results(probe, locales)
    return probe


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--row", required=True, choices=tuple(ROWS))
    parser.add_argument("--producer-commit", required=True)
    parser.add_argument("--producer-run-id", required=True)
    parser.add_argument("--producer-run-attempt", required=True)
    parser.add_argument("--producer-workflow-ref", required=True)
    for name in ("source", "data", "native"):
        parser.add_argument("--" + name + "-bundle", type=Path, required=True)
        parser.add_argument("--" + name + "-manifest-sha256", required=True)
    parser.add_argument("--work-directory", type=Path, required=True)
    args = parser.parse_args()
    smoke.require_raw_host(args.row)
    if p.git("status", "--porcelain"):
        raise ValueError("Use a clean committed probe checkout")
    probe_commit = p.git("rev-parse", "HEAD")
    origin = Origin(args.producer_commit, args.producer_run_id, args.producer_run_attempt, args.producer_workflow_ref)
    source = ProducerSource(origin.commit, git_snapshot(origin.commit))
    bundles = {name: getattr(args, name + "_bundle").absolute() for name in ("source", "data", "native")}
    digests = {name: getattr(args, name + "_manifest_sha256") for name in bundles}
    directory = args.work_directory.absolute()
    binding = prepare(directory, args.row, origin, source, bundles, digests)
    probe = execute(directory, binding)
    if p.git("rev-parse", "HEAD") != probe_commit or p.git("status", "--porcelain"):
        raise ValueError("Probe source changed during execution")
    p.write_new(directory / "probe-report.json", p.json_bytes({
        "schemaVersion": 1, "status": "passed", "runtimeTested": True,
        "producerCommit": origin.commit, "probeCommit": probe_commit,
        "inputBindingSha256": p.sha((directory / "input-binding.json").read_bytes()),
        "probe": probe, "files": hash_files(directory),
        "authenticatedAttestation": False, "signatureVerified": False, "shippingPackage": False,
        "historicalHashBindingEstablished": False,
        "notCovered": ["WASM execution", "Windows ARM64 runtime", "macOS universal binary execution",
                       "iOS/tvOS runtime", "GUI/physical AT", "full ICU conformance"],
        "concurrencyQualification": "Private copies and pre/post byte checks, not exclusive POSIX writer protection or a hostile-process lifetime proof",
    }))
    print("PASS: raw ICU/data cases executed; separate unsigned probe report retained")


if __name__ == "__main__":
    main()
