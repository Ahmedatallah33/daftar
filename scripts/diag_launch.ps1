$lnk = "C:\Users\moaz-\OneDrive\Desktop\Teacher Hub.lnk"
Write-Host "Attempting Invoke-Item..."
try {
    Invoke-Item $lnk
    Write-Host "Invoke-Item succeeded"
} catch {
    Write-Host "Invoke-Item failed: $_"
}
Start-Sleep -Seconds 3
Get-Process pythonw -ErrorAction SilentlyContinue | Format-Table Id, ProcessName, MainWindowTitle
