"""
Web Push notifications (RFC 8030 push protocol + VAPID/RFC 8292), so
Neural can alert the user on their phone or desktop even when the PWA
isn't open in the foreground -- automation results, real tool/voice
errors, and anything the agent itself decides is worth flagging via the
send_notification tool (see notifications.py).

Zero manual setup: a VAPID keypair is generated once on first run and
persisted to Neural/vapid_private_key.pem (gitignored). The public half
is served to the frontend at GET /api/push/vapid-public-key so it can
call pushManager.subscribe(). Each subscription a browser creates (one
per device/browser install) is stored in Neural/push_subscriptions.json,
mirroring automations.json's plain-JSON-file pattern.

The keypair MUST stay stable across restarts -- regenerating it silently
invalidates every subscription already held by a browser, since the
browser pins its subscription to the public key it subscribed with.

Requires an HTTPS or localhost origin -- browsers refuse to hand out a
push subscription on a plain http:// LAN address. See the README's
Mobile access section for the tunnel options if you want this to work
from a phone hitting Neural over your LAN/Wi-Fi.
"""

import base64
import json
import logging
from pathlib import Path
from typing import Any, Optional

from pywebpush import WebPushException, webpush

from . import config

logger = logging.getLogger("neural.push")

ROOT = Path(__file__).resolve().parent.parent
PRIVATE_KEY_PATH = ROOT / "vapid_private_key.pem"
SUBS_PATH = ROOT / "push_subscriptions.json"


def _generate_keypair() -> None:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    PRIVATE_KEY_PATH.write_bytes(pem)
    logger.info("Generated a new VAPID keypair at %s", PRIVATE_KEY_PATH)


def _public_key_b64(pem_bytes: bytes) -> str:
    """The VAPID public key in the raw, uncompressed-point, base64url (no
    padding) form the Push API's applicationServerKey wants -- 0x04 byte
    + 32-byte X + 32-byte Y."""
    from cryptography.hazmat.primitives import serialization

    key = serialization.load_pem_private_key(pem_bytes, password=None)
    numbers = key.public_key().public_numbers()
    raw = b"\x04" + numbers.x.to_bytes(32, "big") + numbers.y.to_bytes(32, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _ensure_keys() -> str:
    if not PRIVATE_KEY_PATH.exists():
        _generate_keypair()
    return _public_key_b64(PRIVATE_KEY_PATH.read_bytes())


VAPID_PUBLIC_KEY = _ensure_keys()


def _load_subs() -> dict[str, dict[str, Any]]:
    if not SUBS_PATH.exists():
        return {}
    try:
        return json.loads(SUBS_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_subs(subs: dict[str, dict[str, Any]]) -> None:
    SUBS_PATH.write_text(json.dumps(subs, indent=2), encoding="utf-8")


def add_subscription(sub: dict[str, Any]) -> None:
    """sub is exactly what the browser's PushSubscription.toJSON() gives
    the frontend: {"endpoint": ..., "keys": {"p256dh": ..., "auth": ...}}.
    Keyed by endpoint, so re-subscribing the same browser/device just
    overwrites its existing entry instead of piling up duplicates."""
    subs = _load_subs()
    subs[sub["endpoint"]] = sub
    _save_subs(subs)


def remove_subscription(endpoint: str) -> bool:
    subs = _load_subs()
    if endpoint not in subs:
        return False
    del subs[endpoint]
    _save_subs(subs)
    return True


def subscription_count() -> int:
    return len(_load_subs())


def send_notification(title: str, body: str, tag: Optional[str] = None, url: str = "/") -> int:
    """Push a notification to every subscribed device. Synchronous/
    blocking (pywebpush uses requests under the hood) -- callers on the
    asyncio event loop should run this via asyncio.to_thread. Returns how
    many devices it actually reached. Subscriptions the push service
    reports as gone (410/404 -- PWA uninstalled, browser data cleared,
    etc.) are pruned so the store doesn't accumulate dead entries."""
    subs = _load_subs()
    if not subs:
        return 0
    payload = json.dumps({"title": title, "body": body, "tag": tag, "url": url})
    sent = 0
    dead: list[str] = []
    for endpoint, sub in subs.items():
        try:
            webpush(
                subscription_info=sub,
                data=payload,
                vapid_private_key=str(PRIVATE_KEY_PATH),
                vapid_claims={"sub": config.VAPID_CONTACT_EMAIL},
            )
            sent += 1
        except WebPushException as exc:
            status = getattr(exc.response, "status_code", None)
            if status in (404, 410):
                dead.append(endpoint)
            else:
                logger.error("Push to %s failed: %s", endpoint[:60], exc)
        except Exception as exc:  # noqa: BLE001 -- one bad subscription shouldn't block the rest
            logger.error("Push to %s failed: %s", endpoint[:60], exc)
    if dead:
        subs = _load_subs()
        for endpoint in dead:
            subs.pop(endpoint, None)
        _save_subs(subs)
    return sent
