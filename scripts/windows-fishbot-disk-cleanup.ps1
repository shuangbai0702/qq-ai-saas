param(
    [string]$DeploymentRoot = 'C:\FishBotPredeploy20261112',
    [string]$MigrationRoot = 'C:\FishBotMigration20261110',
    [ValidateRange(1,60)][int]$FreeTargetGiB=30,
    [switch]$Apply
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$config = Join-Path $DeploymentRoot 'mobile-agent-trial-config.json'
$live = Get-Content -LiteralPath $config -Raw | ConvertFrom-Json
$sources = @($live.sourceRoot,$live.dataRoot,$live.stateRoot,$live.postgresData,$live.postgresBin,$live.node,$live.python)
if (@($sources | Where-Object { -not $_ -or -not [IO.Path]::IsPathRooted($_) }).Count) {throw 'Unverified production paths'}
$mutex = [Threading.Mutex]::new($false,'Global\FishBotDiskCleanup')
if (-not $mutex.WaitOne(0)) {$mutex.Dispose();return}
try {
    # A running deploy is allowed to finish before its input artifacts are pruned.
    $processes = @(Get-CimInstance Win32_Process | Where-Object ProcessId -ne $PID | ForEach-Object {
        "$($_.ExecutablePath) $($_.CommandLine)"
    })
    $actions = @(Get-ScheduledTask -TaskName 'FishBot*' | Where-Object State -ne 'Disabled' | ForEach-Object {
        foreach ($action in $_.Actions) { "$($action.Execute) $($action.Arguments) $($action.WorkingDirectory)" }
    })
    $references = ($sources + $processes + $actions) -join "`n"
    $references = $references.Replace('/','\')
    $stalePackage = [DateTime]::UtcNow.AddHours(-12)
    $week = [DateTime]::UtcNow.AddDays(-7)
    $files = @(
        @{Root=$DeploymentRoot; Since=$stalePackage; Pattern='^(?:dsh-(?:final|four|reminder|runtime|source)|release-delta)-[a-f0-9]{7,40}\.tar\.gz$|^mobile-ui-web\.tar\.gz$'}
        @{Root=(Join-Path $DeploymentRoot 'control'); Since=$week; Pattern='^validation-[a-z][a-z0-9_.-]*\.log$'}
        @{Root=$MigrationRoot; Since=$week; Pattern='^(?:postgresql\.zip|NapCat\.Shell\.Windows\.Node\.zip|preflight-source\.tar\.gz|runtime-(?:essential-objects|precopy)\.tar\.gz|ffmpeg-9\.0\.2-essentials_build\.7z|release-delta-[a-f0-9]{7,40}\.tar\.gz)$'}
    )
    $drive = [IO.DriveInfo]::new([IO.Path]::GetPathRoot($DeploymentRoot))
    $before = $drive.AvailableFreeSpace
    $removed = 0
    $failed = 0
    $eligible = 0
    $bytes = 0L
    $treeEligible = 0
    $treeRemoved = 0
    $treeFailure = @()
    foreach ($rule in $files) {
        $root = Get-Item -LiteralPath $rule.Root
        if (-not $root.PSIsContainer -or ($root.Attributes -band [IO.FileAttributes]::ReparsePoint)) {throw 'Invalid cleanup root'}
        $base = $root.FullName.TrimEnd('\') + '\'
        foreach ($entry in Get-ChildItem -LiteralPath $base -File -Force) {
            if ($entry.Name -notmatch $rule.Pattern -or $entry.LastWriteTimeUtc -ge $rule.Since) { continue }
            $target = [IO.Path]::GetFullPath($entry.FullName)
            if (-not $target.StartsWith($base,[StringComparison]::OrdinalIgnoreCase) -or
                [IO.Path]::GetDirectoryName($target) -ne $root.FullName -or
                ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
                $references.IndexOf($target,[StringComparison]::OrdinalIgnoreCase) -ge 0) { continue }
            $eligible++
            $bytes += $entry.Length
            if ($Apply) {
                try {
                    $check = Get-Item -LiteralPath $target
                    if ($check.PSIsContainer -or ($check.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
                        $check.Length -ne $entry.Length -or $check.LastWriteTimeUtc -ne $entry.LastWriteTimeUtc) {continue}
                    Remove-Item -LiteralPath $target -Force
                    $removed++
                } catch [System.Management.Automation.ItemNotFoundException] {
                    continue
                } catch {
                    $failed++
                }
            }
        }
    }
    if ($drive.AvailableFreeSpace -lt $FreeTargetGiB * 1GB) {
        $candidateDirs = [Collections.Generic.List[IO.DirectoryInfo]]::new()
        $staleStage = [DateTime]::UtcNow.AddHours(-24)
        $oldRollback = [DateTime]::UtcNow.AddDays(-3)
        $control = Join-Path $DeploymentRoot 'control'
        if (Test-Path -LiteralPath $control) {
            $jobs = @(Get-ChildItem -LiteralPath $control -Directory | Where-Object {
                $_.Name -match '^(?:[a-z][a-z0-9-]*-20\d{6}(?:-v\d+)?|platform-referral-[a-f0-9]{7}|referral-activation-[a-f0-9]{7})$' -and
                -not ($_.Attributes -band [IO.FileAttributes]::ReparsePoint)
            })
            $rollbackJobs = @($jobs | Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 3 -ExpandProperty FullName)
            foreach ($job in $jobs) {
                if ($job.LastWriteTimeUtc -ge $staleStage) {continue}
                foreach ($piece in Get-ChildItem -LiteralPath $job.FullName -Directory) {
                    if (($piece.Name -in @('staged','stage','candidate-source','candidate','scratch','long-scratch','continuation-scratch','ffmpeg-parts') -and
                         $piece.LastWriteTimeUtc -lt $staleStage) -or
                        ($piece.Name -in @('backup','program-backup','source-backup') -and
                         $piece.LastWriteTimeUtc -lt $oldRollback -and $rollbackJobs -notcontains $job.FullName)) {
                        $candidateDirs.Add($piece)
                    }
                }
            }
            $sandbox = Join-Path $control 'test-temp'
            if (Test-Path -LiteralPath $sandbox -PathType Container) {
                $dir = Get-Item -LiteralPath $sandbox
                if ($dir.LastWriteTimeUtc -lt $oldRollback) {$candidateDirs.Add($dir)}
            }
        }
        foreach ($layout in @(@{Name='staged';Keep=0},@{Name='web-stages';Keep=2},@{Name='releases';Keep=1})) {
            $parent = Join-Path $DeploymentRoot $layout.Name
            if (-not (Test-Path -LiteralPath $parent -PathType Container)) {continue}
            $builds = @(Get-ChildItem -LiteralPath $parent -Directory | Where-Object {
                $_.Name -match '^[a-f0-9]{7,40}$' -and
                $references.IndexOf($_.FullName,[StringComparison]::OrdinalIgnoreCase) -lt 0
            } | Sort-Object LastWriteTimeUtc -Descending)
            foreach ($dir in ($builds | Select-Object -Skip $layout.Keep)) {
                if ($dir.LastWriteTimeUtc -lt $staleStage) {$candidateDirs.Add($dir)}
            }
        }
        foreach ($name in @('precopy-runtime','content-objects','napcat-preflight')) {
            $retired = Join-Path $MigrationRoot $name
            if (Test-Path -LiteralPath $retired -PathType Container) {
                $dir = Get-Item -LiteralPath $retired
                if ($dir.LastWriteTimeUtc -lt $week) {$candidateDirs.Add($dir)}
            }
        }
        $allowedParents = @($control,(Join-Path $DeploymentRoot 'staged'),(Join-Path $DeploymentRoot 'releases'),(Join-Path $DeploymentRoot 'web-stages'))
        $testRunName = '^(?:autonomous-(?:state|group|maintenance|coordinator)|execution-memory|shared-product|memory-native|autonomy-native|bilibili-maintenance)-[a-z0-9]{6}$'
        foreach ($testRoot in @('C:\code\fish\qq-ai-saas\.local','C:\code\fish\baseline-check\.local')) {
            if (-not (Test-Path -LiteralPath $testRoot -PathType Container) -or
                $references.IndexOf($testRoot,[StringComparison]::OrdinalIgnoreCase) -ge 0) {continue}
            $allowedParents += $testRoot
            foreach ($dir in Get-ChildItem -LiteralPath $testRoot -Directory) {
                if ($dir.Name -match $testRunName -and $dir.LastWriteTimeUtc -lt $oldRollback) {$candidateDirs.Add($dir)}
            }
        }
        $protected = @($sources | ForEach-Object {[IO.Path]::GetFullPath($_)})
        foreach ($entry in $candidateDirs) {
            if ($Apply -and $drive.AvailableFreeSpace -ge $FreeTargetGiB * 1GB) {break}
            $path = [IO.Path]::GetFullPath($entry.FullName)
            $owner = @($allowedParents | Where-Object {$path.StartsWith($_.TrimEnd('\') + '\',[StringComparison]::OrdinalIgnoreCase)} | Select-Object -First 1)
            if (-not $owner -and $path -in @((Join-Path $MigrationRoot 'precopy-runtime'),(Join-Path $MigrationRoot 'content-objects'),(Join-Path $MigrationRoot 'napcat-preflight'))) {
                $owner = @($MigrationRoot)
            }
            if (-not $owner -or $references.IndexOf($path,[StringComparison]::OrdinalIgnoreCase) -ge 0 -or
                @($protected | Where-Object {$_ -eq $path -or $_.StartsWith($path + '\',[StringComparison]::OrdinalIgnoreCase)}).Count) {continue}
            $current = $path
            $verified = $true
            while ($current -ne [IO.Path]::GetPathRoot($path)) {
                if (-not (Test-Path -LiteralPath $current -PathType Container) -or
                    ((Get-Item -LiteralPath $current).Attributes -band [IO.FileAttributes]::ReparsePoint)) {$verified=$false;break}
                $current = [IO.Path]::GetDirectoryName($current)
            }
            if (-not $verified -or (Get-Item -LiteralPath $path).LastWriteTimeUtc -ne $entry.LastWriteTimeUtc) {continue}
            $treeEligible++
            if ($Apply) {
                try {
                    # Directory.Delete removes junctions as links and never walks their targets.
                    [IO.Directory]::Delete($path,$true)
                    $treeRemoved++
                } catch {
                    $treeFailure += [pscustomobject]@{Name=$entry.Name;Parent=$entry.Parent.Name;Error=$_.Exception.GetType().Name}
                    $failed++
                }
            }
        }
    }
    $after = $drive.AvailableFreeSpace
    $result = [ordered]@{ At=[DateTime]::UtcNow.ToString('o'); Applied=[bool]$Apply; Eligible=$eligible;
        EligibleBytes=$bytes; Removed=$removed; TreeEligible=$treeEligible; TreeRemoved=$treeRemoved;
        TreeFailures=$treeFailure; Failed=$failed; FreeTargetGiB=$FreeTargetGiB; FreeBefore=$before; FreeAfter=$after;
        TargetShortfallBytes=[math]::Max(0L,($FreeTargetGiB * 1GB) - $after) }
    $result | ConvertTo-Json -Compress
    if ($Apply) {
        $status = Join-Path $DeploymentRoot 'ops\maintenance\cleanup-status.json'
        $partial = "$status.new"
        $result | ConvertTo-Json -Compress | Set-Content -LiteralPath $partial -Encoding ascii
        if (Test-Path -LiteralPath $status) {
            $previous = "$status.previous"
            [IO.File]::Replace($partial,$status,$previous)
            [IO.File]::Delete($previous)
        } else {[IO.File]::Move($partial,$status)}
    }
    if ($failed) { exit 1 }
} finally {
    $mutex.ReleaseMutex()
    $mutex.Dispose()
}
