# ONE-TIME SETUP. Run this once, yourself, in a real PowerShell window on
# this machine (Claude/Neural can't run this step -- see the note at the
# bottom) to make start_neural.ps1 run automatically every time you log in
# to Windows, including after a reboot. This is what answers "if the
# laptop restarts, these things should auto restart."
#
# Why a logon trigger and not a Windows Service: WhatsApp/Slack/browser
# automation (Playwright) needs a real, interactive, signed-in desktop
# session. A Service runs in an isolated SYSTEM session with no desktop,
# so browser automation would silently break. A logon-triggered task runs
# in your actual desktop session, so everything keeps working exactly the
# way it does when you start it by hand.
#
# Safe to re-run any time (e.g. if you move the project folder) -- it
# replaces the existing task definition instead of erroring on a
# duplicate name.
#
# Run from the project root, in a normal (non-admin is fine) PowerShell
# window:
#   powershell -File setup_autostart.ps1

$taskName = "NeuralAutoStart"
$scriptPath = Join-Path $PSScriptRoot "start_neural.ps1"

$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$scriptPath`""

$trigger = New-ScheduledTaskTrigger -AtLogOn

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 0)

if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
    Write-Host "Existing '$taskName' task found -- replacing it."
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
}

Register-ScheduledTask -TaskName $taskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -User "$env:USERDOMAIN\$env:USERNAME" `
    -Description "Starts Neural's backend (8787) and public media server (8788) at login, so everything comes back automatically after a restart." `
    -Force | Out-Null

Write-Host "== Done =="
Write-Host "'$taskName' will now run start_neural.ps1 every time you log in to Windows."
Write-Host ""
Write-Host "Test it right now without rebooting:"
Write-Host "  Start-ScheduledTask -TaskName '$taskName'"
Write-Host ""
Write-Host "Check its last run result:"
Write-Host "  Get-ScheduledTaskInfo -TaskName '$taskName'"
Write-Host ""
Write-Host "To undo later:"
Write-Host "  Unregister-ScheduledTask -TaskName '$taskName'"
Write-Host ""
Write-Host "Note: this script only registers the task -- it does not run"
Write-Host "start_neural.ps1 itself. Run that once now if you want everything"
Write-Host "up immediately:  powershell -File start_neural.ps1"
