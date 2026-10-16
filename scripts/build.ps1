$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $taskRoot
try {
    $taskTestDirectory = Join-Path $taskRoot ('artifacts\build-tests-' + [guid]::NewGuid().ToString('N'))
    if (Test-Path -LiteralPath $taskTestDirectory) { throw 'Test directory unexpectedly exists.' }
    & .\.venv\Scripts\python.exe -X utf8 -m pytest -q -p no:cacheprovider --basetemp $taskTestDirectory
    if ($LASTEXITCODE -ne 0) { throw 'Tests failed.' }
    & .\.venv\Scripts\python.exe -X utf8 -m PyInstaller --noconfirm --clean Saywell.spec
    if ($LASTEXITCODE -ne 0) { throw 'Build failed.' }
    Copy-Item -LiteralPath 'docs\USER_GUIDE.md' -Destination 'dist\Saywell\使用说明.md'
    Copy-Item -LiteralPath 'THIRD_PARTY_NOTICES.md' -Destination 'dist\Saywell\THIRD_PARTY_NOTICES.md'
    Copy-Item -LiteralPath 'LICENSE' -Destination 'dist\Saywell\LICENSE.txt'
    & .\.venv\Scripts\python.exe -X utf8 scripts/collect_licenses.py
    Write-Output 'Built dist\Saywell\Saywell.exe'
} finally {
    Pop-Location
}
