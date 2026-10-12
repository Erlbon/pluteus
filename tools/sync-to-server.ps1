# Copies what Docker needs to build pluteus into the server's pluteus folder (the "src" subfolder), and nothing else:
# no .git, no virtualenv, no tests. Run it from anywhere; safe to repeat. Then on the server:
#   docker compose build --pull
#   docker compose up -d
param(
    [string]$Source = (Split-Path -Parent $PSScriptRoot),
    [string]$Destination = 'Z:\temp\_docker\pluteus\src'
)

if (-not (Test-Path (Join-Path $Source 'pluteus\app.py'))) { throw "Not a pluteus checkout: $Source" }
New-Item -ItemType Directory -Force -Path $Destination | Out-Null

# /MIR makes src an exact copy of the checkout, so files deleted from the code are also removed from src.
robocopy $Source $Destination /MIR /NFL /NDL /NJH /NJS /NP `
    /XD .git .githooks .venv build dist tests tools docs __pycache__ .pytest_cache pluteus.egg-info `
    /XF *.pyc config.toml .gitignore .gitattributes | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy failed with exit code $LASTEXITCODE" }
$global:LASTEXITCODE = 0      # robocopy uses 1..7 for "copied something": that is success

"Copied to $Destination"
Get-ChildItem $Destination | Select-Object -ExpandProperty Name
