"""
Docs cache for Tutor mode (layer 2 of 3 -- see tutor.py's module
docstring for the full three-layer design). When the tutor needs to
check an official doc page -- a language/library/API reference -- it
fetches the page, strips it to plain text, and summarizes it ONCE;
that summary plus the URL and fetch date is cached to disk and reused
for every later question that touches the same page, until it goes
stale (see config.TUTOR_DOCS_CACHE_DAYS), at which point the next
request for it re-fetches and re-summarizes instead of serving a
possibly-outdated cached answer forever.

Storage is plain JSON (tutor_docs_cache.json at the repo root), the
same pattern automations.json/push_subscriptions.json already use
elsewhere in this codebase -- a personal cache of maybe a few dozen
doc pages doesn't need a database.

Summarization runs on the existing cheap Haiku utility tier
(backend/utility.py), not the main Sonnet-backed tutor agent -- keeps
this fast and out of the conversation's own cost/latency path, same
reasoning as utility.py's own docstring gives for its other uses.

HTML is stripped with a small stdlib html.parser subclass rather than
adding BeautifulSoup/lxml as a new dependency just for this one job.
"""

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Optional

import httpx
from claude_agent_sdk import create_sdk_mcp_server, tool

from . import config, utility

logger = logging.getLogger("neural.docs_cache")

STORE_PATH = Path(__file__).resolve().parent.parent / "tutor_docs_cache.json"

_SKIP_TAGS = {"script", "style", "noscript", "svg", "template"}
_MAX_FETCH_CHARS = 20000  # bound how much page text we ever hand to the summarizer
_MAX_SUMMARY_INPUT_CHARS = 12000  # further bound just the summarization call itself


class _TextExtractor(HTMLParser):
    """Minimal HTML-to-plain-text extractor: drops script/style/etc.
    content, keeps everything else's text, and remembers the <title>."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._in_title = False
        self.title = ""
        self._chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        if tag == "title":
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
            return
        if self._skip_depth == 0 and data.strip():
            self._chunks.append(data.strip())

    def text(self) -> str:
        return re.sub(r"\n{3,}", "\n\n", "\n".join(self._chunks))


def _load() -> dict[str, dict[str, Any]]:
    if not STORE_PATH.exists():
        return {}
    try:
        return json.loads(STORE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save(cache: dict[str, dict[str, Any]]) -> None:
    STORE_PATH.write_text(json.dumps(cache, indent=2), encoding="utf-8")


def _is_stale(entry: dict[str, Any]) -> bool:
    fetched_at = entry.get("fetched_at")
    if not fetched_at:
        return True
    try:
        fetched = datetime.fromisoformat(fetched_at)
    except ValueError:
        return True
    return datetime.now(timezone.utc) - fetched > timedelta(days=config.TUTOR_DOCS_CACHE_DAYS)


def _text(s: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": s}]}


async def _fetch_and_summarize(url: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        resp = await client.get(
            url,
            headers={"User-Agent": "Neural-Tutor/1.0 (personal study assistant)"},
        )
    resp.raise_for_status()

    extractor = _TextExtractor()
    extractor.feed(resp.text)
    page_text = extractor.text()[:_MAX_FETCH_CHARS]
    title = extractor.title.strip() or url

    if not page_text.strip():
        raise ValueError("no readable text found on that page (it may be JS-rendered)")

    summary_prompt = (
        "Summarize the following documentation page for a student who will use this "
        "summary to answer technical questions later, without re-reading the page. "
        "Cover: what the page is about, the key APIs/functions/parameters/config it "
        "documents, any important gotchas or caveats it calls out, and a short code "
        "example if the page has one. Be thorough but concise -- plain prose, "
        "roughly 200-400 words, no meta-commentary about being a summary.\n\n"
        f"Page title: {title}\n\nPage text:\n{page_text[:_MAX_SUMMARY_INPUT_CHARS]}"
    )
    summary = (await utility.quick_task(summary_prompt)).strip()
    if not summary:
        summary = page_text[:1500]  # utility model failed for some reason -- fall back to raw text

    return {
        "url": url,
        "title": title,
        "summary": summary,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


@tool(
    "fetch_doc_page",
    "Fetch and summarize an official documentation page (a language/library/API "
    "reference URL), for when you need to check something rather than rely on "
    "training data alone. The result is cached by URL and reused on later questions "
    "about the same page until it goes stale (a couple weeks by default) -- so don't "
    "hesitate to call this again for a page you already fetched earlier in this or a "
    "past session, it'll just return the cached summary almost instantly and tell you "
    "it did. Pass force_refresh=true only if the user says the page changed or the "
    "cached info looks wrong.",
    {"url": str, "force_refresh": bool},
)
async def fetch_doc_page(args: dict) -> dict[str, Any]:
    url = (args.get("url") or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        return _text("Need a real http(s) URL to fetch a doc page.")

    force_refresh = bool(args.get("force_refresh"))
    cache = _load()
    entry = cache.get(url)

    if entry and not force_refresh and not _is_stale(entry):
        fetched_date = entry["fetched_at"][:10]
        return _text(
            f'"{entry["title"]}" ({url})\n'
            f"[cached -- fetched {fetched_date}, refreshes automatically after "
            f"{config.TUTOR_DOCS_CACHE_DAYS} days]\n\n{entry['summary']}"
        )

    try:
        fresh = await _fetch_and_summarize(url)
    except httpx.HTTPStatusError as e:
        return _text(f"Couldn't fetch {url}: HTTP {e.response.status_code}.")
    except httpx.RequestError as e:
        return _text(f"Couldn't reach {url}: {e}")
    except ValueError as e:
        return _text(f"Fetched {url} but couldn't use it: {e}")
    except Exception as e:  # noqa: BLE001 -- surface any other failure plainly rather than crash the turn
        logger.error("fetch_doc_page failed for %s: %s", url, e)
        return _text(f"Something went wrong fetching {url}: {e}")

    cache[url] = fresh
    _save(cache)
    return _text(f'"{fresh["title"]}" ({url})\n[freshly fetched just now]\n\n{fresh["summary"]}')


@tool(
    "list_cached_docs",
    "List every documentation page currently cached, with its title and when it was "
    "last fetched. Useful to check what's already available before fetching again.",
    {},
)
async def list_cached_docs(args: dict) -> dict[str, Any]:
    cache = _load()
    if not cache:
        return _text("No documentation pages cached yet.")
    lines = []
    for url, entry in cache.items():
        stale_note = " (stale, will refresh on next use)" if _is_stale(entry) else ""
        lines.append(f"- {entry.get('title', url)} -- {url} -- fetched {entry.get('fetched_at', '?')[:10]}{stale_note}")
    return _text("Cached documentation pages:\n" + "\n".join(lines))


DOCS_CACHE_TOOLS = ["fetch_doc_page", "list_cached_docs"]
docs_cache_server = create_sdk_mcp_server(
    name="tutor_docs", version="1.0.0", tools=[fetch_doc_page, list_cached_docs]
)
