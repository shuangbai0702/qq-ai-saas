param([string]$Operation='inspect')
$ErrorActionPreference='Stop'
$c=Get-Content 'C:/FishBotPredeploy20261112/mobile-private-config.json' -Raw|ConvertFrom-Json
$taskName='FishBot B Release 20261113'
if(Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue){throw 'Existing task owner'}
$action=New-ScheduledTaskAction -Execute $c.node -Argument ('C:/FishBotPredeploy20261112/control/b-release-20261113/operator-release.mjs '+$Operation) -WorkingDirectory $c.sourceRoot
$principal=New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
Register-ScheduledTask -TaskName $taskName -Action $action -Principal $principal | Out-Null
$started=[DateTime]::UtcNow
try{
 Start-ScheduledTask -TaskName $taskName
 Start-Sleep -Seconds 3
 for($i=0;$i -lt 360;$i++){if((Get-ScheduledTask -TaskName $taskName).State -ne 'Running'){break};Start-Sleep -Seconds 1}
 if((Get-ScheduledTask -TaskName $taskName).State -eq 'Running'){throw 'Operator still running'}
 $p='C:/FishBotPredeploy20261112/control/b-release-20261113/'+$Operation+'-result.json'
 if((Get-Item -LiteralPath $p).LastWriteTimeUtc -lt $started){throw 'Stale operator result'}
 $r=Get-Content -LiteralPath $p -Raw|ConvertFrom-Json
 if(-not $r.ok){throw ('Release operator failed: '+$r.error)}
 if((Get-ScheduledTaskInfo -TaskName $taskName).LastTaskResult -ne 0){throw 'Release task failed'}
 $r|ConvertTo-Json -Depth 20 -Compress
}finally{if((Get-ScheduledTask -TaskName $taskName).State -ne 'Running'){Unregister-ScheduledTask -TaskName $taskName -Confirm:$false}}
