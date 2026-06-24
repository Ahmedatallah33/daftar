param(
    [string]$ShortcutName = "Teacher Hub"
)

$ErrorActionPreference = "Stop"

# ----- Resolve source paths (may contain Unicode) -----
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$SourceIcon  = Join-Path $ProjectRoot "app\resources\icons\app.ico"

# ----- Per-user launcher directory; never hard-code a developer account. -----
$DeployDir = Join-Path $env:LOCALAPPDATA "TeacherHub\launcher"
New-Item -ItemType Directory -Force -Path $DeployDir | Out-Null

# ----- Create a directory JUNCTION with an ASCII name that points into the
#       actual (possibly Unicode-named) project directory. This lets us build
#       a VBS launcher whose strings are pure ASCII, avoiding all encoding
#       issues when cmd/wscript reads it back. -----
$Junction = Join-Path $DeployDir "src"
if (Test-Path -LiteralPath $Junction) {
    # Remove existing junction safely (don't recurse into target!)
    cmd /c rmdir "`"$Junction`"" | Out-Null
}
cmd /c mklink /J "`"$Junction`"" "`"$ProjectRoot`"" | Out-Null
if (-not (Test-Path -LiteralPath (Join-Path $Junction "main.py"))) {
    Write-Error "Failed to create junction to project: $Junction -> $ProjectRoot"
    exit 1
}

# ----- Copy the icon into the ASCII deploy dir -----
$IconPath = Join-Path $DeployDir "app.ico"
Copy-Item -LiteralPath $SourceIcon -Destination $IconPath -Force

# ----- Locate pythonw.exe -----
$PythonW = $null
$PyLauncher = "$env:WINDIR\py.exe"
if (Test-Path $PyLauncher) {
    $pyExe = (& $PyLauncher -3 -c "import sys; print(sys.executable)").Trim()
    if (-not $pyExe) {
        $pyExe = (& $PyLauncher -c "import sys; print(sys.executable)").Trim()
    }
    if ($pyExe) {
        $candidate = Join-Path (Split-Path -Parent $pyExe) "pythonw.exe"
        if (Test-Path $candidate) { $PythonW = $candidate }
    }
}
if (-not $PythonW) {
    $command = Get-Command pythonw.exe -ErrorAction SilentlyContinue
    if ($command) { $PythonW = $command.Source }
}
if (-not $PythonW) { Write-Error "Could not locate pythonw.exe"; exit 1 }

# ----- VBS launcher (ASCII only, via junction). We avoid setting
#       CurrentDirectory because WScript.Shell.CurrentDirectory rejects
#       junction paths (error 0x8007007B). Instead pass the absolute path
#       to main.py via the junction — Python resolves paths relative to
#       __file__, not cwd, so the app starts correctly either way.
$MainPyViaJunction = Join-Path $Junction "main.py"
$VbsPath = Join-Path $DeployDir "launch.vbs"
$vbs = @"
Set shell = CreateObject("WScript.Shell")
shell.Run """$PythonW"" ""$MainPyViaJunction""", 0, False
"@
[System.IO.File]::WriteAllText($VbsPath, $vbs, [System.Text.UTF8Encoding]::new($false))

# ----- Create shortcuts on BOTH possible desktops -----
$desktops = @()
$default = [Environment]::GetFolderPath("Desktop")
if ($default) { $desktops += $default }
$oneDrive = Join-Path $env:USERPROFILE "OneDrive\Desktop"
if ((Test-Path $oneDrive) -and ($desktops -notcontains $oneDrive)) {
    $desktops += $oneDrive
}

$shell = New-Object -ComObject WScript.Shell
foreach ($d in $desktops) {
    $LnkPath = Join-Path $d "$ShortcutName.lnk"
    if (Test-Path -LiteralPath $LnkPath) { Remove-Item -LiteralPath $LnkPath -Force }
    $sc = $shell.CreateShortcut($LnkPath)
    $sc.TargetPath       = "$env:WINDIR\System32\wscript.exe"
    $sc.Arguments        = "`"$VbsPath`""
    $sc.WorkingDirectory = $DeployDir
    $sc.IconLocation     = "$IconPath,0"
    $sc.WindowStyle      = 1
    $sc.Description      = "Teacher Hub - Online teaching manager"
    $sc.Save()
    Write-Host "Shortcut: $LnkPath"
}

Write-Host ""
Write-Host "Deploy dir:  $DeployDir"
Write-Host "Icon:        $IconPath"
Write-Host "Launcher:    $VbsPath"
Write-Host "Junction:    $Junction"
Write-Host "Target exe:  $PythonW"
