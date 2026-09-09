@echo off
setlocal
set "ROOT=%~dp0"
start "" /min powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%ROOT%launcher\boneco_start.ps1" -OpenPanel
