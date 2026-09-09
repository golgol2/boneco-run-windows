param(
    [string]$TestInput = ""
)

$ErrorActionPreference = "Continue"
$Utf8NoBom = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = $Utf8NoBom
[Console]::OutputEncoding = $Utf8NoBom
[Console]::InputEncoding = $Utf8NoBom
$PSDefaultParameterValues["Set-Content:Encoding"] = "UTF8"
$PSDefaultParameterValues["Add-Content:Encoding"] = "UTF8"
$PSDefaultParameterValues["Out-File:Encoding"] = "UTF8"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$DefaultStateDir = Join-Path $ProjectRoot "runtime\state"
if (-not [string]::IsNullOrWhiteSpace($env:BONECO_STATE_DIR)) {
    $StateDir = $env:BONECO_STATE_DIR
}
else {
    $StateDir = $DefaultStateDir
}
$ActivityFile = Join-Path $StateDir "activity_status"

function Get-EpochMs {
    return [int64](([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()))
}

function Write-ActivityStatus {
    param(
        [string]$State,
        [string]$RequestId,
        [string]$Step,
        [string]$Mode,
        [string]$ReturnMode,
        [int]$ExitCode,
        [int64]$StartedMs,
        [int64]$FinishedMs
    )

    New-Item -ItemType Directory -Force -Path $StateDir | Out-Null
    $duration = 0
    if ($StartedMs -gt 0 -and $FinishedMs -gt 0) {
        $duration = [Math]::Max(0, $FinishedMs - $StartedMs)
    }

    $lines = @(
        "state=$State",
        "started_ms=$StartedMs",
        "finished_ms=$FinishedMs",
        "exit_code=$ExitCode",
        "duration_ms=$duration",
        "event_id=$RequestId",
        "request_id=$RequestId",
        "step=$Step",
        "mode=$Mode",
        "return_mode=$ReturnMode"
    )

    [System.IO.File]::WriteAllLines($ActivityFile, $lines, [System.Text.Encoding]::UTF8)
}

function Read-AllInput {
    if ($TestInput -eq "-" -or $TestInput -eq "__stdin__") {
        return [Console]::In.ReadToEnd()
    }

    if ($TestInput -and (Test-Path -LiteralPath $TestInput -PathType Leaf)) {
        return [System.IO.File]::ReadAllText($TestInput, [System.Text.Encoding]::UTF8)
    }

    Add-Type -AssemblyName System.Windows.Forms
    return [System.Windows.Forms.Clipboard]::GetText()
}

function Get-Directive {
    param(
        [string]$Text,
        [string]$Name,
        [string]$Default
    )

    $pattern = "(?im)^\s*#\s*" + [Regex]::Escape($Name) + "\s*:\s*(.+?)\s*$"
    $match = [Regex]::Match($Text, $pattern)
    if ($match.Success) {
        return $match.Groups[1].Value.Trim()
    }
    return $Default
}

function Get-ExecutableBody {
    param([string]$Text)

    $lines = New-Object System.Collections.Generic.List[string]
    foreach ($line in ($Text -split "`r?`n")) {
        if ($line -match "^\s*#\s*BONECO_[A-Z_]+(?:\s*:.*)?\s*$") {
            continue
        }
        if ($line -match "^\s*#\s*BONECO_RUN\s*$") {
            continue
        }
        $lines.Add($line)
    }

    return ($lines -join "`r`n").Trim()
}

$raw = Read-AllInput
if (-not $raw.TrimStart().StartsWith("# BONECO_RUN")) {
    Write-Output "status=ignored"
    Write-Output "erro=marcador # BONECO_RUN ausente"
    exit 66
}

$requestId = Get-Directive $raw "BONECO_REQUEST_ID" ("windows-" + [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds())
$step = Get-Directive $raw "BONECO_STEP" "Executando etapa no Windows"
$returnMode = Get-Directive $raw "BONECO_RETURN" "status"
$mode = Get-Directive $raw "BONECO_MODE" "auto"
$body = Get-ExecutableBody $raw
$started = Get-EpochMs

Write-ActivityStatus "running" $requestId $step $mode $returnMode 0 $started 0

Write-Output "=== BONECO_RUN TESTE ==="
Write-Output "status=running"
Write-Output "request_id=$requestId"
Write-Output "step=$step"
Write-Output "return_mode=$returnMode"
Write-Output "execution_mode=$mode"
Write-Output "capability="
Write-Output "project_dir=$(Get-Location)"
Write-Output "source=windows"
Write-Output ""
Write-Output "----- COMANDO -----"
Write-Output $body
Write-Output "----- SAIDA -----"

$temp = Join-Path ([System.IO.Path]::GetTempPath()) ("boneco-run-" + [Guid]::NewGuid().ToString("N") + ".ps1")
$exitCode = 0
$output = ""

try {
    $prelude = @'
$Utf8NoBom = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = $Utf8NoBom
[Console]::OutputEncoding = $Utf8NoBom
[Console]::InputEncoding = $Utf8NoBom
$PSDefaultParameterValues["Set-Content:Encoding"] = "UTF8"
$PSDefaultParameterValues["Add-Content:Encoding"] = "UTF8"
$PSDefaultParameterValues["Out-File:Encoding"] = "UTF8"
'@
    [System.IO.File]::WriteAllText($temp, ($prelude + "`r`n" + $body), [System.Text.UTF8Encoding]::new($true))
    $output = & powershell.exe -WindowStyle Hidden -NoProfile -ExecutionPolicy Bypass -File $temp 2>&1
    $exitCode = $LASTEXITCODE
    if ($null -eq $exitCode) {
        $exitCode = 0
    }
}
catch {
    $exitCode = 1
    $output = $_.Exception.Message
}
finally {
    Remove-Item -LiteralPath $temp -Force -ErrorAction SilentlyContinue
}

if ($output) {
    $output | Out-String | Write-Output
}

$finished = Get-EpochMs
Write-Output "----- FIM -----"
Write-Output "exit_code=$exitCode"
Write-ActivityStatus "completed" $requestId $step $mode $returnMode $exitCode $started $finished

exit $exitCode
