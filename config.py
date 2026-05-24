"""
Central configuration — loads every setting from .env.
Call Config.validate() at startup to catch missing credentials early.
"""

import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    # ── Canva ──────────────────────────────────────────────────────────────────
    CANVA_CLIENT_ID: str = os.getenv("CANVA_CLIENT_ID", "")
    CANVA_CLIENT_SECRET: str = os.getenv("CANVA_CLIENT_SECRET", "")
    # The ID of your published Canva brand template (starts with OAB…)
    CANVA_BRAND_TEMPLATE_ID: str = os.getenv("CANVA_BRAND_TEMPLATE_ID", "")

    # Text field names exactly as labelled inside the Canva brand template
    CANVA_NAME_FIELD: str = os.getenv("CANVA_NAME_FIELD", "Name")
    CANVA_PROJECT_FIELD: str = os.getenv("CANVA_PROJECT_FIELD", "Project")
    CANVA_CATEGORY_FIELD: str = os.getenv("CANVA_CATEGORY_FIELD", "Category")

    CANVA_REDIRECT_URI: str = os.getenv("CANVA_REDIRECT_URI", "http://localhost:8080/callback")
    CANVA_TOKEN_FILE: str = os.getenv("CANVA_TOKEN_FILE", "canva_token.json")
    CANVA_REQUEST_DELAY: float = float(os.getenv("CANVA_REQUEST_DELAY_SECONDS", "1.0"))
    CANVA_POLL_INTERVAL: float = float(os.getenv("CANVA_POLL_INTERVAL_SECONDS", "3.0"))
    CANVA_POLL_TIMEOUT: int = int(os.getenv("CANVA_POLL_TIMEOUT_SECONDS", "180"))

    CANVA_AUTH_URL: str = "https://www.canva.com/api/oauth/authorize"
    CANVA_TOKEN_URL: str = "https://www.canva.com/api/oauth/token"
    CANVA_API_BASE: str = "https://api.canva.com/rest/v1"
    CANVA_SCOPES: str = "design:content:read design:content:write asset:read asset:write"

    # ── Gmail ──────────────────────────────────────────────────────────────────
    GMAIL_CREDENTIALS_FILE: str = os.getenv("GMAIL_CREDENTIALS_FILE", "credentials.json")
    GMAIL_TOKEN_FILE: str = os.getenv("GMAIL_TOKEN_FILE", "gmail_token.json")

    # ── Email content ──────────────────────────────────────────────────────────
    EMAIL_SUBJECT: str = os.getenv("EMAIL_SUBJECT", "Congratulations — Your Certificate is Here!")
    EMAIL_FROM_NAME: str = os.getenv("EMAIL_FROM_NAME", "Your Organisation")
    EMAIL_TEMPLATE_DIR: str = os.getenv("EMAIL_TEMPLATE_DIR", "email_sender/templates")
    DEFAULT_TEMPLATE: str = os.getenv("DEFAULT_TEMPLATE", "default.html")

    # ── CSV / paths ────────────────────────────────────────────────────────────
    CSV_PATH: str = os.getenv("CSV_PATH", "data/recipients.csv")
    OUTPUT_DIR: str = os.getenv("OUTPUT_DIR", "data/output")

    # CSV column mapping
    CSV_FIRST_NAME_COL: str = os.getenv("CSV_FIRST_NAME_COL", "First Name")
    CSV_LAST_NAME_COL: str = os.getenv("CSV_LAST_NAME_COL", "Last Name")
    CSV_EMAIL_COL: str = os.getenv("CSV_EMAIL_COL", "Email")
    CSV_PROJECT_COL: str = os.getenv("CSV_PROJECT_COL", "Project Title")
    CSV_CATEGORY_COL: str = os.getenv("CSV_CATEGORY_COL", "Submission Categories")
    CSV_FILTER_STATUS: str = os.getenv("CSV_FILTER_STATUS", "")  # e.g. "Finalist"

    # ── Misc ───────────────────────────────────────────────────────────────────
    DRY_RUN: bool = os.getenv("DRY_RUN", "false").lower() == "true"

    @classmethod
    def validate(cls) -> None:
        """Raise ValueError listing every missing required variable."""
        required = {
            "CANVA_CLIENT_ID": cls.CANVA_CLIENT_ID,
            "CANVA_CLIENT_SECRET": cls.CANVA_CLIENT_SECRET,
            "CANVA_BRAND_TEMPLATE_ID": cls.CANVA_BRAND_TEMPLATE_ID,
        }
        missing = [k for k, v in required.items() if not v]
        if missing:
            raise ValueError(
                f"Missing required environment variables: {', '.join(missing)}\n"
                "Copy .env.example → .env and fill in the values."
            )
