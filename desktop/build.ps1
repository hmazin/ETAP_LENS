param(
    [ValidateSet('x64', 'x86')][string]$Platform = 'x86',
    [switch]$Test,
    [switch]$RestoreOnly,
    [string]$InspectFolder
)
$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$packages = Join-Path $projectRoot '.packages'
New-Item -ItemType Directory -Force -Path $packages | Out-Null

function Restore-Package([string]$Id, [string]$Version, [string]$Hash) {
    $archive = Join-Path $packages "$Id.$Version.zip"
    $directory = Join-Path $packages "$Id.$Version"
    if (-not (Test-Path -LiteralPath $archive)) {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest -UseBasicParsing -Uri "https://api.nuget.org/v3-flatcontainer/$Id/$Version/$Id.$Version.nupkg" -OutFile $archive
    }
    if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash -ne $Hash) {
        throw "Checksum mismatch for $Id $Version. Remove the cached ZIP and restore from the official NuGet feed."
    }
    Expand-Archive -LiteralPath $archive -DestinationPath $directory -Force
    return $directory
}

$sqlite = Restore-Package 'system.data.sqlite' '2.0.4' '5D5AA9ACF03599C0EDFCE25DE9B4D10B3C40A1F2D551F4A91DDDBCCCE58A2F59'
$native = Restore-Package 'sourcegear.sqlite3' '3.53.4' '3EB2B0FC0901FBE09B783968B1C1B002EAC7878053FE47D309E77D2268010D9A'
$framework = Restore-Package 'microsoft.netframework.referenceassemblies.net48' '1.0.3' '8A7E348538E7EB91351696911689F49E3D4F63F8BAB517432BBE159B8B1104A2'
if ($RestoreOnly) { Write-Output 'Build dependencies restored.'; exit 0 }

# The Windows .NET Framework compiler is enough; Visual Studio and a modern .NET SDK are optional.
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path -LiteralPath $compiler)) { $compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework\v4.0.30319\csc.exe' }
if (-not (Test-Path -LiteralPath $compiler)) { throw 'Install .NET Framework 4.8 before building.' }
$referenceRoot = Join-Path $framework 'build\.NETFramework\v4.8'
$output = Join-Path $projectRoot "EtapCrystalReporter\bin\$Platform\Release"
New-Item -ItemType Directory -Force -Path $output | Out-Null
$references = @('mscorlib', 'System', 'System.Core', 'System.Data', 'System.Drawing', 'System.Windows.Forms', 'System.Xml', 'System.Web.Extensions', 'Microsoft.CSharp') |
    ForEach-Object { '/reference:' + (Join-Path $referenceRoot ($_.ToString() + '.dll')) }
$references += '/reference:' + (Join-Path $sqlite 'lib\net471\System.Data.SQLite.dll')
$app = Join-Path $output 'EtapCrystalReporter.exe'
$sources = Get-ChildItem -LiteralPath (Join-Path $projectRoot 'EtapCrystalReporter') -Recurse -Filter '*.cs' |
    Where-Object { $_.FullName -notmatch '[\\/](bin|obj)[\\/]' } | Select-Object -ExpandProperty FullName
& $compiler /nologo /noconfig /nostdlib+ /warnaserror+ /optimize+ "/platform:$Platform" /target:winexe "/out:$app" $references $sources
if ($LASTEXITCODE -ne 0) { throw 'Application compilation failed.' }
Copy-Item -LiteralPath (Join-Path $sqlite 'lib\net471\System.Data.SQLite.dll') -Destination $output -Force
Copy-Item -LiteralPath (Join-Path $native "runtimes\win-$Platform\native\e_sqlite3.dll") -Destination $output -Force
Copy-Item -LiteralPath (Join-Path $projectRoot 'EtapCrystalReporter\App.config') -Destination "$app.config" -Force
Copy-Item -LiteralPath (Join-Path $projectRoot 'EtapCrystalReporter\Templates') -Destination $output -Recurse -Force
Write-Output "Built $app"

$workerExe = Join-Path $output 'EtapCrystalReporter.Worker.exe'
& $compiler /nologo /noconfig /nostdlib+ /warnaserror+ /optimize+ "/platform:$Platform" /target:exe "/out:$workerExe" $references "/reference:$app" (Join-Path $projectRoot 'EtapCrystalReporter.Worker\Program.cs')
if ($LASTEXITCODE -ne 0) { throw 'Report worker compilation failed.' }
Copy-Item -LiteralPath "$app.config" -Destination "$workerExe.config" -Force

if ($Test -or $InspectFolder) {
    $testExe = Join-Path $output 'EtapCrystalReporter.Tests.exe'
    $testSource = Join-Path $projectRoot 'EtapCrystalReporter.Tests\Program.cs'
    & $compiler /nologo /noconfig /nostdlib+ /warnaserror+ /optimize+ "/platform:$Platform" /target:exe "/out:$testExe" $references "/reference:$app" $testSource
    if ($LASTEXITCODE -ne 0) { throw 'Test compilation failed.' }
    Copy-Item -LiteralPath "$app.config" -Destination "$testExe.config" -Force
    if ($Test) { & $testExe; if ($LASTEXITCODE -ne 0) { throw 'Tests failed.' } }
    if ($InspectFolder) { & $testExe --inspect $InspectFolder; if ($LASTEXITCODE -ne 0) { throw 'Sample inspection failed.' } }
}
