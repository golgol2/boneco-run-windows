param(
    [switch]$AcceptTerms,
    [switch]$NoStartup,
    [switch]$NoLaunch
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$StateDir = Join-Path $Root "runtime\state"
$LogFile = Join-Path $StateDir "install-webview.log"
$TermsFile = Join-Path $Root "TERMOS-PT-BR.txt"
$IconFile = Join-Path $Root "assets\boneco-run.ico"

function Write-InstallLog {
    param([string]$Message)

    New-Item -ItemType Directory -Force -Path $StateDir | Out-Null
    $line = (Get-Date).ToString("s") + " " + $Message
    Add-Content -LiteralPath $LogFile -Value $line -Encoding UTF8
    Write-Output $line
}

function Show-TermsAcceptance {
    if ($AcceptTerms) {
        Write-InstallLog "termos=aceite_por_parametro"
        return
    }

    if (-not (Test-Path -LiteralPath $TermsFile -PathType Leaf)) {
        throw "Arquivo de termos nao encontrado: $TermsFile"
    }

    Write-Host ""
    Write-Host "TERMOS DE USO E LICENCA - BONECO RUN Windows"
    Write-Host "Leia os termos abaixo antes de instalar."
    Write-Host ""
    Get-Content -LiteralPath $TermsFile -Encoding UTF8 | ForEach-Object {
        Write-Host $_
    }
    Write-Host ""

    $answer = Read-Host "Digite ACEITO para aceitar os termos e continuar"
    if (([string]$answer).Trim().ToUpperInvariant() -ne "ACEITO") {
        throw "Instalacao cancelada. Os termos nao foram aceitos."
    }

    Write-InstallLog "termos=aceitos"
}

function Stop-BonecoProcess {
    param([string]$Pattern)

    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object {
            $_.CommandLine -like "*$Pattern*" -and
            ($_.CommandLine -like "*$Root*" -or $_.CommandLine -like "*boneco-windows-webview*")
        } |
        ForEach-Object {
            try {
                Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop
                Write-InstallLog "processo_encerrado=$Pattern pid=$($_.ProcessId)"
            }
            catch {
                Write-InstallLog "processo_nao_encerrado=$Pattern pid=$($_.ProcessId)"
            }
        }
}

function Stop-PanelProcess {
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object {
            $_.CommandLine -like "*--app=http://127.0.0.1:8791*"
        } |
        ForEach-Object {
            try {
                Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop
                Write-InstallLog "painel_orfao_encerrado pid=$($_.ProcessId)"
            }
            catch {
                Write-InstallLog "painel_orfao_nao_encerrado pid=$($_.ProcessId)"
            }
        }
}

function Stop-CdpBrowser {
    param([string]$Port)

    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object {
            $_.CommandLine -like "*remote-debugging-port=$Port*"
        } |
        ForEach-Object {
            try {
                Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop
                Write-InstallLog "chatgpt_cdp_encerrado=$Port pid=$($_.ProcessId)"
            }
            catch {
                Write-InstallLog "chatgpt_cdp_nao_encerrado=$Port pid=$($_.ProcessId)"
            }
        }
}

function Resolve-Python {
    $portable = Join-Path $Root "runtime\python\python.exe"
    if (Test-Path -LiteralPath $portable -PathType Leaf) {
        return $portable
    }

    foreach ($command in @("python.exe", "py.exe")) {
        $candidate = Get-Command $command -ErrorAction SilentlyContinue
        if ($candidate) {
            return $candidate.Source
        }
    }

    return ""
}

function Resolve-PythonWindowless {
    $portable = Join-Path $Root "runtime\python\pythonw.exe"
    if (Test-Path -LiteralPath $portable -PathType Leaf) {
        return $portable
    }

    return Resolve-Python
}

function New-Shortcut {
    param(
        [string]$Path,
        [string]$Target,
        [string]$WorkingDirectory,
        [string]$Arguments = ""
    )

    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($Path)
    $shortcut.TargetPath = $Target
    $shortcut.WorkingDirectory = $WorkingDirectory
    $shortcut.Arguments = $Arguments
    if (Test-Path -LiteralPath $IconFile -PathType Leaf) {
        $shortcut.IconLocation = $IconFile
    }
    else {
        $shortcut.IconLocation = "$env:SystemRoot\System32\shell32.dll,44"
    }
    $shortcut.Save()
}

function Clear-StartupRunKey {
    try {
        $runPath = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
        Remove-ItemProperty -Path $runPath -Name "BONECO RUN Windows" -ErrorAction SilentlyContinue
        $properties = Get-ItemProperty -Path $runPath -ErrorAction SilentlyContinue
        if ($properties) {
            $properties.PSObject.Properties |
                Where-Object {
                    ($_.Name -like "*BONECO*" -or $_.Name -like "*boneco*") -or
                    ([string]$_.Value -like "*BONECO*" -or [string]$_.Value -like "*boneco*")
                } |
                ForEach-Object {
                    Remove-ItemProperty -Path $runPath -Name $_.Name -ErrorAction SilentlyContinue
                    Write-InstallLog "run_key_antiga=removida nome=$($_.Name)"
                }
        }
        Write-InstallLog "run_key=removida"
    }
    catch {
        Write-InstallLog ("run_key=remocao_falhou " + $_.Exception.Message)
    }
}

function Remove-StartupShortcut {
    try {
        $startup = [Environment]::GetFolderPath("Startup")
        if (-not [string]::IsNullOrWhiteSpace($startup)) {
            $shortcut = Join-Path $startup "BONECO RUN Windows.lnk"
            Remove-Item -LiteralPath $shortcut -Force -ErrorAction SilentlyContinue
            Get-ChildItem -LiteralPath $startup -Force -ErrorAction SilentlyContinue |
                Where-Object {
                    $_.Name -like "*BONECO*" -or
                    $_.Name -like "*boneco*" -or
                    $_.FullName -like "*BONECO*" -or
                    $_.FullName -like "*boneco*"
                } |
                ForEach-Object {
                    Remove-Item -LiteralPath $_.FullName -Force -ErrorAction SilentlyContinue
                    Write-InstallLog "atalho_inicializacao_antigo=removido nome=$($_.Name)"
                }
            Write-InstallLog "atalho_inicializacao=removido"
        }
    }
    catch {
        Write-InstallLog ("atalho_inicializacao_remocao_falhou " + $_.Exception.Message)
    }
}

function Reset-DaemonTask {
    try {
        Stop-ScheduledTask -TaskName "BONECO_RUN_WINDOWS_DAEMON" -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName "BONECO_RUN_WINDOWS_DAEMON" -Confirm:$false -ErrorAction SilentlyContinue
        Write-InstallLog "daemon_task=removido"
    }
    catch {
        Write-InstallLog ("daemon_task=remocao_falhou " + $_.Exception.Message)
    }
}

function Reset-TrayTask {
    try {
        Stop-ScheduledTask -TaskName "BONECO_RUN_WINDOWS_TRAY" -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName "BONECO_RUN_WINDOWS_TRAY" -Confirm:$false -ErrorAction SilentlyContinue
        Write-InstallLog "tray_task=removido"
    }
    catch {
        Write-InstallLog ("tray_task=remocao_falhou " + $_.Exception.Message)
    }
}

function Register-DaemonTask {
    param(
        [string]$Python,
        [string]$RootPath
    )

    try {
        $taskName = "BONECO_RUN_WINDOWS_DAEMON"
        $userId = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
        $daemon = Join-Path $RootPath "app\boneco_windows_daemon.py"
        $arguments = '"' + $daemon + '" --host 127.0.0.1 --port 8791'
        $action = New-ScheduledTaskAction -Execute $Python -Argument $arguments -WorkingDirectory $RootPath
        $trigger = New-ScheduledTaskTrigger -AtLogOn
        $principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Limited
        $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Days 0)
        Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
        Write-InstallLog "daemon_task=registrado_interativo"
    }
    catch {
        Write-InstallLog ("daemon_task=falhou " + $_.Exception.Message)
    }
}

function Register-TrayTask {
    param(
        [string]$Launcher,
        [string]$RootPath
    )

    try {
        $taskName = "BONECO_RUN_WINDOWS_TRAY"
        $userId = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
        $arguments = '"' + $Launcher + '"'
        $action = New-ScheduledTaskAction -Execute "wscript.exe" -Argument $arguments -WorkingDirectory $RootPath
        $trigger = New-ScheduledTaskTrigger -AtLogOn
        $principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Limited
        $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Days 0)
        Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
        Write-InstallLog "tray_task=registrado_interativo"
    }
    catch {
        Write-InstallLog ("tray_task=falhou " + $_.Exception.Message)
    }
}

Write-InstallLog "root=$Root"
Write-InstallLog "modo=webview_tray"

Show-TermsAcceptance

Stop-BonecoProcess "boneco_run_panel.ps1"
Stop-BonecoProcess "boneco_tray.ps1"
Stop-BonecoProcess "boneco_windows_daemon.py"
Stop-BonecoProcess "boneco-device-agent.py"
Stop-BonecoProcess "boneco-job-controller.py"
Stop-BonecoProcess "remote-debugging-port=9227"
Stop-BonecoProcess "remote-debugging-port=9228"
Stop-CdpBrowser "9227"
Stop-CdpBrowser "9228"
Stop-PanelProcess
Reset-DaemonTask
Reset-TrayTask
Clear-StartupRunKey
Remove-StartupShortcut

$python = Resolve-Python
$pythonBackground = Resolve-PythonWindowless
if ([string]::IsNullOrWhiteSpace($python)) {
    Write-InstallLog "python=nao_encontrado"
}
else {
    Write-InstallLog "python=$python"
    Write-InstallLog "python_background=$pythonBackground"
    $checkOutput = & $python (Join-Path $Root "app\boneco_windows_daemon.py") --check 2>&1
    $checkExitCode = $LASTEXITCODE
    $checkOutput | ForEach-Object {
        Write-InstallLog $_
    }
    if ($checkExitCode -ne 0) {
        throw "Validacao do daemon falhou com exit_code=$checkExitCode"
    }
    Register-DaemonTask $pythonBackground $Root
}

$launcher = Join-Path $Root "Start-BonecoRunWindows.vbs"
$trayLauncher = Join-Path $Root "Start-BonecoRunTray.vbs"
$wscript = Join-Path $env:SystemRoot "System32\wscript.exe"

if (-not (Test-Path -LiteralPath $launcher -PathType Leaf)) {
    throw "Launcher nao encontrado: $launcher"
}
if (-not (Test-Path -LiteralPath $trayLauncher -PathType Leaf)) {
    throw "Launcher da bandeja nao encontrado: $trayLauncher"
}

if (-not $NoStartup) {
    Register-TrayTask $trayLauncher $Root
}

$desktop = [Environment]::GetFolderPath("Desktop")
if (-not [string]::IsNullOrWhiteSpace($desktop)) {
    New-Shortcut (Join-Path $desktop "BONECO RUN Windows.lnk") $wscript $Root ('"' + $launcher + '"')
    Write-InstallLog "atalho_area_trabalho=criado"
}

if (-not $NoStartup) {
    Write-InstallLog "inicializacao=scheduled_task_bandeja"
}

if (-not $NoLaunch) {
    Start-ScheduledTask -TaskName "BONECO_RUN_WINDOWS_DAEMON" -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 3
    if (-not $NoStartup) {
        Start-ScheduledTask -TaskName "BONECO_RUN_WINDOWS_TRAY" -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 2
    Start-Process -FilePath $wscript -ArgumentList ('"' + $launcher + '"') -WorkingDirectory $Root
    Write-InstallLog "daemon_tray_e_painel=iniciados"
}

Write-InstallLog "status=WINDOWS_WEBVIEW_INSTALLED"
