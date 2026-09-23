# Starts (or restarts) everything Neural needs: the main backend
# (backend.main:app, port 8787 -- chat, automations, /ws, full tool
# access) and the public media server (backend.public_media_server:app,
# port 8788 -- the only thing safe to expose publicly; see README's
# Mobile access section for why).
#
# Safe to run any time, for any reason:
#   - after a reboot (this is also what the auto-start task at logon runs --
#     see setup_autostart.ps1)
#   - after editing backend code, yours or Neural's own self-edits
#   - just to bounce a stuck process
#
# It always stops any already-running copies first (matched by command
# line, not just process name, so it won't touch an unrelated uvicorn
# elsewhere), so it's idempotent -- running it twice in a row is fine and
# won't produce a "port already in use" (WinError 10048) error.
#
# Run from the project root:
#   powershell -File start_neural.ps1
#
# Add -SkipTailscale to skip the tailscale serve/funnel step below (e.g.
# if Tailscale isn't installed, or you're only testing locally).

param(
    [switch]$SkipTailscale
)

$ErrorActionPreference = 'Continue'
Set-Location $PSScriptRoot

function Stop-NeuralProcess($pattern) {
    Get-CimInstance Win32_Process | Where-Object {
        $_.CommandLine -match 'uvicorn' -and
        $_.CommandLine -match $pattern -and
        $_.CommandLine -notmatch 'Get-CimInstance' -and
        $_.Name -match 'uvicorn|python'
    } | ForEach-Object {
        Write-Host "Stopping existing process (PID $($_.ProcessId)): $($_.CommandLine)"
        Stop-Process -Id $_.ProcessId -Force
    }
}

Write-Host "== Stopping any existing Neural processes =="
Stop-NeuralProcess 'backend\.main:app'
Stop-NeuralProcess 'backend\.public_media_server:app'

Start-Sleep 2

Write-Host "== Starting main backend on port 8787 =="
Start-Process -WindowStyle Hidden -FilePath "$PSScriptRoot\backend\.venv\Scripts\uvicorn.exe" `
    -ArgumentList 'backend.main:app', '--port', '8787' `
    -RedirectStandardOutput "$PSScriptRoot\neural_backend.log" `
    -RedirectStandardError "$PSScriptRoot\neural_backend.err"

Write-Host "== Starting public media server on port 8788 =="
Start-Process -WindowStyle Hidden -FilePath "$PSScriptRoot\backend\.venv\Scripts\uvicorn.exe" `
    -ArgumentList 'backend.public_media_server:app', '--port', '8788' `
    -RedirectStandardOutput "$PSScriptRoot\neural_media.log" `
    -RedirectStandardError "$PSScriptRoot\neural_media.err"

if (-not $SkipTailscale) {
    $tailscale = Get-Command tailscale -ErrorAction SilentlyContinue
    if ($tailscale) {
        Write-Host "== Re-asserting Tailscale serve/funnel (safe to repeat; no-op if already set) =="

        function Invoke-TailscaleQuiet {
            param([string[]]$CmdArgs)
            try {
                $p = Start-Process -WindowStyle Hidden -FilePath $tailscale.Source -ArgumentList $CmdArgs -PassThru
                $p | Wait-Process -Timeout 20 -ErrorAction SilentlyContinue
                if (-not $p.HasExited) {
                    Write-Host "tailscale $CmdArgs did not finish in time (may need a one-time interactive approval in a real terminal) -- skipping. The backend and media server are already up regardless."
                    Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
                }
            } catch {
                Write-Host "tailscale $CmdArgs failed: $_"
            }
        }

        Invoke-TailscaleQuiet -CmdArgs @('serve', '8787')
        Invoke-TailscaleQuiet -CmdArgs @('funnel', '8788')
    } else {
        Write-Host "Tailscale not found on PATH -- skipping serve/funnel setup. Install it or pass -SkipTailscale to silence this."
    }
}

Start-Sleep 1
Write-Host "== Done. Backend: http://127.0.0.1:8787  Media server: http://127.0.0.1:8788 =="
Write-Host "Logs: neural_backend.log/.err and neural_media.log/.err in the project root."
