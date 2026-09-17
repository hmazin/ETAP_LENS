$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$receipt = Join-Path $root 'cache\report-worker-process.json'
if (-not (Test-Path -LiteralPath $receipt)) { Write-Output 'No report worker process is recorded.'; exit 0 }
$record = Get-Content -LiteralPath $receipt -Raw | ConvertFrom-Json
$process = Get-CimInstance Win32_Process -Filter "ProcessId=$($record.pid)" -ErrorAction SilentlyContinue
$script = Join-Path $PSScriptRoot 'report_worker.py'
if ($process -and $process.CommandLine.Contains($script)) {
    Stop-Process -Id $process.ProcessId
    Write-Output 'Report worker stopped. An interrupted report will be recovered through its lease.'
} else { Write-Output 'The recorded report worker is no longer running.' }
