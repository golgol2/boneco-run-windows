@echo off
setlocal
set "ROOT=%~dp0"
start "" /min powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%ROOT%tray\boneco_tray.ps1"
