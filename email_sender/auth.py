"""
Gmail OAuth2 helper — reads client credentials from .env (no credentials.json needed).

First run  → opens browser for Google sign-in, saves gmail_token.json.
Later runs → loads & auto-refreshes the stored token silently.

Required .env keys:
    GMAIL_CLIENT_ID
    GMAIL_CLIENT_SECRET
"""

import json
import os

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from config import Config
from utils.logger import get_logger

logger = get_logger(__name__)


def _has_scopes(creds: Credentials, required: list[str]) -> bool:
    """Return True if the credentials cover all required scopes."""
    granted = set(getattr(creds, "scopes", None) or [])
    return all(s in granted for s in required)


SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.settings.basic",   # needed to read signature
]


def _client_config() -> dict:
    """Build the client config dict that google-auth expects, using env vars."""
    return {
        "installed": {
            "client_id":                  Config.GMAIL_CLIENT_ID,
            "client_secret":              Config.GMAIL_CLIENT_SECRET,
            "project_id":                 Config.GMAIL_PROJECT_ID,
            "auth_uri":                   "https://accounts.google.com/o/oauth2/auth",
            "token_uri":                  "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "redirect_uris":              ["http://localhost"],
        }
    }


def get_gmail_credentials() -> Credentials:
    """Return valid Gmail credentials, running the OAuth flow if needed."""
    creds: Credentials | None = None

    # Load saved token if it exists
    if os.path.exists(Config.GMAIL_TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(Config.GMAIL_TOKEN_FILE, SCOPES)

    # Force re-auth if token is missing any required scope
    if creds and not _has_scopes(creds, SCOPES):
        logger.info("Token is missing required scopes — re-authorising…")
        creds = None

    # Refresh or re-authorize as needed
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            logger.info("Refreshing Gmail access token…")
            creds.refresh(Request())
        else:
            logger.info("Opening browser for Gmail authorisation…")
            flow = InstalledAppFlow.from_client_config(_client_config(), SCOPES)
            creds = flow.run_local_server(port=0)

        # Persist the token so the browser flow only runs once
        with open(Config.GMAIL_TOKEN_FILE, "w") as fh:
            fh.write(creds.to_json())
        logger.info(f"Gmail token saved → {Config.GMAIL_TOKEN_FILE}")

    return creds
