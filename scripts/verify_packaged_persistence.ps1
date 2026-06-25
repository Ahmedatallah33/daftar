[CmdletBinding(DefaultParameterSetName = "Verify")]
param(
    [Parameter(Mandatory = $true, ParameterSetName = "Verify")][string]$Executable,
    [Parameter(Mandatory = $true, ParameterSetName = "Verify")][string]$Python,
    [Parameter(Mandatory = $true, ParameterSetName = "Verify")][string]$Label,
    [Parameter(Mandatory = $true, ParameterSetName = "Verify")][string]$UserRoot,
    [Parameter(Mandatory = $true, ParameterSetName = "Verify")][string]$LegacyRoot,
    [Parameter(ParameterSetName = "Verify")][string]$ExpectedMainWindowTitle = "",
    [Parameter(Mandatory = $true, ParameterSetName = "ExitProbe")]
    [ValidateSet("Success", "Failure")]
    [string]$ExitSemanticsProbe
)

$ErrorActionPreference = "Stop"
trap {
    Write-Error $_
    exit 1
}

if ($PSCmdlet.ParameterSetName -eq "ExitProbe") {
    if ($ExitSemanticsProbe -eq "Success") {
        Write-Output "PACKAGED_VERIFIER_EXIT_PROBE success"
        exit 0
    }
    throw "PACKAGED_VERIFIER_EXIT_PROBE controlled failure"
}

$env:TEACHER_HUB_HOME = $UserRoot
$env:TEACHER_HUB_LEGACY_ROOT = $LegacyRoot
if (-not $ExpectedMainWindowTitle) {
    $ExpectedMainWindowTitle = [Text.Encoding]::UTF8.GetString(
        [Convert]::FromBase64String(
            "VGVhY2hlciBIdWIg4oCUINil2K/Yp9ix2Kkg2KfZhNit2LXYtQ=="
        )
    )
}
$StartupRecoveryTitle = [Text.Encoding]::UTF8.GetString(
    [Convert]::FromBase64String("2KrYudiw2LEg2KrYtNi62YrZhCBUZWFjaGVyIEh1Yg==")
)

function Start-And-CloseTeacherHub {
    $processName = [System.IO.Path]::GetFileNameWithoutExtension($Executable)
    $existingIds = @(
        Get-Process -Name $processName -ErrorAction SilentlyContinue |
            Select-Object -ExpandProperty Id
    )
    $launcherProcess = Start-Process -FilePath $Executable -PassThru
    $windowProcess = $null
    $handle = 0
    $unexpectedWindowTitles = @()
    for ($attempt = 0; $attempt -lt 180; $attempt++) {
        Start-Sleep -Milliseconds 500
        $launcherProcess.Refresh()
        $candidates = @(
            Get-Process -Name $processName -ErrorAction SilentlyContinue |
                Where-Object { $existingIds -notcontains $_.Id }
        )
        $visibleWindows = @(
            $candidates |
                Where-Object { $_.MainWindowHandle -ne 0 }
        )
        $unexpectedWindowTitles = @(
            $visibleWindows |
                Where-Object { $_.MainWindowTitle -ne $ExpectedMainWindowTitle } |
                Select-Object -ExpandProperty MainWindowTitle -Unique
        )
        $recoveryWindow = $visibleWindows |
            Where-Object { $_.MainWindowTitle -eq $StartupRecoveryTitle } |
            Select-Object -First 1
        if ($recoveryWindow) {
            $candidates | Stop-Process -Force -ErrorAction SilentlyContinue
            throw "$Label opened the startup recovery dialog instead of the main window"
        }
        $windowProcess = $visibleWindows |
            Where-Object { $_.MainWindowTitle -eq $ExpectedMainWindowTitle } |
            Select-Object -First 1
        if ($windowProcess) {
            $handle = $windowProcess.MainWindowHandle
            break
        }
        # In one-file mode the bootloader may exit or hand off before the
        # extracted child process becomes visible. Keep polling for the full
        # startup window instead of treating that brief gap as failure.
    }
    if ($handle -eq 0) {
        Get-Process -Name $processName -ErrorAction SilentlyContinue |
            Stop-Process -Force
        $diagnostic = if ($unexpectedWindowTitles.Count -gt 0) {
            $unexpectedWindowTitles -join ", "
        } else {
            "none"
        }
        throw (
            "$Label did not expose the expected main window " +
            "'$ExpectedMainWindowTitle'; other visible titles: $diagnostic"
        )
    }

    $meiUserFiles = @(
        Get-ChildItem -Path (Join-Path $env:TEMP "_MEI*") `
            -File -Recurse -ErrorAction SilentlyContinue |
            Where-Object {
                $_.Name -eq "teacher.db" -or
                $_.Extension -in ".db", ".pdf", ".xlsx", ".log" -or
                $_.Name -like "*.teacherhub.zip"
            }
    )
    $closeRequested = $windowProcess.CloseMainWindow()
    $remaining = @()
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        Start-Sleep -Milliseconds 500
        $remaining = @(
            Get-Process -Name $processName -ErrorAction SilentlyContinue |
                Where-Object { $existingIds -notcontains $_.Id }
        )
        if ($remaining.Count -eq 0) { break }
    }
    if ($remaining.Count -ne 0) {
        $remaining | Stop-Process -Force
        throw "$Label did not close normally"
    }
    return (
        "pid=$($windowProcess.Id) handle=$handle close_requested=$closeRequested " +
        "all_processes_exited=True mei_user_files=$($meiUserFiles.Count)"
    )
}

$first = Start-And-CloseTeacherHub
$database = Join-Path $UserRoot "data\teacher.db"
if (-not (Test-Path -LiteralPath $database)) {
    throw "$Label did not create the stable database"
}

$env:PROBE_DB = $database
$insertProbe = @"
import os, sqlite3
c = sqlite3.connect(os.environ['PROBE_DB'])
c.execute('INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)', ('packaging_probe', 'survives'))
c.commit()
c.close()
"@
& $Python -c $insertProbe
if ($LASTEXITCODE -ne 0) { throw "$Label probe insert failed" }
$hashBeforeRestart = (
    Get-FileHash $database -Algorithm SHA256 -ErrorAction Stop
).Hash

$second = Start-And-CloseTeacherHub
$readProbe = @"
import os, sqlite3
c = sqlite3.connect(os.environ['PROBE_DB'])
print(c.execute('SELECT value FROM settings WHERE key=?', ('packaging_probe',)).fetchone()[0])
c.close()
"@
$probe = & $Python -c $readProbe
if ($LASTEXITCODE -ne 0) { throw "$Label probe read failed" }
$hashAfterRestart = (
    Get-FileHash $database -Algorithm SHA256 -ErrorAction Stop
).Hash

$executableFiles = @(
    Get-ChildItem -Path (Split-Path -Parent $Executable) `
        -File -Recurse -ErrorAction SilentlyContinue |
        Where-Object {
            $_.Name -eq "teacher.db" -or
            $_.Extension -in ".db", ".pdf", ".xlsx", ".log" -or
            $_.Name -like "*.teacherhub.zip"
        }
)

Write-Output "$Label FIRST $first"
Write-Output "$Label SECOND $second"
Write-Output (
    "$Label PERSIST probe=$probe db=$database " +
    "hash_unchanged_on_restart=$($hashBeforeRestart -eq $hashAfterRestart)"
)
Write-Output "$Label EXE_DIR_USER_FILES=$($executableFiles.Count) USER_ROOT=$UserRoot"
exit 0
