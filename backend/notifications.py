"""
Lets Neural push a notification to the user's phone/desktop proactively --
"tell me when the download finishes", "alert me if my flight changes",
"let me know once that automation is done". Always registered (like
memory), since sending a heads-up is core behavior, not an optional
integration. Delivery itself (push.py, Web Push) is best-effort: if the
user hasn't turned notifications on in the app yet, this quietly reports
zero devices reached instead of erroring, since that's a normal, expected
state, not a bug.

No confirmation needed before calling this, unlike gmail_send or a
Slack/WhatsApp send -- a notification only reaches the user's own
device, it never goes to another person.
"""

import asyncio
from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool

from . import push
from .events import bus


def _text(s: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": s}]}


@tool(
    "send_notification",
    "Push a notification to the user's phone/desktop right now -- e.g. something they "
    "asked to be alerted about, a result worth flagging, or an error you can't resolve "
    "yourself. Only reaches the user's own device (no confirmation needed, unlike "
    "messaging another person). Keep 'title' to a few words and 'body' to 1-2 sentences.",
    {"title": str, "body": str},
)
async def send_notification(args: dict) -> dict[str, Any]:
    title, body = args["title"], args["body"]
    sent = await asyncio.to_thread(push.send_notification, title, body)
    bus.publish({"type": "notification", "title": title, "body": body, "delivered": sent})
    if sent == 0:
        return _text(
            "No notification was delivered -- the user hasn't turned on notifications "
            "in the app yet (the ALERTS toggle in the top bar)."
        )
    return _text(f"Notification sent to {sent} device(s).")


NOTIFICATIONS_TOOLS = ["send_notification"]

notifications_server = create_sdk_mcp_server(
    name="notifications",
    version="1.0.0",
    tools=[send_notification],
)
