"""
A second, minimal, PUBLIC-facing server that exposes ONLY /media/{token}
-- nothing else. This is meant to be the target of `tailscale funnel`
(or Cloudflare Tunnel/ngrok) instead of funneling the main backend
(main.py, port 8787) itself.

Why this exists: main.py's /ws endpoint accepts any connection with ZERO
authentication (no API key, no token, nothing -- see ws_endpoint) and,
once connected, runs full agent turns under bypassPermissions: arbitrary
Bash, browser automation, Gmail/WhatsApp/Slack, Buffer posting -- every
tool Neural has. Tailscale Funnel provisions a real, publicly-trusted
TLS certificate the moment you turn it on, and that certificate is
logged in public Certificate Transparency logs -- so the funnel
hostname isn't meaningfully secret just because it looks like a random
subdomain; anyone scanning CT logs (a routine thing security scanners
do) can find it. Funneling port 8787 directly means anyone who finds
that hostname has unauthenticated remote control of this machine and
every account connected to it, no login screen in the way at all.

Funneling THIS server instead exposes only random-token file lookups
(secrets.token_urlsafe(16) -- 128 bits, not brute-forceable, see
media.py) that expire after 24h and were only ever meant to become
public anyway -- that's the whole point of local_file_to_public_url.
There's no meaningful new exposure from someone finding this specific
URL, unlike the main backend.

Run it as its own process, alongside the main server, on a different
port:

    uvicorn backend.public_media_server:app --port 8788

Then point `tailscale funnel 8788` (NOT 8787) at it, and put the URL it
gives you in PUBLIC_BASE_URL in .env. The main backend (8787) stays
private -- serve it with `tailscale serve` (tailnet-only, no public
exposure) if you also want phone access, per the README's Mobile access
section.

Deliberately reuses backend/media.py's resolve() (the same plain-file
registry, memory/-style philosophy as the rest of this project) rather
than duplicating any registration logic: registrations are written by
the main process's local_file_to_public_url tool and read here from the
same media/registry.json on disk -- the two processes never need to
talk to each other directly, so this file can be started, stopped, or
crash independently of the main backend without losing anything.
"""

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from . import media

app = FastAPI(title="Neural media server", docs_url=None, redoc_url=None)


@app.api_route("/media/{token}", methods=["GET", "HEAD"])
async def get_media(token: str):
    """Same handler as main.py's /media/{token} (see that file's HEAD-
    support and extension-stripping fixes) -- kept identical on purpose
    so PUBLIC_BASE_URL/media links behave the same whether they end up
    served from here or from the main backend directly (e.g. during
    local testing without a tunnel at all). The optional extension
    ("/media/<token>.mp4") lets services that sniff media type from the
    URL suffix (Buffer/YouTube) do so; real tokens never contain a "."
    (secrets.token_urlsafe's alphabet is A-Za-z0-9-_), so this is
    unambiguous."""
    path = media.resolve(token.rsplit(".", 1)[0] if "." in token else token)
    if not path:
        raise HTTPException(status_code=404, detail="not found or expired")
    return FileResponse(path)
