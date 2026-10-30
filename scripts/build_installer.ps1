param([switch]$SkipBuild)
# Builds the portable app (unless -SkipBuild) and wraps it in an Inno Setup installer under release\.
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $taskRoot
try {
    $version = (Select-String -LiteralPath 'pyproject.toml' -Pattern '^version = "(.+)"').Matches[0].Groups[1].Value
    $packageVersion = (Select-String -LiteralPath 'src\saywell\__init__.py' -Pattern '__version__ = "(.+)"').Matches[0].Groups[1].Value
    if ($version -ne $packageVersion) { throw "Version mismatch: pyproject.toml $version, __init__.py $packageVersion." }
    $iscc = @(
        (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe'),
        (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe'),
        (Join-Path $env:ProgramFiles 'Inno Setup 6\ISCC.exe')
    ) | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    if (-not $iscc) { throw 'Inno Setup 6 not found. Install it with: winget install JRSoftware.InnoSetup' }
    if (-not $SkipBuild) {
        & .\scripts\build.ps1
    }
    $app = 'dist\Saywell'
    if (-not (Test-Path -LiteralPath "$app\Saywell.exe")) { throw 'Portable build is missing; run without -SkipBuild.' }
    # Models, keys and recordings must never ship inside the installer.
    $private = Get-ChildItem -LiteralPath $app -Recurse -File |
        Where-Object { $_.Extension -in '.onnx', '.gguf', '.wav', '.m4a', '.mp3' -or $_.Name -like '*key*.txt' }
    if ($private) { throw "Private or model files found in ${app}: $($private.FullName -join ', ')" }
    & $iscc /Q "/DAppVersion=$version" 'installer\Saywell.iss'
    if ($LASTEXITCODE -ne 0) { throw 'Installer build failed.' }
    $name = "Saywell-Setup-$version.exe"
    $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath "release\$name").Hash.ToLowerInvariant()
    Set-Content -LiteralPath "release\$name.sha256" -Value "$hash  $name" -Encoding ascii
    Write-Output "Built release\$name"
    Write-Output "SHA256 $hash"
    # Installed copies learn about the release from this signed file (src\saywell\updates.py).
    & .\.venv\Scripts\python.exe -X utf8 scripts\sign_update.py
    if ($LASTEXITCODE -ne 0) { throw 'Signing release\update.json failed.' }
} finally {
    Pop-Location
}
