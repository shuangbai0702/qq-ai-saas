param(
    [Parameter(Mandatory=$true)][string]$Config,
    [string]$TaskName = 'FishBot Production Supervisor',
    [ValidateSet('NetworkService','LocalService')][string]$ServiceAccount = 'NetworkService',
    [switch]$Preflight,
    [switch]$Start,
    [switch]$PlanOnly
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'windows-service-acl.ps1')
$taskConfigPath = (Resolve-Path -LiteralPath $Config).Path
$taskConfig = Get-Content -LiteralPath $taskConfigPath -Raw | ConvertFrom-Json
$taskPython = (Resolve-Path -LiteralPath $taskConfig.python).Path
$taskScript = Join-Path $taskConfig.sourceRoot 'scripts/windows-production-supervisor.py'
if (-not (Test-Path -LiteralPath $taskScript -PathType Leaf)) {throw 'Supervisor script is missing'}
foreach ($taskPath in @($taskConfigPath,$taskScript,$taskPython)) {
    if ($taskPath.Contains('"')) {throw 'Task paths cannot contain quotes'}
}
$taskArguments = '-B "' + $taskScript + '" --config "' + $taskConfigPath + '"'
if ($Preflight) {$taskArguments += ' --preflight'}
$taskSid = if ($ServiceAccount -eq 'NetworkService') {'S-1-5-20'} else {'S-1-5-19'}
$taskRead = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
$taskModify = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
function Add-TaskAccessPath([string]$Path,[bool]$Modify) {
    if (-not [IO.Path]::IsPathRooted($Path) -or $Path -match '^[A-Za-z]:[^\\/]') {throw 'ACL roots must be absolute'}
    $taskFullPath = [IO.Path]::GetFullPath($Path)
    # Resolve junctions through their actual target. icacls /T on a junction
    # does not reliably traverse the existing Python version directory.
    $taskCanonical = @(& $taskPython -I -S -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' $taskFullPath)
    if ($LASTEXITCODE -ne 0 -or $taskCanonical.Count -ne 1) {throw 'Cannot resolve the exact ACL target'}
    $taskFullPath = [IO.Path]::GetFullPath([string]$taskCanonical[0])
    if ($taskFullPath.TrimEnd('\','/') -eq [IO.Path]::GetPathRoot($taskFullPath).TrimEnd('\','/')) {throw 'Refusing drive-root ACL grant'}
    if ($Modify) {[void]$taskModify.Add($taskFullPath)} else {[void]$taskRead.Add($taskFullPath)}
}
Add-TaskAccessPath $taskConfig.sourceRoot $false
foreach ($taskExplicitReadRoot in @($taskConfig.readOnlyRoots)) {
    if ($taskExplicitReadRoot) {Add-TaskAccessPath $taskExplicitReadRoot $false}
}
Add-TaskAccessPath ([IO.Path]::GetDirectoryName($taskConfig.node)) $false
Add-TaskAccessPath ([IO.Path]::GetDirectoryName($taskConfig.postgresBin.TrimEnd('\','/'))) $false
foreach ($taskPythonPath in @($taskConfig.python,$taskConfig.environment.ASTRBOT_PYTHON)) {
    if (-not $taskPythonPath) {continue}
    $taskPythonRoot = [IO.Path]::GetDirectoryName($taskPythonPath)
    if ([IO.Path]::GetFileName($taskPythonRoot) -eq 'Scripts') {$taskPythonRoot = [IO.Path]::GetDirectoryName($taskPythonRoot)}
    Add-TaskAccessPath $taskPythonRoot $false
}
foreach ($taskPrivateDirectory in @($taskConfig.dataRoot,$taskConfig.stateRoot,$taskConfig.postgresData)) {Add-TaskAccessPath $taskPrivateDirectory $true}
$taskReadFiles = @($taskConfigPath,$taskConfig.environment.PGPASSWORD_FILE) | Where-Object {$_}
$taskExecuteFiles = @($taskConfig.cloudflared) | Where-Object {$_}
if ($taskConfig.dnsSyncConfig) {
    $taskDnsConfigPath = (Resolve-Path -LiteralPath $taskConfig.dnsSyncConfig).Path
    $taskDnsConfig = Get-Content -LiteralPath $taskDnsConfigPath -Raw | ConvertFrom-Json
    $taskReadFiles += @($taskDnsConfigPath,$taskDnsConfig.tokenFile)
    foreach ($taskGatePath in @($taskDnsConfig.authorityFile,$taskDnsConfig.routeEnabledFile)) {Add-TaskAccessPath ([IO.Path]::GetDirectoryName($taskGatePath)) $false}
    Add-TaskAccessPath ([IO.Path]::GetDirectoryName($taskDnsConfig.stateFile)) $true
}
if ($taskConfig.executionAuthority) {
    # Grant inheritance from this one private parent so a later atomic authority
    # replacement remains readable without granting the service write authority.
    Add-TaskAccessPath ([IO.Path]::GetDirectoryName($taskConfig.executionAuthority)) $false
}
if ($PlanOnly) {
    [pscustomobject]@{task=$TaskName;serviceAccount=$ServiceAccount;sid=$taskSid;runLevel='Limited';preflight=[bool]$Preflight;readDirectories=@($taskRead);modifyDirectories=@($taskModify);readFiles=@($taskReadFiles);executeFiles=@($taskExecuteFiles)} | ConvertTo-Json -Depth 3
    return
}
function Grant-TaskAccess([string]$Path,[string]$Permission,[bool]$Directory) {
    Grant-FishBotServiceAccess -Path $Path -Sid $taskSid -Permission $Permission -Directory $Directory
}
foreach ($taskReadDirectory in $taskRead) {Grant-TaskAccess $taskReadDirectory 'RX' $true}
foreach ($taskModifyDirectory in $taskModify) {Grant-TaskAccess $taskModifyDirectory 'M' $true}
foreach ($taskReadFile in $taskReadFiles) {Grant-TaskAccess $taskReadFile 'R' $false}
foreach ($taskExecuteFile in $taskExecuteFiles) {Grant-TaskAccess $taskExecuteFile 'RX' $false}
$taskAction = New-ScheduledTaskAction -Execute $taskPython -Argument $taskArguments -WorkingDirectory $taskConfig.sourceRoot
$taskTrigger = New-ScheduledTaskTrigger -AtStartup
$taskAccountName = ([Security.Principal.SecurityIdentifier]::new($taskSid)).Translate([Security.Principal.NTAccount]).Value
$taskPrincipal = New-ScheduledTaskPrincipal -UserId $taskAccountName -LogonType ServiceAccount -RunLevel Limited
$taskSettings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $TaskName -Action $taskAction -Trigger $taskTrigger -Principal $taskPrincipal -Settings $taskSettings -Force | Out-Null
if ($Start) {Start-ScheduledTask -TaskName $TaskName}
Write-Output ('Registered ' + $ServiceAccount + ' startup task: ' + $TaskName + '; preflight=' + [bool]$Preflight)
