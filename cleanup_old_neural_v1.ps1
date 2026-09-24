# One-time cleanup: fully stop and remove the auto-start setup left behind
# by the FIRST version of Neural (the free-tier/multi-provider project at
# C:\Users\HP\Documents\personal-ai-assistant), which was abandoned in
# favor of this project (Documents\Neural, the Claude Agent SDK build).
#
# That old project installed three Windows Scheduled Tasks (see its own
# scripts\install_neural_startup.ps1 and HOW_TO_RUN_.md):
#   - "Neural Ollama Service"  -- kept the local Ollama fallback model running
#   - "Neural Web UI"          -- its web app / phone interface
#   - "Neural Scheduler"       -- its own reminders/automations scheduler
# plus Ollama itself, which its docs explicitly note is "left running
# because Windows manages it separately" even when the other two tasks are
# stopped -- so Ollama needs its own explicit stop/removal pass here, on
# top of removing that scheduled task.
#
# This script stops and unregisters all of the above, kills any of those
# processes still running right now, and sweeps the Startup folders /
# registry Run keys for any stray Ollama entry not covered by the task
# names above (in case Ollama's own installer added one independently).
#
# It deliberately does NOT touch, anywhere in this script:
#   - Tailscale (service or process) -- required by the CURRENT Neural
#   - "NeuralAutoStart" -- the CURRENT Neural's own logon task
#   - anything under Documents\Neural (the current project)
#
# Run from anywhere, in a normal (non-admin is fine) PowerShell window:
#   powershell -File cleanup_old_neural_v1.ps1

$ErrorActionPreference = "Continue"

Write-Host "== Old Neural v1 cleanup =="
Write-Host "(Tailscale and the current Neural's own 'NeuralAutoStart' task are never touched by this script.)"
Write-Host ""

# --- 1. Stop and remove the three old Task Scheduler tasks -----------------
$oldTasks = @("Neural Ollama Service", "Neural Web UI", "Neural Scheduler")

foreach ($taskName in $oldTasks) {
    $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if (-not $task) {
        Write-Host "Task '$taskName' -- not found (already gone)."
        continue
    }
    if ($task.State -eq "Running") {
        Write-Host "Task '$taskName' is running -- stopping it."
        Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    }
    Write-Host "Removing scheduled task '$taskName'."
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
}

Write-Host ""

# --- 2. Kill any old-project processes still running right now -------------
# Matched by command line containing the OLD project's folder name, so this
# can never match anything in the current Documents\Neural project.
Get-CimInstance Win32_Process | Where-Object {
    $_.CommandLine -match "personal-ai-assistant" -and
    $_.CommandLine -notmatch "Get-CimInstance"
} | ForEach-Object {
    Write-Host "Stopping old Neural v1 process (PID $($_.ProcessId)): $($_.CommandLine)"
    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
}

Write-Host ""

# --- 3. Stop Ollama itself (its task above only stopped ITS scheduled task,
#        not necessarily a copy already running from a previous logon) -----
Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.ProcessName -match "(?i)ollama" } | ForEach-Object {
    Write-Host "Stopping Ollama process (PID $($_.Id)): $($_.ProcessName)"
    Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
}

# Also check for Ollama registered as a genuine Windows Service (uncommon,
# but some setups do this via NSSM or similar).
$ollamaService = Get-Service -Name "*ollama*" -ErrorAction SilentlyContinue
if ($ollamaService) {
    foreach ($svc in $ollamaService) {
        Write-Host "Stopping and disabling Windows service '$($svc.Name)'."
        Stop-Service -Name $svc.Name -Force -ErrorAction SilentlyContinue
        Set-Service -Name $svc.Name -StartupType Disabled -ErrorAction SilentlyContinue
    }
} else {
    Write-Host "No Ollama Windows Service found (expected -- Ollama on Windows normally isn't one)."
}

Write-Host ""

# --- 4. Sweep Startup folders for a stray Ollama shortcut -------------------
# Ollama's own installer sometimes adds a Startup-folder shortcut for its
# tray app independent of any scheduled task. Never touches anything
# mentioning "neural" or "tailscale" even if matched here, as a second
# safety net beyond the exact task names already handled above.
$startupFolders = @(
    [Environment]::GetFolderPath("Startup"),
    [Environment]::GetFolderPath("CommonStartup")
)
foreach ($folder in $startupFolders) {
    if (-not (Test-Path $folder)) { continue }
    Get-ChildItem -Path $folder -Filter "*.lnk" -ErrorAction SilentlyContinue | Where-Object {
        $_.Name -match "(?i)ollama" -and $_.Name -notmatch "(?i)neural|tailscale"
    } | ForEach-Object {
        Write-Host "Removing Startup shortcut: $($_.FullName)"
        Remove-Item -Path $_.FullName -Force -ErrorAction SilentlyContinue
    }
}

# --- 5. Sweep HKCU/HKLM Run keys for a stray Ollama auto-start entry --------
$runKeyPaths = @(
    "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run",
    "HKLM:\Software\Microsoft\Windows\CurrentVersion\Run",
    "HKLM:\Software\Wow6432Node\Microsoft\Windows\CurrentVersion\Run"
)
foreach ($keyPath in $runKeyPaths) {
    if (-not (Test-Path $keyPath)) { continue }
    $props = Get-ItemProperty -Path $keyPath -ErrorAction SilentlyContinue
    if (-not $props) { continue }
    $props.PSObject.Properties |
        Where-Object { $_.Name -notmatch "^PS" } |
        Where-Object { $_.Value -match "(?i)ollama" -and $_.Value -notmatch "(?i)neural|tailscale" } |
        ForEach-Object {
            Write-Host "Removing Run key entry '$($_.Name)' from $keyPath -- $($_.Value)"
            Remove-ItemProperty -Path $keyPath -Name $_.Name -ErrorAction SilentlyContinue
        }
}

Write-Host ""
Write-Host "== Verifying what's left untouched (should show these still fine) =="
Get-Service -Name Tailscale -ErrorAction SilentlyContinue | Select-Object Name, Status, StartType | Format-Table -AutoSize
Get-ScheduledTask -TaskName "NeuralAutoStart" -ErrorAction SilentlyContinue | Select-Object TaskName, State | Format-Table -AutoSize

Write-Host ""
Write-Host "== Done. Old Neural v1 (Ollama, its web UI, its scheduler) is stopped and off auto-start. =="
Write-Host "Nothing under Documents\Neural, Tailscale, or NeuralAutoStart was touched."
