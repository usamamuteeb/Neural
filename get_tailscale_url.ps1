# Prints Neural's actual Tailscale HTTPS URL -- the value that belongs in
# PUBLIC_BASE_URL (.env), and the same URL you open on your phone for
# mobile access (see README's Mobile access section).
#
# `tailscale serve`/`tailscale funnel` don't write their URL anywhere
# Neural (or you) can read back later -- it's only ever printed once, in
# whatever terminal you ran the command in. This derives the SAME URL
# instead from `tailscale status --json`'s Self.DNSName, so you don't
# have to go hunting through old terminal scrollback for it again.
#
# Run from the project root:
#   powershell -File get_tailscale_url.ps1
#
# Add -UpdateEnv to also write the result into .env's PUBLIC_BASE_URL
# line (replacing it if present, appending it if missing). Without that
# switch this only prints -- it never touches .env on its own.

param(
    [switch]$UpdateEnv
)

Set-Location $PSScriptRoot

$tailscale = Get-Command tailscale -ErrorAction SilentlyContinue
if (-not $tailscale) {
    Write-Error "tailscale isn't on PATH. Install it first: winget install -e --id Tailscale.Tailscale"
    exit 1
}

$statusJson = & tailscale status --json 2>$null
if (-not $statusJson) {
    Write-Error "'tailscale status --json' returned nothing -- is Tailscale running and signed in? Try 'tailscale status' on its own first."
    exit 1
}

try {
    $status = $statusJson | ConvertFrom-Json
} catch {
    Write-Error "Couldn't parse 'tailscale status --json' output: $_"
    exit 1
}

$dnsName = $status.Self.DNSName
if (-not $dnsName) {
    Write-Error "Couldn't find this device's DNSName in Tailscale's status output -- is it signed into a tailnet?"
    exit 1
}

# Tailscale's DNSName is FQDN-style with a trailing dot (DNS convention) --
# strip it for a normal URL.
$hostname = $dnsName.TrimEnd('.')

# Main app (mobile access) and the public media server MUST use different
# external ports -- see the comment above the funnel line in
# start_neural.ps1. `serve` defaults to port 443 (bare hostname, no port
# in the URL); `funnel` is explicitly pinned to 8443 (one of Funnel's
# only 3 allowed ports: 443/8443/10000) so it can't silently steal 443
# away from serve, which is exactly what used to break mobile access.
$mainAppUrl = "https://$hostname"
$mediaUrl = "https://${hostname}:8443"

Write-Host "Tailscale hostname for this machine: $hostname"
Write-Host ""
Write-Host "Mobile access to Neural itself (needs 'tailscale serve 8787' running, tailnet-only):"
Write-Host "  $mainAppUrl"
Write-Host ""
Write-Host "PUBLIC_BASE_URL for Buffer media (needs 'tailscale funnel --https=8443 8788' running, publicly reachable):"
Write-Host "  $mediaUrl/"

if ($UpdateEnv) {
    $envPath = Join-Path $PSScriptRoot ".env"
    if (-not (Test-Path $envPath)) {
        Write-Error ".env not found at $envPath -- nothing updated. Copy .env.example to .env first."
        exit 1
    }
    $envLines = Get-Content $envPath
    $newLine = "PUBLIC_BASE_URL=$mediaUrl/"
    if ($envLines -match '^PUBLIC_BASE_URL=') {
        $envLines = $envLines -replace '^PUBLIC_BASE_URL=.*$', $newLine
    } else {
        $envLines += $newLine
    }
    Set-Content -Path $envPath -Value $envLines
    Write-Host ""
    Write-Host "Updated PUBLIC_BASE_URL in .env -- restart Neural (start_neural.ps1) for it to take effect."
}
