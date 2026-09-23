# DEPRECATED -- superseded by start_neural.ps1, which does everything this
# script used to do (restart the main backend) plus the public media
# server and Tailscale re-assertion, in one place. Kept only so older
# notes/scripts that still reference "restart_backend.ps1" by name keep
# working -- it just forwards to start_neural.ps1.
#
# Use start_neural.ps1 directly for anything new.

Write-Host "restart_backend.ps1 is deprecated -- forwarding to start_neural.ps1..."
& (Join-Path $PSScriptRoot "start_neural.ps1") @args
