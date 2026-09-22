"""
One-time OAuth setup + token refresh for Google's Gmail/Calendar MCP
gateway (gmailmcp.googleapis.com / calendarmcp.googleapis.com).

Prerequisites (done once, by you, in Google Cloud Console — under the
personal Gmail account, not a work domain):
  1. Create a project; enable the Gmail API and Google Calendar API
     (and their MCP-gateway counterparts, if your console lists them
     separately from the plain REST APIs).
  2. Create an OAuth 2.0 "Desktop app" client. Put its client ID and
     secret into .env as GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET.
  3. Run from the Neural/ root:  python -m backend.google_auth setup
     A browser opens for consent; the refresh token it prints goes into
     .env as GOOGLE_REFRESH_TOKEN.

After that, get_access_token() mints a short-lived access token from the
refresh token on demand — agent.py calls it right before building the
mcp_servers config so the token handed to Google's MCP gateway is never
stale.

Unverified: the exact tool names gmailmcp/calendarmcp expose, and whether
their transport is "http" or "sse" in Agent SDK terms — Google's own docs
describe the OAuth/connection shape but not the wire-level tool schema.
Treat this file as scaffolding to test once you've done the Cloud Console
setup, not as something already confirmed working end-to-end.
"""

import sys

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from . import config

SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/calendar",
]


def run_oauth_flow() -> None:
    if not (config.GOOGLE_CLIENT_ID and config.GOOGLE_CLIENT_SECRET):
        raise SystemExit(
            "Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in .env first "
            "(from a Desktop-app OAuth client in Google Cloud Console)."
        )
    client_config = {
        "installed": {
            "client_id": config.GOOGLE_CLIENT_ID,
            "client_secret": config.GOOGLE_CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }
    flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
    creds = flow.run_local_server(port=0)
    print("\nAdd this to your .env as GOOGLE_REFRESH_TOKEN:\n")
    print(creds.refresh_token)


def get_access_token() -> str:
    creds = Credentials(
        token=None,
        refresh_token=config.GOOGLE_REFRESH_TOKEN,
        client_id=config.GOOGLE_CLIENT_ID,
        client_secret=config.GOOGLE_CLIENT_SECRET,
        token_uri="https://oauth2.googleapis.com/token",
    )
    creds.refresh(Request())
    return creds.token


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "setup":
        run_oauth_flow()
    else:
        print("Usage: python -m backend.google_auth setup")
