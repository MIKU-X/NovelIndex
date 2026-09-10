# Windows PowerShell 5.1+. Task actions never pass through cmd.exe.
[CmdletBinding()]
param(
    [ValidateSet('Daily', 'Weekly', 'Once')][string]$Schedule = 'Daily',
    [ValidatePattern('^(?:[01][0-9]|2[0-3]):[0-5][0-9]$')][string]$At = '08:00',
    [ValidateSet('Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday')]
    [string[]]$Days = @('Monday'),
    [string]$PythonPath,
    [string]$ProjectRoot,
    [ValidatePattern('^[A-Za-z0-9_-]+$')][string]$TaskName,
    [PSCredential]$Credential,
    [switch]$Check,
    [switch]$PrintOnly
)
$ErrorActionPreference = 'Stop'
if (-not $ProjectRoot) { $ProjectRoot = Split-Path $PSScriptRoot -Parent }
$root = (Resolve-Path -LiteralPath $ProjectRoot).Path
$runner = Join-Path $root 'scripts\scheduled_run.py'
if (-not (Test-Path -LiteralPath $runner -PathType Leaf)) { throw "Missing runner: $runner" }
if (-not $PythonPath) {
    $venv = Join-Path $root '.venv\Scripts\python.exe'
    if (Test-Path -LiteralPath $venv -PathType Leaf) { $PythonPath = $venv }
    else { $PythonPath = (Get-Command python.exe -ErrorAction Stop).Source }
}
$python = (Resolve-Path -LiteralPath $PythonPath).Path
# Validate before replacing an existing task; reject Store aliases / wrong runtime.
& $python -X utf8 -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)'
if ($LASTEXITCODE -ne 0) { throw 'Python 3.10+ is required.' }
$pythonw = Join-Path (Split-Path $python -Parent) 'pythonw.exe'
if (Test-Path -LiteralPath $pythonw -PathType Leaf) { $execute = $pythonw }
else { throw "pythonw.exe is required for a hidden scheduled task: $pythonw" }
$arguments = '-X utf8 "' + $runner + '"'
if ($Check) { $arguments += ' --check' }
if (-not $TaskName) { $TaskName = 'FanqieZhiShu_' + $Schedule }
if ($PrintOnly) {
    [ordered]@{TaskName=$TaskName; Execute=$execute; Arguments=$arguments; WorkingDirectory=$root;
        Schedule=$Schedule; At=$At; Days=$Days; LogonType=$(if ($Credential) {'Password'} else {'Interactive'})} |
        ConvertTo-Json
    return
}
$action = New-ScheduledTaskAction -Execute $execute -Argument $arguments -WorkingDirectory $root
$time = [datetime]::Today.Add([timespan]::ParseExact($At, 'hh\:mm', [cultureinfo]::InvariantCulture))
switch ($Schedule) {
    'Daily' { $trigger = New-ScheduledTaskTrigger -Daily -At $time }
    'Weekly' { $trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $Days -At $time }
    'Once' { $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(5) }
}
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 12)
$user = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$logon = 'Interactive'
if ($Credential) { $user = $Credential.UserName; $logon = 'Password' }
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType $logon -RunLevel Limited
$task = New-ScheduledTask -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
    -Description 'NovelIndex: non-interactive collection/build; logs/scheduled; one instance per task.'
if ($Credential) {
    # Windows stores credentials; never put the password in an argument or file.
    Register-ScheduledTask -TaskName $TaskName -InputObject $task -User $user `
        -Password $Credential.GetNetworkCredential().Password -Force
} else {
    Register-ScheduledTask -TaskName $TaskName -InputObject $task -Force
}
