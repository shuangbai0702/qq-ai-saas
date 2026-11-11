param(
    [Parameter(Mandatory=$true)][string]$Config,
    [Parameter(Mandatory=$true)][string]$Directory,
    [string]$TaskName = 'FishBot PostgreSQL Backup',
    [switch]$PlanOnly
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'windows-service-acl.ps1')
$taskConfigPath = (Resolve-Path -LiteralPath $Config).Path
$taskConfig = Get-Content -LiteralPath $taskConfigPath -Raw | ConvertFrom-Json
$taskPython = (Resolve-Path -LiteralPath $taskConfig.python).Path
$taskScript = (Resolve-Path -LiteralPath (Join-Path $taskConfig.sourceRoot 'scripts/postgres-backup.py')).Path
$taskPassword = (Resolve-Path -LiteralPath $taskConfig.environment.PGPASSWORD_FILE).Path
function Backup-AbsolutePath([string]$Path) {
    if (-not [IO.Path]::IsPathRooted($Path) -or $Path -match '^[A-Za-z]:[^\\/]') {throw 'Backup task paths must be absolute'}
    $taskFullPath = [IO.Path]::GetFullPath($Path)
    if ($taskFullPath.Contains('"') -or $taskFullPath.TrimEnd('\','/') -eq [IO.Path]::GetPathRoot($taskFullPath).TrimEnd('\','/')) {throw 'Invalid backup task path'}
    $taskCanonical = @(& $taskPython -I -S -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' $taskFullPath)
    if ($LASTEXITCODE -ne 0 -or $taskCanonical.Count -ne 1) {throw 'Cannot resolve backup task path'}
    $taskResolved = [IO.Path]::GetFullPath([string]$taskCanonical[0])
    if ($taskResolved.Contains('"') -or $taskResolved.TrimEnd('\','/') -eq [IO.Path]::GetPathRoot($taskResolved).TrimEnd('\','/')) {throw 'Invalid resolved backup task path'}
    return $taskResolved
}
$taskDirectory = Backup-AbsolutePath $Directory
foreach ($taskFile in @($taskConfigPath,$taskPassword,$taskScript,$taskPython)) {
    $taskResolved = Backup-AbsolutePath $taskFile
    if ($taskResolved.StartsWith($taskDirectory.TrimEnd('\','/') + [IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase)) {throw 'Backup directory cannot contain configuration or executables'}
}
$taskRead = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
[void]$taskRead.Add((Backup-AbsolutePath $taskConfig.sourceRoot))
[void]$taskRead.Add((Backup-AbsolutePath ([IO.Path]::GetDirectoryName($taskConfig.postgresBin.TrimEnd('\','/')))))
$taskPythonRoot = [IO.Path]::GetDirectoryName($taskPython)
if ([IO.Path]::GetFileName($taskPythonRoot) -eq 'Scripts') {$taskPythonRoot = [IO.Path]::GetDirectoryName($taskPythonRoot)}
[void]$taskRead.Add((Backup-AbsolutePath $taskPythonRoot))
$taskBasePython = @(& $taskPython -I -S -c 'import os,sys; print(os.path.realpath(sys.base_prefix))')
if ($LASTEXITCODE -ne 0 -or $taskBasePython.Count -ne 1) {throw 'Cannot resolve Python base directory'}
[void]$taskRead.Add((Backup-AbsolutePath ([string]$taskBasePython[0])))
$taskArguments = '-B "' + $taskScript + '" --config "' + $taskConfigPath + '" --directory "' + $taskDirectory + '" --restore-check --keep 7'
if ($PlanOnly) {
    [pscustomobject]@{task=$TaskName;sid='S-1-5-20';runLevel='Limited';dailyAt='04:15';randomDelaySeconds=900;startWhenAvailable=$true;retention=7;restoreCheck=$true;readDirectories=@($taskRead);protectedReadFiles=@($taskConfigPath,$taskPassword);protectedModifyDirectory=$taskDirectory;arguments=$taskArguments} | ConvertTo-Json -Depth 3
    return
}
if (-not (Test-Path -LiteralPath $taskDirectory)) {New-Item -ItemType Directory -Path $taskDirectory | Out-Null}
function Protect-BackupAccess([string]$Path,[bool]$Directory,[string]$ServiceRights) {
    $taskAcl = Get-Acl -LiteralPath $Path
    $taskAcl.SetAccessRuleProtection($true,$false)
    foreach ($taskRule in @($taskAcl.Access)) {[void]$taskAcl.RemoveAccessRuleSpecific($taskRule)}
    foreach ($taskIdentity in @('S-1-5-18','S-1-5-32-544','S-1-5-20')) {
        $taskRights = if ($taskIdentity -eq 'S-1-5-20') {$ServiceRights} else {'FullControl'}
        $taskInheritance = if ($Directory) {[Security.AccessControl.InheritanceFlags]'ContainerInherit,ObjectInherit'} else {[Security.AccessControl.InheritanceFlags]::None}
        $taskRule = [Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new($taskIdentity),[Security.AccessControl.FileSystemRights]$taskRights,$taskInheritance,[Security.AccessControl.PropagationFlags]::None,[Security.AccessControl.AccessControlType]::Allow)
        $taskAcl.AddAccessRule($taskRule)
    }
    Set-Acl -LiteralPath $Path -AclObject $taskAcl
}
foreach ($taskRoot in $taskRead) {Grant-FishBotServiceAccess -Path $taskRoot -Sid 'S-1-5-20' -Permission RX -Directory $true}
foreach ($taskPrivateFile in @($taskConfigPath,$taskPassword)) {Protect-BackupAccess $taskPrivateFile $false 'Read'}
Protect-BackupAccess $taskDirectory $true 'Modify'
$taskAction = New-ScheduledTaskAction -Execute $taskPython -Argument $taskArguments -WorkingDirectory $taskConfig.sourceRoot
$taskTrigger = New-ScheduledTaskTrigger -Daily -At '04:15' -RandomDelay (New-TimeSpan -Minutes 15)
$taskAccount = ([Security.Principal.SecurityIdentifier]::new('S-1-5-20')).Translate([Security.Principal.NTAccount]).Value
$taskPrincipal = New-ScheduledTaskPrincipal -UserId $taskAccount -LogonType ServiceAccount -RunLevel Limited
$taskSettings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 1) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $TaskName -Action $taskAction -Trigger $taskTrigger -Principal $taskPrincipal -Settings $taskSettings -Force | Out-Null
Write-Output 'Registered NetworkService daily PostgreSQL backup with isolated restore checks and seven owned backup pairs'
