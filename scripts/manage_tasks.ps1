# UTF-8 BOM is added for Windows PowerShell 5.1.
$ErrorActionPreference = 'Stop'
try {
    $names = @('FanqieZhiShu_Daily', 'FanqieZhiShu_Weekly')
    Get-ScheduledTask | Where-Object TaskName -In $names | Select-Object TaskName, State
    Write-Host '1 每日 / 2 每周 / 3 立即触发已注册任务 / 4 删除 / 其他 返回'
    $choice = Read-Host '选择'
    if ($choice -in @('1','2')) {
        $params = @{Schedule=$(if ($choice -eq '1') {'Daily'} else {'Weekly'})}
        $time = Read-Host '时间 HH:mm（留空 08:00）'
        if ($time) { $params.At = $time }
        if ($choice -eq '2') {
            $day = Read-Host '星期数字 1-7（1=周一，7=周日）'
            if ($day -notmatch '^[1-7]$') { throw '星期必须为 1-7。' }
            $params.Days = @('Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday')[[int]$day - 1]
        }
        Write-Host '1 已登录时后台运行（无需打开终端） / 2 注销后也运行（Windows 账户密码）'
        $mode = Read-Host '模式（留空 1）'
        if ($mode -eq '2') {
            $params.Credential = Get-Credential -Message '计划任务使用此账户；请输入账户密码，不是 Windows Hello PIN'
            if (-not $params.Credential) { return }
        } elseif ($mode -and $mode -ne '1') { throw '无效模式。' }
        & (Join-Path $PSScriptRoot 'register_task.ps1') @params
        Write-Host '任务已注册。完整日志位于 logs/scheduled；请运行 scripts/test_scheduled_task.ps1 验证环境。'
    } elseif ($choice -in @('3','4')) {
        $kind = Read-Host '1 每日任务 / 2 每周任务'
        if ($kind -notin @('1','2')) { return }
        $name = $names[[int]$kind - 1]
        if ($choice -eq '3') { Start-ScheduledTask -TaskName $name }
        elseif ((Read-Host "删除 $name ? 输入 YES") -ceq 'YES') {
            Unregister-ScheduledTask -TaskName $name -Confirm:$false
        }
    }
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    Write-Host '若提示拒绝访问，请在允许注册任务的 PowerShell 中执行；先完成 Python 环境安装。'
}
[void](Read-Host '按回车返回')
