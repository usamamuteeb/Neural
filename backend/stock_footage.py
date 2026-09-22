"""
Free real stock video via Pexels' own official Video Search API
(https://www.pexels.com/api/) -- for social content that needs real
b-roll footage, not existing footage on the machine or an AI-generated
clip.

Why this instead of AI video generation: three separate "free" AI video
options were tried and each turned out not to actually be free on an
ongoing basis --

  1. Sora (OpenAI) -- discontinued entirely (web/app April 26 2026, API
     September 24 2026), so it isn't available at any price anymore.
  2. Google Veo/Gemini -- Google's own support docs say video generation
     needs a paid Google AI plan (at least Google AI Plus); some blog
     posts claimed a free tier but that wasn't confirmed and contradicts
     Google's own page.
  3. Kling AI -- confirmed live (the user checked their own account)
     that the "66 credits" advertised as a free daily allowance is
     actually a one-time signup trial that expires, not a recurring
     free tier. After it runs out, Kling is a paid subscription like
     the others.

Pattern: every "free" AI video generator treats free as a one-time
signup hook, not a real ongoing tier, because the compute cost per
video is real. That's not something worth re-litigating with a fourth
vendor -- so this pivots to something actually, verifiably free
forever: real stock footage, fed into the existing video-editor
pipeline (trim/caption/color-grade/compose/narration) the same way a
screen recording or an AI clip would be. Confirmed directly against
Pexels' own API docs: completely free, no card, 200 requests/hour,
20,000/month, no trial period.

Setup: free, instant signup at https://www.pexels.com/api/ -- no credit
card, an API key is issued immediately. Paste it into .env as
PEXELS_API_KEY.
"""

from typing import Any

import httpx
from claude_agent_sdk import create_sdk_mcp_server, tool

from . import config

# Pexels documents this as the current endpoint for video search; an
# older https://api.pexels.com/videos/search (no /v1/) path exists too
# but Pexels' own docs mark it as being deprecated in favor of this one.
PEXELS_VIDEO_SEARCH_URL = "https://api.pexels.com/v1/videos/search"

VALID_ORIENTATIONS = {"landscape", "portrait", "square"}


def _text(s: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": s}]}


def _pick_file(video_files: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Prefer Pexels' own "hd" quality tier (a sane, not-huge default for
    a social clip); fall back to the largest file available if no file
    is explicitly tagged hd."""
    if not video_files:
        return None
    hd = next((f for f in video_files if f.get("quality") == "hd"), None)
    if hd:
        return hd
    return max(video_files, key=lambda f: (f.get("width") or 0) * (f.get("height") or 0))


@tool(
    "search_stock_video",
    "Search Pexels' free, real stock video library for b-roll footage matching a topic or "
    "keyword -- use this when a post needs a real video clip that isn't already on the machine "
    "and isn't worth generating with AI (Neural has no working free AI video generator; see "
    "agent.py's system prompt for why). Returns, per result, a direct downloadable file URL "
    "(fetch it with Bash/curl, no login or session needed), the clip's duration, resolution, "
    "and the Pexels page URL for optional creator credit (not legally required by Pexels' "
    "license, but a nice touch if the post has room for it). Pass a plain-language topic as "
    "'query'; 'orientation' should usually be 'portrait' for Reels/TikTok/Shorts and "
    "'landscape' for everything else if you don't already know which platform this is for.",
    {"query": str, "orientation": str, "num_results": int},
)
async def search_stock_video(args: dict) -> dict[str, Any]:
    if not config.PEXELS_ENABLED:
        return _text(
            "Stock footage isn't set up yet -- the user hasn't added their free Pexels API key "
            "(instant signup at pexels.com/api, no card needed) to .env. Relay that plainly; "
            "this isn't something you can do on their behalf."
        )

    query = (args.get("query") or "").strip()
    if not query:
        return _text("Need a topic or keywords to search stock footage for.")

    orientation = (args.get("orientation") or "").strip().lower()
    if orientation and orientation not in VALID_ORIENTATIONS:
        orientation = ""  # ignore an invalid value rather than fail the whole search over it

    try:
        num_results = max(1, min(int(args.get("num_results") or 5), 15))
    except (TypeError, ValueError):
        num_results = 5

    params: dict[str, Any] = {"query": query, "per_page": str(num_results)}
    if orientation:
        params["orientation"] = orientation

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(
                PEXELS_VIDEO_SEARCH_URL,
                headers={"Authorization": config.PEXELS_API_KEY},
                params=params,
            )
    except httpx.RequestError as e:
        return _text(f"Couldn't reach Pexels: {e}")

    if resp.status_code == 429:
        return _text(
            "Pexels rate limit hit (free tier: 200 requests/hour, 20,000/month) -- wait a bit "
            "before searching again."
        )
    if resp.status_code != 200:
        body_snippet = "\nPexels said: " + resp.text[:300] if resp.text else ""
        if resp.status_code in (401, 403):
            return _text(
                "Pexels rejected the request (HTTP "
                + str(resp.status_code)
                + ") -- the API key in .env is likely wrong. Double-check it against your "
                "pexels.com/api dashboard." + body_snippet
            )
        return _text(f"Pexels returned HTTP {resp.status_code}." + body_snippet)

    try:
        data = resp.json()
    except ValueError:
        return _text("Pexels returned a response that wasn't valid JSON -- can't parse results.")

    videos = data.get("videos") or []
    if not videos:
        return _text(f'No stock footage found for "{query}". Try a broader or different term.')

    lines = [f'{len(videos)} result(s) for "{query}":']
    for v in videos:
        f = _pick_file(v.get("video_files") or [])
        if not f:
            continue
        credit = (v.get("user") or {}).get("name") or "unknown"
        lines.append(
            f"- {v.get('duration')}s, {f.get('width')}x{f.get('height')} -- "
            f"download: {f.get('link')} -- credit: {credit} ({v.get('url')})"
        )

    return _text("\n".join(lines))


STOCK_FOOTAGE_TOOLS = ["search_stock_video"]
stock_footage_server = create_sdk_mcp_server(
    name="stock_footage", version="1.0.0", tools=[search_stock_video]
)
