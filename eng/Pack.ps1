[CmdletBinding()]
param(
    [Parameter(Mandatory)][string] $Version,
    [Parameter(Mandatory)][string] $SourceArchive,
    [Parameter(Mandatory)][string] $OutputDirectory
)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$SourceArchive = [IO.Path]::GetFullPath($SourceArchive)
$OutputDirectory = [IO.Path]::GetFullPath($OutputDirectory)
if (Test-Path $OutputDirectory) {
    throw "Use a new output directory; existing package identities are never overwritten."
}
Push-Location $root
try {
    $commit = git rev-parse HEAD
    if ($LASTEXITCODE -ne 0) { throw 'Cannot identify Uno ICU source' }
    python eng/provenance.py fetch-source --archive $SourceArchive
    if ($LASTEXITCODE -ne 0) { throw 'ICU source verification failed' }
    python eng/provenance.py fetch-tool --path artifacts/tools/nuget-6.14.0.exe
    if ($LASTEXITCODE -ne 0) { throw 'NuGet tool verification failed' }
    python eng/provenance.py prepare --archive $SourceArchive --version $Version
    if ($LASTEXITCODE -ne 0) { throw 'Package provenance preparation failed' }
    New-Item -ItemType Directory -Path $OutputDirectory | Out-Null
    foreach ($id in @('wasm', 'macos', 'ios', 'tvos', 'win')) {
        & artifacts/tools/nuget-6.14.0.exe pack "nuget/uno.icu-$id/uno.icu-$id.nuspec" -Version $Version -Properties "RepositoryCommit=$commit" -OutputDirectory $OutputDirectory -NonInteractive
        if ($LASTEXITCODE -ne 0) { throw "NuGet pack failed: $id" }
    }
    python eng/provenance.py inventory --directory $OutputDirectory --version $Version --commit $commit --phase unsigned --output "$OutputDirectory/unsigned-packages.json"
    if ($LASTEXITCODE -ne 0) { throw 'Unsigned package validation failed' }
}
finally {
    Pop-Location
}
