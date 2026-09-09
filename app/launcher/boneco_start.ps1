param(
    [switch]$OpenPanel
)

$ErrorActionPreference = "Continue"
$ProgressPreference = "SilentlyContinue"

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
$DaemonPidFile = Join-Path $StateDir "windows-daemon.pid"
$PanelUrl = "http://127.0.0.1:8791"

function Write-StartLog {
    param([string]$Message)

    try {
        New-Item -ItemType Directory -Force -Path $StateDir | Out-Null
        Add-Content -LiteralPath (Join-Path $StateDir "launcher.log") -Value ((Get-Date).ToString("s") + " " + $Message) -Encoding UTF8
    }
    catch {
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

function Test-Daemon {
    try {
        $health = Invoke-RestMethod -Uri ($PanelUrl + "/health") -Method GET -TimeoutSec 2
        return [bool]$health.ok
    }
    catch {
        return $false
    }
}

function Resolve-DefaultProjectDir {
    $candidates = New-Object System.Collections.Generic.List[string]

    if (-not [string]::IsNullOrWhiteSpace($env:USERPROFILE)) {
        $candidates.Add((Join-Path $env:USERPROFILE "Desktop\Nova pasta\BONECO_RUN_WINDOWS"))
        $candidates.Add((Join-Path $env:USERPROFILE "OneDrive\Desktop\Nova pasta\BONECO_RUN_WINDOWS"))
    }

    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate -PathType Container) {
            return $candidate
        }
    }

    foreach ($candidate in $candidates) {
        try {
            New-Item -ItemType Directory -Force -Path $candidate | Out-Null
            if (Test-Path -LiteralPath $candidate -PathType Container) {
                return $candidate
            }
        }
        catch {
        }
    }

    if (-not [string]::IsNullOrWhiteSpace($env:USERPROFILE)) {
        $desktop = Join-Path $env:USERPROFILE "Desktop"
        if (Test-Path -LiteralPath $desktop -PathType Container) {
            return $desktop
        }
    }

    return $Root
}

function Test-OtherUserProjectDir {
    param([string]$ProjectDir)

    if ([string]::IsNullOrWhiteSpace($ProjectDir) -or [string]::IsNullOrWhiteSpace($env:USERPROFILE)) {
        return $false
    }

    $clean = $ProjectDir.Trim().Replace("/", "\").TrimEnd("\").ToLowerInvariant()
    $current = $env:USERPROFILE.Trim().Replace("/", "\").TrimEnd("\").ToLowerInvariant()

    return $clean.StartsWith("c:\users\") -and -not (
        $clean -eq $current -or $clean.StartsWith($current + "\")
    )
}

function Test-InternalProjectDir {
    param([string]$ProjectDir)

    if ([string]::IsNullOrWhiteSpace($ProjectDir)) {
        return $true
    }

    $clean = $ProjectDir.Trim().TrimEnd("\", "/")
    $rootClean = $Root.TrimEnd("\", "/")

    if ($clean -ieq $rootClean) {
        return $true
    }

    if ($clean.StartsWith($rootClean + "\", [System.StringComparison]::OrdinalIgnoreCase)) {
        return $true
    }

    if ($clean.StartsWith("/tmp/") -or $clean.StartsWith("/media/")) {
        return $true
    }

    return -not (Test-Path -LiteralPath $clean -PathType Container)
}

function Ensure-ProjectConfig {
    try {
        New-Item -ItemType Directory -Force -Path $StateDir | Out-Null
        $configPath = Join-Path $StateDir "windows-webview-config.json"
        $data = [ordered]@{
            project_dir = $Root
            project_target_url = ""
            gateway_url = "https://run.oboneco.com.br"
            local_api_endpoint = "http://127.0.0.1:8765/chat"
            chatgpt_project_url = ""
            chatgpt_api_url = ""
            access_scope = "computer"
        }

        if (Test-Path -LiteralPath $configPath -PathType Leaf) {
            $current = Get-Content -LiteralPath $configPath -Raw -ErrorAction SilentlyContinue | ConvertFrom-Json -ErrorAction SilentlyContinue
            if ($current) {
                foreach ($name in $current.PSObject.Properties.Name) {
                    $data[$name] = [string]$current.$name
                }
            }
        }

        $defaultProject = Resolve-DefaultProjectDir
        if (
            (
                (Test-InternalProjectDir ([string]$data["project_dir"])) -or
                (Test-OtherUserProjectDir ([string]$data["project_dir"]))
            ) -and
            -not [string]::IsNullOrWhiteSpace($defaultProject)
        ) {
            $data["project_dir"] = $defaultProject
            ($data | ConvertTo-Json -Depth 4) + "`n" | Set-Content -LiteralPath $configPath -Encoding UTF8
            Write-StartLog "project_dir_corrigido=$defaultProject"
        }
    }
    catch {
        Write-StartLog ("project_config_error=" + $_.Exception.Message)
    }
}

function Stop-OldDaemons {
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
                    Write-StartLog "daemon_antigo_encerrado=$($_.ProcessId)"
                }
                catch {
                    Write-StartLog "daemon_antigo_nao_encerrado=$($_.ProcessId)"
                }
            }
    }
    catch {
        Write-StartLog ("daemon_cleanup_error=" + $_.Exception.Message)
    }
}

function Start-DaemonTask {
    try {
        $task = Get-ScheduledTask -TaskName "BONECO_RUN_WINDOWS_DAEMON" -ErrorAction SilentlyContinue
        if (-not $task) {
            return $false
        }

        Start-ScheduledTask -TaskName "BONECO_RUN_WINDOWS_DAEMON" -ErrorAction SilentlyContinue
        $deadline = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() + 10000
        while ([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() -lt $deadline) {
            if (Test-Daemon) {
                Write-StartLog "daemon_task=ready"
                return $true
            }
            Start-Sleep -Milliseconds 350
        }

        Write-StartLog "daemon_task=timeout"
        return $false
    }
    catch {
        Write-StartLog ("daemon_task_start_error=" + $_.Exception.Message)
        return $false
    }
}

function Start-Daemon {
    Ensure-ProjectConfig

    if (Test-Daemon) {
        Write-StartLog "daemon=already_running"
        return $true
    }

    if (Start-DaemonTask) {
        return $true
    }

    $python = Resolve-PythonWindowless
    if ([string]::IsNullOrWhiteSpace($python)) {
        Write-StartLog "python=nao_encontrado"
        return $false
    }

    if (-not (Test-Path -LiteralPath $Daemon -PathType Leaf)) {
        Write-StartLog "daemon_script=nao_encontrado"
        return $false
    }

    New-Item -ItemType Directory -Force -Path $StateDir | Out-Null
    $out = Join-Path $StateDir "daemon.stdout.log"
    $err = Join-Path $StateDir "daemon.stderr.log"

    try {
        $env:BONECO_WINDOWS_STATE_DIR = $StateDir
        $env:BONECO_STATE_DIR = $StateDir
        $process = Start-Process -FilePath $python -ArgumentList @("`"$Daemon`"", "--host", "127.0.0.1", "--port", "8791") -WorkingDirectory $Root -WindowStyle Hidden -PassThru -RedirectStandardOutput $out -RedirectStandardError $err
        [System.IO.File]::WriteAllText($DaemonPidFile, [string]$process.Id, [System.Text.Encoding]::UTF8)
        Write-StartLog "daemon_iniciado=$($process.Id)"
    }
    catch {
        Write-StartLog ("daemon_start_error=" + $_.Exception.Message)
        return $false
    }

    $deadline = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() + 15000
    while ([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() -lt $deadline) {
        if (Test-Daemon) {
            Write-StartLog "daemon=ready"
            return $true
        }
        Start-Sleep -Milliseconds 350
    }

    Write-StartLog "daemon=timeout"
    return $false
}

function Resolve-Browser {
    $paths = @()

    if (-not [string]::IsNullOrWhiteSpace($env:ProgramFiles)) {
        $paths += (Join-Path $env:ProgramFiles "Google\Chrome\Application\chrome.exe")
        $paths += (Join-Path $env:ProgramFiles "Microsoft\Edge\Application\msedge.exe")
    }
    if (-not [string]::IsNullOrWhiteSpace(${env:ProgramFiles(x86)})) {
        $paths += (Join-Path ${env:ProgramFiles(x86)} "Google\Chrome\Application\chrome.exe")
        $paths += (Join-Path ${env:ProgramFiles(x86)} "Microsoft\Edge\Application\msedge.exe")
    }
    if (-not [string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
        $paths += (Join-Path $env:LOCALAPPDATA "Google\Chrome\Application\chrome.exe")
        $paths += (Join-Path $env:LOCALAPPDATA "Microsoft\Edge\Application\msedge.exe")
    }

    foreach ($path in $paths) {
        if (Test-Path -LiteralPath $path -PathType Leaf) {
            return $path
        }
    }

    return ""
}

function Open-Panel {
    $browser = Resolve-Browser
    if (-not [string]::IsNullOrWhiteSpace($browser)) {
        Start-Process -FilePath $browser -ArgumentList @("--app=$PanelUrl", "--new-window") | Out-Null
        Write-StartLog "panel=browser_app"
        return
    }

    Start-Process $PanelUrl | Out-Null
    Write-StartLog "panel=shell"
}

Stop-OldDaemons
$ready = Start-Daemon

if ($OpenPanel) {
    if ($ready) {
        Open-Panel
    }
    else {
        Add-Type -AssemblyName System.Windows.Forms
        [System.Windows.Forms.MessageBox]::Show(
            "Nao consegui iniciar o servico local do BONECO RUN. Veja runtime\state\launcher.log e daemon.stderr.log.",
            "BONECO RUN Windows",
            [System.Windows.Forms.MessageBoxButtons]::OK,
            [System.Windows.Forms.MessageBoxIcon]::Error
        ) | Out-Null
    }
}

if ($ready) { exit 0 }
exit 1
