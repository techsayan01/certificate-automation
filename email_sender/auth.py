"""
Gmail OAuth2 helper.

First run  → opens browser, saves gmail_token.json.
Later runs → loads & auto-refreshes the stored token.

Setup:
  1. Go to https://console.cloud.google.com/
  2. Create a project → enable "Gmail API"
  3. OAuth consent screen → add your email as a test user
  4. Credentials → Create OAuth 2.0 Client ID (Desktop app)
  5. Download the JSON → save as credentials.json in the project root
"""

import os

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from config import Config
from utils.logger import get_logger

logger = get_logger(__name__)

SCOPES = ["https://www.googleapis.com/auth/gmail.send"]


def get_gmail_credentials() -> Credentials:
    """Return valid Gmail credentials, running the OAuth flow if needed."""
    creds: Credentials | None = None

    if os.path.exists(Config.GMAIL_TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(Config.GMAIL_TOKEN_FILE, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            logger.info("Refreshing Gmail access token…")
            creds.refresh(Request())
        else:
            if not os.path.exists(Config.GMAIL_CREDENTIALS_FILE):
                raise FileNotFoundError(
                    f"Gmail credentials file not found: {Config.GMAIL_CREDENTIALS_FILE}\n"
                    "Download it from Google Cloud Console → Credentials → OAuth 2.0 Client IDs."
                )
            logger.info("Opening browser for Gmail authorisation…")
            flow = InstalledAppFlow.from_client_secrets_file(
                Config.GMAIL_CREDENTIALS_FILE, SCOPES
            )
            creds = flow.run_local_server(port=0)

        with open(Config.GMAIL_TOKEN_FILE, "w") as fh:
            fh.write(creds.to_json())
        logger.info("Gmail token saved.")

    return creds
