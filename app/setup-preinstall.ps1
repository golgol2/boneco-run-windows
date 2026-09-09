param(
    [string]$InstallDir = ""
)

$ErrorActionPreference = "Continue"

function Stop-TaskIfExists {
    param([string]$Name)

    try {
        Stop-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue
    }
    catch {
    }
}

function Stop-MatchingProcess {
    param([string]$InstallPath)

    $patterns = @(
        "boneco_windows_daemon.py",
        "boneco-device-agent.py",
        "boneco-job-controller.py",
        "boneco-local-api.py",
        "chatgpt-browser-overlay-bridge.py",
        "chatgpt-browser-overlay.py",
        "boneco_tray.ps1",
        "boneco_start.ps1",
        "remote-debugging-port=9227",
        "remote-debugging-port=9228",
        "--app=http://127.0.0.1:8791"
    )

    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object {
            $commandLine = [string]$_.CommandLine
            if ([string]::IsNullOrWhiteSpace($commandLine)) {
                return $false
            }

            if (-not [string]::IsNullOrWhiteSpace($InstallPath) -and $commandLine -like "*$InstallPath*") {
                return $true
            }

            foreach ($pattern in $patterns) {
                if ($commandLine -like "*$pattern*") {
                    return $true
                }
            }

            return $false
        } |
        ForEach-Object {
            try {
                Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop
                Write-Output ("processo_antigo_encerrado=" + $_.ProcessId)
            }
            catch {
                Write-Output ("processo_antigo_nao_encerrado=" + $_.ProcessId)
            }
        }
}

Stop-TaskIfExists "BONECO_RUN_WINDOWS_DAEMON"
Stop-TaskIfExists "BONECO_RUN_WINDOWS_TRAY"
Stop-MatchingProcess $InstallDir
