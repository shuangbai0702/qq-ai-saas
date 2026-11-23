param(
 [Parameter(Mandatory=$true)][string]$Executable,
 [Parameter(Mandatory=$true)][string]$Config,
 [Parameter(Mandatory=$true)][string]$StateDirectory,
 [Parameter(Mandatory=$true)][ValidatePattern('^[a-fA-F0-9]{64}$')][string]$ExpectedSha256
)
$ErrorActionPreference='Stop'
$taskName='FishBotQQEgress'
if(Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue){throw 'QQ egress task already exists; inspect it before replacement'}
$exe=(Resolve-Path -LiteralPath $Executable).Path
$configFile=(Resolve-Path -LiteralPath $Config).Path
$state=[IO.Path]::GetFullPath($StateDirectory)
if((Get-FileHash -LiteralPath $exe -Algorithm SHA256).Hash -ne $ExpectedSha256){throw 'Mihomo executable digest mismatch'}
New-Item -ItemType Directory -Path $state -Force | Out-Null
# One unprivileged native core; QQ is never a child of this task.
& icacls.exe $state /grant '*S-1-5-19:(OI)(CI)M' | Out-Null
if($LASTEXITCODE -ne 0){throw 'Could not grant core state access'}
foreach($file in @($exe,$configFile)){
 & icacls.exe $file /grant '*S-1-5-19:RX' | Out-Null
 if($LASTEXITCODE -ne 0){throw 'Could not grant core input access'}
}
$arguments='-d "'+$state+'" -f "'+$configFile+'"'
$action=New-ScheduledTaskAction -Execute $exe -Argument $arguments -WorkingDirectory $state
$principal=New-ScheduledTaskPrincipal -UserId 'S-1-5-19' -LogonType ServiceAccount
$settings=New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $taskName -Action $action -Principal $principal -Trigger (New-ScheduledTaskTrigger -AtStartup) -Settings $settings -Description 'Single loopback Mihomo core for native QQ SOCKS5 egress; independent from FishBot Agent and QQ.' | Out-Null
Start-ScheduledTask -TaskName $taskName
Write-Output 'Independent QQ egress core task installed.'
