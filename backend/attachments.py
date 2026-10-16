"""
Images the user shows Neural from the chat UI -- a picked/pasted/dropped
image, a single frame of their screen ("Look at my screen"), or a camera
photo. See frontend/app.js's "Vision" section for the capture side.

The frontend downscales and re-encodes each image as JPEG before sending
it over the /ws websocket as base64. This module validates and saves it
under media/uploads/ (already gitignored via media/), and builds the
short note prepended to that turn's prompt telling Claude to open each
file with its own Read tool -- the same way Claude Code itself views
images. So "vision" here needs no API key, no separate vision model and
no extra cost beyond the normal Claude subscription turn.

Hardening: only real image bytes are accepted (MIME allowlist + a
magic-byte check, so a renamed file of anything else is dropped), there's
a per-image size cap and a per-message count cap, filenames are always
generated here (nothing user-supplied ever touches the path), and
uploads older than KEEP_DAYS are pruned on every save so the folder
can't grow forever.
"""

import base64
import binascii
import logging
import time
import uuid
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("neural.attachments")

UPLOAD_DIR = Path(__file__).resolve().parent.parent / "media" / "uploads"
MAX_BYTES = 10 * 1024 * 1024
MAX_COUNT = 4
KEEP_DAYS = 7

_EXT = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif"}
_KIND_DESCRIPTION = {
    "screen": "a screenshot of their screen, taken just now",
    "camera": "a photo from their camera, taken just now",
    "file": "an image they picked or pasted",
}


def _looks_like_image(raw: bytes, mime: str) -> bool:
    if mime == "image/jpeg":
        return raw[:3] == b"\xff\xd8\xff"
    if mime == "image/png":
        return raw[:8] == b"\x89PNG\r\n\x1a\n"
    if mime == "image/gif":
        return raw[:6] in (b"GIF87a", b"GIF89a")
    if mime == "image/webp":
        return raw[:4] == b"RIFF" and raw[8:12] == b"WEBP"
    return False


def prune_old(now: "float | None" = None) -> int:
    if not UPLOAD_DIR.exists():
        return 0
    cutoff = (now or time.time()) - KEEP_DAYS * 86400
    removed = 0
    for f in UPLOAD_DIR.iterdir():
        try:
            if f.is_file() and f.stat().st_mtime < cutoff:
                f.unlink()
                removed += 1
        except OSError:
            continue
    return removed


def save_attachments(items) -> list:
    """Validates and saves up to MAX_COUNT images from a /ws message's
    "attachments" list. Returns [{"path": absolute path str, "kind":
    "screen"|"camera"|"file"}] for each one actually saved; anything
    invalid is skipped (and logged), never raised."""
    if not isinstance(items, list):
        return []
    try:
        prune_old()
    except Exception as e:  # noqa: BLE001 -- pruning is housekeeping, never fatal
        logger.error("Couldn't prune old uploads: %s", e)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

    saved = []
    for item in items[:MAX_COUNT]:
        if not isinstance(item, dict):
            continue
        mime = str(item.get("mime", "")).lower().strip()
        ext = _EXT.get(mime)
        data = item.get("data")
        if not ext or not isinstance(data, str) or len(data) > (MAX_BYTES * 4) // 3 + 16:
            logger.warning("Dropped an attachment: unsupported type or too large (%s).", mime or "?")
            continue
        try:
            raw = base64.b64decode(data, validate=True)
        except (binascii.Error, ValueError):
            logger.warning("Dropped an attachment: not valid base64.")
            continue
        if not raw or len(raw) > MAX_BYTES or not _looks_like_image(raw, mime):
            logger.warning("Dropped an attachment: bytes don't look like a %s image.", mime)
            continue
        name = f"{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:8]}{ext}"
        path = UPLOAD_DIR / name
        path.write_bytes(raw)
        kind = item.get("kind") if item.get("kind") in _KIND_DESCRIPTION else "file"
        saved.append({"path": str(path), "kind": kind})
    return saved


def prompt_note(saved: list) -> str:
    """The note prepended to the user's message for this turn."""
    if not saved:
        return ""
    one = len(saved) == 1
    lines = "\n".join(f"- {s['path']} ({_KIND_DESCRIPTION[s['kind']]})" for s in saved)
    return (
        f"[The user is showing you {'an image' if one else f'{len(saved)} images'} with this message. "
        f"Open {'it' if one else 'each one'} with your Read tool before replying -- "
        f"{'it is' if one else 'they are'} what they're asking about. Don't mention these file paths "
        f"unless they ask.\n{lines}]\n\n"
    )


def display_marker(saved: list) -> str:
    """Short marker for the user's own message in shared chat history
    (the full prompt note stays out of what other devices see)."""
    if not saved:
        return ""
    kinds = {s["kind"] for s in saved}
    if kinds == {"screen"}:
        return "[shared their screen]"
    if kinds == {"camera"}:
        return "[shared a photo]"
    return "[shared an image]" if len(saved) == 1 else f"[shared {len(saved)} images]"
