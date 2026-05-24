"""
Configuration loader — two-layer system:

  Layer 1 (root .env)          — Canva credentials + global defaults (shared)
  Layer 2 (projects/<n>/.env)  — Gmail credentials + per-project overrides

Layer 2 values take precedence over Layer 1.
Call Config.load(project_name) before accessing any value.
Call Config.validate() to catch missing credentials early.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import dotenv_values


class Config:
    # ── Canva (shared) ─────────────────────────────────────────────────────────
    CANVA_CLIENT_ID: str           = ""
    CANVA_CLIENT_SECRET: str       = ""
    CANVA_BRAND_TEMPLATE_ID: str   = ""
    CANVA_NAME_FIELD: str          = "Name"
    CANVA_PROJECT_FIELD: str       = "Project"
    CANVA_CATEGORY_FIELD: str      = "Category"
    CANVA_REDIRECT_URI: str        = "http://localhost:8080/callback"
    CANVA_TOKEN_FILE: str          = "canva_token.json"
    CANVA_REQUEST_DELAY: float     = 1.0
    CANVA_POLL_INTERVAL: float     = 3.0
    CANVA_POLL_TIMEOUT: int        = 180
    CANVA_AUTH_URL: str            = "https://www.canva.com/api/oauth/authorize"
    CANVA_TOKEN_URL: str           = "https://www.canva.com/api/oauth/token"
    CANVA_API_BASE: str            = "https://api.canva.com/rest/v1"
    CANVA_SCOPES: str              = "design:content:read design:content:write asset:read asset:write"

    # ── Gmail (per-project) ────────────────────────────────────────────────────
    GMAIL_CLIENT_ID: str           = ""
    GMAIL_CLIENT_SECRET: str       = ""
    GMAIL_PROJECT_ID: str          = ""
    GMAIL_TOKEN_FILE: str          = "gmail_token.json"

    # ── Email content (per-project) ────────────────────────────────────────────
    EMAIL_SUBJECT: str             = "Congratulations — Your Certificate is Here!"
    EMAIL_FROM_NAME: str           = "Your Organisation"
    EMAIL_TEMPLATE_DIR: str        = "email_sender/templates"
    DEFAULT_TEMPLATE: str          = "default.html"

    # ── CSV (per-project) ──────────────────────────────────────────────────────
    CSV_PATH: str                  = "data/recipients.csv"
    CSV_FIRST_NAME_COL: str        = "First Name"
    CSV_LAST_NAME_COL: str         = "Last Name"
    CSV_EMAIL_COL: str             = "Email"
    CSV_PROJECT_COL: str           = "Project Title"
    CSV_CATEGORY_COL: str          = "Submission Categories"
    CSV_FILTER_STATUS: str         = ""

    # ── Misc ───────────────────────────────────────────────────────────────────
    OUTPUT_DIR: str                = "data/output"
    DRY_RUN: bool                  = False

    # ── active project name (set by load()) ────────────────────────────────────
    PROJECT: str                   = ""

    # ── class-level env map ────────────────────────────────────────────────────
    _env: dict[str, str]           = {}

    # ──────────────────────────────────────────────────────────────────────────

    @classmethod
    def load(cls, project: str) -> None:
        """
        Load configuration for the given project name.

        Reads root .env first, then overlays projects/<project>/.env.
        All values are stored on the class so they're accessible anywhere.
        """
        cls.PROJECT = project

        # Layer 1 — root .env (Canva + global defaults)
        root_env = dotenv_values(".env")

        # Layer 2 — project .env (Gmail + project overrides)
        project_env_path = Path(f"projects/{project}/.env")
        if not project_env_path.exists():
            raise FileNotFoundError(
                f"Project config not found: {project_env_path}\n"
                f"Create it using projects/project_name.env.example as a template."
            )
        project_env = dotenv_values(project_env_path)

        # Merge: project values override root values
        cls._env = {**root_env, **project_env}
        cls._apply()

    @classmethod
    def _apply(cls) -> None:
        """Push merged env dict onto class attributes."""
        e = cls._env

        # Canva
        cls.CANVA_CLIENT_ID          = e.get("CANVA_CLIENT_ID", cls.CANVA_CLIENT_ID)
        cls.CANVA_CLIENT_SECRET      = e.get("CANVA_CLIENT_SECRET", cls.CANVA_CLIENT_SECRET)
        cls.CANVA_BRAND_TEMPLATE_ID  = e.get("CANVA_BRAND_TEMPLATE_ID", cls.CANVA_BRAND_TEMPLATE_ID)
        cls.CANVA_NAME_FIELD         = e.get("CANVA_NAME_FIELD", cls.CANVA_NAME_FIELD)
        cls.CANVA_PROJECT_FIELD      = e.get("CANVA_PROJECT_FIELD", cls.CANVA_PROJECT_FIELD)
        cls.CANVA_CATEGORY_FIELD     = e.get("CANVA_CATEGORY_FIELD", cls.CANVA_CATEGORY_FIELD)
        cls.CANVA_REDIRECT_URI       = e.get("CANVA_REDIRECT_URI", cls.CANVA_REDIRECT_URI)
        cls.CANVA_TOKEN_FILE         = e.get("CANVA_TOKEN_FILE", cls.CANVA_TOKEN_FILE)
        cls.CANVA_REQUEST_DELAY      = float(e.get("CANVA_REQUEST_DELAY_SECONDS", cls.CANVA_REQUEST_DELAY))
        cls.CANVA_POLL_INTERVAL      = float(e.get("CANVA_POLL_INTERVAL_SECONDS", cls.CANVA_POLL_INTERVAL))
        cls.CANVA_POLL_TIMEOUT       = int(e.get("CANVA_POLL_TIMEOUT_SECONDS", cls.CANVA_POLL_TIMEOUT))

        # Gmail
        cls.GMAIL_CLIENT_ID          = e.get("GMAIL_CLIENT_ID", cls.GMAIL_CLIENT_ID)
        cls.GMAIL_CLIENT_SECRET      = e.get("GMAIL_CLIENT_SECRET", cls.GMAIL_CLIENT_SECRET)
        cls.GMAIL_PROJECT_ID         = e.get("GMAIL_PROJECT_ID", cls.GMAIL_PROJECT_ID)
        cls.GMAIL_TOKEN_FILE         = e.get("GMAIL_TOKEN_FILE", cls.GMAIL_TOKEN_FILE)

        # Email
        cls.EMAIL_SUBJECT            = e.get("EMAIL_SUBJECT", cls.EMAIL_SUBJECT)
        cls.EMAIL_FROM_NAME          = e.get("EMAIL_FROM_NAME", cls.EMAIL_FROM_NAME)
        cls.EMAIL_TEMPLATE_DIR       = e.get("EMAIL_TEMPLATE_DIR", cls.EMAIL_TEMPLATE_DIR)
        cls.DEFAULT_TEMPLATE         = e.get("DEFAULT_TEMPLATE", cls.DEFAULT_TEMPLATE)

        # CSV
        cls.CSV_PATH                 = e.get("CSV_PATH", cls.CSV_PATH)
        cls.CSV_FIRST_NAME_COL       = e.get("CSV_FIRST_NAME_COL", cls.CSV_FIRST_NAME_COL)
        cls.CSV_LAST_NAME_COL        = e.get("CSV_LAST_NAME_COL", cls.CSV_LAST_NAME_COL)
        cls.CSV_EMAIL_COL            = e.get("CSV_EMAIL_COL", cls.CSV_EMAIL_COL)
        cls.CSV_PROJECT_COL          = e.get("CSV_PROJECT_COL", cls.CSV_PROJECT_COL)
        cls.CSV_CATEGORY_COL         = e.get("CSV_CATEGORY_COL", cls.CSV_CATEGORY_COL)
        cls.CSV_FILTER_STATUS        = e.get("CSV_FILTER_STATUS", cls.CSV_FILTER_STATUS)

        # Misc
        cls.OUTPUT_DIR               = e.get("OUTPUT_DIR", cls.OUTPUT_DIR)
        cls.DRY_RUN                  = e.get("DRY_RUN", str(cls.DRY_RUN)).lower() == "true"

    @classmethod
    def validate(cls) -> None:
        """Raise ValueError listing every missing required variable."""
        if not cls.PROJECT:
            raise ValueError("No project loaded. Call Config.load('<project_name>') first.")

        required = {
            "GMAIL_CLIENT_ID":     cls.GMAIL_CLIENT_ID,
            "GMAIL_CLIENT_SECRET": cls.GMAIL_CLIENT_SECRET,
        }
        missing = [k for k, v in required.items() if not v]
        if missing:
            raise ValueError(
                f"[project: {cls.PROJECT}] Missing required variables: {', '.join(missing)}\n"
                f"Check projects/{cls.PROJECT}/.env"
            )

    @classmethod
    def summary(cls) -> str:
        """Return a one-line config summary for logging."""
        return (
            f"project={cls.PROJECT} | "
            f"gmail={cls.GMAIL_CLIENT_ID[:20]}… | "
            f"csv={cls.CSV_PATH} | "
            f"dry_run={cls.DRY_RUN}"
        )
