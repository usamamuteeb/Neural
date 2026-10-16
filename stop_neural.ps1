# Stops Neural's main backend (port 8787) and public media server (port
# 8788) without starting them back up. Safe to run even if neither is
# running -- the filters just match nothing.
#
# Note: this only kills the two processes for now -- if the "NeuralAutoStart"
# scheduled task is set up (see setup_autostart.ps1), it will NOT bring them
# back until your next Windows login, so this is a real, if temporary, stop.
# To stop them from ever auto-starting again, see the bottom of this file.
#
# Run from the project root:
#   powershell -File stop_neural.ps1

function Stop-NeuralProcess($pattern) {
    Get-CimInstance Win32_Process | Where-Object {
        $_.CommandLine -match 'uvicorn' -and
        $_.CommandLine -match $pattern -and
        $_.CommandLine -notmatch '--port\s+879[78]' -and
        $_.CommandLine -notmatch 'Get-CimInstance' -and
        $_.Name -match 'uvicorn|python'
    } | ForEach-Object {
        Write-Host "Stopping (PID $($_.ProcessId)): $($_.CommandLine)"
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }
}

Stop-NeuralProcess 'backend\.main:app'
Stop-NeuralProcess 'backend\.public_media_server:app'

Write-Host "== Stopped =="
Write-Host "Restart any time with:  powershell -File start_neural.ps1"
Write-Host ""
Write-Host "To also stop it auto-starting at your next login:"
Write-Host "  Disable-ScheduledTask -TaskName 'NeuralAutoStart'   # pause it, keep the task"
Write-Host "  Unregister-ScheduledTask -TaskName 'NeuralAutoStart'   # remove it for good"
