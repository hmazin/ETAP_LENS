param(
    [Parameter(Mandatory=$true)][string]$ApiUrl,
    [string]$TokenFile
)
$ErrorActionPreference = 'Stop'
$uri = [Uri]$ApiUrl
if ($uri.Scheme -ne 'https' -and $uri.Host -notin @('127.0.0.1', 'localhost')) { throw 'Use an HTTPS API URL.' }
$root = Split-Path $PSScriptRoot -Parent
$cache = Join-Path $root 'cache'
New-Item -ItemType Directory -Force -Path $cache | Out-Null
if ($TokenFile) {
    $secret = ConvertTo-SecureString -AsPlainText -Force ([IO.File]::ReadAllText((Resolve-Path -LiteralPath $TokenFile)).Trim())
} else {
    $secret = Read-Host 'Report worker token (from the API administrator)' -AsSecureString
}
if ($secret.Length -lt 32) { throw 'The worker token must contain at least 32 characters.' }
$credential = New-Object System.Management.Automation.PSCredential($ApiUrl.TrimEnd('/'), $secret)
# Export-Clixml encrypts SecureString with Windows DPAPI for this user/machine.
$credential | Export-Clixml -LiteralPath (Join-Path $cache 'report-worker.credential.xml')
Write-Output 'Worker connection saved using Windows user encryption.'
