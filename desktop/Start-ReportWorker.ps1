param([string]$Templates = (Join-Path $PSScriptRoot 'EtapCrystalReporter\Templates'))
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$cache = Join-Path $root 'cache'
$credentialPath = Join-Path $cache 'report-worker.credential.xml'
if (-not (Test-Path -LiteralPath $credentialPath)) { throw 'Run Configure-ReportWorker.ps1 first.' }
$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { $python = (Get-Command python -ErrorAction Stop).Source }
$script = Join-Path $PSScriptRoot 'report_worker.py'
$receipt = Join-Path $cache 'report-worker-process.json'
if (Test-Path -LiteralPath $receipt) {
    $previous = Get-Content -LiteralPath $receipt -Raw | ConvertFrom-Json
    $running = Get-CimInstance Win32_Process -Filter "ProcessId=$($previous.pid)" -ErrorAction SilentlyContinue
    if ($running -and $running.CommandLine -like '*report_worker.py*' -and $running.ExecutablePath -eq $python) {
        Write-Output "Report worker is already running (PID $($previous.pid))."
        exit 0
    }
}
$credential = Import-Clixml -LiteralPath $credentialPath
$oldApi = $env:ETAP_LENS_REPORT_API
$oldToken = $env:ETAP_LENS_REPORT_WORKER_TOKEN
try {
    $env:ETAP_LENS_REPORT_API = $credential.UserName
    $env:ETAP_LENS_REPORT_WORKER_TOKEN = $credential.GetNetworkCredential().Password
    $arguments = '"{0}" --templates "{1}"' -f $script, (Resolve-Path -LiteralPath $Templates).Path
    $process = Start-Process -FilePath $python -ArgumentList $arguments -WorkingDirectory $root -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $cache 'report-worker-stdout.log') -RedirectStandardError (Join-Path $cache 'report-worker-stderr.log')
    @{ pid = $process.Id; api = $credential.UserName; started = (Get-Date).ToUniversalTime().ToString('o') } | ConvertTo-Json | Set-Content -LiteralPath $receipt
    Write-Output "Report worker started (PID $($process.Id))."
} finally {
    $env:ETAP_LENS_REPORT_API = $oldApi
    $env:ETAP_LENS_REPORT_WORKER_TOKEN = $oldToken
}
