"""
Gmail + Calendar tools for Neural, talking directly to the classic,
generally-available Gmail API (gmail.googleapis.com) and Calendar API
(www.googleapis.com/calendar/v3) rather than Google's newer
gmailmcp.googleapis.com / calendarmcp.googleapis.com gateway. That
gateway turned out to require enrollment in the Google Workspace
Developer Preview Program, which is restricted to Workspace
business/education accounts -- personal @gmail.com accounts are not
eligible at all. This uses the same OAuth credentials
(backend/google_auth.py) against the ordinary REST APIs instead, which
personal accounts can use today.

Runs in-process (no subprocess, no npx) via the Agent SDK's SDK-MCP
support -- see create_sdk_mcp_server below.
"""

import asyncio
import base64
import datetime
from email.mime.text import MIMEText
from typing import Any

import httpx
from claude_agent_sdk import create_sdk_mcp_server, tool

from .google_auth import get_access_token

GMAIL_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"
CALENDAR_BASE = "https://www.googleapis.com/calendar/v3/calendars/primary"


async def _headers() -> dict:
    # get_access_token() is a blocking (sync) network call; keep it off
    # the event loop.
    token = await asyncio.to_thread(get_access_token)
    return {"Authorization": f"Bearer {token}"}


def _text(s: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": s}]}


def _b64d(data: str) -> bytes:
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded)


def _extract_header(headers: list, name: str) -> str:
    for h in headers:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def _extract_body(payload: dict) -> str:
    """Walk a Gmail message payload for the first text/plain part."""
    if payload.get("mimeType") == "text/plain" and payload.get("body", {}).get("data"):
        return _b64d(payload["body"]["data"]).decode("utf-8", "replace")
    for part in payload.get("parts", []) or []:
        found = _extract_body(part)
        if found:
            return found
    if payload.get("mimeType") == "text/html" and payload.get("body", {}).get("data"):
        return _b64d(payload["body"]["data"]).decode("utf-8", "replace")
    return ""


@tool(
    "gmail_search",
    "Search Gmail using Gmail's own query syntax, e.g. 'is:unread', "
    "'from:someone@example.com', 'newer_than:2d'.",
    {"query": str, "max_results": int},
)
async def gmail_search(args: dict) -> dict[str, Any]:
    query = args["query"]
    max_results = args.get("max_results") or 10
    headers = await _headers()
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(
            f"{GMAIL_BASE}/messages",
            headers=headers,
            params={"q": query, "maxResults": max_results},
        )
        resp.raise_for_status()
        ids = [m["id"] for m in resp.json().get("messages", [])]

        results = []
        for mid in ids:
            m = await client.get(
                f"{GMAIL_BASE}/messages/{mid}",
                headers=headers,
                params={
                    "format": "metadata",
                    "metadataHeaders": ["Subject", "From", "Date"],
                },
            )
            m.raise_for_status()
            data = m.json()
            hl = data.get("payload", {}).get("headers", [])
            results.append(
                {
                    "id": mid,
                    "from": _extract_header(hl, "From"),
                    "subject": _extract_header(hl, "Subject"),
                    "date": _extract_header(hl, "Date"),
                    "snippet": data.get("snippet", ""),
                }
            )
    if not results:
        return _text("No messages matched.")
    lines = [
        f"- [{r['id']}] {r['date']} | {r['from']} | {r['subject']}\n  {r['snippet']}"
        for r in results
    ]
    return _text("\n".join(lines))


@tool(
    "gmail_read",
    "Read the full body of one Gmail message by its id (from gmail_search results).",
    {"message_id": str},
)
async def gmail_read(args: dict) -> dict[str, Any]:
    headers = await _headers()
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(
            f"{GMAIL_BASE}/messages/{args['message_id']}",
            headers=headers,
            params={"format": "full"},
        )
        resp.raise_for_status()
        data = resp.json()
    hl = data.get("payload", {}).get("headers", [])
    body = _extract_body(data.get("payload", {})) or data.get("snippet", "")
    header_line = (
        f"From: {_extract_header(hl, 'From')}\n"
        f"Subject: {_extract_header(hl, 'Subject')}\n"
        f"Date: {_extract_header(hl, 'Date')}\n\n"
    )
    return _text(header_line + body)


@tool(
    "gmail_send",
    "Send an email from the user's Gmail account. Always show the user the "
    "exact To/Subject/Body and get their explicit go-ahead before calling this.",
    {"to": str, "subject": str, "body": str},
)
async def gmail_send(args: dict) -> dict[str, Any]:
    msg = MIMEText(args["body"])
    msg["to"] = args["to"]
    msg["subject"] = args["subject"]
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
    headers = await _headers()
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            f"{GMAIL_BASE}/messages/send", headers=headers, json={"raw": raw}
        )
        resp.raise_for_status()
    return _text(f"Sent to {args['to']}.")


@tool(
    "calendar_list_events",
    "List upcoming Google Calendar events. time_min/time_max are optional "
    "RFC3339 timestamps; defaults to now onward.",
    {"time_min": str, "time_max": str, "max_results": int},
)
async def calendar_list_events(args: dict) -> dict[str, Any]:
    params = {
        "singleEvents": "true",
        "orderBy": "startTime",
        "maxResults": args.get("max_results") or 10,
        "timeMin": args.get("time_min")
        or datetime.datetime.utcnow().isoformat() + "Z",
    }
    if args.get("time_max"):
        params["timeMax"] = args["time_max"]
    headers = await _headers()
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(f"{CALENDAR_BASE}/events", headers=headers, params=params)
        resp.raise_for_status()
        items = resp.json().get("items", [])
    if not items:
        return _text("No upcoming events.")
    lines = []
    for e in items:
        start = e.get("start", {}).get("dateTime") or e.get("start", {}).get("date")
        lines.append(f"- [{e.get('id')}] {start} | {e.get('summary', '(no title)')}")
    return _text("\n".join(lines))


@tool(
    "calendar_create_event",
    "Create a Google Calendar event. start/end are RFC3339 timestamps.",
    {"summary": str, "start": str, "end": str, "description": str},
)
async def calendar_create_event(args: dict) -> dict[str, Any]:
    body = {
        "summary": args["summary"],
        "start": {"dateTime": args["start"]},
        "end": {"dateTime": args["end"]},
    }
    if args.get("description"):
        body["description"] = args["description"]
    headers = await _headers()
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(f"{CALENDAR_BASE}/events", headers=headers, json=body)
        resp.raise_for_status()
        data = resp.json()
    return _text(f"Created: {data.get('summary')} ({data.get('htmlLink', '')})")


GOOGLE_WORKSPACE_TOOLS = [
    "gmail_search",
    "gmail_read",
    "gmail_send",
    "calendar_list_events",
    "calendar_create_event",
]

google_workspace_server = create_sdk_mcp_server(
    name="google_workspace",
    version="1.0.0",
    tools=[gmail_search, gmail_read, gmail_send, calendar_list_events, calendar_create_event],
)
