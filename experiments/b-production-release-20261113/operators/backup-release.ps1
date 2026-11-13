param([switch]$CheckStopped)
$ErrorActionPreference='Stop'
$base='C:/FishBotPredeploy20261112/control/b-release-20261113'
$cfg=Get-Content 'C:/FishBotPredeploy20261112/mobile-private-config.json' -Raw|ConvertFrom-Json
$taskName='FishBot B Private Backup 20261113'
if($CheckStopped){$task=Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue;if($task -and $task.State -eq 'Running'){throw 'Backup owner still running; keep release held'};@{ok=$true;backupStopped=$true}|ConvertTo-Json -Compress;exit 0}
if(Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue){throw 'Backup task already exists'}
$action=New-ScheduledTaskAction -Execute $cfg.python -Argument ('-B '+$base+'/backup-release.py') -WorkingDirectory $cfg.sourceRoot
$principal=New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
$settings=New-ScheduledTaskSettingsSet -Hidden -ExecutionTimeLimit (New-TimeSpan -Minutes 23)
Register-ScheduledTask -TaskName $taskName -Action $action -Principal $principal -Settings $settings|Out-Null
$started=[DateTime]::UtcNow
try{
 Start-ScheduledTask -TaskName $taskName
 $deadline=[DateTime]::UtcNow.AddMinutes(22)
 do{
  Start-Sleep -Seconds 1
  $task=Get-ScheduledTask -TaskName $taskName
  if($task.State -ne 'Running' -and [DateTime]::UtcNow -gt $started.AddSeconds(3)){break}
 }while([DateTime]::UtcNow -lt $deadline)
 if((Get-ScheduledTask -TaskName $taskName).State -eq 'Running'){throw 'Private backup still running; do not install or auto-resume'}
 if((Get-ScheduledTaskInfo -TaskName $taskName).LastTaskResult -ne 0){throw 'Private backup failed; installation prohibited'}
 $proof=$base+'/backup-result.json'
 if((-not(Test-Path -LiteralPath $proof)) -or (Get-Item -LiteralPath $proof).LastWriteTimeUtc -lt $started){throw 'Fresh private backup proof missing'}
 $result=Get-Content -LiteralPath $proof -Raw|ConvertFrom-Json
 if(-not($result.ok -and $result.complete -and $result.postgresVerified -and $result.aclRestricted -and $result.vssRemoved)){throw 'Private backup incomplete'}
 $result|ConvertTo-Json -Depth 8 -Compress
}finally{
 if((Get-ScheduledTask -TaskName $taskName).State -ne 'Running'){Unregister-ScheduledTask -TaskName $taskName -Confirm:$false}
}
