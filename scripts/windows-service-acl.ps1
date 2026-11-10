function Grant-FishBotServiceAccess {
    param(
        [Parameter(Mandatory=$true)][string]$Path,
        [Parameter(Mandatory=$true)][ValidateSet('S-1-5-19','S-1-5-20')][string]$Sid,
        [Parameter(Mandatory=$true)][ValidateSet('R','RX','M')][string]$Permission,
        [Parameter(Mandatory=$true)][bool]$Directory
    )
    $taskAclPath = [IO.Path]::GetFullPath($Path)
    if ($taskAclPath.TrimEnd('\','/') -eq [IO.Path]::GetPathRoot($taskAclPath).TrimEnd('\','/')) {throw 'Refusing drive-root ACL grant'}
    if (-not (Test-Path -LiteralPath $taskAclPath)) {throw 'Required ACL target does not exist'}
    # Existing protected files need an explicit ACE without directory-only
    # inheritance flags. A recursive inheritable grant alone is insufficient.
    $taskExplicitGrant = '*' + $Sid + ':' + $Permission
    if ($Directory) {& icacls.exe $taskAclPath /grant:r $taskExplicitGrant /T /Q | Out-Null} else {& icacls.exe $taskAclPath /grant:r $taskExplicitGrant /Q | Out-Null}
    if ($LASTEXITCODE -ne 0) {throw 'Existing-file service ACL grant failed'}
    if ($Directory) {
        $taskFutureGrant = '*' + $Sid + ':(OI)(CI)' + $Permission
        & icacls.exe $taskAclPath /grant:r $taskFutureGrant /Q | Out-Null
        if ($LASTEXITCODE -ne 0) {throw 'Future-file service ACL grant failed'}
    }
}
