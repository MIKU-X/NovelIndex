[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$PythonPath, [PSCredential]$Credential,
      [switch]$PromptForCredential, [switch]$CheckNetwork)
$ErrorActionPreference = 'Stop'
if ($PromptForCredential) {
    $Credential = Get-Credential -UserName ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) `
        -Message 'Validate a non-interactive scheduled task. Enter your Windows password, not the Hello PIN.'
    if (-not $Credential) { throw 'Credential entry was cancelled; no task was created.' }
}
$root = Split-Path $PSScriptRoot -Parent
$id = [guid]::NewGuid().ToString('N')
$fixture = Join-Path $root ('.runtime\scheduler test 中文 & !\' + $id)
$scripts = Join-Path $fixture 'scripts'
New-Item -ItemType Directory -Path $scripts -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'scheduled_run.py') -Destination $scripts
# A fixture stands in for run_all.py. It runs through the exact production action
# and wrapper, without touching the real data, cache, credentials or pipeline.
$stub = @'
import ctypes, json, os, pathlib, sys, urllib.request
root = pathlib.Path(__file__).resolve().parent
assert pathlib.Path.cwd() == root
assert sys.stdin.read() == ''
print('无人值守标准输出', flush=True)
print('无人值守标准错误', file=sys.stderr, flush=True)
code = int((root / 'exit.txt').read_text())
session = ctypes.c_ulong()
if not ctypes.windll.kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(session)):
    raise OSError('Cannot read the task session ID')
network = 'not_requested'
if (root / 'check_network').exists():
    with urllib.request.urlopen('https://example.com/', timeout=15) as response:
        network = response.status
(root / 'result.json').write_text(json.dumps({'cwd_ok': True, 'stdin_closed': True, 'exit_code': code,
                                            'session_id': session.value, 'network_status': network}))
raise SystemExit(code)
'@
[IO.File]::WriteAllText((Join-Path $fixture 'run_all.py'), $stub, [Text.UTF8Encoding]::new($false))
if ($CheckNetwork) { [IO.File]::WriteAllText((Join-Path $fixture 'check_network'), '1') }
$name = 'NovelIndex_Verify_' + $id
$params = @{Schedule='Once'; TaskName=$name; PythonPath=$PythonPath; ProjectRoot=$fixture}
if ($Credential) { $params.Credential = $Credential }
$registered = $false
$report = @()
try {
    & (Join-Path $PSScriptRoot 'register_task.ps1') @params | Out-Null
    $registered = $true
    $actualLogon = [string](Get-ScheduledTask -TaskName $name).Principal.LogonType
    $expectedLogon = if ($Credential) { 'Password' } else { 'Interactive' }
    if ($actualLogon -ne $expectedLogon) { throw "Unexpected logon type: $actualLogon" }
    foreach ($expected in @(0,7)) {
        [IO.File]::WriteAllText((Join-Path $fixture 'exit.txt'), [string]$expected)
        $start = Get-Date
        Start-ScheduledTask -TaskName $name
        $deadline = (Get-Date).AddSeconds(45)
        do {
            Start-Sleep -Milliseconds 500
            $info = Get-ScheduledTaskInfo -TaskName $name
            $state = (Get-ScheduledTask -TaskName $name).State
            if ($info.LastRunTime -ge $start.AddSeconds(-1) -and $state -ne 'Running' -and
                $info.LastTaskResult -eq $expected) { break }
        } while ((Get-Date) -lt $deadline)
        if ($info.LastTaskResult -ne $expected -or $state -eq 'Running') {
            throw "Task did not finish with expected result $expected (actual $($info.LastTaskResult), $state)"
        }
        $result = Get-Content -Raw -LiteralPath (Join-Path $fixture 'result.json') | ConvertFrom-Json
        if ($result.exit_code -ne $expected -or -not $result.cwd_ok -or -not $result.stdin_closed) {
            throw 'Fixture result did not match.'
        }
        if ($Credential -and $result.session_id -ne 0) { throw 'Password task did not run in non-interactive session 0.' }
        $log = Get-ChildItem -LiteralPath (Join-Path $fixture 'logs\scheduled') -Filter '*.log' |
            Sort-Object LastWriteTime | Select-Object -Last 1
        $content = [IO.File]::ReadAllText($log.FullName)
        if (-not ($content.Contains('无人值守标准输出') -and $content.Contains('无人值守标准错误') -and
                  $content.Contains("exit_code=$expected"))) { throw 'Missing output or exit status in log.' }
        $report += @{expected=$expected; last_task_result=$info.LastTaskResult; cwd_ok=$true;
                     stdin_closed=$true; utf8_stdout_stderr=$true; log=$log.FullName;
                     logon_type=$actualLogon; session_id=$result.session_id; network_status=$result.network_status}
        # LastRunTime has second resolution; ensure the next invocation is distinct.
        Start-Sleep -Milliseconds 1100
    }
    $report | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $fixture 'verification.json') -Encoding UTF8
    Write-Host "PASS: $fixture"
} finally {
    if ($registered) {
        if ((Get-ScheduledTask -TaskName $name).State -eq 'Running') { Stop-ScheduledTask -TaskName $name }
        Unregister-ScheduledTask -TaskName $name -Confirm:$false
    }
}
