# Builds pluteus-admin.exe: one file, no Python needed on the machine that runs it.
# It uses a throwaway environment (so PyInstaller never ends up in the project's own venv) and leaves the result in
# the output folder. Needs Python 3.11+ with tkinter on the machine that builds.
#   .\tools\build-admin-exe.ps1                          # output goes to C:\Temp\Claude\admin-build\dist
#   .\tools\build-admin-exe.ps1 -Output D:\somewhere
param(
    [string]$Output = 'C:\Temp\Claude\admin-build',
    [string]$Python = 'python'
)
# Windows PowerShell 5.1 turns anything a program writes to stderr into an error; PyInstaller logs there normally,
# so success is judged by exit codes instead.
$ErrorActionPreference = 'Continue'
$repo = Split-Path -Parent $PSScriptRoot
$venv = Join-Path $Output 'venv'
New-Item -ItemType Directory -Force -Path $Output | Out-Null

if (-not (Test-Path (Join-Path $venv 'Scripts\python.exe'))) { & $Python -m venv $venv }
$py = Join-Path $venv 'Scripts\python.exe'
& $py -m pip install --quiet --upgrade pip pyinstaller
if ($LASTEXITCODE -ne 0) { throw 'could not install PyInstaller' }

# The admin app needs only the standard library plus the pluteus package itself (config, auth, useredit).
Push-Location $repo
try {
    & $py -m PyInstaller --noconfirm --onefile --windowed --name pluteus-admin `
        --paths $repo --distpath (Join-Path $Output 'dist') --workpath (Join-Path $Output 'work') `
        --specpath (Join-Path $Output 'spec') (Join-Path $repo 'tools\pluteus_admin_entry.py')
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed' }
} finally { Pop-Location }

$exe = Join-Path $Output 'dist\pluteus-admin.exe'
"Built $exe  ($([math]::Round((Get-Item $exe).Length / 1MB, 1)) MB)"
