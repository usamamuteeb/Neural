"""
On-demand job search via Adzuna's own official job-search API
(https://developer.adzuna.com) -- not RapidAPI/JSearch.

Why the switch: JSearch (via RapidAPI) went through two rounds of real,
confirmed endpoint/param bugs (wrong path, wrong param names, wrong
enum values) that got fixed live against RapidAPI's own test console --
and it *still* kept failing afterward with no clear cause visible from
here. Adzuna is the job board's own directly-documented API, not a
third-party wrapper on top of it, so there's one fewer layer that can be
subtly wrong or go down independently.

Setup: free, instant signup at https://developer.adzuna.com -- no credit
card, an app_id/app_key pair is issued immediately. Free tier is capped
at a few hundred calls/day (check your own developer.adzuna.com
dashboard for the current number). Paste both into .env as
ADZUNA_APP_ID / ADZUNA_APP_KEY.

Known real limitation, not a bug: Adzuna only covers a specific set of
countries -- gb, us, de, fr, au, nz, ca, in, pl, br, at, za (see
https://developer.adzuna.com/docs/regional for the current list) -- NOT
Pakistan, and not most of Asia/the Middle East/Eastern Europe. A search
has to target one of the covered countries; search_jobs defaults to
"us" and says so plainly if asked for one Adzuna doesn't cover, rather
than silently guessing or falling back to something misleading.
"""

import html
import re
from typing import Any

import httpx
from claude_agent_sdk import create_sdk_mcp_server, tool

from . import config

ADZUNA_BASE_URL = "https://api.adzuna.com/v1/api/jobs"

# Confirmed supported by Adzuna as of when this was wired in. If a search
# ever 404s with an "unknown country" style error, check
# https://developer.adzuna.com/docs/regional for the current list --
# Adzuna has expanded coverage before and may again.
SUPPORTED_COUNTRIES = {
    "gb", "us", "de", "fr", "au", "nz", "ca", "in", "pl", "br", "at", "za",
}

_TAG_RE = re.compile(r"<[^>]+>")


def _text(s: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": s}]}


def _clean(s: Any) -> str:
    """Adzuna's text fields sometimes carry HTML entities/stray tags --
    strip both so results read as plain chat text."""
    if not s:
        return ""
    return html.unescape(_TAG_RE.sub("", str(s))).strip()


def _format_job(job: dict[str, Any]) -> str:
    title = _clean(job.get("title")) or "Untitled role"
    company = _clean((job.get("company") or {}).get("display_name")) or "Unknown company"
    location = _clean((job.get("location") or {}).get("display_name")) or "Location not listed"

    salary_min = job.get("salary_min")
    salary_max = job.get("salary_max")
    salary = ""
    if salary_min and salary_max and salary_min != salary_max:
        salary = f" -- approx. {salary_min:,.0f}-{salary_max:,.0f}"
    elif salary_min:
        salary = f" -- approx. {salary_min:,.0f}+"

    posted = (job.get("created") or "")[:10] or "unknown date"
    url = job.get("redirect_url") or ""
    return f"- {title} at {company} ({location}){salary}, posted {posted}\n  Apply: {url}"


@tool(
    "search_jobs",
    "Search real, current job listings via Adzuna (adzuna.com) when the user asks to find, search "
    "for, or look up jobs -- on-demand only, not a background automation. Adzuna only covers a "
    "specific set of countries: gb, us, de, fr, au, nz, ca, in, pl, br, at, za. If the user wants a "
    "country outside that list (including Pakistan), say plainly that Adzuna doesn't cover it "
    "rather than guessing or silently defaulting to something else. Pass their role/keywords into "
    "'query', a city/region into 'location' if they gave one, and a two-letter country code into "
    "'country' (default 'us' if they don't say). Adzuna has no dedicated remote-only filter --  "
    "if they want remote roles, just include \"remote\" in the query text itself.",
    {"query": str, "location": str, "country": str, "num_results": int},
)
async def search_jobs(args: dict) -> dict[str, Any]:
    if not config.ADZUNA_ENABLED:
        return _text(
            "Job search isn't set up yet -- the user hasn't added their free Adzuna app_id/app_key "
            "(instant signup at developer.adzuna.com, no card needed) to .env. Relay that plainly; "
            "this isn't something you can do on their behalf."
        )

    query = (args.get("query") or "").strip()
    if not query:
        return _text("Need a role or some keywords to search for.")

    country = (args.get("country") or "us").strip().lower()
    if country not in SUPPORTED_COUNTRIES:
        return _text(
            f"Adzuna doesn't cover \"{country}\" -- it only covers: {', '.join(sorted(SUPPORTED_COUNTRIES))}. "
            "Tell the user plainly that country isn't available rather than guessing at a workaround."
        )

    try:
        num_results = max(1, min(int(args.get("num_results") or 10), 30))
    except (TypeError, ValueError):
        num_results = 10

    params: dict[str, Any] = {
        "app_id": config.ADZUNA_APP_ID,
        "app_key": config.ADZUNA_APP_KEY,
        "what": query,
        "results_per_page": str(num_results),
        "content-type": "application/json",
    }
    location = (args.get("location") or "").strip()
    if location:
        params["where"] = location

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(f"{ADZUNA_BASE_URL}/{country}/search/1", params=params)
    except httpx.RequestError as e:
        return _text(f"Couldn't reach Adzuna: {e}")

    if resp.status_code != 200:
        body_snippet = "\nAdzuna said: " + resp.text[:300] if resp.text else ""
        if resp.status_code in (401, 403):
            return _text(
                "Adzuna rejected the request (HTTP "
                + str(resp.status_code)
                + ") -- the app_id/app_key in .env is likely wrong, or not active yet. Double-check "
                "them against the developer.adzuna.com dashboard." + body_snippet
            )
        return _text(f"Adzuna returned HTTP {resp.status_code}." + body_snippet)

    try:
        data = resp.json()
    except ValueError:
        return _text("Adzuna returned a response that wasn't valid JSON -- can't parse results.")

    results = data.get("results") or []
    where_note = f" in {location}" if location else ""
    if not results:
        return _text(f"No jobs found for \"{query}\"{where_note} ({country}).")

    lines = [_format_job(job) for job in results]
    return _text(
        f"Found {len(lines)} job(s) for \"{query}\"{where_note} ({country}):\n\n" + "\n".join(lines)
    )


JOBS_TOOLS = ["search_jobs"]
jobs_server = create_sdk_mcp_server(name="jobs", version="1.0.0", tools=[search_jobs])
