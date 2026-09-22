"""
On-demand job search via JSearch (RapidAPI) -- pulls real, current
listings aggregated from LinkedIn, Indeed, Glassdoor, and more through
Google for Jobs, without touching LinkedIn directly (and risking the
account getting flagged/rate-limited the way browser-automating it
would).

This is on-demand only, by design: the user asked for a tool they can
invoke ("find me X jobs right now"), not a background automation that
runs on a schedule and pushes results unprompted. If that changes later,
this is a natural candidate to also wire into automations.py.

Requires a free RapidAPI account + a JSearch subscription (the free/Basic
plan) -- account creation isn't something Neural can do on the user's
behalf, so this stays disabled (JSEARCH_ENABLED = False) until the user
signs up themselves and drops the resulting X-RapidAPI-Key into .env as
JSEARCH_API_KEY. See the README's "Job search (JSearch)" section for the
one-time signup steps. Note: JSearch's exact free-tier request quota
wasn't independently verified while building this -- check the live
RapidAPI dashboard for the current number rather than trusting a
remembered figure.
"""

from typing import Any

import httpx
from claude_agent_sdk import create_sdk_mcp_server, tool

from . import config

JSEARCH_BASE_URL = "https://jsearch.p.rapidapi.com"


def _text(s: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": s}]}


def _format_job(job: dict) -> str:
    title = job.get("job_title") or "?"
    employer = job.get("employer_name") or "?"
    city = job.get("job_city") or ""
    state = job.get("job_state") or ""
    country = job.get("job_country") or ""
    location_bits = [b for b in (city, state, country) if b]
    location = ", ".join(location_bits) if location_bits else "location not listed"
    if job.get("job_is_remote"):
        location = ("Remote (" + location + ")") if location_bits else "Remote"

    salary = ""
    lo, hi, period = job.get("job_min_salary"), job.get("job_max_salary"), job.get("job_salary_period")
    if lo or hi:
        if lo and hi:
            salary_range = f"{lo:,.0f}-{hi:,.0f}"
        else:
            salary_range = f"{(lo or hi):,.0f}"
        salary = " -- " + salary_range + ("/" + period.lower() if period else "")

    posted = job.get("job_posted_at_datetime_utc") or ""
    apply_link = job.get("job_apply_link") or ""

    line = "- \"" + title + "\" at " + employer + " (" + location + ")" + salary
    if posted:
        line += ", posted " + posted
    if apply_link:
        line += "\n  Apply: " + apply_link
    return line


@tool(
    "search_jobs",
    "Search for real, current job listings (aggregated from LinkedIn, Indeed, Glassdoor, and "
    "more via Google for Jobs) using free-text criteria -- e.g. 'senior python developer "
    "remote' or 'marketing manager in Chicago'. On-demand only: call this whenever the user "
    "asks to find, search for, or look up jobs -- never guess or answer from memory, since "
    "listings change constantly. Fold location, seniority, or remote preference straight into "
    "the 'query' text the way you'd type it into a job search box.",
    {
        "query": str,
        "num_results": int,
        "remote_only": bool,
        "date_posted": str,
    },
)
async def search_jobs(args: dict) -> dict[str, Any]:
    if not config.JSEARCH_ENABLED:
        return _text(
            "Job search isn't set up yet -- the user needs to sign up for a free RapidAPI "
            "account, subscribe to JSearch's free plan, and add the resulting API key to "
            "their .env as JSEARCH_API_KEY (then restart the server) before this tool works."
        )

    query = (args.get("query") or "").strip()
    if not query:
        return _text("Give me something to search for -- a role, a location, or both.")

    num_results = args.get("num_results") or 10
    try:
        num_results = max(1, min(int(num_results), 30))
    except (TypeError, ValueError):
        num_results = 10
    num_pages = max(1, (num_results + 9) // 10)

    params: dict[str, Any] = {"query": query, "page": "1", "num_pages": str(num_pages)}
    if args.get("remote_only"):
        # Confirmed against RapidAPI's own live "Job Search" playground
        # (Sept 2026): the current v5 API uses work_from_home, not the
        # remote_jobs_only name from earlier JSearch docs/versions.
        params["work_from_home"] = "true"
    date_posted = args.get("date_posted")
    # Valid values per RapidAPI's own validation error message: "anytime"
    # (default) -- not "all", which is what an earlier version of this code
    # (and some older docs) used, and which the live API now rejects with a
    # 400.
    if date_posted in ("anytime", "today", "3days", "week", "month"):
        params["date_posted"] = date_posted

    headers = {
        "X-RapidAPI-Key": config.JSEARCH_API_KEY,
        "X-RapidAPI-Host": "jsearch.p.rapidapi.com",
    }

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(JSEARCH_BASE_URL + "/search-v2", headers=headers, params=params)
        resp.raise_for_status()
        payload = resp.json()
    except httpx.HTTPStatusError as e:
        status = e.response.status_code
        # RapidAPI's own gateway returns a JSON (or plain-text) body explaining
        # *why* even on a 4xx -- e.g. {"message": "You are not subscribed to
        # this API."} -- and that message is far more useful for diagnosing a
        # setup problem than the bare status code, so always surface it
        # (truncated) rather than just str(e), which drops the body entirely.
        try:
            body = e.response.text.strip()
        except Exception:
            body = ""
        body_snippet = (" Response: " + body[:300]) if body else ""

        if status in (401, 403):
            return _text(
                "JSearch rejected the request (auth error, HTTP " + str(status) + ") -- the "
                "API key may be wrong, expired, or not subscribed to the JSearch API on "
                "RapidAPI. Check JSEARCH_API_KEY in .env and the subscription status at "
                "rapidapi.com." + body_snippet
            )
        if status == 404:
            return _text(
                "JSearch returned HTTP 404 -- on RapidAPI this usually means either the "
                "request path is wrong for the current API version, or the key's RapidAPI "
                "application isn't actually subscribed to JSearch's pricing plan yet "
                "(viewing the docs page isn't the same as subscribing). Check the JSearch "
                "'Job Search' endpoint's own 'Test Endpoint' button on rapidapi.com directly "
                "-- if that also 404s, it's a subscription/account issue, not something in "
                "Neural's code." + body_snippet
            )
        if status == 429:
            return _text(
                "JSearch rate-limited this request -- the free plan's request quota may be "
                "used up for now. Try again later, or check usage at rapidapi.com."
                + body_snippet
            )
        return _text(
            "JSearch request failed (HTTP " + str(status) + ")." + body_snippet
        )
    except httpx.HTTPError as e:
        return _text("Couldn't reach JSearch: " + str(e))

    jobs = (payload or {}).get("data") or []
    if not jobs:
        return _text("No jobs found for \"" + query + "\". Try broadening the search terms.")

    jobs = jobs[:num_results]
    lines = [_format_job(j) for j in jobs]
    return _text("Found " + str(len(jobs)) + " job(s) for \"" + query + "\":\n\n" + "\n".join(lines))


JOBS_TOOLS = ["search_jobs"]

jobs_server = create_sdk_mcp_server(name="jobs", version="1.0.0", tools=[search_jobs])
