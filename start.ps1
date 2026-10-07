param([switch]$ImportData)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$taskPython = Join-Path $PSScriptRoot '.runtime/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) {
    $taskLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($taskLauncher) {
        & $taskLauncher.Source -3 -m venv .runtime
    } else {
        $taskLauncher = Get-Command python -ErrorAction SilentlyContinue
        if (-not $taskLauncher) { throw "Installer Python 3.11 ou 3.12 puis relancer start.ps1." }
        & $taskLauncher.Source -m venv .runtime
    }
    if ($LASTEXITCODE -ne 0) { throw "Impossible de creer le venv Python." }
}
& $taskPython -c 'import fastapi, uvicorn, pandas, httpx, openpyxl' 2>$null
if ($LASTEXITCODE -ne 0) {
    & $taskPython -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw "Installation des dependances echouee." }
}
if ($ImportData -or -not (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'Backend/bos.db'))) {
    & $taskPython -m Backend.etl
    if ($LASTEXITCODE -ne 0) { throw "Import des donnees echoue." }
}
Write-Host 'Copilote : http://127.0.0.1:8000 - API : http://127.0.0.1:8000/docs'
& $taskPython -m uvicorn Backend.app:app --host 127.0.0.1 --port 8000
