$ErrorActionPreference='Stop'
$b='C:/FishBotPredeploy20261112';$c=Get-Content "$b/mobile-private-config.json" -Raw|ConvertFrom-Json
$taskName='FishBot B API Reload 20261113'
if(Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue){throw 'Reload task already exists'}
$action=New-ScheduledTaskAction -Execute $c.python -Argument '-B C:/FishBotPredeploy20261112/control/b-release-20261113/restart-api.py' -WorkingDirectory $c.sourceRoot
$principal=New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
$settings=New-ScheduledTaskSettingsSet -Hidden -ExecutionTimeLimit (New-TimeSpan -Minutes 2)
Register-ScheduledTask -TaskName $taskName -Action $action -Principal $principal -Settings $settings | Out-Null
$started=[DateTime]::UtcNow
try{
Start-ScheduledTask -TaskName $taskName
$deadline=[DateTime]::UtcNow.AddSeconds(55)
do{Start-Sleep -Seconds 1;if((Test-Path "$b/control/b-release-20261113/api-restart-proof.json") -and (Get-Item "$b/control/b-release-20261113/api-restart-proof.json").LastWriteTimeUtc -ge $started){break}}while([DateTime]::UtcNow -lt $deadline)
if((-not(Test-Path "$b/control/b-release-20261113/api-restart-proof.json")) -or (Get-Item "$b/control/b-release-20261113/api-restart-proof.json").LastWriteTimeUtc -lt $started){throw 'Reload proof not received'}
$result=Get-Content "$b/control/b-release-20261113/api-restart-proof.json" -Raw|ConvertFrom-Json
$result|ConvertTo-Json -Compress
if(-not $result.apiReady){throw 'Reload did not confirm API readiness'}
}finally{if((Get-ScheduledTask -TaskName $taskName).State -ne 'Running'){Unregister-ScheduledTask -TaskName $taskName -Confirm:$false}}
