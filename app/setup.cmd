@echo off
setlocal

for %%I in ("%~dp0.") do set "SOURCE=%%~fI"

if "%LOCALAPPDATA%"=="" (
  echo LOCALAPPDATA nao definido. A instalacao nao pode continuar.
  pause
  exit /b 1
)

set "DEST=%LOCALAPPDATA%\BonecoRunWindows"

if not exist "%DEST%" (
  mkdir "%DEST%"
)

if exist "%SOURCE%setup-preinstall.ps1" (
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SOURCE%setup-preinstall.ps1" -InstallDir "%DEST%"
)

for %%I in ("%SOURCE%.") do set "SOURCE_FULL=%%~fI"
for %%I in ("%DEST%.") do set "DEST_FULL=%%~fI"

if /I "%SOURCE_FULL%"=="%DEST_FULL%" (
  goto RUN_INSTALL
)

robocopy "%SOURCE%" "%DEST%" /E /XD "%SOURCE%\runtime\state" "%DEST%\runtime\state" /XF "BONECO_RUN_WINDOWS_INSTALLER.exe" "*.7z"
set "ROBOCOPY_EXIT=%ERRORLEVEL%"

if %ROBOCOPY_EXIT% GEQ 8 (
  echo Falha ao copiar arquivos do BONECO RUN Windows. Codigo: %ROBOCOPY_EXIT%
  pause
  exit /b %ROBOCOPY_EXIT%
)

:RUN_INSTALL
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%DEST%\install.ps1" %*
set "INSTALL_EXIT=%ERRORLEVEL%"

if not "%INSTALL_EXIT%"=="0" (
  echo Instalacao encerrada com erro. Codigo: %INSTALL_EXIT%
  pause
)

exit /b %INSTALL_EXIT%
