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
        $_.CommandLine -notmatch '--port\s+879[78]' -and
        $_.CommandLine -notmatch 'Get-CimInstance' -and
        $_.Name -match 'uvicorn|python'
    } | ForEach-Object {
        Write-Host "Stopping existing process (PID $($_.ProcessId)): $($_.CommandLine)"
        # -ErrorAction SilentlyContinue: the CIM snapshot above can list a
        # short-lived child process (e.g. a launcher's python.exe helper)
        # that has already exited on its own by the time we get here --
        # harmless, but Stop-Process would otherwise print a scary-looking
        # (and misleading) red error for it.
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }
}

Write-Host "== Stopping any existing Neural processes =="
Stop-NeuralProcess 'backend\.main:app'
Stop-NeuralProcess 'backend\.public_media_server:app'

Start-Sleep 2

# Neural's optional local Qwen utility sidecar speaks to Ollama on its
# loopback-only OpenAI-compatible endpoint. Ollama is intentionally not
# stopped above: it can serve other local apps too, and keeping it alive
# preserves the loaded model between Neural restarts. If it is not already
# running (after boot, for example), recover it here so Qwen utility calls
# do not silently fall back merely because its local runtime was forgotten.
function Test-OllamaServer {
    try {
        Invoke-WebRequest -Uri 'http://127.0.0.1:11434/api/tags' -TimeoutSec 2 -UseBasicParsing | Out-Null
        return $true
    } catch {
        return $false
    }
}

if (-not (Test-OllamaServer)) {
    $ollama = Get-Command ollama -ErrorAction SilentlyContinue
    if ($ollama) {
        Write-Host "== Starting local Ollama runtime for the Qwen utility sidecar =="
        Start-Process -WindowStyle Hidden -FilePath $ollama.Source -ArgumentList 'serve'
        for ($attempt = 0; $attempt -lt 5 -and -not (Test-OllamaServer); $attempt++) {
            Start-Sleep 1
        }
        if (-not (Test-OllamaServer)) {
            Write-Host "Ollama did not become ready; Neural will keep its Claude utility fallback available."
        }
    } else {
        Write-Host "Ollama is not installed; Neural will keep its Claude utility fallback available."
    }
}

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

        # --bg is required here: without it, `tailscale funnel` (and, on
        # some versions, `serve`) attaches to the foreground and blocks
        # until Ctrl-C instead of applying the config and returning, which
        # is exactly the hang the 20s timeout above was working around.
        #
        # --https=8443 on the funnel line is NOT optional/cosmetic: both
        # `serve` and `funnel` default to external port 443 on this same
        # hostname when no port is given, and Tailscale only lets ONE
        # mapping own port 443 per node -- confirmed live on this exact
        # setup (the funnel command below, run second, silently took over
        # port 443 from the serve line above it, so the main app became
        # unreachable at the plain https://<hostname> URL and mobile
        # access broke, even though nothing was ever publicly exposed).
        # Putting funnel on 8443 instead (one of Funnel's only 3 allowed
        # ports: 443/8443/10000) gives it its own slot so serve keeps 443
        # for the main app. PUBLIC_BASE_URL (.env) must include :8443 to
        # match -- see get_tailscale_url.ps1.
        Invoke-TailscaleQuiet -CmdArgs @('serve', '--bg', '8787')
        Invoke-TailscaleQuiet -CmdArgs @('funnel', '--bg', '--https=8443', '8788')
    } else {
        Write-Host "Tailscale not found on PATH -- skipping serve/funnel setup. Install it or pass -SkipTailscale to silence this."
    }
}

Start-Sleep 1
Write-Host "== Done. Backend: http://127.0.0.1:8787  Media server: http://127.0.0.1:8788 =="
Write-Host "Logs: neural_backend.log/.err and neural_media.log/.err in the project root."
