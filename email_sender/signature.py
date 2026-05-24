"""
Fetches the Gmail signature for the authenticated account via the
Gmail Settings API and caches it for the duration of the run.

The signature is whatever is configured in:
  Gmail → Settings → General → Signature

It is appended automatically after the template body in every email.
If no signature is configured, an empty string is returned silently.
"""

from __future__ import annotations

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from config import Config
from email_sender.auth import get_gmail_credentials
from utils.logger import get_logger

logger = get_logger(__name__)

_cache: dict[str, str] = {}          # project → signature HTML


def get_signature(project: str) -> str:
    """
    Return the Gmail signature HTML for the currently loaded project.
    Result is cached so the API is only called once per run.
    """
    if project in _cache:
        return _cache[project]

    try:
        creds   = get_gmail_credentials()
        service = build("gmail", "v1", credentials=creds)

        # Use the sender email from config (avoids needing gmail.readonly scope)
        sender_email = Config.GMAIL_SENDER_EMAIL
        if not sender_email:
            logger.warning("GMAIL_SENDER_EMAIL not set in project .env — skipping signature.")
            _cache[project] = ""
            return ""

        result  = (
            service.users()
                   .settings()
                   .sendAs()
                   .get(userId="me", sendAsEmail=sender_email)
                   .execute()
        )
        sig_html = result.get("signature", "")

        if sig_html:
            logger.info("Gmail signature fetched successfully.")
        else:
            logger.info("No Gmail signature configured — skipping.")

        _cache[project] = sig_html
        return sig_html

    except HttpError as exc:
        logger.warning(f"Could not fetch Gmail signature: {exc} — continuing without it.")
        _cache[project] = ""
        return ""
