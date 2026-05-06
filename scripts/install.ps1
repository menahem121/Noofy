$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $RepoRoot

py -3 scripts\noofy.py install @args
exit $LASTEXITCODE
