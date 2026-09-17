@echo off
setlocal
set "APP=%~dp0EtapCrystalReporter\bin\x86\Release\EtapCrystalReporter.exe"
if not exist "%APP%" (
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0build.ps1" -Platform x86
  if errorlevel 1 (
    echo Build failed. See the desktop README for setup requirements.
    pause
    exit /b 1
  )
)
start "" "%APP%"
