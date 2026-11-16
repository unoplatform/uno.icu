"""Small real NuGet file-license/commit contract. Creates no ICU binary package."""
import argparse
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "eng"))
import provenance as p


def run(archive, directory, nuget):
    p.verify_tool(nuget)
    directory.mkdir(parents=True, exist_ok=False)
    icu = p.verify_source(archive, p.load_lock())
    uno = (p.ROOT / "LICENSE.md").read_bytes()
    p.write_new(directory / "ICU-LICENSE.txt", icu)
    p.write_new(directory / "Uno-LICENSE.md", uno)
    p.write_new(directory / "LICENSE.txt", icu + b"\n\n" + uno)
    p.write_new(directory / "NOTICE.md", b"# Nonshipping NuGet notice contract fixture\n")
    commit = p.git("rev-parse", "HEAD")
    text = f"""<?xml version="1.0"?>
<package xmlns="{p.NS['n']}">
  <metadata>
    <id>Uno.Icu.Notice.ContractFixture</id><version>0.0.0</version>
    <authors>Uno Platform</authors><description>Nonshipping notice contract only</description>
    <license type="file">LICENSE.txt</license>
    <readme>NOTICE.md</readme>
    <repository type="git" url="{p.REPOSITORY}" commit="$RepositoryCommit$"/>
  </metadata>
  <files>
    <file src="LICENSE.txt" target=""/>
    <file src="ICU-LICENSE.txt" target="licenses"/>
    <file src="Uno-LICENSE.md" target="licenses"/>
    <file src="NOTICE.md" target=""/>
  </files>
</package>"""
    p.write_new(directory / "fixture.nuspec", text.encode())
    command = [str(nuget), "pack", str(directory / "fixture.nuspec"),
               "-OutputDirectory", str(directory), "-Properties", f"RepositoryCommit={commit}",
               "-NonInteractive"]
    result = subprocess.run(command, capture_output=True)
    evidence = {"command": command, "exitCode": result.returncode,
                "stdout": result.stdout.decode("utf-8", errors="replace"),
                "stderr": result.stderr.decode("utf-8", errors="replace"),
                "coverage": "NuGet file-license packaging/commit substitution only; no ICU native build"}
    p.write_new(directory / "pack-result.json", p.json_bytes(evidence))
    print(evidence["stdout"])
    print(evidence["stderr"])
    result.check_returncode()
    package = directory / "Uno.Icu.Notice.ContractFixture.0.0.0.nupkg"
    with zipfile.ZipFile(package) as source:
        tree = ET.fromstring(source.read("Uno.Icu.Notice.ContractFixture.nuspec"))
        metadata = tree.find("{*}metadata")
        assert metadata.findtext("{*}license") == "LICENSE.txt"
        assert metadata.find("{*}license").get("type") == "file"
        assert metadata.find("{*}repository").get("commit") == commit
        assert source.read("licenses/ICU-LICENSE.txt") == icu
        assert source.read("licenses/Uno-LICENSE.md") == uno
        assert source.read("LICENSE.txt") == icu + b"\n\n" + uno
    p.write_new(directory / "verified.json", p.json_bytes({
        "package": package.name, "sha256": p.sha(package.read_bytes()),
        "icuLicenseSha256": p.sha(icu), "unoLicenseSha256": p.sha(uno),
        "nuspecNamespace": tree.tag, "commit": commit,
        "shippingArtifact": False, "nativeBuild": False, "passed": True,
    }))
    print("PASS: actual NuGet file-license packaging, complete texts and commit substitution")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--work-directory", type=Path, required=True)
    parser.add_argument("--nuget", type=Path, required=True)
    args = parser.parse_args()
    run(args.archive.resolve(), args.work_directory.resolve(), args.nuget.resolve())
