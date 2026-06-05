"""
Application settings — loaded from environment variables.

Required env vars in production (Cloud Run reads them from Secret Manager
via the deployment YAML):

    MONGO_URI              MongoDB Atlas connection string
    SESSION_SECRET         Random 64-char string for session cookies
    ENCRYPTION_KEY         32-byte base64 Fernet key for at-rest secret encryption
    BASE_URL               Public URL of this service (e.g. https://certs.example.com)
    INITIAL_ADMIN_EMAIL    Bootstrap admin user — created if no users exist
    INITIAL_ADMIN_PASSWORD Bootstrap admin password
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Mongo ─────────────────────────────────────────────────────────────────
    MONGO_URI: str = "mongodb://localhost:27017"
    MONGO_DB:  str = "certificate_automation"

    # ── Auth ──────────────────────────────────────────────────────────────────
    SESSION_SECRET: str  = "change-me-in-production"
    SESSION_COOKIE: str  = "cert_session"
    SESSION_MAX_AGE: int = 60 * 60 * 24 * 7  # 7 days

    # Fernet key (base64-encoded 32-byte key) for encrypting OAuth refresh tokens
    # and client secrets stored in Mongo. Generate via:
    #     python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    ENCRYPTION_KEY: str = ""

    # ── Service ───────────────────────────────────────────────────────────────
    BASE_URL: str = "http://localhost:8000"
    ENV: str      = "development"

    # ── Bootstrap admin (only used on first boot when no users exist) ─────────
    INITIAL_ADMIN_EMAIL: str    = ""
    INITIAL_ADMIN_PASSWORD: str = ""

    # ── Canva — global integration (one Cert-Automate app powers all festivals) ─
    CANVA_CLIENT_ID:     str = ""
    CANVA_CLIENT_SECRET: str = ""
    CANVA_AUTH_URL:      str = "https://www.canva.com/api/oauth/authorize"
    CANVA_TOKEN_URL:     str = "https://api.canva.com/rest/v1/oauth/token"
    CANVA_API_BASE:      str = "https://api.canva.com/rest/v1"
    CANVA_SCOPES:        str = "design:meta:read design:content:read design:content:write"

    # ── Local storage for laurel uploads (Phase 2). GCS in Phase 4. ──────────
    LAUREL_STORAGE_DIR:  str = "backend/data/laurels"

    # ── Cloud Tasks — production async run dispatch ──────────────────────────
    GCP_PROJECT:            str = ""
    CLOUD_TASKS_LOCATION:   str = ""        # e.g. asia-south1
    CLOUD_TASKS_QUEUE:      str = ""        # e.g. cert-runs
    CLOUD_TASKS_SA_EMAIL:   str = ""        # service account that the Cloud Tasks dispatcher impersonates


@lru_cache
def get_settings() -> Settings:
    return Settings()
