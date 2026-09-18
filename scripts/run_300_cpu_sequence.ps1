param(
    [Parameter(Mandatory=$true)][string]$OutputDirectory,
    [Parameter(Mandatory=$true)][string]$ServerRecord
)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskOutput = (Resolve-Path -LiteralPath $OutputDirectory).Path
$taskRecordPath = (Resolve-Path -LiteralPath $ServerRecord).Path
$taskRecord = Get-Content -LiteralPath $taskRecordPath -Raw | ConvertFrom-Json
$taskListener = Get-NetTCPConnection -State Listen -LocalPort 11435 -ErrorAction Stop
$taskProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$($taskRecord.pid)"
if ($taskRecord.host -ne '127.0.0.1:11435' -or $taskListener.OwningProcess -ne $taskRecord.pid -or
    $taskProcess.ExecutablePath -ne $taskRecord.executable) { throw 'Owned server identity mismatch' }
if ((Invoke-RestMethod -Uri 'http://127.0.0.1:11435/api/ps').models.Count -ne 0) {
    throw 'Another model is still loaded; wait for its run to finish'
}
$env:OLLAMA_BASE_URL = 'http://127.0.0.1:11435'
$env:PYTHONIOENCODING = 'utf-8'
$taskPython = Join-Path $taskRoot '.venv/Scripts/python.exe'
$taskWrapper = Join-Path $PSScriptRoot 'run_300_guarded.py'
foreach ($taskModel in @('ax', 'qwen', 'bllossom')) {
    & $taskPython $taskWrapper --model $taskModel --num-gpu 0 `
        --output $taskOutput --server-record $taskRecordPath
    if ($LASTEXITCODE -ne 0) { throw "Stopped at $taskModel; records preserved for investigation" }
}
