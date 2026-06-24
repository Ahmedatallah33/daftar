$lnk = Join-Path ([Environment]::GetFolderPath("Desktop")) "Teacher Hub.lnk"
$sh = New-Object -ComObject WScript.Shell
$sc = $sh.CreateShortcut($lnk)
Write-Host "LnkPath:      $lnk"
Write-Host "Exists:       $(Test-Path $lnk)"
Write-Host "TargetPath:   $($sc.TargetPath)"
Write-Host "Arguments:    $($sc.Arguments)"
Write-Host "WorkingDir:   $($sc.WorkingDirectory)"
Write-Host "IconLocation: $($sc.IconLocation)"

# Strip quotes from arguments to test script existence
$scriptPath = $sc.Arguments.Trim('"')
Write-Host "Script exists: $(Test-Path $scriptPath)"
Write-Host "Icon exists:   $(Test-Path ($sc.IconLocation -split ',')[0])"
Write-Host "Target exists: $(Test-Path $sc.TargetPath)"
