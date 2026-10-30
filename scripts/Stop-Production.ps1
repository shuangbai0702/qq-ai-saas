$ErrorActionPreference='Stop'
$taskRoot=Split-Path -Parent $PSScriptRoot
$taskInfo=Get-Content -LiteralPath (Join-Path $taskRoot '.local/production-process.json') -Raw | ConvertFrom-Json
[IO.File]::WriteAllText((Join-Path $taskRoot '.local/production.stop'),[string]$taskInfo.api)
for($taskWait=0;$taskWait -lt 60;$taskWait++) {
  if(-not (Get-Process -Id $taskInfo.api -ErrorAction SilentlyContinue)){Write-Host '生产模式已优雅停止';exit 0}
  Start-Sleep -Seconds 1
}
throw '未确认服务退出，请查看日志；不会自动强制终止未知进程。'
