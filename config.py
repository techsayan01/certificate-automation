"""
Central configuration — loads every setting from .env.
Call Config.validate() at startup to catch missing credentials early.
"""

import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    # ── Gmail OAuth2 ───────────────────────────────────────────────────────────
    # Copy these from the JSON downloaded in Google Cloud Console
    # (open the JSON, look inside the "installed" block)
    GMAIL_CLIENT_ID: str     = os.getenv("GMAIL_CLIENT_ID", "")
    GMAIL_CLIENT_SECRET: str = os.getenv("GMAIL_CLIENT_SECRET", "")
    GMAIL_PROJECT_ID: str    = os.getenv("GMAIL_PROJECT_ID", "certificate-automation")

    # Token file — written automatically after the first browser auth flow.
    # Never commit this file.
    GMAIL_TOKEN_FILE: str = os.getenv("GMAIL_TOKEN_FILE", "gmail_token.json")

    # ── Email content ──────────────────────────────────────────────────────────
    EMAIL_SUBJECT: str       = os.getenv("EMAIL_SUBJECT", "Congratulations — Your Certificate is Here!")
    EMAIL_FROM_NAME: str     = os.getenv("EMAIL_FROM_NAME", "Your Organisation")
    EMAIL_TEMPLATE_DIR: str  = os.getenv("EMAIL_TEMPLATE_DIR", "email_sender/templates")
    DEFAULT_TEMPLATE: str    = os.getenv("DEFAULT_TEMPLATE", "default.html")

    # ── CSV / paths ────────────────────────────────────────────────────────────
    CSV_PATH: str    = os.getenv("CSV_PATH", "data/recipients.csv")
    OUTPUT_DIR: str  = os.getenv("OUTPUT_DIR", "data/output")

    # CSV column mapping (FilmFreeway export defaults)
    CSV_FIRST_NAME_COL: str = os.getenv("CSV_FIRST_NAME_COL", "First Name")
    CSV_LAST_NAME_COL: str  = os.getenv("CSV_LAST_NAME_COL",  "Last Name")
    CSV_EMAIL_COL: str      = os.getenv("CSV_EMAIL_COL",      "Email")
    CSV_PROJECT_COL: str    = os.getenv("CSV_PROJECT_COL",    "Project Title")
    CSV_CATEGORY_COL: str   = os.getenv("CSV_CATEGORY_COL",   "Submission Categories")
    CSV_FILTER_STATUS: str  = os.getenv("CSV_FILTER_STATUS",  "")

    # ── Misc ───────────────────────────────────────────────────────────────────
    DRY_RUN: bool = os.getenv("DRY_RUN", "false").lower() == "true"

    @classmethod
    def validate(cls) -> None:
        """Raise ValueError listing every missing required variable."""
        required = {
            "GMAIL_CLIENT_ID":     cls.GMAIL_CLIENT_ID,
            "GMAIL_CLIENT_SECRET": cls.GMAIL_CLIENT_SECRET,
        }
        missing = [k for k, v in required.items() if not v]
        if missing:
            raise ValueError(
                f"Missing required environment variables: {', '.join(missing)}\n"
                "Copy .env.example → .env and fill in the values from Google Cloud Console."
            )
