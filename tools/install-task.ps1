<#
.SYNOPSIS
    Runs pluteus at Windows startup through Task Scheduler (built in, nothing to download), and restarts it if it dies.

.DESCRIPTION
    Registers a scheduled task that runs "python -m pluteus supervise": it starts "python -m pluteus serve --config <your config>",
    appends the output to a log file, starts it again 10 seconds after any exit, and is never stopped for running too long.

    Default: starts at BOOT as the low-privilege account LOCAL SERVICE (needs an elevated PowerShell: "Run as administrator").
    A server that reads untrusted archives, images and media should not own the machine, so the script gives that account
    only what the server needs, with icacls:
        modify      the config's folder (the admin page and "pluteus user" save beside config.toml), data_dir, cache_dir,
                    uploads_dir and the log file
        read only   the libraries, themes_dir, and the program itself when it sits inside a user profile
    -NoGrant leaves all folder rights alone (you set them yourself). LOCAL SERVICE has no identity on the network: it cannot
    read \\server\share libraries, and no task account sees mapped drive letters such as Z: (the script warns about both).

    -AtLogon: starts when YOU log on, as you, and needs no administrator rights (handy for trying it out, and the way to
    read network shares with your own rights).
    -AsSystem: starts at boot as SYSTEM, the old default. SYSTEM owns the machine: a bug in any file parser then gives the
    whole computer away. Use it only when nothing else can reach your libraries; the script warns every time.

    For the two boot modes the script also warns when Python or the pluteus folder can be changed by accounts that are not
    administrators: whoever can change those files decides what runs at the next boot.

.EXAMPLE
    .\tools\install-task.ps1 -Config C:\ProgramData\pluteus\config.toml -Start
    .\tools\install-task.ps1 -Config C:\pluteus\config.toml -AtLogon -Start
    .\tools\install-task.ps1 -Config C:\ProgramData\pluteus\config.toml -AsSystem -Start
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
    [switch]$AsSystem,
    [switch]$NoGrant,
    [switch]$Start,
    [switch]$Remove,
    [switch]$Force
)
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot

# Well-known accounts by SID, so this works on a Windows in any language (the names are translated there).
$LocalServiceSid = 'S-1-5-19'
$TrustedSids = @(
    'S-1-5-18',                                                             # SYSTEM
    'S-1-5-32-544',                                                         # Administrators
    'S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464',       # TrustedInstaller
    'S-1-3-0'                                                               # CREATOR OWNER (a template for new files, not an account)
)
# The bits of an access rule that let its holder change a file or folder: write or append data, delete, delete children,
# change permissions, take ownership, and the two generic rights that include those (GENERIC_WRITE, GENERIC_ALL).
$WriteBits = 0x2 -bor 0x4 -bor 0x40 -bor 0x10000 -bor 0x40000 -bor 0x80000 -bor 0x10000000 -bor 0x40000000

function Stop-AndUnregister {
    $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $existing) { return $false }
    if ($existing.State -eq 'Running') { Stop-ScheduledTask -TaskName $TaskName; Start-Sleep -Seconds 2 }
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    return $true
}

function Test-Elevated {
    $me = New-Object System.Security.Principal.WindowsPrincipal([System.Security.Principal.WindowsIdentity]::GetCurrent())
    return $me.IsInRole([System.Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-FullPath([string]$Path, [string]$Base) {
    # A relative path in the config is relative to the folder the server runs in.
    if (-not [System.IO.Path]::IsPathRooted($Path)) { $Path = Join-Path $Base $Path }
    return [System.IO.Path]::GetFullPath($Path).TrimEnd('\')
}

function Test-Inside([string]$Child, [string]$Parent) {
    # Is $Child the folder $Parent itself, or somewhere below it?
    $c = $Child.TrimEnd('\') + '\'
    $p = $Parent.TrimEnd('\') + '\'
    return $c.StartsWith($p, [System.StringComparison]::OrdinalIgnoreCase)
}

function Test-NetworkPath([string]$Path) {
    if ($Path.StartsWith('\\')) { return $true }
    if ($Path -match '^([A-Za-z]):') {
        try { return ([System.IO.DriveInfo]::new($Matches[1]).DriveType -eq 'Network') } catch { return $false }
    }
    return $false
}

function Test-AdminSid([string]$Sid) {
    if ($TrustedSids -contains $Sid) { return $true }
    # the built-in Administrator account, Domain Admins and Enterprise Admins of a domain
    return ($Sid -match '^S-1-5-21-.+-(500|512|519)$')
}

function Get-NonAdminWriters([string]$Path) {
    # The accounts, other than administrators and the system, that may change $Path: its owner, and everyone an
    # access rule gives a writing right on the item itself (rules that only apply to new children are left out).
    $found = @()
    try {
        $acl = Get-Acl -LiteralPath $Path -ErrorAction Stop
        $sidType = [System.Security.Principal.SecurityIdentifier]
        $owner = $acl.GetOwner($sidType)
        $who = @()
        if ($owner -and -not (Test-AdminSid $owner.Value)) { $who += $owner }
        foreach ($rule in $acl.GetAccessRules($true, $true, $sidType)) {
            if ($rule.AccessControlType -ne 'Allow') { continue }
            if (($rule.PropagationFlags -band [System.Security.AccessControl.PropagationFlags]::InheritOnly) -ne 0) { continue }
            if (([int]$rule.FileSystemRights -band $WriteBits) -eq 0) { continue }
            if (Test-AdminSid $rule.IdentityReference.Value) { continue }
            $who += $rule.IdentityReference
        }
        foreach ($sid in $who) {
            $name = $sid.Value
            try { $name = $sid.Translate([System.Security.Principal.NTAccount]).Value } catch { }
            if ($found -notcontains $name) { $found += $name }
        }
    } catch {
        Write-Warning "Could not read the access rights of ${Path}: $($_.Exception.Message)"
    }
    return $found
}

function Grant-Access([string]$Path, [string]$Rights, [string]$What) {
    # $Rights: 'M' (modify) or 'RX' (read). Folders pass the right on to everything inside them.
    if (-not (Test-Path -LiteralPath $Path)) {
        Write-Warning "$What does not exist, so no rights were set on it: $Path"
        return
    }
    $spec = '*{0}:{1}' -f $LocalServiceSid, $Rights
    if (Test-Path -LiteralPath $Path -PathType Container) { $spec = '*{0}:(OI)(CI){1}' -f $LocalServiceSid, $Rights }
    $output = & icacls.exe $Path /grant $spec /Q 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "Could not give $script:accountName the right '$Rights' on $What ($Path): $output"
        return
    }
    $word = 'read'
    if ($Rights -eq 'M') { $word = 'modify' }
    "  {0,-6} : {1}  ({2})" -f $word, $Path, $What
}

if ($Remove) {
    if (Stop-AndUnregister) {
        "Removed the task '$TaskName'."
        "Rights this script gave LOCAL SERVICE on your folders are left as they are; take them away with:  icacls <folder> /remove *$LocalServiceSid"
    } else { "There is no task '$TaskName'." }
    return
}

if ($AtLogon -and $AsSystem) { throw 'Choose one: -AtLogon (as you) or -AsSystem (as SYSTEM). Neither = at boot as LOCAL SERVICE.' }
if (-not $Config) { throw 'Give the config file: -Config C:\path\to\config.toml' }
if (-not (Test-Path -LiteralPath $Config)) { throw "Config file not found: $Config" }
$Config = (Resolve-Path -LiteralPath $Config).ProviderPath
if (-not $Python) {
    $venv = Join-Path $repo '.venv\Scripts\python.exe'
    $Python = if (Test-Path $venv) { $venv } else { (Get-Command python -ErrorAction Stop).Source }
}
if (-not (Test-Path -LiteralPath $Python)) { throw "Python not found: $Python" }
$Python = (Resolve-Path -LiteralPath $Python).ProviderPath
if (-not $WorkingDirectory) { $WorkingDirectory = $repo }
$WorkingDirectory = (Resolve-Path -LiteralPath $WorkingDirectory).ProviderPath.TrimEnd('\')
$configDir = Split-Path -Parent $Config
if (-not $Log) { $Log = Join-Path $configDir 'pluteus-service.log' }
$Log = Get-FullPath $Log (Get-Location).ProviderPath

# The two boot modes register a task for another account, which only an administrator may do. Say so before anything is changed.
if (-not $AtLogon -and -not (Test-Elevated)) {
    throw "Access denied. Start-at-boot needs an elevated PowerShell (Run as administrator); or add -AtLogon to run as yourself without admin rights."
}

# pluteus must be importable by that Python, and the config must be valid, or the task would just fail in a loop.
& $Python -c 'import pluteus' 2>$null
if ($LASTEXITCODE -ne 0) { throw "That Python cannot import pluteus: $Python`nInstall it first (pip install .) or pass -Python for the right environment." }
$check = & $Python -m pluteus check-config --config $Config 2>&1
if ($LASTEXITCODE -ne 0) {
    if (-not $Force) { throw "check-config failed:`n$check`n(-Force registers the task anyway, for example when a library share is offline right now.)" }
    Write-Warning "check-config failed, registering anyway: $check"
}

# The folders the config names, asked of pluteus itself (so the answer is what the server will really use). The answer is
# plain ASCII JSON, which survives any console code page.
$folders = $null
$probe = "import json, sys; from pluteus.config import load_config; c = load_config(sys.argv[1], check_roots=False); r = c.roots.values() if isinstance(c.roots, dict) else c.roots; print(json.dumps({'data': c.data_dir, 'cache': c.cache_dir, 'uploads': c.uploads_dir, 'themes': c.themes_dir, 'libraries': [x.path for x in r], 'base': sys.base_prefix, 'prefix': sys.prefix}))"
try {
    $answer = & $Python -c $probe $Config 2>$null
    if ($LASTEXITCODE -eq 0 -and $answer) { $folders = ($answer | Select-Object -Last 1) | ConvertFrom-Json }
} catch { $folders = $null }
if (-not $folders) { Write-Warning 'Could not read the folders from the config, so the folder checks below are skipped.' }

$writable = @()          # (path, what) the server writes to
$readable = @()          # (path, what) the server only reads
$codeDirs = @($WorkingDirectory, (Split-Path -Parent $Python))
if ($folders) {
    foreach ($pair in @(@($folders.data, 'data_dir'), @($folders.cache, 'cache_dir'), @($folders.uploads, 'uploads_dir'))) {
        if ($pair[0]) { $writable += , @((Get-FullPath $pair[0] $WorkingDirectory), $pair[1]) }
    }
    if ($folders.themes) { $readable += , @((Get-FullPath $folders.themes $WorkingDirectory), 'themes_dir') }
    foreach ($library in $folders.libraries) { $readable += , @((Get-FullPath $library $WorkingDirectory), 'library') }
    foreach ($dir in @($folders.base, $folders.prefix)) {
        if ($dir -and ($codeDirs -notcontains $dir.TrimEnd('\'))) { $codeDirs += $dir.TrimEnd('\') }
    }
}

# A task account has no mapped network drives, and LOCAL SERVICE has no identity on the network at all.
if (-not $AtLogon) {
    foreach ($entry in ($writable + $readable)) {
        $path = $entry[0]
        if ($path -match '^([A-Za-z]):' -and (Test-NetworkPath $path)) {
            Write-Warning "$($entry[1]) $path is on a mapped network drive. A task that starts at boot cannot see it: use the \\server\share path in config.toml."
        } elseif ($path.StartsWith('\\') -and -not $AsSystem) {
            Write-Warning "$($entry[1]) $path is on a network share. LOCAL SERVICE has no identity on the network and will be refused there. Use -AtLogon (your own rights), or -AsSystem with a share that admits this computer's account."
        }
    }
}

# Whoever can change Python or the pluteus code decides what the task runs at the next boot, with the task's rights.
if (-not $AtLogon) {
    $asWhom = 'LOCAL SERVICE'
    if ($AsSystem) { $asWhom = 'SYSTEM, which owns this computer' }
    $checked = @($Python) + $codeDirs
    foreach ($item in $checked) {
        if (-not (Test-Path -LiteralPath $item)) { continue }
        $writers = Get-NonAdminWriters $item
        if ($writers.Count -gt 0) {
            Write-Warning ("{0} can be changed by: {1}. Any program running as one of them can replace what this task runs at boot as {2}. Put Python and pluteus where only administrators can write (for example under C:\Program Files), or use -AtLogon." -f $item, ($writers -join ', '), $asWhom)
        }
    }
}
if ($AsSystem) {
    Write-Warning 'The task will run as SYSTEM. pluteus reads untrusted archives, images and media files; as SYSTEM a bug in any of those readers gives away the whole computer. The default (no -AsSystem) runs it as LOCAL SERVICE instead.'
}

$null = Stop-AndUnregister
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Log) | Out-Null

# Give LOCAL SERVICE what the server needs, and nothing on the program itself beyond reading it.
$script:accountName = 'LOCAL SERVICE'
if (-not $AtLogon -and -not $AsSystem) {
    try {
        $script:accountName = (New-Object System.Security.Principal.SecurityIdentifier($LocalServiceSid)).Translate([System.Security.Principal.NTAccount]).Value
    } catch { }
    if ($NoGrant) {
        "No folder rights were changed (-NoGrant). $script:accountName needs: modify on the config's folder, data_dir, cache_dir, uploads_dir and the log; read on the libraries and on Python and pluteus."
    } else {
        "Rights for ${script:accountName}:"
        # The config's folder: the editors write a temporary file and config.toml.bak beside the config. Never when that
        # folder also holds the program (the server could then rewrite itself): the config file alone, and a warning.
        $configHoldsCode = $false
        foreach ($dir in $codeDirs) { if (Test-Inside $dir $configDir) { $configHoldsCode = $true } }
        if ($configHoldsCode) {
            Grant-Access $Config 'M' 'the config file'
            Write-Warning "The config is in a folder that also holds the program ($configDir), so only the file itself was made writable. The admin page's Users and Libraries tabs cannot save there (they write beside the file). Move the config to a folder of its own, for example C:\ProgramData\pluteus, and run this script again."
        } else {
            Grant-Access $configDir 'M' "the config's folder"
        }
        foreach ($entry in $writable) {
            if (Test-NetworkPath $entry[0]) { continue }
            if (-not (Test-Path -LiteralPath $entry[0])) { New-Item -ItemType Directory -Force -Path $entry[0] | Out-Null }
            Grant-Access $entry[0] 'M' $entry[1]
        }
        if ($configHoldsCode -or -not (Test-Inside $Log $configDir)) {
            if (-not (Test-Path -LiteralPath $Log)) { New-Item -ItemType File -Path $Log | Out-Null }
            Grant-Access $Log 'M' 'the log file'
        }
        foreach ($entry in $readable) {
            if (Test-NetworkPath $entry[0]) { continue }
            Grant-Access $entry[0] 'RX' $entry[1]
        }
        # Program folders are readable by every account already, except inside a user profile.
        $profiles = Split-Path -Parent ([Environment]::GetFolderPath('UserProfile'))
        foreach ($dir in $codeDirs) {
            if ($profiles -and (Test-Inside $dir $profiles)) { Grant-Access $dir 'RX' 'the program (inside a user profile)' }
        }
    }
}

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
    $mode = "at logon of $who"
} elseif ($AsSystem) {
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
    $mode = 'at boot, as SYSTEM'
} else {
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $principal = New-ScheduledTaskPrincipal -UserId $script:accountName -LogonType ServiceAccount -RunLevel Limited
    $mode = "at boot, as $script:accountName"
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
"Registered '$TaskName' ($mode)."
"  command : $Python -m pluteus supervise --config $Config"
"  log     : $Log"
if ($Start) {
    Start-ScheduledTask -TaskName $TaskName
    "Started. Look at the log, or open the address from your config."
} else {
    "Start it now with:  Start-ScheduledTask -TaskName $TaskName"
}
"Remove it with:  .\tools\install-task.ps1 -Remove -TaskName $TaskName"
