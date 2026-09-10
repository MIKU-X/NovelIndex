param([switch]$Check, [string]$PythonPath)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
if (-not $PythonPath) {
    $venv = Join-Path $root '.venv\Scripts\python.exe'
    if (Test-Path -LiteralPath $venv -PathType Leaf) { $PythonPath = $venv }
    else { $PythonPath = (Get-Command python.exe -ErrorAction Stop).Source }
}
$arguments = @('-X', 'utf8', (Join-Path $PSScriptRoot 'scheduled_run.py'))
if ($Check) { $arguments += '--check' }
& $PythonPath @arguments
exit $LASTEXITCODE
