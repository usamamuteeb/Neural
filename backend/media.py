"""
Turns a local file (a screen recording, an existing video, a generated
image) into a public HTTPS URL that an external service can fetch --
specifically Buffer, whose API attaches media to a post by URL, not by
direct upload (see backend/config.py's PUBLIC_BASE_URL for the tunnel
requirement this implies).

Files are exposed under a random, unguessable token rather than by their
real name or path, and registrations expire after a day so a link doesn't
stay live on the public internet forever after you've actually posted it
-- the underlying file itself is untouched either way, only the public
URL stops resolving.

This is its own SDK-MCP tool (not folded into agent.py's Buffer wiring)
because it's useful independent of Buffer -- anything that needs to hand
another service a URL for a local file can reuse it, so it's always
registered like memory/notifications rather than gated on BUFFER_ENABLED.
"""

import json
import mimetypes
import secrets
import time
from pathlib import Path
from typing import Any, Optional

from claude_agent_sdk import create_sdk_mcp_server, tool

from . import config

MEDIA_DIR = Path(__file__).resolve().parent.parent / "media"
MEDIA_DIR.mkdir(exist_ok=True)
REGISTRY_PATH = MEDIA_DIR / "registry.json"

TTL_SECONDS = 24 * 60 * 60  # a day is plenty to draft, review, and publish a post


def _load() -> dict[str, dict[str, Any]]:
    if not REGISTRY_PATH.exists():
        return {}
    try:
        return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save(reg: dict[str, dict[str, Any]]) -> None:
    REGISTRY_PATH.write_text(json.dumps(reg, indent=2), encoding="utf-8")


def _prune(reg: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    now = time.time()
    return {k: v for k, v in reg.items() if v.get("expires_at", 0) > now}


def resolve(token: str) -> Optional[Path]:
    """Used by main.py's GET /media/{token} route."""
    reg = _prune(_load())
    entry = reg.get(token)
    if not entry:
        return None
    path = Path(entry["path"])
    return path if path.is_file() else None


def _text(s: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": s}]}


@tool(
    "local_file_to_public_url",
    "Turn a local image or video file into a public HTTPS URL that an external service "
    "(like Buffer) can fetch to attach it to a post. Requires PUBLIC_BASE_URL to be "
    "configured (a tunnel pointed at this server) -- if it isn't, this says so instead "
    "of returning a broken localhost link. The URL stays valid for 24 hours.",
    {"path": str},
)
async def local_file_to_public_url(args: dict) -> dict[str, Any]:
    if not config.PUBLIC_BASE_URL:
        return _text(
            "PUBLIC_BASE_URL isn't set, so there's no public URL to hand out. Set up a "
            "tunnel (Tailscale/Cloudflare Tunnel/ngrok -- see the README's Mobile access "
            "section) pointed at this server, put its HTTPS origin in PUBLIC_BASE_URL in "
            ".env, and restart the server."
        )
    path = Path(args["path"]).expanduser().resolve()
    if not path.is_file():
        return _text(f"No such file: {path}")

    reg = _prune(_load())
    token = secrets.token_urlsafe(16)
    reg[token] = {
        "path": str(path),
        "expires_at": time.time() + TTL_SECONDS,
        "mime": mimetypes.guess_type(str(path))[0] or "application/octet-stream",
    }
    _save(reg)
    return _text(f"{config.PUBLIC_BASE_URL}/media/{token}")


MEDIA_TOOLS = ["local_file_to_public_url"]

media_server = create_sdk_mcp_server(
    name="media",
    version="1.0.0",
    tools=[local_file_to_public_url],
)
