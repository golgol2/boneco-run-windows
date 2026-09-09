param(
    [switch]$OpenOnStart
)

$ErrorActionPreference = "Continue"

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$Root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
if (-not [string]::IsNullOrWhiteSpace($env:BONECO_WINDOWS_STATE_DIR)) {
    $StateDir = $env:BONECO_WINDOWS_STATE_DIR
}
elseif (-not [string]::IsNullOrWhiteSpace($env:BONECO_STATE_DIR)) {
    $StateDir = $env:BONECO_STATE_DIR
}
elseif (-not [string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
    $StateDir = Join-Path $env:LOCALAPPDATA "BonecoRunWindows\state"
}
else {
    $StateDir = Join-Path $Root "runtime\state"
}
$Daemon = Join-Path $Root "app\boneco_windows_daemon.py"
$Launcher = Join-Path $Root "launcher\boneco_start.ps1"
$IconFile = Join-Path $Root "assets\boneco-run.ico"
$DaemonPidFile = Join-Path $StateDir "windows-daemon.pid"
$PanelUrl = "http://127.0.0.1:8791"

function Write-TrayLog {
    param([string]$Message)

    try {
        New-Item -ItemType Directory -Force -Path $StateDir | Out-Null
        Add-Content -LiteralPath (Join-Path $StateDir "tray.log") -Value ((Get-Date).ToString("s") + " " + $Message) -Encoding UTF8
    }
    catch {
    }
}

function Resolve-Python {
    $portable = Join-Path $Root "runtime\python\python.exe"
    if (Test-Path -LiteralPath $portable -PathType Leaf) {
        return $portable
    }

    $commands = @("python.exe", "py.exe")
    foreach ($command in $commands) {
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

function Resolve-TrayIcon {
    if (Test-Path -LiteralPath $IconFile -PathType Leaf) {
        try {
            return New-Object System.Drawing.Icon -ArgumentList $IconFile
        }
        catch {
            Write-TrayLog ("icone_personalizado_falhou=" + $_.Exception.Message)
        }
    }

    return [System.Drawing.SystemIcons]::Application
}

function Test-Daemon {
    try {
        $health = Invoke-RestMethod -Uri ($PanelUrl + "/health") -Method GET -TimeoutSec 2
        return [bool]$health.ok
    }
    catch {
        return $false
    }
}

function Stop-OldWebviewDaemons {
    try {
        Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
            Where-Object {
                $_.CommandLine -like "*boneco_windows_daemon.py*" -and
                $_.CommandLine -like "*boneco-windows-webview*" -and
                $_.CommandLine -notlike "*$Root*"
            } |
            ForEach-Object {
                try {
                    Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop
                    Write-TrayLog "daemon_antigo_encerrado=$($_.ProcessId)"
                }
                catch {
                    Write-TrayLog "daemon_antigo_nao_encerrado=$($_.ProcessId)"
                }
            }
    }
    catch {
        Write-TrayLog ("daemon_cleanup_error=" + $_.Exception.Message)
    }
}

function Start-Daemon {
    if (Test-Path -LiteralPath $Launcher -PathType Leaf) {
        $result = Start-Process -FilePath "powershell.exe" -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-File", "`"$Launcher`"") -WorkingDirectory $Root -Wait -PassThru -WindowStyle Hidden
        return $result.ExitCode -eq 0 -or (Test-Daemon)
    }

    if (Test-Daemon) {
        return $true
    }

    $python = Resolve-PythonWindowless
    if ([string]::IsNullOrWhiteSpace($python)) {
        [System.Windows.Forms.MessageBox]::Show(
            "Python nao encontrado. Instale Python ou inclua runtime\python\python.exe no pacote.",
            "BONECO RUN Windows",
            [System.Windows.Forms.MessageBoxButtons]::OK,
            [System.Windows.Forms.MessageBoxIcon]::Error
        ) | Out-Null
        return $false
    }

    New-Item -ItemType Directory -Force -Path $StateDir | Out-Null
    $out = Join-Path $StateDir "daemon.stdout.log"
    $err = Join-Path $StateDir "daemon.stderr.log"

    try {
        $process = Start-Process -FilePath $python -ArgumentList @("`"$Daemon`"", "--host", "127.0.0.1", "--port", "8791") -WorkingDirectory $Root -WindowStyle Hidden -PassThru -RedirectStandardOutput $out -RedirectStandardError $err
        [System.IO.File]::WriteAllText($DaemonPidFile, [string]$process.Id, [System.Text.Encoding]::UTF8)
    }
    catch {
        Write-TrayLog ("daemon_start_error=" + $_.Exception.ToString())
        [System.Windows.Forms.MessageBox]::Show(
            "Falha ao iniciar daemon: " + $_.Exception.Message,
            "BONECO RUN Windows",
            [System.Windows.Forms.MessageBoxButtons]::OK,
            [System.Windows.Forms.MessageBoxIcon]::Error
        ) | Out-Null
        return $false
    }

    $deadline = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() + 12000
    while ([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() -lt $deadline) {
        if (Test-Daemon) {
            return $true
        }
        Start-Sleep -Milliseconds 400
    }

    Write-TrayLog "daemon_timeout"
    return $false
}

function Resolve-Browser {
    $paths = @()
    if (-not [string]::IsNullOrWhiteSpace($env:ProgramFiles)) {
        $paths += (Join-Path $env:ProgramFiles "Microsoft\Edge\Application\msedge.exe")
        $paths += (Join-Path $env:ProgramFiles "Google\Chrome\Application\chrome.exe")
    }
    if (-not [string]::IsNullOrWhiteSpace(${env:ProgramFiles(x86)})) {
        $paths += (Join-Path ${env:ProgramFiles(x86)} "Microsoft\Edge\Application\msedge.exe")
        $paths += (Join-Path ${env:ProgramFiles(x86)} "Google\Chrome\Application\chrome.exe")
    }
    if (-not [string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
        $paths += (Join-Path $env:LOCALAPPDATA "Microsoft\Edge\Application\msedge.exe")
        $paths += (Join-Path $env:LOCALAPPDATA "Google\Chrome\Application\chrome.exe")
    }

    foreach ($path in $paths) {
        if (Test-Path -LiteralPath $path -PathType Leaf) {
            return $path
        }
    }

    return ""
}

function Open-Panel {
    if (-not (Start-Daemon)) {
        return
    }

    $browser = Resolve-Browser
    if (-not [string]::IsNullOrWhiteSpace($browser)) {
        Start-Process -FilePath $browser -ArgumentList @("--app=$PanelUrl", "--new-window") | Out-Null
        return
    }

    Start-Process $PanelUrl | Out-Null
}

function Shutdown-Daemon {
    try {
        Invoke-RestMethod -Uri ($PanelUrl + "/api/system/shutdown") -Method POST -ContentType "application/json" -Body "{}" -TimeoutSec 2 | Out-Null
    }
    catch {
    }
}

Stop-OldWebviewDaemons
Start-Daemon | Out-Null

$notify = New-Object System.Windows.Forms.NotifyIcon
$notify.Icon = Resolve-TrayIcon
$notify.Text = "BONECO RUN Windows"
$notify.Visible = $true

$menu = New-Object System.Windows.Forms.ContextMenuStrip
$openItem = $menu.Items.Add("Abrir painel")
$statusItem = $menu.Items.Add("Status")
$shutdownItem = $menu.Items.Add("Encerrar servico local")
$exitItem = $menu.Items.Add("Sair da bandeja")

$openItem.add_Click({ Open-Panel })
$statusItem.add_Click({
    $message = if (Test-Daemon) { "Servico local ativo em $PanelUrl" } else { "Servico local parado" }
    [System.Windows.Forms.MessageBox]::Show(
        $message,
        "BONECO RUN Windows",
        [System.Windows.Forms.MessageBoxButtons]::OK,
        [System.Windows.Forms.MessageBoxIcon]::Information
    ) | Out-Null
})
$shutdownItem.add_Click({
    Shutdown-Daemon
    $notify.ShowBalloonTip(1500, "BONECO RUN Windows", "Servico local encerrado.", [System.Windows.Forms.ToolTipIcon]::Info)
})
$exitItem.add_Click({
    $notify.Visible = $false
    [System.Windows.Forms.Application]::Exit()
})

$notify.ContextMenuStrip = $menu
$notify.add_DoubleClick({ Open-Panel })
$notify.ShowBalloonTip(1200, "BONECO RUN Windows", "Rodando na bandeja do Windows.", [System.Windows.Forms.ToolTipIcon]::Info)

if ($OpenOnStart) {
    Open-Panel
}

[System.Windows.Forms.Application]::Run()

$notify.Visible = $false
$notify.Dispose()
