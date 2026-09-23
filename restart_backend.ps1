# Restarts Neural's backend after its own source has been edited -- by
# Neural itself mid-conversation (it has Bash/PowerShell access and will
# run this after patching its own code), or by you manually. Finds the
# currently-running `uvicorn backend.main:app` process, stops it, and
# starts a fresh one in the background with console output redirected to
# backend_restart.log/.err (both gitignored) instead of a visible window.
#
# Run from the project root:
#   powershell -File restart_backend.ps1
#
# Safe to run even if no old process is running -- the Where-Object
# filter just matches nothing and the Stop-Process step is skipped.

Get-CimInstance Win32_Process | Where-Object {
    $_.CommandLine -match 'uvicorn' -and
    $_.CommandLine -match 'backend.main:app' -and
    $_.CommandLine -notmatch 'Get-CimInstance' -and
    $_.Name -match 'uvicorn|python'
} | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }

Start-Sleep 2

Set-Location $PSScriptRoot
Start-Process -WindowStyle Hidden -FilePath "$PSScriptRoot\backend\.venv\Scripts\uvicorn.exe" `
    -ArgumentList 'backend.main:app', '--port', '8787' `
    -RedirectStandardOutput "$PSScriptRoot\backend_restart.log" `
    -RedirectStandardError "$PSScriptRoot\backend_restart.err"
