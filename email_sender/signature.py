"""
Fetches the Gmail signature for the authenticated account via the
Gmail Settings API and caches it for the duration of the run.

The signature is whatever is configured in:
  Gmail → Settings → General → Signature

It is appended automatically after the template body in every email.
If no signature is configured, an empty string is returned silently.

External <img> URLs inside the fetched signature are rewritten to use
the CID inline image "gmail_signature" so the signature image renders
properly inside the email (Gmail blocks external image loads by default).
"""

from __future__ import annotations

import re

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from config import Config
from email_sender.auth import get_gmail_credentials
from utils.logger import get_logger

logger = get_logger(__name__)

_cache: dict[str, str] = {}          # project → signature HTML

# Matches any <img … src="http(s)://…" …> and rewrites src to CID
_IMG_SRC_RE = re.compile(r'(<img\b[^>]*?\bsrc=")https?://[^"]*(")', re.IGNORECASE)


def _rewrite_img_srcs(html: str) -> str:
    """Replace all external img src URLs with cid:gmail_signature."""
    return _IMG_SRC_RE.sub(r'\1cid:gmail_signature\2', html)


def get_signature(project: str) -> str:
    """
    Return the Gmail signature HTML for the currently loaded project.
    External image URLs are rewritten to cid:gmail_signature so they
    are served as CID inline attachments rather than blocked externals.
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
            sig_html = _rewrite_img_srcs(sig_html)
            logger.info("Gmail signature fetched successfully (external img URLs rewritten to CID).")
        else:
            logger.info("No Gmail signature configured — skipping.")

        _cache[project] = sig_html
        return sig_html

    except HttpError as exc:
        logger.warning(f"Could not fetch Gmail signature: {exc} — continuing without it.")
        _cache[project] = ""
        return ""
