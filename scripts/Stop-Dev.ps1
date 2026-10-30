$ErrorActionPreference='Stop'
$taskProject=Split-Path -Parent $PSScriptRoot
$taskManifest=Join-Path $taskProject '.local/dev-processes.json'
if (-not (Test-Path -LiteralPath $taskManifest)) { throw 'No development launcher record' }
$taskProcesses=Get-Content -LiteralPath $taskManifest -Raw | ConvertFrom-Json
[IO.File]::WriteAllText((Join-Path $taskProject '.local/dev.stop'),[string]$taskProcesses.launcher)
Write-Host '已请求优雅退出：等待实例停止和数据库关闭。'
for($taskAttempt=0;$taskAttempt -lt 60;$taskAttempt++) {
  if(-not (Get-Process -Id $taskProcesses.launcher -ErrorAction SilentlyContinue)){Write-Host '开发服务已退出';exit 0}
  Start-Sleep -Seconds 1
}
throw '退出尚未完成；请检查.local/dev.stderr.log，不会自动强制终止未知进程。'
