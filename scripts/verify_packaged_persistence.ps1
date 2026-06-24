param(
    [Parameter(Mandatory = $true)][string]$Executable,
    [Parameter(Mandatory = $true)][string]$Python,
    [Parameter(Mandatory = $true)][string]$Label,
    [Parameter(Mandatory = $true)][string]$UserRoot,
    [Parameter(Mandatory = $true)][string]$LegacyRoot
)

$ErrorActionPreference = "Stop"
$env:TEACHER_HUB_HOME = $UserRoot
$env:TEACHER_HUB_LEGACY_ROOT = $LegacyRoot

function Start-And-CloseTeacherHub {
    $processName = [System.IO.Path]::GetFileNameWithoutExtension($Executable)
    $existingIds = @(
        Get-Process -Name $processName -ErrorAction SilentlyContinue |
            Select-Object -ExpandProperty Id
    )
    $launcherProcess = Start-Process -FilePath $Executable -PassThru
    $windowProcess = $null
    $handle = 0
    for ($attempt = 0; $attempt -lt 180; $attempt++) {
        Start-Sleep -Milliseconds 500
        $launcherProcess.Refresh()
        $candidates = @(
            Get-Process -Name $processName -ErrorAction SilentlyContinue |
                Where-Object { $existingIds -notcontains $_.Id }
        )
        $windowProcess = $candidates |
            Where-Object { $_.MainWindowHandle -ne 0 } |
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
        throw "$Label did not expose a main window"
    }

    $meiDatabases = @(
        Get-ChildItem -Path (Join-Path $env:TEMP "_MEI*") `
            -Filter teacher.db -File -Recurse -ErrorAction SilentlyContinue
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
        "all_processes_exited=True mei_databases=$($meiDatabases.Count)"
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
        -Include teacher.db,*.pdf,*.xlsx -File -Recurse -ErrorAction SilentlyContinue
)

Write-Output "$Label FIRST $first"
Write-Output "$Label SECOND $second"
Write-Output (
    "$Label PERSIST probe=$probe db=$database " +
    "hash_unchanged_on_restart=$($hashBeforeRestart -eq $hashAfterRestart)"
)
Write-Output "$Label EXE_DIR_USER_FILES=$($executableFiles.Count) USER_ROOT=$UserRoot"
