$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $RepoRoot

py -3 scripts\noofy.py run @args
exit $LASTEXITCODE
