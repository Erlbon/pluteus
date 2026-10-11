<#
.SYNOPSIS
    Runs pluteus at Windows startup through Task Scheduler (built in, nothing to download), and restarts it if it dies.

.DESCRIPTION
    Registers a scheduled task that runs "python -m pluteus supervise": it starts "python -m pluteus serve --config <your config>",
    appends the output to a log file, starts it again 10 seconds after any exit, and is never stopped for running too long.

    Default: starts at BOOT as SYSTEM (needs an elevated PowerShell: "Run as administrator"). SYSTEM cannot see mapped
    drive letters such as Z:, so use local paths or \\server\share paths in config.toml (the script warns about this).
    -AtLogon: starts when YOU log on, as you, and needs no administrator rights (handy for trying it out).

.EXAMPLE
    .\tools\install-task.ps1 -Config C:\pluteus\config.toml -Start
    .\tools\install-task.ps1 -Config C:\pluteus\config.toml -AtLogon -Start
    .\tools\install-task.ps1 -Remove
#>
[CmdletBinding()]
param(
    [string]$Config,
    [string]$TaskName = 'pluteus',
    [string]$Python,
    [string]$WorkingDirectory,
    [string]$Log,
    [switch]$AtLogon,
    [switch]$Start,
    [switch]$Remove,
    [switch]$Force
)
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot

function Stop-AndUnregister {
    $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $existing) { return $false }
    if ($existing.State -eq 'Running') { Stop-ScheduledTask -TaskName $TaskName; Start-Sleep -Seconds 2 }
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    return $true
}

if ($Remove) {
    if (Stop-AndUnregister) { "Removed the task '$TaskName'." } else { "There is no task '$TaskName'." }
    return
}

if (-not $Config) { throw 'Give the config file: -Config C:\path\to\config.toml' }
if (-not (Test-Path -LiteralPath $Config)) { throw "Config file not found: $Config" }
$Config = (Resolve-Path -LiteralPath $Config).ProviderPath
if (-not $Python) {
    $venv = Join-Path $repo '.venv\Scripts\python.exe'
    $Python = if (Test-Path $venv) { $venv } else { (Get-Command python -ErrorAction Stop).Source }
}
if (-not (Test-Path -LiteralPath $Python)) { throw "Python not found: $Python" }
if (-not $WorkingDirectory) { $WorkingDirectory = $repo }
if (-not $Log) { $Log = Join-Path (Split-Path -Parent $Config) 'pluteus-service.log' }

# pluteus must be importable by that Python, and the config must be valid, or the task would just fail in a loop.
& $Python -c 'import pluteus' 2>$null
if ($LASTEXITCODE -ne 0) { throw "That Python cannot import pluteus: $Python`nInstall it first (pip install .) or pass -Python for the right environment." }
$check = & $Python -m pluteus check-config --config $Config 2>&1
if ($LASTEXITCODE -ne 0) {
    if (-not $Force) { throw "check-config failed:`n$check`n(-Force registers the task anyway, for example when a library share is offline right now.)" }
    Write-Warning "check-config failed, registering anyway: $check"
}

# A task account has no mapped network drives.
if (-not $AtLogon) {
    foreach ($m in [regex]::Matches((Get-Content -LiteralPath $Config -Raw), "(?im)^\s*(?:path|uploads_dir|data_dir|cache_dir|themes_dir)\s*=\s*['""]([A-Za-z]):")) {
        $letter = $m.Groups[1].Value
        try {
            if ([System.IO.DriveInfo]::new($letter).DriveType -eq 'Network') {
                Write-Warning "${letter}: is a mapped network drive. SYSTEM cannot see it: use the \\server\share path in config.toml."
            }
        } catch { }
    }
}

$null = Stop-AndUnregister
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Log) | Out-Null

# "pluteus supervise" runs the server, appends its output to the log, and starts it again 10 seconds after any exit
# (Task Scheduler's own "restart on failure" did not restart a killed process in testing). It also puts the server in
# a Windows job object, so stopping the task takes the server with it instead of leaving it holding the port.
# pythonw.exe (when it sits next to python.exe) runs without a console window.
$pythonw = Join-Path (Split-Path -Parent $Python) 'pythonw.exe'
$exe = if (Test-Path -LiteralPath $pythonw) { $pythonw } else { $Python }
$command = '-m pluteus supervise --config "{0}" --log "{1}"' -f $Config, $Log
$action = New-ScheduledTaskAction -Execute $exe -Argument $command -WorkingDirectory $WorkingDirectory
if ($AtLogon) {
    $who = "$env:USERDOMAIN\$env:USERNAME"
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $who
    $principal = New-ScheduledTaskPrincipal -UserId $who -LogonType Interactive -RunLevel Limited
} else {
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
}
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -MultipleInstances IgnoreNew -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero)

try {
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings `
        -Description 'pluteus: filesystem-first book, comic and file server' -ErrorAction Stop | Out-Null
} catch {
    if ($_.FullyQualifiedErrorId -match '0x80070005' -or $_.Exception.GetType().Name -match 'Unauthorized|AccessDenied') {
        throw "Access denied. Start-at-boot needs an elevated PowerShell (Run as administrator); or add -AtLogon to run as yourself without admin rights."
    }
    throw
}
if (-not (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue)) { throw "The task '$TaskName' was not created." }
"Registered '$TaskName' ($(if ($AtLogon) { "at logon of $who" } else { 'at boot, as SYSTEM' }))."
"  command : $Python -m pluteus supervise --config $Config"
"  log     : $Log"
if ($Start) {
    Start-ScheduledTask -TaskName $TaskName
    "Started. Look at the log, or open the address from your config."
} else {
    "Start it now with:  Start-ScheduledTask -TaskName $TaskName"
}
"Remove it with:  .\tools\install-task.ps1 -Remove -TaskName $TaskName"
